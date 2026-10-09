"""Engine-registered miracle learning, separate from global player cast history."""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import uuid
from .spells import SPELLS


@dataclass(frozen=True)
class MiracleLearning:
    event_id: str
    observed_at: float
    spell: str
    counter_value: int
    counter_delta: int

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not 0 < len(self.event_id) <= 96:
            raise ValueError('Invalid miracle learning identity')
        if type(self.observed_at) not in (float, int) or not math.isfinite(self.observed_at):
            raise ValueError('Invalid miracle learning time')
        if self.spell not in SPELLS.values():
            raise ValueError('Unverified miracle learning type')
        if any(type(v) is not int or not 0 <= v <= 0xffffffff
               for v in (self.counter_value, self.counter_delta)) or self.counter_delta > self.counter_value:
            raise ValueError('Invalid miracle learning counter')


@dataclass(frozen=True)
class HealKnowledge:
    learning_counter: int
    learning_requirement: float
    learning_threshold_met: bool = field(init=False)

    def __post_init__(self):
        if type(self.learning_counter) is not int or not 0 <= self.learning_counter <= 0xffffffff:
            raise ValueError('Invalid Heal knowledge counter')
        if (type(self.learning_requirement) not in (int, float)
                or not math.isfinite(self.learning_requirement)
                or not 0 < self.learning_requirement <= 1000000):
            raise ValueError('Invalid Heal learning requirement')
        object.__setattr__(self, 'learning_threshold_met',
                           self.learning_counter >= self.learning_requirement)


@dataclass(frozen=True)
class MiracleContext:
    learning: tuple[MiracleLearning, ...] = ()
    heal_knowledge: HealKnowledge | None = None
    knowledge: tuple[MiracleKnowledge, ...] = ()

    def __post_init__(self):
        if (type(self.learning) is not tuple or len(self.learning) > 8
                or any(not isinstance(e, MiracleLearning) for e in self.learning)
                or len({e.event_id for e in self.learning}) != len(self.learning)):
            raise ValueError('Invalid miracle learning window')
        if self.heal_knowledge is not None and not isinstance(self.heal_knowledge, HealKnowledge):
            raise ValueError('Invalid Heal knowledge context')
        if (type(self.knowledge) is not tuple or len(self.knowledge) > 41
                or any(not isinstance(k, MiracleKnowledge) for k in self.knowledge)
                or len({k.spell for k in self.knowledge}) != len(self.knowledge)):
            raise ValueError('Invalid miracle knowledge context')


@dataclass(frozen=True)
class SpellSample:
    spell_type: int
    counter: int
    learning_tick: int
    requirement: float | None = None

    def __post_init__(self):
        if type(self.spell_type) is not int or self.spell_type not in SPELLS:
            raise ValueError('Unsupported miracle slot')
        if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in (self.counter,self.learning_tick)):
            raise ValueError('Invalid miracle counter or tick')
        if self.requirement is not None:
            HealKnowledge(self.counter, self.requirement)


@dataclass(frozen=True)
class MiracleKnowledge:
    spell: str
    learning_counter: int
    learning_tick: int
    learning_requirement: float
    learning_threshold_met: bool = field(init=False)

    def __post_init__(self):
        if self.spell not in SPELLS.values():
            raise ValueError('Unsupported miracle knowledge')
        if type(self.learning_tick) is not int or not 0 <= self.learning_tick <= 0xffffffff:
            raise ValueError('Invalid miracle knowledge tick')
        knowledge = HealKnowledge(self.learning_counter, self.learning_requirement)
        object.__setattr__(self, 'learning_threshold_met', knowledge.learning_threshold_met)


