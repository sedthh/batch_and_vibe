"""Bounded read-only leash facts for the fingerprint supported by TrainingReader."""
from dataclasses import dataclass, replace
import hashlib
import time

from .control import ControlObservation
from .leash import LeashContext
from .object_types import OBJECT_KINDS, object_kind
from .building_types import building_kind
from .pipe import snapshot
from .training_live import TrainingReader, CREATURE_MENTAL, CREATURE_OWNER

HAND_INTERFACE = 0x39C
INTERFACE_LEASH = 0x12C
LEASH_ACTIVE, LEASH_VARIANT, LEASH_TARGET = 0x14, 0x1C, 0x24
VARIANTS = {1: "aggression", 2: "learning", 3: "compassion"}

def target_type(reader, address):
    table = reader.word(address)
    descriptor = reader.word(reader.word(table - 4) + 12)
    name = reader.read(descriptor + 8, 64).split(b'\0', 1)[0]
    if not name.startswith(b'.?AV') or not name.endswith(b'@@'):
        raise ValueError('Unexpected leash target type metadata')
    return name


@dataclass(frozen=True)
class LeashSample:
    session: str
    creature_id: str | None
    observed_at: float
    context: LeashContext


def read_leash(reader: TrainingReader, observer: dict | None = None) -> LeashSample:
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        game, tick, player, creature, hand = head
        identity, context = None, LeashContext()
        if creature:
            reader.require_type(creature, b'.?AVCreature@@')
            if reader.word(creature + CREATURE_OWNER) != player:
                raise ValueError('Selected creature owner mismatch')
            mental = reader.word(creature + CREATURE_MENTAL)
            reader.require_type(mental, b'.?AVCreatureMental@@')
            reader.require_type(hand, b'.?AVGInterface@@')
            interface = reader.word(hand + HAND_INTERFACE)
            reader.require_type(interface, b'.?AVGInterfaceStatus@@')
            leash = reader.word(interface + INTERFACE_LEASH)
            reader.require_type(leash, b'.?AVGLeashStatus@@')
            offsets = (LEASH_ACTIVE, LEASH_VARIANT, LEASH_TARGET)
            values = tuple(reader.word(leash + offset) for offset in offsets)
            active, variant, target = values
            if active not in (0, 1):
                raise ValueError('Unknown leash activity value')
            if not active:
                # Engine retains the selected variant after unleashing.
                context = LeashContext(False, attachment='none')
            else:
                target_id = target_kind = None
                if target:
                    # Validate bounded RTTI; unknown classes stay undescribed.
                    table = reader.word(target)
                    name = target_type(reader, target)
                    target_kind = OBJECT_KINDS.get(name)
                    if not target_kind:
                        read_type = lambda ptr: target_type(reader, ptr)
                        try:
                            target_kind = (building_kind(reader, target, name, read_type)
                                or object_kind(reader, target, name, read_type))
                        except (OSError, ValueError):
                            pass  # Optional subtype unavailable; preserve the verified attachment.
                    target_kind = target_kind or 'object'
                    # Same public identity as world inspection and player-held objects.
                    target_id = hashlib.sha256(f'{session}:{creature}:{target}'.encode()).hexdigest()[:24]
                    if reader.word(target) != table:
                        continue
                context = LeashContext(True, VARIANTS.get(variant),
                    'object' if target else 'hand', target_id, target_kind)
            if (values != tuple(reader.word(leash + offset) for offset in offsets)
                    or reader.word(hand + HAND_INTERFACE) != interface
                    or reader.word(interface + INTERFACE_LEASH) != leash
                    or reader.word(creature + CREATURE_MENTAL) != mental
                    or reader.word(creature + CREATURE_OWNER) != player):
                continue
            identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        final_head = reader._head()
        if final_head[:1] + final_head[2:] != head[:1] + head[2:]:
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Observer session changed during leash read')
        now = time.monotonic()
        if now - started > .75:
            raise ValueError('Leash read exceeded freshness limit')
        return LeashSample(session, identity, now, context)
    raise ValueError('Leash fields changed during observation')


def with_leash(observation: ControlObservation, sample: LeashSample, now: float) -> ControlObservation:
    # Never retain a previous creature's attachment or refresh the VM source age.
    if (sample.session != observation.session or not 0 <= now - sample.observed_at <= .75
            or sample.creature_id is None or (observation.creature_id is not None
                and sample.creature_id != observation.creature_id)):
        return replace(observation, leash=LeashContext())
    return replace(observation, creature_id=sample.creature_id, leash=sample.context)
