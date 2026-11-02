"""Local observed starts; model context receives a label, never a play count."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


LISTENING_LABELS = ('new','rare','occasional','regular','favorite')


def listening_label(count):
    for threshold,label in ((25,'favorite'),(10,'regular'),
                            (4,'occasional'),(2,'rare'),(1,'new')):
        if count >= threshold:return label
    return 'new'


def song_key(filename=None, title=None, artist=None):
    if filename:
        value='file:'+str(filename).replace('/','\\').casefold()
    elif title:
        value='label:'+str(artist or '').casefold()+'\0'+title.casefold()
    else:return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class ListeningHistory:
    MAX_SONGS = 4096
    RECENT_STARTS = 10

    def __init__(self, path=None):
        self.path=Path(path) if path is not None else None
        self.counts={}
        self.active=None
        self.recent=[]
        self.previous=None
        self.loaded=False
        self.writable=True
        self.error=None

    def break_continuity(self):
        self.previous=None

    @property
    def on_repeat(self):
        return self.active is not None and self.recent.count(self.active)>=3

    def _load(self):
        self.loaded=True
        if self.path is None:return
        try:
            with self.path.open('rb') as stream:raw=stream.read(512001)
            if len(raw)>512000:raise ValueError('Oversized history')
            data=json.loads(raw)
            def valid_key(key):
                return isinstance(key,str) and len(key)==64 and all(c in '0123456789abcdef' for c in key)
            if (not isinstance(data,dict) or data.get('version') not in (1,2)
                    or set(data)!=({'version','counts','active'} | ({'recent'} if data['version']==2 else set()))
                    or not isinstance(data['counts'],dict)
                    or len(data['counts'])>self.MAX_SONGS
                    or any(not valid_key(key) or type(count) is not int or not 1<=count<=1000000
                           for key,count in data['counts'].items())
                    or data['active'] is not None and not valid_key(data['active'])
                    or data['version']==2 and (not isinstance(data['recent'],list)
                        or len(data['recent'])>self.RECENT_STARTS
                        or any(not valid_key(key) for key in data['recent']))):
                raise ValueError('Invalid history')
            self.counts=data['counts'];self.active=data['active']
            self.recent=data.get('recent',[])
        except FileNotFoundError:pass
        except (OSError,ValueError,RecursionError):
            self.error='load_failed_original_preserved';self.writable=False

    def _save(self):
        if self.path is None or not self.writable:return
        temporary=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            payload=json.dumps({'version':2,'counts':self.counts,'active':self.active,
                                'recent':self.recent},separators=(',',':'))
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path.parent,
                                             prefix=self.path.name+'.',suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name);stream.write(payload);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            self.error=None
        except OSError:self.error='save_failed_previous_file_preserved'
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def observe(self, reading, now):
        if not self.loaded:self._load()
        if reading.state not in ('playing','paused'):
            self.previous=None
            if reading.state in ('stopped','not_running') and self.active is not None:
                self.active=None;self._save()
            return None
        key=reading.song_key or song_key(title=reading.title,artist=(reading.tags or {}).get('artist'))
        if key is None:
            self.previous=None
            return None
        position=reading.position_seconds
        valid_position=type(position) in (int,float) and math.isfinite(position) and position>=0
        near_start=valid_position and position<=5
        previous=self.previous
        continuous=previous is not None and 0<=now-previous[0]<=3
        start=reading.state=='playing' and near_start and key!=self.active
        metadata_change=False
        if previous is not None and previous[1]!=key:
            old_identity=previous[5]
            identity=reading.identity
            metadata_change=(old_identity is not None and identity is not None
                             and len(old_identity)==len(identity)==5
                             and old_identity[:4]==identity[:4]
                             and (old_identity[4] is None or identity[4] is None))
            if metadata_change:
                # A filename permission/read recovery is not a new song start.
                count=max(self.counts.get(previous[1],0),self.counts.get(key,0))
                if count:self.counts[key]=count
                self.recent=[key if item==previous[1] else item for item in self.recent]
                start=False
        if continuous and previous[1]==key:
            _,_,state,old_position,duration,identity=previous
            # Same-song restart at a different playlist slot, or an observed end
            # wrapping to its beginning. Ordinary backwards seeks do not count.
            if (reading.state==state=='playing' and near_start
                    and type(old_position) in (int,float) and old_position>position+2):
                moved_slot=identity is not None and reading.identity is not None and identity[:3]!=reading.identity[:3]
                wrapped=(type(duration) in (int,float) and duration>5
                         and duration-3<=old_position<=duration
                         and abs(old_position+(now-previous[0])-duration-position)<=1.5)
                start = start or moved_slot or wrapped
        if start:
            count=min(1000000,self.counts.pop(key,0)+1)
            self.counts[key]=count
            self.recent.append(key)
            self.recent=self.recent[-self.RECENT_STARTS:]
        while len(self.counts)>self.MAX_SONGS:self.counts.pop(next(iter(self.counts)))
        changed=key!=self.active
        self.active=key
        self.previous=(now,key,reading.state,position,reading.duration_seconds,reading.identity)
        if start or changed:self._save()
        return listening_label(self.counts.get(key,0))
