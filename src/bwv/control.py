"""Fail-closed permission to spend tokens and control the creature.

The engine adapter supplies facts, never guesses based on a timer, camera motion,
or the absence of a known cutscene. Unknown facts deny control. This module has
no network access and summarizes interruptions without invoking a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Mapping

from .training import TrainingContext
from .creature import CreatureContext
from .leash import LeashContext
from .miracle import MiracleContext
from .holding import HoldingContext
from .surroundings import SurroundingsContext
from .actions import PickupTarget
from .experience import Experience
from .environment import Environment
from .god_hand import GodHandContext
from .island import IslandContext


@dataclass(frozen=True)
class ControlObservation:
    session: str
    sequence: int
    observed_at: float
    creature_id: str | None = None
    connected: bool = False
    supported: bool = False
    world_ready: bool | None = None
    loading: bool | None = None
    paused: bool | None = None
    cutscene: bool | None = None
    player_has_control: bool | None = None
    creature_present: bool | None = None
    script_owns_creature: bool | None = None
    in_temple: bool | None = None
    # Candidates must be supplied by a verified engine adapter, not an LLM.
    candidates: tuple[str, ...] = ()
    context_event: str | None = None
    training: TrainingContext = TrainingContext()
    creature: CreatureContext = CreatureContext()
    leash: LeashContext = LeashContext()
    miracles: MiracleContext = MiracleContext()
    holding: HoldingContext = HoldingContext()
    surroundings: SurroundingsContext = SurroundingsContext()
    # Trusted adapter capabilities; nearby observations alone never populate this.
    pickup_targets: tuple[PickupTarget, ...] = ()
    experience: Experience = Experience()
    environment: Environment = Environment()
    god_hand: GodHandContext = GodHandContext()
    island: IslandContext = IslandContext()


@dataclass(frozen=True)
class Lease:
    generation: int
    session: str
    creature_id: str


@dataclass(frozen=True)
class GateStatus:
    allowed: bool
    reasons: tuple[str, ...]
    generation: int
    summary: str | None = None


@dataclass
class _Interruption:
    start: float
    reasons: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)


LABELS = {
    'fullscreen_overlay_unavailable': 'fullscreen does not support creature chat',
    'game_display_unavailable': 'game window unavailable or minimized',
    'chat_overlay_unavailable': 'creature chat overlay unavailable',
    "temple": "player viewing temple interior",
    "disconnected": "game disconnected", "unsupported": "unverified game adapter",
    "stale": "stale game observations", "invalid_observation": "invalid game observation",
    "loading": "loading", "paused": "game paused", "world_not_ready": "world not ready",
    "no_creature": "no creature available", "cutscene": "cutscene",
    "unverified_creature": "creature identity not yet verified",
    "scripted_creature": "script directing the creature", "control_locked": "player control locked",
    "unknown_control": "control state not yet verified", "no_actions": "no available actions",
    "settling": "waiting for stable control",
    "scene_state_unverified": "scene state not yet verified",
    "creature_unavailable": "creature unavailable for conversation",
}

# Shared by enforcement and diagnostics so the debugger names every unknown fact.
CONTROL_EXPECTATIONS = (
    ("loading", False, "loading"), ("paused", False, "paused"),
    ("world_ready", True, "world_not_ready"), ("creature_present", True, "no_creature"),
    ("cutscene", False, "cutscene"), ("script_owns_creature", False, "scripted_creature"),
    ("player_has_control", True, "control_locked"), ("in_temple", False, "temple"),
)


class ControlGate:
    def __init__(self, *, max_age: float = 1.5, resume_delay: float = 0.75,
                 preserve_stale_seconds: float = 0):
        if not math.isfinite(max_age) or max_age <= 0 or not math.isfinite(resume_delay) or resume_delay < 0:
            raise ValueError("Invalid gate timing")
        if not math.isfinite(preserve_stale_seconds) or preserve_stale_seconds < 0:
            raise ValueError("Invalid stale observation wait")
        self.max_age, self.resume_delay = max_age, resume_delay
        self.preserve_stale_seconds = preserve_stale_seconds
        self.waiting_for_observation = False
        self.observation: ControlObservation | None = None
        self.generation = 0
        self._identity: tuple[str, str | None] | None = None
        self._last_now = -math.inf
        self._valid_since: float | None = None
        self._allowed = False
        self._interruption: _Interruption | None = None
        self._summaries: list[str] = []
        self._last_sequence = -1

    def _reasons(self, now: float) -> tuple[str, ...]:
        obs = self.observation
        if obs is None:
            return ("disconnected",)
        if not math.isfinite(now) or not math.isfinite(obs.observed_at) or obs.observed_at > now + 0.1:
            return ("invalid_observation",)
        reasons = []
        if obs.connected is not True:
            reasons.append("disconnected")
        if obs.supported is not True:
            reasons.append("unsupported")
        if now - obs.observed_at > self.max_age:
            reasons.append("stale")
        for attribute, expected, reason in CONTROL_EXPECTATIONS:
            value = getattr(obs, attribute)
            if type(value) is not bool:
                reasons.append("unknown_control")
            elif value != expected:
                reasons.append(reason)
        if not obs.creature_id and obs.creature_present is not False:
            reasons.append("unverified_creature")
        # Verified free control permits reflection even without an action to take.
        # The planner uses reflection_only; pause/story/unknown checks still apply.
        return tuple(dict.fromkeys(reasons))

    def update(self, observation: ControlObservation, now: float) -> GateStatus:
        if not observation.session or type(observation.sequence) is not int or observation.sequence < 0:
            self.observation = None
            return self.poll(now)
        same_session = self._identity is not None and observation.session == self._identity[0]
        if (same_session and observation.sequence == self._last_sequence and
                self.observation is not None and
                replace(observation, observed_at=self.observation.observed_at) == self.observation):
            # Separate Windows uptime and Python clock samples have small skew.
            # Re-reading the same cached frame must neither fake a disconnect
            # nor refresh its age. Keep the earliest source-time estimate.
            if math.isfinite(observation.observed_at):
                self.observation = replace(self.observation,
                    observed_at=min(self.observation.observed_at, observation.observed_at))
            else:
                self.observation = None
            return self.poll(now)
        if same_session and (observation.sequence < self._last_sequence or
                (observation.sequence == self._last_sequence and observation != self.observation)):
            # An out-of-order frame cannot restore control after a cutscene.
            self.observation = None
            self._valid_since = None
            return self.poll(now)
        identity = (observation.session, observation.creature_id)
        if identity != self._identity:
            self.waiting_for_observation = False
            self.generation += 1
            self._allowed = False
            self._valid_since = None
            # Save loads must not inherit summaries from a different timeline.
            if self._identity is not None and observation.session != self._identity[0]:
                self._interruption = None
                self._summaries.clear()
            self._identity = identity
        self.observation = observation
        self._last_sequence = observation.sequence
        return self.poll(now)

    def poll(self, now: float) -> GateStatus:
        reasons = self._reasons(now)
        monitoring_gap = (self._allowed and self.max_age < now-self._last_now
                          <= self.preserve_stale_seconds)
        # A monitoring gap is not evidence of continuously valid control.
        if now - self._last_now > self.max_age:
            self._valid_since = None
            if self._allowed and now - self._last_now > self.preserve_stale_seconds:
                self.generation += 1
                self._allowed = False
        if now < self._last_now:
            reasons = ("invalid_observation",)
        self._last_now = now
        if reasons:
            self._valid_since = None
        else:
            if self._valid_since is None:
                self._valid_since = now
            if now - self._valid_since < self.resume_delay:
                reasons = ("settling",)
        allowed = not reasons
        # The live conversation queue may retain an intention through a brief
        # missing sample. The lease is still INVALID while stale/settling, and
        # execution must reacquire native capabilities after fresh data returns.
        stale_wait = (reasons == ('stale',) and self.observation is not None
                      and now - self.observation.observed_at <= self.preserve_stale_seconds)
        recovering = (self.waiting_for_observation or monitoring_gap) and reasons == ('settling',)
        if (self._allowed or self.waiting_for_observation) and not allowed and not (stale_wait or recovering):
            self.generation += 1  # Revokes every pending result and queued action.
        self.waiting_for_observation = bool(stale_wait or recovering)
        self._allowed = allowed
        if not allowed:
            if self._interruption is None:
                self._interruption = _Interruption(now)
            self._interruption.reasons.update(r for r in reasons if r != "settling")
            event = self.observation.context_event if self.observation else None
            if event and event not in self._interruption.events and len(self._interruption.events) < 8:
                # Events remain untrusted contextual data, not model instructions.
                self._interruption.events.append(event[:240])
        elif self._interruption is not None:
            interruption = self._interruption
            if interruption.reasons:
                labels = ", ".join(LABELS[r] for r in sorted(interruption.reasons))
                message = f"[AI inactive for {max(0, now - interruption.start):.1f}s: {labels}.]"
                if interruption.events:
                    message += " Observed context: " + " | ".join(interruption.events)
                self._summaries.append(message)
                self._summaries = self._summaries[-8:]
            self._interruption = None
        summary = "\n".join(self._summaries) or None
        return GateStatus(allowed, reasons, self.generation, summary)

    def issue(self, now: float) -> Lease | None:
        if not self.poll(now).allowed or not self.observation or not self.observation.creature_id:
            return None
        return Lease(self.generation, self.observation.session, self.observation.creature_id)

    def valid(self, lease: Lease, now: float) -> bool:
        if not self.poll(now).allowed or not self.observation:
            return False
        return lease == Lease(self.generation, self.observation.session, self.observation.creature_id or "")

    def acknowledge_summary(self, lease: Lease, now: float) -> None:
        if self.valid(lease, now):
            self._summaries.clear()


def from_observer(snapshot: Mapping, now: float, source_age: float | None = None) -> ControlObservation:
    """The current observer does NOT establish gameplay-control facts.

    Its VM initialization and native-function names cannot establish that a
    creature exists, that a cutscene ended, or that the player has control.
    Therefore this adapter intentionally grants no inference or action rights.
    """
    if source_age is None:
        milliseconds = snapshot.get("source_age_ms")
        source_age = (milliseconds / 1000 if type(milliseconds) in (int, float)
                      and math.isfinite(milliseconds) and milliseconds >= 0 else math.inf)
    return ControlObservation(
        session=f"{snapshot.get('pid', 'unknown')}:{snapshot.get('session', 'unknown')}",
        sequence=snapshot.get("ticks", 0),
        observed_at=now - source_age,
        connected=snapshot.get("protocol") == 1,
        supported=False,
    )
