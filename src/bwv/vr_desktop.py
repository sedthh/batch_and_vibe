"""Use BW1-VR's desktop backend for ordinary launches, preserving its options."""
import ctypes
import json
from pathlib import Path

from .vr_install import VR_LAUNCHER

DESKTOP={'vr':'0','backend':r'dxwrapper\ddraw.dll'}
JOURNAL='.bwv-desktop-vr.json'


class Profile:
    def __init__(self,path):
        self.path=str(Path(path).resolve())
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.GetPrivateProfileStringW.argtypes=[ctypes.c_wchar_p]*3+[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_wchar_p]
        self.api.GetPrivateProfileStringW.restype=ctypes.c_uint
        self.api.WritePrivateProfileStringW.argtypes=[ctypes.c_wchar_p]*4
        self.api.WritePrivateProfileStringW.restype=ctypes.c_int

    def get(self,key):
        missing='__BWV_MISSING_VALUE__';buffer=ctypes.create_unicode_buffer(32768)
        size=self.api.GetPrivateProfileStringW('bwvr',key,missing,buffer,len(buffer),self.path)
        if size>=len(buffer)-1:raise ValueError('BW1-VR setting is too long.')
        return None if buffer.value==missing else buffer.value

    def set(self,key,value):
        if not self.api.WritePrivateProfileStringW('bwvr',key,value,self.path):
            raise OSError('Cannot update BW1-VR desktop launch settings.')


def restore(root,*,profile=None):
    root=Path(root);journal=root/JOURNAL
    if not journal.exists():return
    with journal.open('rb') as stream:raw=stream.read(131073)
    if len(raw)>131072:raise ValueError('Invalid saved BW1-VR desktop settings.')
    original=json.loads(raw)
    if (type(original) is not dict or set(original)!=set(DESKTOP) or
            any(value is not None and not isinstance(value,str) for value in original.values())):
        raise ValueError('Invalid saved BW1-VR desktop settings.')
    profile=profile or Profile(root/'bwvr.ini')
    for key,value in original.items():
        # Respect settings changed by the player or another launcher meanwhile.
        if profile.get(key)==DESKTOP[key]:profile.set(key,value)
    journal.unlink()


def prepare(root,*,profile=None):
    root=Path(root)
    if not (root/VR_LAUNCHER).is_file():return False
    if not (root/'dxwrapper'/'ddraw.dll').is_file():
        raise ValueError('BW1-VR desktop renderer is missing: dxwrapper/ddraw.dll. Repair the BW1-VR installation.')
    profile=profile or Profile(root/'bwvr.ini')
    restore(root,profile=profile)
    original={key:profile.get(key) for key in DESKTOP}
    journal=root/JOURNAL;temporary=journal.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(original),encoding='utf-8')
        temporary.replace(journal)
    finally:temporary.unlink(missing_ok=True)
    try:
        for key,value in DESKTOP.items():profile.set(key,value)
    except (OSError,ValueError):
        restore(root,profile=profile)
        raise
    return True
