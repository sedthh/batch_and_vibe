"""Private settings fixtures contain only invented configuration and names."""
from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bwv.creature import CreatureContext
from bwv.llm import LLMConfig, LLMProvider, init_settings, load_settings
from bwv.profile_names import ProfileNames
from bwv.prompt import ACTION_CONTRACT, STATE_CONTRACT, preview_prompt
from bwv.prompt_settings import PromptSettings
from bwv.bridge_prompt import BRIDGE_GUIDANCE, MEMORY_GUIDANCE, SPEECH_GUIDANCE
from bwv.conversation import reply_contract
from bwv.qa_live import LiveDummyRuntime
from test_control import playable


class SettingsTests(unittest.TestCase):
    def test_optional_message_cap_migrates_legacy_config_without_rewriting_on_load(self):
        self.assertIsNone(LLMConfig.defaults().max_messages)
        for value in (True,0,-1,1.5,'100'):
            with self.assertRaises(ValueError):LLMConfig('fixture',max_messages=value)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            for fields,expected in (({},None),({'max_messages':None},None),
                    ({'max_messages':40},40),({'max_requests':100},100),
                    ({'max_requests':100,'max_messages':None},None)):
                raw=json.dumps({'model':'fixture',**fields});path.write_text(raw)
                config=LLMConfig.load(path)
                self.assertEqual(config.max_messages,expected)
                self.assertEqual(path.read_text(),raw)
                config.save(path)
                saved=json.loads(path.read_text())
                self.assertNotIn('max_requests',saved)
                self.assertEqual(saved['max_messages'],expected)

    def test_history_limits_default_override_and_validation(self):
        config=LLMConfig.defaults()
        self.assertEqual((config.history_pairs,config.history_chars),(12,24000))
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text('{"model":"fixture","history_pairs":16,"history_chars":32000}')
            loaded=LLMConfig.load(path)
            self.assertEqual((loaded.history_pairs,loaded.history_chars),(16,32000))
        for values in ({'history_pairs':True},{'history_pairs':0},{'history_pairs':101},
                       {'history_chars':999},{'history_chars':256001}):
            with self.assertRaises(ValueError):LLMConfig('fixture',**values)

    def test_custom_general_prompt_cannot_remove_fixed_contracts(self):
        custom=PromptSettings(general='Be a quiet cartoon cow. Literal {braces}.')
        system=preview_prompt(playable(),None,settings=custom).system
        self.assertIn(custom.general,system)
        self.assertIn(BRIDGE_GUIDANCE,system)
        self.assertIn(MEMORY_GUIDANCE,reply_contract(custom))
        self.assertIn(SPEECH_GUIDANCE,reply_contract(custom))
        self.assertEqual(reply_contract(custom),reply_contract())
        self.assertNotIn('miracle_attempts_available',PromptSettings().general)
        self.assertNotIn('update_memory',PromptSettings().general)

    def test_legacy_migration_preserves_prose_but_discards_editable_bridge_overrides(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            raw=json.dumps({'model':'fixture','prompts':{
                'role_and_world':'Be a curious fox.',
                'game_context':'A world of talking animals.',
                'memory':'Invented obsolete memory override.',
                'observation_guidance':'Invented obsolete observation override.'}})
            path.write_text(raw)
            with self.assertWarnsRegex(UserWarning,'migrated'):
                config=LLMConfig.load(path)
            self.assertIn('Be a curious fox.',config.prompts.general)
            self.assertIn('A world of talking animals.',config.prompts.general)
            self.assertNotIn('obsolete',config.prompts.general)
            self.assertEqual(path.read_text(),raw)
            config.save(path)
            self.assertEqual(set(json.loads(path.read_text())['prompts']),{'general'})
            self.assertEqual(LLMConfig.load(path),config)

    def test_fresh_start_uses_packaged_defaults_without_writing_private_files(self):
        with TemporaryDirectory() as directory:
            destination=Path(directory)/'settings.json'
            with patch('bwv.llm.DEFAULT_SETTINGS_PATH',destination):
                config,path=load_settings()
            self.assertEqual(path,destination)
            self.assertEqual(config,LLMConfig.defaults())
            self.assertEqual(config.cadence.active_seconds,30)
            self.assertFalse(destination.exists())
            with self.assertRaises(ValueError):load_settings(destination)
            with self.assertRaisesRegex(ValueError,'Choose a model'):
                LLMProvider(config,environ={})

    def test_sparse_private_overrides_merge_sections_and_preserve_original_file(self):
        with TemporaryDirectory() as directory:
            destination=Path(directory)/'settings.local.json'
            content=json.dumps({'api':'chat_completions','model':'local-test',
                'base_url':'http://127.0.0.1:1234/v1','api_key_env':None,
                'cadence':{'jitter_percent':7},'prompts':{'general':'Explore gently.'},
                'max_tool_rounds':0})
            destination.write_text(content)
            config,path=load_settings(destination)
            self.assertEqual(config.cadence.jitter_percent,7)
            self.assertEqual(config.cadence.quiet_seconds,120)
            self.assertEqual(config.prompts.general,'Explore gently.')
            self.assertIsNone(config.api_key_env)
            self.assertEqual(destination.read_text(),content)
            self.assertEqual(path,destination)
            destination.write_text('{invalid')
            with patch('bwv.llm.DEFAULT_SETTINGS_PATH',destination),self.assertRaises(ValueError):load_settings()
            self.assertEqual(destination.read_text(),'{invalid')

    def test_runtime_writers_never_replace_distributable_defaults(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.default.json'
            path.write_text('protected')
            with self.assertRaises(ValueError):LLMConfig.defaults().save(path)
            with self.assertRaises(ValueError):load_settings(path)
            self.assertEqual(path.read_text(),'protected')

    def test_malformed_inspector_http_is_a_recoverable_read_failure(self):
        from http.client import BadStatusLine
        from bwv.qa_live import InspectorSource
        class BrokenOpener:
            def open(self,*args,**kwargs):raise BadStatusLine('private fixture')
        source=InspectorSource('http://127.0.0.1:1234/'+'a'*32+'/')
        source.opener=BrokenOpener()
        with self.assertRaisesRegex(ValueError,'^Invalid inspector HTTP response$'):source.read()

    def test_init_round_trip_has_all_editable_sections_but_no_key_or_live_data(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.local.json'
            result=init_settings(path)
            self.assertEqual(result['provider_calls'],0)
            data=json.loads(path.read_text())
            self.assertEqual(set(data['prompts']),{'general'})
            self.assertNotIn('api_key',data)
            self.assertEqual(data['api_key_env'],'OPENAI_API_KEY')
            self.assertNotIn(directory,path.read_text())
            self.assertNotIn('god_player_name',path.read_text())
            self.assertEqual(LLMConfig.load(path),LLMConfig('YOUR_MODEL_ID'))
            before=path.read_bytes()
            with self.assertRaises(ValueError):init_settings(path)
            self.assertEqual(path.read_bytes(),before)

    def test_prompt_prose_is_literal_and_protocol_and_dynamic_species_stay_generated(self):
        settings=PromptSettings(general='My literal {species} label. $not_code')
        monkey=replace(playable(),creature=CreatureContext('monkey','engine'))
        first=preview_prompt(monkey,None,settings=settings).system
        self.assertIn('My literal {species} label. $not_code',first)
        self.assertIn(ACTION_CONTRACT,first)
        self.assertIn(STATE_CONTRACT,first)
        self.assertIn('"species":"monkey"',first)
        horse=replace(monkey,creature=CreatureContext('horse','engine'))
        second=preview_prompt(horse,None,settings=settings).system
        self.assertIn('"species":"horse"',second)
        self.assertNotIn('"species":"monkey"',second)
        self.assertFalse(any('"candidate"' in s for s in asdict(settings).values()))

    def test_unknown_fields_invalid_sections_and_secret_fields_rejected_without_echo(self):
        for prompts in ({'wire_contract':'invented'}, {'general':''},{'general':23},
                        {'general':'x'*20001},{'general':'\0invalid'}):
            with self.assertRaises(ValueError):LLMConfig('synthetic',prompts=prompts)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.local.json'
            path.write_text(json.dumps({'model':'synthetic','api_key':'synthetic-secret'}))
            with self.assertRaises(ValueError) as caught:LLMConfig.load(path)
            self.assertNotIn('synthetic-secret',str(caught.exception))
            self.assertNotIn(directory,str(caught.exception))


class SharedRuntimeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_applies_history_limits_and_durable_memory_guidance(self):
        with TemporaryDirectory() as directory:
            settings=LLMConfig('fixture',history_pairs=14,history_chars=28000)
            runtime=LiveDummyRuntime(directory,settings=settings,names_reader=ProfileNames)
            try:
                self.assertEqual(runtime.memory.pairs,14)
                self.assertEqual(runtime.memory.history_chars,28000)
                self.assertIn('Proactively save important interactions',reply_contract(settings.prompts))
                self.assertIn('one-off instruction',reply_contract(settings.prompts))
            finally:await runtime.close()

    async def test_first_timing_edit_creates_only_the_private_settings_file(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            with patch('bwv.llm.DEFAULT_SETTINGS_PATH',path):settings,destination=load_settings()
            runtime=LiveDummyRuntime(Path(directory)/'state',names_reader=ProfileNames,
                settings=settings,settings_path=destination)
            try:
                self.assertFalse(path.exists())
                await runtime.configure({'cadence':{'jitter_percent':5}})
                self.assertEqual(LLMConfig.load(path).cadence.jitter_percent,5)
                self.assertEqual(LLMConfig.defaults().cadence.jitter_percent,20)
                self.assertEqual(runtime.payload()['runtime']['external_model_calls'],0)
            finally:await runtime.close()

    async def test_dummy_uses_shared_prompt_and_timing_save_preserves_unloaded_edits(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.local.json'
            settings=LLMConfig('synthetic',prompts={'general':'Be a patient creature. Use short friendly answers. Remember lessons with evidence.'})
            settings.save(path,create=True)
            state=Path(directory)/'state'
            state.mkdir()
            # A prior per-service override must not silently shadow shared settings.
            (state/'config.local.json').write_text('{"active_seconds":15}')
            runtime=LiveDummyRuntime(state,clock=lambda:100,names_reader=lambda:ProfileNames('Guide','Fern'),
                                     settings=settings,settings_path=path)
            runtime.gate.resume_delay=0
            try:
                obs=asdict(replace(playable(100),creature=CreatureContext('horse','engine')))
                frame={'observation':obs,'diagnostics':{'script_camera_owned':False},
                    'prompt':{'system':'Old observer prompt should not override settings',
                              'context':{'surroundings':{'trees_nearby':4}}}}
                runtime.ingest(frame)
                await runtime.question({'question':'status'})
                actual=runtime.provider.requests[-1].messages[0]['content']
                for part in ('Be a patient creature.','Use short friendly answers.',
                             'Remember lessons with evidence.','"species":"horse"','"creature_name":"Fern"'):
                    self.assertIn(part,actual)
                self.assertNotIn('Old observer prompt',actual)
                self.assertEqual(runtime.cadence.active_seconds,30)
                changed=replace(settings,model='different-model',prompts=PromptSettings(general='New disk notes.'))
                changed.save(path)
                await runtime.configure({'cadence':{'active_seconds':25,'jitter_percent':10}})
                loaded=LLMConfig.load(path)
                self.assertEqual(loaded.model,'different-model')
                self.assertEqual(loaded.prompts.general,'New disk notes.')
                self.assertEqual(loaded.cadence.active_seconds,25)
                self.assertIn('Remember lessons with evidence.',runtime.prompt_settings.general)
                payload=runtime.payload()
                self.assertEqual(payload['runtime']['settings_scope'],'shared_file')
                self.assertNotIn(directory,json.dumps(payload))
                self.assertNotIn('base_url',payload['runtime'])
                self.assertNotIn('api_key_env',payload['runtime'])
            finally:await runtime.close()
            restarted=LiveDummyRuntime(state,clock=lambda:100,names_reader=ProfileNames,
                                      settings=LLMConfig.load(path),settings_path=path)
            try:
                self.assertEqual(restarted.prompt_settings.general,'New disk notes.')
                self.assertEqual(restarted.cadence.jitter_percent,10)
            finally:await restarted.close()

    async def test_invalid_disk_settings_are_preserved_and_live_timing_unchanged(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'settings.local.json'
            settings=LLMConfig('synthetic')
            settings.save(path,create=True)
            runtime=LiveDummyRuntime(Path(directory)/'state',names_reader=ProfileNames,
                                     settings=settings,settings_path=path)
            try:
                path.write_text('invalid edit')
                with self.assertRaises(ValueError):
                    await runtime.configure({'cadence':{'jitter_percent':5}})
                self.assertEqual(runtime.cadence.jitter_percent,20)
                self.assertEqual(path.read_text(),'invalid edit')
            finally:await runtime.close()
