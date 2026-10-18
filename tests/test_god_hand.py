import hashlib
import json
import struct
from dataclasses import asdict, replace
from unittest import TestCase
from unittest.mock import patch

from bwv import god_hand_live as live
from bwv.god_hand import GodHandContext, hand_contents
from bwv.llm_context import summarize_context
from bwv.model_projection import model_turn
from bwv.prompt import preview_prompt
from bwv.surroundings import NearbyObject, SurroundingsContext
from bwv.direct_tools import DirectCreatureToolSession
from test_control import playable
from test_creature_tools import frame
from test_training_live import MemoryFixture


class HandMemory(MemoryFixture):
    def __init__(self):
        super().__init__()
        self.status, self.controller, self.held = 0x6100000, 0x6200000, 0x6300000
        self.next_type = 10
        for address, name in ((self.hand, b'.?AVGInterface@@'),
                              (self.status, b'.?AVGInterfaceStatus@@'),
                              (self.controller, b'.?AVCHand@@')):
            self.typed(address, name)
        self.put(self.hand + live.INTERFACE_STATUS, self.status)
        self.put(self.hand + live.INTERFACE_HAND, self.controller)
        self.put(self.status + live.STATUS_INTERFACE, self.hand)
        self.put(self.controller + live.HAND_STATUS, self.status)
        self.put(self.controller + live.HAND_HELD_OBJECT, 0)

    def typed(self, address, name):
        table = 0x5000000 + self.next_type * 0x1000
        self.next_type += 1
        self.put(address, table)
        self.put(table - 4, table + 0x100)
        self.put(table + 0x10c, table + 0x200)
        self.bytes(table + 0x208, name.ljust(64, b'\0'))

    def hold(self, name=b'.?AVTree@@'):
        self.typed(self.held, name)
        self.put(self.controller + live.HAND_HELD_OBJECT, self.held)

    def spell(self, activated, *, base=22, power=0xFFFFFFFF, variant=23):
        self.hold(b'.?AVSpellSeed@@' if activated else b'.?AVOneOffSpellSeed@@')
        seed = 4
        info = live.SEED_INFO_BASE + seed * live.SEED_INFO_STRIDE
        self.typed(info, b'.?AVGSpellSeedInfo@@')
        self.put(info + 0x124, base)
        for n in range(3):self.put(info + 0x128 + n * 4, variant)
        self.put(self.held + (0x58 if activated else 0x68), info if activated else seed)
        self.put(self.held + (0x6C if activated else 0x78), power)

    def sample(self, final=None):
        with patch.object(live, 'snapshot', return_value=final or self.observer), \
                patch('bwv.object_details_live.read_details', return_value=({}, 0)):
            return live.read_god_hand(self.reader, self.observer)


class GodHandReaderTests(TestCase):
    def test_empty_object_drop_and_current_identity(self):
        m = HandMemory()
        self.assertEqual(m.sample().context, GodHandContext('empty'))
        m.hold()
        sample = m.sample()
        self.assertEqual(sample.context.contents, 'holding_object')
        self.assertEqual(sample.context.held_object['kind'], 'tree')
        identity = hashlib.sha256(f'{sample.session}:{m.creature}:{m.held}'.encode()).hexdigest()[:24]
        self.assertEqual(sample.context.held_object['object_id'], identity)
        m.put(m.controller + live.HAND_HELD_OBJECT, 0)
        self.assertEqual(hand_contents(m.sample().context), {'contents': 'empty'})

    def test_bubbles_and_activated_spells_use_distinct_metadata_and_variants(self):
        for activated in (False, True):
            for power, variant, expected in ((0xFFFFFFFF,23,'water'), (0,23,'water_power_up_1'),
                                              (1,0,'water'), (2,23,'water_power_up_1')):
                m = HandMemory();m.spell(activated, power=power, variant=variant)
                data = hand_contents(m.sample().context)
                if activated:
                    self.assertEqual(data, {'contents':'activated_miracle','activated_miracle':expected})
                    self.assertNotIn('held_object', data)
                else:
                    self.assertEqual(data['held_object']['kind'], 'miracle_bubble')
                    self.assertEqual(data['held_object']['miracle'], expected)
                    self.assertNotIn('activated_miracle', data)

    def test_unreadable_spell_is_not_an_empty_hand_or_a_guessed_spell(self):
        m = HandMemory();m.spell(True)
        m.put(m.held + 0x6C, 7)
        sample = m.sample()
        self.assertEqual(hand_contents(sample.context), {'contents':'activated_miracle'})
        self.assertEqual(sample.context.detail_read_errors, 1)
        m.spell(False)
        m.put(m.held + 0x68, 30)
        sample = m.sample()
        self.assertEqual(sample.context.held_object['kind'], 'miracle_bubble')
        self.assertNotIn('miracle', sample.context.held_object)

    def test_nonspell_and_unknown_objects_are_not_assumed_to_be_bubbles(self):
        for runtime, kind, species in ((b'.?AVCow@@','animal','cow'),
                                        (b'.?AVFootball@@','toy',None),
                                        (b'.?AVUnknownThing@@','object',None)):
            m = HandMemory();m.hold(runtime)
            obj = m.sample().context.held_object
            self.assertEqual(obj['kind'], kind)
            self.assertEqual(obj.get('species'), species)
            self.assertNotIn('miracle', obj)

    def test_scaffolding_in_player_hand_retains_type_and_inspectable_id(self):
        m = HandMemory();m.hold(b'.?AVScaffold@@')
        hand = m.sample().context
        obj = hand_contents(hand)['held_object']
        self.assertEqual(obj['kind'], 'scaffolding')
        self.assertEqual(obj['name'], 'scaffolding')
        f = frame();f['observation']['god_hand'] = asdict(hand)
        result = DirectCreatureToolSession(lambda:f).call('inspect_object', {'object_id':obj['object_id']})
        self.assertEqual(result['held_by'], 'player_god')
        self.assertEqual(result['object']['type'], 'scaffolding')
        self.assertFalse(result['executed'])
        context = json.loads(preview_prompt(playable(god_hand=hand), None).context_json)
        projected = model_turn({'snapshot':summarize_context(context)})['snapshot']
        self.assertEqual(projected['god_hand']['held_object']['type'], 'scaffolding')

    def test_player_hand_reuses_world_categories_and_fence_subtype(self):
        for runtime, expected in ((b'.?AVBonfire@@','bonfire'),(b'.?AVFragment@@','debris')):
            m = HandMemory();m.hold(runtime)
            self.assertEqual(m.sample().context.held_object['kind'],expected)
        m = HandMemory();m.hold(b'.?AVMobileStatic@@')
        info = m.held + 0x1000
        m.typed(info,b'.?AVGMobileStaticInfo@@')
        m.put(m.held+0x28,info);m.put(info+0x120,56)
        self.assertEqual(m.sample().context.held_object['kind'],'fence')

    def test_wrong_owner_session_and_unstable_pointer_are_rejected(self):
        for offset, address in ((live.HAND_STATUS,'controller'),(live.STATUS_INTERFACE,'status')):
            m = HandMemory();m.put(getattr(m,address) + offset, 0)
            with self.assertRaises(ValueError):m.sample()
        m = HandMemory()
        with self.assertRaises(ValueError):m.sample({**m.observer,'session':6})
        m = HandMemory();m.hold();read = m.reader.read;count = 0
        def changing(address,size):
            nonlocal count
            if address == m.controller + live.HAND_HELD_OBJECT:
                count += 1
                return struct.pack('<I', m.held if count % 2 else 0)
            return read(address,size)
        m.reader.read = changing
        with self.assertRaises(ValueError):m.sample()

    def test_unavailable_stale_and_other_creature_clear_previous_contents(self):
        m = HandMemory();m.spell(True);sample=m.sample()
        obs=playable(session=sample.session,creature_id=sample.creature_id,
                     god_hand=GodHandContext('holding_object',{'kind':'tree'}))
        self.assertEqual(live.with_god_hand(obs,sample,sample.observed_at).god_hand,sample.context)
        for bad in (replace(sample,session='other'),replace(sample,creature_id='other'),
                    replace(sample,observed_at=sample.observed_at-1)):
            self.assertEqual(live.with_god_hand(obs,bad,sample.observed_at).god_hand,GodHandContext())
        self.assertEqual(hand_contents(GodHandContext()), {})


