"""Synthetic speech providers and audio; no real credentials or provider calls."""
from dataclasses import asdict,replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import wave

import httpx
from bwv.llm import LLMConfig
from bwv.launcher_ui import save_basics
from bwv.speech import SpeechSettings
from bwv.speech_audio import Synthesizer,SpeechError,SpeechPlaybackError,SpeechPlayer,decode_wav,play_audio
from bwv.speech_settings_ui import speech_form_values,apply_speech_preset
from test_launcher_ui import form


def wav():
    output=io.BytesIO()
    with wave.open(output,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
        stream.writeframes(b'\0\0'*240)
    return output.getvalue()


class SpeechConfigTests(unittest.TestCase):
    def test_old_settings_default_off_and_separate_input_output_roundtrip(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';path.write_text('{"model":"fixture"}')
            config=LLMConfig.load(path)
            self.assertFalse(config.speech.enabled);self.assertTrue(config.speech.feedback_protection)
            values=form(config);values.update(speech_enabled=True,speech_provider='windows',
                speech_voice='Synthetic voice',speech_speed='1.2',speech_volume='45',speech_feedback_protection=False)
            saved=save_basics(path,values)
            self.assertEqual(saved.voice,config.voice)
            self.assertTrue(saved.speech.enabled);self.assertFalse(saved.speech.feedback_protection)
            self.assertEqual(saved.speech.speed,1.2);self.assertEqual(saved.speech.volume,45)
            self.assertEqual(LLMConfig.load(path),saved)

    def test_separate_speech_secret_and_collision_validation(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';LLMConfig('fixture').save(path)
            values=form(LLMConfig.load(path));values['speech_api_key_env']='SYNTHETIC_SPEECH_KEY'
            save_basics(path,values,speech_key='synthetic-secret')
            self.assertNotIn('synthetic-secret',path.read_text())
            self.assertIn('SYNTHETIC_SPEECH_KEY=synthetic-secret',(path.parent/'.local/.env').read_text())
            values['speech_api_key_env']=values['api_key_env']
            before=path.read_bytes()
            with self.assertRaises(ValueError):save_basics(path,values,key='one',speech_key='two')
            self.assertEqual(path.read_bytes(),before)

    def test_provider_and_limit_validation(self):
        for change in ({'enabled':1},{'feedback_protection':'false'},{'volume':101},{'volume':True},
                {'speed':float('nan')},{'speed':0},{'max_requests':0},{'speaker_id':-1},{'timeout_seconds':121}):
            with self.subTest(change=change),self.assertRaises(ValueError):SpeechSettings(**change)
        for url in ('http://remote.example','https://user:secret@example.com','https://example.com/?key=x'):
            with self.assertRaises(ValueError):SpeechSettings(enabled=True,provider='openai',voice='fixture',model='fixture',base_url=url)
        with self.assertRaises(ValueError):SpeechSettings(enabled=True,provider='piper',base_url='https://example.com')
        with self.assertRaises(ValueError):SpeechSettings(enabled=True,provider='elevenlabs',model='x',voice='x',speed=2)
        with self.assertRaises(ValueError):speech_form_values({'speech_volume':'loud'})
        with self.assertRaises(ValueError):SpeechSettings.parse({'password':'secret'})

    def test_preset_preserves_playback_and_microphone_protection_preferences(self):
        values={f'speech_{k}':Mock() for k in asdict(SpeechSettings())};key=Mock()
        apply_speech_preset(values,key,'Piper server (offline models)')
        values['speech_provider'].set.assert_called_once_with('piper')
        values['speech_feedback_protection'].set.assert_not_called()
        values['speech_enabled'].set.assert_not_called();values['speech_volume'].set.assert_not_called()
        key.set.assert_called_once_with('')


class SpeechApiTests(unittest.TestCase):
    def test_notices_and_emoji_only_text_never_reach_any_synthesis_engine(self):
        texts=('[Creature is obeying your command]','[Bongo is obeying your command]',
            '[ERROR: Request failed]','[Voice error: Microphone unavailable.]',
            '[Model error: Request failed]','[Voice input ready.]','[Heard: Hello.]','👋🏽 ❤️ 🇭🇺 1️⃣')
        for provider in ('windows','openai','elevenlabs','piper'):
            config=SpeechSettings(enabled=True,provider=provider,model='fixture',voice='fixture',
                base_url='http://localhost:5000' if provider=='piper' else 'https://example.com/v1')
            handler=Mock(side_effect=AssertionError('Notice was sent to provider'))
            service=Synthesizer(config,'unused/settings.json',transport=httpx.MockTransport(handler))
            with patch('bwv.speech_audio.windows_speech',side_effect=AssertionError('Notice sent to Windows')):
                for text in texts:
                    with self.subTest(provider=provider,text=text):
                        self.assertIsNone(service.synthesize(text,threading.Event()))
            handler.assert_not_called();self.assertEqual(service.requests,0)

    def test_provider_receives_only_spoken_words_without_emoji_or_embedded_notices(self):
        seen=[]
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,content=b'\0\0'*24,headers={'content-type':'audio/pcm'})
        self.synth(handler=respond).synthesize('Hello 👋🏽 [ERROR: synthetic failure] friend ❤️.',threading.Event())
        self.assertEqual(seen[0]['input'],'Hello friend .')

    def synth(self,provider='openai',handler=None,**options):
        config=SpeechSettings(enabled=True,provider=provider,model='fixture-model',voice='fixture-voice',**options)
        return Synthesizer(config,Path('unused/settings.json'),environ={'OPENAI_API_KEY':'synthetic'},
            transport=httpx.MockTransport(handler or (lambda request:httpx.Response(200,content=b'\0\0'*24,headers={'content-type':'audio/pcm'}))))

    def test_openai_request_is_plain_text_with_voice_speed_and_no_other_context(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,content=b'\0\0'*24,headers={'content-type':'audio/pcm'})
        service=self.synth(handler=respond,speed=1.1,instructions='Speak gently.')
        pcm,rate,channels=service.synthesize('<speak>Hello $world</speak>',threading.Event())
        body=json.loads(seen[0].content)
        self.assertEqual(body,{'input':'<speak>Hello $world</speak>','voice':'fixture-voice','model':'fixture-model',
            'speed':1.1,'instructions':'Speak gently.','response_format':'pcm'})
        self.assertEqual(seen[0].headers['authorization'],'Bearer synthetic')
        self.assertEqual(seen[0].url.path,'/v1/audio/speech')
        self.assertEqual((rate,channels),(24000,1));self.assertEqual(len(pcm),48)

    def test_piper_local_server_and_elevenlabs_formats(self):
        for provider in ('piper','elevenlabs'):
            seen=[]
            def respond(request):
                seen.append(request)
                return httpx.Response(200,content=wav() if provider=='piper' else b'\0\0'*24,
                    headers={'content-type':'audio/wav' if provider=='piper' else 'audio/pcm'})
            options={'base_url':'http://localhost:5000','speaker_id':2} if provider=='piper' else {}
            service=self.synth(provider,respond,**options)
            self.assertIsNotNone(service.synthesize('Hello.',threading.Event()))
            body=json.loads(seen[0].content)
            if provider=='piper':
                self.assertEqual(seen[0].url.path,'/synthesize')
                self.assertEqual(body,{'text':'Hello.','voice':'fixture-voice','length_scale':1,'speaker_id':2})
                self.assertNotIn('authorization',seen[0].headers)
            else:
                self.assertEqual(body,{'text':'Hello.','model_id':'fixture-model','voice_settings':{'speed':1}})
                self.assertEqual(seen[0].headers['xi-api-key'],'synthetic')
                self.assertEqual(seen[0].url.params['output_format'],'pcm_24000')

    def test_limits_count_failed_attempts_and_never_retry_or_expose_provider_body(self):
        calls=[]
        def respond(request):
            calls.append(request)
            return httpx.Response(429,text='PRIVATE synthetic secret response')
        service=self.synth(handler=respond,max_requests=1)
        with self.assertRaises(SpeechError) as caught:service.synthesize('Hello.',threading.Event())
        self.assertNotIn('PRIVATE',str(caught.exception))
        with self.assertRaisesRegex(SpeechError,'request limit'):service.synthesize('Again.',threading.Event())
        self.assertEqual(len(calls),1)
        service=self.synth(handler=respond,max_characters=2)
        with self.assertRaisesRegex(SpeechError,'character limit'):service.synthesize('Too long',threading.Event())
        self.assertEqual(service.requests,0)

    def test_cancellation_redirect_and_non_audio_responses_do_not_play(self):
        cancelled=threading.Event();cancelled.set()
        service=self.synth();self.assertIsNone(service.synthesize('Hello',cancelled));self.assertEqual(service.requests,0)
        for response in (httpx.Response(302,headers={'location':'https://example.com'}),
                         httpx.Response(200,json={'error':'secret'}),
                         httpx.Response(200,content=wav(),headers={'content-type':'audio/wav'}),
                         httpx.Response(200,content=b'x',headers={'content-type':'audio/pcm'})):
            calls=[]
            def respond(request):calls.append(request);return response
            with self.assertRaises(SpeechError):self.synth(handler=respond).synthesize('Hello',threading.Event())
            self.assertEqual(len(calls),1)


class SpeechAudioTests(unittest.TestCase):
    def test_windows_receives_words_without_emoji_cues(self):
        with patch('bwv.speech_audio.windows_speech',return_value=wav()) as local:
            Synthesizer(SpeechSettings(enabled=True),'unused').synthesize('Hello 👋 friend',threading.Event())
            self.assertEqual(local.call_args.args[0]['text'],'Hello friend')

    def test_windows_uses_plain_data_and_audio_decoder_rejects_corruption(self):
        config=SpeechSettings(enabled=True)
        with patch('bwv.speech_audio.windows_speech',return_value=wav()) as local:
            audio=Synthesizer(config,'unused').synthesize('Hello <break/> $x',threading.Event())
            self.assertEqual(local.call_args.args[0]['text'],'Hello <break/> $x')
            self.assertEqual(audio[1:],(24000,1))
        for data in (b'garbage',wav()[:-2]):
            with self.assertRaises(SpeechError):decode_wav(data)

    def test_player_cancellation_suppresses_late_audio_and_errors(self):
        entered=threading.Event();release=threading.Event();synth=Mock();player=Mock()
        def generate(text,cancelled):entered.set();release.wait(2);return (b'\0\0',24000,1)
        synth.synthesize.side_effect=generate
        service=SpeechPlayer(SpeechSettings(), 'unused',synthesizer=synth,player=player)
        service.start('Hello');self.assertTrue(entered.wait(1));self.assertFalse(service.start('Overlap'))
        service.cancel();release.set();service.thread.join(1)
        player.assert_not_called();self.assertIsNone(service.error)

    def test_output_volume_device_and_cancellation_use_one_stream(self):
        writes=[];cancelled=threading.Event()
        stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
        def write(data):writes.append(data);cancelled.set()
        stream.write.side_effect=write
        sd=SimpleNamespace(RawOutputStream=Mock(return_value=stream))
        with patch.dict('sys.modules',sounddevice=sd):
            play_audio((b'\x10\x00'*4800,24000,1),SpeechSettings(volume=50,output_device='Fixture output'),cancelled)
        self.assertEqual(writes[0],b'\x08\x00'*1200)
        self.assertEqual(sd.RawOutputStream.call_args.kwargs['device'],'Fixture output')
        stream.abort.assert_called_once()

    def test_player_classifies_output_failure_and_clears_it_on_next_success(self):
        synth=Mock();synth.synthesize.return_value=(b'\0\0',24000,1)
        player=Mock(side_effect=[SpeechPlaybackError('Synthetic output failure'),None])
        service=SpeechPlayer(SpeechSettings(),'unused',synthesizer=synth,player=player)
        service.start('First');service.thread.join(1)
        self.assertTrue(service.retryable_error);self.assertIsNotNone(service.error)
        service.start('Second');service.thread.join(1)
        self.assertFalse(service.retryable_error);self.assertIsNone(service.error)
        self.assertEqual(player.call_count,2)
