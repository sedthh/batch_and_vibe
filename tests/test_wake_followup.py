"""Native waking must finish before a selected action can execute."""
import unittest
from tempfile import TemporaryDirectory

from bwv.dispatch import ActionOffer, DecisionDispatcher
from bwv.qa_live import LiveChatRuntime
from bwv.profile_names import ProfileNames
from test_pending_actions import Harness


class WakeFollowupTests(unittest.TestCase):
    def sleep(self,h):
        h.probe.update(candidates=['wake_up'],wake_protocol=3,request_at_uptime_ms=40)

    def wake_done(self,h,**changes):
        h.probe.update(requested_action='wake_up',request_at_uptime_ms=41,
            result_phase='finished',completion='awake_observed',outcome='native_wake_observed',
            result_current_creature=True,script_owned=False,candidates=['look_confused'])
        h.probe.update(changes)

    def enqueue_after_wake(self,h):
        offer=h.pending.factory(h.obs(),wake_result={'queued':True,'wake_after_request':40})
        offer.offer();offer.dispatch({'candidate':'look_confused'})

    def test_wait_for_new_wake_evidence_and_cooldown_then_action(self):
        h=Harness();self.sleep(h);self.enqueue_after_wake(h)
        # A cached previous completed wake is not this request's completion.
        self.wake_done(h,request_at_uptime_ms=40)
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)
        self.wake_done(h)
        h.probe['action_lifecycle']['cooldown_remaining_ms']=5000
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)
        h.probe['action_lifecycle']['cooldown_remaining_ms']=0
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_failed_replaced_or_unconfirmed_wake_never_dispatches(self):
        for changes in ({'completion':'unconfirmed'},{'result_phase':'rejected'},
                        {'requested_action':'approach_hand'},{'result_current_creature':False},
                        {'script_owned':True}):
            h=Harness();self.enqueue_after_wake(h);self.wake_done(h,**changes)
            h.pending.advance(h.obs(),allowed=True)
            self.assertIsNone(h.pending.pending);self.assertFalse(h.sent)

    def test_unknown_wake_receipt_has_no_followup_offer(self):
        h=Harness()
        for result in ({'queued':False,'status':'not_confirmed_no_retry'}, {'queued':True},
                       {'queued':True,'wake_after_request':True}):
            with self.assertRaises(ValueError):h.pending.factory(h.obs(),wake_result=result)

    def test_control_change_and_original_expiry_apply_during_wake(self):
        for expire in (False,True):
            h=Harness();self.enqueue_after_wake(h)
            if expire:h.now+=31
            h.pending.advance(h.obs(),allowed=expire)
            self.assertIsNone(h.pending.pending);self.assertFalse(h.sent)

    def wake_factory(self,h,*,lost=False):
        class Adapter:
            def capture(self,now):return ActionOffer(('synthetic',),(('wake_up',b'wake'),),now+1)
            def still_valid(self,*args):return True
            def submit(self,packet):
                h.sent.append(packet)
                if lost:raise TimeoutError()
                return True
        return lambda obs:DecisionDispatcher(Adapter(),clock=lambda:h.now)

    def test_falls_asleep_while_waiting_wake_then_original_action(self):
        h=Harness();h.pending.wake_factory=self.wake_factory(h);h.enqueue()
        self.sleep(h);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.sent,[b'wake'])
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(h.sent,[b'wake'])
        self.wake_done(h);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),2)

    def test_lost_wake_receipt_never_retries_or_executes_action(self):
        h=Harness();h.pending.wake_factory=self.wake_factory(h,lost=True);h.enqueue()
        self.sleep(h);h.pending.advance(h.obs(),allowed=True)
        self.wake_done(h);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.sent,[b'wake']);self.assertIsNone(h.pending.pending)

    def test_second_sleep_gets_one_more_wake_but_not_an_endless_loop(self):
        h=Harness();h.pending.wake_factory=self.wake_factory(h);self.enqueue_after_wake(h)
        self.wake_done(h,candidates=['wake_up'],wake_protocol=3)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.sent,[b'wake'])
        self.wake_done(h,request_at_uptime_ms=42,candidates=['wake_up'],wake_protocol=3)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'cancelled_asleep_again')
        self.assertEqual(h.sent,[b'wake'])


class WakeConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_wake_precheck_keeps_gesture_and_recovers_once(self):
        h=Harness();helper=WakeFollowupTests();helper.sleep(h)
        h.probe['activity']={'verified':True,'controller_mode':3,'animation':29,'sleep_in_sequence':True}
        factory=helper.wake_factory(h);checks=[]
        def flaky(obs):
            dispatcher=factory(obs)
            def check(*args):
                checks.append(True)
                if len(checks)==1:raise TimeoutError('Synthetic observer read failure')
                return True
            dispatcher.adapter.still_valid=check
            return dispatcher
        with TemporaryDirectory() as directory:
            runtime=LiveChatRuntime(directory,clock=lambda:h.now,names_reader=ProfileNames,
                action_factory=h.factory,wake_factory=flaky,wait_actions=True)
            try:
                runtime.gate.resume_delay=0;runtime.ingest(h.source())
                await runtime.question({'question':'say: 😕'})
                self.assertEqual(runtime.broker.last_wake['status'],'validation_unavailable')
                self.assertFalse(h.sent)
                self.assertFalse(runtime.provider.requests[-1].current['conversation_only'])
                deadline=runtime.pending_actions.pending['expires']
                await runtime.step();self.assertEqual(h.sent,[b'wake'])
                self.assertEqual(runtime.pending_actions.pending['expires'],deadline)
                helper.wake_done(h)
                h.probe['activity']={'verified':True,'controller_mode':0,'animation':0,'sleep_in_sequence':False}
                runtime.ingest(h.source());await runtime.step()
                self.assertEqual(len(h.sent),2)
                await runtime.step();self.assertEqual(len(h.sent),2)
            finally:await runtime.close()

    async def test_direct_question_wakes_then_selected_gesture_executes(self):
        h=Harness();helper=WakeFollowupTests()
        helper.sleep(h)
        h.probe['activity']={'verified':True,'controller_mode':2,'animation':29,'sleep_in_sequence':True}
        with TemporaryDirectory() as directory:
            runtime=LiveChatRuntime(directory,clock=lambda:h.now,names_reader=ProfileNames,
                action_factory=h.factory,wake_factory=helper.wake_factory(h),wait_actions=True)
            try:
                runtime.gate.resume_delay=0;runtime.ingest(h.source())
                await runtime.question({'question':'look confused'})
                self.assertEqual(h.sent,[b'wake'])
                self.assertEqual(runtime.memory.history[-1]['action_status'],'waiting_for_native')
                await runtime.step();self.assertEqual(h.sent,[b'wake'])
                h.now+=.1;helper.wake_done(h)
                h.probe['activity']={'verified':True,'controller_mode':0,'animation':0,'sleep_in_sequence':False}
                runtime.ingest(h.source());await runtime.step()
                self.assertEqual(len(h.sent),2)
                self.assertEqual(runtime.memory.history[-1]['action_status'],'queued_unconfirmed')
                await runtime.step();self.assertEqual(len(h.sent),2)
            finally:await runtime.close()


if __name__=='__main__':unittest.main()
