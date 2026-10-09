"""Read-only named-pipe client. The server offers snapshots, not arbitrary calls."""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes

# A fully learned creature can expose many action/target pairs. Keep the wire
# bounded without truncating its expanded catalog at the old 64 KiB test limit.
MAX_SNAPSHOT_BYTES=512*1024


def snapshot(pid: int, timeout_ms: int = 2000) -> dict:
    if os.name != "nt":
        raise OSError("The game bridge requires Windows")
    if not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("Invalid process ID")
    if type(timeout_ms) is not int or not 0 < timeout_ms <= 60000:
        raise ValueError("Invalid pipe timeout")
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.GetTickCount64.argtypes = []
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout_ms / 1000
    name = rf"\\.\pipe\bwv-observer-{pid}"
    if not kernel.WaitNamedPipeW(name, timeout_ms):
        raise OSError(ctypes.get_last_error(), "Bridge unavailable; launch the instrumented game first")
    # A connected server writes one bounded cached snapshot then disconnects.
    with open(name, "rb", buffering=0) as stream:
        # This handle belongs exclusively to this invocation, with no outstanding
        # I/O. Peek before reading so a connected but silent server cannot hang us.
        handle = msvcrt.get_osfhandle(stream.fileno())
        raw = bytearray()
        while len(raw) < MAX_SNAPSHOT_BYTES:
            if time.monotonic() >= deadline:
                raise TimeoutError("Bridge response timed out")
            available = wintypes.DWORD()
            if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                raise OSError(ctypes.get_last_error(), "Bridge disconnected before completing its response")
            if not available.value:
                time.sleep(0.01)
                continue
            chunk = stream.read(min(available.value, 4096, MAX_SNAPSHOT_BYTES - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if b"\n" in chunk:
                result = json.loads(raw.split(b"\n", 1)[0])
                if not isinstance(result, dict):
                    raise ValueError("Invalid bridge response")
                captured = result.get("captured_uptime_ms")
                received = kernel.GetTickCount64()
                # The pipe serves a cached observation. Receiving it again does
                # not make the game's source observation fresh.
                result["source_age_ms"] = (received - captured if type(captured) is int
                    and 0 <= captured <= received else None)
                return result
    raise ValueError("Incomplete or oversized bridge response")
