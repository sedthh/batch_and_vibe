"""Push-to-talk UI tests using fake keys, devices and chat; no game required."""
from dataclasses import replace
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from bwv.voice import VoiceSettings, Recording
from bwv.voice_overlay import VoiceOverlay, VoiceInputMeter
from bwv.overlay_windows import OverlayWindow
from bwv.overlay import ChatInputHistory, OverlayModel, ReplyReveal
from test_overlay import payload
from test_overlay_submission import ImmediateThread


class Entry:
    def __init__(self): self.text = ''; self.state = 'normal'
    def get(self): return self.text
    def configure(self, **kwargs): self.state = kwargs.get('state', self.state)
    def delete(self, *args):
        if self.state == 'normal': self.text = ''
    def insert(self, index, text):
        if self.state == 'normal': self.text = text
    def focus_set(self): pass
    def icursor(self, index): pass
    def xview_moveto(self, fraction): pass


class VoiceOverlayTests(unittest.TestCase):
    def test_unconfigured_or_disabled_voice_does_not_claim_f11_or_create_worker(self):
        from bwv.llm import LLMConfig
        widgets = {name: Mock() for name in ('Tk', 'Frame', 'Label', 'Text', 'Scrollbar', 'Entry', 'Button')}
        for settings in (VoiceSettings(enabled=True, model=''),
                         VoiceSettings(enabled=True, model='  '), VoiceSettings(enabled=False)):
            with self.subTest(settings=settings), patch.multiple('tkinter', **widgets), \
                    patch('bwv.overlay_windows.Windows'), patch('bwv.overlay_windows.threading.Thread'), \
                    patch('bwv.llm.LLMConfig.load', return_value=LLMConfig('fixture', voice=settings)), \
                    patch('bwv.voice_overlay.VoiceInput') as worker:
                window = OverlayWindow(Mock(), settings_path='unused')
                self.assertIsNone(window.voice)
                self.assertIsNone(window.voice_meter)
                worker.assert_not_called()
                bindings = [call.args[0] for call in window.entry.bind.call_args_list]
                self.assertIn('<Return>', bindings)
                self.assertNotIn('<F11>', bindings)
                self.assertNotIn('<Control-F11>', bindings)

    def setUp(self):
        w = OverlayWindow.__new__(OverlayWindow)
        w.model = Mock(identity=('session', 'creature'))
        w.model.can_show.return_value = True; w.model.can_send.return_value = True
        w.win = Mock(); w.win.u.GetForegroundWindow.return_value = 1
        self.keys = set()
        w.win.u.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in self.keys else 0
        w.game = 1; w.hwnd = 2; w.pending = False; w.notice = ''; w.expanded = False
        w.entry = Entry(); w.root = Mock()
        w.render = Mock(); w.advance_submission = Mock(); w.input_history = Mock()
        w.submission = None
        service = Mock(busy=False)
        def start(identity):
            service.job = Recording(identity, threading.Event(), threading.Event())
            return service.job
        service.start.side_effect = start
        service.cancel.side_effect = lambda: service.job.cancelled.set()
        service.release.side_effect = lambda: service.job.released.set()
        w.voice = VoiceOverlay(w, VoiceSettings(enabled=True), 'unused', service=service)
        self.w, self.v, self.service = w, w.voice, service

    def press(self): self.keys.add(0x7A); self.v.tick()
    def release(self): self.keys.clear(); self.v.tick()
    def event(self, kind, value=None): self.v.handle(kind, (self.v.job, value))

    def test_one_press_one_job_release_and_autosend_through_normal_chat(self):
        self.press()
        for _ in range(30): self.v.tick()
        self.service.start.assert_called_once_with(('session', 'creature'))
        self.w.submit()  # No text submission during recording.
        self.assertIsNone(self.w.submission)
        self.release(); self.assertTrue(self.v.job.released.is_set())
        self.event('voice_text', 'Come here')
        self.event('voice_done')
        self.assertTrue(self.w.pending)
        self.assertEqual(self.w.submission.text, 'Come here')
        self.assertEqual(self.w.submission.identity, ('session', 'creature'))
        self.w.advance_submission.assert_called_once()
        self.w.input_history.submitted.assert_called_once_with('Come here')
        self.assertEqual(self.w.entry.get(), 'Come here')
        self.assertTrue(self.w.expanded)
        self.w.win.u.SetForegroundWindow.assert_not_called()

    def test_review_keeps_transcript_editable_and_sends_corrected_text(self):
        self.v.config = replace(self.v.config, auto_send=False)
        self.press(); self.release(); self.event('voice_text', 'Cast food')
        self.assertFalse(self.w.pending)
        self.assertEqual(self.w.entry.get(), 'Cast food')
        self.assertTrue(self.w.expanded)
        self.assertEqual(self.w.entry.state, 'normal')
        self.w.win.u.SetForegroundWindow.assert_called_once_with(self.w.hwnd)
        self.w.entry.text = 'Cast wood'
        self.w.submit()
        self.assertEqual(self.w.submission.text, 'Cast wood')
        self.assertEqual(self.w.entry.get(), 'Cast wood')

    def test_hungarian_transcript_reaches_chat_and_display_unchanged(self):
        words = 'Gyere ide, és segíts a falusiaknak! Ők kérnek fát.'
        self.press(); self.release(); self.event('voice_text', words)
        self.assertEqual(self.w.submission.text, words)
        self.assertEqual(self.w.entry.get(), words)

    def test_no_overwrite_of_typed_draft_or_submission(self):
        for text, pending in (('unfinished typing', False), ('sent text', True)):
            self.keys.clear(); self.v.tick()
            self.w.entry.text = text; self.w.pending = pending
            self.press()
            self.assertEqual(self.w.entry.text, text)
            self.service.start.assert_not_called()

    def test_inactive_hotkey_does_not_arm_until_released(self):
        self.w.win.u.GetForegroundWindow.return_value = 99
        self.press(); self.service.start.assert_not_called()
        self.w.win.u.GetForegroundWindow.return_value = 1
        self.v.tick(); self.service.start.assert_not_called()
        self.release(); self.press(); self.service.start.assert_called_once()

    def test_cancellation_and_late_result_never_send_to_a_new_session(self):
        self.press()
        self.w.model.identity = ('new-session', 'other-creature')
        self.v.tick()
        self.assertFalse(self.v.active)
        self.assertTrue(self.v.job.cancelled.is_set())
        self.event('voice_text', 'Old request')
        self.assertIsNone(self.w.submission)
        self.assertEqual(self.w.entry.get(), '')

    def test_menu_focus_loss_and_escape_cancel_even_before_next_tick(self):
        for cause in ('menu', 'focus', 'escape'):
            self.setUp(); self.press()
            if cause == 'menu': self.w.model.can_show.return_value = False
            elif cause == 'focus': self.w.win.u.GetForegroundWindow.return_value = 99
            else: self.keys.add(0x1B)
            if cause == 'escape': self.v.tick()
            self.event('voice_text', 'Should not send')
            self.assertTrue(self.v.job.cancelled.is_set())
            self.assertIsNone(self.w.submission)

    def test_finishing_at_time_limit_does_not_restart_while_held(self):
        self.press(); self.event('voice_error', 'Recording interrupted'); self.event('voice_done')
        for _ in range(20): self.v.tick()
        self.service.start.assert_called_once()
        self.release(); self.press()
        self.assertEqual(self.service.start.call_count, 2)

    def test_expired_job_cannot_update_the_next_recording(self):
        self.press(); old = self.v.job; self.v.cancel(); self.release(); self.press()
        self.v.handle('voice_text', (old, 'Old words'))
        self.v.handle('voice_done', (old, None))
        self.assertTrue(self.v.active)
        self.assertEqual(self.w.entry.get(), '')

    def test_duplicate_transcript_events_do_not_append_or_submit_again(self):
        self.press(); self.release(); self.event('voice_text', 'Wave')
        for _ in range(100):
            self.event('voice_text', 'Wave')
            self.assertEqual(self.w.entry.get(), 'Wave')
            self.assertEqual(self.w.submission.text, 'Wave')
        self.w.advance_submission.assert_called_once()
        self.w.input_history.submitted.assert_called_once_with('Wave')

    def use_real_chat_flow(self):
        """Exercise shared submission, rendering and reply events with fake widgets."""
        clock = patch('bwv.overlay_windows.time.monotonic', return_value=100)
        clock.start(); self.addCleanup(clock.stop)
        threads = patch('bwv.overlay_windows.threading.Thread', ImmediateThread)
        threads.start(); self.addCleanup(threads.stop)
        w = self.w
        w.model = OverlayModel(); w.model.update(payload(), 100)
        w.target_identity = w.model.identity; w.input_history = ChatInputHistory()
        w.events = queue.SimpleQueue(); w.state_lock = threading.Lock(); w.latest_state = None
        w.closed = False; w.deadline = float('inf'); w.restore_input_focus = False
        w.client = Mock(); w.client.state.return_value = payload()
        w.win.pid.return_value = 123; w.win.rect.return_value = (0, 0, 800, 600)
        w.heading = Mock(); w.indicator = Mock(); w.message = Mock(); w.message.yview.return_value = (0, 1)
        w.message_box = Mock(); w.message_scroll = Mock(); w.message_text = ''
        w.form = Mock(); w.form.winfo_reqheight.return_value = 28
        w.voice_meter = Mock()
        w.reply_reveal = ReplyReveal(); w._message_key = None; w.hotkey_failed = False
        w.geometry = None; w.want_hotkey = threading.Event(); w.send = Mock(); w.diagnostics = Mock()
        w.render = OverlayWindow.render.__get__(w)
        w.advance_submission = OverlayWindow.advance_submission.__get__(w)
        return w

    def test_voice_status_and_meter_stay_in_input_without_replacing_reply(self):
        w = self.use_real_chat_flow()
        w.model.is_dummy = False
        w.model.creature_name = 'Fixture creature'
        w.model.accept_message({'text':'I am near the trees.', 'created_at':100})
        w.render(); w.message.reset_mock()
        w.heading.configure.assert_called_with(text='Chat Ctrl+Enter   -   Voice F11')
        self.press()
        self.assertTrue(w.expanded)
        self.assertEqual((w.entry.get(), w.entry.state), ('', 'disabled'))
        for status, level in (('Listening', .25), ('Listening', .8), ('Transcribing', 0)):
            self.event('voice_status', status); self.v.job.level = level; w.render()
            w.voice_meter.render.assert_called_with(status, level)
            self.assertEqual(w.message_text, 'I am near the trees.')
            self.assertEqual(w.entry.get(), '')
        w.message.configure.assert_not_called(); w.message.insert.assert_not_called()
        w.win.u.SetForegroundWindow.assert_not_called()
        w.client.ask.assert_not_called()
        self.v.cancel(); w.render()
        w.voice_meter.render.assert_called_with('', 0)
        self.assertEqual((w.entry.get(), w.entry.state), ('', 'normal'))
        w.voice = None; w.render()
        w.heading.configure.assert_called_with(text='Chat Ctrl+Enter')

    def test_release_clears_meter_even_before_transcribing_status_arrives(self):
        w = self.use_real_chat_flow()
        self.press(); self.event('voice_status', 'Listening'); self.v.job.level = .8
        w.render(); w.voice_meter.render.assert_called_with('Listening', .8)
        self.release(); w.render()
        w.voice_meter.render.assert_called_with('Listening', 0)

    def test_transcript_stays_disabled_until_first_reply_chunk_then_empty_and_editable(self):
        w = self.use_real_chat_flow()
        reply = 'I am coming over to see you. I will leave the tree beside your hand. ' * 5
        w.client.ask.return_value = {'last_message': {'text': reply, 'created_at': 100}}
        self.press(); self.release(); self.event('voice_text', 'Gyere ide!')
        self.event('voice_done')
        self.assertTrue(w.pending); self.assertTrue(w.expanded)
        self.assertEqual((w.entry.get(), w.entry.state), ('Gyere ide!', 'disabled'))
        w.voice_meter.render.assert_called_with('', 0)
        w.send.configure.assert_any_call(state='disabled')
        w.win.u.SetForegroundWindow.assert_not_called()
        w.tick()
        self.assertFalse(w.pending)
        self.assertEqual((w.entry.get(), w.entry.state), ('', 'normal'))
        self.assertTrue(w.expanded)
        self.assertTrue(reply.startswith(w.message_text))
        self.assertGreater(len(w.message_text), 0)
        self.assertLess(len(w.message_text), len(reply))
        self.assertNotIn('You:', w.message_text)
        w.entry.insert(0, 'Next question'); w.tick()
        self.assertEqual(w.entry.get(), 'Next question')
        w.client.ask.assert_called_once_with('Gyere ide!', w.model.identity)

    def test_failed_voice_send_unlocks_original_transcript_for_editing(self):
        w = self.use_real_chat_flow()
        w.client.ask.side_effect = OSError('fixture connection failed')
        self.press(); self.release(); self.event('voice_text', 'Gyere ide!')
        self.assertEqual(w.entry.state, 'disabled')
        w.tick(); w.tick()
        self.assertFalse(w.pending)
        self.assertEqual((w.entry.get(), w.entry.state), ('Gyere ide!', 'normal'))
        self.assertTrue(w.expanded)
        w.client.ask.assert_called_once()


