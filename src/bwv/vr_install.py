"""Read-only discovery of the installed BW1-VR launcher and required files."""
import json
from pathlib import Path

VR_LAUNCHER='Black & White VR Mod.exe'
VR_FILES=('runblack.exe',VR_LAUNCHER,'ddraw.dll','openxr_loader.dll')


def installation_error(root):
    if root is None:return 'Select your Black & White installation in the launcher first.'
    try:
        missing=[name for name in VR_FILES if not (Path(root)/name).is_file()]
    except (OSError,ValueError):return 'Cannot read the game folder.'
    return 'BW1-VR is unavailable. Missing: '+', '.join(missing) if missing else None


def configured_folder(settings_path,game_root=None):
    """Use the launcher's selected installation for every mod."""
    if game_root is not None:return Path(game_root)
    try:
        data=json.loads((Path(settings_path).parent/'.local'/'launcher.json').read_text('utf-8'))
        value=data['game_directory']
        return Path(value) if isinstance(value,str) and value else None
    except (OSError,ValueError,TypeError,KeyError):return None
