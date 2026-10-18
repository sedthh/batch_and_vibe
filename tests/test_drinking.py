"""Drinking uses native water search, not a selected object or a miracle."""
import unittest

from bwv.action_probe import COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.tool_docs import tool_documentation
from bwv.world_actions import world_offers_available
from test_action_recovery import RecoveryHarness
from test_action_tools import action_request
from test_creature_tools import frame
from test_world_actions import WorldHarness


def bind(harness):
    offer = harness.pending.factory(harness.obs())
    data = offer.offer()
    request = action_request(data['candidates'])
    request.current['snapshot'].update(data)
    session = DirectCreatureToolSession(frame, action_candidates=data['candidates'])
    session.bind_request(request)
    return offer, session


class DrinkingTests(unittest.TestCase):
    def test_direct_self_request_is_documented_and_dispatches_once(self):
        h = WorldHarness()
        offer, session = bind(h)
        definition = next(d for d in session.definitions() if d['name'] == 'do_action')
        self.assertIn('drink', definition['parameters']['properties']['action']['enum'])
        self.assertNotIn('drink', session.spells)
        documentation = tool_documentation(session.definitions())
        self.assertIn('drink uses target=self', documentation)
        self.assertIn('natural water', documentation)
        result = session.call('do_action', {'action': 'drink', 'target': 'self', 'miracle': None})
        self.assertEqual(result['status'], 'selected_pending_final_reply')
        self.assertFalse(h.sent)
        offer.dispatch(session.finalize_reply({})['decision'])
        for _ in range(3):
            h.pending.advance(h.obs(), allowed=True)
        expected = next(e for e in h.probe['world_actions']['offers'] if e['action'] == 'drink')
        self.assertEqual(len(h.sent), 1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (19, expected['token']))

    def test_object_target_cannot_silently_choose_unrelated_water(self):
        h = WorldHarness()
        offer, session = bind(h)
        unrelated = session._targets('move_to')[0]['target_id']
        result = session.call('do_action', {'action': 'drink', 'target': unrelated, 'miracle': None})
        self.assertEqual(result['status'], 'invalid_target')
        self.assertIsNone(session.inner.selected_action)
        self.assertFalse(h.sent)
        entry = next(e for e in h.probe['world_actions']['offers'] if e['action'] == 'drink')
        entry.update(kind='fishing_area', distance_engine_units=20)
        self.assertNotIn('drink', [e['action'] for e in world_offers_available(h.probe)])

    def test_full_hand_is_put_down_before_single_drinking_request(self):
        h = RecoveryHarness()
        h.hold()
        offer, session = bind(h)
        session.call('do_action', {'action': 'drink', 'target': 'self', 'miracle': None})
        offer.dispatch(session.finalize_reply({})['decision'])
        for _ in range(3):
            h.pending.advance(h.obs(), allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent], [12])
        h.dropped()
        h.probe['candidates'] = ['drink']
        for _ in range(3):
            h.pending.advance(h.obs(), allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent], [12, 19])
        self.assertEqual(h.pending.last['hand_preparation'], 'empty_hand_observed')

    def test_old_bridge_without_drink_does_not_offer_it(self):
        h = WorldHarness()
        h.probe['world_actions']['offers'] = [e for e in h.probe['world_actions']['offers']
            if e['action'] != 'drink']
        _, session = bind(h)
        self.assertNotIn('drink', session.menu)
