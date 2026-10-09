"""Synthetic island player layouts and model/tool projections; no game/API use."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import struct
from unittest import TestCase
from unittest.mock import patch

from bwv import island_live as live
from bwv.island import IslandContext, island_context, island_roster, island_updates, with_island_roster
from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from bwv.direct_tools import DirectCreatureToolSession
from bwv.surroundings import NearbyObject, SurroundingsContext
from bwv.model_projection import model_turn
from test_god_hand import HandMemory
from test_control import playable
from test_creature_tools import frame


class IslandMemory(HandMemory):
    def __init__(self):
        super().__init__()
        self.bytes(self.game+live.NEUTRAL_PLAYER_INDEX,b'\x07')
        self.put(self.game+0x59C8,512);self.put(self.game+0x59C4,512)
        for slot in range(8):
            address=self.player+slot*live.PLAYER_STRIDE
            self.typed(address,b'.?AVGPlayer@@')
            self.bytes(address+live.PLAYER_NUMBER,bytes([slot]))
            self.put(address+live.PLAYER_TYPE,1 if slot==0 else 2 if slot in (1,2) else 3 if slot==7 else 0)
            self.bytes(address+live.PLAYER_NAME,(f'Example god {slot}\0').encode('utf-16-le').ljust(64,b'\0'))
            self.put(address+live.PLAYER_CREATURE,self.creature if slot==0 else 0)
            self.put(address+live.PLAYER_COMPUTER,0)
            self.bytes(address+live.PLAYER_HANDS,bytes(72))
            self.bytes(address+live.PLAYER_ALLIANCES,struct.pack('<8f',0,.001,0,0,0,0,0,0))
            alignment=0x6800000+slot*0x1000
            self.typed(alignment,b'.?AVGAlignment@@')
            self.put(address+0x60,alignment)
            self.bytes(alignment+8,struct.pack('<f',-.2 if slot==1 else .2))
        self.put(self.player+live.PLAYER_HANDS,self.hand)
        self.bytes(self.status+0x28,b'\0')
        self.bytes(self.status+0xC8,struct.pack('<fff',100,0,200))
        self.species(self.creature,b'Ape creature',0x6900000)
        self.bytes(self.creature+0x14,struct.pack('<iif',int(100/live.POSITION_SCALE),int(150/live.POSITION_SCALE),0))
        self.ally=self.player+live.PLAYER_STRIDE
        self.other=0x6A00000
        self.typed(self.other,b'.?AVCreature@@')
        self.put(self.other+live.CREATURE_OWNER,self.ally)
        self.put(self.ally+live.PLAYER_CREATURE,self.other)
        self.species(self.other,b'Tortoise creature',0x6B00000)
        self.computer,self.aihand=0x6C00000,0x6D00000
        self.typed(self.computer,b'.?AVGComputerPlayer@@')
        self.typed(self.aihand,b'.?AVPlayerActionState@@')
        self.put(self.ally+live.PLAYER_COMPUTER,self.computer)
        self.put(self.computer+0x28,self.ally);self.put(self.computer+0x1B8,1)
        self.put(self.computer+0x34,self.aihand)
        self.put(self.aihand+0x6C,self.computer);self.put(self.aihand+0x4C,1)
        self.bytes(self.aihand+0x14,struct.pack('<iif',int(130/live.POSITION_SCALE),int(200/live.POSITION_SCALE),0))

    def species(self,creature,name,info):
        self.typed(info,b'.?AVCreatureInfo@@');self.put(creature+0x28,info)
        self.bytes(info+0x18,(name+b'\0').ljust(48,b'\0'))

    def sample(self,final=None):
        with patch.object(live,'snapshot',return_value=final or self.observer):
            return live.read_island(self.reader,self.observer)


class IslandReaderTests(TestCase):
    def test_active_slots_alliance_names_and_registered_creatures(self):
        m=IslandMemory();sample=m.sample();public=island_context(sample.context)
        self.assertTrue(public['roster_complete'])
        self.assertEqual(public['god_count'],3)
        self.assertEqual(public['observed_registered_creature_count'],2)
        self.assertEqual([g['god_id'] for g in public['gods']],['god_0','god_1','god_2'])
        own,ally,enemy=public['gods']
        self.assertTrue(own['is_player']);self.assertTrue(own['creature']['is_agent'])
        self.assertEqual(ally['relation_to_player'],'ally')  # .001, not a guessed .5 threshold
        self.assertEqual(enemy['relation_to_player'],'enemy')
        self.assertEqual(ally['label'],'Example god 1 (ally)')
        self.assertEqual(enemy['label'],'Example god 2 (enemy)')
        self.assertNotIn('label',own)
        self.assertEqual(ally['alignment'],-.2)  # Good/evil is independent of alliance.
        self.assertEqual(ally['creature']['type'],'tortoise')
        self.assertEqual(ally['creature']['object_id'],hashlib.sha256(f'{sample.session}:{m.creature}:{m.other}'.encode()).hexdigest()[:24])
        self.assertEqual(live.creature_ownership(m.reader,m.game,m.other),('god_1','registered_creature'))
        self.assertEqual(sample.context.read_errors,0)

    def test_computer_hand_uses_actual_mapcoords_and_distances(self):
        m=IslandMemory();ally=m.sample().context.gods[1]
        self.assertEqual(ally['hand']['position'],{'x':130.,'z':200.})
        self.assertAlmostEqual(ally['hand']['distance'],58.3,places=1)
        self.assertEqual(ally['hand']['distance_to_god_hand'],30.)
        m.put(m.aihand+0x4C,2)
        bad=m.sample().context
        self.assertNotIn('hand',bad.gods[1]);self.assertEqual(bad.read_errors,1)
        self.assertEqual(bad.gods[1]['creature']['type'],'tortoise')
        m.put(m.aihand+0x4C,1);m.put(m.computer+0x1B8,0)
        self.assertNotIn('hand',m.sample().context.gods[1])

    def test_human_hand_uses_active_device_not_another_players_cursor(self):
        m=IslandMemory();m.put(m.ally+live.PLAYER_COMPUTER,0)
        interface,status,device=0x7000000,0x7100000,0x7200000
        m.typed(interface,b'.?AVGInterface@@');m.typed(status,b'.?AVGInterfaceStatus@@')
        m.put(m.ally+live.PLAYER_HANDS,interface);m.put(interface+0x39C,status)
        m.put(interface+0x1BC,device);m.put(device+0x1F4,1)
        m.bytes(status+0x28,b'\x01');m.put(status+0x2C,interface)
        m.bytes(status+0xC8,struct.pack('<fff',230,0,320))
        self.assertEqual(m.sample().context.gods[1]['hand']['position'],{'x':230.,'z':320.})
        m.put(device+0x1F4,0)
        self.assertNotIn('hand',m.sample().context.gods[1])
        m.put(device+0x1F4,1);m.bytes(status+0x28,b'\x02')
        self.assertNotIn('hand',m.sample().context.gods[1])

    def test_inactive_god_removed_and_changed_owner_allegiance_are_fresh(self):
        m=IslandMemory();first=m.sample().context
        m.put(m.ally+live.PLAYER_TYPE,0)
        second=m.sample().context
        self.assertNotIn('god_1',[g['god_id'] for g in second.gods])
        self.assertNotEqual(island_roster(first),island_roster(second))
        m.put(m.ally+live.PLAYER_TYPE,2)
        m.bytes(m.player+live.PLAYER_ALLIANCES+4,struct.pack('<f',0))
        m.put(m.other+live.CREATURE_OWNER,m.player)
        third=m.sample().context
        self.assertEqual(third.gods[1]['relation_to_player'],'enemy')
        self.assertNotIn('creature',third.gods[1])
        self.assertEqual(live.creature_ownership(m.reader,m.game,m.other),('god_0','engine_only'))

    def test_unreadable_optional_fields_do_not_erase_roster_or_invent_values(self):
        m=IslandMemory()
        m.bytes(m.player+live.PLAYER_ALLIANCES+4,struct.pack('<f',float('nan')))
        m.bytes(m.ally+live.PLAYER_NAME,b'\x00\xd8'+bytes(62))  # Invalid UTF-16.
        m.put(m.ally+0x60,0)
        sample=m.sample().context
        self.assertTrue(sample.roster_complete)
        self.assertEqual(sample.read_errors,3)
        for field in ('relation_to_player','name','alignment'):self.assertNotIn(field,sample.gods[1])
        m.put(m.ally+live.PLAYER_TYPE,99)
        sample=m.sample().context
        self.assertFalse(sample.roster_complete)
        self.assertNotIn('god_count',island_context(sample))

    def test_names_special_characters_and_session_ownership_coherence(self):
        m=IslandMemory();name='A "god" {x}\\é'
        m.bytes(m.ally+live.PLAYER_NAME,(name+'\0').encode('utf-16-le').ljust(64,b'\0'))
        m.bytes(m.ally+live.PLAYER_NAME+62,b'\x00\xd8')  # Unused tail after terminator.
        self.assertEqual(m.sample().context.gods[1]['name'],name)
        with self.assertRaises(ValueError):m.sample({**m.observer,'session':6})
        read=m.reader.read;count=0
        def changing(address,size):
            nonlocal count
            if address==m.ally+live.PLAYER_TYPE:
                count+=1
                return struct.pack('<I',2 if count==1 else 0)
            return read(address,size)
        m.reader.read=changing
        with self.assertRaises(ValueError):m.sample()


class IslandProjectionTests(TestCase):
    def context(self):return IslandMemory().sample().context

    def test_roster_stays_in_system_while_positions_alignment_change_in_ticks(self):
        island=self.context();obs=playable(island=island)
        first=preview_prompt(obs,None)
        moved=deepcopy(island.gods);moved[1]['hand']['position']['x']+=10;moved[1]['alignment']=.9
        second=preview_prompt(replace(obs,island=replace(island,gods=moved)),None)
        self.assertEqual(first.system,second.system)
        self.assertNotEqual(first.context_json,second.context_json)
        roster=json.loads(first.system.split('## Current island gods and registered creatures (data)\n\n')[1])
        self.assertEqual(roster['god_count'],3)
        self.assertNotIn('hand',roster['gods'][1])
        compact=summarize_context(json.loads(second.context_json),asdict(replace(obs,island=replace(island,gods=moved))))
        self.assertEqual(compact['other_gods'][0]['hand']['position']['x'],140)
        self.assertEqual(compact['other_gods'][0]['label'],'Example god 1 (ally)')
        self.assertEqual(roster['gods'][1]['label'],'Example god 1 (ally)')
        self.assertNotIn('gods',compact)
        self.assertNotIn('name',compact['other_gods'][0])
        self.assertEqual(island_updates(IslandContext()),{})
        self.assertEqual(with_island_roster('base',IslandContext()),'base')

    def test_ownership_survives_both_views_model_projection_and_inspection(self):
        island=self.context();identity=island.gods[1]['creature']['object_id']
        obj=NearbyObject(identity,'creature',12, distance_to_god_hand_engine_units=4,
                         species='tortoise',owner_god_id='god_1',owner_link='registered_creature')
        surroundings=SurroundingsContext(True,(obj,),god_hand_available=True,god_hand_objects=(obj,))
        obs=playable(island=island,surroundings=surroundings)
        preview=preview_prompt(obs,None)
        compact=model_turn({'snapshot':summarize_context(json.loads(preview.context_json),asdict(obs))})['snapshot']
        self.assertTrue(compact['surroundings_also_near_god_hand'])
        self.assertEqual(compact['surroundings']['other_creature_details'][0]['owner_god_id'],'god_1')
        f=frame();f['observation']=asdict(obs);f['prompt']['context']=json.loads(preview.context_json)
        session=DirectCreatureToolSession(lambda:f)
        result=session.call('inspect_object',{'object_id':identity})
        self.assertEqual(result['object']['owner_god_id'],'god_1')
        self.assertEqual(result['owner_god']['relation_to_player'],'ally')
        self.assertEqual(result['owner_god']['label'],'Example god 1 (ally)')
        self.assertEqual(session.call('inspect_object',{'object_id':'god_1'})['god']['hand']['position'],{'x':130.,'z':200.})
        self.assertEqual(session.call('inspect_self',{})['island']['god_count'],3)
        # A registered creature is inspectable even outside the surroundings list.
        f['observation']['surroundings']={}
        result=session.call('inspect_object',{'object_id':identity})
        self.assertEqual(result['object']['type'],'tortoise creature')
        self.assertNotIn('position',result['object'])
        self.assertFalse(result['executed'])
        f['observation']['island']=asdict(IslandContext())
        self.assertEqual(session.call('inspect_object',{'object_id':'god_1'})['status'],'object_not_currently_known')

    def test_untrusted_metadata_is_not_forwarded(self):
        raw=asdict(self.context());raw['gods'][1]['private_address']=123456
        raw['gods'][1]['hand']['target_id']='god_hand'
        raw['gods'][1]['hand']['private_address']=123456
        raw['gods'][1]['creature']['private_address']=123456
        self.assertNotIn('private_address',json.dumps(island_context(raw)))
        self.assertNotIn('target_id',json.dumps(island_context(raw)))

    def test_labels_refresh_alliance_without_changing_names_or_lookup_ids(self):
        raw=asdict(self.context())
        raw['gods'][1]['name']='Player[2]'
        raw['gods'][1]['label']='Invented ally'  # Always derive it from current facts.
        original=deepcopy(raw)
        self.assertEqual(island_updates(raw)['other_gods'][0]['label'],'Player[2] (ally)')
        self.assertEqual(raw,original)
        raw['gods'][1]['relation_to_player']='enemy'
        god=island_roster(raw)['gods'][1]
        self.assertEqual((god['god_id'],god['name'],god['label']),
                         ('god_1','Player[2]','Player[2] (enemy)'))
        raw['gods'][1].pop('name')
        self.assertEqual(island_roster(raw)['gods'][1]['label'],'God 1 (enemy)')
        raw['gods'][1].pop('relation_to_player')
        self.assertNotIn('label',island_roster(raw)['gods'][1])
        self.assertNotIn('label',island_updates(raw)['other_gods'][0])
