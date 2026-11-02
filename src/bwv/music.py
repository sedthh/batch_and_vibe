"""Read-only Winamp context. Playback and native reactions belong to the MP3 add-on.

Playback uses scalar IPC; the optional filename uses a bounded read of Winamp's
returned string. No process writes, audio capture or player controls. API reference:
https://github.com/hugovk/winamp/blob/master/wa_ipc.h
"""
import asyncio
from collections import deque, OrderedDict
from copy import deepcopy
import ctypes
from dataclasses import dataclass, field
import math
import re
import sys
import time
from .music_tags import TagCache, TrackTags
from .music_history import ListeningHistory, song_key


def display_title(caption):
    """Keep the displayed label whole; never guess an artist/title split."""
    if len(caption) >= 1023 or any(ord(c) < 32 for c in caption):
        return None
    title = re.sub(r'\s+-\s+Winamp(?:\s+\[(?:Paused|Stopped)\])?$', '',
                   caption, flags=re.IGNORECASE).strip()
    title = re.sub(r'^\d+\.\s+', '', title)
    if not title or title.casefold().startswith('winamp') or len(title) > 200:
        return None
    # Some title formats expose a file/stream location rather than a label.
    if re.search(r'://|^[A-Za-z]:[\\/]|^\\\\|^/', title):
        return None
    return title


@dataclass(frozen=True)
class Playback:
    state: str
    title: str | None = None
    identity: tuple | None = None
    position_seconds: float | None = None
    duration_seconds: int | None = None
    tags: dict | None = None
    song_key: str | None = None
    lyric_source: TrackTags | None = field(default=None,repr=False,compare=False)


