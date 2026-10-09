"""Bounded creature-belief traversal and current proximity, without game calls."""
from .object_types import OBJECT_KINDS, object_kind
from .object_fire import read_object_fire
from dataclasses import dataclass
import hashlib
import math
import struct
import time

from .pipe import snapshot
from .positions import POSITION_KINDS, position
from .surroundings import NearbyObject, SurroundingsContext, SUMMARY_RADIUS
from .animal_types import ANIMAL_TYPES
from .building_types import building_kind
from .training_live import TrainingReader, CREATURE_MENTAL, CREATURE_OWNER
from .towns_live import TownSampler
from .creature_live import read_object_species
from .nearby_creatures_live import read_body_radius, read_physical_traits
from .nearby_people_live import read_person
from .object_details_live import read_details
from .object_details import lookup_order
from .vortex_live import read_portals

BELIEFS, LISTS = 0x2208, (0x2210, 0x2224)
POSITION_SCALE = 10 / 65536
BELIEF_TYPES = {b'.?AVCreatureBeliefSmall@@', b'.?AVCreatureBelief@@'} | {
    f'.?AVCreatureBeliefAbout{name}@@'.encode() for name in
    ('Abode','Citadel','Creature','Flock','Forest','MobileObject','Town','Villager')}
SCAN_SECONDS = .75


def select_objects(objects, distance_key=None):
    """Bound exported detail, without letting early trees crowd out creatures."""
    if len(objects) <= 1024:
        return objects
    def order(obj):
        priority, distance, identity = lookup_order(vars(obj), distance_key or 'distance_engine_units')
        if distance_key is None and obj.distance_to_god_hand_engine_units is not None:
            distance = min(distance, obj.distance_to_god_hand_engine_units)
        return priority, distance, identity
    return sorted(objects, key=order)[:1024]


class SurroundingsReadError(ValueError):
    """Fixed diagnostic codes, without exception text or local paths."""
    def __init__(self, reason):
        self.reason = reason if reason in {
            'owner_changed', 'invalid_creature_position',
            'invalid_belief_list', 'belief_links_changed', 'invalid_object_position',
            'belief_count_changed', 'session_changed', 'freshness_limit',
            'identity_or_beliefs_changed',
        } else 'read_unavailable'
        super().__init__(self.reason)


@dataclass(frozen=True)
class SurroundingsSample:
    session: str
    creature_id: str | None
    observed_at: float
    context: SurroundingsContext


def hand_position(reader,game,interface):
    reader.require_type(interface,b'.?AVGInterface@@')
    status=reader.word(interface+0x39C)
    reader.require_type(status,b'.?AVGInterfaceStatus@@')
    raw=reader.read(status+0xC8,12)
    x,y,z=struct.unpack('<fff',raw)
    width,depth=reader.word(game+0x59C8),reader.word(game+0x59C4)
    if (not all(math.isfinite(v) for v in (x,y,z)) or not 0<width<=1024
            or not 0<depth<=1024 or not 0<=x<width*10 or not 0<=z<depth*10):
        raise ValueError('Invalid god hand position')
    # Motion is expected during a read-only scan. Validate the hand identity,
    # not that the player's pointer stayed perfectly still throughout it.
    return x,z,[(interface+0x39C,struct.pack('<I',status))]


