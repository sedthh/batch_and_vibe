from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock

from bwv.game_paths import GAME_KEY,set_game_directory,restore_game_directory


class GamePathTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);(self.root/'runblack.exe').touch()
        self.registry=MagicMock(KEY_QUERY_VALUE=1,KEY_SET_VALUE=2,KEY_WOW64_32KEY=512,REG_SZ=1)
        self.values={'GameDir':('missing old install',1),'other':('untouched',1)}
        def query(key,name):
            if name not in self.values:raise FileNotFoundError(name)
            return self.values[name]
        self.registry.QueryValueEx.side_effect=query
        self.registry.SetValueEx.side_effect=lambda key,name,reserved,kind,value:self.values.__setitem__(name,(value,kind))
        self.registry.DeleteValue.side_effect=lambda key,name:self.values.pop(name)

    def test_selects_install_without_touching_other_settings_and_rolls_back(self):
        before=set_game_directory(self.root,registry=self.registry)
        self.assertEqual(self.values['GameDir'],(str(self.root.resolve()),1))
        self.registry.CreateKeyEx.assert_called_once_with(self.registry.HKEY_CURRENT_USER,GAME_KEY,0,515)
        self.assertEqual(self.values['other'],('untouched',1))
        restore_game_directory(before,registry=self.registry)
        self.assertEqual(self.values['GameDir'],('missing old install',1))

    def test_rollback_removes_value_that_was_originally_absent(self):
        del self.values['GameDir']
        before=set_game_directory(self.root,registry=self.registry)
        self.assertIsNone(before)
        restore_game_directory(before,registry=self.registry)
        self.assertNotIn('GameDir',self.values)

    def test_invalid_install_cannot_change_save_path(self):
        (self.root/'runblack.exe').unlink()
        with self.assertRaises(ValueError):set_game_directory(self.root,registry=self.registry)
        self.registry.CreateKeyEx.assert_not_called()
