"""Exact-build body and native plan sampling; no calls into the game."""
from .object_types import object_kind
import hashlib
import struct
import time

from .experience import BodyState, NativeInstinct, ExperienceSample
from .conditions_live import read_fire
from .holding_live import CREATURE_PHYSICAL, MENTAL_PLAN, PLAN_ACTION
from .training_live import CREATURE_OWNER, CREATURE_MENTAL
from .pipe import snapshot
from .surroundings_live import OBJECT_KINDS, BELIEF_TYPES
from .building_types import building_kind

HEALTH = 0x48
ACTION_INFO = 0xC6C490
ACTION_STRIDE = 0x110
ACTION_NAME = 0x24
PHYSICAL_STATS = {'warmth':0x10,'fatness':0x14,'energy':0x1C,
                  'itchiness':0x20,'exhaustion':0x30,'dehydration':0x34,'strength':0xC}


def read_plan_target(reader, mental, session, creature):
    """The primary plan belief describes an intended target, not an impact."""
    def runtime_type(obj):
        table = reader.word(obj)
        descriptor = reader.word(reader.word(table-4)+12)
        return reader.read(descriptor+8,64).split(b'\0',1)[0]
    address = mental + MENTAL_PLAN + 0x10
    belief = reader.word(address)
    if not belief or runtime_type(belief) not in BELIEF_TYPES:
        return None, None, []
    obj = reader.word(belief+0x30)
    if not obj:
        return None, None, []
    name = runtime_type(obj)
    kind = OBJECT_KINDS.get(name) or object_kind(reader,obj,name,runtime_type) or building_kind(reader,obj,name,runtime_type)
    if not kind:
        return None, None, []
    identity = hashlib.sha256(f'{session}:{creature}:{obj}'.encode()).hexdigest()[:24]
    return kind, identity, [(address,struct.pack('<I',belief)), (belief+0x30,struct.pack('<I',obj)),
                            (obj,reader.read(obj,4))]


def read_experience(reader,observer=None):
    reader.ensure_running()
    start = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        _,tick,player,creature,_ = head
        if not creature:
            raise ValueError('No creature for experience observation')
        reader.require_type(creature,b'.?AVCreature@@')
        if reader.word(creature+CREATURE_OWNER) != player:
            raise ValueError('Creature owner mismatch')
        mental = reader.word(creature+CREATURE_MENTAL)
        physical = reader.word(creature+CREATURE_PHYSICAL)
        reader.require_type(mental,b'.?AVCreatureMental@@')
        reader.require_type(physical,b'.?AVCreaturePhysical@@')
        reader.require_type(mental+MENTAL_PLAN,b'.?AVCreaturePlan@@')
        fields = {'health':creature+HEALTH,**{k:physical+v for k,v in PHYSICAL_STATS.items() if k!='strength'}}
        checks = [(address,reader.read(address,4)) for address in fields.values()]
        values = {name:struct.unpack('<f',raw)[0] for name,(_,raw) in zip(fields,checks)}
        try:
            raw=reader.read(physical+0xC,4)
            value,=struct.unpack('<f',raw)
            import math
            if math.isfinite(value) and 0<=value<=1e9:
                values['strength']=value;checks.append((physical+0xC,raw))
        except (OSError,ValueError):pass
        plan = reader.word(mental+MENTAL_PLAN+PLAN_ACTION)
        instinct = NativeInstinct()
        if 0 <= plan < 328:
            info = ACTION_INFO+plan*ACTION_STRIDE
            reader.require_type(info,b'.?AVCreatureActionInfo@@')
            raw_name = reader.read(info+ACTION_NAME,32)
            label,separator,_ = raw_name.partition(b'\0')
            label = label.decode('ascii').strip()
            target_kind = target_id = None
            try:
                target_kind, target_id, target_checks = read_plan_target(reader, mental, session, creature)
                checks.extend(target_checks)
            except (OSError, ValueError):
                pass  # Optional target must not erase body or current-plan readings.
            instinct = NativeInstinct(plan,label or None,not bool(separator),
                                      target_kind=target_kind,target_id=target_id)
            checks.append((info+ACTION_NAME,raw_name))
        fire = read_fire(reader,observer)
        identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        if fire.session != session or fire.creature_id != identity:
            raise ValueError('Creature changed during fire observation')
        body = BodyState(**values,on_fire=fire.on_fire)
        if (reader.word(creature+CREATURE_OWNER)!=player or reader.word(creature+CREATURE_MENTAL)!=mental
                or reader.word(creature+CREATURE_PHYSICAL)!=physical
                or reader.word(mental+MENTAL_PLAN+PLAN_ACTION)!=plan
                or any(reader.read(address,len(raw))!=raw for address,raw in checks) or reader._head()!=head):
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Session changed during experience read')
        now=time.monotonic()
        if now-start>.75:
            raise ValueError('Experience observation exceeded freshness limit')
        return ExperienceSample(session,identity,now,tick,body,instinct)
    raise ValueError('Experience changed during observation')