class GodHandProjectionTests(TestCase):
    def test_overlapping_views_retain_hand_contents_and_other_hand_metadata(self):
        obj=NearbyObject('tree','tree',10,distance_to_god_hand_engine_units=5)
        surroundings=SurroundingsContext(available=True,nearby_known_objects=(obj,),
            god_hand_available=True,god_hand_objects=(obj,))
        for hand in (GodHandContext('holding_object',{'object_id':'bubble','kind':'miracle_bubble','miracle':'water'}),
                     GodHandContext('activated_miracle',activated_miracle='food'),GodHandContext('empty')):
            observation=playable(surroundings=surroundings,god_hand=hand)
            context=json.loads(preview_prompt(observation,None).context_json)
            compact=summarize_context(context,asdict(observation))
            compact['god_hand'].update(target_id='god_hand',distance_engine_units=10,position={'x':1,'z':2})
            compact['world']={'god_alignment':.5}
            projected=model_turn({'snapshot':compact})['snapshot']
            self.assertTrue(projected['surroundings_also_near_god_hand'])
            self.assertNotIn('surroundings_near_god_hand',projected)
            self.assertEqual(projected['god_hand']['contents'],hand.contents)
            self.assertEqual(projected['god_hand']['alignment'],.5)
            self.assertEqual(projected['god_hand']['target_id'],'god_hand')
            self.assertEqual(projected['god_hand']['distance'],10)
            if hand.activated_miracle:self.assertEqual(projected['god_hand']['activated_miracle'],'food')
            if hand.held_object:self.assertEqual(projected['god_hand']['held_object']['miracle'],'water')

    def test_raw_missing_read_does_not_reuse_legacy_contents(self):
        context={'god_hand':{'contents':'activated_miracle','activated_miracle':'water'}}
        self.assertNotIn('god_hand',summarize_context(context,{'god_hand':asdict(GodHandContext())}))
        self.assertEqual(summarize_context(context)['god_hand'],context['god_hand'])

    def test_inspection_distinguishes_player_and_creature_hands(self):
        f=frame()
        f['observation']['god_hand']=asdict(GodHandContext('holding_object',
            {'kind':'miracle_bubble','object_id':'god-bubble','miracle':'water','private_debug':'omit'}))
        f['observation']['holding']={'holding':True,'kind':'tree','object_id':'creature-tree'}
        f['observation']['creature']={'god_alignment':.4}
        s=DirectCreatureToolSession(lambda:f)
        result=s.call('inspect_object',{'object_id':'god-bubble'})
        self.assertEqual(result['held_by'],'player_god')
        self.assertEqual(result['object']['miracle'],'water')
        self.assertNotIn('private_debug',json.dumps(result))
        self.assertFalse(result['executed'])
        result=s.call('inspect_self',{})
        self.assertEqual(result['god_hand']['held_object']['object_id'],'god-bubble')
        self.assertEqual(result['god_hand']['alignment'],.4)
        self.assertEqual(result['self']['held_object']['object_id'],'creature-tree')
        self.assertFalse(s.inner.selected_action)
