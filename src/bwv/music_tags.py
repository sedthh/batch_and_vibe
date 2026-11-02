"""Bounded local MP3 tags; full lyrics never leave the in-memory cache."""
from bisect import bisect_right
from collections import OrderedDict, Counter
from dataclasses import dataclass, field
from io import BytesIO
from functools import cached_property
import math
from pathlib import Path
import re


MAX_TAG_BYTES = 1024 * 1024
MAX_LYRIC_BYTES = 320


def short_text(value, limit):
    if not isinstance(value, str):return ''
    value = ' '.join(value.split())
    return value.encode('utf-8')[:limit].decode('utf-8', 'ignore')


def timed_lines(values):
    """TinyTag converts embedded SYLT into LRC."""
    stamp = re.compile(r'\[(\d{1,4}):([0-5]\d)(?:\.(\d{1,3}))?\]')
    for value in values[:4]:
        if not isinstance(value, str) or len(value) > 65536:continue
        # An LRC offset is milliseconds, positive shifts the lyric earlier.
        offset = re.search(r'\[offset:([+-]?\d+)\]', value, re.IGNORECASE)
        shift = int(offset[1])/1000 if offset else 0
        lines = []
        for line in value.splitlines()[:2000]:
            matches = list(stamp.finditer(line))
            if not matches:continue
            text = short_text(line[matches[-1].end():], MAX_LYRIC_BYTES)
            for match in matches[:8]:
                seconds = int(match[1])*60 + int(match[2]) + float('0.'+(match[3] or '0')) - shift
                # Preserve blank cues: they stop a previous lyric in instrumental gaps.
                lines.append((max(0, round(seconds,3)), text))
        if lines:return tuple(sorted(lines, key=lambda item:item[0]))[:2000]
    return ()


def lyric_data(synced=None, plain=None):
    timed=timed_lines([synced] if isinstance(synced,str) else [])
    untimed=()
    if not timed and isinstance(plain,str) and len(plain)<=65536:
        untimed=tuple(line.strip() for line in plain.splitlines()[:2000] if line.strip())
    return timed,untimed


@dataclass(frozen=True)
class TrackTags:
    fields: dict = field(default_factory=dict)
    lines: tuple = ()
    untimed: tuple = ()

    @cached_property
    def refrain(self):
        """A repeated phrase is a singable hint, not a timed lyric location."""
        lines=[line for line in self.untimed if not re.fullmatch(r'\s*[\[(].*?[\])]\s*',line)]
        normalized=[' '.join(re.findall(r'[^\W_]+',line.casefold())) for line in lines]
        candidates=[]
        for size in (2,1):
            blocks=[tuple(normalized[i:i+size]) for i in range(len(lines)-size+1)]
            counts=Counter(blocks)
            seen=set()
            for index,block in enumerate(blocks):
                if block in seen:continue
                seen.add(block)
                words=sum(len(line.split()) for line in block)
                if counts[block]<2 or not 3<=words<=40 or any(not line for line in block):continue
                # Require disjoint occurrences rather than overlapping fragments.
                occurrences=[]
                for i,b in enumerate(blocks):
                    if b==block and (not occurrences or i-occurrences[-1]>=size):occurrences.append(i)
                if len(occurrences)<2:continue
                text=short_text(' '.join(lines[index:index+size]),MAX_LYRIC_BYTES)
                candidates.append((len(occurrences)*min(words,20),words,-index,text))
        return max(candidates)[3] if candidates else None

    def matching(self, words):
        """Retrieve text, never classify the player's intent or gate commands."""
        if self.lines or not self.untimed or not isinstance(words,str):return []
        tokens=re.findall(r'[^\W_]+',words.casefold())[:80]
        if len(tokens)<3:return []
        # Literal word phrases tolerate punctuation/case and a few extra spoken
        # words. Player text is escaped; it never becomes executable regex syntax.
        phrases=[r'\W+'.join(re.escape(t) for t in tokens[i:i+3]) for i in range(len(tokens)-2)]
        pattern=re.compile(r'\b(?:'+'|'.join(phrases)+r')\b',re.IGNORECASE)
        for index,line in enumerate(self.untimed):
            passage=line+(' '+self.untimed[index+1] if index+1<len(self.untimed) else '')
            match=pattern.search(passage)
            if match:
                # A nearby excerpt only, with no fabricated playback timestamp.
                excerpt=short_text(passage[match.start():match.end()+120],MAX_LYRIC_BYTES)
                return [{'text':excerpt,'match':'words'}] if excerpt else []
        return []

    def at(self, seconds):
        result = dict(self.fields)
        if not self.lines and self.refrain:
            result['lyrics']=[{'text':self.refrain,'match':'refrain'}]
            return result
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not self.lines:return result
        index = bisect_right([item[0] for item in self.lines], seconds) - 1
        if index < 0 or seconds-self.lines[index][0] > 15 or not self.lines[index][1]:return result
        # Current cue and at most one nearby preceding cue; never future lyrics.
        chosen = self.lines[max(0,index-1):index+1]
        chosen = [item for item in chosen if item[1] and seconds-item[0] <= 15]
        remaining = MAX_LYRIC_BYTES
        excerpt = []
        for at, text in reversed(chosen):
            text = short_text(text, remaining)
            if text:
                excerpt.append({'at_seconds':at, 'text':text})
                remaining -= len(text.encode('utf-8'))
        if excerpt:result['lyrics'] = list(reversed(excerpt))
        return result