@dataclass(frozen=True)
class MiracleSample:
    session: str
    creature_id: str | None
    observed_at: float
    game_tick: int
    # Player history is diagnostic only: it does not establish creature perception.
    player_cast_type: int
    player_cast_tick: int
    heal_counter: int
    heal_learning_tick: int
    heal_learning_requirement: float | None = None
    other_spells: tuple[SpellSample, ...] = ()

    def __post_init__(self):
        if not isinstance(self.session, str) or not 0 < len(self.session) <= 128:
            raise ValueError('Invalid miracle session')
        if self.creature_id is not None and (not isinstance(self.creature_id, str)
                or not 0 < len(self.creature_id) <= 96):
            raise ValueError('Invalid miracle creature identity')
        if type(self.observed_at) not in (int, float) or not math.isfinite(self.observed_at):
            raise ValueError('Invalid miracle observation time')
        if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in (
                self.game_tick, self.player_cast_type, self.player_cast_tick,
                self.heal_counter, self.heal_learning_tick)):
            raise ValueError('Invalid miracle observation value')
        if self.heal_learning_requirement is not None:
            HealKnowledge(self.heal_counter, self.heal_learning_requirement)
        if (type(self.other_spells) is not tuple or len(self.other_spells) > 40
                or any(not isinstance(s, SpellSample) or s.spell_type == 10 for s in self.other_spells)
                or len({s.spell_type for s in self.other_spells}) != len(self.other_spells)):
            raise ValueError('Invalid additional miracle samples')

    def spells(self):
        return (SpellSample(10, self.heal_counter, self.heal_learning_tick,
                            self.heal_learning_requirement), *self.other_spells)


class MiracleDetector:
    """Publish new engine learning registrations, never infer visual witnessing.

    The counter is weighted and rate-limited by the game. It is not a spell count,
    percentage, mastery flag or evidence of the caster/recipient's identity.
    """
    def __init__(self, max_gap: float = .75, retention: float = 30):
        if any(not math.isfinite(v) or v <= 0 for v in (max_gap, retention)):
            raise ValueError('Invalid miracle detector timing')
        self.max_gap, self.retention = max_gap, retention
        self._nonce = uuid.uuid4().hex[:16]
        self._epoch = 0
        self.reset()

    def reset(self) -> MiracleContext:
        self._previous = None
        self._events = ()
        self._epoch += 1
        return MiracleContext()

    def update(self, sample: MiracleSample) -> MiracleContext:
        previous = self._previous
        spells = {s.spell_type: s for s in sample.spells()}
        old = {s.spell_type: s for s in previous.spells()} if previous else {}
        if sample.creature_id is None:
            return self.reset()
        if previous is not None and (
                (sample.session, sample.creature_id) != (previous.session, previous.creature_id)
                or not 0 <= sample.observed_at - previous.observed_at <= self.max_gap
                or sample.game_tick < previous.game_tick
                or spells.keys() != old.keys()
                or any((s.learning_tick < old[k].learning_tick
                        and old[k].learning_tick <= previous.game_tick) or s.counter < old[k].counter
                    or (s.learning_tick == old[k].learning_tick and s.counter != old[k].counter)
                    for k, s in spells.items())):
            self.reset()
            previous = None
        self._events = tuple(e for e in self._events if sample.observed_at-e.observed_at <= self.retention)
        for k, s in spells.items():
            # Knowledge survives an island clock reset. Only a changed native
            # timestamp inside this observation window establishes new learning,
            # including when it replaces a timestamp retained from the old island.
            if (previous is not None and s.learning_tick != old[k].learning_tick
                    and previous.game_tick <= s.learning_tick <= sample.game_tick
                    and (s.learning_tick > old[k].learning_tick
                         or old[k].learning_tick > previous.game_tick)):
                event = MiracleLearning(f'{self._nonce}:{self._epoch}:{k}:{s.learning_tick}',
                    sample.observed_at, SPELLS[k], s.counter, s.counter-old[k].counter)
                self._events = (*self._events, event)[-8:]
        self._previous = sample
        knowledge = (HealKnowledge(sample.heal_counter, sample.heal_learning_requirement)
                     if sample.heal_learning_requirement is not None else None)
        all_knowledge = tuple(MiracleKnowledge(SPELLS[k], s.counter, s.learning_tick, s.requirement)
                              for k,s in sorted(spells.items()) if s.requirement is not None)
        return MiracleContext(self._events, knowledge, all_knowledge)
