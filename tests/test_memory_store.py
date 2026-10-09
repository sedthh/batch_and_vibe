import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch

from bwv.conversation import ConversationMemory, ConversationBroker
from bwv.control import ControlGate
from bwv.planner import PlanningRequest
from test_control import playable


class MemoryStoreTests(TestCase):
    def test_legacy_target_lists_are_compacted_before_history_is_trimmed_on_reload(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json'
            memory=ConversationMemory(path=path)
            memory.record({'snapshot':{},'god_question':'Water a tree'},'Trying.',{'candidate':None})
            memory.record({'snapshot':{},'god_question':'Then the field'},'Trying.',{'candidate':'cast_water_field'})
            saved=json.loads(path.read_text(encoding='utf-8'))
            saved['recent_pairs'][-1]['input']['snapshot']['action_targets']=['expired-id'*1000]
            saved['recent_pairs'][-1]['decision']['target']='expired-target'
            path.write_text(json.dumps(saved),encoding='utf-8')
            loaded=ConversationMemory(path=path,history_chars=2000)
            self.assertIsNone(loaded.store.error)
            self.assertEqual(len(loaded.history),2)
            self.assertNotIn('expired-',json.dumps(loaded.messages('System',{'snapshot':{}})))

    def test_delayed_action_terminal_statuses_reload_without_losing_notes(self):
        for status in ('already_awake','cancelled_asleep_again','cancelled_miracle_target_changed','wake_not_confirmed_no_retry',
                       'cancelled_retry_limit','cancelled_put_down_replaced','cancelled_put_down_not_empty',
                       'cancelled_hand_preparation_limit','cancelled_put_down_failed'):
            with self.subTest(status=status),TemporaryDirectory() as directory:
                path=Path(directory)/'memory.json'
                memory=ConversationMemory(path=path)
                memory.record({},'',{'candidate':'wake_up'},notes='Synthetic lesson')
                memory.record_dispatch({'intent_id':'synthetic-intent','status':'waiting_for_native'})
                memory.update_dispatch({'intent_id':'synthetic-intent','status':status})
                loaded=ConversationMemory(path=path)
                self.assertIsNone(loaded.store.error)
                self.assertEqual(loaded.notes,'Synthetic lesson')
                self.assertEqual(loaded.history[-1]['action_status'],status)

    def test_dispatch_receipts_persist_as_unconfirmed_not_completed(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json'
            for status, expected in [('queued','queued_unconfirmed'),
                                     ('not_confirmed_no_retry','dispatch_unconfirmed')]:
                memory=ConversationMemory(path=path)
                memory.record({},'Trying to move',{'candidate':'approach_hand'})
                memory.record_dispatch({'status':status})
                loaded=ConversationMemory(path=path)
                self.assertEqual(loaded.history[-1]['action_status'],expected)
                self.assertIsNone(loaded.store.error)

    def test_storage_location_is_not_exposed_in_debug_disk_or_model_context(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'private-user-folder'/'creature-memory.json'
            memory=ConversationMemory(path=path)
            memory.record({'snapshot':{}},'Hello',{'candidate':None},'A lesson')
            debug=memory.snapshot()
            stored=json.loads(path.read_text(encoding='utf-8'))
            messages=memory.messages('System',{'snapshot':{}})
            self.assertNotIn('storage_path',debug)
            self.assertEqual(set(stored),{'version','notes','recent_pairs','entries'})
            self.assertEqual(debug['persistence'],'local_file')
            for data in (debug,stored,messages):
                self.assertNotIn('private-user-folder',json.dumps(data))
            with patch('bwv.memory_store.os.replace',side_effect=OSError(str(path))):
                memory.record({},'',{'candidate':None})
            self.assertEqual(memory.snapshot()['storage_error'],'save_failed_previous_file_preserved')
            self.assertNotIn('private-user-folder',json.dumps(memory.snapshot()))

    def test_restart_restores_bounded_notes_history_and_speech_text(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json'
            memory=ConversationMemory(path=path)
            for i in range(4):
                memory.record({'snapshot':{'trees_nearby':i}},'',{'candidate':None},'Remember God.',say='Hello')
            loaded=ConversationMemory(pairs=2,path=path)
            self.assertEqual(loaded.notes,'Remember God.')
            self.assertEqual(len(loaded.history),2)
            self.assertEqual(loaded.history[-1]['say'],'Hello')
            self.assertEqual(loaded.snapshot()['persistence'],'local_file')
            loaded.clear()
            self.assertEqual(ConversationMemory(path=path).notes,'')

    def test_corrupt_or_unsupported_file_is_visible_and_never_overwritten(self):
        for data in ('bad json','{"version":2}','{"version":1,"notes":"ok","recent_pairs":[{}]}'):
            with self.subTest(data=data),TemporaryDirectory() as directory:
                path=Path(directory)/'memory.json';path.write_text(data)
                memory=ConversationMemory(path=path)
                self.assertEqual(memory.snapshot()['storage_error'],'load_failed_original_preserved')
                memory.record({},'',{'candidate':None},'New')
                self.assertEqual(path.read_text(),data)

    def test_failed_atomic_replace_preserves_previous_file_and_retries(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json';memory=ConversationMemory(path=path)
            memory.record({},'',{'candidate':None},'Old')
            before=path.read_bytes()
            with patch('bwv.memory_store.os.replace',side_effect=OSError('disk failed')):
                memory.record({},'',{'candidate':None},'New')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(memory.snapshot()['storage_error'],'save_failed_previous_file_preserved')
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])
            memory.record({},'',{'candidate':None})
            self.assertEqual(ConversationMemory(path=path).notes,'New')
            self.assertIsNone(memory.snapshot()['storage_error'])


class ResumeMemoryTests(IsolatedAsyncioTestCase):
    async def test_restarted_broker_preserves_memory_and_reconciles_first_nonempty_tick(self):
        class Provider:
            def __init__(self):self.requests=[]
            async def respond(self,request):
                self.requests.append(request)
                return {'answer':'Hello','decision':{'candidate':None}}
        with TemporaryDirectory() as directory:
            path=Path(directory)/'memory.json'
            ConversationMemory(path=path).record({'snapshot':{'trees_nearby':100}},'',{'candidate':None},'Old lesson')
            gate=ControlGate(resume_delay=0);now=[0]
            gate.update(playable(candidates=()),0)
            provider=Provider();broker=ConversationBroker(provider,gate,clock=lambda:now[0],memory=ConversationMemory(path=path))
            def request():return PlanningRequest(gate.issue(now[0]),gate.observation.candidates,None)
            try:
                self.assertIsNone(await broker.choose(request()))
                self.assertEqual(provider.requests,[])
                # A question during a lock may answer but cannot consume recovery.
                now[0]=.1;gate.update(playable(.1,in_temple=True),.1)
                await broker.ask('Hello')
                self.assertNotIn('memory_reconciliation',provider.requests[-1].current)
                now[0]=.2;gate.update(playable(.2),.2)
                await broker.choose(request())
                self.assertIn('memory_reconciliation',provider.requests[-1].current)
                self.assertIn('Old lesson',provider.requests[-1].messages[0]['content'])
                self.assertNotIn('trees_nearby',provider.requests[-1].current['snapshot'])
                await broker.choose(request())
                self.assertNotIn('memory_reconciliation',provider.requests[-1].current)
                now[0]=.3;gate.update(playable(.3,session='different-save'),.3)
                await broker.choose(request())
                self.assertIn('memory_reconciliation',provider.requests[-1].current)
                self.assertEqual(broker.memory.notes,'Old lesson')
            finally:await broker.close()