class VoiceMeterTests(unittest.TestCase):
    def test_meter_reuses_items_and_redraws_only_for_changed_pixels_status_or_size(self):
        canvas = Mock(); canvas.winfo_width.return_value = 400; canvas.winfo_height.return_value = 24
        canvas.create_rectangle.return_value = 'bar'; canvas.create_text.return_value = 'label'
        with patch('tkinter.Canvas', return_value=canvas):
            meter = VoiceInputMeter(Mock(), ('Segoe UI', 9))
        meter.render('Listening', .25)
        canvas.coords.assert_any_call('bar', 0, 0, 100, 24)
        canvas.itemconfigure.assert_called_once_with('label', text='[Listening]')
        canvas.coords.reset_mock(); canvas.itemconfigure.reset_mock()
        for _ in range(1000): meter.render('Listening', .25001)
        canvas.coords.assert_not_called(); canvas.itemconfigure.assert_not_called()
        canvas.place.assert_called_once()
        meter.render('Listening', .75)
        canvas.coords.assert_any_call('bar', 0, 0, 300, 24)
        canvas.winfo_width.return_value = 600
        meter.render('Listening', .75)
        canvas.coords.assert_any_call('bar', 0, 0, 450, 24)
        meter.render('Transcribing')
        canvas.coords.assert_any_call('bar', 0, 0, 0, 24)
        canvas.itemconfigure.assert_called_once_with('label', text='[Transcribing]')
        for _ in range(1000): meter.render('')
        canvas.place_forget.assert_called_once()
        canvas.create_rectangle.assert_called_once(); canvas.create_text.assert_called_once()


if __name__ == '__main__': unittest.main()
