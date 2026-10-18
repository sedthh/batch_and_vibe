"""Toy identity survives the readers, compact context and public tools."""
from dataclasses import asdict
import unittest

from bwv.direct_tools import DirectCreatureToolSession
from bwv.god_hand_live import runtime_type
from bwv.llm_context import summarize_context
from bwv.model_projection import model_objects, model_turn
from bwv.object_types import object_subtype
from test_creature_tools import frame
from test_god_hand import HandMemory
import test_hand_surroundings
from test_holding_live import HoldingMemory
from test_surroundings import NearbyMemory


TOYS = ((399, 'ball'), (400, 'bowling_ball'), (401, 'cuddly_toy'),
        (402, 'die'), (403, 'skittle'))


def static_toy(memory, address, mesh):
    info = 0x6900000
    NearbyMemory.typed(memory, address, b'.?AVMobileStatic@@', 30)
    NearbyMemory.typed(memory, info, b'.?AVGMobileStaticInfo@@', 31)
    memory.put(address + 0x28, info)
    memory.put(info + 0x120, mesh)
    return info


class ToyTypeTests(unittest.TestCase):
    def test_ground_toys_keep_subtypes_in_both_search_centers_and_inspection(self):
        for mesh, subtype in TOYS:
            for center in ('creature', 'god_hand'):
                with self.subTest(mesh=mesh, center=center):
                    m, _ = test_hand_surroundings.HandSurroundingsTests().memory(
                        object_x=500 if center == 'god_hand' else 10, hand_x=500)
                    static_toy(m, m.tree, mesh)
                    surroundings = m.sample().context
                    obj = surroundings.known_objects[0]
                    self.assertEqual((obj.kind, obj.object_type), ('toy', subtype))
                    data = frame()
                    data['observation']['surroundings'] = asdict(surroundings)
                    session = DirectCreatureToolSession(lambda: data)
                    for detail in ('brief', 'detailed'):
                        found = session.call('find_objects', {'type':'toy', 'center':center,
                            'scope':'nearby', 'detail':detail, 'limit':10})['objects']
                        self.assertEqual(len(found), 1)
                        self.assertEqual(found[0]['type'], subtype.replace('_', ' '))
                        self.assertNotIn('object_type', found[0])
                    inspected = session.call('inspect_object', {'object_id':obj.object_id})
                    self.assertEqual(inspected['object']['type'], subtype.replace('_', ' '))
                    self.assertFalse(inspected['executed'])

    def test_creature_held_toys_reach_tick_and_inspection_without_surroundings(self):
        for mesh, subtype in TOYS:
            with self.subTest(mesh=mesh):
                m = HoldingMemory()
                static_toy(m, m.held, mesh)
                held = m.sample().context
                self.assertEqual((held.kind, held.object_type), ('toy', subtype))
                data = frame()
                data['observation']['holding'] = asdict(held)
                compact = summarize_context({}, data['observation'])
                public = model_turn({'snapshot':compact})['snapshot']['self']['held_object']
                self.assertEqual(public['type'], subtype.replace('_', ' '))
                inspected = DirectCreatureToolSession(lambda: data).call(
                    'inspect_object', {'object_id':held.object_id})
                self.assertEqual(inspected['held_by'], 'creature')
                self.assertEqual(inspected['object']['type'], public['type'])

    def test_god_held_toys_reach_tick_and_inspection_even_when_views_overlap(self):
        for mesh, subtype in TOYS:
            with self.subTest(mesh=mesh):
                m = HandMemory()
                m.hold()
                static_toy(m, m.held, mesh)
                hand = m.sample().context
                self.assertEqual(hand.held_object['object_type'], subtype)
                data = frame()
                data['observation']['god_hand'] = asdict(hand)
                for overlap in (False, True):
                    compact = summarize_context({}, data['observation'])
                    compact['surroundings_also_near_god_hand'] = overlap
                    public = model_turn({'snapshot':compact})['snapshot']['god_hand']['held_object']
                    self.assertEqual(public['type'], subtype.replace('_', ' '))
                inspected = DirectCreatureToolSession(lambda: data).call(
                    'inspect_object', {'object_id':hand.held_object['object_id']})
                self.assertEqual(inspected['held_by'], 'player_god')
                self.assertEqual(inspected['object']['type'], public['type'])

    def test_alias_never_replaces_observed_subtype(self):
        data = frame()
        data['observation']['surroundings'].update(available=True, known_objects=[
            {'object_id':'die', 'kind':'toy', 'object_type':'die', 'distance_engine_units':1},
            {'object_id':'ball', 'kind':'toy', 'object_type':'ball', 'distance_engine_units':2},
            {'object_id':'unknown', 'kind':'toy', 'distance_engine_units':3}])
        session = DirectCreatureToolSession(lambda: data)
        for alias in ('dice', 'ball', 'cuddly toy'):
            result = session.call('find_objects', {'type':alias, 'scope':'nearby',
                'center':'creature', 'detail':'brief', 'limit':10})
            self.assertEqual([(obj['object_id'],obj['type']) for obj in result['objects']],
                             [('die','die'), ('ball','ball'), ('unknown','toy')])

    def test_unverified_metadata_does_not_invent_a_subtype(self):
        m = HoldingMemory()
        info = static_toy(m, m.held, 402)
        read_type = lambda ptr: runtime_type(m.reader, ptr)
        for mesh in (398, 404, 0xFFFFFFFF):
            m.put(info + 0x120, mesh)
            self.assertIsNone(object_subtype(m.reader, m.held, b'.?AVMobileStatic@@', read_type))
        m.put(info + 0x120, 402)
        NearbyMemory.typed(m, info, b'.?AVGMobileObjectInfo@@', 32)
        self.assertIsNone(object_subtype(m.reader, m.held, b'.?AVMobileStatic@@', read_type))

    def test_named_die_retains_its_pickup_target(self):
        from test_action_tools import action_request
        from test_pickup_actions import PickupHarness
        h = PickupHarness()
        h.probe['pickup_action']['targets'][0].update(kind='toy', object_handle=123456)
        offer = h.pending.factory(h.obs())
        offered = offer.offer()
        request = action_request(offered['candidates'])
        request.current['snapshot'].update(offered)
        target = offered['action_targets']['pick_up']['objects'][0]
        data = frame()
        data['observation']['surroundings']['known_objects'] = [
            {'object_id':target['object_id'], 'kind':'toy', 'object_type':'die',
             'distance_engine_units':10}]
        session = DirectCreatureToolSession(lambda: data, action_candidates=offered['candidates'])
        session.bind_request(request)
        found = session.call('find_targets', {'type':'dice', 'action':'pick_up', 'limit':3})
        die = found['action_targets'][0]
        self.assertEqual(die['type'], 'die')
        selected = session.call('do_action', {'action':'pick_up', 'target':die['object_id']})
        self.assertEqual(selected['status'], 'selected_pending_final_reply')
        self.assertEqual(selected['selected_object']['type'], 'die')
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(), allowed=True)
        self.assertEqual(len(h.sent), 1)

    def test_changing_subtype_is_rejected(self):
        m = HoldingMemory()
        info = static_toy(m, m.held, 402)
        original = m.reader.word
        calls = 0
        def changing(address):
            nonlocal calls
            if address == info + 0x120:
                calls += 1
                return 402 if calls == 1 else 399
            return original(address)
        m.reader.word = changing
        with self.assertRaisesRegex(ValueError, 'subtype changed'):
            object_subtype(m.reader, m.held, b'.?AVMobileStatic@@',
                           lambda ptr: runtime_type(m.reader, ptr))

    def test_newly_resolved_distant_target_gets_the_same_toy_description(self):
        from test_action_tools import action_request
        from test_pickup_actions import PickupHarness
        h = PickupHarness()
        offered = h.pending.factory(h.obs()).offer()
        request = action_request(offered['candidates'])
        request.current['snapshot'].update(offered)
        data = frame()
        data['observation']['surroundings']['known_objects'] = [
            {'object_id':'remote-die', 'kind':'toy', 'object_type':'die',
             'distance_engine_units':600}]
        lookups = []
        def resolve(identity, recipe):
            lookups.append((identity, recipe))
            return [{'object_id':identity, 'kind':'toy', 'target_id':'resolved-toy',
                     'distance_engine_units':600}]
        session = DirectCreatureToolSession(lambda: data,
            action_candidates=offered['candidates'], target_resolver=resolve)
        session.bind_request(request)
        result = session.call('find_targets', {'type':'dice', 'action':'pick_up',
                                             'scope':'distant', 'limit':3})
        self.assertEqual(result['action_targets'][0]['type'], 'die')
        self.assertEqual(lookups, [('remote-die', 'pick_up')])

    def test_runtime_ball_types_still_work_without_static_metadata(self):
        for runtime, subtype in ((b'.?AVBall@@','ball'), (b'.?AVFootball@@','football')):
            m = NearbyMemory()
            m.typed(m.tree, runtime, 4)
            obj = m.sample().context.known_objects[0]
            self.assertEqual(model_objects(asdict(obj))['type'], subtype)
