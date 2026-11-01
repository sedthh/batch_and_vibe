"""Keep the game's registry save root aligned with the installation we launch."""
from pathlib import Path

GAME_KEY = r'Software\Lionhead Studios Ltd\Black & White'


def set_game_directory(root, *, registry=None):
    if registry is None:
        import winreg as registry
    root=Path(root).resolve()
    if not (root/'runblack.exe').is_file():
        raise ValueError('Cannot set the game save path: runblack.exe is missing.')
    access=registry.KEY_QUERY_VALUE|registry.KEY_SET_VALUE|registry.KEY_WOW64_32KEY
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,GAME_KEY,0,access) as key:
        try:previous=registry.QueryValueEx(key,'GameDir')
        except FileNotFoundError:previous=None
        # The engine caches this as its save root. cwd alone is insufficient.
        registry.SetValueEx(key,'GameDir',0,registry.REG_SZ,str(root))
    return previous


def restore_game_directory(previous, *, registry=None):
    """Rollback a failed launch; successful launches retain their save root."""
    if registry is None:
        import winreg as registry
    access=registry.KEY_SET_VALUE|registry.KEY_WOW64_32KEY
    with registry.OpenKey(registry.HKEY_CURRENT_USER,GAME_KEY,0,access) as key:
        if previous is None:
            try:registry.DeleteValue(key,'GameDir')
            except FileNotFoundError:pass
        else:registry.SetValueEx(key,'GameDir',0,previous[1],previous[0])
