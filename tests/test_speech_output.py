from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bwv.speech import SpeechSettings
from bwv.speech_output import SpeechOutput
from bwv.voice import VoiceSettings
import test_voice_overlay as voice_tests


class SpeechOutputTests(unittest.TestCase):
    def setUp(self):
        self.clock=[100]
        self.model=SimpleNamespace(identity=('session','creature'),message_time=1,last_message='Old reply',
            can_send=lambda:True,can_show=lambda:True)
        self.window=SimpleNamespace(model=self.model,game=1,hwnd=2,win=Mock(),voice=None,notice='',captions=Mock())
        self.window.win.u.GetForegroundWindow.return_value=1
        self.service=Mock(busy=False,error=None);self.service.start.return_value=True
        self.output=SpeechOutput(self.window,SpeechSettings(enabled=True),'unused',service=self.service,clock=lambda:self.clock[0])
        self.output.tick()

    def message(self,text='New reply'):
        self.model.last_message=text;self.model.message_time+=1;self.output.tick()

    def test_old_reply_is_not_replayed_and_new_reply_is_spoken_once(self):
        self.service.start.assert_not_called()
        self.message();self.service.start.assert_called_once_with('New reply')
        for _ in range(20):self.output.tick()
        self.service.start.assert_called_once()

    def test_scene_change_focus_loss_and_cancel_discard_pending_reply(self):
        self.service.busy=True;self.message()
        self.window.win.u.GetForegroundWindow.return_value=9;self.output.tick()
        self.service.cancel.assert_called();self.assertIsNone(self.output.pending)
        self.service.busy=False;self.clock[0]+=2
        self.window.win.u.GetForegroundWindow.return_value=1;self.output.tick()
        self.service.start.assert_not_called()
        self.model.identity=('new','creature');self.message('Old scene')
        self.service.start.assert_not_called()

    def test_empty_messages_and_errors_are_not_spoken(self):
        self.message('');self.service.start.assert_not_called()
        self.service.busy=True;self.message('Queue');self.service.busy=False;self.service.error='Synthetic error'
        self.output.tick();self.output.tick()
        self.window.captions.queue_voice_notice.assert_called_once()
        self.assertIn('Synthetic error',self.window.notice)
        self.service.start.assert_not_called();self.assertTrue(self.output.failed)

    def test_message_source_keeps_acknowledgements_and_errors_visible_but_silent(self):
        from bwv.overlay import OverlayModel
        from test_vr import payload
        model=OverlayModel();model.update(payload(),now=self.clock[0])
        model.can_send=lambda:OverlayModel.can_send(model,now=self.clock[0])
        self.window.model=model
        output=SpeechOutput(self.window,SpeechSettings(enabled=True),'unused',service=self.service,clock=lambda:self.clock[0])
        output.tick()
        for i,(source,text) in enumerate((('acknowledgement','[Creature is obeying your command]'),
                ('error','[ERROR: Request failed]'),('error','An error without brackets'),
                ('notice','Plain status text'))):
            model.accept_message({'text':text,'source':source,'created_at':101+i})
            output.tick()
            self.assertEqual(model.last_message,text)
            self.assertEqual(model.message_source,source)
        self.service.start.assert_not_called()
        model.accept_message({'text':'I will water that tree. 👋','source':'god_question','created_at':106})
        output.tick();self.service.start.assert_called_once_with('I will water that tree.')

    def test_legacy_banners_and_emoji_only_messages_never_start_speech(self):
        for text in ('[Creature is obeying your command]','[ERROR: Synthetic error]','👋 ❤️'):
            self.message(text)
        self.service.start.assert_not_called()

    def test_busy_playback_keeps_only_latest_pending_line(self):
        self.service.busy=True;self.message('First');self.message('Latest')
        self.service.start.assert_not_called()
        self.service.busy=False;self.output.tick()
        self.service.start.assert_not_called()
        self.clock[0]+=.5;self.output.tick()
        self.service.start.assert_called_once_with('Latest')

    def test_short_data_gap_does_not_cut_playback_or_lose_new_reply(self):
        self.service.cancel.reset_mock()
        self.service.busy=True;self.output.tick()
        self.model.can_send=lambda:False
        self.clock[0]+=1;self.message('Reply arriving during a read gap')
        self.service.cancel.assert_not_called()
        self.service.busy=False;self.clock[0]+=.5;self.output.tick()
        self.service.start.assert_not_called()
        self.model.can_send=lambda:True;self.clock[0]+=.5;self.output.tick()
        self.service.start.assert_called_once_with('Reply arriving during a read gap')

    def test_actual_scene_lock_or_long_data_loss_still_cancels(self):
        self.service.busy=True;self.output.tick();self.service.cancel.reset_mock()
        self.model.can_show=lambda:False;self.output.tick()
        self.service.cancel.assert_called_once()
        self.model.can_show=lambda:True;self.model.can_send=lambda:False
        self.service.cancel.reset_mock();self.clock[0]+=3;self.output.tick()
        self.service.cancel.assert_called_once()

    def test_reply_accepted_during_real_model_read_gap_is_spoken_after_refresh(self):
        from bwv.overlay import OverlayModel
        from test_vr import payload
        model=OverlayModel();model.update(payload(),now=self.clock[0])
        model.can_send=lambda:OverlayModel.can_send(model,now=self.clock[0])
        self.window.model=model
        output=SpeechOutput(self.window,SpeechSettings(enabled=True),'unused',service=self.service,clock=lambda:self.clock[0])
        output.tick()
        model.read_gap();model.accept_message({'text':'A fresh reply','created_at':101})
        self.clock[0]=101;output.tick();self.service.start.assert_not_called()
        self.clock[0]=102;model.update(payload(now=102),now=102);output.tick()
        self.service.start.assert_called_once_with('A fresh reply')

    def test_output_device_error_recovers_on_later_reply_without_replaying_failed_line(self):
        self.message('Failed line');self.service.start.reset_mock()
        self.service.error='Output interrupted';self.service.retryable_error=True
        self.service.busy=False;self.output.tick()
        self.assertFalse(self.output.failed)
        self.service.start.assert_not_called()
        self.clock[0]+=1;self.message('Later reply');self.service.start.assert_not_called()
        self.clock[0]+=5;self.output.tick()
        self.service.start.assert_called_once_with('Later reply')
        self.window.captions.queue_voice_notice.assert_called_once()

    def test_feedback_protection_can_be_disabled_without_changing_input_preferences(self):
        voice=Mock(automatic=True,service=Mock(busy=True));self.window.voice=voice
        self.message();voice.cancel.assert_called_once();self.service.start.assert_not_called()
        self.output.config=replace(self.output.config,feedback_protection=False)
        self.output.tick();self.service.start.assert_called_once_with('New reply')
        self.service.busy=True
        self.assertFalse(self.output.blocks_microphone)
        self.output.config=replace(self.output.config,feedback_protection=True)
        self.assertTrue(self.output.blocks_microphone)

    def test_manual_recording_has_priority_and_waiting_lines_expire(self):
        self.window.voice=Mock(automatic=False,service=Mock(busy=True),config=VoiceSettings())
        self.message();self.service.start.assert_not_called()
        self.clock[0]+=11;self.output.tick();self.assertIsNone(self.output.pending)

    def test_microphone_listener_obeys_optional_protection_and_push_to_talk_interrupts(self):
        fixture=voice_tests.VoiceOverlayTests();fixture.setUp()
        speech=Mock(blocks_microphone=True);fixture.w.speech_output=speech
        fixture.press();speech.cancel.assert_called_once();fixture.service.start.assert_not_called()
        speech.blocks_microphone=False;fixture.v.tick()
        fixture.service.start.assert_called_once()
        speech.blocks_microphone=True;fixture.v.tick()
        self.assertTrue(fixture.v.job.cancelled.is_set())
        self.assertNotEqual(fixture.w.notice,'[Voice input cancelled.]')

    def test_automatic_listener_resumes_when_protected_playback_finishes(self):
        fixture=voice_tests.VoiceOverlayTests();fixture.setUp()
        fixture.v.automatic=True
        fixture.w.model.is_thinking.return_value=False
        fixture.w.speech_output=Mock(blocks_microphone=True)
        fixture.v.tick();fixture.service.start.assert_not_called()
        fixture.w.speech_output.blocks_microphone=False
        fixture.v.tick();fixture.service.start.assert_called_once()
