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


def _connect_snapshot(kernel, name, deadline):
    """Wait/open share one deadline; availability does not reserve a pipe slot."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Bridge connection timed out")
        if not kernel.WaitNamedPipeW(name, max(1, int(remaining * 1000))):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = kernel.CreateFileW(name, 0x80000000, 0, None, 3, 0, None)
        if handle != wintypes.HANDLE(-1).value:
            return handle
        error = ctypes.get_last_error()
        if error != 231:  # ERROR_PIPE_BUSY: another reader took the available slot.
            raise ctypes.WinError(error)
        # Retry only this read-only connection, never an action or model call.
        time.sleep(min(.01, max(0, deadline - time.monotonic())))


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
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetTickCount64.argtypes = []
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout_ms / 1000
    name = rf"\\.\pipe\bwv-observer-{pid}"
    handle = _connect_snapshot(kernel, name, deadline)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    # A connected server writes one bounded cached snapshot then disconnects.
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
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
