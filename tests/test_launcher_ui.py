"""Invented settings and services only; never reads user credentials or the game."""
import asyncio
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from bwv.credentials import credential_environment
from bwv.launcher import arguments, main
from bwv.launcher_lifecycle import run_managed
from bwv.launcher_ui import save_basics, apply_provider_preset, StatusWindow
from bwv.llm import LLMConfig


def form(config):
    data = asdict(config)
    data.update(data.pop('cadence'))
    return {k: v if isinstance(v,bool) else '' if v is None else str(v) for k, v in data.items()}


class SettingsFormTests(unittest.TestCase):
    def test_message_cap_form_accepts_blank_and_positive_whole_numbers(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';config=LLMConfig('fixture');config.save(path)
            values=form(config)
            for value,expected in (('25',25),('',None),('  ',None)):
                values['max_messages']=value
                self.assertEqual(save_basics(path,values).max_messages,expected)
                self.assertEqual(LLMConfig.load(path).max_messages,expected)
            for value in ('0','-1','1.5','unlimited'):
                values['max_messages']=value
                with self.assertRaises(ValueError):save_basics(path,values)

    def test_general_prompt_form_round_trip_and_invalid_edit_does_not_save(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            config=LLMConfig('fixture'); config.save(path)
            values=form(config)
            values['general_prompt']=('## Character\n\nMy cartoon cow. {player} ${literal} %s \\n\n'
                '"Quotes", apostrophes: \' ’. Árvíz 日本語 🐄 👩‍🌾 e\u0301\n\n'
                '## Speaking style\n\n**British humour**\n\tKeep <tags> & `code` literal.\n')
            saved=save_basics(path,values)
            self.assertEqual(saved.prompts.general,values['general_prompt'])
            self.assertEqual(LLMConfig.load(path).prompts,saved.prompts)
            before=path.read_bytes()
            for invalid in (' ', 'damaged \ud800 character', 'damaged \udfff', 'control \x00'):
                values['general_prompt']=invalid
                with self.assertRaisesRegex(ValueError,'General prompt'):
                    save_basics(path,values,'fixture-secret')
                self.assertEqual(path.read_bytes(),before)
                self.assertFalse((path.parent/'.local'/'.env').exists())

    def test_switching_provider_clears_draft_secret_and_preserves_other_settings(self):
        import tkinter as tk
        interpreter = tk.Tcl()
        config = LLMConfig('fixture-model', reasoning_effort='low')
        values = {k: (tk.BooleanVar if isinstance(v,bool) else tk.StringVar)(interpreter, value=v)
                  for k, v in form(config).items()}
        key = tk.StringVar(interpreter, value='fixture-unsaved-key')
        apply_provider_preset(values, key, 'Claude (compatibility API)')
        self.assertEqual(key.get(), '')
        self.assertEqual(values['model'].get(), '')
        self.assertEqual(values['reasoning_effort'].get(), '')
        self.assertEqual(values['max_messages'].get(), '')
        self.assertEqual(values['api_key_env'].get(), 'ANTHROPIC_API_KEY')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'; config.save(path)
            env = path.parent / '.local' / '.env'; env.parent.mkdir()
            env.write_text('OPENAI_API_KEY=fixture-existing-key\n')
            values['model'].set('fixture-claude')
            saved = save_basics(path, {k: v.get() for k, v in values.items()}, 'fixture-claude-key')
            self.assertEqual(saved.api, 'chat_completions')
            self.assertEqual(saved.base_url, 'https://api.anthropic.com/v1')
            self.assertEqual(saved.token_parameter, 'max_tokens')
            self.assertIsNone(saved.reasoning_effort)
            self.assertTrue(saved.reenable_cut_content)
            self.assertIn('OPENAI_API_KEY=fixture-existing-key', env.read_text())
            self.assertIn('ANTHROPIC_API_KEY=fixture-claude-key', env.read_text())
            self.assertNotIn('fixture-claude-key', path.read_text())

    def test_key_write_failure_is_explicit_and_does_not_destroy_old_key(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('fixture'); config.save(path)
            env = path.parent / '.local' / '.env'; env.parent.mkdir()
            env.write_text('OPENAI_API_KEY=fixture-old\n')
            original_replace = Path.replace
            def replace(source, destination):
                if destination == env:
                    raise PermissionError('synthetic failure')
                return original_replace(source, destination)
            with patch.object(Path, 'replace', replace), self.assertRaisesRegex(ValueError, 'Settings were saved, but the API key'):
                save_basics(path, form(config), 'fixture-new')
            self.assertEqual(env.read_text(), 'OPENAI_API_KEY=fixture-old\n')
            self.assertEqual(list(env.parent.glob('.env.*.tmp')), [])

    def test_merges_advanced_settings_and_stores_key_only_in_private_env(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('fixture', prompts={'general': 'Fixture instructions.'}, wake_on_question=True)
            config.save(path)
            env = path.parent / '.local' / '.env'
            env.parent.mkdir()
            env.write_text('# Keep this comment\nOTHER_KEY=fixture-other\nexport OPENAI_API_KEY=fixture-old\n')
            values = form(config); values.update(model='fixture-next', active_seconds='30')
            saved = save_basics(path, values, 'fixture-new-secret')
            self.assertEqual(saved.prompts.general, 'Fixture instructions.')
            self.assertTrue(saved.wake_on_question)
            self.assertEqual(saved.cadence.active_seconds, 30)
            self.assertNotIn('fixture-new-secret', path.read_text())
            self.assertEqual(credential_environment('OPENAI_API_KEY', env, environ={}),
                             {'OPENAI_API_KEY': 'fixture-new-secret'})
            self.assertIn('OTHER_KEY=fixture-other', env.read_text())
            self.assertIn('# Keep this comment', env.read_text())
            before = env.read_bytes()
            save_basics(path, values, '')
            self.assertEqual(env.read_bytes(), before)

    def test_local_model_needs_no_key_and_invalid_form_preserves_both_files(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            config = LLMConfig('fixture'); config.save(path)
            values = form(config)
            values.update(api='chat_completions', base_url='http://127.0.0.1:1234/v1', api_key_env='')
            self.assertIsNone(save_basics(path, values).api_key_env)
            self.assertFalse((path.parent / '.local' / '.env').exists())
            before = path.read_bytes()
            values['active_seconds'] = '900'
            with self.assertRaises(ValueError): save_basics(path, values, 'fixture-secret')
            self.assertEqual(before, path.read_bytes())
            values['active_seconds'] = '25'
            values['api_key_env'] = 'OPENAI_API_KEY'
            with self.assertRaises(ValueError): save_basics(path, values, 'fixture\nOTHER=bad')
            self.assertEqual(before, path.read_bytes())

    def test_configure_does_not_elevate_or_start_provider(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'; LLMConfig.defaults().save(path)
            with patch('bwv.launcher.ensure_admin', side_effect=AssertionError('Unexpected UAC')), \
                 patch('bwv.launcher.prepare_provider', side_effect=AssertionError('Unexpected provider')), \
                 patch('bwv.launcher_ui.SettingsWindow') as window:
                self.assertEqual(main(['--configure', '--config', str(path)]), 0)
                window.return_value.root.mainloop.assert_called_once()
        self.assertFalse(arguments([]).no_window)


class LaunchButtonTests(unittest.TestCase):
    def test_missing_install_disables_both_buttons_without_repeating_error(self):
        window=StatusWindow.__new__(StatusWindow)
        window.stopping=False;window.write=Mock();window.game_buttons=[Mock(),Mock()]
        window.game_launcher=Mock(busy=False,installation_error='Game not found.')
        window.refresh_game_buttons();window.refresh_game_buttons()
        window.write.assert_called_once_with('ERROR: Game not found.')
        for button in window.game_buttons:button.configure.assert_called_with(state='disabled')
        window.game_launcher.installation_error=None;window.refresh_game_buttons()
        for button in window.game_buttons:button.configure.assert_called_with(state='normal')
        window.write.assert_called_with('Game installation is available again.')

    def test_both_buttons_disable_before_spawn_and_recover_after_exit_or_failure(self):
        window=StatusWindow.__new__(StatusWindow)
        window.stopping=False;window.root=Mock();window.size=Mock();window.write=Mock()
        window.game_buttons=[Mock(),Mock()];window.game_launcher=Mock(busy=False,installation_error=None,vr_mode=False)
        def start(*args):
            for button in window.game_buttons:
                self.assertEqual(button.configure.call_args.kwargs,{'state':'disabled'})
            window.game_launcher.busy=True
            return 'Starting'
        window.game_launcher.start.side_effect=start
        window.start_game('windowed')
        for button in window.game_buttons:
            self.assertEqual(button.configure.call_args.kwargs,{'state':'disabled'})
        window.game_launcher.busy=False
        window.refresh_game_buttons()
        for button in window.game_buttons:
            self.assertEqual(button.configure.call_args.kwargs,{'state':'normal'})
        window.game_launcher.start.side_effect=OSError('fixture launch failed')
        window.start_game('borderless')
        window.write.assert_called_with('ERROR: fixture launch failed')
        for button in window.game_buttons:
            self.assertEqual(button.configure.call_args.kwargs,{'state':'normal'})
        window.stopping=True;window.refresh_game_buttons()
        for button in window.game_buttons:
            self.assertEqual(button.configure.call_args.kwargs,{'state':'disabled'})


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_close_waits_for_cleanup_and_does_not_cancel_twice(self):
        cleaned = []
        class Window:
            def __init__(self, path, stop): self.stop = stop
            def write(self, *args): pass
            def pump(self): self.stop()  # repeated close/stop clicks
            def close(self): cleaned.append('window')
        async def service(emit):
            try: await asyncio.Future()
            finally:
                await asyncio.sleep(.1)
                cleaned.append('services')
        with patch('bwv.launcher_ui.StatusWindow', Window), \
             patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()):
            await run_managed(service, Path('fixture'))
        self.assertEqual(cleaned, ['services', 'window'])

    async def test_console_close_uses_same_cleanup_without_a_window(self):
        cleaned = []
        def controls(stop):
            asyncio.get_running_loop().call_later(.05, stop)
            return nullcontext()
        async def service(emit):
            try: await asyncio.Future()
            finally: cleaned.append('services')
        with patch('bwv.launcher_lifecycle.console_shutdown', controls), \
             patch('bwv.launcher_ui.StatusWindow', side_effect=AssertionError('Unexpected UI')):
            await run_managed(service, Path('fixture'), window=False)
        self.assertEqual(cleaned, ['services'])

    async def test_fatal_service_error_is_not_swallowed(self):
        async def service(emit): raise RuntimeError('fixture failure')
        with patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()), \
             self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            await run_managed(service, Path('fixture'), window=False)
