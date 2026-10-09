"""Synthetic delayed conversation -> exact native wire packet, no game access."""
import asyncio
from copy import deepcopy
import hashlib
import json
import unittest

from bwv.action_probe import ConversationActionAdapter, COMMAND
from bwv.conversation import ConversationBroker, ConversationMemory
from bwv.control import ControlGate
from bwv.dispatch import DecisionDispatcher
from bwv.planner import PlanningRequest
from test_control import playable
from test_dispatch import frame


class DelayedReply:
    def __init__(self, reply):
        self.reply = reply
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        self.entered.set()
        await self.release.wait()
        return self.reply


class ConversationActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_direct_questions_mark_physical_actions_as_player_commands(self):
        from bwv.action_probe import PLAYER_COMMAND_FLAG
        self.state['action_probe']['player_command_priority']={'version':1,'available':True}
        task=await self.begin();await self.finish(task)
        self.assertEqual(COMMAND.unpack(self.sent[-1])[1],2|PLAYER_COMMAND_FLAG)
        self.state['action_probe'].update(phase='ready',engine_calls=0)
        request=PlanningRequest(self.gate.issue(self.now),self.gate.observation.candidates,None)
        await self.broker.choose(request)
        self.assertEqual(COMMAND.unpack(self.sent[-1])[1],2)

    async def test_social_interruption_offers_only_direct_movement(self):
        self.state['action_probe'].update(allowed_action_mask=4,candidates=['approach_hand'])
        dispatcher=self.factory(self.gate.observation)
        offered=dispatcher.offer()
        self.assertEqual(offered['candidates'],['approach_hand'])
        result=dispatcher.dispatch({'candidate':'approach_hand'})
        self.assertTrue(result['queued'])
        self.assertEqual(COMMAND.unpack(self.sent[0])[1],2)

    def setUp(self):
        self.now = 0.
        self.sequence = 0
        self.state = frame()
        self.state['action_probe'].update(candidates=['look_confused', 'sleep_here', 'approach_hand'],
            movement={'verified': True, 'distance_to_hand_engine_units': 15},
            conversation_actions={'version': 2, 'max_offer_age_ms': 30000,
                'hand_target_generation': 7, 'creature_handle': 42, 'mental_handle': 43})
        self.identity = ('123:3', hashlib.sha256(b'123:3:42:43').hexdigest()[:24])
        self.gate = ControlGate(resume_delay=0)
        self.refresh()
        self.sent = []
        self.offers = 0
        self.reply = {'answer': 'I will try to come to your hand.', 'decision': {'candidate': 'approach_hand'}}
        self.provider = DelayedReply(self.reply)
        self.broker = ConversationBroker(self.provider, self.gate, clock=lambda: self.now,
            memory=ConversationMemory(), action_factory=self.factory)

    def refresh(self, **changes):
        self.sequence += 1
        self.gate.update(playable(self.now, sequence=self.sequence, session=self.identity[0], creature_id=self.identity[1],
                                  **changes), self.now)

    def factory(self, observation):
        self.offers += 1
        adapter = ConversationActionAdapter(123,
            expected_identity=(observation.session, observation.creature_id),
            read=lambda pid: deepcopy(self.state), send=self.send)
        return DecisionDispatcher(adapter, clock=lambda: self.now)

    def send(self, pid, packet):
        self.sent.append(packet)
        self.state['action_probe'].update(phase='finished', engine_calls=2)
        return True

    async def begin(self):
        task = asyncio.create_task(self.broker.ask('come to me'))
        async with asyncio.timeout(2):
            await self.provider.entered.wait()
        return task

    async def finish(self, task, seconds=3):
        while self.now < seconds:
            self.now = min(seconds, self.now + .25)
            self.refresh()
        self.state['captured_uptime_ms'] = 100 + int(seconds*1000)
        self.state['action_probe']['offered_at_uptime_ms'] = self.state['captured_uptime_ms']
        self.provider.release.set()
        return await task

    async def asyncTearDown(self):
        self.provider.release.set()
        await self.broker.close()

    async def test_delayed_direct_reply_uses_original_offer_and_private_identity(self):
        task = await self.begin()
        current = self.provider.requests[0].current
        self.assertFalse(current['conversation_only'])
        self.assertEqual(current['snapshot']['candidates'], ['look_confused', 'approach_hand'])
        self.assertNotIn('creature_handle', json.dumps(self.provider.requests[0].messages))
        result = await self.finish(task)
        self.assertEqual(result['decision'], {'candidate': 'approach_hand'})
        self.assertEqual(COMMAND.unpack(self.sent[0]), (0x42575041, 2, 3, 4, 100, 3, 7))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.broker.last_execution, {'status': 'queued', 'queued': True})
        self.assertEqual(self.broker.memory.history[-1]['action_status'], 'queued_unconfirmed')

    async def test_expired_response_is_not_refreshed(self):
        task = await self.begin()
        await self.finish(task, 31)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.broker.last_execution['status'], 'expired')
        self.assertEqual(self.offers, 1)

    async def test_native_sleep_then_wake_revokes_old_response(self):
        task = await self.begin()
        self.state['action_probe']['generation'] += 2
        await self.finish(task)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.broker.last_execution['status'], 'conditions_changed')

    async def test_hand_moves_away_and_back_revokes_target(self):
        task = await self.begin()
        self.state['action_probe']['conversation_actions']['hand_target_generation'] += 2
        await self.finish(task)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.broker.last_execution['status'], 'conditions_changed')

    async def test_temple_then_resume_does_not_revive_offer(self):
        task = await self.begin()
        self.now = 1; self.refresh(in_temple=True)
        await self.finish(task)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.broker.last_execution['status'], 'conversation_control_changed')

    async def test_question_during_scene_lock_cannot_create_offer(self):
        self.refresh(cutscene=True)
        task = await self.begin()
        self.assertTrue(self.provider.requests[0].current['conversation_only'])
        await self.finish(task)
        self.assertEqual(self.offers, 0)
        self.assertEqual(self.sent, [])

    async def test_sleep_and_invented_actions_cannot_send(self):
        self.reply['decision'] = {'candidate': 'sleep_here'}
        task = await self.begin(); await self.finish(task)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.broker.last_execution['status'], 'not_confirmed_no_retry')

    async def test_action_with_emoji_has_only_one_transport_attempt(self):
        self.reply['say'] = 'Coming over 😕'
        task = await self.begin(); result = await self.finish(task)
        self.assertEqual(result['say'], 'Coming over')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(COMMAND.unpack(self.sent[0])[-2], 3)
        speech = self.broker.speech.snapshot(self.now)
        self.assertEqual(speech['last_expression']['status'], 'skipped_action_conflict')

    async def test_emoji_only_reply_can_dispatch_without_blank_banner(self):
        self.reply.update(answer='😕', decision={'candidate': None})
        task = await self.begin(); result = await self.finish(task)
        self.assertEqual(result['answer'], '')
        self.assertIsNone(self.broker.speech.snapshot(self.now)['current'])
        self.assertEqual(COMMAND.unpack(self.sent[0])[-2:], (1, 0))
        self.assertEqual(self.broker.last_execution['status'], 'queued')

    async def test_cancelled_question_never_dispatches(self):
        task = await self.begin(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.provider.release.set(); await asyncio.sleep(0)
        self.assertEqual(self.sent, [])

    async def test_timeout_discards_provider_that_ignores_cancellation(self):
        async def stubborn(request):
            self.provider.entered.set()
            try:
                await self.provider.release.wait()
            except asyncio.CancelledError:
                await self.provider.release.wait()
            return self.reply
        self.provider.respond = stubborn
        self.broker.timeout = .02
        task = await self.begin()
        with self.assertRaises(TimeoutError): await task
        self.provider.release.set()
        await asyncio.sleep(.02)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.broker.memory.history), 0)

    async def test_invalid_memory_prevents_action_side_effect(self):
        self.reply['memory'] = {'not': 'text'}
        task = await self.begin()
        with self.assertRaises(RuntimeError): await self.finish(task)
        self.assertEqual(self.sent, [])

    async def test_wrong_creature_and_old_protocol_have_no_offer(self):
        self.state['action_probe']['conversation_actions']['creature_handle'] = 99
        task = await self.begin(); result = await self.finish(task)
        self.assertTrue(self.provider.requests[0].current['conversation_only'])
        self.assertEqual(result['decision'], {'candidate': None})
        self.assertEqual(self.sent, [])

    async def test_unknown_receipt_stays_unknown_without_retry(self):
        def lost(pid, packet):
            self.sent.append(packet)
            raise TimeoutError('synthetic receipt loss')
        self.send = lost
        task = await self.begin(); await self.finish(task)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.broker.last_execution['status'], 'not_confirmed_no_retry')
        self.assertEqual(self.broker.memory.history[-1]['action_status'], 'dispatch_unconfirmed')

    async def test_automatic_requests_can_use_the_action_executor(self):
        request = PlanningRequest(self.gate.issue(0), self.gate.observation.candidates, None)
        task = asyncio.create_task(self.broker.choose(request))
        await self.provider.entered.wait()
        self.provider.release.set(); await task
        self.assertEqual(self.offers, 1)
        self.assertEqual(len(self.sent), 1)

    async def test_old_bridge_keeps_text_only_contract(self):
        self.state['action_probe'].pop('conversation_actions')
        task = await self.begin(); await self.finish(task)
        self.assertTrue(self.provider.requests[0].current['conversation_only'])
        self.assertEqual(self.sent, [])
