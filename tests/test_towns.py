"""Invented town metadata and beliefs; no game data or personal profiles."""
from dataclasses import asdict
import json
import struct
import unittest

from bwv import towns_live as live
from bwv.llm_context import summarize_context
from bwv.prompt import preview_prompt
from bwv.creature_tools import CreatureToolSession
from test_control import playable
from test_surroundings import NearbyMemory


class TownMemory(NearbyMemory):
    def __init__(self):
        super().__init__()
        self.town=0x6800000
        self.typed(self.tree,b'.?AVAbode@@',4)
        self.put(0x5104000+live.GET_TOWN_SLOT,0x401730)
        self.put(self.tree+0x98,self.town)
        self.typed(self.town,b'.?AVTown@@',10)
        self.typed(self.town+live.TOWN_BELIEF,b'.?AVGBelief@@',11)
        for slot in range(8):
            address=self.game+live.PLAYER_START+slot*live.PLAYER_STRIDE
            self.typed(address,b'.?AVGPlayer@@',20+slot)
            self.bytes(address+live.PLAYER_NUMBER,bytes([slot]))
        self.bytes(self.game+live.NEUTRAL_PLAYER_INDEX,b'\x07')
        self.owner(1)
        self.put(self.town+live.TOWN_TRIBE,0)
        self.beliefs((30,90,0,0,0,0,0,100))
        self.bytes(self.town+live.TOWN_BELIEF+live.BELIEF_CAPS,struct.pack('<8f',*[200]*8))

    def owner(self,slot):
        self.put(self.town+live.TOWN_OWNER,0 if slot is None else
                 self.game+live.PLAYER_START+slot*live.PLAYER_STRIDE)

    def beliefs(self,values):
        self.bytes(self.town+live.TOWN_BELIEF+live.BELIEF_VALUES,struct.pack('<8f',*values))