def read_tags(path):
    from tinytag import TinyTag
    # Read bounded tag regions only; no audio decoding or artwork extraction.
    with path.open('rb') as source:
        header = source.read(10)
        data = b''
        if len(header) == 10 and header[:3] == b'ID3':
            if any(byte & 128 for byte in header[6:10]):return TrackTags()
            size = sum(byte << (7*(3-i)) for i,byte in enumerate(header[6:10]))
            if size > MAX_TAG_BYTES:return TrackTags()
            body = source.read(size)
            if len(body) != size:return TrackTags()
            data = header + body
        source.seek(0,2)
        if source.tell() >= 128:
            source.seek(-128,2)
            tail = source.read(128)
            if tail[:3] == b'TAG':data += tail
    if not data:return TrackTags()
    tags = TinyTag.get(filename='track.mp3', file_obj=BytesIO(data), duration=False, image=False)
    fields = {}
    for name,limit in (('title',160),('artist',120),('genre',60)):
        value = short_text(getattr(tags,name,None),limit)
        if value:fields[name] = value
    bpm = tags.other.get('bpm') or []
    if bpm:
        try:
            number = float(bpm[0])
            if math.isfinite(number) and 0 < number <= 1000:fields['tempo_bpm'] = round(number,2)
        except (ValueError,TypeError):pass
    values=tags.other.get('lyrics') or []
    timed=timed_lines(values)
    untimed=()
    if not timed:
        for value in values[:4]:
            if isinstance(value,str) and 0<len(value)<=65536:
                untimed=tuple(line.strip() for line in value.splitlines()[:2000] if line.strip())
                if untimed:break
    return TrackTags(fields,timed,untimed)


class TagCache:
    def __init__(self, loader=read_tags):
        self.loader = loader
        self.entries = OrderedDict()

    def get(self, filename):
        if not isinstance(filename,str) or '://' in filename or filename.startswith(('\\\\','//')):
            return TrackTags()
        path = Path(filename)
        if not path.is_absolute() or path.suffix.lower() != '.mp3':return TrackTags()
        try:
            stat = path.stat()
            key = (str(path),stat.st_size,stat.st_mtime_ns)
            if key not in self.entries:
                # Malformed/unsupported tags must not interrupt playback observations.
                try:value = self.loader(path)
                except Exception:value = TrackTags()
                self.entries[key] = value
            self.entries.move_to_end(key)
            while len(self.entries) > 8:self.entries.popitem(last=False)
            return self.entries[key]
        except (OSError,ValueError):return TrackTags()
