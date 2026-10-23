"""Public tool workflows, consistent observations and bounded read-only queries."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from bwv.action_probe import COMMAND
from bwv.creature_tools import ToolArgumentError
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm_context import summarize_context
from bwv.model_projection import model_snapshot, model_turn
from bwv.object_details import known_objects
from bwv.tool_docs import tool_documentation
from test_creature_tools import frame
from test_tool_interface import bound
from test_world_actions import WorldHarness
from test_held_miracle import HeldMiracleHarness
from test_positions import PositionHarness


class SimplifiedToolsTests(unittest.TestCase):
    def test_public_schema_and_reference_have_one_descriptive_search_and_consistent_targets(self):
        session, _, _ = bound(WorldHarness())
        definitions = {d['name']: d for d in session.definitions()}
        self.assertNotIn('inspect_surrounding', definitions)
        self.assertNotIn('search_actions', definitions)
        for name in ('move_to', 'do_action'):
            self.assertIn('target', definitions[name]['parameters']['properties'])
            self.assertNotIn('destination', definitions[name]['parameters']['properties'])
        doc = tool_documentation(session.definitions())
        self.assertIn('"town_needs"', doc)
        self.assertIn('1..20 items', doc)
        self.assertNotIn('cast_held_miracle', doc)
        self.assertNotIn('### inspect_surrounding', doc)

    def test_search_scopes_details_and_priority_do_not_resolve_or_execute_actions(self):
        source = frame()
        source['observation']['surroundings'] = {'available': True, 'god_hand_available': True,
            'known_objects': [
                {'object_id': 'tree', 'kind': 'tree', 'distance_engine_units': 3, 'distance_to_god_hand_engine_units': 700},
                {'object_id': 'toy', 'kind': 'toy', 'distance_engine_units': 100, 'distance_to_god_hand_engine_units': 700},
                {'object_id': 'store', 'kind': 'storage', 'distance_engine_units': 700, 'distance_to_god_hand_engine_units': 3,
                 'town_id': 'town-a', 'details': {'food_engine_units': 12}}],
            'towns': [{'town_id': 'town-a', 'needs': {'food': .5}}]}
        resolver = unittest.mock.Mock(side_effect=AssertionError('Descriptive lookup must not resolve actions'))
        h = WorldHarness(); session, _, _ = bound(h, source, resolver)
        near = session.call('find_objects', {'scope': 'nearby'})
        self.assertEqual([o['object_id'] for o in near['objects']], ['toy', 'tree'])
        hand = session.call('find_objects', {'scope': 'nearby', 'center': 'god_hand'})
        self.assertEqual([o['object_id'] for o in hand['objects']], ['store'])
        self.assertNotIn('towns', hand); self.assertNotIn('details', hand['objects'][0])
        detailed = session.call('find_objects', {'scope': 'distant', 'detail': 'detailed'})
        self.assertEqual(detailed['objects'][0]['details']['food'], 12)
        self.assertEqual(detailed['towns'], source['observation']['surroundings']['towns'])
        self.assertEqual(session.call('find_objects', {})['matching_known_objects'], 3)
        self.assertIsNone(session.inner.selected_action); self.assertFalse(h.sent)
        resolver.assert_not_called()

    def test_batch_inspection_uses_one_index_and_one_snapshot_with_individual_missing_results(self):
        source = frame()
        source['observation']['surroundings'] = {'known_objects': [
            {'object_id': 'store-a', 'kind': 'storage', 'town_id': 'a'},
            {'object_id': 'store-b', 'kind': 'storage', 'town_id': 'b'}],
            'towns': [{'town_id': 'a', 'needs': {'food': 0}, 'beliefs': []},
                      {'town_id': 'b', 'needs': {'food': 0}, 'beliefs': []}]}
        calls = 0
        def supplier():
            nonlocal calls
            calls += 1
            for town in source['observation']['surroundings']['towns']: town['needs']['food'] = calls
            return deepcopy(source)
        session = DirectCreatureToolSession(supplier)
        with patch('bwv.object_inspection.known_objects', wraps=known_objects) as index:
            result = session.call('inspect_object', {'object_ids': ['store-a', 'missing', 'store-b'], 'sections': ['town_needs']})
        self.assertEqual(index.call_count, 1)
        objects = result['results']
        self.assertEqual([o['object_id'] for o in objects], ['store-a', 'missing', 'store-b'])
        self.assertEqual(objects[0]['town']['needs'], objects[2]['town']['needs'])
        self.assertEqual(objects[1]['status'], 'object_not_currently_known')
        self.assertNotIn('beliefs', objects[0]['town'])
        later = session.call('inspect_object', {'object_ids': ['store-a'], 'sections': ['town_needs']})
        self.assertGreater(later['results'][0]['town']['needs']['food'], objects[0]['town']['needs']['food'])

    def test_sections_preserve_zeroes_and_available_detail_without_mutating_observation(self):
        source = frame()
        source['observation']['surroundings'] = {'known_objects': [
            {'object_id': 'store', 'kind': 'storage', 'town_id': 'town', 'details': {'food_engine_units': 20}}],
            'towns': [{'town_id': 'town', 'tribe': 'celtic', 'control': 'unclaimed', 'needs': {'food': 0},
                       'beliefs': [], 'player_belief_cap_engine_units': 100, 'conversion_reference_engine_units': 20}]}
        original = deepcopy(source); session = DirectCreatureToolSession(lambda: source)
        needs = session.call('inspect_object', {'object_ids': ['store'], 'sections': ['town_needs']})['results'][0]
        self.assertEqual(needs['town']['needs'], {'food': 0})
        self.assertNotIn('details', needs['object']); self.assertNotIn('control', needs['town'])
        ownership = session.call('inspect_object', {'object_ids': ['store'], 'sections': ['ownership']})['results'][0]
        self.assertEqual(ownership['town']['control'], 'unclaimed')
        full = session.call('inspect_object', {'object_ids': ['store']})['results'][0]
        self.assertEqual(full['object']['details']['food'], 20)
        self.assertEqual(full['town']['conversion_reference'], 20)
        self.assertEqual(source, original)

    def test_invalid_batch_and_action_source_do_not_select_or_dispatch(self):
        session, _, _ = bound(WorldHarness())
        for args in ({'object_ids': []}, {'object_ids': [7]}, {'object_ids': ['x'], 'sections': ['invented']},
                     {'object_ids': ['x'] * 21}, {'object_ids': ['x'], 'sections': 'town_needs'}):
            with self.subTest(args=args), self.assertRaises(ToolArgumentError): session.call('inspect_object', args)
        with self.assertRaises(ToolArgumentError):
            session.call('find_targets', {'action': 'attack', 'source': 'held'})
        self.assertIsNone(session.inner.selected_action)

    def test_identified_target_has_no_lease_and_can_be_used_directly_without_second_lookup(self):
        h = WorldHarness()
        entry = next(o for o in h.probe['world_actions']['offers'] if o['action'] == 'move_to')
        entry.update(object_handle=123456)
        resolver = unittest.mock.Mock(side_effect=AssertionError('Already bound identity needs no lookup'))
        session, offer, _ = bound(h, resolver=resolver)
        identity = session._targets('move_to')[0]['object_id']
        target = session.call('find_targets', {'action': 'move_to', 'object_id': identity, 'limit': 1})['action_targets'][0]
        self.assertIn('object_id', target); self.assertNotIn('target_id', target)
        selected = session.call('move_to', {'target': target['object_id'], 'pace': 'run'})
        self.assertEqual(selected['target'], target['object_id'])
        self.assertEqual(selected['status'], 'selected_pending_final_reply')
        self.assertFalse(h.sent); resolver.assert_not_called()
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(), allowed=True); h.pending.advance(h.obs(), allowed=True)
        self.assertEqual(len(h.sent), 1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1], entry['token'])

    def test_held_cast_uses_same_operation_name_without_learned_spell_requirement(self):
        h = HeldMiracleHarness(); offer, session, _ = h.session()
        found = session.call('find_targets', {'action': 'cast_miracle', 'source': 'held'})
        self.assertEqual(found['source'], 'held')
        target = found['action_targets'][0]
        session.call('use_held_object', {'action': 'cast_miracle', 'target': target.get('object_id') or target['target_id']})
        offer.dispatch(session.finalize_reply({})['decision']); h.pending.advance(h.obs(), allowed=True)
        self.assertEqual(len(h.sent), 1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1], 900)

    def test_coordinates_share_target_parameter_and_action_limit(self):
        h = PositionHarness(); session, _, _ = bound(h)
        result = session.call('move_to', {'target': {'x': 5, 'z': 8}, 'pace': 'walk'})
        self.assertEqual(result['position'], {'x': 5, 'z': 8})
        self.assertFalse(result['arrival_confirmed'])
        self.assertEqual(session.call('move_to', {'target': {'x': 6, 'z': 9}})['status'], 'action_already_selected')

    def test_inspection_and_ticks_share_body_motivations_learning_and_activity(self):
        source = frame()
        source['observation']['experience'] = {'body': {'health': .8, 'energy': .6, 'exhaustion': .2},
            'instinct': {'plan_name': 'BringHome', 'target_kind': 'tree', 'target_id': 'tree-a'}}
        source['observation']['holding'] = {'holding': True, 'kind': 'tree', 'object_id': 'tree-a',
            'hunger_desire': 1.4, 'drives': [{'name': 'bring_objects_home', 'value': .2}]}
        source['prompt']['context'] = {'body_engine_units': {'health': .8, 'energy': .6, 'exhaustion': .2},
            'instinct': {'native_plan': 'BringHome', 'intended_target': {'kind': 'tree', 'observed_object_id': 'tree-a'}},
            'native_thought_text': 'Bringing something home', 'learned_miracles': ['water']}
        snapshot = model_snapshot(summarize_context(source['prompt']['context'], source['observation']))
        session, _, _ = bound(WorldHarness(), source)
        detailed = session.call('inspect_self', {})
        self.assertEqual(detailed['self']['body'], snapshot['self']['body'])
        self.assertEqual(detailed['self']['motivations']['hunger_motivation'], snapshot['self']['motivations']['hunger_motivation'])
        self.assertEqual(detailed['self']['held_object'], snapshot['self']['held_object'])
        self.assertEqual(detailed['self']['learning']['learned'], ['water'])
        self.assertEqual(detailed['activity'], snapshot['activity'])
        self.assertNotIn('holding_and_motivations', detailed)
        self.assertNotIn('native_activity', detailed)
        self.assertEqual(model_turn({'snapshot': snapshot})['snapshot'], snapshot)

    def test_cast_recovery_prefers_known_id_over_extra_inspection(self):
        session, _, _ = bound(WorldHarness())
        correction = session.reply_correction({'say': "I'll cast Water."})
        self.assertIn('known object_id directly', correction)
        self.assertNotIn('inspect_surrounding', correction)


if __name__ == '__main__': unittest.main()
