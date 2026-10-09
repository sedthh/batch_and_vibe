"""Full provider protocol -> tools -> guarded dispatcher, with no game or network."""
import asyncio
from dataclasses import asdict
import json
from tempfile import TemporaryDirectory
import unittest

import httpx

from bwv.conversation import ConversationRequest
from bwv.creature_tools import CreatureToolSession
from bwv.dispatch import ActionOffer, DecisionDispatcher
from bwv.llm import LLMConfig, LLMProvider
from bwv.profile_names import ProfileNames
from bwv.qa_live import LiveChatRuntime
from test_control import playable
from test_creature_tools import call, frame
from test_llm import envelope, reply
from test_pending_actions import Harness


def action_request(candidates=('look_confused','approach_hand'), *, kind='god_question', conversation_only=False):
    return ConversationRequest(kind, (), {'snapshot':{'candidates':list(candidates)},
                                         'conversation_only':conversation_only})


class ActionCatalogTests(unittest.TestCase):
    def session(self):
        return CreatureToolSession(frame,action_candidates=('look_confused','approach_hand'))

    def test_requires_both_executor_capability_and_request_offer(self):
        for kind, only, candidates in (('observation',True,('look_confused',)),
                                      ('god_question',True,('look_confused',)),('god_question',False,())):
            s=self.session();s.bind_request(action_request(candidates,kind=kind,conversation_only=only))
            self.assertNotIn('do_action',{t['name'] for t in s.definitions()})
        s=CreatureToolSession(frame);s.bind_request(action_request())
        self.assertNotIn('do_action',{t['name'] for t in s.definitions()})
        with self.assertRaises(ValueError):s.bind_request(action_request())

    def test_search_uses_only_bound_capabilities_and_describes_targets(self):
        s=self.session();s.bind_request(action_request(('approach_hand',)))
        self.assertEqual(s.call('search_actions',{'query':'confused'})['actions'],[])
        found=s.call('search_actions',{'query':'come to me'})['actions']
        self.assertEqual([a['action'] for a in found],['approach_hand'])
        self.assertEqual(found[0]['target_options'],[None,'god_hand'])
        self.assertNotIn('keywords',found[0])
        self.assertEqual(s.call('search_actions',{'query':'cast fireball'})['actions'],[])

    def test_only_one_selection_and_no_invented_targets_or_execution(self):
        s=self.session();s.bind_request(action_request())
        self.assertEqual(s.call('do_action',{'action':'cast','target':None})['status'],'not_offered')
        self.assertEqual(s.call('do_action',{'action':'approach_hand','target':'tree-a'})['status'],'invalid_target')
        result=s.call('do_action',{'action':'approach_hand'})
        self.assertFalse(result['executed'])
        self.assertEqual(result['status'],'selected_pending_final_reply')
        self.assertEqual(s.call('do_action',{'action':'look_confused','target':None})['status'],'action_already_selected')
        self.assertEqual(s.finalize_reply(reply())['decision'],{'candidate':'approach_hand'})


class ActionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=TemporaryDirectory();self.runtime=None;self.now=100.;self.sent=[]

    async def asyncTearDown(self):
        if self.runtime:await self.runtime.close()
        self.tmp.cleanup()

    def source(self, **changes):
        obs=playable(self.now,sequence=int(self.now*10),**changes)
        return {'observation':asdict(obs),'diagnostics':{'script_camera_owned':False},
                'prompt':{'system':'Synthetic creature','context':{'body_engine_units':{'health':1}}}}

    def factory(self, obs):
        outer=self
        class Adapter:
            def capture(self, now):return ActionOffer((obs.session,obs.creature_id),
                (('look_confused',b'gesture'),('approach_hand',b'hand')),now+30)
            def still_valid(self,offer,candidate):return True
            def submit(self,packet):outer.sent.append(packet);return True
        return DecisionDispatcher(Adapter(),clock=lambda:self.now)

    def start(self, handler, api='responses', *, factory=None, wait=False, clock=None, source=None):
        provider=LLMProvider(LLMConfig('synthetic-model',api=api),environ={'OPENAI_API_KEY':'synthetic'},
                             transport=httpx.MockTransport(handler),clock=clock or (lambda:self.now))
        self.runtime=LiveChatRuntime(self.tmp.name,provider=provider,clock=clock or (lambda:self.now),
            names_reader=ProfileNames,action_factory=factory or self.factory,wait_actions=wait)
        self.runtime.gate.resume_delay=0;self.runtime.ingest(source or self.source())
        return self.runtime

    async def test_search_select_final_reply_dispatch_once_both_protocols(self):
        for api in ('responses','chat_completions'):
            with self.subTest(api=api):
                wires=[];self.sent=[]
                def handler(req):
                    self.assertEqual(self.sent,[]) # Tool rounds cannot execute anything.
                    wires.append(json.loads(req.content))
                    result=(call(api,'move_to',{'destination':{'special':'god_hand'},'pace':None},'c2') if len(wires)==1 else
                            envelope(api,reply(answer='Coming to your hand.')))
                    return httpx.Response(200,json=result)
                runtime=self.start(handler,api)
                await runtime.question({'question':'come to me'})
                self.assertEqual(self.sent,[b'hand'])
                self.assertEqual(runtime.memory.history[-1]['action_status'],'queued_unconfirmed')
                self.assertEqual(runtime.provider.last_tool_trace[-1]['result']['executed'],False)
                self.assertNotIn('tool_call',json.dumps(list(runtime.memory.history)))
                await runtime.close();self.runtime=None

    async def test_failed_final_reply_discards_selection(self):
        count=0
        def handler(req):
            nonlocal count
            count+=1
            data=(call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None}) if count==1 else
                  envelope(result=reply(decision={'candidate':'invented'})))
            return httpx.Response(200,json=data)
        runtime=self.start(handler)
        with self.assertRaises(RuntimeError):await runtime.question({'question':'make a gesture'})
        self.assertEqual(self.sent,[])

    async def test_speech_repair_retains_selection_and_dispatches_once(self):
        for api in ('responses','chat_completions'):
            wires=[];self.sent=[]
            def handler(req):
                self.assertEqual(self.sent,[])
                wires.append(json.loads(req.content))
                n=len(wires)
                if n==1:data=call(api,'move_to',{'destination':{'special':'god_hand'},'pace':None})
                else:data=envelope(api,reply(answer='{' if n==2 else 'Coming over.'))
                if n==3:self.assertEqual(wires[-1]['tool_choice'],'none')
                return httpx.Response(200,json=data)
            runtime=self.start(handler,api)
            await runtime.question({'question':'come here'})
            self.assertEqual(self.sent,[b'hand'])
            self.assertEqual(len(wires),3)
            self.assertEqual(runtime.memory.history[-1]['answer'],'Coming over.')
            await runtime.close();self.runtime=None

    async def test_cancel_after_selection_never_dispatches(self):
        waiting=asyncio.Event();count=0
        async def handler(req):
            nonlocal count
            count+=1
            if count==1:return httpx.Response(200,json=call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None}))
            waiting.set();await asyncio.Event().wait()
        runtime=self.start(handler)
        task=asyncio.create_task(runtime.question({'question':'make a gesture'}))
        await waiting.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.sent,[])

    async def test_action_with_emoji_does_not_dispatch_second_gesture(self):
        count=0
        def handler(req):
            nonlocal count
            count+=1
            data=(call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None}) if count==1 else
                  envelope(result=reply(answer='Coming. 😕')))
            return httpx.Response(200,json=data)
        runtime=self.start(handler)
        result=await runtime.question({'question':'come to me'})
        self.assertEqual(self.sent,[b'hand'])
        self.assertNotIn('😕',result['reply']['answer'])

    async def test_automatic_turn_can_select_one_action_through_tools(self):
        wires=[]
        def handler(req):
            wires.append(json.loads(req.content))
            data=(call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None})
                  if len(wires)==1 else envelope())
            return httpx.Response(200,json=data)
        runtime=self.start(handler)
        await runtime.force_tick({})
        self.assertIn('move_to',{t['name'] for t in wires[0]['tools']})
        self.assertEqual(self.sent,[b'hand'])

    async def test_menu_during_final_response_keeps_text_without_dispatch(self):
        count=0
        def handler(req):
            nonlocal count
            count+=1
            if count==1:return httpx.Response(200,json=call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None}))
            self.now+=.1
            source=self.source(paused=True);source['diagnostics']['paused']=True
            self.runtime.ingest(source)
            return httpx.Response(200,json=envelope(result=reply(answer='Synthetic reply.')))
        runtime=self.start(handler)
        await runtime.question({'question':'make a gesture'})
        self.assertEqual(self.sent,[])
        self.assertEqual(runtime.memory.history[-1]['answer'],'Synthetic reply.')

    async def test_delayed_observation_resumes_tool_turn_without_replaying_request(self):
        for gap_round in (1,2):
            with self.subTest(gap_round=gap_round):
                self.sent=[];count=0;delayed=asyncio.Event()
                def handler(req):
                    nonlocal count
                    count+=1
                    if count==gap_round:
                        old_source=self.source()
                        self.now+=2
                        self.runtime.ingest(old_source)
                        delayed.set()
                    data=(call('responses','move_to',{'destination':{'special':'god_hand'},'pace':None})
                          if count==1 else envelope(result=reply(answer='Coming over.')))
                    return httpx.Response(200,json=data)
                runtime=self.start(handler)
                task=asyncio.create_task(runtime.question({'question':'come here'}))
                await asyncio.wait_for(delayed.wait(),1)
                await asyncio.sleep(.06)
                self.assertFalse(task.done())
                self.assertEqual(count,gap_round)
                self.assertEqual(self.sent,[])
                self.assertTrue(runtime.gate.waiting_for_observation)
                runtime.ingest(self.source())
                await asyncio.wait_for(task,1)
                self.assertEqual(count,2)
                self.assertEqual(self.sent,[b'hand'])
                self.assertEqual(runtime.memory.history[-1]['answer'],'Coming over.')
                await runtime.close();self.runtime=None

    async def test_selection_waits_for_eligibility_then_sends_once(self):
        h=Harness();h.probe.update(phase='unavailable',reason='native_animation_busy')
        count=0
        def handler(req):
            nonlocal count
            count+=1
            data=envelope(result=reply(answer='😕'))
            return httpx.Response(200,json=data)
        runtime=self.start(handler,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
        await runtime.question({'question':'make a gesture'})
        self.assertEqual(h.sent,[])
        self.assertEqual(runtime.pending_actions.snapshot()['status'],'waiting_for_native')
        h.probe['phase']='ready';h.now+=1;runtime.ingest(h.source());await runtime.step()
        self.assertEqual(len(h.sent),1)
        self.assertEqual(runtime.pending_actions.snapshot()['status'],'queued')
        await runtime.step();self.assertEqual(len(h.sent),1)
