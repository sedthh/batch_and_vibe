"""Installation drops obsolete conversation, never durable memories or settings."""
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bwv.conversation import ConversationMemory
from bwv.memory_store import MemoryStore, clear_installed_history


def saved_memory(version=2):
    # Longer than the runtime's default note budget: cleanup must not trim it.
    notes = 'Remember this exact lesson: café 🐒\n' * 90
    data = {'version':version, 'notes':notes, 'recent_pairs':[
        {'input':{'snapshot':{'obsolete_group':1}}, 'answer':'Old reply',
         'decision':{'candidate':'old_action'}, 'action_status':'no_action'}]}
    if version == 2:
        data['entries'] = [{'id':'memory-existing-id', 'text':notes}]
    return data


class InstallHistoryTests(unittest.TestCase):
    def test_clears_only_history_preserving_complete_notes_ids_and_file_version(self):
        for version in (1, 2):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                path = Path(directory) / 'memory.json'
                before = saved_memory(version)
                path.write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
                self.assertTrue(MemoryStore(path).clear_history())
                self.assertEqual(json.loads(path.read_text('utf-8')), {**before, 'recent_pairs':[]})
                reloaded = ConversationMemory(path=path, memory_chars=10000)
                self.assertEqual(reloaded.notes, before['notes'])
                self.assertEqual(list(reloaded.history), [])
                # Ordinary startup must preserve exchanges created after install.
                reloaded.record({'god_question':'Hello'}, 'Hello', {'candidate':None})
                self.assertEqual(len(ConversationMemory(path=path, memory_chars=10000).history), 1)

    def test_obsolete_exchange_format_can_be_removed_without_losing_valid_notes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.json'
            before = saved_memory()
            before['recent_pairs'] = [{'retired_tool':'inspect_surrounding', 'old_status':'no_longer_supported'}]
            path.write_text(json.dumps(before), encoding='utf-8')
            self.assertIsNone(MemoryStore(path).load())
            self.assertTrue(MemoryStore(path).clear_history())
            self.assertEqual(MemoryStore(path).load(), {**before, 'recent_pairs':[]})

    def test_invalid_memories_are_never_overwritten(self):
        for value in ('not json', {'version':99},
                      {**saved_memory(), 'notes':'Does not match the entries'}):
            with self.subTest(value=type(value).__name__), TemporaryDirectory() as directory:
                path = Path(directory) / 'memory.json'
                path.write_text(value if isinstance(value,str) else json.dumps(value), encoding='utf-8')
                original = path.read_bytes()
                self.assertFalse(MemoryStore(path).clear_history())
                self.assertEqual(path.read_bytes(), original)

    def test_failed_replace_preserves_file_and_cleanup_can_retry(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'memory.json'
            path.write_text(json.dumps(saved_memory()), encoding='utf-8')
            original = path.read_bytes()
            with patch('bwv.memory_store.os.replace', side_effect=OSError('synthetic disk failure')):
                self.assertFalse(MemoryStore(path).clear_history())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])
            self.assertTrue(MemoryStore(path).clear_history())

    def test_only_standard_runtime_stores_are_cleaned_and_missing_stores_are_not_created(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (root/'creature-memory.json', root/'live-llm'/'creature-memory.json',
                     root/'live-dummy'/'dummy-memory.json')
            unrelated = (root/'settings.json', root/'live-llm'/'token-usage.json',
                         root/'custom-session'/'creature-memory.json')
            for path in (*paths, *unrelated):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(saved_memory()), encoding='utf-8')
            untouched = {path:path.read_bytes() for path in unrelated}
            with patch('bwv.memory_store.default_memory_path', return_value=paths[0]):
                self.assertEqual(clear_installed_history(), {'history_cleared':True,'failed_stores':0})
            for path in paths:
                self.assertEqual(MemoryStore(path).load()['recent_pairs'], [])
                self.assertEqual(MemoryStore(path).load()['notes'], saved_memory()['notes'])
            for path, original in untouched.items():
                self.assertEqual(path.read_bytes(), original)
            missing = root/'not-created'/'memory.json'
            with patch('bwv.memory_store.default_memory_path', return_value=missing):
                self.assertTrue(clear_installed_history()['history_cleared'])
            self.assertFalse(missing.parent.exists())

    def test_one_bad_store_reports_failure_without_deleting_it_or_skipping_other_stores(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); legacy = root/'creature-memory.json'
            legacy.write_text('invalid', encoding='utf-8')
            live = root/'live-llm'/'creature-memory.json'
            live.parent.mkdir(); live.write_text(json.dumps(saved_memory()), encoding='utf-8')
            with patch('bwv.memory_store.default_memory_path', return_value=legacy):
                self.assertEqual(clear_installed_history(), {'history_cleared':False,'failed_stores':1})
            self.assertEqual(legacy.read_text('utf-8'), 'invalid')
            self.assertEqual(MemoryStore(live).load()['recent_pairs'], [])


