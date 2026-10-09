"""Exact-build, read-only creature-hand observations with coherent identity checks."""
from dataclasses import dataclass, replace
import hashlib
import struct
import time

from .control import ControlObservation
from .holding import HoldingContext
from .object_types import object_kind
from .held_objects import RUNTIME_HELD_KINDS, RUNTIME_HELD_DETAILS
from .drives import DRIVES, CreatureDrive
from .pipe import snapshot
from .training_live import TrainingReader, CREATURE_OWNER, CREATURE_MENTAL

CREATURE_PHYSICAL = 0x160
PHYSICAL_HELD_OBJECT = 0x28
MENTAL_PLAN = 0xF48
PLAN_ACTION = 0x18
MENTAL_DESIRES = 8
HUNGER_DESIRE = 0x148 + 4 * 4
DESIRE_VALUES = 0x148
KINDS = RUNTIME_HELD_KINDS
# Exact-build action IDs corroborated by runtime action names and labelled play.
PLANS = {11: 'eat', 142: 'take_from_player', 249: 'plant_tree', 275: 'examine_held_object'}
# Exact-build action names and their spell metadata agree for these cast plans.
# Excludes general tasks (e.g. help build a house) even if they reference a spell.
MIRACLE_PLANS = {
    45: 'lightning', 46: 'fireball', 47: 'explosion', 48: 'food', 49: 'forest',
    54: 'shield', 58: 'heal', 95: 'lightning_storm', 96: 'fireball_power_up_1',
    97: 'fireball_power_up_2', 98: 'food_power_up_1', 99: 'lightning_power_up_1',
    100: 'lightning_power_up_2', 101: 'heal', 102: 'heal_power_up_1',
    103: 'tornado', 104: 'wood', 105: 'creature_freeze', 106: 'creature_small',
    107: 'creature_big', 108: 'creature_weak', 109: 'creature_strong',
    110: 'creature_fat', 111: 'creature_thin', 112: 'creature_invisible',
    113: 'creature_compassion', 114: 'creature_angry', 115: 'creature_hungry',
    116: 'creature_frightened', 117: 'creature_tired', 118: 'creature_ill',
    119: 'creature_thirsty', 120: 'creature_itchy', 182: 'teleport', 183: 'shield',
    184: 'physical_shield', 185: 'explosion_power_up_1', 186: 'explosion_power_up_2',
    196: 'teleport', 238: 'water', 239: 'water', 240: 'water', 246: 'food',
    248: 'wood', 286: 'shield', 304: 'water', 305: 'water',
    325: 'water_power_up_1', 326: 'water', 327: 'water_power_up_1',
}


@dataclass(frozen=True)
class HoldingSample:
    session: str
    creature_id: str | None
    observed_at: float
    context: HoldingContext


def read_holding(reader: TrainingReader, observer: dict | None = None) -> HoldingSample:
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        game, tick, player, creature, hand = head
        identity, context = None, HoldingContext()
        if creature:
            reader.require_type(creature, b'.?AVCreature@@')
            if reader.word(creature + CREATURE_OWNER) != player:
                raise ValueError('Selected creature owner mismatch')
            physical = reader.word(creature + CREATURE_PHYSICAL)
            mental = reader.word(creature + CREATURE_MENTAL)
            reader.require_type(physical, b'.?AVCreaturePhysical@@')
            reader.require_type(mental, b'.?AVCreatureMental@@')
            reader.require_type(mental + MENTAL_PLAN, b'.?AVCreaturePlan@@')
            reader.require_type(mental + MENTAL_DESIRES, b'.?AVCreatureDesires@@')
            held = reader.word(physical + PHYSICAL_HELD_OBJECT)
            action = reader.word(mental + MENTAL_PLAN + PLAN_ACTION)
            desire_raw = reader.read(mental + MENTAL_DESIRES + DESIRE_VALUES, len(DRIVES)*4)
            values = struct.unpack('<40f', desire_raw)
            drives = tuple(CreatureDrive(name,value) for name,value in zip(DRIVES,values))
            desire = values[4]
            item_id = kind = object_type = None
            object_id=metadata=None
            species=person=None
            detail_errors=0
            if held:
                table = reader.word(held)
                descriptor = reader.word(reader.word(table - 4) + 12)
                name = reader.read(descriptor + 8, 64).split(b'\0', 1)[0]
                if not name.startswith(b'.?AV') or not name.endswith(b'@@'):
                    raise ValueError('Invalid held-object runtime type metadata')
                def runtime_type(address):
                    table = reader.word(address)
                    descriptor = reader.word(reader.word(table - 4) + 12)
                    return reader.read(descriptor + 8, 64).split(b'\0', 1)[0]
                kind = KINDS.get(name) or object_kind(reader, held, name, runtime_type) or 'object'
                object_type = RUNTIME_HELD_DETAILS.get(name)
                item_id = hashlib.sha256(f'{session}:{creature}:held:{held}'.encode()).hexdigest()[:24]
                object_id=hashlib.sha256(f'{session}:{creature}:{held}'.encode()).hexdigest()[:24]
                from .object_details_live import read_details
                try:metadata,detail_errors=read_details(reader,held,name)
                except (OSError,ValueError):detail_errors+=1
                if kind=='animal':
                    from .animal_types import ANIMAL_TYPES
                    species=ANIMAL_TYPES.get(name)
                if kind=='villager':
                    from .nearby_people_live import read_person
                    try:
                        person,errors=read_person(reader,held,tick,name)
                        detail_errors+=errors
                    except (OSError,ValueError):detail_errors+=1
                if reader.word(held) != table:
                    continue
            if (reader.word(physical + PHYSICAL_HELD_OBJECT) != held
                    or reader.word(mental + MENTAL_PLAN + PLAN_ACTION) != action
                    or reader.read(mental + MENTAL_DESIRES + DESIRE_VALUES, len(DRIVES)*4) != desire_raw
                    or reader.word(creature + CREATURE_PHYSICAL) != physical
                    or reader.word(creature + CREATURE_MENTAL) != mental
                    or reader.word(creature + CREATURE_OWNER) != player):
                continue
            context = HoldingContext(bool(held), item_id, kind, PLANS.get(action), desire,
                                     MIRACLE_PLANS.get(action), drives, object_type,object_id,metadata or None,detail_errors,
                                     species,person or None)
            identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        final_head=reader._head()
        if head[:1]+head[2:] != final_head[:1]+final_head[2:]:
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Observer session changed during held-object read')
        now = time.monotonic()
        if now-started > .75:
            raise ValueError('Held-object read exceeded freshness limit')
        return HoldingSample(session, identity, now, context)
    raise ValueError('Held-object fields changed during observation')


def with_holding(observation: ControlObservation, sample: HoldingSample, now: float) -> ControlObservation:
    if (sample.session != observation.session or not 0 <= now-sample.observed_at <= .75
            or sample.creature_id is None or (observation.creature_id is not None
                and observation.creature_id != sample.creature_id)):
        return replace(observation, holding=HoldingContext())
    return replace(observation, creature_id=sample.creature_id, holding=sample.context)
