import json
import unittest

from bwv.object_types import FEATURE_MUSHROOM_INFO
from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from test_control import playable
from test_surroundings import NearbyMemory


class ObjectTypesTests(unittest.TestCase):
    def test_bonfire_reaches_compact_summary(self):
        context = self.sample(b'.?AVBonfire@@')
        self.assertEqual([o.kind for o in context.nearby_known_objects], ['bonfire'])
        compact = summarize_context(json.loads(preview_prompt(playable(surroundings=context), None).context_json))
        self.assertEqual(compact['surroundings'], {'bonfires_nearby': 1})

    def test_bonfire_water_target_can_be_inspected_and_selected(self):
        from test_world_actions import WorldHarness
        from test_action_tools import action_request
        from test_creature_tools import frame
        from bwv.direct_tools import DirectCreatureToolSession
        h = WorldHarness()
        offer = next(o for o in h.probe['world_actions']['offers'] if o['action'] == 'cast_water_object')
        offer['kind'] = 'bonfire'
        offer['on_fire'] = True
        data = h.pending.factory(h.obs()).offer()
        request = action_request(data['candidates'])
        request.current['snapshot'].update(data)
        session = DirectCreatureToolSession(frame, action_candidates=data['candidates'])
        session.bind_request(request)
        found = session.call('inspect_surrounding', {'kind':'bonfire', 'limit':3,
            'action':'cast_miracle', 'miracle':'water'})
        target = found['action_targets'][0]
        self.assertEqual(target['type'], 'bonfire')
        self.assertIs(target['on_fire'], True)
        selected = session.call('do_action', {'action':'cast_miracle', 'miracle':'water',
            'target':target['target_id']})
        self.assertNotEqual(selected.get('status'), 'invalid_target')

    def sample(self, name, info_type=None, offset=0, value=0, info=0x6300000):
        memory = NearbyMemory()
        memory.typed(memory.tree, name, 4)
        if info_type:
            memory.put(memory.tree + 0x28, info)
            memory.typed(info, info_type, 5)
            if offset:
                memory.put(info + offset, value)
        return memory.sample().context

    def test_mushrooms_and_toys_reach_summary(self):
        cases = [(b'.?AVBall@@', None, 0, 0, 'toy'),
                 (b'.?AVFootball@@', None, 0, 0, 'toy')]
        cases += [(b'.?AVMobileObject@@', b'.?AVGMobileObjectInfo@@', 0x104, n, 'mushroom')
                  for n in (18, 19)]
        cases += [(b'.?AVMobileStatic@@', b'.?AVGMobileStaticInfo@@', 0x120, n, 'toy')
                  for n in range(399, 404)]
        for name, info, offset, value, kind in cases:
            with self.subTest(name=name, value=value):
                context = self.sample(name, info, offset, value)
                self.assertEqual([o.kind for o in context.nearby_known_objects], [kind])
                compact = summarize_context(json.loads(preview_prompt(playable(surroundings=context), None).context_json))
                self.assertEqual(compact['surroundings'], {kind + 's_nearby': 1})

    def test_unrelated_scenery_and_wrong_metadata_are_not_mushrooms_or_toys(self):
        for name, info, offset, values in (
                (b'.?AVMobileObject@@', b'.?AVGMobileObjectInfo@@', 0x104, (17, 20)),
                (b'.?AVMobileStatic@@', b'.?AVGMobileStaticInfo@@', 0x120, (398, 404)),
                (b'.?AVMobileObject@@', b'.?AVGMobileStaticInfo@@', 0x104, (18, 19))):
            for value in values:
                self.assertEqual(self.sample(name, info, offset, value).nearby_known_objects, ())

    def test_feature_mushroom_is_exact_table_entry(self):
        for info in (FEATURE_MUSHROOM_INFO - 0x124, FEATURE_MUSHROOM_INFO, FEATURE_MUSHROOM_INFO + 0x124):
            context = self.sample(b'.?AVFeature@@', b'.?AVGFeatureInfo@@', info=info)
            self.assertEqual([o.kind for o in context.nearby_known_objects],
                             ['mushroom'] if info == FEATURE_MUSHROOM_INFO else [])

    def test_held_mushroom_and_generic_toy_are_named(self):
        from test_holding_live import HoldingMemory
        for name, info_type, offset, value, kind in (
                (b'.?AVMobileObject@@', b'.?AVGMobileObjectInfo@@', 0x104, 18, 'mushroom'),
                (b'.?AVMobileStatic@@', b'.?AVGMobileStaticInfo@@', 0x120, 401, 'toy')):
            memory = HoldingMemory()
            NearbyMemory.typed(memory, memory.held, name, 20)
            info = 0x6300000
            memory.put(memory.held + 0x28, info)
            NearbyMemory.typed(memory, info, info_type, 21)
            memory.put(info + offset, value)
            context = memory.sample().context
            self.assertEqual(context.kind, kind)
            compact = summarize_context(json.loads(preview_prompt(playable(holding=context), None).context_json))
            self.assertEqual(compact['self']['held_object']['kind'], kind)

    def test_mushroom_pickup_uses_live_request_bound_target(self):
        from test_pickup_actions import PickupHarness
        from test_action_tools import action_request
        from test_creature_tools import frame
        from bwv.creature_tools import CreatureToolSession
        h = PickupHarness()
        h.probe['pickup_action']['targets'][0]['kind'] = 'mushroom'
        data = h.pending.factory(h.obs()).offer()
        request = action_request(data['candidates'])
        request.current['snapshot'].update(data)
        session = CreatureToolSession(frame, action_candidates=data['candidates'])
        session.bind_request(request)
        found = session.call('inspect_surrounding', {'kind':'mushroom', 'limit':3})
        targets = found['action_targets']
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]['kind'], 'mushroom')
        selected = session.call('do_action', {'action':'pick_up', 'target':targets[0]['target_id']})
        self.assertNotEqual(selected.get('status'), 'invalid_target')