class LauncherInstallHistoryTests(unittest.TestCase):
    def test_legacy_cli_install_cleans_only_after_success_and_reports_cleanup_failure(self):
        from bwv.cli import main
        for variant in ('success', 'install_failed', 'cleanup_failed'):
            with self.subTest(variant=variant), ExitStack() as stack:
                stack.enter_context(patch('sys.argv', ['bwv','install-observer','synthetic-game',
                                                      'synthetic.dll','--experimental']))
                stack.enter_context(patch('bwv.install.install', return_value={'state':'installed'},
                    side_effect=ValueError('synthetic install failure') if variant=='install_failed' else None))
                cleanup = stack.enter_context(patch('bwv.memory_store.clear_installed_history',
                    return_value={'history_cleared':variant!='cleanup_failed',
                                  'failed_stores':int(variant=='cleanup_failed')}))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                errors = stack.enter_context(redirect_stderr(io.StringIO()))
                self.assertEqual(main(), 0 if variant=='success' else 1)
                self.assertEqual(cleanup.call_count, int(variant!='install_failed'))
                if variant=='success': self.assertTrue(json.loads(output.getvalue())['history_cleared'])
                if variant=='cleanup_failed': self.assertIn('bridge is installed', errors.getvalue())

    def test_reinstall_even_with_same_dll_cleans_after_verification_before_popup(self):
        from bwv.launcher import main
        for variant in ('changed', 'unchanged', 'install_failed', 'verify_failed', 'cleanup_failed', 'running', 'uninstall'):
            with self.subTest(variant=variant), TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                config = root/'settings.json'; config.write_text('{}', encoding='utf-8')
                bridge = root/'bridge.dll'; bridge.write_bytes(b'synthetic')
                events = []
                def install(*_):
                    events.append('install')
                    if variant == 'install_failed': raise OSError('synthetic install failure')
                    return {'installed':True, 'changed':variant=='changed'}
                def verify(*_):
                    events.append('verify')
                    if variant == 'verify_failed': raise ValueError('synthetic verification failure')
                def cleanup():
                    events.append('cleanup')
                    return {'history_cleared':variant!='cleanup_failed', 'failed_stores':int(variant=='cleanup_failed')}
                stack.enter_context(patch('bwv.launcher.ensure_admin', return_value=True))
                stack.enter_context(patch('bwv.launcher.instance_lock', return_value=nullcontext()))
                stack.enter_context(patch('bwv.launcher.choose_game', return_value=root))
                stack.enter_context(patch('bwv.launcher.game_processes', return_value=[object()] if variant=='running' else []))
                stack.enter_context(patch('bwv.install.update', side_effect=install))
                stack.enter_context(patch('bwv.install.uninstall', side_effect=lambda *_:events.append('uninstall')))
                stack.enter_context(patch('bwv.launcher.check_installed', side_effect=verify))
                stack.enter_context(patch('bwv.memory_store.clear_installed_history', side_effect=cleanup))
                stack.enter_context(patch('bwv.launcher.show_install_success', side_effect=lambda **_:events.append('popup')))
                stack.enter_context(redirect_stdout(io.StringIO()))
                errors = stack.enter_context(redirect_stderr(io.StringIO()))
                code = main(['--uninstall' if variant=='uninstall' else '--install',
                             '--config',str(config),'--bridge',str(bridge)])
                if variant in ('changed','unchanged'):
                    self.assertEqual(code, 0)
                    self.assertEqual(events, ['install','verify','cleanup','popup'])
                elif variant == 'uninstall':
                    self.assertEqual(code, 0); self.assertEqual(events, ['uninstall','popup'])
                else:
                    self.assertEqual(code, 1)
                    self.assertNotIn('popup', events)
                    if variant != 'cleanup_failed': self.assertNotIn('cleanup', events)
                    else: self.assertIn('bridge is installed', errors.getvalue())


if __name__ == '__main__': unittest.main()
