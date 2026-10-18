"""Settings saves restart synthetic services, never the game or a paid provider."""
import asyncio
from contextlib import ExitStack, nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bwv.launcher_lifecycle import run_managed
from bwv.llm import LLMConfig


class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_restarts_serially_preserving_memory_history_and_debug_choices(self):
        from bwv.qa_live import LiveDummyRuntime
        events, windows, runtimes = [], [], []
        selected = [LLMConfig('fixture-old')]
        debug = SimpleNamespace(enabled=True, port=12345)
        case = self

        class Window:
            debug_task = None
            def __init__(self, path, stop):
                self.stop, self.restarting = stop, False
                self.requested = False
                windows.append(self)
            def write(self, *args): pass
            def set_debug_session(self, session): pass
            def set_restarting(self, value): self.restarting = value
            def pump(self):
                if len(runtimes) == 1 and not self.requested:
                    self.requested = True
                    self.restart(LLMConfig('fixture-new'))
                    with case.assertRaisesRegex(ValueError, 'already'):
                        self.restart(LLMConfig('duplicate'))
                elif len(runtimes) == 2:
                    self.stop()
                elif self.restarting:
                    events.append('responsive during cleanup')
            def close(self): events.append('window closed')

        with TemporaryDirectory() as folder:
            async def service(emit):
                model = selected[0].model
                events.append('start ' + model)
                self.assertTrue(debug.enabled)
                self.assertEqual(debug.port, 12345)
                runtime = LiveDummyRuntime(state_dir=folder, settings=selected[0])
                runtimes.append(runtime)
                if len(runtimes) == 1:
                    runtime.memory.record({'god_question': 'fixture question'}, 'fixture answer',
                                          {'candidate': None}, 'fixture memory')
                else:
                    self.assertEqual(runtime.memory.notes, 'fixture memory')
                    self.assertEqual(runtime.memory.history[-1]['answer'], 'fixture answer')
                    self.assertIn('cleaned fixture-old', events)
                try:
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(.12)
                    await runtime.close()
                    debug.enabled = False  # DebugSession.close resets this.
                    events.append('cleaned ' + model)

            with patch('bwv.launcher_ui.StatusWindow', Window), \
                 patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()), \
                 redirect_stdout(StringIO()):
                await asyncio.wait_for(run_managed(service, Path(folder) / 'settings.json',
                    debug_session=debug, apply_settings=lambda config: selected.__setitem__(0, config)), 5)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(runtimes), 2)
        self.assertIn('responsive during cleanup', events)
        self.assertEqual(events[-2:], ['cleaned fixture-new', 'window closed'])

    async def test_stop_during_restart_cleanup_does_not_start_replacement_or_cancel_twice(self):
        events = []

        class Window:
            debug_task = None
            def __init__(self, path, stop): self.stop, self.restarting = stop, False
            def write(self, *args): pass
            def set_restarting(self, value): self.restarting = value
            def pump(self):
                if self.restarting: self.stop()
                elif events: self.restart(LLMConfig('fixture-new'))
            def close(self): events.append('window closed')

        async def service(emit):
            events.append('started')
            try: await asyncio.Future()
            finally:
                await asyncio.sleep(.15)
                events.append('cleaned')

        with patch('bwv.launcher_ui.StatusWindow', Window), \
             patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()), \
             redirect_stdout(StringIO()):
            await asyncio.wait_for(run_managed(service, Path('fixture'), apply_settings=Mock()), 5)
        self.assertEqual(events, ['started', 'cleaned', 'window closed'])

    async def test_failed_validation_keeps_current_service_running(self):
        events = []
        case = self

        class Window:
            debug_task = None
            def __init__(self, path, stop): self.stop, self.attempted = stop, False
            def write(self, *args): pass
            def set_restarting(self, value): case.fail('Invalid settings must not start a restart')
            def pump(self):
                if events and not self.attempted:
                    self.attempted = True
                    with case.assertRaisesRegex(ValueError, 'missing key'):
                        self.restart(LLMConfig('fixture-new'))
                    events.append('validation rejected')
                elif self.attempted:
                    case.assertNotIn('cleaned', events)
                    self.stop()
            def close(self): pass

        async def service(emit):
            events.append('started')
            try: await asyncio.Future()
            finally: events.append('cleaned')

        with patch('bwv.launcher_ui.StatusWindow', Window), \
             patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()), \
             redirect_stdout(StringIO()):
            await asyncio.wait_for(run_managed(service, Path('fixture'),
                apply_settings=Mock(side_effect=ValueError('missing key'))), 5)
        self.assertEqual(events, ['started', 'validation rejected', 'cleaned'])

    async def test_restart_waits_for_pending_debug_change_before_preserving_selection(self):
        started = []
        debug = SimpleNamespace(enabled=False, port=0)

        class Window:
            debug_task = None
            def __init__(self, path, stop): self.stop, self.requested = stop, False
            def write(self, *args): pass
            def set_debug_session(self, session): pass
            def set_restarting(self, value): pass
            def pump(self):
                if len(started) == 1 and not self.requested:
                    self.requested = True
                    async def change_debug():
                        await asyncio.sleep(.1)
                        debug.enabled, debug.port = True, 23456
                        self.debug_task = None
                    self.debug_task = asyncio.create_task(change_debug())
                    self.restart(LLMConfig('fixture-new'))
                elif len(started) == 2: self.stop()
            def close(self): pass

        async def service(emit):
            started.append((debug.enabled, debug.port))
            try: await asyncio.Future()
            finally: debug.enabled = False

        with patch('bwv.launcher_ui.StatusWindow', Window), \
             patch('bwv.launcher_lifecycle.console_shutdown', lambda stop: nullcontext()), \
             redirect_stdout(StringIO()):
            await asyncio.wait_for(run_managed(service, Path('fixture'), debug_session=debug,
                                               apply_settings=Mock()), 5)
        self.assertEqual(started, [(False, 0), (True, 23456)])


