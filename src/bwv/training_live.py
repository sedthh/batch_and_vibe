"""Experimental, fingerprint-bound Windows reader for creature training.

Only interoperability layout metadata is embedded. No game instructions, assets,
raw memory captures, injected calls, or process-write APIs are used here.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import math
import os
from pathlib import Path
import struct
import time

from .compat import EXE_SHA256, check_image, sha256
from .pe import PE
from .pipe import snapshot
from .training_detector import TrainingSample


# Local interoperability findings for this exact executable, not all 1.42 builds.
IMAGE_BASE = 0x400000
GAME_POINTER = 0xD0195C
PLAYER_INDEX = 0x205A59
HAND_INDEX = 0x205A5A
GAME_TICK = 0x205A40
PLAYER_START, PLAYER_STRIDE = 0x18, 0xA60
PLAYER_CREATURE, PLAYER_HANDS = 0xA4C, 0x14
HAND_STATE, HAND_TARGET = 0x3AC, 0x400
CREATURE_OWNER, CREATURE_MENTAL = 0x1070, 0x164
INTERACTION_MAGNITUDE = 0x18C5C  # GET_INTERACTION_MAGNITUDE
# Consecutive word written with the magnitude: engine tick of that feedback.
CREATURE_INTERACTION_STATE = 19


class TrainingReader:
    def __init__(self, pid: int):
        if os.name != "nt":
            raise OSError("Live training observation requires Windows")
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("Invalid process ID")
        self.pid = pid
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        # PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION; no write rights.
        self.handle = k.OpenProcess(0x1010, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            if not k.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            executable = Path(path.value)
            self.game_directory = executable.parent
            pe = PE.read(executable)
            compatible=check_image(pe,sha256(executable),'exe')
            if not compatible['supported']:
                raise ValueError(compatible['reason'])
            if (pe.bits != 32 or pe.machine != 0x14C or
                    pe.u32(pe.optional + 28) != IMAGE_BASE or
                    self.read(IMAGE_BASE + pe.header, 24) != pe.take(pe.header, 24)):
                raise ValueError("Unexpected loaded executable layout")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, address: int, size: int) -> bytes:
        if (not self.handle or type(address) is not int or not 0x10000 <= address
                or not 0 < size <= 256 or address + size > 0x80000000):
            raise ValueError("Invalid bounded training read")
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        if (not self.kernel.ReadProcessMemory(self.handle, address, buffer, size,
                ctypes.byref(received)) or received.value != size):
            raise OSError("Training data is unavailable")
        return buffer.raw

    def word(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def require_type(self, address: int, expected: bytes):
        table = self.word(address)
        locator = self.word(table - 4)
        descriptor = self.word(locator + 12)
        if self.read(descriptor + 8, 64).split(b"\0", 1)[0] != expected:
            raise ValueError("Unexpected training object type")

    def _head(self) -> tuple[int, ...]:
        game = self.word(GAME_POINTER)
        player_index = self.read(game + PLAYER_INDEX, 1)[0]
        hand_index = self.read(game + HAND_INDEX, 1)[0]
        if player_index >= 8 or hand_index >= 2:
            raise ValueError("Unsupported player or hand index")
        player = game + PLAYER_START + player_index * PLAYER_STRIDE
        self.require_type(player, b".?AVGPlayer@@")
        return (game, self.word(game + GAME_TICK), player,
                self.word(player + PLAYER_CREATURE),
                self.word(player + PLAYER_HANDS + hand_index * 4))

    def _session(self, state: dict) -> str:
        age = state.get("source_age_ms")
        epoch = state.get("session")
        if (state.get("protocol") != 1 or state.get("pid") != self.pid or
                type(epoch) is not int or epoch < 0 or type(age) not in (int, float)
                or not math.isfinite(age) or not 0 <= age <= 1500):
            raise ValueError("Fresh observer lifecycle information is required")
        return f"{self.pid}:{epoch}"

    def ensure_running(self):
        code = wintypes.DWORD()
        if (not self.handle or not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code))
                or code.value != 259):
            raise OSError("Game process exited")

    def sample(self, observer: dict | None = None) -> TrainingSample:
        self.ensure_running()
        started = time.monotonic()
        session = self._session(snapshot(self.pid) if observer is None else observer)
        for _ in range(3):
            head = self._head()
            game, tick, player, creature, hand = head
            magnitude, feedback_tick, attention, identity = 0.0, 0, False, None
            if creature:
                self.require_type(creature, b".?AVCreature@@")
                if self.word(creature + CREATURE_OWNER) != player:
                    raise ValueError("Selected creature owner mismatch")
                mental = self.word(creature + CREATURE_MENTAL)
                self.require_type(mental, b".?AVCreatureMental@@")
                raw = self.read(mental + INTERACTION_MAGNITUDE, 8)
                magnitude, feedback_tick = struct.unpack("<fI", raw)
                hand_state, target = self.word(hand + HAND_STATE), self.word(hand + HAND_TARGET)
                attention = hand_state == CREATURE_INTERACTION_STATE and target == creature
                # Refuse a sample spanning an object replacement or feedback write.
                if (self.word(creature + CREATURE_MENTAL) != mental or
                        self.read(mental + INTERACTION_MAGNITUDE, 8) != raw or
                        self.word(hand + HAND_STATE) != hand_state or
                        self.word(hand + HAND_TARGET) != target):
                    continue
                identity = hashlib.sha256(f"{session}:{creature}:{mental}".encode()).hexdigest()[:24]
            if head != self._head():
                continue
            if self._session(snapshot(self.pid)) != session:
                raise ValueError("Observer session changed during training read")
            now = time.monotonic()
            if now - started > 0.75:
                raise ValueError("Training read exceeded freshness limit")
            return TrainingSample(session, identity, now, tick, feedback_tick, magnitude, attention)
        raise ValueError("Training fields changed during observation")
