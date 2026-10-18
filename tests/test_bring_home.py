"""A held-item trip home is one bound action, independent of the native variant."""
import unittest

from bwv.action_probe import COMMAND, dummy_decision
from bwv.direct_tools import DirectCreatureToolSession
from bwv.held_objects import HELD_KINDS
from bwv.llm_context import summarize_context, plan_label as current_plan
from bwv.model_projection import model_snapshot
from bwv.feedback_context import FeedbackContext
from bwv.tool_docs import tool_documentation
from test_action_tools import action_request
from test_creature_tools import frame
from test_held_actions import HeldHarness


def bind(harness):
    offer = harness.pending.factory(harness.obs())
    data = offer.offer()
    request = action_request(data['candidates'])
    request.current['snapshot'].update(data)
    session = DirectCreatureToolSession(frame, action_candidates=data['candidates'])
    session.bind_request(request)
    return offer, session, request


class BringHomeTests(unittest.TestCase):
    def test_native_plan_thought_and_motivation_remain_separate_readings(self):
        from test_tool_interface import bound
        from test_world_actions import WorldHarness
        from test_feedback_context import frame as feedback_frame
        for plan in ('BringHome', 'BringFoodHome', 'TakeToyHome'):
            source = frame()
            instinct = {'plan_name':plan, 'name_truncated':False,
                        'target_kind':'tree', 'target_id':'a'*24}
            source['observation']['experience'] = {'instinct':instinct}
            drives = [{'name':'bring_objects_home', 'value':.3}]
            source['observation']['holding'] = {'holding':True, 'kind':'tree', 'drives':drives}
            context = {'instinct':{'native_plan':plan,
                'intended_target':{'kind':'tree', 'observed_object_id':'a'*24}},
                'native_thought_text':'Bringing something home'}
            source['prompt']['context'] = context
            session, _, _ = bound(WorldHarness(), source)
            inspected = session.call('inspect_self', {})
            self.assertEqual(inspected['activity']['native_plan'], current_plan(plan))
            self.assertEqual(inspected['activity']['native_thought_text'], 'Bringing something home')
            self.assertEqual(inspected['self']['motivations']['drives'], drives)
            current = model_snapshot(summarize_context(context, source['observation']))
            self.assertIn('native_plan', current['activity'])
            self.assertEqual(current['activity']['native_plan_target']['object_id'], 'a'*24)
            self.assertNotIn('drives', current['activity'])
            # A sampled intention with a target survives into the event group
            # even if a different native plan begins before the next model tick.
            history = FeedbackContext()
            event = feedback_frame(1, plan=plan)
            event['prompt']['context']['instinct']['intended_target'] = context['instinct']['intended_target']
            history.ingest(event, 1)
            history.ingest(feedback_frame(2, plan='SitDown'), 2)
            observation = history.project({})['events']['native_activity_observed'][0]
            self.assertEqual(observation['native_plan'], current['activity']['native_plan'])
            self.assertEqual(observation['status'], 'observed_intention_not_confirmed')

    def harness(self, kind='tree'):
        h = HeldHarness(kind)
        h.probe['held_actions'].update(bring_home_version=1, can_bring_home=True)
        h.probe['hand_preparation_protocol'] = 1
        return h

    def test_single_general_tool_call_for_every_held_kind_without_put_down(self):
        for kind in HELD_KINDS:
            with self.subTest(kind=kind):
                h = self.harness(kind)
                offer, session, request = bind(h)
                capabilities = session.project_request(request).current['capabilities']
                self.assertIn('bring_home', capabilities['held_object_actions'])
                self.assertNotIn('bring_home', capabilities.get('actions', []))
                documentation = tool_documentation(session.definitions())
                self.assertIn('No separate movement or put-down call is needed', documentation)
                self.assertNotIn('BringFoodHome', documentation)
                self.assertNotIn('TakeToyHome', documentation)
                selected = session.call('use_held_object', {'action':'bring_home', 'target':None})
                self.assertEqual(selected['status'], 'selected_pending_final_reply')
                self.assertFalse(h.sent)
                # The same reply cannot queue another physical action.
                extra = session.call('use_held_object', {'action':'put_down', 'target':None})
                self.assertEqual(extra['status'], 'action_already_selected')
                offer.dispatch(session.finalize_reply({})['decision'])
                for _ in range(5):
                    h.pending.advance(h.obs(), allowed=True)
                self.assertEqual(len(h.sent), 1)
                packet = COMMAND.unpack(h.sent[0])
                self.assertEqual((packet[1], *packet[-2:]), (6, 22, 7))

    def test_old_bridge_or_missing_home_never_offers_bring_home(self):
        for metadata in ({}, {'bring_home_version':1, 'can_bring_home':False},
                         {'bring_home_version':2, 'can_bring_home':True}):
            h = HeldHarness()
            h.probe['held_actions'].update(metadata)
            _, session, _ = bind(h)
            self.assertNotIn('bring_home', session.held_menu)
            self.assertIsNone(dummy_decision(h.native, 'bring_home')['candidate'])
            self.assertIn('put_down', session.held_menu)

    def test_hand_or_home_change_cancels_before_sending(self):
        for changes in ({'target_generation':0}, {'target_generation':8},
                        {'can_bring_home':False}):
            h = self.harness()
            h.enqueue('bring_home')
            h.probe['held_actions'].update(changes)
            h.pending.advance(h.obs(), allowed=True)
            self.assertFalse(h.sent)
            self.assertEqual(h.pending.last['status'], 'cancelled_held_object_changed')

    def test_unknown_destination_does_not_turn_into_an_unrequested_trip(self):
        h = self.harness()
        _, session, _ = bind(h)
        result = session.call('use_held_object', {'action':'bring_home', 'target':'another-house'})
        self.assertEqual(result['status'], 'invalid_target')
        self.assertIsNone(session.inner.selected_action)
        self.assertFalse(h.sent)


if __name__ == '__main__':
    unittest.main()
