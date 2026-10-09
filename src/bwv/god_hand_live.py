"""Bounded, read-only player-hand sensor for the verified engine layout."""
from dataclasses import dataclass, replace
import hashlib
import time

from .god_hand import GodHandContext
from .held_objects import RUNTIME_HELD_KINDS, RUNTIME_HELD_DETAILS
from .object_types import OBJECT_KINDS, object_kind
from .building_types import building_kind
from .pipe import snapshot
from .spells import SPELLS
from .training_live import CREATURE_OWNER, CREATURE_MENTAL

INTERFACE_STATUS, INTERFACE_HAND = 0x39C, 0x3A0
STATUS_INTERFACE, HAND_STATUS, HAND_HELD_OBJECT = 0x2C, 0x4850, 0x4904
SEED_INFO_BASE, SEED_INFO_STRIDE, SEED_INFO_COUNT = 0xD9D678, 0x190, 30


@dataclass(frozen=True)
class GodHandSample:
    session: str
    creature_id: str | None
    observed_at: float
    context: GodHandContext


def runtime_type(reader, address):
    table = reader.word(address)
    descriptor = reader.word(reader.word(table - 4) + 12)
    name = reader.read(descriptor + 8, 64).split(b'\0', 1)[0]
    if not name.startswith(b'.?AV') or not name.endswith(b'@@'):
        raise ValueError('Invalid player-hand object type')
    return name


def read_spell(reader, address, name):
    # SpellSeed is the activated magic-hand object, not its world bubble or
    # SpellIcon dispenser. GetMagicInfo uses its seed info and power-up level.
    if name == b'.?AVSpellSeed@@':
        fields = (0x58, 0x6C)
        info, power = (reader.word(address + offset) for offset in fields)
    elif name == b'.?AVOneOffSpellSeed@@':
        fields = (0x68, 0x78)
        seed, power = (reader.word(address + offset) for offset in fields)
        if seed >= SEED_INFO_COUNT:raise ValueError('Unknown miracle bubble type')
        info = SEED_INFO_BASE + seed * SEED_INFO_STRIDE
    else:
        raise ValueError('Not a recognized held miracle')
    if (not SEED_INFO_BASE <= info < SEED_INFO_BASE + SEED_INFO_COUNT * SEED_INFO_STRIDE
            or (info - SEED_INFO_BASE) % SEED_INFO_STRIDE
            or power not in (0xFFFFFFFF, 0, 1, 2)):
        raise ValueError('Invalid held-miracle metadata')
    reader.require_type(info, b'.?AVGSpellSeedInfo@@')
    base = reader.word(info + 0x124)
    spell = base if power == 0xFFFFFFFF else reader.word(info + 0x128 + power * 4) or base
    if spell not in SPELLS:raise ValueError('Unknown held miracle')
    expected = (info, power) if name == b'.?AVSpellSeed@@' else (seed, power)
    if tuple(reader.word(address + offset) for offset in fields) != expected:
        raise ValueError('Held miracle changed while reading')
    return SPELLS[spell]


def describe_object(reader, address, name, session, creature, tick):
    errors = 0
    kind = OBJECT_KINDS.get(name) or RUNTIME_HELD_KINDS.get(name)
    if not kind:
        try:
            read_type = lambda ptr: runtime_type(reader, ptr)
            kind = building_kind(reader, address, name, read_type) or object_kind(reader, address, name, read_type)
        except (OSError, ValueError):errors += 1
    kind = kind or 'object'
    obj = {'object_id': hashlib.sha256(f'{session}:{creature}:{address}'.encode()).hexdigest()[:24],
           'kind': kind}
    if name in RUNTIME_HELD_DETAILS:obj['object_type'] = RUNTIME_HELD_DETAILS[name]
    if name == b'.?AVOneOffSpellSeed@@':
        try:obj['miracle'] = read_spell(reader, address, name)
        except (OSError, ValueError):errors += 1
    from .object_details_live import read_details
    try:
        metadata, failures = read_details(reader, address, name)
        errors += failures
        if metadata:obj['details'] = metadata
    except (OSError, ValueError):errors += 1
    if kind == 'animal':
        from .animal_types import ANIMAL_TYPES
        if name in ANIMAL_TYPES:obj['species'] = ANIMAL_TYPES[name]
    if kind == 'villager':
        from .nearby_people_live import read_person
        try:
            person, failures = read_person(reader, address, tick, name)
            errors += failures
            if person:obj['person'] = person
        except (OSError, ValueError):errors += 1
    return obj, errors


def read_god_hand(reader, observer=None):
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        game, tick, player, creature, interface = head
        reader.require_type(interface, b'.?AVGInterface@@')
        status, hand = (reader.word(interface + offset) for offset in (INTERFACE_STATUS, INTERFACE_HAND))
        reader.require_type(status, b'.?AVGInterfaceStatus@@')
        reader.require_type(hand, b'.?AVCHand@@')
        if reader.word(status + STATUS_INTERFACE) != interface or reader.word(hand + HAND_STATUS) != status:
            raise ValueError('Player-hand owner mismatch')
        identity = mental = None
        if creature:
            reader.require_type(creature, b'.?AVCreature@@')
            if reader.word(creature + CREATURE_OWNER) != player:
                raise ValueError('Player creature owner mismatch')
            mental = reader.word(creature + CREATURE_MENTAL)
            reader.require_type(mental, b'.?AVCreatureMental@@')
            identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        held = reader.word(hand + HAND_HELD_OBJECT)
        context = GodHandContext('empty')
        if held:
            table = reader.word(held)
            name = runtime_type(reader, held)
            if name == b'.?AVSpellSeed@@':
                try:context = GodHandContext('activated_miracle', activated_miracle=read_spell(reader, held, name))
                except (OSError, ValueError):
                    context = GodHandContext('activated_miracle', detail_read_errors=1)
            else:
                obj, errors = describe_object(reader, held, name, session, creature, tick)
                context = GodHandContext('holding_object', held_object=obj, detail_read_errors=errors)
            if reader.word(held) != table:continue
        final = reader._head()
        if (head[:1] + head[2:] != final[:1] + final[2:]
                or reader.word(interface + INTERFACE_STATUS) != status
                or reader.word(interface + INTERFACE_HAND) != hand
                or reader.word(status + STATUS_INTERFACE) != interface
                or reader.word(hand + HAND_STATUS) != status
                or reader.word(hand + HAND_HELD_OBJECT) != held
                or creature and (reader.word(creature + CREATURE_MENTAL) != mental
                                 or reader.word(creature + CREATURE_OWNER) != player)):
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Session changed during player-hand observation')
        now = time.monotonic()
        if now - started > .75:raise ValueError('Player-hand read exceeded freshness limit')
        return GodHandSample(session, identity, now, context)
    raise ValueError('Player-hand contents changed during observation')


def with_god_hand(observation, sample, now):
    valid = (sample.session == observation.session and sample.creature_id == observation.creature_id
             and sample.creature_id is not None and 0 <= now - sample.observed_at <= .75)
    return replace(observation, god_hand=sample.context if valid else GodHandContext())
