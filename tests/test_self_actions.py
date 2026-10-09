"""Named self-action catalog never enables commands on an older bridge."""
import unittest
from bwv.action_probe import ACTIONS, SELF_ACTIONS, COMMAND, dummy_decision
from bwv.creature_tools import CreatureToolSession
from bwv.action_feedback import action_result
from test_pending_actions import Harness
from test_action_tools import action_request
from test_creature_tools import frame

class SelfActionTests(unittest.TestCase):
    def test_catalog_protocol_dispatches_each_recipe_once(self):
        for action in SELF_ACTIONS:
            h=Harness();h.probe['self_action_catalog']={'version':5,'verified':True}
            h.probe['candidates']=list(SELF_ACTIONS)
            h.enqueue(action);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,action)
            packet=COMMAND.unpack(h.sent[0]);self.assertEqual((packet[1],packet[-2],packet[-1]),(5,ACTIONS[action],0))
            h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_old_bridge_does_not_offer_expanded_catalog(self):
        h=Harness();h.probe['candidates']=list(SELF_ACTIONS)
        for metadata in ({},{'version':5,'verified':False},{'version':6,'verified':True}):
            h.probe['self_action_catalog']=metadata
            offer=h.pending.factory(h.obs()).offer()
            self.assertFalse(set(SELF_ACTIONS)&set(offer['candidates']))
            self.assertIsNone(dummy_decision(h.native,'wave')['candidate'])

    def test_search_and_selection_require_request_capability(self):
        for action in SELF_ACTIONS:
            s=CreatureToolSession(frame,action_candidates=SELF_ACTIONS)
            s.bind_request(action_request((action,)))
            found=s.call('search_actions',{'query':'available'})['actions']
            self.assertEqual([a['action'] for a in found],[action])
            self.assertEqual(s.call('do_action',{'action':action,'target':'tree-x'})['status'],'invalid_target')
            self.assertEqual(s.call('do_action',{'action':action,'target':None})['status'],'selected_pending_final_reply')

    def test_native_sleep_completion_and_gesture_limits(self):
        base={'result_current_creature':True,'request_at_uptime_ms':1,'result_phase':'finished',
              'requested_action':'sleep_here','outcome':'native_sleep_observed','completion':'sleep_observed','script_owned':False}
        self.assertEqual(action_result(base,'synthetic')['status'],'completed')
        self.assertNotEqual(action_result({**base,'completion':'unconfirmed'},'synthetic')['status'],'completed')
        for action in SELF_ACTIONS[1:]:
            result=action_result({**base,'requested_action':action,'outcome':'native_plan_changed_after_action','plan_observed':True},'synthetic')
            self.assertEqual(result['status'],'plan_ended')

if __name__=='__main__':unittest.main()
