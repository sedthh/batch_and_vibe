"""Bounded, opt-in logs using synthetic audio; never record a microphone."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import wave

from bwv.voice import VoiceInput, VoiceSettings
from bwv.voice_diagnostics import VoiceDiagnostics, audio_summary


def fixture_audio():
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\x00\x10' * 8000)
    return output.getvalue()


def enable(folder):
    settings = Path(folder) / 'settings.json'
    settings.write_text('{}')
    private = settings.parent / '.local'; private.mkdir()
    (private / 'voice-debug.enabled').touch()
    return settings


class VoiceDiagnosticsTests(unittest.TestCase):
    def test_opt_out_never_creates_a_log(self):
        with TemporaryDirectory() as folder:
            settings = Path(folder) / 'settings.json'; settings.write_text('{}')
            logger = VoiceDiagnostics(settings)
            logger.write({'transcript': 'Private words'})
            self.assertFalse(logger.enabled)
            self.assertFalse(logger.path.exists())

    def test_log_remains_bounded_and_preserves_exact_hungarian_characters(self):
        with TemporaryDirectory() as folder:
            logger = VoiceDiagnostics(enable(folder))
            text = 'Gyere ide! Árvíztűrő tükörfúrógép.'
            for number in range(75): logger.write({'attempt': number, 'transcript': text})
            raw = logger.path.read_bytes()
            records = json.loads(raw)
            self.assertEqual(len(records), 20)
            self.assertEqual([r['attempt'] for r in records], list(range(55, 75)))
            self.assertTrue(all(r['transcript'] == text for r in records))
            self.assertLessEqual(len(raw), logger.MAX_BYTES)
            self.assertTrue(raw.isascii())
            self.assertFalse(list(logger.path.parent.glob('*.tmp')))

    def test_worker_logs_audio_quality_and_text_without_storing_audio(self):
        with TemporaryDirectory() as folder:
            settings = enable(folder)
            events = []
            audio = fixture_audio()
            expected = 'Gyere ide!'
            worker = VoiceInput(VoiceSettings(language='hu'), settings, lambda *event: events.append(event),
                recorder=Mock(return_value=audio), transcriber=Mock(transcribe=Mock(return_value=expected)))
            worker.start(('session', 'creature')); worker.thread.join(2)
            records = json.loads(worker.diagnostics.path.read_bytes())
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['language_hint'], 'hu')
            self.assertEqual(records[0]['transcript'], expected)
            self.assertEqual(records[0]['audio']['seconds'], 0.5)
            self.assertEqual(records[0]['audio']['sample_rate'], 16000)
            self.assertEqual(records[0]['audio']['rms_dbfs'], -18.1)
            self.assertNotIn(b'RIFF', worker.diagnostics.path.read_bytes())
            self.assertEqual([kind for kind, _ in events], ['voice_status', 'voice_text', 'voice_done'])

    def test_storage_failure_does_not_interrupt_voice(self):
        with TemporaryDirectory() as folder:
            logger = VoiceDiagnostics(enable(folder))
            with patch('bwv.voice_diagnostics.tempfile.NamedTemporaryFile', side_effect=PermissionError):
                logger.write({'transcript': 'Gyere ide'})
            self.assertFalse(logger.path.exists())

    def test_cancel_during_audio_summary_does_not_start_transcription(self):
        with TemporaryDirectory() as folder:
            settings = enable(folder)
            transcriber = Mock()
            worker = VoiceInput(VoiceSettings(), settings, Mock(),
                recorder=Mock(return_value=fixture_audio()), transcriber=transcriber)
            def summary(audio):
                worker.cancel()
                return {'seconds': .5}
            with patch('bwv.voice_diagnostics.audio_summary', side_effect=summary):
                worker.start(('session', 'creature')); worker.thread.join(2)
            self.assertFalse(worker.busy)
            self.assertEqual(transcriber.transcribe.call_count, 0)

    def test_digital_silence_has_no_infinite_log_values(self):
        output = io.BytesIO()
        with wave.open(output, 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(b'\0' * 16000)
        summary = audio_summary(output.getvalue())
        self.assertIsNone(summary['rms_dbfs'])
        self.assertIsNone(summary['peak_dbfs'])
        json.dumps(summary, allow_nan=False)
