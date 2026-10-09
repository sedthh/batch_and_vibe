"""Read-only town links/allegiance for the verified engine layout. No game calls."""
import hashlib
import math
import struct

from .towns import TRIBES, TownBelief, TownContext
from .training_live import PLAYER_START, PLAYER_STRIDE

TOWN_OWNER=0x2C
TOWN_TRIBE=0x5B8
TOWN_BELIEF=0x798
BELIEF_VALUES=8
BELIEF_CAPS=0x68
NEUTRAL_PLAYER_INDEX=0x205A5B
PLAYER_NUMBER=0xB5
GET_TOWN_SLOT=0x48
# Verified getter addresses and data offsets. Functions are never invoked.
TOWN_LINK_GETTERS={0x401730:0x98,0x751F00:0x12C,0x528960:0x118}


class TownSampler:
    """At most eight linked towns per observation, with coherent rereads."""
    def __init__(self,reader,game,player,session):
        self.reader,self.game,self.player,self.session=reader,game,player,session
        self.cache={}
        self.failed=set()
        self.checks=[]

    def for_object(self,obj):
        r=self.reader
        table=r.word(obj)
        getter=r.word(table+GET_TOWN_SLOT)
        offset=TOWN_LINK_GETTERS.get(getter)
        if offset is None:return None
        raw=r.read(obj+offset,4);town,=struct.unpack('<I',raw)
        if not town or town in self.failed:return None
        if town not in self.cache:
            if len(self.cache)+len(self.failed)>=8:return None
            try:
                context,checks=self.read_town(town)
            except (OSError,ValueError):
                self.failed.add(town)
                raise
            self.cache[town]=context
            self.checks.extend(checks)
        self.checks.extend(((obj,struct.pack('<I',table)),(obj+offset,raw),
                            (table+GET_TOWN_SLOT,struct.pack('<I',getter))))
        return self.cache[town]

    def read_town(self,town):
        r=self.reader
        r.require_type(town,b'.?AVTown@@')
        r.require_type(town+TOWN_BELIEF,b'.?AVGBelief@@')
        checks=[]
        def read(address,size):
            raw=r.read(address,size);checks.append((address,raw));return raw
        def word(address):return struct.unpack('<I',read(address,4))[0]
        def player_number(pointer):
            index,remainder=divmod(pointer-self.game-PLAYER_START,PLAYER_STRIDE)
            if remainder or not 0<=index<8:raise ValueError('Unknown town player link')
            r.require_type(pointer,b'.?AVGPlayer@@')
            if read(pointer+PLAYER_NUMBER,1)[0]!=index:raise ValueError('Inconsistent player slot')
            return index
        read(town,4);read(town+TOWN_BELIEF,4)
        neutral=read(self.game+NEUTRAL_PLAYER_INDEX,1)[0]
        if neutral>=8:raise ValueError('Invalid neutral player slot')
        own=player_number(self.player)
        if own==neutral:raise ValueError('Selected player is neutral')
        owner_pointer=word(town+TOWN_OWNER)
        owner=player_number(owner_pointer) if owner_pointer else None
        tribe=word(town+TOWN_TRIBE)
        # Belief changes during normal play. Sample the numbers once; only
        # identity, membership and allegiance must survive the coherence check.
        values=struct.unpack('<8f',r.read(town+TOWN_BELIEF+BELIEF_VALUES,32))
        caps=struct.unpack('<8f',r.read(town+TOWN_BELIEF+BELIEF_CAPS,32))
        if any(not math.isfinite(v) or not 0<=v<=1e9 for v in (*values,*caps)):
            raise ValueError('Invalid town belief data')
        beliefs=[]
        for slot,value in enumerate(values):
            # Every player slot can hold initialized belief, even when no god
            # occupies it on this island. A valid GPlayer object is not evidence
            # of participation. Report only the selected player and this town's
            # actual owner until a verified island roster is available.
            if slot==neutral or slot not in (own,owner):continue
            player_number(self.game+PLAYER_START+slot*PLAYER_STRIDE)
            beliefs.append(TownBelief(f'god_{slot}','player_god' if slot==own else 'other_god',value))
        control=('unknown' if owner is None else 'unclaimed' if owner==neutral else
                 'player_god' if owner==own else 'other_god')
        # The non-owner branch of GetBeliefNeededToConvert reads current-owner
        # belief. Matching this reference does not itself establish conversion.
        reference=values[owner] if owner is not None and owner!=own else None
        identity=hashlib.sha256(f'{self.session}:town:{town}'.encode()).hexdigest()[:24]
        return TownContext(identity,TRIBES[tribe] if tribe<len(TRIBES) else None,
            control,f'god_{owner}' if owner is not None and owner!=neutral else None,
            tuple(beliefs),values[neutral],caps[own],reference,
            max(0,reference-values[own]) if reference is not None else None),checks
