import asyncio
from dataclasses import replace
import json
import unittest

from bwv.control import ControlGate
from bwv.conversation import ConversationBroker, ConversationMemory, CreatureSpeech, current_prompt
from bwv.holding import HoldingContext
from bwv.planner import CreaturePlanner, PlanningRequest
from test_control import playable


class Provider:
    def __init__(self,release=None):
        self.release=release;self.entered=asyncio.Event();self.requests=[]
        self.active=0;self.max_active=0
    async def respond(self,request):
        self.requests.append(request);self.active+=1;self.max_active=max(self.max_active,self.active)
        self.entered.set()
        try:
            if self.release:await self.release.wait()
            return {'answer':'A synthetic answer.','decision':{'candidate':None},'memory':'Synthetic lesson.'}
        finally:self.active-=1


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def test_persistent_message_survives_expiry_and_silent_automatic_reply(self):
        speech=CreatureSpeech(2)
        speech.publish('I will watch the field.',0,{'candidate':None},source='action_comment')
        initial=speech.snapshot(0)['last_message']
        self.assertIsNone(speech.snapshot(20)['current'])
        self.assertEqual(speech.snapshot(20)['last_message'],initial)
        for text in ('','  ','😕'):
            speech.publish(text,30,{'candidate':None},source='action_comment')
            self.assertEqual(speech.snapshot(30)['last_message'],initial)
        speech.retain_answer('A full answer.',31)
        self.assertEqual(speech.snapshot(99)['last_message']['text'],'A full answer.')
        speech.clear()
        self.assertIsNone(speech.snapshot(100)['last_message'])

    def setUp(self):
        self.now=[0];self.gate=ControlGate(resume_delay=0);self.gate.update(playable(),0)
    def broker(self,provider,**kwargs):
        kwargs.setdefault('memory',ConversationMemory())
        return ConversationBroker(provider,self.gate,clock=lambda:self.now[0],**kwargs)
    def request(self):
        return PlanningRequest(self.gate.issue(self.now[0]),self.gate.observation.candidates,None)

    async def test_pinned_reply_clears_after_three_silent_ticks_without_losing_history(self):
        class Speaking(Provider):
            async def respond(self, request):
                return {'answer':'I remember the field.' if request.kind=='god_question' else '',
                        'say':'I remember the field.' if request.kind=='god_question' else '👋',
                        'decision':{'candidate':None}}
        broker=self.broker(Speaking(),speech_seconds=None)
        try:
            await broker.ask('Remember the field?')
            for _ in range(2):
                await broker.choose(self.request())
                # Repeated status polling and emoji-only replies don't replace it.
                for _ in range(5):
                    self.assertEqual(broker.speech.snapshot(0)['last_message']['text'],
                                     'I remember the field.')
            await broker.choose(self.request())
            speech=broker.speech.snapshot(0)
            for key in ('current','last_answer','last_message'):
                self.assertIsNone(speech[key])
            self.assertTrue(any(pair.get('answer')=='I remember the field.'
                                for pair in broker.memory.history))
        finally:await broker.close()

    async def test_new_visible_reply_restarts_three_tick_span(self):
        class Speaking(Provider):
            say=''
            async def respond(self, request):
                return {'answer':'Hello.' if request.kind=='god_question' else '',
                        'say':self.say if request.kind!='god_question' else 'Hello.',
                        'decision':{'candidate':None}}
        provider=Speaking();broker=self.broker(provider,speech_seconds=None)
        try:
            await broker.ask('Hello')
            for _ in range(2):await broker.choose(self.request())
            provider.say='The tree is burning.'
            await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['last_message']['text'],'The tree is burning.')
            provider.say=''
            for _ in range(2):await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['last_message']['text'],'The tree is burning.')
            await broker.ask('Hello again')
            for _ in range(2):await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['last_message']['text'],'Hello.')
            await broker.choose(self.request())
            self.assertIsNone(broker.speech.snapshot(0)['last_message'])
        finally:await broker.close()

    async def test_failed_automatic_turn_does_not_age_reply(self):
        class Failing(Provider):
            fail=False
            async def respond(self, request):
                if self.fail:raise OSError('Synthetic failure')
                return await super().respond(request)
        provider=Failing();broker=self.broker(provider)
        try:
            await broker.ask('Hello')
            for _ in range(2):await broker.choose(self.request())
            provider.fail=True
            with self.assertRaises(RuntimeError):await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['last_message']['text'],'A synthetic answer.')
            provider.fail=False
            await broker.choose(self.request())
            self.assertIsNone(broker.speech.snapshot(0)['last_message'])
        finally:await broker.close()

    async def test_expression_only_comes_from_spoken_reply_and_never_grants_control(self):
        class Expressive(Provider):
            async def respond(self, request):
                return {'answer': 'I am confused 😕', 'decision': {'candidate': None},
                        'memory': 'A remembered feeling 😕'}
        broker = self.broker(Expressive())
        try:
            self.now[0] = .1
            self.gate.update(playable(.1, in_temple=True), .1)
            reply = await broker.ask('How do you feel?')
            self.assertEqual(reply['answer'], 'I am confused')
            expression = broker.speech.snapshot(.1)['current']['expression']
            self.assertEqual(expression['candidate'], 'look_confused')
            self.assertEqual(expression['status'], 'skipped_executor_unavailable')
            self.assertEqual(reply['decision'], {'candidate': None})
            # A silent automatic response's answer/memory must not trigger speech.
            self.gate.update(playable(.2), .2); self.now[0] = .2
            broker.speech.clear()
            await broker.choose(self.request())
            self.assertIsNone(broker.speech.snapshot(.2)['current'])
        finally:
            await broker.close()

    async def test_emoji_only_direct_answer_is_hidden_but_raw_response_is_debuggable(self):
        class Expressive(Provider):
            async def respond(self, request):
                return {'answer': '😕', 'decision': {'candidate': None}}
        broker = self.broker(Expressive())
        try:
            reply = await broker.ask('How do you feel?')
            self.assertEqual(reply['answer'], '')
            self.assertEqual(broker.memory.snapshot()['recent_pairs'][-1]['answer'], '😕')
            self.assertIsNone(broker.speech.snapshot(0)['current'])
            self.assertIsNone(broker.speech.snapshot(0)['last_answer'])
        finally:
            await broker.close()

    async def test_empty_and_whitespace_responses_are_silent_without_error(self):
        for text in ('', ' \t\n'):
            class Silent(Provider):
                async def respond(self, request):
                    return {'answer': text, 'say': text, 'decision': {'candidate': None}}
            broker = self.broker(Silent())
            try:
                result = await broker.ask('Hello?')
                self.assertEqual(result['answer'], '')
                self.assertEqual(result['say'], '')
                self.assertIsNone(broker.last_error)
                self.assertIsNone(broker.speech.snapshot(0)['current'])
                self.assertIsNone(broker.speech.snapshot(0)['last_answer'])
            finally:
                await broker.close()

    async def test_thinking_is_delayed_direct_reply_only_and_clears_on_cancel(self):
        release=asyncio.Event();provider=Provider(release);broker=self.broker(provider)
        first=asyncio.create_task(broker.choose(self.request()));await provider.entered.wait()
        question=asyncio.create_task(broker.ask('Why?'));await asyncio.sleep(0)
        try:
            self.now[0]=1;self.gate.update(playable(1),1)
            self.assertFalse(broker.answer_status(1)['thinking'])  # queued behind background work
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            async with asyncio.timeout(1):
                while len(provider.requests)<2:await asyncio.sleep(0)
            self.assertFalse(broker.answer_status(1)['thinking'])
            self.now[0]=2;self.gate.update(playable(2),2)
            self.assertTrue(broker.answer_status(2)['thinking'])
            question.cancel()
            with self.assertRaises(asyncio.CancelledError):await question
            self.assertFalse(broker.answer_status(2)['thinking'])
            self.assertIsNone(broker.speech.snapshot(2)['last_answer'])
        finally:release.set();await broker.close()

    async def test_full_answer_survives_banner_expiry_and_automatic_comment(self):
        class Speaking(Provider):
            async def respond(self, request):
                return {'answer':'A full explanation.','decision':{'candidate':None},
                        'say':'A brief comment.'}
        broker=self.broker(Speaking(),speech_seconds=1)
        try:
            await broker.ask('Explain?')
            speech=broker.speech.snapshot(2)
            self.assertIsNone(speech['current'])
            self.assertEqual(speech['last_answer']['text'],'A full explanation.')
            self.assertFalse(broker.answer_status(2)['thinking'])
            await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['current']['text'],'A brief comment.')
            self.assertEqual(broker.speech.snapshot(0)['last_answer']['text'],'A full explanation.')
            self.gate.update(playable(.1,session='new-session'),.1);broker._sync_identity()
            self.assertIsNone(broker.speech.snapshot(.1)['last_answer'])
        finally:await broker.close()

    async def test_optional_speech_survives_silent_turn_and_is_saved_in_memory(self):
        class Speaking(Provider):
            async def respond(self, request):
                self.requests.append(request)
                return {'answer':'', 'decision':{'candidate':None},
                        **({'say':'I will wait here.'} if len(self.requests)==1 else {})}
        broker=self.broker(Speaking(),speech_seconds=10)
        try:
            await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(0)['current']['text'],'I will wait here.')
            self.now[0]=.5;self.gate.update(playable(.5),.5)
            await broker.choose(self.request())
            self.assertEqual(broker.speech.snapshot(.5)['current']['created_at'],0)
            self.assertEqual(broker.memory.snapshot()['recent_pairs'][0]['say'],'I will wait here.')
            self.assertNotIn('say',broker.memory.snapshot()['recent_pairs'][1])
            self.assertIsNone(broker.speech.snapshot(10)['current'])
        finally:await broker.close()

    async def test_invalid_speech_does_not_update_memory_or_display(self):
        class Invalid(Provider):
            async def respond(self, request):
                return {'answer':'', 'decision':{'candidate':None},'say':'x'*301,'memory':'bad'}
        broker=self.broker(Invalid())
        try:
            with self.assertRaises(RuntimeError):await broker.choose(self.request())
            self.assertEqual(broker.memory.snapshot()['memory'],'')
            self.assertIsNone(broker.speech.snapshot(0)['current'])
        finally:await broker.close()

    async def test_direct_answer_replaces_speech_and_new_identity_clears_it(self):
        provider=Provider();broker=self.broker(provider,speech_seconds=None)
        try:
            await broker.ask('Hello?')
            self.assertEqual(broker.speech.snapshot(0)['current']['text'],'A synthetic answer.')
            broker.provider=None
            self.now[0]=.1;self.gate.update(playable(.1,session='new-game'),.1)
            with self.assertRaises(RuntimeError):await broker.ask('Hello again?')
            self.assertIsNone(broker.speech.snapshot(.1)['current'])
            self.assertEqual(len(broker.memory.snapshot()['recent_pairs']),1)
        finally:await broker.close()

    async def test_question_bypasses_empty_context_and_temple_but_never_permits_action(self):
        class WantsAction(Provider):
            async def respond(self,request):
                self.requests.append(request)
                return {'answer':'I can talk.','decision':{'candidate':'rest:home'}}
        provider=WantsAction();broker=self.broker(provider)
        self.gate.update(playable(.1,in_temple=True,candidates=(),holding=HoldingContext(False,hunger_desire=1)),.1)
        self.now[0]=.1
        try:
            reply=await broker.ask('How are you?')
            self.assertEqual(reply['decision'],{'candidate':None})
            self.assertEqual(provider.requests[0].current['snapshot'],{})
            self.assertTrue(provider.requests[0].current['conversation_only'])
            self.assertEqual(broker.calls_started,1)
        finally:await broker.close()

    async def test_no_creature_unknown_or_stale_disallows_question_without_call(self):
        provider=Provider();broker=self.broker(provider)
        try:
            for changes in ({'creature_present':False,'creature_id':None},
                            {'creature_present':None},{'creature_id':None},{'connected':False}):
                self.now[0]+=.1;self.gate.update(playable(self.now[0],**changes),self.now[0])
                with self.assertRaises(ValueError):await broker.ask('Hello?')
            self.gate.update(playable(1),1);self.now[0]=5
            with self.assertRaises(ValueError):await broker.ask('Hello?')
            self.assertEqual(provider.requests,[])
        finally:await broker.close()

    async def test_question_priority_tick_merge_and_dispatch_time_snapshot(self):
        release=asyncio.Event();provider=Provider(release);broker=self.broker(provider)
        first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
        tick1=asyncio.create_task(broker.choose(self.request()));await asyncio.sleep(0)
        tick2=asyncio.create_task(broker.choose(self.request()));await asyncio.sleep(0)
        question=asyncio.create_task(broker.ask('Second'));await asyncio.sleep(0)
        self.now[0]=.2
        self.gate.update(playable(.2,holding=HoldingContext(False,hunger_desire=7)),.2)
        release.set()
        try:
            await first;await question;await tick2
            with self.assertRaises(asyncio.CancelledError):await tick1
            self.assertEqual([r.kind for r in provider.requests],['god_question','god_question','observation'])
            self.assertEqual(provider.requests[1].current['snapshot']['hunger_motivation_engine_units'],7)
            self.assertEqual(provider.max_active,1)
            self.assertEqual(broker.merged_ticks,1)
        finally:await broker.close()

    async def test_creature_loss_blocks_queued_question_and_discards_active_reply(self):
        release=asyncio.Event();provider=Provider(release);broker=self.broker(provider)
        first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
        second=asyncio.create_task(broker.ask('Second'));await asyncio.sleep(0)
        self.now[0]=.2;self.gate.update(playable(.2,creature_present=False,creature_id=None),.2)
        release.set()
        try:
            for task in (first,second):
                with self.assertRaises(RuntimeError):await task
            self.assertEqual(len(provider.requests),1)
            self.assertEqual(list(broker.memory.history),[])
            self.assertIsNone(broker.speech.snapshot(.2)['current'])
        finally:await broker.close()

    async def test_session_change_does_not_import_old_answer_or_memory(self):
        release=asyncio.Event();provider=Provider(release);broker=self.broker(provider)
        first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
        self.now[0]=.2;self.gate.update(playable(.2,session='loaded-save'),.2);release.set()
        try:
            with self.assertRaises(RuntimeError):await first
            self.assertEqual(broker.memory.notes,'')
            await broker.ask('New timeline?')
            self.assertEqual(len(provider.requests[-1].messages),2)
        finally:await broker.close()

    async def test_shared_planner_lane_and_manual_override_during_block(self):
        provider=Provider();broker=self.broker(provider)
        planner=CreaturePlanner(broker,gate=self.gate,clock=lambda:self.now[0])
        try:
            planner.tick();await planner._task
            self.assertEqual(planner.reflections_completed,1)
            self.now[0]=.2;planner.update(playable(.2,cutscene=True));planner.tick()
            await broker.ask('Can you hear me?')
            self.assertEqual(len(provider.requests),2)
            self.assertIsNone(planner.take_proposal())
        finally:await planner.close();await broker.close()

    async def test_automatic_intent_uses_existing_planner_target_validation(self):
        from bwv.actions import PickupTarget, ActionIntent
        class IntentProvider(Provider):
            async def respond(self,request):
                self.requests.append(request)
                return {'answer':'I plan to pick up a tree.',
                        'decision':ActionIntent('pick_up','tree').as_dict()}
        target=PickupTarget('tree','instance','tree','pickup:tree',2,True,True)
        self.gate.update(playable(.1,candidates=(target.candidate,),pickup_targets=(target,)),.1)
        self.now[0]=.1;provider=IntentProvider();broker=self.broker(provider)
        planner=CreaturePlanner(broker,gate=self.gate,clock=lambda:self.now[0])
        try:
            planner.tick();await planner._task
            self.assertEqual(planner.take_proposal().resolved_action.target.object_id,'tree')
            self.assertEqual(broker.memory.history[-1]['action_status'],'proposal_not_executed')
        finally:await planner.close();await broker.close()

    async def test_losing_control_cancels_automatic_provider_but_question_lane_survives(self):
        cancelled=asyncio.Event()
        class Blocking(Provider):
            async def respond(self,request):
                self.requests.append(request);self.entered.set()
                if request.kind=='observation':
                    try:await asyncio.Event().wait()
                    finally:cancelled.set()
                return {'answer':'Still here.','decision':{'candidate':None}}
        provider=Blocking();broker=self.broker(provider)
        planner=CreaturePlanner(broker,gate=self.gate,clock=lambda:self.now[0])
        try:
            planner.tick();await provider.entered.wait()
            self.now[0]=.2;planner.update(playable(.2,paused=True))
            await asyncio.wait_for(cancelled.wait(),1)
            with self.assertRaises(ValueError):await broker.ask('Can you hear me during pause?')
            self.now[0]=.3;planner.update(playable(.3))
            reply=await broker.ask('Can you hear me after resuming?')
            self.assertEqual(reply['answer'],'Still here.')
            self.assertIsNone(planner.take_proposal())
        finally:await planner.close();await broker.close()

    async def test_timeout_does_not_start_overlapping_request_or_accept_late_memory(self):
        release=asyncio.Event()
        class Stubborn(Provider):
            async def respond(self,request):
                self.requests.append(request);self.entered.set()
                if len(self.requests)==1:
                    try:await release.wait()
                    except asyncio.CancelledError:await release.wait()
                return {'answer':'reply','decision':{'candidate':None},'memory':'new'}
        provider=Stubborn();broker=self.broker(provider,timeout=.01)
        first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
        second=asyncio.create_task(broker.ask('Second'))
        try:
            with self.assertRaises(TimeoutError):await first
            self.assertFalse(broker.answer_status(1)['thinking'])
            self.assertEqual(len(provider.requests),1)
            self.assertEqual(broker.memory.notes,'')
            release.set();await second
            self.assertEqual(len(broker.memory.history),1)
        finally:release.set();await broker.close()

    async def test_empty_automatic_snapshot_and_queue_capacity(self):
        self.gate.update(playable(.1,candidates=()),.1);self.now[0]=.1
        provider=Provider();broker=self.broker(provider,max_questions=1)
        try:
            self.assertIsNone(await broker.choose(self.request()))
            self.assertEqual(broker.calls_started,0)
        finally:await broker.close()

    async def test_full_question_queue_rejects_instead_of_dropping_or_merging_questions(self):
        release=asyncio.Event();provider=Provider(release);broker=self.broker(provider,max_questions=1)
        first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
        second=asyncio.create_task(broker.ask('Second'));await asyncio.sleep(0)
        try:
            with self.assertRaises(RuntimeError):await broker.ask('Third')
            release.set();await first;await second
            self.assertEqual([r.current['god_question'] for r in provider.requests],['First','Second'])
        finally:release.set();await broker.close()


