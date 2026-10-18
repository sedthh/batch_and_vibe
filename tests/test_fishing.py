"""Fishing destinations select catch-and-deliver plans, never an eating fallback."""
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
    return offer, session, request


class FishingTests(unittest.TestCase):
    def test_one_public_action_and_each_named_destination_dispatches_once(self):
        for destination, recipe in (('self', 'fish'), ('god_hand', 'fish_to_hand'),
                                    ('player_temple', 'fish_home')):
            with self.subTest(destination=destination):
                h = WorldHarness()
                offer, session, request = bind(h)
                capabilities = session.project_request(request).current['capabilities']
                self.assertEqual([a for a in capabilities['actions'] if a.startswith('fish')], ['fish'])
                self.assertEqual(capabilities['fishing_destinations'],
                                 ['self', 'god_hand', 'player_temple', 'storage', 'worship_site'])
                doc = tool_documentation(session.definitions())
                self.assertIn('target=god_hand catches and delivers', doc)
                self.assertIn('Delivery does not eat the catch', doc)
                self.assertNotIn('fish_to_hand', doc)
                selected = session.call('do_action', {'action': 'fish', 'target': destination})
                self.assertEqual(selected['status'], 'selected_pending_final_reply')
                self.assertEqual(selected['destination'], destination)
                self.assertFalse(h.sent)
                self.assertEqual(session.call('do_action', {'action': 'fish', 'target': 'self'})['status'],
                                 'action_already_selected')
                decision = session.finalize_reply({})['decision']
                self.assertEqual(decision['candidate'], recipe)
                offer.dispatch(decision)
                for _ in range(3):
                    h.pending.advance(h.obs(), allowed=True)
                entry = next(e for e in h.probe['world_actions']['offers'] if e['action'] == recipe)
                self.assertEqual(len(h.sent), 1)
                self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (19, entry['token']))

    def test_storage_and_worship_accept_object_id_or_search_target(self):
        for recipe, kind in (('fish_to_storage', 'storage'), ('fish_to_worship', 'worship_site')):
            for use_object_id in (True, False):
                with self.subTest(recipe=recipe, object_id=use_object_id):
                    h = WorldHarness()
                    offer, session, _ = bind(h)
                    entry = session.inner.action_targets[recipe]['objects'][0]
                    entry['object_id'] = 'known-destination'
                    target = entry['object_id'] if use_object_id else entry['target_id']
                    result = session.call('do_action', {'action': 'fish', 'target': target})
                    self.assertEqual(result['status'], 'selected_pending_final_reply')
                    self.assertEqual(result['selected_object']['type'], kind.replace('_', ' '))
                    decision = session.finalize_reply({})['decision']
                    self.assertEqual(decision['candidate'], recipe)
                    offer.dispatch(decision)
                    h.pending.advance(h.obs(), allowed=True)
                    expected = next(e for e in h.probe['world_actions']['offers'] if e['action'] == recipe)
                    self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (19, expected['token']))

    def test_missing_delivery_offer_never_falls_back_to_eating(self):
        for destination in ('god_hand', 'player_temple', 'missing-store', None):
            h = WorldHarness()
            h.probe['world_actions']['offers'] = [e for e in h.probe['world_actions']['offers']
                                                if not e['action'].startswith('fish_')]
            _, session, request = bind(h)
            self.assertEqual(session.project_request(request).current['capabilities']['fishing_destinations'], ['self'])
            result = session.call('do_action', {'action': 'fish', 'target': destination})
            self.assertIn(result['status'], ('not_offered', 'invalid_target'))
            self.assertIsNone(session.inner.selected_action)
            self.assertFalse(h.sent)

    def test_full_hand_is_put_down_before_catching_for_delivery(self):
        h = RecoveryHarness()
        h.hold()
        offer, session, _ = bind(h)
        session.call('do_action', {'action': 'fish', 'target': 'god_hand'})
        offer.dispatch(session.finalize_reply({})['decision'])
        for _ in range(3):
            h.pending.advance(h.obs(), allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent], [12])
        h.dropped()
        h.probe['candidates'] = ['fish_to_hand']
        for _ in range(3):
            h.pending.advance(h.obs(), allowed=True)
        expected = next(e for e in h.probe['world_actions']['offers'] if e['action'] == 'fish_to_hand')
        self.assertEqual([COMMAND.unpack(p)[-2] for p in h.sent], [12, 19])
        self.assertEqual(COMMAND.unpack(h.sent[-1])[-1], expected['token'])

    def test_delivery_target_kinds_cannot_be_confused_with_food_sources(self):
        h = WorldHarness()
        offers = h.probe['world_actions']['offers']
        for name, wrong_kind in (('fish_to_hand', 'fishing_area'), ('fish_home', 'temple'),
                                 ('fish_to_storage', 'field'), ('fish_to_worship', 'storage')):
            entry = next(e for e in offers if e['action'] == name)
            entry['kind'] = wrong_kind
            self.assertNotIn(name, [e['action'] for e in world_offers_available(h.probe)])

    def test_search_finds_delivery_buildings_without_duplicate_self_targets(self):
        _, session, _ = bind(WorldHarness())
        found = session.call('find_targets', {'action': 'fish', 'kind': 'storage'})
        self.assertEqual(len(found['action_targets']), 1)
        self.assertEqual(found['action_targets'][0]['type'], 'storage')
        selected = session.call('do_action', {'action': 'fish', 'target': found['action_targets'][0]['target_id']})
        self.assertEqual(selected['status'], 'selected_pending_final_reply')
        self.assertEqual(session.finalize_reply({})['decision']['candidate'], 'fish_to_storage')


if __name__ == '__main__':
    unittest.main()
