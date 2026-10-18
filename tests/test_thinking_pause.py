import asyncio
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import threading
import unittest

from bwv.action_probe import COMMAND
from bwv.control import ControlGate
from bwv.conversation import ConversationBroker, ConversationMemory
from bwv.planner import PlanningRequest
from bwv.thinking_pause import ThinkingPause
from test_control import playable
from test_dispatch import frame


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = frame()
        self.state.update(pid=123, session=3, source_age_ms=0)
        self.state['action_probe'].update(epoch=3, offered_at_uptime_ms=100,
            thinking_pause={'version':1, 'available':True},
            conversation_actions={'creature_handle':42,'mental_handle':43})
        self.obs = SimpleNamespace(session='123:3',
            creature_id=hashlib.sha256(b'123:3:42:43').hexdigest()[:24])
        self.sent = []

    def pause(self, send=None):
        def record(pid, packet):
            self.sent.append(COMMAND.unpack(packet));return True
        return ThinkingPause(123, self.obs, read=lambda _:deepcopy(self.state), send=send or record)

    async def test_start_end_are_token_bound_and_cleanup_does_not_need_a_fresh_snapshot(self):
        p = self.pause();await p.begin(30)
        start = self.sent[0]
        self.assertEqual(start[:3], (0x42575041,14,3))
        self.assertGreater(start[3],0)
        self.assertEqual(start[4:],(100,35000,42))
        self.state.clear()
        await p.close();await p.close()
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.sent[1], (*start[:4],0,0,42))

    async def test_unsupported_stale_or_different_creature_does_not_pause(self):
        for case in ('old_bridge','stale','different_creature'):
            with self.subTest(case=case):
                state=deepcopy(self.state)
                if case=='old_bridge':self.state['action_probe'].pop('thinking_pause')
                elif case=='stale':self.state['source_age_ms']=1501
                else:self.obs.creature_id='changed'
                p=self.pause();await p.begin(30);await p.close()
                self.assertEqual(self.sent,[])
                self.state=state

    async def test_lost_start_receipt_still_releases(self):
        def send(pid, packet):
            self.sent.append(COMMAND.unpack(packet))
            if len(self.sent)==1:raise TimeoutError('receipt lost')
            return True
        p=self.pause(send)
        with self.assertRaises(TimeoutError):await p.begin(30)
        await p.close()
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.sent[0][3],self.sent[1][3])

    async def test_final_read_cancels_takeover_even_if_leash_was_already_removed(self):
        p=self.pause();await p.begin(30)
        native=self.state['action_probe']['thinking_pause']
        native.update(token=self.sent[0][3],version=2,status='thinking',control_taken=False)
        self.assertTrue(await p.action_allowed())
        # A native continuation may end the pause without player intervention.
        native.update(active=False,status='native_agenda_changed')
        self.assertTrue(await p.action_allowed())
        native.update(status='leash_control',control_taken=True)
        self.assertFalse(await p.action_allowed())
        # Later observations must not resurrect the old turn's physical action.
        self.assertFalse(await p.action_allowed())
        await p.close()

    async def test_late_action_requires_matching_turn_and_fresh_native_read(self):
        for change in ({'status':'control_takeover'}, {'status':'turn_timeout'}, {'token':0}):
            p=self.pause();await p.begin(30)
            native=self.state['action_probe']['thinking_pause']
            native.update(token=self.sent[-1][3],status='thinking',control_taken=False)
            native.update(change)
            self.assertFalse(await p.action_allowed())
            await p.close()
        p=self.pause();await p.begin(30)
        self.state['action_probe']['thinking_pause'].update(token=self.sent[-1][3],status='thinking')
        self.state['source_age_ms']=1501
        self.assertFalse(await p.action_allowed())
        await p.close()

    async def test_cancellation_cannot_send_end_before_pending_start(self):
        entered=threading.Event();release=threading.Event()
        def send(pid,packet):
            command=COMMAND.unpack(packet)
            if command[-2]:entered.set();release.wait(2)
            self.sent.append(command);return True
        p=self.pause(send)
        async def run():
            try:await p.begin(30)
            finally:await p.close()
        task=asyncio.create_task(run())
        await asyncio.to_thread(entered.wait,1)
        task.cancel();await asyncio.sleep(.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual([r[-2] for r in self.sent],[35000,0])


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gate=ControlGate(resume_delay=0);self.gate.update(playable(),0)
        self.events=[];self.pauses=[];self.active=False

    def pause(self, obs):
        test=self
        class Pause:
            status='thinking'
            control_revision=None
            async def begin(self, timeout):
                test.assertFalse(test.active);test.active=True;test.events.append('begin')
            async def close(self):
                test.active=False;test.events.append('end')
            async def action_allowed(self):return True
        p=Pause();self.pauses.append(p);return p

    def broker(self, respond, **kwargs):
        return ConversationBroker(SimpleNamespace(respond=respond),self.gate,clock=lambda:0,
            memory=ConversationMemory(),thinking_pause_factory=self.pause,**kwargs)

    async def test_questions_and_ticks_pause_across_all_provider_rounds_and_release_on_speech_only(self):
        async def respond(request):
            self.assertTrue(self.active);self.events.append(request.kind)
            await asyncio.sleep(0)
            self.assertTrue(self.active)
            return {'answer':'Hello','decision':{'candidate':None}}
        b=self.broker(respond)
        try:
            await b.ask('Hello')
            await b.choose(PlanningRequest(self.gate.issue(0),self.gate.observation.candidates,None))
            self.assertEqual(self.events,['begin','god_question','end','begin','observation','end'])
        finally:await b.close()

    async def test_errors_and_invalid_replies_always_release(self):
        for error in (True,False):
            async def respond(request):
                self.assertTrue(self.active)
                if error:raise OSError('provider failed')
                return {'answer':'invalid'}
            b=self.broker(respond)
            try:
                with self.assertRaises(RuntimeError):await b.ask('Hello')
                await asyncio.sleep(0)
                self.assertFalse(self.active)
            finally:await b.close()
        self.assertEqual(self.events,['begin','end','begin','end'])

    async def test_cancel_and_queued_question_get_separate_pauses(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def respond(request):
            self.assertTrue(self.active);entered.set();await release.wait()
            return {'answer':'Hello','decision':{'candidate':None}}
        b=self.broker(respond)
        first=asyncio.create_task(b.ask('First'));await entered.wait()
        second=asyncio.create_task(b.ask('Second'));await asyncio.sleep(0)
        self.assertEqual(len(self.pauses),1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        release.set();await second
        self.assertEqual(self.events,['begin','end','begin','end'])
        await b.close()

    async def test_timeout_releases_even_if_provider_ignores_cancellation(self):
        ignored=asyncio.Event();release=asyncio.Event()
        async def respond(request):
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:ignored.set();await release.wait()
            return {'answer':'late','decision':{'candidate':None}}
        b=self.broker(respond,timeout=.01)
        task=asyncio.create_task(b.ask('Hello'))
        await ignored.wait();await asyncio.sleep(0)
        self.assertFalse(self.active)
        self.assertFalse(b._worker.done())
        release.set()
        with self.assertRaises(TimeoutError):await task
        await b.close()

    async def test_close_releases_running_turn(self):
        entered=asyncio.Event()
        async def respond(request):entered.set();await asyncio.Event().wait()
        b=self.broker(respond)
        task=asyncio.create_task(b.ask('Hello'));await entered.wait();await b.close()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.active)

    async def test_native_failure_does_not_break_chat_or_leave_pause_unreleased(self):
        def failing(obs):
            p=self.pause(obs)
            async def begin(timeout):
                self.active=True;self.events.append('begin');raise OSError('lost receipt')
            p.begin=begin;return p
        async def respond(request):
            self.assertFalse(self.active)
            return {'answer':'Hello','decision':{'candidate':None}}
        b=self.broker(respond);b.thinking_pause_factory=failing
        await b.ask('Hello');await b.close()
        self.assertEqual(self.events,['begin','end'])

    async def test_identity_change_during_begin_cancels_instead_of_leaving_question_pending(self):
        def changing(obs):
            p=self.pause(obs);begin=p.begin
            async def changed(timeout):
                await begin(timeout)
                self.gate.update(playable(sequence=1,session='another-save'),0)
            p.begin=changed;return p
        async def respond(request):self.fail('Must not send an obsolete question')
        b=self.broker(respond);b.thinking_pause_factory=changing
        with self.assertRaises(asyncio.CancelledError):await b.ask('Hello')
        await b.close();self.assertFalse(self.active)

    async def test_pause_remains_until_physical_action_selected(self):
        import test_conversation_actions as helpers
        harness=helpers.ConversationActionsTests();harness.setUp()
        harness.broker.thinking_pause_factory=self.pause
        dispatch=harness.send
        def send(pid,packet):
            self.assertTrue(self.active)
            self.events.append('action');return dispatch(pid,packet)
        harness.send=send
        task=await harness.begin();await harness.finish(task)
        await asyncio.sleep(0)
        self.assertEqual(self.events,['begin','action','end'])
        await harness.broker.close()

    async def test_late_reply_does_not_override_takeover_and_new_question_still_works(self):
        import test_conversation_actions as helpers
        h=helpers.ConversationActionsTests();h.setUp()
        h.broker.thinking_pause_factory=self.pause
        task=await h.begin()
        async def taken():return False
        self.pauses[-1].action_allowed=taken
        reply=await h.finish(task)
        self.assertFalse(h.sent)
        self.assertEqual(reply['decision'],{'candidate':None})
        self.assertEqual(reply['answer'],"I'll follow your lead.")
        self.assertEqual(h.broker.last_execution['status'],'conversation_control_changed')
        self.assertEqual(h.broker.memory.history[-1]['decision'],{'candidate':None})
        # The cancellation belongs to this turn, not all later leashed commands.
        await h.broker.ask('Come here now')
        self.assertEqual(len(h.sent),1)
        await h.broker.close()

    async def test_takeover_keeps_speech_but_cannot_dispatch_an_emoji(self):
        import test_conversation_actions as helpers
        h=helpers.ConversationActionsTests();h.setUp()
        h.reply.update(answer='Hello 😕',decision={'candidate':None})
        h.broker.thinking_pause_factory=self.pause
        task=await h.begin()
        async def taken():return False
        self.pauses[-1].action_allowed=taken
        reply=await h.finish(task)
        self.assertFalse(h.sent)
        self.assertIn('Hello',reply['answer'])
        self.assertEqual(h.broker.speech.snapshot(h.now)['last_expression']['status'],
                         'skipped_executor_unavailable')
        await h.broker.close()

    async def test_session_change_during_final_native_check_discards_late_reply(self):
        import test_conversation_actions as helpers
        h=helpers.ConversationActionsTests();h.setUp()
        h.broker.thinking_pause_factory=self.pause
        task=await h.begin()
        async def changed():
            h.gate.update(playable(h.now,sequence=100,session='another-save'),h.now)
            return True
        self.pauses[-1].action_allowed=changed
        with self.assertRaises(RuntimeError):await h.finish(task)
        self.assertFalse(h.sent)
        self.assertFalse(h.broker.memory.history)
        await h.broker.close()
