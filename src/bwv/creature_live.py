"""Read current species from typed exact-build CreatureInfo; no engine calls."""
from dataclasses import dataclass, replace
import hashlib
import time
import struct
import math

from .creature import CreatureContext, CreatureTrait
from .profile_names import read_profile_names
from .pipe import snapshot
from .training_live import TrainingReader, CREATURE_MENTAL, CREATURE_OWNER

CREATURE_INFO = 0x28
INFO_NAME = 0x18
# Same ownership fields as the native command bridge. This is script control,
# not a combat flag; ordinary combat must not be classified from a plan name.
CREATURE_FLAGS, SCRIPT_OWNED_MASK, CREATURE_SCRIPT = 0x24, 0x400, 0x10C0
# Bounded metadata labels inspected in the local installation, not game assets.
SPECIES = {b'Ape creature': 'monkey', b'Cow creature': 'cow', b'Tiger creature': 'tiger',
    b'Leopard creature': 'leopard', b'Wolf creature': 'wolf', b'Lion creature': 'lion',
    b'Horse creature': 'horse', b'Tortoise creature': 'tortoise', b'Zebra creature': 'zebra',
    b'Bear creature': 'bear', b'Polar bear creature': 'polar bear',
    b'Nice sheep creature': 'sheep', b'Chimp creature': 'chimp',
    b'Ogre creature': 'ogre', b'Mandrill creature': 'mandrill'}


class CreatureReadError(ValueError):
    """Fixed diagnostic reason safe to expose without paths or profile data."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def read_object_species(reader, creature):
    """Species of any typed creature, without assuming player ownership."""
    reader.require_type(creature,b'.?AVCreature@@')
    info=reader.word(creature+CREATURE_INFO)
    reader.require_type(info,b'.?AVCreatureInfo@@')
    raw=reader.read(info+INFO_NAME,48)
    name,separator,_=raw.partition(b'\0')
    if (reader.word(creature+CREATURE_INFO)!=info
            or reader.read(info+INFO_NAME,48)!=raw):
        raise CreatureReadError('identity_or_species_changed')
    return SPECIES.get(name) if separator else None


@dataclass(frozen=True)
class CreatureSample:
    session: str
    creature_id: str | None
    observed_at: float
    context: CreatureContext
    script_owns_creature: bool | None = None


def _script_owns_creature(reader, creature):
    flags = int.from_bytes(reader.read(creature + CREATURE_FLAGS, 2), 'little')
    return bool(flags & SCRIPT_OWNED_MASK or reader.word(creature + CREATURE_SCRIPT))


def read_creature(reader: TrainingReader, observer: dict | None = None) -> CreatureSample:
    reader.ensure_running()
    # Registry display names are independent of the live creature snapshot.
    # Do not repeat those reads inside a frame-sensitive memory read.
    names = read_profile_names()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        game, tick, player, creature, hand = head
        identity, context, scripted = None, CreatureContext(), None
        if creature:
            context = CreatureContext(names=names)
            reader.require_type(creature, b'.?AVCreature@@')
            if reader.word(creature + CREATURE_OWNER) != player:
                raise CreatureReadError('owner_mismatch')
            mental = reader.word(creature + CREATURE_MENTAL)
            reader.require_type(mental, b'.?AVCreatureMental@@')
            scripted = _script_owns_creature(reader, creature)
            info = reader.word(creature + CREATURE_INFO)
            reader.require_type(info, b'.?AVCreatureInfo@@')
            raw = reader.read(info + INFO_NAME, 48)
            name, separator, _ = raw.partition(b'\0')
            species = SPECIES.get(name) if separator else None
            if species:
                context = CreatureContext(species, 'engine', names=names)
            from .alignment_live import read_alignment
            values={};errors=0;traits=[]
            for key,owner,offset in (('alignment',creature,0x168),('god_alignment',player,0x60)):
                try:values[key]=read_alignment(reader,owner,offset)
                except (OSError,ValueError):errors+=1
            try:
                # CreaturePhysical::GetUserSize returns the controller scale.
                physical=reader.word(creature+0x160)
                reader.require_type(physical,b'.?AVCreaturePhysical@@')
                controller=reader.word(physical+0x58)
                if reader.word(controller+0x4834)!=creature:raise ValueError('Size controller mismatch')
                size_raw=reader.read(controller+0x90,4);size,=struct.unpack('<f',size_raw)
                if not math.isfinite(size) or not 0<size<=1e6:raise ValueError('Invalid size')
                if reader.read(controller+0x90,4)!=size_raw or reader.word(physical+0x58)!=controller:
                    raise ValueError('Size changed during read')
                traits.append(CreatureTrait('body_size_scale',size,'engine','engine_units'))
            except (OSError,ValueError):errors+=1
            context=replace(context,**values,individual_traits=tuple(traits),trait_read_errors=errors)
            if (reader.word(creature + CREATURE_INFO) != info
                    or reader.word(creature + CREATURE_OWNER) != player
                    or reader.word(creature + CREATURE_MENTAL) != mental
                    or _script_owns_creature(reader, creature) != scripted
                    or reader.read(info + INFO_NAME, 48) != raw):
                continue
            identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        final_head = reader._head()
        # Game ticks advance during normal play. They do not invalidate stable
        # owner, creature, mental and species pointers, all rechecked above.
        if final_head[:1] + final_head[2:] != head[:1] + head[2:]:
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise CreatureReadError('session_changed')
        now = time.monotonic()
        if now - started > .75:
            raise CreatureReadError('freshness_limit')
        return CreatureSample(session, identity, now, context, scripted)
    raise CreatureReadError('identity_or_species_changed')