class TownTests(unittest.TestCase):
    def test_house_membership_control_and_belief_reach_compact_context(self):
        m=TownMemory();context=m.sample().context;town,=context.towns
        self.assertEqual(context.town_read_errors,0)
        self.assertEqual(context.nearby_known_objects[0].town_id,town.town_id)
        self.assertEqual((town.tribe,town.control,town.controlling_god_id),('celtic','other_god','god_1'))
        self.assertEqual(town.conversion_reference_engine_units,90)
        self.assertEqual(town.additional_belief_to_match_reference_engine_units,60)
        self.assertEqual([(b.god_id,b.belief_engine_units) for b in town.beliefs],[('god_0',30),('god_1',90)])
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
        t=compact['towns_nearby'][0]
        self.assertEqual(t['control'],'other_god')
        self.assertNotIn('player_belief_cap_engine_units',t)
        self.assertEqual(compact['surroundings']['houses_nearby'],1)

    def test_inspection_preserves_membership_and_detailed_belief_cap(self):
        from test_creature_tools import frame
        m=TownMemory();context=m.sample().context
        state=frame();state['observation']['surroundings']=asdict(context)
        tool=CreatureToolSession(lambda:state)
        result=tool.call('inspect_surrounding',{'kind':'house','limit':3})
        self.assertEqual(result['objects'][0]['town_id'],result['towns'][0]['town_id'])
        self.assertEqual(result['towns'][0]['player_belief_cap_engine_units'],200)

    def test_individual_building_inspection_links_current_town_and_owner(self):
        from test_creature_tools import frame
        m=TownMemory();state=frame();tool=CreatureToolSession(lambda:state)
        for owner,control,god in ((1,'other_god','god_1'),(7,'unclaimed',None),(0,'player_god','god_0')):
            with self.subTest(owner=owner):
                m.owner(owner);state['observation']['surroundings']=asdict(m.sample().context)
                obj=state['observation']['surroundings']['nearby_known_objects'][0]
                result=tool.call('inspect_object',{'object_id':obj['object_id']})
                self.assertEqual(result['object']['town_id'],result['town']['town_id'])
                self.assertEqual(result['town']['control'],control)
                self.assertEqual(result['town']['controlling_god_id'],god)
                self.assertEqual(result['town']['tribe'],'celtic')
                town=tool.call('inspect_object',{'object_id':result['town']['town_id']})
                self.assertEqual(town['town'],result['town'])
                self.assertNotIn('object',town)
        # Losing the link must not reuse an old owner or invent neutrality.
        m.put(m.tree+0x98,0);state['observation']['surroundings']=asdict(m.sample().context)
        result=tool.call('inspect_object',{'object_id':obj['object_id']})
        self.assertEqual(result['status'],'observed')
        self.assertNotIn('town',result)
        self.assertEqual(tool.call('inspect_object',{'object_id':'missing-town'})['status'],
            'object_not_currently_known')

    def test_matched_reference_is_not_reported_as_owned_until_owner_changes(self):
        m=TownMemory();m.beliefs((90,90,0,0,0,0,0,100))
        town,=m.sample().context.towns
        self.assertEqual(town.additional_belief_to_match_reference_engine_units,0)
        self.assertEqual(town.control,'other_god')
        m.owner(0);town,=m.sample().context.towns
        self.assertEqual(town.control,'player_god')
        self.assertIsNone(town.conversion_reference_engine_units)

    def test_neutral_control_and_unknown_owner_are_distinct(self):
        m=TownMemory();m.owner(7);town,=m.sample().context.towns
        self.assertEqual(town.control,'unclaimed')
        self.assertIsNone(town.controlling_god_id)
        self.assertEqual(town.conversion_reference_engine_units,100)
        self.assertTrue(town.beliefs)  # Unclaimed does not mean no belief.
        m.owner(None);town,=m.sample().context.towns
        self.assertEqual(town.control,'unknown')
        self.assertIsNone(town.conversion_reference_engine_units)

    def test_villager_membership_uses_its_own_verified_getter(self):
        m=TownMemory();m.typed(m.tree,b'.?AVVillager@@',4)
        m.put(0x5104000+live.GET_TOWN_SLOT,0x751F00)
        m.put(m.tree+0x12C,m.town)
        context=m.sample().context
        self.assertEqual(context.nearby_known_objects[0].town_id,context.towns[0].town_id)

    def test_town_getter_not_kind_allowlist_controls_membership(self):
        m=TownMemory();m.typed(m.tree,b'.?AVWorshipSite@@',4)
        context=m.sample().context
        self.assertEqual(context.nearby_known_objects[0].kind,'worship_site')
        self.assertEqual(context.nearby_known_objects[0].town_id,context.towns[0].town_id)
        m.put(m.reader.word(m.tree)+live.GET_TOWN_SLOT,0)
        context=m.sample().context
        self.assertEqual(context.towns,())
        self.assertIsNone(context.nearby_known_objects[0].town_id)

    def test_initialized_unused_slots_do_not_invent_other_gods(self):
        m=TownMemory();m.owner(7)
        m.beliefs((0.16,0.3,0.3,0.3,0.3,0.3,0.3,0.3))
        context=m.sample().context;town,=context.towns
        self.assertEqual([b.god_id for b in town.beliefs],['god_0'])
        self.assertEqual(town.control,'unclaimed')
        self.assertAlmostEqual(town.conversion_reference_engine_units,0.3)
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
        self.assertEqual(len(compact['towns_nearby'][0]['beliefs']),1)
        m.owner(2);town,=m.sample().context.towns
        self.assertEqual([b.god_id for b in town.beliefs],['god_0','god_2'])
        self.assertEqual(town.controlling_god_id,'god_2')

    def test_invalid_town_does_not_erase_independent_house_observation(self):
        for variant in ('type','owner','belief','neutral'):
            with self.subTest(variant=variant):
                m=TownMemory()
                if variant=='type':m.typed(m.town,b'.?AVRock@@',10)
                elif variant=='owner':m.put(m.town+live.TOWN_OWNER,m.player+1)
                elif variant=='belief':m.beliefs((float('nan'),90,0,0,0,0,0,100))
                else:m.bytes(m.game+live.NEUTRAL_PLAYER_INDEX,b'\x08')
                context=m.sample().context
                self.assertEqual(context.towns,())
                self.assertEqual(len(context.nearby_known_objects),1)
                self.assertIsNone(context.nearby_known_objects[0].town_id)
                self.assertEqual(context.town_read_errors,1)

    def test_changed_owner_discards_town_links_without_stale_allegiance(self):
        m=TownMemory();original=m.reader.read;calls=0
        def read(address,size):
            nonlocal calls
            if address==m.town+live.TOWN_OWNER:
                calls+=1
                if calls>1:return struct.pack('<I',m.player)
            return original(address,size)
        m.reader.read=read
        context=m.sample().context
        self.assertEqual(context.towns,())
        self.assertIsNone(context.nearby_known_objects[0].town_id)
        self.assertEqual(context.town_read_errors,1)

    def test_normal_belief_changes_do_not_erase_town_identity(self):
        m=TownMemory();original=m.reader.read;calls=0
        def read(address,size):
            nonlocal calls
            if address==m.town+live.TOWN_BELIEF+live.BELIEF_VALUES:
                calls+=1
                return struct.pack('<8f',30+calls,90,0,0,0,0,0,100)
            return original(address,size)
        m.reader.read=read
        first=m.sample().context
        second=m.sample().context
        self.assertEqual(first.town_read_errors,0)
        self.assertEqual(second.town_read_errors,0)
        self.assertEqual(first.towns[0].town_id,second.towns[0].town_id)
        self.assertEqual(first.nearby_known_objects[0].town_id,first.towns[0].town_id)
        self.assertEqual(first.towns[0].beliefs[0].belief_engine_units,31)
        self.assertEqual(second.towns[0].beliefs[0].belief_engine_units,32)

    def test_unknown_getter_and_tribe_do_not_get_guessed(self):
        m=TownMemory();m.put(m.town+live.TOWN_TRIBE,99)
        self.assertIsNone(m.sample().context.towns[0].tribe)
        m.put(0x5104000+live.GET_TOWN_SLOT,0)
        context=m.sample().context
        self.assertEqual(context.towns,())
        self.assertEqual(context.town_read_errors,0)
