import queue
import threading
import unittest
from unittest.mock import Mock, patch

from bwv.errors import ChatNotSubmitted
from bwv.overlay import OverlayModel, ChatInputHistory
from bwv.overlay_windows import OverlayWindow
from test_overlay import payload


class Entry:
    def __init__(self,text):self.text=text;self.state='normal'
    def get(self):return self.text
    def configure(self,*,state):self.state=state
    def delete(self,*args):self.text=''


class ImmediateThread:
    def __init__(self,*,target,**kwargs):self.target=target
    def start(self):self.target()


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.clock=patch('bwv.overlay_windows.time.monotonic',side_effect=lambda:self.now)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.threads=patch('bwv.overlay_windows.threading.Thread',ImmediateThread)
        self.threads.start();self.addCleanup(self.threads.stop)
        w=self.window=object.__new__(OverlayWindow)
        w.model=OverlayModel();w.model.update(payload(),100)
        w.entry=Entry('Come here');w.client=Mock();w.client.ask.return_value={}
        w.client.state.return_value=payload()
        w.pending=False;w.submission=None;w.notice='';w.input_history=ChatInputHistory()
        w.events=queue.SimpleQueue();w.state_lock=threading.Lock();w.latest_state=None
        w.target_identity=w.model.identity;w.render=Mock();w.root=Mock()
        w.closed=False;w.deadline=float('inf');w.game=1;w.hwnd=2;w.expanded=True
        w.win=Mock();w.win.pid.return_value=123;w.win.u.GetForegroundWindow.return_value=1

    def refresh(self,**changes):
        self.window.model.update(payload(self.now,**changes),self.now)

    def test_enter_during_read_gap_waits_then_sends_once(self):
        w=self.window;w.model.read_gap();w.submit();w.submit()
        self.assertTrue(w.pending);w.client.ask.assert_not_called()
        self.assertIn('Waiting to send',w.submission.notice(self.now))
        self.now+=1;self.refresh();w.tick();w.tick();w.tick()
        w.client.ask.assert_called_once_with('Come here',('123:1','monkey'))
        self.assertFalse(w.pending);self.assertEqual(w.entry.get(),'')
        self.assertEqual(w.input_history.items,['Come here'])

    def test_persistent_gap_unlocks_with_original_text_and_no_later_send(self):
        w=self.window;w.model.read_gap();w.submit()
        self.now=109.9;w.tick()
        self.assertTrue(w.pending);w.client.ask.assert_not_called()
        self.now=110;w.tick()
        self.assertFalse(w.pending);self.assertEqual(w.entry.get(),'Come here')
        self.assertEqual(w.entry.state,'normal');self.assertIn('press Enter',w.notice)
        self.now+=1;self.refresh();w.tick();w.client.ask.assert_not_called()

    def test_pre_admission_race_retries_without_extending_deadline(self):
        w=self.window
        w.client.ask.side_effect=[ChatNotSubmitted(),{}]
        w.submit();deadline=w.submission.deadline;w.tick()
        self.assertTrue(w.pending);self.assertFalse(w.submission.sending)
        self.assertEqual(w.submission.deadline,deadline)
        self.now+=.6;self.refresh();w.tick();w.tick()
        self.assertEqual(w.client.ask.call_count,2);self.assertFalse(w.pending)
        self.assertEqual(w.entry.get(),'')

    def test_transport_failure_is_never_automatically_replayed(self):
        for error in (OSError('lost receipt'),EOFError(),ValueError('provider failed')):
            with self.subTest(error=type(error).__name__):
                w=self.window;w.entry.text='Come here';w.client.ask.reset_mock()
                w.client.ask.side_effect=error;w.submit();w.tick()
                self.now+=1;self.refresh();w.tick()
                w.client.ask.assert_called_once()
                self.assertFalse(w.pending);self.assertEqual(w.entry.get(),'Come here')

    def test_accepted_reply_during_read_gap_clears_text_without_resending(self):
        w=self.window;w.submit();w.model.read_gap();w.tick()
        self.assertEqual(w.entry.get(),'');self.assertFalse(w.pending)
        self.now+=1;self.refresh();w.tick();w.client.ask.assert_called_once()

    def test_pause_resume_waits_for_same_recipient(self):
        w=self.window;w.model.read_gap();w.submit();self.refresh(paused=True)
        w.tick();w.client.ask.assert_not_called()
        self.now+=2;self.refresh(paused=False);w.tick();w.tick()
        w.client.ask.assert_called_once();self.assertFalse(w.pending)

    def test_changed_recipient_keeps_text_but_cancels_automatic_delivery(self):
        w=self.window;w.model.read_gap();w.submit()
        self.now+=1;self.refresh(creature_id='other');w.tick();w.tick()
        self.assertFalse(w.pending);self.assertEqual(w.entry.get(),'Come here')
        w.client.ask.assert_not_called()

    def test_backend_identity_rejection_does_not_retry(self):
        w=self.window;w.client.ask.side_effect=ChatNotSubmitted(retryable=False)
        w.submit();w.tick();self.now+=1;self.refresh();w.tick()
        self.assertEqual(w.entry.get(),'Come here');self.assertFalse(w.pending)
        w.client.ask.assert_called_once()
