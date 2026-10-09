from dataclasses import replace
import json
import struct
import unittest

from bwv.control import GateStatus
from bwv.creature import CreatureContext, CreatureTrait
from bwv.holding import HoldingContext
from bwv.prompt import preview_prompt, SYSTEM
from bwv.surroundings import NearbyObject, SurroundingsContext
from bwv.updates import ContextUpdates
from bwv import surroundings_live as live
from test_control import playable
from test_surroundings import NearbyMemory


class AggregationTests(unittest.TestCase):
    def test_full_scan_keeps_more_than_twelve_objects_but_prompt_only_counts(self):
        memory = NearbyMemory()
        total = 20
        for i in range(total):
            node, obj = memory.node+i*0x100, memory.tree+i*0x100
            memory.typed(node, b'.?AVCreatureBeliefSmall@@', 3)
            memory.typed(obj, b'.?AVVillager@@', 4)
            memory.bytes(node+8, struct.pack('<II', node+0x100 if i+1<total else 0,
                                             node-0x100 if i else 0))
            memory.put(node+0x30, obj)
            memory.bytes(obj+0x14, struct.pack('<iif', 65536+i, 0, 0))
        memory.bytes(memory.mental+live.LISTS[0]+8,
                     struct.pack('<III', memory.node, memory.node+(total-1)*0x100, total))
        context = memory.sample().context
        self.assertEqual(len(context.nearby_known_objects), total)
        self.assertEqual(context.matching_objects, total)
        prompt = preview_prompt(playable(candidates=(), surroundings=context), None)
        self.assertEqual(json.loads(prompt.context_json), {'surroundings': {'people_nearby':20}})
        self.assertLess(len(prompt.context_json), 60)

    def test_mixed_counts_omit_zeroes_and_keep_species_traits_in_system(self):
        context = SurroundingsContext(True, (
            NearbyObject('one','villager',2), NearbyObject('two','tree',4),
            NearbyObject('three','tree',5), NearbyObject('four','food_pile',6)))
        creature = CreatureContext('monkey','engine',
            (CreatureTrait('learning_factor',1.03,'engine'),))
        prompt = preview_prompt(playable(candidates=(), surroundings=context, creature=creature),None)
        self.assertEqual(json.loads(prompt.context_json), {'surroundings': {
            'people_nearby':1,'trees_nearby':2,'food_piles_nearby':1}})
        self.assertIn('learning_factor',prompt.system)
        self.assertNotIn('learning_factor',prompt.context_json)
        prose=' '.join(SYSTEM.split())
        self.assertIn("Your alignment can differ from the player's",prose)
        self.assertIn('Partial learning allows attempts that may fail',prose)


class ContextPacketTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = ContextUpdates()
        self.gate = GateStatus(False,('unknown_control',),0)

    def update(self, now, **changes):
        obs = playable(now, candidates=(), **changes)
        return self.scheduler.update(obs,preview_prompt(obs,None),self.gate,now)

    def test_delta_reconstructs_snapshot_and_heartbeat_rebases(self):
        def nearby(count):
            return SurroundingsContext(True, tuple(NearbyObject(str(i),'villager',2) for i in range(count)))
        held = HoldingContext(True,'food','food_pile',hunger_desire=1)
        first = self.update(0, holding=held, surroundings=nearby(10))['last_update']
        self.assertEqual(first['llm_input'], {'snapshot':first['context']})
        state = dict(first['llm_input']['snapshot'])
        second = self.update(2, holding=replace(held,hunger_desire=.9), surroundings=nearby(11))['last_update']
        self.assertEqual(second['llm_input'], {'changes':{
            'surroundings':{'people_nearby':11}, 'hunger_motivation_engine_units':.9}})
        state.update(second['llm_input']['changes'])
        self.assertEqual(state,second['context'])
        third = self.update(4, holding=HoldingContext(False,hunger_desire=.9), surroundings=nearby(11))['last_update']
        self.assertEqual(third['llm_input'], {'no_longer_observed':['happening','held_object']})
        for key in third['llm_input']['no_longer_observed']: state.pop(key)
        self.assertEqual(state,third['context'])
        heartbeat = self.update(19, holding=HoldingContext(False,hunger_desire=.9), surroundings=nearby(11))['last_update']
        self.assertEqual(heartbeat['trigger'],'heartbeat')
        self.assertEqual(heartbeat['llm_input'],{'snapshot':state})

    def test_empty_then_same_context_forces_new_snapshot(self):
        held = HoldingContext(False,hunger_desire=1)
        first = self.update(0,holding=held)['last_update']
        skipped = self.update(2)
        self.assertEqual(skipped['state'],'skipped_empty')
        self.assertEqual(skipped['last_update']['number'],first['number'])
        resumed = self.update(3,holding=held)['last_update']
        self.assertIn('snapshot',resumed['llm_input'])
        self.assertEqual(resumed['number'],first['number']+1)

    def test_pause_identity_and_species_changes_rebase(self):
        held = HoldingContext(False,hunger_desire=1)
        self.update(0,holding=held,creature=CreatureContext('monkey','engine'))
        self.assertEqual(self.update(2,paused=True)['state'],'suspended')
        resumed = self.update(3,holding=held)['last_update']
        self.assertIn('snapshot',resumed['llm_input'])
        switched = self.update(5,holding=held,creature=CreatureContext('horse','engine'))['last_update']
        self.assertIn('snapshot',switched['llm_input'])
        self.assertIn('horse',switched['system'])
        new_session = self.update(6,holding=held,session='other')['last_update']
        self.assertIn('snapshot',new_session['llm_input'])

    def test_count_changes_coalesce_without_a_request_for_every_frame(self):
        def nearby(n):
            return SurroundingsContext(True,tuple(NearbyObject(str(i),'tree',1) for i in range(n)))
        self.update(0,surroundings=nearby(1))
        self.assertEqual(self.update(1,surroundings=nearby(2))['state'],'waiting')
        final = self.update(2,surroundings=nearby(3))['last_update']
        self.assertEqual(final['llm_input'],{'changes':{'surroundings':{'trees_nearby':3}}})
        self.assertEqual(final['provider_calls'],0)
