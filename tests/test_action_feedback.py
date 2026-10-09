from copy import deepcopy
import json
import unittest

from bwv.action_feedback import action_result
from bwv.cadence import AdaptiveUpdates, CadenceSettings
from bwv.control import GateStatus
from bwv.prompt import CreaturePrompt
from test_control import playable


def finished():
    return {'result_current_creature': True, 'requested_action': 'approach_hand',
            'request_at_uptime_ms': 100, 'result_phase': 'finished',
            'outcome': 'native_plan_changed_after_action', 'plan_observed': True,
            'script_owned': False, 'completion': 'reached_hand_target'}


class ActionFeedbackTests(unittest.TestCase):
    def test_container_arrival_reports_dispatch_position_not_following_a_group(self):
        probe={**finished(),'requested_action':'move_to','requested_target_kind':'animal_group',
               'completion':'reached_position_target'}
        result=action_result(probe,'synthetic')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['evidence'],'reached_target_position_at_dispatch')
        probe['completion']='unconfirmed'
        self.assertEqual(action_result(probe,'synthetic')['status'],'plan_ended')

    def test_attack_reports_requested_fence_without_claiming_impact(self):
        event=action_result({**finished(),'requested_action':'attack','requested_target_kind':'fence'},'fixture')
        self.assertEqual(event['requested_target'],{'kind':'fence'})
        self.assertEqual(event['status'],'plan_ended')
        self.assertEqual(event['evidence'],'native_plan_ended')
        unknown=action_result({**finished(),'requested_target_kind':'arbitrary metadata'},'fixture')
        self.assertNotIn('requested_target',unknown)
    def test_replaced_action_is_interrupted_not_completed(self):
        from bwv.action_feedback import interrupted_results
        probe={'interrupted_actions':[{'action':'cast_water_tree','request_at_uptime_ms':100,
                                       'replacement_request_at_uptime_ms':110}]}
        result=interrupted_results(probe,'synthetic')
        self.assertEqual(result,[{'event_id':'synthetic:action:100:interrupted',
            'action':'cast_water_tree','status':'interrupted','evidence':'replaced_by_new_action'}])
        probe['interrupted_actions'][0]['replacement_request_at_uptime_ms']=99
        self.assertEqual(interrupted_results(probe,'synthetic'),[])

    def test_cast_end_is_not_spell_success(self):
        event=action_result({**finished(),'requested_action':'cast_food'},'fixture')
        self.assertEqual(event['status'],'plan_ended')
        self.assertEqual(event['evidence'],'casting_plan_ended')

    def test_object_plan_ending_alone_does_not_prove_arrival(self):
        probe={**finished(),'requested_action':'approach_object'}
        self.assertEqual(action_result(probe,'synthetic:1')['status'],'plan_ended')
        probe['completion']='reached_object_target'
        result=action_result(probe,'synthetic:1')
        self.assertEqual(result['evidence'],'native_plan_ended')
        self.assertEqual(result['status'],'plan_ended')
        probe['plan_observed']=False
        self.assertNotEqual(action_result(probe,'synthetic:1')['status'],'completed')

    def test_repeated_results_have_distinct_event_ids_after_rearm(self):
        first=action_result(finished(),'synthetic:1')
        second=action_result({**finished(),'request_at_uptime_ms':15000,
                              'phase':'ready'},'synthetic:1')
        self.assertNotEqual(first['event_id'],second['event_id'])
        self.assertEqual(second['status'],'completed')

    def test_wake_receipt_and_plan_are_not_completion(self):
        probe={**finished(),'requested_action':'wake_up'}
        self.assertNotEqual(action_result(probe,'synthetic:1')['status'],'completed')
        probe.update(outcome='native_wake_observed',completion='awake_observed')
        self.assertEqual(action_result(probe,'synthetic:1')['status'],'completed')
        probe['script_owned']=True
        self.assertNotEqual(action_result(probe,'synthetic:1')['status'],'completed')

    def test_completion_requires_explicit_engine_evidence_and_current_identity(self):
        probe = finished()
        event = action_result(probe, 'synthetic:1')
        self.assertEqual(event['status'], 'completed')
        for change in ({'completion':'unconfirmed'}, {'plan_observed':False},
                       {'script_owned':True}, {'requested_action':'look_confused'}):
            self.assertNotEqual(action_result({**probe, **change}, 'synthetic:1')['status'], 'completed')
        for change in ({'result_current_creature':False}, {'result_phase':None},
                       {'request_at_uptime_ms':0}, {'requested_action':None}):
            self.assertIsNone(action_result({**probe, **change}, 'synthetic:1'))
        rejected = {**probe, 'result_phase':'rejected', 'outcome':'hand_target_changed_before_dispatch'}
        self.assertEqual(action_result(rejected, 'synthetic:1')['status'], 'rejected')

    def test_native_plan_end_is_not_gesture_or_sleep_completion(self):
        for action in ('look_confused', 'sleep_here'):
            event=action_result({**finished(),'requested_action':action},'synthetic:1')
            self.assertEqual(event['status'],'plan_ended')
            self.assertEqual(event['evidence'],'native_plan_ended')

    def test_result_triggers_without_waiting_for_tick_but_coalesces_while_busy(self):
        updates=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        gate=GateStatus(True,(),1)
        context={'surroundings':{'people_nearby':2}}
        def update(now, busy=False, allowed=True):
            state=gate if allowed else GateStatus(False,('cutscene',),2)
            return updates.update(playable(now),CreaturePrompt('System',json.dumps(context)),state,now,busy=busy)
        update(0)
        context['action_results']=[action_result(finished(),'synthetic:1')]
        self.assertEqual(update(.5,busy=True)['state'],'conversation_in_progress')
        result=update(1)
        self.assertEqual(result['last_update']['trigger'],'action_result')
        self.assertEqual(update(1.25)['state'],'waiting')
        # A blocked interval sends nothing; resumption uses a fresh full snapshot.
        self.assertEqual(update(2,allowed=False)['state'],'suspended')
        context['surroundings']={'people_nearby':4}
        resumed=update(3)
        self.assertEqual(resumed['last_update']['trigger'],'initial_or_resumed')
        self.assertEqual(resumed['last_update']['context']['surroundings']['people_nearby'],4)
        self.assertEqual(resumed['last_update']['context']['action_results'][0]['event_id'],
                         context['action_results'][0]['event_id'])
