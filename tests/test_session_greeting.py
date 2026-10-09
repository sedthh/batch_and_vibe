"""Session greetings use existing eligible turns, never extra game or API calls."""
import asyncio
import unittest

from bwv.control import ControlGate
from bwv.conversation import ConversationBroker, ConversationMemory
from bwv.experience import BodyState, Experience
from bwv.planner import PlanningRequest
from bwv.session_greeting import SESSION_GREETING_GUIDANCE
from test_control import playable
from test_conversation import Provider


class GreetingProvider(Provider):
    async def respond(self, request):
        self.requests.append(request)
        return {'answer':'Good to see you!' if request.current.get('session_event') else 'Routine reflection.',
                'decision':{'candidate':None}}


class SessionGreetingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=0
        self.gate=ControlGate(resume_delay=0)
        self.gate.update(playable(experience=Experience(body=BodyState(health=1))),self.now)
        self.provider=GreetingProvider()
        self.memory=ConversationMemory()
        self.broker=ConversationBroker(self.provider,self.gate,clock=lambda:self.now,
            memory=self.memory,session_greetings=True)

    async def asyncTearDown(self):
        await self.broker.close()

    def observe(self, **changes):
        self.now+=1
        self.gate.update(playable(self.now,experience=Experience(body=BodyState(health=1)),**changes),self.now)
        self.broker._sync_identity()

    async def tick(self):
        return await self.broker.choose(PlanningRequest(self.gate.issue(self.now),
            self.gate.observation.candidates,None))

    async def test_one_visible_greeting_then_normal_silent_observations(self):
        await self.tick()
        self.assertEqual(self.provider.requests[0].current['session_event'],'connection_started')
        self.assertEqual(self.broker.speech.snapshot(self.now)['last_message']['text'],'Good to see you!')
        self.assertIn(SESSION_GREETING_GUIDANCE,self.provider.requests[0].messages[0]['content'])
        self.observe()
        await self.tick()
        self.assertNotIn('session_event',self.provider.requests[-1].current)
        self.assertEqual(self.broker.speech.snapshot(self.now)['last_message']['text'],'Good to see you!')
        self.assertEqual(len(self.provider.requests),2)

    async def test_scene_changes_species_identity_and_same_session_reconnect_do_not_regreet(self):
        await self.tick()
        for changes in ({'paused':True},{'in_temple':True},{'cutscene':True},
                        {'script_owns_creature':True},{'creature_present':False,'creature_id':None},
                        {'connected':False},{'creature_id':'new-form'}):
            self.observe(**changes)
            self.observe()
            await self.tick()
            self.assertNotIn('session_event',self.provider.requests[-1].current)

    async def test_empty_optional_say_does_not_hide_greeting_answer(self):
        async def greet(request):
            return {'answer':'Good to see you!','say':'','decision':{'candidate':None}}
        self.provider.respond=greet
        await self.tick()
        self.assertEqual(self.broker.speech.snapshot(self.now)['last_message']['text'],'Good to see you!')

    async def test_multiple_loading_transitions_collapse_into_one_new_session_greeting(self):
        await self.tick()
        self.observe(session='save-B',loading=True)
        self.observe(session='save-C',loading=True)
        self.observe(session='save-C')
        await self.tick()
        self.assertEqual(self.provider.requests[-1].current['session_event'],'world_session_changed')
        self.observe(session='save-C')
        await self.tick()
        self.assertNotIn('session_event',self.provider.requests[-1].current)

    async def test_empty_snapshot_does_not_send_or_consume_greeting(self):
        original=self.broker.prompt_builder
        self.broker.prompt_builder=lambda *a,**k:('Example',{'snapshot':{}})
        await self.tick()
        self.assertFalse(self.provider.requests)
        self.assertEqual(self.broker.session_greeting.pending,'connection_started')
        self.broker.prompt_builder=original
        await self.tick()
        self.assertIn('session_event',self.provider.requests[-1].current)

    async def test_direct_question_shares_one_turn_with_saved_memory_and_history(self):
        self.memory.update_note('The player calls Water rain magic.')
        self.memory.record({'god_question':'Call Water rain magic.'},'I understand.',{'candidate':None})
        await self.broker.ask('Do you remember rain magic?')
        request=self.provider.requests[-1]
        self.assertEqual(request.current['god_question'],'Do you remember rain magic?')
        self.assertIn('session_event',request.current)
        self.assertIn('The player calls Water rain magic.',request.messages[0]['content'])
        self.assertTrue(any('Call Water rain magic.' in m['content'] for m in request.messages))
        self.assertEqual(len(self.provider.requests),1)
        self.observe()
        await self.tick()
        self.assertNotIn('session_event',self.provider.requests[-1].current)

    async def test_scene_locked_question_does_not_consume_pending_greeting(self):
        self.observe(in_temple=True)
        await self.broker.ask('Hello')  # Legacy text-only broker path; live UI blocks this.
        self.assertNotIn('session_event',self.provider.requests[-1].current)
        self.observe()
        await self.tick()
        self.assertIn('session_event',self.provider.requests[-1].current)

    async def test_provider_error_keeps_greeting_for_next_existing_turn(self):
        async def fail(request):raise RuntimeError('Synthetic failure')
        self.provider.respond=fail
        with self.assertRaises(RuntimeError):await self.tick()
        self.assertEqual(self.broker.session_greeting.pending,'connection_started')
        self.provider.respond=GreetingProvider.respond.__get__(self.provider)
        self.observe()
        await self.tick()
        self.assertIsNone(self.broker.session_greeting.pending)

    async def test_late_old_session_reply_cannot_consume_new_session_greeting(self):
        release=asyncio.Event()
        blocking=Provider(release)
        self.broker.provider=blocking
        task=asyncio.create_task(self.tick())
        await blocking.entered.wait()
        self.observe(session='save-B')
        release.set()
        with self.assertRaises(RuntimeError):await task
        self.assertEqual(self.broker.session_greeting.pending,'world_session_changed')
        self.broker.provider=self.provider
        await self.tick()
        self.assertEqual(self.provider.requests[-1].current['session_event'],'world_session_changed')
