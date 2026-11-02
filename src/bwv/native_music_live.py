"""Read-only music observations for the supported executable.

Layout verified against the local supported image: GetCurrentMusicEmotion at
0x634370, its updater at 0x633ef0, and line-input dance timing at 0x486fcb.
Public structure references: openblack/bw1-decomp MusicMood.h and
CreatureDanceLineInput.h. No game code, calls, audio capture or feature activation.
"""
import ctypes
import hashlib
import math
import os
import struct


MOOD_SIGNATURES = (
    (0x634370, 42, '138294060c2ac1220b479c0f782738b1fa71823c362d07e242d32a2e71adf916'),
    (0x633ef0, 296, '89666ddb148107f73e2dec8f2573b624e9f7a900400d84919246f8720525458a'))
LINE_SIGNATURE = (0x486fcb, 109, '276e6865825a65dcfd14bc358859fb12be4216c7e340186d540abdb03d3cd4e4')


def verified(reader, signatures):
    return all(hashlib.sha256(reader.read(address, size)).hexdigest() == digest
               for address, size, digest in signatures)


class Sample:
    def __init__(self, reader):
        self.reader = reader
        self.checks = {}

    def value(self, address, fmt='I'):
        raw = self.reader.read(address, 4)
        self.checks[address] = raw
        value, = struct.unpack('<'+fmt, raw)
        if fmt == 'f' and (not math.isfinite(value) or abs(value) > 1e9):
            raise ValueError('Invalid native music reading')
        return value

    def stable(self):
        return all(self.reader.read(address,4) == raw for address,raw in self.checks.items())


def mood(reader):
    if not verified(reader, MOOD_SIGNATURES):return None
    sample = Sample(reader)
    result = {'source':'native_music_mood',
              'mp3_feature_enabled':bool(sample.value(0xd06434)),
              'music_mood_enabled':bool(sample.value(0xd06438))}
    if result['music_mood_enabled']:
        entry = sample.value(0xd410e0)
        if entry:
            count = sample.value(entry+8)
            index = sample.value(0xd410e4)
            result['entry_track_id'] = format(sample.value(entry), '08x')
            # This matches the accessor's neutral/default slot for an invalid
            # index. The slot is not claimed as a measured song preference.
            if 1 <= index <= count <= 100000:
                table = sample.value(entry+12)
                if table < 0x10000:raise ValueError('Invalid music mood table')
                result['mood_engine_units'] = sample.value(table+(index-1)*8, 'f')
        result['recent_feedback_engine_units'] = sample.value(0xc64140, 'f')
    return result if sample.stable() else None


def line_input(reader, tick_ms):
    if not verified(reader, (LINE_SIGNATURE,)):return None
    sample = Sample(reader)
    pointer = sample.value(0xd01964)
    result = {'source':'game_line_input', 'active':False}
    if pointer:
        result['active'] = bool(sample.value(pointer+0x14))
        if result['active']:
            threshold = sample.value(pointer+0x102c, 'f')
            strength = sample.value(pointer+0x350c, 'f')
            spread = sample.value(pointer+0x3050, 'f')
            detected = sample.value(pointer+0x3508)
            tempo = sample.value(pointer+0x3058, 'i')
            # Same freshness/strength checks used by the native dance code.
            age = (tick_ms-detected) & 0xffffffff
            recent = strength > threshold and age < 4000 and spread < 50
            result['recent_beat_detected'] = recent
            result['signal_above_threshold'] = strength > threshold
            if recent and 0 < tempo <= 1000:result['detected_tempo_bpm'] = tempo
            # Never report the native 120 BPM fallback as a detected beat.
    return result if sample.stable() else None


def read_native_music(reader, *, tick_ms=None):
    result = {}
    try:
        value = mood(reader)
        if value is not None:result['mood'] = value
    except (OSError, ValueError):pass
    if tick_ms is None and os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetTickCount.restype = ctypes.c_uint32
        tick_ms = kernel.GetTickCount()
    if tick_ms is not None:
        try:
            value = line_input(reader, tick_ms)
            if value is not None:result['line_input'] = value
        except (OSError, ValueError):pass
    return result or None
