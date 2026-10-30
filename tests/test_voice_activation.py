import io
from array import array
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import wave

from bwv.voice import VoiceSettings,VoiceInput
from bwv.voice_activation import SpeechSegment,capture_activated
from bwv.voice_overlay import VoiceOverlay
from bwv.vr import voice_settings
import test_voice_overlay


FRAME=b'\x00\x10'*320
QUIET=b'\0'*640


class ActivationTests(unittest.TestCase):
    def test_empty_transcription_rearms_promptly_even_after_repeated_empty_results(self):
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        w=fixture.w;w.model.is_thinking.return_value=False
        now=[100.0]
        with patch('bwv.voice_overlay.time.monotonic',side_effect=lambda:now[0]):
            v=VoiceOverlay(w,VoiceSettings(enabled=True,push_to_talk=False),'unused',service=fixture.service)
            v.tick();v.handle('voice_empty',(v.job,'No speech recognized.'))
            v.handle('voice_done',(v.job,None))
            self.assertFalse(v.auto_failed)
            self.assertEqual(v.retry_at,101)
            v.tick();self.assertEqual(fixture.service.start.call_count,1)
            now[0]=101;v.tick();self.assertEqual(fixture.service.start.call_count,2)
            self.assertTrue(v.active)
            self.assertEqual(w.entry.get(),'')
            v.handle('voice_empty',(v.job,'No speech recognized.'))
            v.handle('voice_done',(v.job,None))
            self.assertEqual(v.retry_at,102)

    def test_quiet_noise_flagged_as_speech_does_not_trigger_upload(self):
        segment=SpeechSegment(16000,5,lambda data,rate:True)
        noise=array('h',[40,-40]*160).tobytes()  # About -58 dBFS.
        for _ in range(1000):segment.feed(noise)
        # A brief louder click also must not open the speech gate.
        for frame in [FRAME]*3+[noise]*100:segment.feed(frame)
        self.assertFalse(segment.started)
        self.assertEqual(segment.audio(),b'')

    def test_quiet_speech_survives_energy_check_and_noise_ends_phrase(self):
        segment=SpeechSegment(16000,5,lambda data,rate:True)
        noise=array('h',[40,-40]*160).tobytes()
        speech=array('h',[104,-104]*160).tobytes()  # About -50 dBFS.
        for frame in [noise]*15+[speech]*30+[noise]*60:segment.feed(frame)
        self.assertTrue(segment.finished)
        with wave.open(io.BytesIO(segment.audio())) as stream:
            self.assertIn(speech*30,stream.readframes(stream.getnframes()))

    def segment(self,seconds=5):
        return SpeechSegment(16000,seconds,lambda data,rate:data==FRAME)

    def test_silence_and_short_noise_never_form_a_phrase(self):
        segment=self.segment()
        for _ in range(10000):segment.feed(QUIET)
        for frame in [FRAME]*3+[QUIET]*100:segment.feed(frame)
        self.assertFalse(segment.started)
        self.assertEqual(segment.audio(),b'')
        self.assertLessEqual(len(segment.pre),15)
        self.assertEqual(segment.frames,[])

    def test_speech_has_preroll_and_ends_after_pause(self):
        segment=self.segment()
        for frame in [QUIET]*15+[FRAME]*20+[QUIET]*59:segment.feed(frame)
        self.assertTrue(segment.started);self.assertFalse(segment.finished)
        segment.feed(QUIET);self.assertTrue(segment.finished)
        with wave.open(io.BytesIO(segment.audio())) as stream:
            audio=stream.readframes(stream.getnframes())
            self.assertTrue(audio.startswith(QUIET))
            self.assertIn(FRAME*20,audio)
        before=len(segment.frames);segment.feed(FRAME)
        self.assertEqual(len(segment.frames),before)

    def test_continuous_voice_is_bounded(self):
        segment=self.segment()
        for _ in range(10000):segment.feed(FRAME)
        self.assertTrue(segment.finished)
        self.assertEqual(len(segment.frames),250)

    def test_capture_synthetic_phrase_and_cancellation(self):
        for cancelled in (False,True):
            stop=threading.Event()
            class Stream:
                def __init__(self,**options):self.options=options
                def __enter__(self):
                    for frame in [FRAME]*20+[QUIET]*60:self.options['callback'](frame,320,None,False)
                    if cancelled:stop.set()
                    return self
                def __exit__(self,*args):pass
            sd=SimpleNamespace(RawInputStream=Stream,check_input_settings=Mock())
            vad=SimpleNamespace(Vad=lambda mode:SimpleNamespace(is_speech=lambda data,rate:data==FRAME))
            with patch.dict('sys.modules',sounddevice=sd,webrtcvad=vad):
                result=capture_activated(VoiceSettings(),threading.Event(),stop,Mock())
            self.assertEqual(bool(result),not cancelled)

    def test_empty_idle_window_never_calls_transcription(self):
        transcriber=Mock();emit=Mock()
        service=VoiceInput(VoiceSettings(),'unused',emit,recorder=lambda *a,**k:b'',transcriber=transcriber)
        service.start(('session','creature'));service.thread.join(2)
        self.assertFalse(service.busy)
        transcriber.transcribe.assert_not_called()

    def test_driver_buffers_are_split_into_vad_frames_without_losing_audio(self):
        pcm=FRAME*20+QUIET*60
        callbacks=[pcm[:1110],pcm[1110:4320],pcm[4320:12800],pcm[12800:32000],pcm[32000:]]
        class Stream:
            def __init__(self,**options):
                self.options=options
                self.assert_no_forced_block='blocksize' not in options
                if not self.assert_no_forced_block:raise AssertionError('Driver buffers must match push-to-talk')
            def __enter__(self):
                for chunk in callbacks:self.options['callback'](chunk,len(chunk)//2,None,False)
                return self
            def __exit__(self,*args):pass
        sd=SimpleNamespace(RawInputStream=Stream,check_input_settings=Mock())
        vad=SimpleNamespace(Vad=lambda mode:SimpleNamespace(is_speech=lambda data,rate:data==FRAME))
        with patch.dict('sys.modules',sounddevice=sd,webrtcvad=vad):
            result=capture_activated(VoiceSettings(),threading.Event(),threading.Event(),Mock())
        with wave.open(io.BytesIO(result)) as stream:
            self.assertEqual(stream.readframes(stream.getnframes()),pcm)

    def test_vr_starts_without_key_and_stops_on_lost_focus(self):
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        w=fixture.w;w.vr=True;w.model.is_thinking.return_value=False
        v=VoiceOverlay(w,voice_settings(VoiceSettings(enabled=True,auto_send=False,push_to_talk=False)),'unused',service=fixture.service)
        v.tick();self.assertTrue(v.active)
        self.assertFalse(fixture.keys)
        w.win.u.GetForegroundWindow.return_value=99
        v.tick();self.assertTrue(v.job.cancelled.is_set())
        self.assertFalse(v.active)

    def test_activation_error_latches_until_settings_restart(self):
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        w=fixture.w;w.vr=True;w.model.is_thinking.return_value=False
        v=VoiceOverlay(w,VoiceSettings(enabled=True,push_to_talk=False),'unused',service=fixture.service)
        v.tick();v.handle('voice_error',(v.job,'Synthetic failure'))
        v.handle('voice_done',(v.job,None));v.retry_at=0
        v.tick();fixture.service.start.assert_called_once()
        self.assertIn('Save & apply',w.notice)

    def test_desktop_can_opt_into_activation_without_pressing_a_key(self):
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        w=fixture.w;w.vr=False;w.model.is_thinking.return_value=False
        v=VoiceOverlay(w,VoiceSettings(enabled=True,push_to_talk=False),'unused',service=fixture.service)
        v.tick()
        self.assertTrue(v.automatic);self.assertTrue(v.active)
        self.assertFalse(fixture.keys)

    def test_vr_keeps_push_to_talk_and_does_not_start_without_key(self):
        fixture=test_voice_overlay.VoiceOverlayTests();fixture.setUp()
        w=fixture.w;w.vr=True
        v=VoiceOverlay(w,VoiceSettings(enabled=True),'unused',service=fixture.service)
        v.tick()
        self.assertFalse(v.automatic);self.assertFalse(v.active)
        fixture.service.start.assert_not_called()
