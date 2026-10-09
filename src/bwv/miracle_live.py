"""Read-only, exact-fingerprint miracle learning observations; no engine calls."""
import hashlib
import struct
import time

from .miracle import MiracleSample, SpellSample
from .spells import SPELLS
from .pipe import snapshot
from .training_live import TrainingReader, CREATURE_MENTAL, CREATURE_OWNER

PLAYER_CAST_TYPE, PLAYER_CAST_TICK = 0xE8, 0xEC
MENTAL_KNOWN_ACTIONS = 0x1A9FC
MENTAL_CREATURE = 0x20D18
HEAL_TYPE = 10
HEAL_COUNTER = 0x17D38 + HEAL_TYPE * 4
HEAL_LEARNING_TICK = 0x17DE0 + HEAL_TYPE * 4
CREATURE_INFO = 0x28
INFO_MAGIC_LEARNING_FACTOR = 0x37C
HEAL_LEARNING_INFO = 0xCA9C70 + HEAL_TYPE * 0x70
BASE_LEARNING_REQUIREMENT = 0x54


def read_miracle(reader: TrainingReader, observer: dict | None = None) -> MiracleSample:
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        game, tick, player, creature, hand = head
        cast = reader.read(player + PLAYER_CAST_TYPE, 8)
        cast_type, cast_tick = struct.unpack('<II', cast)
        identity, counter, learning_tick, requirement = None, 0, 0, None
        other_spells = ()
        if creature:
            reader.require_type(creature, b'.?AVCreature@@')
            if reader.word(creature + CREATURE_OWNER) != player:
                raise ValueError('Selected creature owner mismatch')
            mental = reader.word(creature + CREATURE_MENTAL)
            reader.require_type(mental, b'.?AVCreatureMental@@')
            reader.require_type(mental + MENTAL_KNOWN_ACTIONS, b'.?AVCreatureActionsKnownAbout@@')
            if reader.word(mental + MENTAL_CREATURE) != creature:
                raise ValueError('Creature mental identity mismatch')
            info = reader.word(creature + CREATURE_INFO)
            reader.require_type(info, b'.?AVCreatureInfo@@')
            factor_raw = reader.read(info + INFO_MAGIC_LEARNING_FACTOR, 4)
            factor, = struct.unpack('<f', factor_raw)
            samples, bases = [], []
            for kind in SPELLS:
                entry = 0xCA9C70 + kind * 0x70
                reader.require_type(entry, b'.?AVCreatureMagicActionKnownAboutEntry@@')
                base = reader.word(entry + BASE_LEARNING_REQUIREMENT)
                bases.append((entry,base))
                samples.append(SpellSample(kind, reader.word(mental+0x17D38+kind*4),
                    reader.word(mental+0x17DE0+kind*4), base*factor))
            # Saved learning timestamps can belong to an earlier island whose
            # game clock ran longer. They do not invalidate the current counters;
            # the detector separately checks the time window for new events.
            if (any(reader.word(mental+0x17D38+s.spell_type*4) != s.counter
                    or reader.word(mental+0x17DE0+s.spell_type*4) != s.learning_tick for s in samples)
                    or any(reader.word(entry+BASE_LEARNING_REQUIREMENT) != base for entry,base in bases)
                    or reader.word(creature + CREATURE_MENTAL) != mental
                    or reader.word(mental + MENTAL_CREATURE) != creature
                    or reader.word(creature + CREATURE_OWNER) != player
                    or reader.word(creature + CREATURE_INFO) != info
                    or reader.read(info + INFO_MAGIC_LEARNING_FACTOR, 4) != factor_raw):
                continue
            heal = next(s for s in samples if s.spell_type == HEAL_TYPE)
            counter, learning_tick, requirement = heal.counter, heal.learning_tick, heal.requirement
            other_spells = tuple(s for s in samples if s.spell_type != HEAL_TYPE)
            identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        if cast != reader.read(player + PLAYER_CAST_TYPE, 8) or reader._head() != head:
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Observer session changed during miracle read')
        now = time.monotonic()
        if now-started > .75:
            raise ValueError('Miracle read exceeded freshness limit')
        return MiracleSample(session, identity, now, tick, cast_type, cast_tick, counter,
                             learning_tick, requirement, other_spells)
    raise ValueError('Miracle fields changed during observation')
