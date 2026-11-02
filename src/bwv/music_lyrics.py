"""Optional background LRCLIB lookup; never send audio or player speech."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__
from .music_tags import TrackTags, lyric_data


USER_AGENT=f'BlackAndWhiteLLMCreatureMod/{__version__} (https://github.com/sedthh/batch_and_vibe)'


def normalized(value):
    return ' '.join(re.findall(r'[^\W_]+',value.casefold())) if isinstance(value,str) else ''


def query_identity(fields, duration):
    artist=fields.get('artist');title=fields.get('title')
    if not isinstance(artist,str) or not artist.strip() or not isinstance(title,str):return None
    artist=artist.strip();title=title.strip()
    # Common video labels, while retaining meaningful mix/live/version names.
    title=re.sub(r'\s*[\[(](?:(?:official|offical)\s+(?:music\s+)?video|official\s+audio|lyrics?|lyric\s+video|hd|hq|4k)[\])]\s*$',
                 '',title,flags=re.IGNORECASE).strip()
    prefix=re.match(re.escape(artist)+r'(?:\s*[-–—:]\s*|\s+)',title,re.IGNORECASE)
    if prefix:title=title[prefix.end():].strip()
    if not title or len(title)>200 or len(artist)>200:return None
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 1<=duration<=3600:return None
    return {'artist_name':artist,'track_name':title,'duration':round(duration)}


def select_lyrics(records, query):
    """Use exact normalized artist/title; mismatched durations allow text only."""
    matches=[]
    for record in records[:20]:
        if not isinstance(record,dict):continue
        if (normalized(record.get('artistName'))!=normalized(query['artist_name'])
                or normalized(record.get('trackName'))!=normalized(query['track_name'])):continue
        duration=record.get('duration')
        if type(duration) not in (int,float) or not math.isfinite(duration):continue
        distance=abs(duration-query['duration'])
        synced=record.get('syncedLyrics');plain=record.get('plainLyrics')
        if not isinstance(synced,str) or len(synced)>65536:synced=None
        if not isinstance(plain,str) or len(plain)>65536:plain=None
        if distance>2:synced=None
        if not synced and not plain:continue
        matches.append((distance,not bool(synced),{'synced':synced,'plain':plain}))
    return min(matches,key=lambda item:item[:2])[2] if matches else {'synced':None,'plain':None}


class LyricsLookup:
    def __init__(self, cache_dir=None, *, enabled=False, opener=urlopen, clock=time.time):
        self.enabled=enabled
        self.cache_dir=Path(cache_dir) if cache_dir is not None else None
        self.opener=opener;self.clock=clock
        self.pool=None;self.pending=None
        self.entries=OrderedDict()
        self.parsed=OrderedDict()
        self.lifecycle=Lock()
        self.next_request=0
        self.closed=False

    def _request(self, endpoint, params):
        if self.clock()<self.next_request:return None
        self.next_request=self.clock()+.5
        request=Request('https://lrclib.net/api/'+endpoint+'?'+urlencode(params),
                        headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
        try:
            with self.opener(request,timeout=4) as response:
                if 'application/json' not in response.headers.get('Content-Type',''):return None
                raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:return None
            return json.loads(raw)
        except HTTPError as error:
            if error.code==404:return []
            if error.code in (429,503):
                value=(error.headers or {}).get('Retry-After','60')
                try:delay=float(value)
                except ValueError:
                    try:delay=parsedate_to_datetime(value).timestamp()-self.clock()
                    except (ValueError,TypeError,OverflowError):delay=60
                if not math.isfinite(delay):delay=60
                self.next_request=self.clock()+max(1,delay)*1.1
            return None
        except (OSError,ValueError,URLError):return None

    def _fetch(self, query):
        data=self._request('get',query)
        if data is None:return None
        records=data if isinstance(data,list) else [data]
        result=select_lyrics(records,query)
        if result['synced'] or result['plain']:return result
        # A video edit may differ from an album version. Search can still yield
        # plain lyrics for local phrase matching, without inventing its timing.
        wait=max(0,self.next_request-self.clock())
        if wait>1:return None
        if wait:time.sleep(wait)
        data=self._request('search',{k:v for k,v in query.items() if k!='duration'})
        return select_lyrics(data,query) if isinstance(data,list) else None

    def _disk_read(self, key):
        if self.cache_dir is None:return None
        try:
            with (self.cache_dir/(key+'.json')).open('rb') as stream:raw=stream.read(550001)
            if len(raw)>550000:return None
            data=json.loads(raw)
            if (not isinstance(data,dict) or type(data.get('expires')) not in (int,float)
                    or not math.isfinite(data['expires']) or data['expires']<=self.clock()):return None
            result=data.get('lyrics')
            if (not isinstance(result,dict) or set(result)!={'synced','plain'}
                    or any(v is not None and (not isinstance(v,str) or len(v)>65536) for v in result.values())):return None
            return data
        except (OSError,ValueError,RecursionError):return None

    def _finish(self):
        if self.pending is None or not self.pending[1].done():return
        key,future=self.pending;self.pending=None
        try:result=future.result()
        except Exception:result=None
        found=result is not None and bool(result.get('synced') or result.get('plain'))
        ttl=30*86400 if found else (86400 if result is not None else 300)
        entry={'expires':self.clock()+ttl,'lyrics':result or {'synced':None,'plain':None}}
        self.entries[key]=entry
        if self.cache_dir is not None:
            target=self.cache_dir/(key+'.json');temporary=target.with_suffix('.tmp')
            try:
                self.cache_dir.mkdir(parents=True,exist_ok=True)
                temporary.write_text(json.dumps(entry,ensure_ascii=False),encoding='utf-8')
                temporary.replace(target)
                files=sorted(self.cache_dir.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                for path in files[128:]:
                    if re.fullmatch(r'[0-9a-f]{64}\.json',path.name):path.unlink(missing_ok=True)
            except OSError:pass
            finally:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass
        while len(self.entries)>128:self.entries.popitem(last=False)

    def enrich(self, reading):
        if not self.enabled or self.closed or reading.state not in ('playing','paused'):return reading
        self._finish()
        source=reading.lyric_source or TrackTags(dict(reading.tags or {}))
        if source.lines or source.untimed:return reading
        fields=dict(source.fields);fields.setdefault('title',reading.title)
        query=query_identity(fields,reading.duration_seconds)
        if query is None:return reading
        key=hashlib.sha256(json.dumps(query,sort_keys=True).encode()).hexdigest()
        entry=self.entries.get(key)
        if entry is None:
            entry=self._disk_read(key)
            if entry:self.entries[key]=entry
        if entry is not None and entry['expires']>self.clock():
            parsed_key=(key,entry['expires'],tuple(sorted(source.fields.items())))
            enriched=self.parsed.get(parsed_key)
            if enriched is None:
                synced,plain=lyric_data(entry['lyrics']['synced'],entry['lyrics']['plain'])
                enriched=TrackTags(source.fields,synced,plain)
                self.parsed[parsed_key]=enriched
            self.parsed.move_to_end(parsed_key)
            while len(self.parsed)>8:self.parsed.popitem(last=False)
            return replace(reading,lyric_source=enriched,tags=enriched.at(reading.position_seconds) or reading.tags)
        if self.pending is None and self.clock()>=self.next_request:
            with self.lifecycle:
                if not self.closed:
                    if self.pool is None:self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='music-lyrics')
                    self.pending=(key,self.pool.submit(self._fetch,query))
        return reading

    def close(self):
        with self.lifecycle:
            self.closed=True
            if self.pool is not None:self.pool.shutdown(wait=False,cancel_futures=True)
