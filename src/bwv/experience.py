"""Creature body readings, native intentions and explicitly sourced effects."""
from dataclasses import dataclass
import hashlib
import math

from .spells import SPELLS
from .native_text_live import NativeMessage


@dataclass(frozen=True)
class BodyState:
    health: float | None = None
    energy: float | None = None
    exhaustion: float | None = None
    dehydration: float | None = None
    warmth: float | None = None
    fatness: float | None = None
    itchiness: float | None = None
    on_fire: bool | None = None
    strength: float | None = None

    def __post_init__(self):
        for name in ('health','energy','exhaustion','dehydration','warmth','fatness','itchiness','strength'):
            value = getattr(self,name)
            # Warmth is signed in live engine samples; it is not a percentage
            # or a measured temperature. Other body readings remain nonnegative.
            minimum = -1e9 if name == 'warmth' else 0
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not minimum <= value <= 1e9):
                raise ValueError('Invalid body reading')
        if self.on_fire is not None and type(self.on_fire) is not bool:
            raise ValueError('Invalid fire state')


@dataclass(frozen=True)
class NativeInstinct:
    plan_id: int | None = None
    plan_name: str | None = None
    name_truncated: bool = False
    source: str = 'engine_plan'
    target_kind: str | None = None
    target_id: str | None = None

    def __post_init__(self):
        if self.plan_id is not None and (type(self.plan_id) is not int or not 0 <= self.plan_id < 328):
            raise ValueError('Invalid native plan')
        if self.plan_name is not None and (self.plan_id is None or not isinstance(self.plan_name,str)
                or not 0 < len(self.plan_name) <= 32 or any(ord(c)<32 or ord(c)>126 for c in self.plan_name)):
            raise ValueError('Invalid native plan label')
        if self.source != 'engine_plan' or type(self.name_truncated) is not bool:
            raise ValueError('Invalid native intention source')


@dataclass(frozen=True)
class ReceivedEffect:
    """Only a trusted adapter's target-linked native impact record belongs here.

    A cast notification, proximity or coincident health change is insufficient.
    No live adapter currently emits these records.
    """
    event_id: str
    target_id: str
    observed_at: float
    kind: str
    source_kind: str | None = None
    source_id: str | None = None
    miracle: str | None = None
    evidence: str = 'native_target_linked_impact'

    def __post_init__(self):
        for value in (self.event_id,self.target_id):
            if not isinstance(value,str) or not 0 < len(value) <= 96:
                raise ValueError('Invalid impact identity')
        if not math.isfinite(self.observed_at) or self.kind not in ('damage','healing'):
            raise ValueError('Invalid impact')
        if self.source_kind not in (None,'god_player','other_god','self','other_creature','world'):
            raise ValueError('Invalid impact source')
        if self.source_id is not None and (self.source_kind is None or not isinstance(self.source_id,str) or not 0 < len(self.source_id) <= 96):
            raise ValueError('Invalid source identity')
        if self.miracle is not None and self.miracle not in SPELLS.values():
            raise ValueError('Invalid impact miracle')
        if self.evidence != 'native_target_linked_impact':
            raise ValueError('Impact requires target-linked native evidence')


@dataclass(frozen=True)
class HealthChange:
    event_id: str
    direction: str
    amount_engine_units: float
    # A sampled net change never establishes its cause or number of hits.
    attribution: str = 'unknown'


@dataclass(frozen=True)
class Experience:
    body: BodyState = BodyState()
    instinct: NativeInstinct = NativeInstinct()
    health_changes: tuple[HealthChange,...] = ()
    received_effects: tuple[ReceivedEffect,...] = ()
    validation: str = 'unavailable'
    source_attribution: str = 'native_impact_adapter_not_implemented'
    hover_thought_text: str | None = None
    native_training_feedback: tuple[NativeMessage, ...] = ()
    native_text_validation: str = 'unavailable'


@dataclass(frozen=True)
class ExperienceSample:
    session: str
    creature_id: str
    observed_at: float
    game_tick: int
    body: BodyState
    instinct: NativeInstinct = NativeInstinct()
    effects: tuple[ReceivedEffect,...] = ()


class ExperienceTracker:
    """No deltas across loads, gaps or locks; short event retention for cadence."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = None
        self.events = []
        self.effects = []
        self.seen = {}
        self.serial = 0

    def update(self,sample,now):
        if not math.isfinite(now) or not -.1 <= now-sample.observed_at <= .75:
            self.reset()
            raise ValueError('Stale experience sample')
        previous = self.previous
        discontinuity = (previous is None or (sample.session,sample.creature_id) != (previous.session,previous.creature_id)
            or sample.game_tick < previous.game_tick or sample.observed_at < previous.observed_at
            or sample.observed_at-previous.observed_at > 1.5)
        if discontinuity:
            self.reset()
            previous = None
        self.events = [(when,event) for when,event in self.events if now-when<=15]
        self.effects = [event for event in self.effects if -.1<=now-event.observed_at<=15]
        self.seen = {key:when for key,when in self.seen.items() if now-when<=60}
        if previous and previous.body.health is not None and sample.body.health is not None:
            delta = sample.body.health-previous.body.health
            if abs(delta)>1e-5:
                self.serial += 1
                key = hashlib.sha256(f'{sample.session}:{sample.creature_id}:{sample.game_tick}:{sample.observed_at}:{self.serial}'.encode()).hexdigest()[:24]
                self.events.append((now,HealthChange(key,'increased' if delta>0 else 'decreased',abs(delta))))
        # Establish a baseline on attachment instead of replaying old impacts.
        for event in sample.effects[:32]:
            if event.target_id != sample.creature_id or not -.1<=now-event.observed_at<=1.5:
                continue
            if event.event_id not in self.seen:
                self.seen[event.event_id] = now
                if previous is not None:
                    self.effects.append(event)
        self.events = self.events[-16:]
        self.effects = self.effects[-16:]
        if len(self.seen)>256:
            self.seen = dict(sorted(self.seen.items(),key=lambda pair:pair[1])[-256:])
        self.previous = sample
        return Experience(sample.body,sample.instinct,tuple(event for _,event in self.events),
                          tuple(self.effects),'offline_layout_live_qa_pending')