def read_surroundings(reader: TrainingReader, observer=None) -> SurroundingsSample:
    reader.ensure_running()
    started = time.monotonic()
    def check_time():
        if time.monotonic() - started > SCAN_SECONDS:
            raise SurroundingsReadError('freshness_limit')
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    type_cache = {}
    def kind(address):
        table = reader.word(address)
        if table not in type_cache:
            locator = reader.word(table-4)
            descriptor = reader.word(locator+12)
            type_cache[table] = reader.read(descriptor+8,64).split(b'\0',1)[0]
        return type_cache[table]

    for _ in range(3):
        head = reader._head()
        game,tick,player,creature,hand = head
        if not creature:
            return SurroundingsSample(session,None,time.monotonic(),SurroundingsContext())
        reader.require_type(creature,b'.?AVCreature@@')
        if reader.word(creature+CREATURE_OWNER) != player:
            raise SurroundingsReadError('owner_changed')
        mental = reader.word(creature+CREATURE_MENTAL)
        reader.require_type(mental,b'.?AVCreatureMental@@')
        reader.require_type(mental+BELIEFS,b'.?AVCreatureBeliefs@@')
        creature_pos = reader.read(creature+0x14,12)
        cx,cz,alt = struct.unpack('<iif',creature_pos)
        if not math.isfinite(alt):
            raise SurroundingsReadError('invalid_creature_position')
        # Positions are recent measurements, not a transaction over a frozen
        # world. Requiring every villager (and our creature) to remain still
        # until this entire scan finishes starves observations in busy towns.
        # Keep identity/list checks and the scan's freshness bound instead.
        checks, objects, object_ids = [], [], set()
        hand_checks,hand_objects=[],[]
        known_objects=[]
        detail_errors=0
        hand_errors=0
        try:hx,hz,hand_checks=hand_position(reader,game,hand)
        except (OSError,ValueError):hand_errors=1
        scanned = 0
        towns=TownSampler(reader,game,player,session)
        town_errors=0
        fire_errors=0
        creature_errors=0
        person_errors=0
        creature_details=0
        own_radius=None
        for offset in LISTS:
            address = mental+offset
            reader.require_type(address,b'.?AVCreatureBeliefList@@')
            header = reader.read(address+8,12)
            node,tail,count = struct.unpack('<III',header)
            checks.append((address+8,header))
            seen,previous = set(),0
            # Beliefs grow as the creature explores an island. Bound work by
            # elapsed time and validate the linked list, not an assumed town size.
            for index in range(count):
                if index % 32 == 0:
                    check_time()
                if not node or node in seen or kind(node) not in BELIEF_TYPES:
                    raise SurroundingsReadError('invalid_belief_list')
                seen.add(node)
                links = reader.read(node+8,8)
                following,back = struct.unpack('<II',links)
                if back != previous:
                    raise SurroundingsReadError('belief_links_changed')
                obj_raw = reader.read(node+0x30,4)
                obj, = struct.unpack('<I',obj_raw)
                checks.extend(((node+8,links),(node+0x30,obj_raw)))
                if obj and obj != creature and obj not in object_ids:
                    object_ids.add(obj)
                    object_type = reader.read(obj,4)
                    runtime_type = kind(obj)
                    obj_kind = OBJECT_KINDS.get(runtime_type) or object_kind(reader, obj, runtime_type, kind) or building_kind(reader, obj, runtime_type, kind)
                    if obj_kind:
                        pos = reader.read(obj+0x14,12)
                        x,z,y = struct.unpack('<iif',pos)
                        if not math.isfinite(y):
                            raise SurroundingsReadError('invalid_object_position')
                        checks.append((obj,object_type))
                        distance = math.hypot(x-cx,z-cz)*POSITION_SCALE
                        hand_distance=math.hypot(x*POSITION_SCALE-hx,z*POSITION_SCALE-hz) if hand_checks else None
                        if distance <= 20000:
                            detailed = distance <= 360 or (hand_distance is not None and hand_distance <= 360)
                            on_fire=None
                            if detailed:
                                try:on_fire=read_object_fire(reader,obj)
                                except (OSError,ValueError):fire_errors+=1
                            identity = hashlib.sha256(f'{session}:{creature}:{obj}'.encode()).hexdigest()[:24]
                            species=ANIMAL_TYPES.get(runtime_type) if obj_kind=='animal' else None
                            traits=None
                            owner_id=owner_link=None
                            person=None
                            metadata=None
                            if detailed:
                                try:
                                    metadata,errors=read_details(reader,obj,runtime_type)
                                    detail_errors+=errors
                                except (OSError,ValueError):detail_errors+=1
                            if obj_kind=='villager' and detailed:
                                try:
                                    person,errors=read_person(reader,obj,tick,runtime_type)
                                    person_errors+=errors
                                except (OSError,ValueError):person_errors+=1
                            if obj_kind=='creature':
                                from .island_live import creature_ownership
                                try:owner_id,owner_link=creature_ownership(reader,game,obj)
                                except (OSError,ValueError):creature_errors+=1
                                try:species=read_object_species(reader,obj)
                                except (OSError,ValueError):creature_errors+=1
                                if creature_details==0:
                                    try:own_radius=read_body_radius(reader,creature)
                                    except (OSError,ValueError):creature_errors+=1
                                if detailed:
                                    try:
                                        traits,errors=read_physical_traits(reader,obj,own_radius)
                                        creature_errors+=errors
                                    except (OSError,ValueError):creature_errors+=1
                                creature_details+=1
                            town=None
                            # The verified getter identifies its layout; do not
                            # hide an inherited town link behind a kind allowlist.
                            try:town=towns.for_object(obj)
                            except (OSError,ValueError):town_errors+=1
                            item=NearbyObject(identity,obj_kind,round(distance,1),
                                town.town_id if town else None,on_fire,
                                round(hand_distance,1) if hand_distance is not None else None,
                                position({'x':x*POSITION_SCALE,'z':z*POSITION_SCALE}) if detailed and obj_kind in POSITION_KINDS else None,
                                species,traits or None,person or None,metadata or None,obj,
                                owner_god_id=owner_id,owner_link=owner_link)
                            known_objects.append(item)
                            if distance<=SUMMARY_RADIUS:objects.append(item)
                            if hand_distance is not None and hand_distance<=SUMMARY_RADIUS:hand_objects.append(item)
                previous,node = node,following
            if node or previous != tail:
                raise SurroundingsReadError('belief_count_changed')
            scanned += count
        # Story entrances need not appear in creature beliefs. Read the small
        # engine-owned portal lists, then apply the same proximity radii.
        portals, portal_errors = read_portals(reader, session, creature, player,
            (cx * POSITION_SCALE, cz * POSITION_SCALE),
            (hx, hz) if hand_checks else None, kind)
        known_objects = select_objects(portals + known_objects)
        objects = select_objects([o for o in portals if o.distance_engine_units <= SUMMARY_RADIUS] + objects,
                                 'distance_engine_units')
        hand_objects = select_objects([o for o in portals if o.distance_to_god_hand_engine_units is not None
            and o.distance_to_god_hand_engine_units <= SUMMARY_RADIUS] + hand_objects,
            'distance_to_god_hand_engine_units')
        # A changing allegiance must not discard unrelated nearby objects.
        try:
            towns_coherent=all(reader.read(a,len(b))==b for a,b in towns.checks)
        except (OSError,ValueError):towns_coherent=False
        if not towns_coherent:
            from dataclasses import replace
            objects=[replace(obj,town_id=None) for obj in objects]
            hand_objects=[replace(obj,town_id=None) for obj in hand_objects]
            known_objects=[replace(obj,town_id=None) for obj in known_objects]
            towns.cache.clear();town_errors+=1
        try:hand_coherent=bool(hand_checks) and all(reader.read(a,len(b))==b for a,b in hand_checks)
        except OSError:hand_coherent=False
        if hand_checks and not hand_coherent:
            from dataclasses import replace
            hand_objects=[];hand_errors+=1
            objects=[replace(obj,distance_to_god_hand_engine_units=None) for obj in objects]
            known_objects=[replace(obj,distance_to_god_hand_engine_units=None) for obj in known_objects]
        final_head=reader._head()
        coherent = True
        for index, (address, expected) in enumerate(checks):
            if index % 32 == 0:
                check_time()
            if reader.read(address, len(expected)) != expected:
                coherent = False
                break
        if (reader.word(creature+CREATURE_OWNER) != player
                or reader.word(creature+CREATURE_MENTAL) != mental
                or not coherent
                or final_head[:1]+final_head[2:] != head[:1]+head[2:]):
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise SurroundingsReadError('session_changed')
        now = time.monotonic()
        if now-started > SCAN_SECONDS:
            raise SurroundingsReadError('freshness_limit')
        objects.sort(key=lambda o:(o.distance_engine_units,o.object_id))
        hand_objects.sort(key=lambda o:(o.distance_to_god_hand_engine_units,o.object_id))
        identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        return SurroundingsSample(session,identity,now,
            SurroundingsContext(True,tuple(objects),SUMMARY_RADIUS,scanned,len(objects),
                                towns=tuple(towns.cache.values()),town_read_errors=town_errors,
                                fire_read_errors=fire_errors,god_hand_available=hand_coherent,
                                god_hand_objects=tuple(hand_objects),hand_read_errors=hand_errors,
                                creature_read_errors=creature_errors,person_read_errors=person_errors,
                                known_objects=tuple(known_objects),detail_read_errors=detail_errors,
                                portal_read_errors=portal_errors))
    raise SurroundingsReadError('identity_or_beliefs_changed')
