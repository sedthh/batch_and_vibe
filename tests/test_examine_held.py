"""Physical examination uses the held item, without a lookup or replacement."""
import unittest

from bwv.action_probe import ACTIONS, COMMAND, HELD_GESTURES, dummy_decision
from bwv.expressions import EXPRESSION_CONTRACT, speech_expression
from bwv.held_objects import HELD_KINDS
from bwv.tool_docs import tool_documentation
from test_held_actions import HeldHarness
from test_creature_tools import frame
from test_tool_interface import bound


def source_for(h):
    source = frame()
    source['diagnostics'] = {'action_probe':h.probe}
    source['observation']['holding'] = {'holding':True, 'object_id':'held-item',
        'kind':h.probe['held_actions']['kind'], 'details':{'health_engine_units':.7}}
    return source


def bind(h, source=None):
    session, offer, request = bound(h, source_for(h) if source is None else source)
    return offer, session, request


class ExamineHeldTests(unittest.TestCase):
    def harness(self, kind='object'):
        h = HeldHarness(kind)
        h.probe['held_actions']['examine_version'] = 1
        return h

    def gesture_harness(self):
        h = self.harness()
        h.probe['held_actions']['held_gesture_version'] = 1
        return h

    def test_held_cues_select_exact_gesture_without_an_inspection_call(self):
        for emoji, action in (('🔍','look_at_held'), ('👂','shake_held'),
                              ('👃','smell_held'), ('🫳','stroke_held')):
            with self.subTest(emoji=emoji):
                h = self.gesture_harness()
                offer, session, request = bind(h)
                definitions = {d['name']:d for d in session.definitions()}
                self.assertIn('examine', definitions['use_held_object']['parameters']['properties']['action']['enum'])
                self.assertFalse(set(session.actions) & set(HELD_GESTURES))
                result = speech_expression('Here '+emoji, {'candidate':None}, dispatcher=offer)
                self.assertEqual(result['status'], 'waiting_for_native')
                for _ in range(3):h.pending.advance(h.obs(), allowed=True)
                self.assertEqual(len(h.sent), 1)
                self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (ACTIONS[action],7))

    def test_inspection_and_cue_are_one_exact_command_not_random_plus_emoji(self):
        for tool in ('inspect_object','use_held_object'):
            for emoji, action in (('🔍','look_at_held'), ('👂','shake_held'),
                                  ('👃','smell_held'), ('🫳','stroke_held')):
                with self.subTest(tool=tool, emoji=emoji):
                    h = self.gesture_harness()
                    offer, session, _ = bind(h)
                    args = {'object_ids':['held-item']} if tool=='inspect_object' else {'action':'examine'}
                    session.call(tool,args)
                    reply = session.finalize_reply({'answer':'Details '+emoji, 'say':None})
                    self.assertEqual(reply['decision'], {'candidate':action})
                    self.assertFalse(h.sent)
                    offer.dispatch(reply['decision'])
                    expression = speech_expression(reply['answer'],reply['decision'],dispatcher=offer)
                    self.assertEqual(expression['status'], 'selected_with_inspection')
                    for _ in range(3):h.pending.advance(h.obs(), allowed=True)
                    self.assertEqual(len(h.sent),1)
                    self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(ACTIONS[action],7))

    def test_no_cue_keeps_random_inspection_and_first_spoken_cue_wins(self):
        h = self.gesture_harness()
        _, session, _ = bind(h)
        session.call('use_held_object',{'action':'examine'})
        for response, action in (
                ({'answer':'It looks interesting.'}, 'examine_held'),
                ({'answer':'👃', 'say':'👂 then 🫳'}, 'shake_held'),
                ({'answer':'🫳', 'say':''}, 'examine_held'),
                ({'answer':'👋 then 👃'}, 'examine_held')):
            self.assertEqual(session.finalize_reply(response)['decision'],{'candidate':action})

    def test_other_selected_action_keeps_priority_over_held_emoji(self):
        for action, recipe, wire in (('put_down','put_down',12), ('eat','eat_held',14)):
            h = self.gesture_harness()
            h.probe['held_actions'].update(edibility_version=1,can_eat=True)
            offer, session, _ = bind(h)
            session.call('use_held_object', {'action':action})
            reply = session.finalize_reply({'answer':'👃'})
            self.assertEqual(reply['decision'], {'candidate':recipe})
            offer.dispatch(reply['decision'])
            self.assertEqual(speech_expression('👃',reply['decision'],dispatcher=offer)['status'],
                             'skipped_action_conflict')
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(wire,7))

    def test_empty_or_replaced_hand_never_fetches_or_animates_another_item(self):
        for emoji in ('🔍','👂','👃','🫳'):
            h = self.gesture_harness()
            h.probe['held_actions']['target_generation'] = 0
            source = source_for(h)
            source['observation']['holding'] = {'holding':False}
            offer, _, _ = bind(h,source)
            speech_expression(emoji,{'candidate':None},dispatcher=offer)
            h.pending.advance(h.obs(),allowed=True)
            self.assertFalse(h.sent)
            h = self.gesture_harness()
            offer, _, _ = bind(h)
            speech_expression(emoji,{'candidate':None},dispatcher=offer)
            h.probe['held_actions']['target_generation'] = 8
            h.pending.advance(h.obs(),allowed=True)
            self.assertFalse(h.sent)
            self.assertEqual(h.pending.last['status'],'cancelled_held_object_changed')

    def test_old_bridge_never_replaces_explicit_cue_with_random_examination(self):
        for version in (None,0,2):
            h = self.gesture_harness()
            h.probe['held_actions']['held_gesture_version'] = version
            _, session, _ = bind(h)
            self.assertFalse(set(session.inner.action_candidates) & set(HELD_GESTURES))
            session.call('use_held_object',{'action':'examine'})
            self.assertEqual(session.finalize_reply({'answer':'👃'})['decision'],{'candidate':None})
            for action in HELD_GESTURES:
                self.assertIsNone(dummy_decision(h.native,action)['candidate'])

    def test_prompt_explains_held_only_explicit_override_and_separate_stroking(self):
        self.assertIn('require you to be holding something', EXPRESSION_CONTRACT)
        self.assertIn('random, never stroke', EXPRESSION_CONTRACT)
        self.assertIn('choose its exact gesture instead of the random pick', EXPRESSION_CONTRACT)

    def test_public_command_works_for_every_held_kind_without_put_down(self):
        for kind in HELD_KINDS:
            with self.subTest(kind=kind):
                h = self.harness(kind)
                offer, session, request = bind(h)
                definitions = {d['name']:d for d in session.definitions()}
                self.assertIn('examine', definitions['use_held_object']['parameters']['properties']['action']['enum'])
                self.assertNotIn('examine_held', session.menu)
                docs = tool_documentation(session.definitions())
                self.assertIn('physical examination', docs)
                self.assertIn('single call', docs)
                choice = session.call('use_held_object', {'action':'examine', 'target':None})
                self.assertEqual(choice['status'], 'selected_pending_final_reply')
                self.assertEqual(choice['inspection']['object']['object_id'], 'held-item')
                self.assertEqual(choice['inspection']['object']['details']['health'], .7)
                self.assertEqual(choice['inspection']['held_by'], 'creature')
                self.assertFalse(h.sent)
                second = session.call('use_held_object', {'action':'discard', 'target':None})
                self.assertEqual(second['status'], 'action_already_selected')
                offer.dispatch(session.finalize_reply({})['decision'])
                for _ in range(3):
                    h.pending.advance(h.obs(), allowed=True)
                self.assertEqual(len(h.sent), 1)
                packet = COMMAND.unpack(h.sent[0])
                self.assertEqual((packet[1], *packet[-2:]), (6, 23, 7))

    def test_old_bridge_never_offers_examination_even_if_in_candidates(self):
        for version in (None, 0, 2):
            h = self.harness()
            h.probe['held_actions']['examine_version'] = version
            _, session, _ = bind(h)
            self.assertNotIn('examine', session.held_menu)
            self.assertIsNone(dummy_decision(h.native, 'examine_held')['candidate'])
            self.assertIn('put_down', session.held_menu)

    def test_lost_replaced_or_reacquired_object_cancels_without_retargeting(self):
        for token in (0, 8, 9):
            h = self.harness()
            h.enqueue('examine_held')
            h.probe['held_actions']['target_generation'] = token
            h.pending.advance(h.obs(), allowed=True)
            self.assertFalse(h.sent)
            self.assertEqual(h.pending.last['status'], 'cancelled_held_object_changed')

    def test_examination_cannot_target_a_different_object(self):
        h = self.harness()
        _, session, _ = bind(h)
        choice = session.call('use_held_object', {'action':'examine', 'target':'other-object'})
        self.assertEqual(choice['status'], 'invalid_target')
        self.assertIsNone(session.inner.selected_action)
        self.assertFalse(h.sent)

    def test_inspecting_held_object_in_batch_returns_details_and_selects_once(self):
        h = self.harness()
        offer, session, _ = bind(h)
        result = session.call('inspect_object', {'object_ids':['tree-a','held-item','missing','held-item']})
        self.assertNotIn('examination', result['results'][0])
        held = result['results'][1]
        self.assertEqual(held['object']['details']['health'], .7)
        self.assertEqual(held['examination']['status'], 'selected_pending_final_reply')
        self.assertFalse(held['examination']['executed'])
        self.assertEqual(result['results'][2]['status'], 'object_not_currently_known')
        self.assertNotIn('examination', result['results'][3])
        again = session.call('inspect_object', {'object_id':'held-item'})
        self.assertEqual(again['examination']['status'], 'action_already_selected')
        self.assertEqual(again['object']['details']['health'], .7)
        self.assertFalse(h.sent)
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(), allowed=True)
        self.assertEqual(len(h.sent), 1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (23, 7))

    def test_reading_held_details_never_replaces_a_selected_action(self):
        for first in ('eat', 'put_down'):
            h = self.harness('food_pile')
            offer, session, _ = bind(h)
            session.call('use_held_object', {'action':first})
            result = session.call('inspect_object', {'object_ids':['held-item'], 'sections':['details']})
            entry = result['results'][0]
            self.assertEqual(entry['object']['details']['health'], .7)
            self.assertEqual(entry['examination']['status'], 'action_already_selected')
            offer.dispatch(session.finalize_reply({})['decision'])
            h.pending.advance(h.obs(), allowed=True)
            self.assertEqual(len(h.sent), 1)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2], 14 if first=='eat' else 12)

    def test_world_player_held_and_general_self_inspection_remain_read_only(self):
        h = self.harness()
        source = source_for(h)
        source['observation']['god_hand'] = {'contents':'holding_object',
            'held_object':{'object_id':'god-tree', 'kind':'tree'}}
        _, session, _ = bind(h, source)
        result = session.call('inspect_object', {'object_ids':['tree-a','god-tree']})
        self.assertTrue(all('examination' not in o for o in result['results']))
        self.assertEqual(result['results'][1]['held_by'], 'player_god')
        session.call('inspect_self', {})
        self.assertIsNone(session.inner.selected_action)
        self.assertFalse(h.sent)

    def test_old_bridge_and_conversation_only_still_return_held_details(self):
        from bwv.direct_tools import DirectCreatureToolSession
        for old_bridge in (True, False):
            h = self.harness()
            source = source_for(h)
            if old_bridge:
                h.probe['held_actions'].pop('examine_version')
                _, session, _ = bind(h, source)
            else:
                session = DirectCreatureToolSession(lambda:source)
            result = session.call('inspect_object', {'object_id':'held-item'})
            self.assertEqual(result['object']['details']['health'], .7)
            self.assertEqual(result['examination']['status'], 'not_offered')
            self.assertIsNone(session.inner.selected_action)

    def test_item_changed_during_turn_returns_fresh_details_without_animating_replacement(self):
        for operation in ('inspect_object', 'use_held_object'):
            h = self.harness()
            source = source_for(h)
            _, session, _ = bind(h, source)
            source['observation']['holding']['object_id'] = 'replacement'
            h.probe['held_actions']['target_generation'] = 8
            if operation=='inspect_object':
                result = session.call(operation, {'object_id':'replacement'})
                self.assertEqual(result['examination']['status'], 'held_object_changed')
                self.assertEqual(result['object']['object_id'], 'replacement')
            else:
                result = session.call(operation, {'action':'examine'})
                self.assertEqual(result['status'], 'held_object_changed')
                self.assertEqual(result['inspection']['object']['object_id'], 'replacement')
            self.assertIsNone(session.inner.selected_action)
            self.assertFalse(h.sent)

    def test_examination_includes_verified_bubble_spell_without_casting(self):
        h = self.harness('miracle_bubble')
        h.probe['held_miracle'] = {'version':1, 'verified':True,
            'held_generation':7, 'spell':22, 'bubble_spell':22}
        offer, session, _ = bind(h)
        result = session.call('use_held_object', {'action':'examine'})
        self.assertEqual(result['inspection']['object']['miracle'], 'water')
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(), allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:], (23, 7))


if __name__ == '__main__':
    unittest.main()
