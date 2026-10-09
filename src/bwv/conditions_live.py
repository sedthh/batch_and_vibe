"""Experimental read-only fire query, pending labelled live validation.

Not connected to the normal observer yet. Exact-build layout facts were traced
from the native IS_ON_FIRE query; no engine functions are called.
"""
from dataclasses import dataclass, asdict
import math
import struct
import time
import hashlib

from .training_live import TrainingReader, CREATURE_OWNER, CREATURE_MENTAL
from .pipe import snapshot

OBJECT_FIRE_DATA = 0x44
FIRE_VALUE = 0x14
FIRE_OBJECT = 0x1C
OBJECT_INFO = 0x28
INFO_FIRE_THRESHOLD = 0xB4
MIN_FIRE_THRESHOLD = 40.0


@dataclass(frozen=True)
class FireSample:
    session: str
    creature_id: str
    observed_at: float
    on_fire: bool
    fire_value_engine_units: float | None
    ignition_threshold_engine_units: float | None
    validation: str = 'offline_layout_only_live_validation_pending'


def read_fire(reader: TrainingReader, observer=None):
    reader.ensure_running()
    start = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    for _ in range(3):
        head = reader._head()
        _, _, player, creature, _ = head
        if not creature:
            raise ValueError('Creature unavailable for fire observation')
        reader.require_type(creature,b'.?AVCreature@@')
        if reader.word(creature+CREATURE_OWNER) != player:
            raise ValueError('Creature owner mismatch')
        mental = reader.word(creature+CREATURE_MENTAL)
        reader.require_type(mental,b'.?AVCreatureMental@@')
        fire = reader.word(creature+OBJECT_FIRE_DATA)
        value = threshold = None
        burning = False
        if fire:
            if reader.word(fire+FIRE_OBJECT) != creature:
                raise ValueError('Fire record does not refer to selected creature')
            info = reader.word(creature+OBJECT_INFO)
            reader.require_type(info,b'.?AVCreatureInfo@@')
            raw_value = reader.read(fire+FIRE_VALUE,4)
            raw_threshold = reader.read(info+INFO_FIRE_THRESHOLD,4)
            value, = struct.unpack('<f',raw_value)
            threshold, = struct.unpack('<f',raw_threshold)
            if not all(math.isfinite(v) and 0 <= v <= 1e9 for v in (value,threshold)):
                raise ValueError('Invalid fire observation')
            threshold = max(MIN_FIRE_THRESHOLD,threshold)
            burning = value >= threshold
            if (reader.word(fire+FIRE_OBJECT) != creature
                    or reader.word(creature+OBJECT_INFO) != info
                    or reader.read(fire+FIRE_VALUE,4) != raw_value
                    or reader.read(info+INFO_FIRE_THRESHOLD,4) != raw_threshold):
                continue
        if (reader.word(creature+OBJECT_FIRE_DATA) != fire
                or reader.word(creature+CREATURE_OWNER) != player
                or reader.word(creature+CREATURE_MENTAL) != mental or reader._head() != head):
            continue
        if reader._session(snapshot(reader.pid)) != session:
            raise ValueError('Session changed during fire observation')
        now = time.monotonic()
        if now-start > .75:
            raise ValueError('Fire observation exceeded freshness limit')
        identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
        return FireSample(session,identity,now,burning,value,threshold)
    raise ValueError('Fire state changed during observation')


def run(pid, seconds=5):
    """Bounded diagnostics only: never offers these provisional facts to a model."""
    from .holding_live import read_holding
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError('Condition probe must last between 0 and 60 seconds')
    samples = []
    deadline = time.monotonic()+seconds
    with TrainingReader(pid) as reader:
        while time.monotonic() < deadline:
            try:
                state = snapshot(pid)
                fire = read_fire(reader,state)
                holding = read_holding(reader,state)
                if fire.creature_id != holding.creature_id or fire.session != holding.session:
                    raise ValueError('Creature changed between probes')
                samples.append({'fire':asdict(fire),'motivations':[asdict(d) for d in holding.context.drives]})
            except (OSError,ValueError) as error:
                samples.append({'error':type(error).__name__})
            time.sleep(.25)
    return {'mode':'read_only_condition_probe','live_validation':'pending',
            'provider_calls':0,'game_actions':0,'samples':samples}
