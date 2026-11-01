from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from bwv.game_display import GameLauncher
from bwv.vr_desktop import DESKTOP,JOURNAL,prepare,restore
from bwv.vr_install import VR_FILES


class VrDesktopTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        for name in VR_FILES:(self.root/name).touch()
        (self.root/'dxwrapper').mkdir();(self.root/'dxwrapper'/'ddraw.dll').touch()
        self.values={'vr':'1','backend':'vulkan','unrelated':'keep'}
        self.profile=Mock()
        self.profile.get.side_effect=lambda key:self.values.get(key)
        self.profile.set.side_effect=lambda key,value:self.values.__setitem__(key,value)

    def test_normal_launch_with_vr_installed_and_restore_after_exit(self):
        for mode in ('windowed','borderless'):
            with self.subTest(mode=mode),patch('bwv.vr_desktop.Profile',return_value=self.profile):
                windows=Mock();windows.monitor.return_value=(0,0,1920,1080)
                spawn=Mock();process=spawn.return_value;process.poll.return_value=None
                configure=Mock()
                launcher=GameLauncher(self.root,windows=windows,discover=lambda:[],spawn=spawn,configure=configure,
                    configure_directory=Mock(),restore_directory=Mock())
                launcher.start(mode,'800x600')
                self.assertEqual({key:self.values[key] for key in DESKTOP},DESKTOP)
                spawn.assert_called_once_with([str(self.root/'runblack.exe')],cwd=str(self.root))
                configure.assert_called_once_with(*( (800,600) if mode=='windowed' else (1920,1080)))
                launcher.pending=None;process.poll.return_value=0;launcher.poll()
                self.assertEqual(self.values,{'vr':'1','backend':'vulkan','unrelated':'keep'})
                self.assertFalse((self.root/JOURNAL).exists())

    def test_spawn_failure_restores_vr_and_display_settings(self):
        with patch('bwv.vr_desktop.Profile',return_value=self.profile):
            windows=Mock();windows.monitor.return_value=(0,0,1920,1080)
            display_restore=Mock()
            launcher=GameLauncher(self.root,windows=windows,discover=lambda:[],
                spawn=Mock(side_effect=OSError('fixture')),configure=Mock(return_value={'saved':1}),restore=display_restore,
                configure_directory=Mock(),restore_directory=Mock())
            with self.assertRaises(OSError):launcher.start('windowed','800x600')
            self.assertEqual(self.values['backend'],'vulkan');self.assertEqual(self.values['vr'],'1')
            display_restore.assert_called_once_with({'saved':1})

    def test_recovery_preserves_unrelated_and_external_changes(self):
        prepare(self.root,profile=self.profile)
        self.values['backend']='other-user-backend';self.values['unrelated']='changed'
        restore(self.root,profile=self.profile)
        self.assertEqual(self.values,{'vr':'1','backend':'other-user-backend','unrelated':'changed'})

    def test_next_vr_launch_recovers_settings_after_mod_was_closed(self):
        prepare(self.root,profile=self.profile)
        with patch('bwv.vr_desktop.Profile',return_value=self.profile):
            launcher=GameLauncher(self.root,vr_mode=True,windows=Mock(),discover=lambda:[],spawn=Mock(),configure_directory=Mock())
            launcher.start('vr','unused')
        self.assertEqual(self.values['vr'],'1');self.assertEqual(self.values['backend'],'vulkan')

    def test_partial_ini_write_rolls_back(self):
        def write(key,value):
            if key=='backend' and value==DESKTOP[key]:raise OSError('fixture write failed')
            self.values[key]=value
        self.profile.set.side_effect=write
        with self.assertRaises(OSError):prepare(self.root,profile=self.profile)
        self.assertEqual(self.values['vr'],'1');self.assertEqual(self.values['backend'],'vulkan')
        self.assertFalse((self.root/JOURNAL).exists())
