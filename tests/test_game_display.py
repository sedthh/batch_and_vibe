"""Synthetic windows and launch processes: no game, registry writes or elevation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bwv.game_display import DisplayMonitor, DisplayWindows, GameLauncher, WS_CAPTION


class DisplayTests(unittest.TestCase):
    def test_caption_and_our_borderless_marker_are_distinct_from_fullscreen(self):
        windows=DisplayWindows.__new__(DisplayWindows)
        windows.window=Mock(return_value=20)
        windows.win=Mock()
        windows.win.u.GetPropW.return_value=None
        windows.win.get_long.return_value=WS_CAPTION
        self.assertEqual(windows.mode(7),'windowed')
        windows.win.get_long.return_value=0x80000000
        self.assertEqual(windows.mode(7),'fullscreen')
        windows.win.u.GetPropW.return_value=1
        self.assertEqual(windows.mode(7),'borderless')
        windows.window.return_value=None
        self.assertEqual(windows.mode(7),'unavailable')

    def test_monitor_blocks_fullscreen_missing_window_and_dead_overlay_then_recovers(self):
        now=[1];pid=[7];windows=Mock();windows.mode.return_value='fullscreen'
        monitor=DisplayMonitor(windows,lambda:pid[0],clock=lambda:now[0])
        self.assertEqual(monitor.reason(),'fullscreen_overlay_unavailable')
        windows.mode.return_value='borderless';now[0]+=1
        self.assertIsNone(monitor.reason())
        monitor.overlay_alive=lambda:False
        self.assertEqual(monitor.reason(),'chat_overlay_unavailable')
        monitor.overlay_alive=lambda:True
        windows.mode.side_effect=OSError();now[0]+=1
        self.assertEqual(monitor.reason(),'game_display_unavailable')
        windows.mode.side_effect=None;windows.mode.return_value='windowed';now[0]+=1
        self.assertIsNone(monitor.reason())
        pid[0]=None
        self.assertEqual(monitor.reason(),'game_display_unavailable')


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);(self.root/'runblack.exe').touch()
        self.windows=Mock();self.windows.monitor.return_value=(-1920,0,0,1080)
        self.windows.window.return_value=None
        self.windows.render_window_ready.return_value=False
        self.windows.borderless_matches.return_value=False
        self.process=Mock(pid=7);self.process.poll.return_value=None
        self.spawn=Mock(return_value=self.process);self.configure=Mock(return_value={'fixture':1})
        self.restore=Mock();self.discover=Mock(return_value=[]);self.now=1
        self.launcher=GameLauncher(self.root,windows=self.windows,discover=self.discover,
            spawn=self.spawn,configure=self.configure,restore=self.restore,clock=lambda:self.now)

    def test_windowed_launch_uses_selected_install_cwd_and_size_without_shell(self):
        self.launcher.start('windowed','800x600')
        self.configure.assert_called_once_with(800,600)
        self.spawn.assert_called_once_with([str(self.root/'runblack.exe')],cwd=str(self.root))
        self.windows.window.return_value=20;self.windows.win.rect.return_value=(5,6,805,606)
        self.assertIn('running windowed',self.launcher.poll())
        self.windows.make_borderless.assert_not_called()

    def test_missing_install_blocks_launch_before_display_changes_and_recovers(self):
        self.assertIsNone(self.launcher.installation_error)
        (self.root/'runblack.exe').unlink()
        # Launch forces a fresh check even inside the UI's one-second cache.
        with self.assertRaisesRegex(ValueError,'Cannot find runblack.exe'):
            self.launcher.start('windowed','800x600')
        self.spawn.assert_not_called();self.configure.assert_not_called()
        (self.root/'runblack.exe').touch();self.now+=2
        self.assertIsNone(self.launcher.installation_error)

    def test_launch_stays_busy_after_window_ready_until_process_exits(self):
        self.launcher.start('windowed','800x600')
        self.assertTrue(self.launcher.busy)
        self.windows.window.return_value=20;self.windows.win.rect.return_value=(0,0,800,600)
        self.launcher.poll()
        self.assertIsNone(self.launcher.pending)
        self.assertTrue(self.launcher.busy)
        with self.assertRaisesRegex(ValueError,'already running'):
            self.launcher.start('borderless','800x600')
        self.spawn.assert_called_once()
        self.process.poll.return_value=0;self.now+=2
        self.assertFalse(self.launcher.busy)

    def test_timeout_keeps_launch_blocked_while_game_remains_running(self):
        self.launcher.start('windowed','800x600');self.now+=61
        with self.assertRaises(RuntimeError):self.launcher.poll()
        self.assertTrue(self.launcher.busy)

    def test_external_game_discovery_is_throttled_and_tracks_exit(self):
        self.discover.return_value=[SimpleNamespace(pid=9)]
        for _ in range(30):self.assertTrue(self.launcher.busy)
        self.discover.assert_called_once()
        self.discover.return_value=[];self.now+=2
        self.assertFalse(self.launcher.busy)

    def test_borderless_accepts_clamped_client_size_and_verifies_removed_menu(self):
        self.launcher.start('borderless','800x600',hwnd=90)
        self.windows.monitor.assert_called_once_with(90)
        self.configure.assert_called_once_with(1920,1080)
        self.windows.window.return_value=20;self.windows.win.rect.return_value=(0,0,300,200)
        self.assertIsNone(self.launcher.poll())
        self.windows.make_borderless.assert_not_called()
        # A framed window can never have the monitor-sized client area used by
        # the old readiness check. The menu/title bar occupy part of its height.
        self.windows.win.rect.return_value=(8,50,1912,1030)
        self.windows.render_window_ready.return_value=True
        self.assertIsNone(self.launcher.poll())
        self.now+=.5
        self.windows.borderless_matches.return_value=True
        self.assertIsNone(self.launcher.poll())
        self.windows.make_borderless.assert_called_once_with(20,(-1920,0,0,1080))
        self.now+=1
        self.assertIn('running borderless',self.launcher.poll())

    def test_borderless_retries_restored_menu_without_false_success(self):
        self.launcher.start('borderless','800x600')
        self.windows.window.return_value=20;self.windows.win.rect.return_value=(0,0,1900,1000)
        self.windows.render_window_ready.return_value=True
        self.assertIsNone(self.launcher.poll())
        for _ in range(2):
            self.now+=.5
            self.assertIsNone(self.launcher.poll())
        self.now+=.5
        with self.assertRaisesRegex(RuntimeError,'did not retain borderless'):
            self.launcher.poll()
        self.assertEqual(self.windows.make_borderless.call_count,3)
        self.assertIsNone(self.launcher.pending)
        self.process.terminate.assert_not_called()

    def test_startup_menu_reappears_then_borderless_is_reapplied_and_verified(self):
        self.launcher.start('borderless','800x600')
        self.windows.window.return_value=20;self.windows.win.rect.return_value=(0,0,1900,1000)
        self.windows.render_window_ready.return_value=True
        self.launcher.poll();self.now+=.5
        self.windows.borderless_matches.return_value=True
        self.assertIsNone(self.launcher.poll())
        self.now+=.5;self.windows.borderless_matches.return_value=False
        self.assertIsNone(self.launcher.poll())
        self.now+=.5;self.windows.borderless_matches.return_value=True
        self.assertIsNone(self.launcher.poll())
        self.now+=1
        self.assertIn('running borderless',self.launcher.poll())
        self.assertEqual(self.windows.make_borderless.call_count,2)

    def test_running_game_rejects_launch_before_settings_are_touched(self):
        self.discover.return_value=[SimpleNamespace(pid=1)]
        with self.assertRaisesRegex(ValueError,'already running'):
            self.launcher.start('windowed','800x600')
        self.configure.assert_not_called();self.spawn.assert_not_called()

    def test_spawn_failure_restores_only_changed_display_settings(self):
        self.spawn.side_effect=OSError('fixture')
        with self.assertRaises(OSError):self.launcher.start('windowed','800x600')
        self.restore.assert_called_once_with({'fixture':1})

    def test_startup_timeout_is_visible_and_never_kills_game(self):
        self.launcher.start('borderless','800x600')
        self.now=65
        with self.assertRaisesRegex(RuntimeError,'left running'):self.launcher.poll()
        self.process.terminate.assert_not_called()
        self.assertIsNone(self.launcher.pending)
