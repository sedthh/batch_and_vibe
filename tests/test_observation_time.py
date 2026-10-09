import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import unittest

from bwv.observation_time import timestamp, turn_times
from bwv.conversation import ConversationBroker, ConversationMemory
from bwv.control import ControlGate
from bwv.planner import PlanningRequest
from test_control import playable
from test_conversation import Provider


class TimestampTests(unittest.IsolatedAsyncioTestCase):
    def test_compact_unambiguous_utc_and_separate_sample_and_receipt_times(self):
        now=datetime(2030,1,2,3,4,5,900000,tzinfo=timezone.utc)
        result=turn_times(100,101,wall_now=now,received_at='2030-01-02T03:04:01Z')
        self.assertEqual(result,{'sent_at':'2030-01-02T03:04:05Z',
            'observed_at':'2030-01-02T03:04:04Z','message_received_at':'2030-01-02T03:04:01Z'})
        self.assertNotIn('observed_at',turn_times(100,200,wall_now=now))

    async def test_queued_question_keeps_its_receipt_time_and_new_observation_time(self):
        gate=ControlGate(resume_delay=0);gate.update(playable(100),100)
        release=asyncio.Event();provider=Provider(release)
        broker=ConversationBroker(provider,gate,clock=lambda:100,memory=ConversationMemory())
        wall=[datetime(2030,1,1,tzinfo=timezone.utc)]
        try:
            with patch('bwv.conversation.utc_now',side_effect=lambda:wall[0]),patch('bwv.observation_time.utc_now',side_effect=lambda:wall[0]):
                first=asyncio.create_task(broker.ask('First question'))
                await provider.entered.wait()
                wall[0]+=timedelta(seconds=2)
                second=asyncio.create_task(broker.ask('Follow-up question'))
                await asyncio.sleep(0)
                wall[0]+=timedelta(seconds=10)
                release.set();await first;await second
            times=provider.requests[1].current['time']
            self.assertEqual(times['message_received_at'],'2030-01-01T00:00:02Z')
            self.assertEqual(times['sent_at'],'2030-01-01T00:00:12Z')
            self.assertEqual(broker.memory.snapshot()['recent_pairs'][-1]['input']['time'],times)
        finally:await broker.close()

    async def test_tick_timestamp_does_not_turn_empty_context_into_a_model_call(self):
        gate=ControlGate(resume_delay=0);gate.update(playable(),0)
        provider=Provider()
        broker=ConversationBroker(provider,gate,clock=lambda:0,memory=ConversationMemory(),
            prompt_builder=lambda *a,**k:('test',{'snapshot':{}}))
        try:
            await broker.choose(PlanningRequest(gate.issue(0),gate.observation.candidates,None))
            self.assertEqual(provider.requests,[])
        finally:await broker.close()

    async def test_ticks_include_receipt_time_but_no_fictional_god_message(self):
        gate=ControlGate(resume_delay=0);gate.update(playable(),0)
        provider=Provider();broker=ConversationBroker(provider,gate,clock=lambda:0,memory=ConversationMemory())
        try:
            await broker.choose(PlanningRequest(gate.issue(0),gate.observation.candidates,None))
            times=provider.requests[0].current['time']
            self.assertIn('tick_received_at',times)
            self.assertNotIn('message_received_at',times)
            self.assertNotIn('time',provider.requests[0].current['snapshot'])
        finally:await broker.close()