class _Win32:
    def __init__(self):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]
        self.api.FindWindowExW.restype = w.HWND
        self.api.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.api.GetWindowTextW.restype = ctypes.c_int
        self.api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM,
            w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.api.SendMessageTimeoutW.restype = w.LPARAM

    def windows(self):
        first = self.api.FindWindowExW(None, None, 'Winamp v1.x', None)
        if not first:
            return ()
        second = self.api.FindWindowExW(None, first, 'Winamp v1.x', None)
        return (first, second) if second else (first,)

    def caption(self, window):
        buffer = ctypes.create_unicode_buffer(1024)
        if not self.api.GetWindowTextW(window, buffer, len(buffer)):
            raise OSError('Winamp title unavailable')
        return buffer.value

    def pid(self, window):
        from ctypes import wintypes as w
        pid = w.DWORD()
        if not self.api.GetWindowThreadProcessId(window, ctypes.byref(pid)) or not pid.value:
            raise OSError('Winamp process unavailable')
        return pid.value

    def query(self, window, code, parameter=0):
        if (code, parameter) not in ((104,0), (125,0), (105,0), (105,1)):
            raise ValueError('Unsupported read-only Winamp query')
        return ctypes.c_int32(self._send(window, code, parameter) & 0xffffffff).value

    def _send(self, window, code, parameter=0):
        result = ctypes.c_size_t()
        # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT; at most 100 ms/query.
        if not self.api.SendMessageTimeoutW(window, 0x400, parameter, code, 0x23, 100,
                                            ctypes.byref(result)):
            raise OSError('Winamp query unavailable')
        return result.value

    def filename(self, window, pid, index):
        """IPC_GETPLAYLISTFILE(W) pointers belong to Winamp, never this process."""
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD,w.BOOL,w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        kernel.ReadProcessMemory.argtypes = [w.HANDLE,ctypes.c_void_p,ctypes.c_void_p,
                                            ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        kernel.ReadProcessMemory.restype = w.BOOL
        kernel.IsWow64Process.argtypes = [w.HANDLE,ctypes.POINTER(w.BOOL)]
        kernel.IsWow64Process.restype = w.BOOL
        handle = kernel.OpenProcess(0x1010, False, pid)  # read + limited query only
        if not handle:raise OSError('Winamp filename unavailable')
        try:
            wow = w.BOOL()
            if not kernel.IsWow64Process(handle,ctypes.byref(wow)):
                raise OSError('Winamp architecture unavailable')
            for code,unit,encoding in ((214,2,'utf-16-le'),(211,1,'mbcs')):
                pointer = self._send(window,code,index)
                if wow.value:pointer &= 0xffffffff
                if pointer < 0x10000:continue
                data = bytearray()
                # Page-bounded reads avoid failing at the end of mapped strings.
                while len(data) < 8192:
                    size = min(256,4096-((pointer+len(data)) % 4096),8192-len(data))
                    buffer = ctypes.create_string_buffer(size)
                    received = ctypes.c_size_t()
                    if not kernel.ReadProcessMemory(handle,pointer+len(data),buffer,size,ctypes.byref(received)):
                        break
                    if received.value != size:break
                    data.extend(buffer.raw)
                    for end in range(0,len(data)-unit+1,unit):
                        if data[end:end+unit] == b'\0'*unit:
                            if self.pid(window) != pid:raise OSError('Winamp changed')
                            return data[:end].decode(encoding)
            raise OSError('Winamp filename unavailable')
        finally:
            kernel.CloseHandle(handle)


class WinampReader:
    def __init__(self, api=None, tag_cache=None):
        self.api = api
        self.tag_cache = tag_cache or TagCache()

    def read(self):
        if self.api is None:
            if sys.platform != 'win32':
                return Playback('unsupported')
            self.api = _Win32()
        windows = self.api.windows()
        if not windows:
            return Playback('not_running')
        if len(windows) != 1:
            return Playback('multiple_players')
        window = windows[0]
        pid = self.api.pid(window)
        state = self.api.query(window, 104)
        if state == 0:
            return Playback('stopped')
        if state not in (1, 3):
            return Playback('unavailable')
        index = self.api.query(window, 125)
        caption = self.api.caption(window)
        def optional(code, parameter=0):
            try:return self.api.query(window, code, parameter)
            except OSError:return -1
        position = optional(105)
        length = optional(105, 1)
        seconds = round(position/1000,3) if 0 <= position <= 0x7fffffff else None
        tags = {}
        filename = None
        metadata = None
        if hasattr(self.api,'filename'):
            try:
                filename = self.api.filename(window,pid,index)
                metadata = self.tag_cache.get(filename)
                tags = metadata.at(seconds)
                if self.api.filename(window,pid,index) != filename:
                    return Playback('unavailable')
            except (OSError,UnicodeError):
                filename = None
                tags = {}
                metadata = None
        # Reject a transition halfway through sampling, rather than mix tracks.
        if (index < 0 or self.api.query(window, 125) != index
                or self.api.query(window, 104) != state
                or self.api.caption(window) != caption or self.api.pid(window) != pid):
            return Playback('unavailable')
        title = display_title(caption)
        title = tags.get('title') or title
        return Playback({1:'playing', 3:'paused'}[state], title,
                        (window, pid, index, caption, filename), seconds,
                        length if 0 < length <= 0x7fffffff else None, tags or None,
                        song_key(filename,title,tags.get('artist')), metadata)


class MusicMonitor:
    """Bounded off-thread polling, with stale data never presented as playing."""
    def __init__(self, *, enabled=False, reader=None, clock=time.monotonic, history_path=None,
                 lyrics_online=False, lyrics_cache_dir=None, lyrics_lookup=None):
        self.enabled = enabled
        self.reader = reader or WinampReader()
        self.clock = clock
        self._lock = asyncio.Lock()
        self._generation = 0
        self.listening = ListeningHistory(history_path)
        from .music_lyrics import LyricsLookup
        self.lyrics = lyrics_lookup or LyricsLookup(lyrics_cache_dir,enabled=lyrics_online)
        self.history = deque(maxlen=660)
        self._lyric_sources=OrderedDict()
        self.clear()

    def clear(self):
        self._reading = Playback('unavailable')
        self._identity = None
        self._sampled = -math.inf
        self.history.clear()
        self._lyric_sources.clear()
        self.listening.break_continuity()
        self._listening_label=None
        self._on_repeat=False

    async def poll(self):
        if not self.enabled:
            return
        async with self._lock:
            now = self.clock()
            if now - self._sampled < 1:
                return
            # A long sampling gap breaks continuity even if the same label returns.
            if now - self._sampled > 3:
                self._identity = None
            def read_and_track():
                try:reading = self.reader.read()
                except OSError:reading = Playback('unavailable')
                reading=self.lyrics.enrich(reading)
                label=self.listening.observe(reading,self.clock())
                return reading,label,self.listening.on_repeat if label else False
            reading,self._listening_label,self._on_repeat = await asyncio.to_thread(read_and_track)
            if reading.identity != self._identity:
                self._generation += 1
            if reading.lyric_source is not None:
                self._lyric_sources[self._generation]=reading.lyric_source
                self._lyric_sources.move_to_end(self._generation)
                while len(self._lyric_sources)>64:self._lyric_sources.popitem(last=False)
            self._identity = reading.identity
            self._reading = reading
            self._sampled = self.clock()
            self.history.append((self._sampled, self.snapshot()))
            while self.history and self._sampled-self.history[0][0] > 600:
                self.history.popleft()

    def snapshot(self):
        if not self.enabled:
            return {'source':'winamp', 'state':'disabled'}
        if self.clock() - self._sampled > 3:
            return {'source':'winamp', 'state':'unavailable'}
        result = {'source':'winamp', 'state':self._reading.state}
        if self._reading.title:
            result['title'] = self._reading.title
            result['title_source'] = 'id3_tags' if (self._reading.tags or {}).get('title') else 'window_caption'
        if self._identity:
            result['track_session'] = self._generation
        for name in ('position_seconds', 'duration_seconds'):
            value = getattr(self._reading, name)
            if value is not None:result[name] = value
        if self._reading.tags:result.update(deepcopy(self._reading.tags))
        if self._listening_label:result['listening_history']=self._listening_label
        if self._on_repeat:result['on_repeat']=True
        if self.listening.error:result['listening_history_error']=self.listening.error
        result['sample_age_seconds'] = round(max(0, self.clock()-self._sampled), 2)
        return result

    def close(self):
        self.lyrics.close()

    def add_lyric_match(self, text, music, recording):
        """Enrich only this submitted message, using its captured track sessions."""
        from .music_context import music_blocks
        current={'music_at_submission':music,'music_during_recording':recording}
        blocks=list(music_blocks(current))
        # For recorded voice, never substitute the song at submission when the
        # recording's samples cannot identify a song.
        if recording is not None:
            blocks=list(music_blocks({'music_during_recording':recording}))
        if any('at_seconds' in item for block in blocks for item in block.get('lyrics',[])):return
        for block in blocks:
            source=self._lyric_sources.get(block.get('track_session'))
            if source is not None:
                excerpt=source.matching(text)
                if excerpt:
                    block['lyrics']=excerpt
                    return

    def during_recording(self, start, end):
        """Summarize sampled playback, without assigning an entire clip to one song."""
        if not self.enabled:return None
        def at(when):
            candidate = next(((stamp, value) for stamp, value in reversed(self.history)
                              if stamp <= when), None)
            if candidate is None or when-candidate[0] > 3:
                return {'source':'winamp', 'state':'unavailable'}
            stamp, value = candidate
            return dict(value, sample_age_seconds=round(when-stamp, 2))
        first, last = at(start), at(end)
        previous = (first.get('track_session'), first['state'])
        changes = []
        stamps = [stamp for stamp, _ in self.history if start < stamp <= end]
        for stamp, value in self.history:
            if not start < stamp <= end:continue
            current = (value.get('track_session'), value['state'])
            if current != previous:
                changes.append({'seconds_into_recording':round(stamp-start,2), **value})
            previous = current
        result = {'source':'winamp_samples', 'recording_seconds':round(end-start,2),
                  'recording_timing':'estimated_from_captured_audio_duration',
                  'at_start':first, 'at_end':last, 'changes':changes[:16],
                  'has_observation_gap':(first['state']=='unavailable' or last['state']=='unavailable'
                      or any(value['state']=='unavailable' for stamp,value in self.history if start < stamp <= end)
                      or any(b-a > 3 for a,b in zip([start,*stamps], [*stamps,end])))}
        if len(changes)>16:result['changes_truncated']=True
        return deepcopy(result)
