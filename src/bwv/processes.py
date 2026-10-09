"""Windows process discovery without shell commands or process-name-only attachment."""
from dataclasses import dataclass
from pathlib import Path
import ctypes
from ctypes import wintypes
import os


@dataclass(frozen=True)
class GameProcess:
    pid: int
    image: str
    started: int


def game_processes():
    if os.name != 'nt':
        raise RuntimeError('The game launcher requires Windows.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = []
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            if entry.name.casefold() == 'runblack.exe':
                process = kernel.OpenProcess(0x1000, False, entry.pid)
                if not process:
                    # A vanished process is recoverable; access denied is not absence.
                    error = ctypes.get_last_error()
                    if error != 87:
                        raise ctypes.WinError(error)
                else:
                    try:
                        image = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(image))
                        stamps = [wintypes.FILETIME() for _ in range(4)]
                        if not kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size)):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if not kernel.GetProcessTimes(process, *(ctypes.byref(s) for s in stamps)):
                            raise ctypes.WinError(ctypes.get_last_error())
                        started = (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
                        result.append(GameProcess(entry.pid, image.value, started))
                    finally:
                        kernel.CloseHandle(process)
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() not in (0, 18):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    return result


def select_game(processes, root):
    expected = str((Path(root) / 'runblack.exe').resolve()).casefold()
    matches = [p for p in processes if str(Path(p.image).resolve()).casefold() == expected]
    if len(matches) > 1:
        raise RuntimeError('More than one game is running from the selected folder. Close the extra instance.')
    return matches[0] if matches else None