class MemoryTests(unittest.TestCase):
    def test_larger_default_history_retains_twelve_recent_event_exchanges(self):
        memory=ConversationMemory()
        for i in range(15):
            memory.record({'snapshot':{'events':{'action_results':[{'event_id':str(i)}]}}},
                          '',{'candidate':None})
        self.assertEqual(len(memory.history),12)
        self.assertEqual(memory.history[0]['input']['snapshot']['events']['action_results'][0]['event_id'],'3')
        self.assertEqual(memory.snapshot()['limits']['history_chars'],24000)

    def test_ephemeral_targets_never_consume_the_saved_conversation_budget(self):
        memory=ConversationMemory(pairs=4,history_chars=2000)
        memory.record({'snapshot':{},'god_question':'Water a tree'},'I will try.',{'candidate':None})
        current={'snapshot':{'trees_nearby':3,'action_targets':['temporary-id'*1000],
                             'candidates':['cast_water_tree']},'god_question':'Then that one'}
        decision={'candidate':'cast_water_tree','target':'temporary-target'}
        for _ in range(3):memory.record(current,'Trying.',decision)
        self.assertEqual(len(memory.history),4)
        self.assertEqual(memory.history[0]['input']['god_question'],'Water a tree')
        self.assertEqual(memory.history[-1]['input']['snapshot'],{'trees_nearby':3})
        self.assertEqual(memory.history[-1]['decision'],{'candidate':'cast_water_tree'})
        self.assertIn('action_targets',current['snapshot'])
        self.assertIn('target',decision)
        self.assertLessEqual(len(json.dumps(list(memory.history))),2000)

    def test_status_ticks_preserve_recent_direct_conversation_within_existing_limits(self):
        memory=ConversationMemory(pairs=4,history_chars=2000)
        for question in ('Water a tree','Then the field'):
            memory.record({'snapshot':{},'god_question':question},'Trying.',{'candidate':None})
        for i in range(20):
            memory.record({'snapshot':{'trees_nearby':i}},'',{'candidate':None})
        self.assertEqual([p['input']['god_question'] for p in memory.history if 'god_question' in p['input']],
                         ['Water a tree','Then the field'])
        self.assertEqual(memory.history[-1]['input']['snapshot']['trees_nearby'],19)
        self.assertEqual(len(memory.history),4)
        self.assertLessEqual(len(json.dumps(list(memory.history))),2000)

    def test_speech_expiry_replacement_and_indefinite_duration(self):
        speech=CreatureSpeech(3)
        speech.publish('First',10,{'candidate':'rest'},source='action_comment')
        self.assertEqual(speech.snapshot(12)['current']['action_status'],'proposal_not_executed')
        speech.publish('Second',12,{'candidate':None},source='god_question')
        self.assertEqual(speech.snapshot(14)['current']['text'],'Second')
        self.assertIsNone(speech.snapshot(15)['current'])
        speech=CreatureSpeech(None)
        speech.publish('Persistent',0,{'candidate':None},source='god_question')
        self.assertEqual(speech.snapshot(100000)['current']['text'],'Persistent')
        speech.clear();self.assertIsNone(speech.snapshot(100001)['current'])
        for value in (0,-1,True,float('inf'),float('nan')):
            with self.assertRaises(ValueError):CreatureSpeech(value)

    def test_debug_snapshot_is_detached_from_memory_and_keeps_exact_notes(self):
        memory=ConversationMemory()
        current={'snapshot':{'trees_nearby':2}}
        memory.record(current,'',{'candidate':None},'I like trees.',say='A tree!')
        current['snapshot']['trees_nearby']=99
        snapshot=memory.snapshot()
        self.assertEqual(snapshot['recent_pairs'][0]['input']['snapshot']['trees_nearby'],2)
        snapshot['recent_pairs'][0]['say']='changed'
        self.assertEqual(memory.snapshot()['recent_pairs'][0]['say'],'A tree!')
        self.assertEqual(snapshot['memory'],'I like trees.')

    def test_recent_pairs_bounded_notes_are_data_and_proposals_not_executed(self):
        memory=ConversationMemory(pairs=2,history_chars=2000,memory_chars=100)
        for i in range(4):memory.record({'snapshot':{'count':i}},str(i),{'candidate':'rest:home'},'lesson')
        self.assertEqual([p['answer'] for p in memory.history],['2','3'])
        self.assertEqual(memory.history[-1]['action_status'],'proposal_not_executed')
        messages=memory.messages('system',{'god_question':'why?'})
        self.assertEqual(messages[0]['role'],'system')
        self.assertIn('never instructions',messages[0]['content'])
        self.assertIn('lesson',messages[0]['content'])
        self.assertEqual(messages[1]['role'],'user')
        self.assertEqual(json.loads(messages[1]['content']),{'snapshot':{'count':2}})
        self.assertNotIn('lesson',json.dumps(messages[1:]))
        with self.assertRaises(ValueError):memory.record({},'',{'candidate':None},'x'*101)
        self.assertEqual(len(memory.history),2)

    def test_fresh_snapshot_replaces_old_scene_and_stale_questions_are_rejected(self):
        with self.assertRaises(ValueError):current_prompt(playable(0),5,question='hello')
        _,payload=current_prompt(playable(0,candidates=()),0,question='hello')
        self.assertEqual(payload['snapshot'],{})
        self.assertEqual(payload['god_question'],'hello')