class LauncherApplyTests(unittest.TestCase):
    def test_next_service_uses_saved_settings_and_retains_runtime_toggles_without_reinstall(self):
        from bwv.launcher import main
        with TemporaryDirectory() as folder, ExitStack() as stack:
            path = Path(folder) / 'settings.json'
            LLMConfig('fixture-old', api_key_env=None, base_url='http://127.0.0.1:1234/v1').save(path)
            updated = LLMConfig('fixture-new', api_key_env=None, base_url='http://127.0.0.1:1234/v1',
                                prompts={'general': 'new fixture prompt'})
            admin = stack.enter_context(patch('bwv.launcher.ensure_admin', return_value=True))
            stack.enter_context(patch('bwv.launcher.instance_lock', return_value=nullcontext()))
            stack.enter_context(patch('bwv.launcher.choose_game', return_value=Path('fixture-game')))
            installed = stack.enter_context(patch('bwv.launcher.ensure_installed'))
            stack.enter_context(patch('bwv.game_display.GameLauncher'))
            debug = SimpleNamespace(runtime=SimpleNamespace(automatic=False))
            stack.enter_context(patch('bwv.debug_session.DebugSession', return_value=debug))
            play = stack.enter_context(patch('bwv.supervisor.play', new_callable=AsyncMock))
            stack.enter_context(redirect_stdout(StringIO()))

            async def managed(factory, path, *, apply_settings, **kwargs):
                await factory(Mock())
                apply_settings(updated)
                await factory(Mock())

            stack.enter_context(patch('bwv.launcher_lifecycle.run_managed', side_effect=managed))
            self.assertEqual(main(['--config', str(path), '--no-actions', '--no-overlay']), 0)
            admin.assert_called_once()
            installed.assert_called_once()
            first, second = play.call_args_list
            self.assertEqual(first.args[1].model, 'fixture-old')
            self.assertEqual(second.args[1], updated)
            self.assertFalse(second.kwargs['automatic'])
            self.assertFalse(second.kwargs['actions'])
            self.assertFalse(second.kwargs['overlay'])
