"""Synthetic launcher lifecycle tests; no game, elevation or provider traffic."""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bwv.launcher import arguments, choose_game, prepare_provider
from bwv.llm import LLMConfig
from bwv.processes import GameProcess, select_game
from bwv.supervisor import Notices, ObserverSupervisor, chat_error


class LauncherTests(unittest.TestCase):
    def test_missing_bridge_autoinstalls_then_verifies_before_success_popup(self):
        from bwv.launcher import ensure_installed
        from contextlib import ExitStack
        report = {'status':'experimental','abi_candidate':True,'installed':False}
        for variant in ('success','running','no_build','bad_verification','existing'):
            with self.subTest(variant=variant), ExitStack() as stack:
                stack.enter_context(patch('bwv.launcher.inspect_install',return_value={**report,'installed':variant=='existing'}))
                stack.enter_context(patch('bwv.launcher.game_processes',return_value=[object()] if variant=='running' else []))
                installer=stack.enter_context(patch('bwv.install.update'))
                verify=stack.enter_context(patch('bwv.launcher.check_installed',side_effect=ValueError('fixture') if variant=='bad_verification' else None))
                popup=stack.enter_context(patch('bwv.launcher.show_install_success'))
                stack.enter_context(redirect_stdout(io.StringIO()))
                bridge=Mock();bridge.is_file.return_value=variant!='no_build'
                if variant in ('running','no_build','bad_verification'):
                    with self.assertRaises((RuntimeError,ValueError)):
                        ensure_installed(Path('synthetic-game'),bridge)
                else:ensure_installed(Path('synthetic-game'),bridge)
                self.assertEqual(installer.call_count,int(variant in ('success','bad_verification')))
                self.assertEqual(popup.call_count,int(variant=='success'))
                if variant=='success':popup.assert_called_once_with(continuing=True)
                if variant=='existing':verify.assert_called_once()

    def test_one_debug_port_is_validated_before_startup(self):
        self.assertEqual(arguments(['--debug','--port','8080']).port,8080)
        from contextlib import redirect_stderr
        for argv in (['--port','8080'],['--debug','--port','0'],['--debug','--port','65536']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(argv)

    def test_configured_provider_is_default_and_dummy_is_explicit(self):
        self.assertFalse(arguments([]).dummy)
        self.assertTrue(arguments(['--dummy']).dummy)
        self.assertFalse(arguments([]).debug)
        self.assertTrue(arguments([]).actions)
        self.assertTrue(arguments(['--actions']).actions)
        self.assertFalse(arguments(['--no-actions']).actions)
        with patch('bwv.credentials.credential_environment', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Choose a model'):
                prepare_provider(LLMConfig.defaults(), Path('settings.json'), dummy=False)
            with self.assertRaisesRegex(ValueError, 'API key is missing'):
                prepare_provider(LLMConfig('fixture'), Path('settings.json'), dummy=False)
            prepare_provider(LLMConfig.defaults(), Path('settings.json'), dummy=True)

    def test_discovery_matches_path_not_name_and_rejects_ambiguity(self):
        root = Path('synthetic-game').resolve()
        other = GameProcess(2, str(Path('other-game/runblack.exe').resolve()), 1)
        own = GameProcess(3, str(root / 'runblack.exe'), 1)
        self.assertIsNone(select_game([other], root))
        self.assertEqual(select_game([other, own], root), own)
        with self.assertRaisesRegex(RuntimeError, 'More than one'):
            select_game([own, GameProcess(4, own.image, 2)], root)

    def test_folder_is_remembered_only_in_private_storage_and_invalid_file_recovers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); game = root / 'game'; game.mkdir()
            (game / 'runblack.exe').touch()
            private = root / '.local' / 'launcher.json'
            self.assertEqual(choose_game(game, private), game.resolve())
            self.assertEqual(choose_game(None, private), game.resolve())
            with self.assertRaises(ValueError): choose_game(root, private)
            self.assertEqual(json.loads(private.read_text())['game_directory'], str(game.resolve()))

    def test_errors_visible_repeated_with_backoff_and_recovery_visible(self):
        now = [0]; emit = Mock(); notices = Notices(emit, lambda: now[0])
        notices.show('x', 'error', error=True)
        now[0] = 3; notices.show('x', 'error', error=True)
        self.assertEqual(emit.call_count, 1)
        now[0] = 31; notices.show('x', 'error', error=True)
        notices.show('x', 'recovered')
        self.assertEqual(emit.call_count, 3)
        self.assertIn('API key',chat_error('authentication_failed'))
        self.assertIn('retry',chat_error('connection_failed'))


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_detach_reuse_pid_and_recoverable_discovery_error(self):
        root = Path('synthetic-game').resolve()
        one = GameProcess(20, str(root / 'runblack.exe'), 10)
        restarted = GameProcess(20, one.image, 11)
        discovery = Mock(side_effect=[[], [one], OSError('fixture unavailable'), [one], [], [restarted]])
        inspector = Mock(); messages = Mock(); validator = Mock(); started = []; ended = []
        async def collect(inspector, pid, seconds):
            started.append(pid)
            try: await asyncio.Future()
            finally: ended.append(pid)
        supervisor = ObserverSupervisor(root, inspector, Notices(messages), discover=discovery,
                                        validator=validator, collector=collect)
        try:
            await supervisor.step(); self.assertIsNone(supervisor.pid)
            await supervisor.step(); await asyncio.sleep(0); self.assertEqual(supervisor.pid, 20)
            await supervisor.step(); self.assertIsNone(supervisor.pid)
            self.assertTrue(any('Retrying' in str(c) for c in messages.call_args_list))
            await supervisor.step(); await asyncio.sleep(0)
            await supervisor.step(); self.assertIsNone(supervisor.pid)
            await supervisor.step(); await asyncio.sleep(0); self.assertEqual(supervisor.active, restarted)
            self.assertEqual(len(started), 3)
        finally:
            await supervisor.detach()
        self.assertEqual(len(ended), 3)

    async def test_failed_attachment_is_retried_without_creating_a_collector(self):
        root = Path('synthetic-game').resolve()
        game = GameProcess(30, str(root / 'runblack.exe'), 1)
        validator = Mock(side_effect=[OSError('fixture loading'), None])
        collector = AsyncMock()
        supervisor = ObserverSupervisor(root, Mock(), Notices(Mock()), discover=lambda: [game],
                                        validator=validator, collector=collector)
        try:
            await supervisor.step(); self.assertIsNone(supervisor.pid); collector.assert_not_called()
            await supervisor.step(); self.assertEqual(supervisor.pid, 30)
        finally:
            await supervisor.detach()


class InstallUpdateTests(unittest.TestCase):
    def test_update_keeps_original_and_recovers_after_prepared_manifest(self):
        from bwv.install import update, uninstall
        from bwv.compat import ORIGINAL_VM, VM_PATH, sha256
        with TemporaryDirectory() as directory:
            root = Path(directory); (root/'Plug Ins').mkdir()
            (root/ORIGINAL_VM).write_bytes(b'original fixture')
            (root/VM_PATH).write_bytes(b'old authored bridge')
            incoming = root/'new.dll'; incoming.write_bytes(b'new authored bridge')
            original_hash=sha256(root/ORIGINAL_VM); old_hash=sha256(root/VM_PATH)
            marker=root/'bwv-install.json'
            marker.write_text(json.dumps({'schema_version':1,'original_vm_sha256':original_hash,
                                          'bridge_sha256':old_hash}))
            report={'status':'experimental','vm_exports':['Version']}
            pe=Mock(machine=0x14c); pe.exports.return_value=['Version']
            with patch('bwv.install.inspect_install',return_value=report), patch('bwv.install.PE.read',return_value=pe):
                self.assertTrue(update(root,incoming)['changed'])
                self.assertEqual(sha256(root/ORIGINAL_VM),original_hash)
                self.assertFalse(update(root,incoming)['changed'])
                # Crash recovery before staged DLL replacement: prior hash remains recognized.
                (root/VM_PATH).write_bytes(b'old authored bridge')
                uninstall(root)
                self.assertEqual(sha256(root/VM_PATH),original_hash)

    def test_changed_bridge_is_not_overwritten(self):
        from bwv.install import update
        from bwv.compat import ORIGINAL_VM, VM_PATH, sha256
        with TemporaryDirectory() as directory:
            root=Path(directory); (root/'Plug Ins').mkdir()
            (root/ORIGINAL_VM).write_bytes(b'original')
            (root/VM_PATH).write_bytes(b'other modification')
            (root/'bwv-install.json').write_text(json.dumps({'schema_version':1,
                'original_vm_sha256':sha256(root/ORIGINAL_VM),'bridge_sha256':'different'}))
            with patch('bwv.install.inspect_install',return_value={'status':'experimental'}):
                with self.assertRaisesRegex(ValueError,'another modification'):
                    update(root,root/'new.dll')
            self.assertEqual((root/VM_PATH).read_bytes(),b'other modification')
