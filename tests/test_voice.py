"""Synthetic audio and mocked providers only: no microphone, downloads or paid calls."""
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import httpx

from bwv.llm import LLMConfig
from bwv.launcher_ui import save_basics
from bwv.voice import VoiceSettings, VoiceError, VoiceInput, Transcriber, capture, transcript_text, microphone_level


def form(config):
    data = asdict(config)
    data.update(data.pop('cadence'))
    data.update({'voice_' + k: v for k, v in data.pop('voice').items()})
    return {k: v if type(v) is bool else '' if v is None else str(v) for k, v in data.items()}


def audio_clip(seconds=0.5, rate=16000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(b'\x00\x10' * round(seconds * rate))
    return output.getvalue()


class SettingsTests(unittest.TestCase):
    def test_push_to_talk_defaults_and_form_roundtrip(self):
        self.assertTrue(VoiceSettings().push_to_talk)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';config=LLMConfig('fixture');config.save(path)
            values=form(config);values['voice_push_to_talk']=False
            saved=save_basics(path,values)
            self.assertFalse(saved.voice.push_to_talk)
            self.assertFalse(saved.voice.enabled)
            self.assertEqual(LLMConfig.load(path),saved)
            values['voice_push_to_talk']='false'
            with self.assertRaises(ValueError):save_basics(path,values)

    def test_push_to_talk_control_follows_player_preference(self):
        import tkinter as tk
        from bwv.voice_settings_ui import VoiceTab
        tcl=tk.Tcl();tab=VoiceTab.__new__(VoiceTab)
        tab.values={'voice_push_to_talk':tk.BooleanVar(tcl,value=True)}
        tab.push_to_talk=tk.BooleanVar(tcl)
        tab.push_to_talk_toggle=Mock();tab.hotkey=Mock()
        for enabled in (True,False,True):
            tab.values['voice_push_to_talk'].set(enabled);tab.refresh_input_mode()
            self.assertEqual(tab.push_to_talk.get(),enabled)
            tab.push_to_talk_toggle.configure.assert_called_with(state='normal')
            tab.hotkey.configure.assert_called_with(state='readonly')
        tab.values['voice_push_to_talk'].set(False);tab.refresh_input_mode()
        self.assertFalse(tab.push_to_talk.get())
        tab.hotkey.configure.assert_called_with(state='readonly')

    def test_old_settings_opt_out_and_sparse_voice_roundtrips(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text(json.dumps({'model': 'fixture'}))
            self.assertFalse(LLMConfig.load(path).voice.enabled)
            path.write_text(json.dumps({'model': 'fixture', 'voice': {'enabled': True, 'auto_send': False}}))
            config = LLMConfig.load(path)
            self.assertEqual(config.voice, VoiceSettings(enabled=True, auto_send=False))
            config.save(path)
            self.assertEqual(LLMConfig.load(path).voice, config.voice)

    def test_voice_validation(self):
        for kwargs in ({'enabled': 'yes'}, {'provider': 'other'}, {'hotkey': 'A'}, {'push_to_talk': 1},
                {'model': None}, {'max_recording_seconds': 0}, {'max_recording_seconds': True},
                {'timeout_seconds': float('nan')}, {'language': 'English'}, {'auto_send': 1},
                {'max_requests': 0}, {'max_requests': True}, {'max_requests': 1.5},
                {'max_audio_minutes': 0}, {'max_audio_minutes': -1}, {'max_audio_minutes': True},
                {'max_audio_minutes': float('inf')}, {'max_audio_minutes': float('nan')},
                {'base_url': 'http://example.com/v1'}, {'base_url': 'https://user:secret@example.com'},
                {'base_url': 'https://example.com?key=secret'}, {'api_key_env': 'secret-key'},
                {'provider': 'api', 'api_key_env': None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                VoiceSettings(**{'enabled': True, 'provider': 'api', **kwargs})
        self.assertIsNone(VoiceSettings(provider='api', base_url='http://localhost:8000/v1', api_key_env=None).api_key_env)
        with self.assertRaises(ValueError): VoiceSettings.parse({'unknown': True})

    def test_blank_voice_model_saves_without_enabling_voice_or_changing_chat(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('chat-fixture'); config.save(path)
            for provider in ('local', 'api'):
                values = form(config)
                values.update(voice_enabled=True, voice_provider=provider, voice_model='   ')
                save_basics(path, values)
                saved = LLMConfig.load(path)
                self.assertEqual(saved.model, 'chat-fixture')
                self.assertEqual(saved.voice.model, '')
                self.assertFalse(saved.voice.input_enabled)

    def test_unused_api_connection_does_not_block_chat_settings(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('chat-fixture'); config.save(path)
            for provider, enabled, model in (('api', False, ''), ('api', True, ''),
                                             ('local', True, 'base')):
                with self.subTest(provider=provider, enabled=enabled, model=model):
                    values = form(config)
                    values.update(voice_provider=provider, voice_enabled=enabled,
                                  voice_model=model, voice_base_url='', voice_api_key_env='')
                    saved = save_basics(path, values)
                    self.assertEqual(LLMConfig.load(path), saved)
                    self.assertEqual(saved.model, 'chat-fixture')
            values.update(voice_provider='api', voice_enabled=True, voice_model='speech-fixture')
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                save_basics(path, values)
            self.assertEqual(path.read_bytes(), before)

    def test_quota_form_roundtrip_and_blank_unlimited(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            original = LLMConfig('fixture', max_messages=25); original.save(path)
            values = form(original)
            values.update(voice_max_requests='40', voice_max_audio_minutes='2.5')
            saved = save_basics(path, values)
            self.assertEqual((saved.voice.max_requests, saved.voice.max_audio_minutes), (40, 2.5))
            self.assertEqual(LLMConfig.load(path), saved)
            self.assertEqual(saved.max_messages, 25)
            values.update(voice_max_requests='', voice_max_audio_minutes=' ')
            saved = save_basics(path, values)
            self.assertIsNone(saved.voice.max_requests)
            self.assertIsNone(saved.voice.max_audio_minutes)
            before = path.read_bytes()
            for field, value in (('voice_max_requests', '1.5'), ('voice_max_audio_minutes', 'nan')):
                invalid = {**values, field: value}
                with self.assertRaises(ValueError): save_basics(path, invalid)
                self.assertEqual(path.read_bytes(), before)

    def test_save_separate_keys_and_preserve_both_on_blank(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('fixture'); config.save(path)
            values = form(config)
            values.update(voice_enabled=True, voice_provider='api', voice_model='speech-fixture',
                          voice_api_key_env='VOICE_KEY', voice_auto_send=False)
            saved = save_basics(path, values, 'fixture-chat-secret', voice_key='fixture-voice-secret')
            self.assertTrue(saved.voice.enabled)
            self.assertFalse(saved.voice.auto_send)
            self.assertEqual(saved.model, 'fixture')
            env = path.parent / '.local' / '.env'
            self.assertIn('VOICE_KEY=fixture-voice-secret', env.read_text())
            self.assertIn('OPENAI_API_KEY=fixture-chat-secret', env.read_text())
            self.assertNotIn('secret', path.read_text())
            before = env.read_bytes()
            save_basics(path, values)
            self.assertEqual(env.read_bytes(), before)
            values['voice_api_key_env'] = 'OPENAI_API_KEY'
            previous = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'different key variable'):
                save_basics(path, values, 'fixture-one', voice_key='fixture-two')
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(env.read_bytes(), before)

    def test_presets_do_not_change_chat_and_clear_typed_voice_key(self):
        import tkinter as tk
        from bwv.voice_settings_ui import PRESETS, apply_voice_preset, voice_form_values
        tcl = tk.Tcl()
        config = LLMConfig('chat-fixture', voice=VoiceSettings(enabled=True, language='hu',
            microphone='Fixture microphone', auto_send=False, max_requests=25, max_audio_minutes=5))
        values = {k: (tk.BooleanVar if type(v) is bool else tk.StringVar)(tcl, value=v)
                  for k, v in form(config).items()}
        key = tk.StringVar(tcl)
        protected = {k:v.get() for k,v in values.items() if k not in
            {'voice_provider', 'voice_model', 'voice_base_url', 'voice_api_key_env'}}
        for name in (*PRESETS, 'OpenAI transcription'):
            with self.subTest(preset=name):
                key.set('fixture-secret')
                apply_voice_preset(values, key, name)
                self.assertEqual(key.get(), '')
                self.assertEqual({k:values[k].get() for k in protected}, protected)
                data = voice_form_values({k:v.get() for k,v in values.items()})
                if name == 'Custom compatible API':
                    self.assertEqual(data['base_url'], ''); self.assertEqual(data['model'], '')
                    data.update(base_url='https://speech.example/v1', model='fixture')
                VoiceSettings(**data)
        self.assertEqual(values['voice_model'].get(), 'gpt-transcribe')


class TranscriberTests(unittest.TestCase):
    def api(self, handler, **kwargs):
        config = VoiceSettings(enabled=True, provider='api', **{'model': 'speech-fixture', **kwargs})
        client_type = httpx.Client
        def client(**options):
            self.assertFalse(options['follow_redirects'])
            self.assertFalse(options['trust_env'])
            return client_type(transport=httpx.MockTransport(handler), **options)
        return Transcriber(config, Path('unused-settings.json')), patch('httpx.Client', client)

    def test_new_provider_presets_send_their_own_endpoint_model_key_and_language(self):
        from bwv.voice_settings_ui import PRESETS
        for name in ('Groq Whisper (fast)', 'Groq Whisper (accuracy)', 'Together AI'):
            preset = dict(PRESETS[name]); preset.pop('provider')
            calls = []
            def handler(request):
                calls.append(request)
                self.assertEqual(str(request.url), preset['base_url'] + '/audio/transcriptions')
                self.assertEqual(request.headers['Authorization'], 'Bearer fixture-voice-key')
                body = request.read()
                self.assertIn(b'name="model"\r\n\r\n' + preset['model'].encode(), body)
                self.assertIn(b'name="language"\r\n\r\nhu', body)
                self.assertIn(b'name="response_format"\r\n\r\njson', body)
                self.assertNotIn(b'fixture-chat-key', body)
                return httpx.Response(200, json={'text':'Gyere ide!'})
            transcriber, client = self.api(handler, **preset, language='hu', max_requests=1)
            with self.subTest(provider=name), client, patch('bwv.credentials.credential_environment',
                    return_value={preset['api_key_env']:'fixture-voice-key', 'OPENAI_API_KEY':'fixture-chat-key'}) as keys:
                self.assertEqual(transcriber.transcribe(audio_clip()), 'Gyere ide!')
                self.assertEqual(keys.call_args.args[0], preset['api_key_env'])
                with self.assertRaisesRegex(VoiceError, 'request limit reached'):
                    transcriber.transcribe(audio_clip())
            self.assertEqual(len(calls), 1)

    def test_one_multipart_call_with_voice_model_only(self):
        calls = []
        audio = audio_clip()
        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/audio/transcriptions')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-key')
            body = request.read()
            for part in (b'speech-fixture', b'filename="speech.wav"', b'audio/wav', audio, b'json', b'en'):
                self.assertIn(part, body)
            self.assertNotIn(b'history', body)
            self.assertIn(b'name="language"\r\n\r\nen', body)
            self.assertNotIn(b'name="languages[]"', body)
            return httpx.Response(200, json={'text': ' Come here.\n Please. '})
        transcriber, client = self.api(handler, language='en')
        with client, patch('bwv.credentials.credential_environment', return_value={'OPENAI_API_KEY': 'fixture-key'}):
            self.assertEqual(transcriber.transcribe(audio), 'Come here. Please.')
        self.assertEqual(len(calls), 1)

    def test_gpt_transcribe_language_hint_and_automatic_detection(self):
        for language in ('en', 'hu', ''):
            expected = 'Gyere ide, és segíts a falusiaknak!' if language == 'hu' else 'Come here'
            def handler(request):
                body = request.read()
                self.assertIn(b'gpt-transcribe', body)
                self.assertNotIn(b'name="language"', body)
                if language:
                    self.assertIn(b'name="languages[]"\r\n\r\n' + language.encode('ascii'), body)
                else:
                    self.assertNotIn(b'name="languages[]"', body)
                payload = {'text': expected, 'languages': [{'code': language or 'en'}]}
                return httpx.Response(200, content=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            transcriber, client = self.api(handler, model='gpt-transcribe', language=language)
            with self.subTest(language=language), client, patch('bwv.credentials.credential_environment',
                    return_value={'OPENAI_API_KEY': 'fixture-key'}):
                self.assertEqual(transcriber.transcribe(audio_clip()), expected)

    def test_local_endpoint_needs_no_key(self):
        def handler(request):
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, json={'text': 'Wave'})
        transcriber, client = self.api(handler, base_url='http://localhost:8000/v1', api_key_env=None)
        with client: self.assertEqual(transcriber.transcribe(audio_clip()), 'Wave')

    def test_errors_are_safe_and_never_retried_or_redirected(self):
        for code in (302, 401, 403, 404, 413, 429, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(code, text='PRIVATE provider response', headers={'Location': 'https://other.example'})
            transcriber, client = self.api(handler)
            with self.subTest(code=code), client, patch('bwv.credentials.credential_environment',
                    return_value={'OPENAI_API_KEY': 'fixture-key'}):
                with self.assertRaises(VoiceError) as error: transcriber.transcribe(audio_clip())
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertNotIn('fixture-key', str(error.exception))
                self.assertEqual(len(calls), 1)

    def test_invalid_or_large_result_is_rejected(self):
        for body in (b'{', b'[]', b'{"text": ""}', b'{"text": 7}', b'x' * 65537,
                     json.dumps({'text': 'a' * 2001}).encode()):
            transcriber, client = self.api(lambda request: httpx.Response(200, content=body))
            with client, patch('bwv.credentials.credential_environment', return_value={'OPENAI_API_KEY': 'fixture-key'}):
                with self.assertRaises(VoiceError): transcriber.transcribe(audio_clip())

    def test_request_limit_blocks_upload_after_success_or_failure(self):
        for status in (200, 429):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={'text': 'Hello'})
            transcriber, client = self.api(handler, max_requests=1)
            with self.subTest(status=status), client, patch('bwv.credentials.credential_environment',
                    return_value={'OPENAI_API_KEY': 'fixture-key'}):
                if status == 200: self.assertEqual(transcriber.transcribe(audio_clip()), 'Hello')
                else:
                    with self.assertRaises(VoiceError): transcriber.transcribe(audio_clip())
                with self.assertRaisesRegex(VoiceError, 'request limit reached'):
                    transcriber.transcribe(audio_clip())
            self.assertEqual(len(calls), 1)
            self.assertEqual(transcriber.requests, 1)

    def test_audio_budget_counts_duration_once_and_rejected_clip_is_not_sent(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'text': 'Hello'})
        transcriber, client = self.api(handler, max_audio_minutes=0.05)  # Three seconds.
        with client, patch('bwv.credentials.credential_environment', return_value={'OPENAI_API_KEY': 'fixture-key'}):
            transcriber.transcribe(audio_clip(2, rate=48000))
            with self.assertRaisesRegex(VoiceError, '1.0 seconds remain'):
                transcriber.transcribe(audio_clip(2))
            self.assertEqual(len(calls), 1)
            transcriber.transcribe(audio_clip(1))
            with self.assertRaisesRegex(VoiceError, 'audio limit reached'):
                transcriber.transcribe(audio_clip(0.5))
        self.assertEqual(len(calls), 2)
        self.assertEqual(transcriber.audio_seconds, 3)

    def test_missing_key_and_invalid_audio_do_not_consume_budget(self):
        transcriber, client = self.api(Mock(side_effect=AssertionError('Must not send')), max_requests=1)
        with client, patch('bwv.credentials.credential_environment', return_value={}):
            with self.assertRaisesRegex(VoiceError, 'key is missing'): transcriber.transcribe(audio_clip())
            with self.assertRaisesRegex(VoiceError, 'Cannot read the recording'): transcriber.transcribe(b'invalid')
        self.assertEqual((transcriber.requests, transcriber.audio_seconds), (0, 0))

    def test_cancel_during_api_preparation_does_not_upload_or_consume_budget(self):
        cancelled = threading.Event()
        handler = Mock(side_effect=AssertionError('Cancelled audio must not be sent'))
        transcriber, client = self.api(handler)
        def credentials(*args):
            cancelled.set()
            return {'OPENAI_API_KEY': 'fixture-key'}
        with client, patch('bwv.credentials.credential_environment', side_effect=credentials):
            self.assertEqual(transcriber.transcribe(audio_clip(), cancelled=cancelled), '')
        handler.assert_not_called()
        self.assertEqual((transcriber.requests, transcriber.audio_seconds), (0, 0))

    def test_cancel_before_transcription_skips_both_backends(self):
        cancelled = threading.Event(); cancelled.set()
        for provider in ('local', 'api'):
            transcriber = Transcriber(VoiceSettings(provider=provider), Path('unused-settings.json'))
            with self.subTest(provider=provider), patch.object(transcriber, '_local') as local, \
                    patch.object(transcriber, '_api') as remote:
                self.assertEqual(transcriber.transcribe(b'audio', cancelled=cancelled), '')
                local.assert_not_called(); remote.assert_not_called()

    def test_cancel_while_loading_local_model_skips_inference(self):
        cancelled = threading.Event()
        model = Mock()
        def factory(*args, **kwargs):
            cancelled.set()
            return model
        transcriber = Transcriber(VoiceSettings(), Path('unused-settings.json'))
        with patch.dict('sys.modules', faster_whisper=SimpleNamespace(WhisperModel=factory)):
            self.assertEqual(transcriber.transcribe(b'audio', cancelled=cancelled), '')
        model.transcribe.assert_not_called()

    def test_unset_quotas_stay_unlimited_across_requests(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'text': 'Hello'})
        transcriber, client = self.api(handler)
        with client, patch('bwv.credentials.credential_environment', return_value={'OPENAI_API_KEY': 'fixture-key'}):
            for _ in range(40): transcriber.transcribe(audio_clip())
        self.assertEqual(len(calls), 40)
        self.assertEqual((transcriber.requests, transcriber.audio_seconds), (40, 20))

    def test_local_model_loaded_once_but_each_clip_is_independent(self):
        model = Mock()
        clips = []
        def transcribe(stream, **options):
            clips.append(stream.read())
            self.assertFalse(options['condition_on_previous_text'])
            self.assertTrue(options['vad_filter'])
            return iter([SimpleNamespace(text=' Come here ')]), None
        model.transcribe.side_effect = transcribe
        factory = Mock(return_value=model)
        transcriber = Transcriber(VoiceSettings(max_requests=1, max_audio_minutes=.01), Path('fixture/settings.json'))
        with patch.dict('sys.modules', faster_whisper=SimpleNamespace(WhisperModel=factory)):
            for audio in (b'first', b'second'):
                self.assertEqual(transcriber.transcribe(audio), 'Come here')
        self.assertEqual(clips, [b'first', b'second'])
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs['cpu_threads'], 2)
        self.assertEqual((transcriber.requests, transcriber.audio_seconds), (0, 0))


class CaptureTests(unittest.TestCase):
    def test_meter_tracks_volume_without_changing_recorded_audio(self):
        from array import array
        chunks = [array('h', [sample] * 8000).tobytes() for sample in (64, 8192, 0)]
        now = [100.0]; steps = iter(chunks); callback = []; levels = []
        class Stream:
            def __init__(self, **options): callback.append(options['callback'])
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def wait(_):
            now[0] += .11
            chunk = next(steps, None)
            if chunk is None: return True
            callback[0](chunk, len(chunk)//2, None, False)
            return False
        released = Mock(); released.is_set.return_value = False; released.wait.side_effect = wait
        sd = SimpleNamespace(RawInputStream=Stream, check_input_settings=Mock())
        with patch.dict('sys.modules', sounddevice=sd), patch('bwv.voice.time.monotonic', side_effect=lambda:now[0]):
            audio = capture(VoiceSettings(), released, threading.Event(), Mock(), on_level=levels.append)
        self.assertEqual(len(levels), 4)
        self.assertGreater(levels[0], 0); self.assertGreater(levels[1], levels[0])
        self.assertEqual(levels[2:], [0, 0])
        with wave.open(io.BytesIO(audio)) as stream:
            self.assertEqual(stream.readframes(stream.getnframes()), b''.join(chunks))
        self.assertEqual(microphone_level(b''), 0)
        self.assertEqual(microphone_level(b'\x00\x80' * 200000), 1)

    def record(self, pcm, *, cancel=False, full=False, status=False, native_rate=None):
        released, cancelled = threading.Event(), threading.Event()
        opened = []
        class Stream:
            def __init__(self, **options): self.options = options
            def __enter__(self):
                opened.append('open')
                self.options['callback'](pcm, len(pcm)//2, None, status)
                if cancel: cancelled.set()
                if not full: released.set()
                return self
            def __exit__(self, *args): opened.append('closed')
        def check(**options):
            if native_rate and options['samplerate'] != native_rate: raise ValueError('Unsupported rate')
        sd = SimpleNamespace(RawInputStream=Stream, check_input_settings=check,
            query_devices=Mock(return_value={'default_samplerate': native_rate}))
        with patch.dict('sys.modules', sounddevice=sd):
            try: return capture(VoiceSettings(max_recording_seconds=5), released, cancelled, Mock())
            finally: self.assertEqual(opened, ['open', 'closed'])

    def test_release_produces_wav_and_cancellation_discards(self):
        pcm = b'\x00\x10' * 8000
        with wave.open(io.BytesIO(self.record(pcm))) as stream:
            self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()), (1, 2, 16000))
            self.assertEqual(stream.readframes(8000), pcm)
        self.assertEqual(self.record(pcm, cancel=True), b'')

    def test_memory_bounded_silence_short_audio_and_overflow(self):
        with wave.open(io.BytesIO(self.record(b'\x00\x10' * 200000, full=True))) as stream:
            self.assertEqual(stream.getnframes(), 5 * 16000)
        for pcm, options in ((b'\0' * 16000, {}), (b'\x00\x10' * 100, {}),
                             (b'\x00\x10' * 8000, {'status': True})):
            with self.assertRaises(VoiceError): self.record(pcm, **options)

    def test_microphone_error_does_not_echo_device_or_os_details(self):
        with patch.dict('sys.modules', sounddevice=SimpleNamespace(RawInputStream=Mock(side_effect=OSError('PRIVATE')))):
            with self.assertRaises(VoiceError) as error:
                capture(VoiceSettings(), threading.Event(), threading.Event(), Mock())
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_driver_native_rate_preserved_in_audio(self):
        pcm = b'\x00\x10' * 24000
        with wave.open(io.BytesIO(self.record(pcm, native_rate=48000))) as stream:
            self.assertEqual(stream.getframerate(), 48000)
            self.assertEqual(stream.getnframes(), 24000)

    def test_microphone_choices_distinguish_drivers_without_recording(self):
        from bwv.voice_settings_ui import microphone_names
        sd = SimpleNamespace(query_hostapis=Mock(return_value=[{'name': 'MME'}, {'name': 'Windows WASAPI'}]),
            query_devices=Mock(return_value=[
                {'name': 'Fixture mic', 'hostapi': 0, 'max_input_channels': 1},
                {'name': 'Fixture mic', 'hostapi': 1, 'max_input_channels': 2},
                {'name': 'Fixture output', 'hostapi': 1, 'max_input_channels': 0}]), RawInputStream=Mock())
        self.assertEqual(microphone_names(sd), ('Fixture mic, MME', 'Fixture mic, Windows WASAPI'))
        sd.RawInputStream.assert_not_called()


class WorkerTests(unittest.TestCase):
    def test_empty_transcript_is_distinct_from_provider_failure(self):
        from bwv.voice import NoSpeechError
        events=[]
        worker=VoiceInput(VoiceSettings(),'unused',lambda *event:events.append(event),
            recorder=Mock(return_value=b'audio'),
            transcriber=Mock(transcribe=Mock(side_effect=NoSpeechError('No speech recognized.'))))
        worker.start(('session','creature'));worker.thread.join(2)
        self.assertEqual([kind for kind,_ in events],['voice_status','voice_empty','voice_done'])

    def test_volume_updates_replace_one_value_without_queueing_events(self):
        events = []
        def record(config, released, cancelled, on_recording, *, on_level):
            on_recording()
            for i in range(10000): on_level((i % 100) / 100)
            return b'audio'
        worker = VoiceInput(VoiceSettings(), 'unused', lambda *event:events.append(event),
            recorder=record, transcriber=Mock(transcribe=Mock(return_value='Come here')))
        job = worker.start(('session', 'creature')); worker.thread.join(2)
        self.assertFalse(worker.busy)
        self.assertEqual(job.level, 0)
        self.assertEqual([kind for kind, _ in events], ['voice_status', 'voice_status', 'voice_text', 'voice_done'])

    def test_reached_quota_reports_in_chat_without_opening_microphone(self):
        config = VoiceSettings(provider='api', max_requests=1)
        transcriber = Transcriber(config, 'unused-settings.json')
        transcriber.requests = 1
        events, recorder = [], Mock()
        worker = VoiceInput(config, 'unused-settings.json', lambda *event: events.append(event),
                            recorder=recorder, transcriber=transcriber)
        worker.start(('session', 'creature')); worker.thread.join(2)
        recorder.assert_not_called()
        self.assertEqual([kind for kind, _ in events], ['voice_error', 'voice_done'])
        self.assertIn('You can still type in chat', events[0][1][1])

    def test_cancelled_transcription_never_delivers_and_no_jobs_accumulate(self):
        started, finish = threading.Event(), threading.Event()
        transcriber = Mock()
        def transcribe(audio, *, cancelled=None):
            started.set(); finish.wait(3); return 'Late request'
        transcriber.transcribe.side_effect = transcribe
        events = []
        worker = VoiceInput(VoiceSettings(), 'unused', lambda *event: events.append(event),
                            recorder=Mock(return_value=b'audio'), transcriber=transcriber)
        first = worker.start(('session', 'creature'))
        self.assertTrue(started.wait(2))
        try:
            worker.cancel()
            for _ in range(20): self.assertIsNone(worker.start(('session', 'creature')))
            self.assertIs(worker.job, first)
        finally:
            finish.set(); worker.thread.join(2)
        self.assertFalse(worker.busy)
        self.assertFalse(any(kind == 'voice_text' for kind, _ in events))
        transcriber.transcribe.assert_called_once()

    def test_each_new_press_sends_only_its_own_clip(self):
        events = []
        transcriber = Mock(); transcriber.transcribe.side_effect = ['first words', 'second words']
        recorder = Mock(side_effect=[b'first clip', b'second clip'])
        worker = VoiceInput(VoiceSettings(), 'unused', lambda *event: events.append(event),
                            recorder=recorder, transcriber=transcriber)
        for _ in range(2): worker.start(('s','c')); worker.thread.join(2)
        self.assertEqual([call.args[0] for call in transcriber.transcribe.call_args_list], [b'first clip', b'second clip'])
        self.assertEqual([value for kind, (_, value) in events if kind == 'voice_text'], ['first words', 'second words'])


if __name__ == '__main__': unittest.main()
