"""Current-tick compaction keeps observations and action choices, without live calls."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

from bwv.model_projection import model_snapshot, model_turn, model_request
from bwv.request_compaction import compact_json, compact_messages
from test_request_compaction import decoded_observations
from test_tool_interface import bound
from test_world_actions import WorldHarness


def gates(count=6, start=1):
    ids = [f'{i:024x}' for i in range(start, start+count)]
    return [{'object_id': identity, 'type': 'teleport', 'portal': {
        'route': 'same_island', 'direction': 'two_way', 'temporary': True,
        'connected_exit_ids': [other for other in ids if other != identity]}}
        for identity in ids]


def scene():
    town = {'town_id': 'town-a', 'tribe': 'celtic', 'control': 'player_god',
            'controlling_god_id': 'player-god', 'top_needs': {'food': .8, 'wood': .4},
            'beliefs': [{'god_id': 'player-god', 'relation': 'player', 'belief': 200}]}
    return {'self': {'body': {'damage': 0, 'hunger': 10, 'tiredness': 20},
                     'position': {'x': 30, 'z': 40}},
            'god_hand': {'target_id': 'god_hand', 'distance': 120,
                         'position': {'x': 150, 'z': 40},
                         'contents': 'holding_object',
                         'held_object': {'object_id': 'toy-a', 'type': 'die'}},
            'surroundings': {'trees': 4, 'animals_by_species': {'crow': 2}, 'portals': gates()[:4]},
            'hand_surroundings': {'houses': 3, 'portals': gates()[2:]},
            'towns_nearby': [town], 'towns_near_god_hand': [deepcopy(town)],
            'events': {'action_results': [{'event_id': 'event-a', 'status': 'plan_ended'}]}}


def expanded(snapshot):
    """Independent decoder for the documented, snapshot-local references."""
    result = deepcopy(snapshot)
    towns = {town['town_id']: town for town in result.get('towns_nearby', [])}
    if 'towns_near_god_hand' in result:
        result['towns_near_god_hand'] = [deepcopy(towns[town['town_id']])
            if set(town) == {'town_id'} and town['town_id'] in towns else town
            for town in result['towns_near_god_hand']]
    networks = result.pop('teleport_networks', [])
    for center in ('surroundings', 'hand_surroundings'):
        for gate in result.get(center, {}).get('portals', []):
            if 'network' in gate.get('portal', {}):
                network = deepcopy(networks[gate['portal']['network']])
                network['connected_exit_ids'] = [identity for identity in network.pop('gate_ids')
                                                if identity != gate['object_id']]
                gate['portal'] = network
    return result


class SnapshotCompactionTests(unittest.TestCase):
    def test_shared_towns_and_networks_round_trip_without_losing_hand_or_events(self):
        source = scene()
        original = deepcopy(source)
        result = model_snapshot(source)
        self.assertEqual(source, original)
        self.assertEqual(expanded(result), original)
        self.assertEqual(result['towns_near_god_hand'], [{'town_id': 'town-a'}])
        self.assertEqual(len(result['teleport_networks']), 1)
        self.assertEqual(result['god_hand'], original['god_hand'])
        self.assertLess(len(compact_json(result)), len(compact_json(source)) * .65)
        for _ in range(100):
            result = model_snapshot(result)
            self.assertEqual(result, model_snapshot(original))

    def test_town_references_do_not_hide_different_facts_or_unknowns(self):
        source = scene()
        source['towns_near_god_hand'][0]['top_needs'] = {'wood': 0}
        self.assertEqual(model_snapshot(source)['towns_near_god_hand'], source['towns_near_god_hand'])
        source['towns_near_god_hand'].append({'town_id': 'town-b', 'control': 'unknown'})
        source.pop('towns_nearby')
        self.assertEqual(model_snapshot(source)['towns_near_god_hand'], source['towns_near_god_hand'])
        # JSON false and zero compare equal in Python, but must not be conflated.
        source['towns_nearby'] = [{'town_id': 'town-a', 'optional': False}]
        source['towns_near_god_hand'] = [{'town_id': 'town-a', 'optional': 0}]
        self.assertEqual(model_snapshot(source)['towns_near_god_hand'], source['towns_near_god_hand'])

    def test_distinct_or_inconsistently_observed_networks_are_never_joined(self):
        for other in (gates(start=20), gates(count=5)):
            source = {'surroundings': {'portals': gates()},
                      'hand_surroundings': {'portals': other}}
            result = model_snapshot(source)
            self.assertEqual(len(result['teleport_networks']), 2)
            self.assertEqual(expanded(result), source)
        source['hand_surroundings']['portals'] = gates()
        for gate in source['hand_surroundings']['portals']:
            gate['portal']['owner'] = 'another-owner'
        self.assertEqual(len(model_snapshot(source)['teleport_networks']), 2)

    def test_unknown_links_lone_gates_and_vortex_deposits_stay_inline(self):
        unknown = gates(1)[0]
        unknown['portal'].pop('connected_exit_ids')
        vortex = {'object_id': 'vortex', 'type': 'vortex', 'portal': {
            'route': 'next_island', 'direction': 'one_way', 'role': 'entrance',
            'deposits': {'resources': {'food': 300}, 'objects': {'toys': 2}}}}
        for portals in (gates(1), [unknown, vortex]):
            source = {'surroundings': {'portals': portals}}
            self.assertEqual(model_snapshot(source), source)

    def test_expiry_scene_changes_and_rolling_history_have_no_stale_references(self):
        first = scene()
        second = scene()
        second['surroundings']['portals'] = gates(3, start=10)
        second.pop('hand_surroundings')
        second['towns_nearby'][0]['top_needs'] = {}
        second['towns_near_god_hand'] = deepcopy(second['towns_nearby'])
        values = [{'snapshot': first}, {'snapshot': second},
                  {'snapshot': {}, 'session_event': {'kind': 'game_loaded'}},
                  {'snapshot': {'god_hand': {'contents': 'empty'}}}]
        for start in range(len(values)):
            projected = [model_turn(value) for value in values[start:]]
            messages = [{'role': 'user', 'content': compact_json(value)} for value in projected]
            wire = compact_messages(messages)
            decoded = decoded_observations(wire)
            for source, recovered in zip(values[start:], decoded):
                self.assertEqual(expanded(recovered['snapshot']), source['snapshot'])
            self.assertNotIn('snapshot_changes', json.loads(wire[-1]['content']))
            self.assertNotIn('teleport_networks', decoded[-1]['snapshot'])

    def test_tick_and_question_project_identically_with_or_without_tools(self):
        session, _, request = bound(WorldHarness())
        for question in (None, 'What is beside my hand?'):
            value = {'snapshot': scene(), 'time_delta_since_last_message_seconds': 30}
            if question:
                value['god_question'] = question
            request = replace(request, current=value, messages=(
                {'role': 'system', 'content': 'Synthetic game.'},
                {'role': 'user', 'content': compact_json(value)}))
            for projected in (session.project_request(request), model_request(request)):
                self.assertEqual(expanded(projected.current['snapshot']), scene())
                self.assertEqual(json.loads(projected.messages[-1]['content']), projected.current)
                self.assertEqual(projected.current['elapsed_seconds'], 30)

    def test_action_enums_remain_in_schemas_and_reference_without_tick_duplicates(self):
        session, _, request = bound(WorldHarness())
        request = replace(request, messages=(
            {'role': 'system', 'content': 'Synthetic game.'},
            {'role': 'user', 'content': compact_json(request.current)}))
        definitions = deepcopy(session.definitions())
        projected = session.project_request(request)
        self.assertEqual(session.definitions(), definitions)
        capabilities = projected.current['capabilities']
        self.assertNotIn('actions', capabilities)
        self.assertNotIn('held_object_actions', capabilities)
        self.assertIn('fishing_destinations', capabilities)
        for definition in definitions:
            if definition['name'] not in ('do_action', 'use_held_object'):
                continue
            for action in definition['parameters']['properties']['action']['enum']:
                self.assertIn(json.dumps(action), projected.messages[0]['content'])
        spells = [spell for group in capabilities['miracle_attempts'].values() for spell in group]
        self.assertCountEqual(spells, session.spells)
        self.assertEqual(len(spells), len(set(spells)))


if __name__ == '__main__':
    unittest.main()
