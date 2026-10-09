from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

from bwv.action_probe import NativeActionAdapter, COMMAND, run, submit
from bwv.dispatch import DecisionDispatcher, DummyActionProvider, ActionNotSent


def frame():
    return {'pid': 123, 'session': 3, 'source_age_ms': 20, 'captured_uptime_ms': 110,
            'action_probe': {'experimental_manual_only': True, 'supported': True,
                'transport_ready': True, 'phase': 'ready', 'candidate': 'look_confused',
                'engine_calls': 0, 'epoch': 3, 'generation': 4, 'offered_at_uptime_ms': 100}}


class DispatchTests(unittest.TestCase):
    def test_only_explicit_native_zero_is_a_negative_receipt(self):
        packet=COMMAND.pack(0x42575041,1,3,4,100,1,0)
        for receipt in (0,1,2):
            with self.subTest(receipt=receipt):
                kernel=MagicMock();kernel.CreateFileW.return_value=123
                def written(handle,data,size,count,overlapped):
                    count._obj.value=size;return True
                def available(handle,buffer,size,read,total,left):
                    total._obj.value=4;return True
                def read(handle,result,size,count,overlapped):
                    result._obj.value=receipt;count._obj.value=4;return True
                kernel.WriteFile.side_effect=written
                kernel.PeekNamedPipe.side_effect=available
                kernel.ReadFile.side_effect=read
                with patch('bwv.action_probe.os.name','nt'), patch(
                        'bwv.action_probe.ctypes.WinDLL',return_value=kernel,create=True):
                    if receipt==2:
                        with self.assertRaisesRegex(OSError,'Unknown action receipt'):submit(123,packet)
                    else:self.assertIs(submit(123,packet),receipt==1)
                kernel.CloseHandle.assert_called_once_with(123)

    def test_native_negative_acknowledgement_is_explicitly_not_queued(self):
        decision=self.choose()
        self.adapter.send=lambda pid,packet:False
        self.assertEqual(self.dispatcher.dispatch(decision),
            {'status':'rejected','queued':False,'stage':'before_native_queue'})
        with self.assertRaises(ValueError):self.dispatcher.dispatch(decision)

    def test_failed_read_is_known_not_sent_and_does_not_call_transport(self):
        decision=self.choose()
        def unavailable(pid):raise TimeoutError('Synthetic read timeout')
        self.adapter.read=unavailable
        result=self.dispatcher.dispatch(decision)
        self.assertEqual(result['status'],'validation_unavailable')
        self.assertEqual(result['stage'],'before_transport')
        self.assertFalse(self.sent)
        with self.assertRaises(ValueError):self.dispatcher.dispatch(decision)

    def test_failure_before_write_is_distinct_from_unknown_receipt(self):
        decision=self.choose()
        def unavailable(pid,packet):raise ActionNotSent('action_pipe_unavailable',231)
        self.adapter.send=unavailable
        result=self.dispatcher.dispatch(decision)
        self.assertEqual(result['status'],'transport_unavailable')
        self.assertEqual(result['stage'],'before_write')
        self.assertEqual(result['winerror'],231)
        self.assertFalse(result['queued'])
    def test_repeat_action_needs_new_offer_and_dispatcher_after_native_rearm(self):
        decision=self.choose()
        old_offer=DecisionDispatcher(self.adapter,clock=lambda:self.now)
        old_offer.offer()
        self.assertTrue(self.dispatcher.dispatch(decision)['queued'])
        probe=self.state['action_probe']
        probe.update(engine_calls=2,phase='finished',action_lifecycle={
            'version':1,'repeated_actions_enabled':True,'in_flight':False,
            'cooldown_remaining_ms':10000})
        with self.assertRaises(ValueError):
            DecisionDispatcher(self.adapter,clock=lambda:self.now).offer()
        probe.update(phase='ready',generation=probe['generation']+2)
        probe['action_lifecycle']['cooldown_remaining_ms']=0
        self.assertEqual(old_offer.dispatch(decision)['status'],'conditions_changed')
        with self.assertRaises(ValueError):self.dispatcher.dispatch(decision)
        second=DecisionDispatcher(self.adapter,clock=lambda:self.now)
        second.offer()
        self.assertTrue(second.dispatch(decision)['queued'])
        self.assertEqual(len(self.sent),2)
        self.assertNotEqual(self.sent[0][1],self.sent[1][1])

    def setUp(self):
        self.state = frame()
        self.now = 10.
        self.sent = []
        self.adapter = NativeActionAdapter(123, read=lambda pid: deepcopy(self.state),
            send=lambda pid, packet: self.sent.append((pid, packet)) or True)
        self.dispatcher = DecisionDispatcher(self.adapter, clock=lambda: self.now)

    def choose(self):
        return DummyActionProvider().choose(self.dispatcher.offer())

    def test_dummy_response_reaches_transport_with_original_offer(self):
        decision = self.choose()
        # A later frame must not refresh the original wire capability.
        self.state['action_probe']['offered_at_uptime_ms'] = 150
        self.state['captured_uptime_ms'] = 150
        result = self.dispatcher.dispatch(decision)
        self.assertEqual(result, {'status': 'queued', 'queued': True})
        self.assertEqual(COMMAND.unpack(self.sent[0][1]), (0x42575041, 1, 3, 4, 100, 1, 0))
        with self.assertRaises(ValueError): self.dispatcher.dispatch(decision)
        self.assertEqual(len(self.sent), 1)

    def test_no_action_and_unoffered_actions_never_send(self):
        for decision in ({'candidate': None}, {'candidate': 'pick_up'}, {'candidate': 'sleep_here'},
                         {'candidate': []}, {'candidate': 'look_confused', 'address': 123}):
            with self.subTest(decision=decision):
                self.setUp(); self.choose()
                if decision == {'candidate': None}:
                    self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'no_action')
                else:
                    with self.assertRaises(ValueError): self.dispatcher.dispatch(decision)
                self.assertEqual(self.sent, [])

    def test_late_reply_is_not_given_a_new_offer(self):
        decision = self.choose()
        self.now += .98  # The frame was already 30ms old at offer time.
        self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'expired')
        self.assertEqual(self.sent, [])
        with self.assertRaises(ValueError): self.dispatcher.offer()

    def test_lock_then_unlock_and_creature_change_revoke_generation(self):
        decision = self.choose()
        self.state['action_probe']['generation'] += 2
        self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'conditions_changed')
        self.assertEqual(self.sent, [])

    def test_engine_blocks_and_identity_changes_never_send(self):
        changes = [('phase', 'unavailable'), ('engine_calls', 2), ('supported', False),
                   ('transport_ready', False), ('candidate', None), ('epoch', 4)]
        for key, value in changes:
            with self.subTest(key=key):
                self.setUp(); decision = self.choose()
                self.state['action_probe'][key] = value
                self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'conditions_changed')
                self.assertEqual(self.sent, [])
        for key, value in [('pid', 124), ('session', 4), ('source_age_ms', 2000)]:
            with self.subTest(key=key):
                self.setUp(); decision = self.choose(); self.state[key] = value
                self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'conditions_changed')
                self.assertEqual(self.sent, [])

    def test_slow_recheck_expiry_and_unknown_receipt_cannot_retry(self):
        decision = self.choose()
        def slow_read(pid):
            self.now += 2
            return deepcopy(self.state)
        self.adapter.read = slow_read
        self.assertEqual(self.dispatcher.dispatch(decision)['status'], 'expired')
        self.assertEqual(self.sent, [])
        self.setUp(); decision = self.choose()
        def lost_receipt(pid, packet):
            self.sent.append((pid, packet))
            raise TimeoutError('Receipt unknown')
        self.adapter.send = lost_receipt
        with self.assertRaises(TimeoutError): self.dispatcher.dispatch(decision)
        with self.assertRaises(ValueError): self.dispatcher.dispatch(decision)
        self.assertEqual(len(self.sent), 1)

    def test_invalid_or_expired_source_cannot_offer(self):
        for key, value in [('pid', 124), ('captured_uptime_ms', None), ('captured_uptime_ms', 99),
                           ('captured_uptime_ms', 1200)]:
            with self.subTest(key=key):
                self.setUp(); self.state[key] = value
                with self.assertRaises(ValueError): self.dispatcher.offer()
                self.assertEqual(self.sent, [])

    def test_manual_runner_uses_dispatcher_and_distinguishes_queue_from_execution(self):
        finished = frame()
        finished['action_probe'].update(phase='finished', engine_calls=2,
                                        current_plan=92, plan_observed=True)
        # Initial preview, capability capture, dispatch recheck, engine observation.
        with patch('bwv.action_probe.snapshot', side_effect=[frame(), frame(), frame(), finished]), \
             patch('bwv.action_probe.submit', return_value=True) as send, \
             patch('builtins.print'):
            report = run(123, execute=True)
        self.assertEqual(report['dispatch']['status'], 'queued')
        self.assertTrue(report['submitted'])
        self.assertTrue(report['visual_confirmation_required'])
        self.assertEqual(report['provider_calls'], 0)
        send.assert_called_once()

    def test_manual_runner_discards_reply_when_scene_changes_before_send(self):
        blocked = frame()
        blocked['action_probe'].update(phase='unavailable', reason='temple')
        with patch('bwv.action_probe.snapshot', side_effect=[frame(), frame(), blocked]), \
             patch('bwv.action_probe.submit') as send, patch('builtins.print'):
            report = run(123, execute=True)
        self.assertFalse(report['submitted'])
        self.assertEqual(report['dispatch']['status'], 'conditions_changed')
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
