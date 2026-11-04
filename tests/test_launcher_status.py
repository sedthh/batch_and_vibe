"""Launcher indicators use synthetic settings; no device or provider access."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bwv.launcher_status import configuration_status,runtime_status,short_name
from bwv.llm import LLMConfig
from bwv.voice import VoiceSettings
from bwv.speech import SpeechSettings


class LauncherStatusTests(unittest.TestCase):
    def rows(self,config,**options):
        return configuration_status(config,'unused/settings.json',environ={'OPENAI_API_KEY':'synthetic'},
            available=options.pop('available',lambda _:True),**options)

    def test_configured_models_are_short_and_disabled_features_red(self):
        rows=self.rows(LLMConfig('provider/model-id'))
        self.assertEqual(rows[0],('LLM thinking','model-id','ok'))
        self.assertEqual(rows[1],('Voice Input','disabled','disabled'))
        self.assertEqual(rows[2],('Creature Speech','disabled','disabled'))
        self.assertEqual(short_name(r'C:\example\models\local-voice'),'local-voice')
        self.assertLessEqual(len(short_name('a'*100)),30)

    def test_enabled_audio_shows_models_or_voice_and_known_issues(self):
        config=LLMConfig('fixture',voice=VoiceSettings(enabled=True),speech=SpeechSettings(enabled=True))
        rows=self.rows(config)
        self.assertEqual(rows[1],('Voice Input','base','ok'))
        self.assertEqual(rows[2],('Creature Speech','Windows · default voice','ok'))
        rows=self.rows(config,available=lambda name:name!='faster_whisper')
        self.assertEqual(rows[1][2],'warning');self.assertIn('support missing',rows[1][1])
        rows=self.rows(replace(config,speech=replace(config.speech,volume=0)))
        self.assertEqual(rows[2][2],'warning');self.assertIn('muted',rows[2][1])
        rows=self.rows(replace(config,vr_mode=True,voice=replace(config.voice,auto_send=False)))
        self.assertEqual(rows[1][2],'warning');self.assertIn('VR',rows[1][1])

    def test_missing_key_or_model_warns_without_showing_secrets(self):
        from unittest.mock import patch
        config=LLMConfig('fixture',voice=VoiceSettings(enabled=True,provider='api'),
            speech=SpeechSettings(enabled=True,provider='openai',model='fixture',voice='coral'))
        with patch('bwv.launcher_status.credential_environment',return_value={}):
            rows=self.rows(config)
        self.assertTrue(all(row[2]=='warning' for row in rows))
        self.assertTrue(all('missing API key' in row[1] for row in rows))
        rows=self.rows(replace(config,voice=VoiceSettings(enabled=True,model='')))
        self.assertEqual(rows[1][2],'warning');self.assertIn('choose a model',rows[1][1])

    def test_runtime_toggle_and_provider_error_update_and_recover(self):
        rows=self.rows(LLMConfig('fixture'))
        runtime=SimpleNamespace(automatic=True,is_llm=True,provider=Mock(config=LLMConfig('fixture')))
        runtime.provider.status.return_value={'last_error':None}
        self.assertEqual(runtime_status(rows,runtime),rows)
        runtime.provider.status.return_value={'last_error':'PRIVATE raw message'}
        updated=runtime_status(rows,runtime)
        self.assertEqual(updated[0],('LLM thinking','fixture — provider issue','warning'))
        self.assertNotIn('PRIVATE',str(updated))
        runtime.automatic=False
        self.assertEqual(runtime_status(rows,runtime)[0],('LLM thinking','disabled','disabled'))
        runtime.automatic=True;runtime.is_llm=False
        self.assertEqual(runtime_status(rows,runtime)[0][2],'disabled')
        runtime.is_llm=True;runtime.provider.status.return_value={'last_error':None}
        self.assertEqual(runtime_status(rows,runtime),rows)
