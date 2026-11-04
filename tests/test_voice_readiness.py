"""Synthetic readiness checks: no microphone, API or local model is opened."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock,patch

from bwv.voice import VoiceSettings
from bwv.voice_readiness import initial_notice,VoiceReadiness,ready_notice,check_microphone


class VoiceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.config=VoiceSettings(enabled=True)
        self.microphone=Mock()

    def check(self,config=None,**kwargs):
        return initial_notice(config or self.config,'unused/settings.json',microphone=self.microphone,
            available=kwargs.pop('available',lambda _:True),environ={},**kwargs)

    def test_disabled_missing_model_and_review_do_not_open_microphone(self):
        self.assertIn('Voice input off.',self.check(replace(self.config,enabled=False)))
        self.assertIn('Choose a transcription model',self.check(replace(self.config,model='')))
        self.assertIn('Enable Send transcript immediately',self.check(replace(self.config,auto_send=False)))
        self.microphone.assert_not_called()

    def test_dependencies_and_credentials_are_checked_without_network(self):
        self.assertIn('Install local voice support',self.check(available=lambda name:name!='faster_whisper'))
        with patch('bwv.voice_readiness.credential_environment',return_value={}):
            self.assertIn('Set the transcription API key',self.check(replace(self.config,provider='api')))
        with patch('bwv.voice_readiness.credential_environment',side_effect=ValueError('private path')):
            text=self.check(replace(self.config,provider='api'))
            self.assertIn('Check the transcription API key',text);self.assertNotIn('private',text)
        self.microphone.assert_not_called()

    def test_microphone_failure_is_curated_and_ready_explains_input_mode(self):
        self.microphone.side_effect=RuntimeError('PRIVATE device response')
        text=self.check();self.assertTrue(text.startswith('Voice input stopped:'))
        self.assertNotIn('PRIVATE',text)
        self.microphone.side_effect=None
        self.assertEqual(self.check(),'Voice input ready. Hold F11 to speak.')
        self.assertEqual(self.check(replace(self.config,push_to_talk=False)),
            'Voice input ready. Speak, then pause to send.')

    def test_late_startup_result_cannot_overwrite_failure_and_close_suppresses_reports(self):
        entered=threading.Event();release=threading.Event();done=threading.Event();report=Mock()
        def check(*_):entered.set();release.wait(2);done.set();return 'Voice input ready.'
        monitor=VoiceReadiness(self.config,'unused',report,check=check)
        monitor.start();self.assertTrue(entered.wait(1))
        monitor.failed('Synthetic failure.');release.set();self.assertTrue(done.wait(1))
        # The same lock serializes the startup completion and runtime results.
        monitor.close()
        self.assertEqual([call.args[0] for call in report.call_args_list],['Voice input stopped: Synthetic failure.'])
        monitor.succeeded();monitor.failed('Ignored')
        report.assert_called_once()

    def test_success_reports_recovery_without_changing_preferences(self):
        report=Mock();monitor=VoiceReadiness(self.config,'unused',report)
        monitor.failed('Synthetic failure.');monitor.succeeded()
        report.assert_called_with(ready_notice(self.config))
        self.assertTrue(self.config.push_to_talk)

    def test_microphone_check_matches_capture_rate_fallback_without_recording(self):
        for manual,accepted in ((True,44100),(False,48000)):
            sd=Mock();sd.query_devices.return_value={'default_samplerate':44100}
            def check(**options):
                if options['samplerate']!=accepted:raise ValueError('Unsupported rate')
            sd.check_input_settings.side_effect=check
            with patch.dict('sys.modules',sounddevice=sd):
                check_microphone(replace(self.config,push_to_talk=manual))
            sd.RawInputStream.assert_not_called()

    def test_vr_failures_and_recovery_use_readiness_without_duplicate_error_notice(self):
        import test_voice_overlay
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        fixture.w.captions=Mock();fixture.v.readiness=Mock()
        fixture.press();fixture.event('voice_error','Synthetic failure.')
        fixture.v.readiness.failed.assert_called_once_with('Synthetic failure.')
        fixture.w.captions.queue_voice_notice.assert_not_called()
        fixture.release();fixture.press();fixture.event('voice_text','Hello.')
        fixture.v.readiness.succeeded.assert_called_once()
        fixture.w.captions.queue_voice_notice.assert_called_once_with('Heard: Hello.',fixture.w.model.identity)
