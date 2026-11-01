from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from bwv.game_display import GameLauncher
from bwv.launcher import arguments
from bwv.launcher_ui import save_basics,StatusWindow
from bwv.llm import LLMConfig
from bwv.vr_install import VR_FILES,VR_LAUNCHER,installation_error,configured_folder
from test_launcher_ui import form


class VrLauncherTests(unittest.TestCase):
    def test_start_window_has_same_buttons_in_desktop_and_vr_modes(self):
        import tkinter as tk
        from tkinter import ttk
        layouts=[]
        for vr in (False,True):
            window=StatusWindow.__new__(StatusWindow)
            window.root=Mock();window.game_controls=Mock();window.game_buttons=[]
            window.stopping=False;window.restarting=False;window.write=Mock()
            window.load_display_size=Mock(return_value='800x600')
            launcher=Mock(vr_mode=vr,busy=False,installation_error=None,vr_installation_error=None)
            with patch.object(tk,'StringVar'),patch.object(ttk,'Frame'),patch.object(ttk,'Combobox'),patch.object(ttk,'Button') as button:
                window.set_game_launcher(launcher)
                labels=[call.kwargs['text'] for call in button.call_args_list]
                layouts.append(labels)
                button.return_value.configure.assert_called_with(state='normal')
            window.root.geometry.assert_called_once_with('780x500')
        self.assertEqual(layouts[0],['Run B&W windowed','Run B&W borderless fullscreen','Run B&W VR'])
        self.assertEqual(layouts[0],layouts[1])

    def test_saved_mode_roundtrip_without_enabling_microphone(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';config=LLMConfig('fixture');config.save(path)
            values=form(config);values.update(vr_mode=True)
            result=save_basics(path,values)
            self.assertTrue(result.vr_mode);self.assertFalse(result.voice.enabled)
            self.assertTrue(LLMConfig.load(path).vr_mode)
            for invalid in ('true',1,None):
                with self.assertRaises(ValueError):replace(result,vr_mode=invalid)
        self.assertIsNone(arguments([]).vr)
        self.assertTrue(arguments(['--vr']).vr)
        self.assertFalse(arguments(['--no-vr']).vr)

    def test_old_vr_folder_setting_does_not_require_a_separate_installation(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';LLMConfig('fixture',vr_mode=True).save(path)
            data=json.loads(path.read_text('utf-8'))
            data['vr_game_folder']='unused/old/installation'
            path.write_text(json.dumps(data),encoding='utf-8')
            config=LLMConfig.load(path)
            self.assertTrue(config.vr_mode)
            config.save(path)
            self.assertNotIn('vr_game_folder',json.loads(path.read_text('utf-8')))

    def test_every_required_vr_file_is_checked_and_changes_are_detected(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for name in VR_FILES:(root/name).touch()
            self.assertIsNone(installation_error(root))
            for name in VR_FILES:
                (root/name).unlink()
                self.assertIn(name,installation_error(root))
                (root/name).touch()
            self.assertEqual(configured_folder(root/'settings.json',folder),root.resolve())
            self.assertIsNone(configured_folder(root/'settings.json'))
            cache=root/'.local';cache.mkdir()
            (cache/'launcher.json').write_text(json.dumps({'game_directory':str(root)}),encoding='utf-8')
            self.assertEqual(configured_folder(root/'settings.json'),root)
            selected=root/'selected'
            self.assertEqual(configured_folder(root/'settings.json',selected),selected)

    def test_vr_launch_uses_official_launcher_without_display_writes(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for name in VR_FILES:(root/name).touch()
            spawn=Mock();spawn.return_value.poll.return_value=None
            configure=Mock();windows=Mock()
            directory=Mock()
            launcher=GameLauncher(root,vr_mode=True,windows=windows,discover=lambda:[],spawn=spawn,configure=configure,
                configure_directory=directory)
            result=launcher.start('vr','ignored')
            self.assertIn('Opened',result)
            spawn.assert_called_once_with([str(root/VR_LAUNCHER)],cwd=str(root))
            configure.assert_not_called();windows.monitor.assert_not_called()
            directory.assert_called_once_with(root)
            self.assertIsNone(launcher.pending)
            self.assertTrue(launcher.busy)
            with self.assertRaises(ValueError):launcher.start('vr','ignored')

    def test_vr_spawn_failure_restores_save_root(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for name in VR_FILES:(root/name).touch()
            directory=Mock(return_value=('old',1));restore=Mock()
            launcher=GameLauncher(root,vr_mode=True,windows=Mock(),discover=lambda:[],
                spawn=Mock(side_effect=OSError('failed')),configure_directory=directory,restore_directory=restore)
            with self.assertRaises(OSError):launcher.start('vr','ignored')
            restore.assert_called_once_with(('old',1))

    def test_missing_files_and_desktop_override_never_spawn(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'runblack.exe').touch()
            spawn=Mock();configure=Mock()
            launcher=GameLauncher(root,vr_mode=True,windows=Mock(),discover=lambda:[],spawn=spawn,configure=configure,restore=Mock(),
                configure_directory=Mock(),restore_directory=Mock())
            with self.assertRaisesRegex(ValueError,'BW1-VR is unavailable'):launcher.start('vr','800x600')
            with self.assertRaises(ValueError):launcher.start('windowed','800x600')
            launcher.vr_mode=False;(root/VR_LAUNCHER).touch()
            launcher.windows.monitor.return_value=(0,0,1920,1080)
            with self.assertRaisesRegex(ValueError,'desktop renderer is missing'):launcher.start('windowed','800x600')
            spawn.assert_not_called()

    def test_missing_vr_disables_only_vr_button_then_recovers(self):
        window=StatusWindow.__new__(StatusWindow)
        window.stopping=False;window.restarting=False;window.write=Mock()
        window.game_buttons=[Mock(),Mock(),Mock()]
        window.game_launcher=Mock(busy=False,installation_error=None,vr_mode=False,vr_installation_error='Missing VR files.')
        window.vr_button=window.game_buttons[-1]
        window.refresh_game_buttons()
        for button in window.game_buttons[:2]:button.configure.assert_called_with(state='normal')
        window.game_buttons[-1].configure.assert_called_with(state='disabled')
        window.game_launcher.vr_installation_error=None;window.game_launcher.vr_mode=True
        window.refresh_game_buttons()
        window.game_buttons[-1].configure.assert_called_with(state='normal')

    def test_vr_button_applies_mode_before_launch_without_changing_voice(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';original=LLMConfig('fixture');original.save(path)
            window=StatusWindow.__new__(StatusWindow)
            window.settings_path=path;window.stopping=False;window.restarting=False
            window.root=Mock();window.size=Mock();window.write=Mock()
            window.game_buttons=[Mock(),Mock(),Mock()];window.vr_button=window.game_buttons[-1]
            window.game_launcher=Mock(busy=False,installation_error=None,vr_mode=False,vr_installation_error=None)
            def restart(config):
                self.assertTrue(config.vr_mode);self.assertEqual(config.voice,original.voice)
                window.game_launcher.vr_mode=config.vr_mode;window.restarting=True
            window.restart=restart
            window.start_game('vr')
            window.game_launcher.start.assert_not_called()
            self.assertEqual(window.pending_game_mode,'vr')
            window.heading=Mock();window.settings_button=Mock();window.cut_content_toggle=Mock()
            window.set_restarting(False)
            window.game_launcher.start.assert_called_once_with('vr',window.size.get(),window.root.winfo_id())
            self.assertIsNone(window.pending_game_mode)
