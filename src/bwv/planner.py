"""Provider-neutral creature planner with cancellation and no speculative calls.

Outputs are proposals only. A future game-thread executor must independently
recheck the lease and engine control facts immediately before any action.
"""
from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass
import time
from typing import Callable, Protocol

from .control import ControlGate, ControlObservation, GateStatus, Lease
from .training import TrainingContext
from .creature import CreatureContext
from .leash import LeashContext
from .miracle import MiracleContext
from .holding import HoldingContext
from .surroundings import SurroundingsContext
from .experience import Experience
from .environment import Environment
from .god_hand import GodHandContext
from .island import IslandContext
from .prompt import CreaturePrompt, build_prompt
from .actions import (ActionIntent, PickupTarget, ResolvedAction, parse_intent,
                      resolve_intent, target_still_valid)


@dataclass(frozen=True)
class PlanningRequest:
    lease: Lease
    candidates: tuple[str, ...]
    interruption_summary: str | None
    training: TrainingContext = TrainingContext()
    reflection_only: bool = False
    creature: CreatureContext = CreatureContext()
    leash: LeashContext = LeashContext()
    miracles: MiracleContext = MiracleContext()
    holding: HoldingContext = HoldingContext()
    surroundings: SurroundingsContext = SurroundingsContext()
    pickup_targets: tuple[PickupTarget, ...] = ()
    experience: Experience = Experience()
    environment: Environment = Environment()
    god_hand: GodHandContext = GodHandContext()
    island: IslandContext = IslandContext()

    def to_prompt(self) -> CreaturePrompt:
        return build_prompt(self)


class Provider(Protocol):
    # Reflection-only requests return None and cannot generate a game proposal.
    async def choose(self, request: PlanningRequest) -> str | ActionIntent | dict | None: ...


@dataclass(frozen=True)
class Proposal:
    lease: Lease
    candidate: str
    expires_at: float
    resolved_action: ResolvedAction | None = None


class CreaturePlanner:
    def __init__(self, provider: Provider, *, gate: ControlGate | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 cooldown: float = 10, timeout: float = 15, hourly_limit: int = 120):
        if (not math.isfinite(cooldown) or cooldown < 0 or
                not math.isfinite(timeout) or timeout <= 0 or
                type(hourly_limit) is not int or hourly_limit < 1):
            raise ValueError("Invalid planner limits")
        self.provider, self.clock = provider, clock
        self.gate = gate or ControlGate()
        self.cooldown, self.timeout, self.hourly_limit = cooldown, timeout, hourly_limit
        self._task: asyncio.Task | None = None
        self._proposal: Proposal | None = None
        self._calls: deque[float] = deque()
        self._last_call = float("-inf")
        self.calls_started = 0
        self.results_discarded = 0
        self.reflections_completed = 0
        self.last_error: str | None = None
        self.last_resolution: str | None = None
        self._closed = False

    def update(self, observation: ControlObservation) -> GateStatus:
        status = self.gate.update(observation, self.clock())
        self._revoke(status)
        return status

    def _revoke(self, status: GateStatus) -> None:
        pending = self._proposal
        observation = self.gate.observation
        if not status.allowed or (pending and (
                pending.lease.generation != status.generation or observation is None or
                pending.candidate not in observation.candidates or
                pending.resolved_action is not None and not target_still_valid(
                    pending.resolved_action, observation.pickup_targets, observation.candidates))):
            self._proposal = None
        if self._task is not None and not self._task.done():
            lease = getattr(self._task, "bwv_lease", None)
            if not status.allowed or (lease and lease.generation != status.generation):
                self._task.cancel()

    def tick(self) -> GateStatus:
        now = self.clock()
        status = self.gate.poll(now)
        self._revoke(status)
        if self._closed or not status.allowed or self._proposal is not None:
            return status
        if self._task is not None and not self._task.done():
            return status
        while self._calls and now - self._calls[0] >= 3600:
            self._calls.popleft()
        if now - self._last_call < self.cooldown or len(self._calls) >= self.hourly_limit:
            return status
        lease = self.gate.issue(now)
        if lease is None:
            return status
        assert self.gate.observation is not None
        observation = self.gate.observation
        request = PlanningRequest(lease, observation.candidates, status.summary,
            observation.training, reflection_only=not observation.candidates,
            creature=observation.creature, leash=observation.leash, miracles=observation.miracles,
            holding=observation.holding, surroundings=observation.surroundings,
            pickup_targets=observation.pickup_targets, experience=observation.experience,
            environment=observation.environment, god_hand=observation.god_hand, island=observation.island)
        if not request.to_prompt().has_context:
            return status
        # Gate before ANY provider work, including prompt-building or memory calls.
        self._last_call = now
        self._calls.append(now)
        self.calls_started += 1
        self.last_resolution = None
        self._task = asyncio.create_task(self._run(request))
        self._task.bwv_lease = lease
        return status

    async def _run(self, request: PlanningRequest) -> None:
        try:
            deadline = time.monotonic() + self.timeout
            async with asyncio.timeout(self.timeout) as timeout_scope:
                result = await self.provider.choose(request)
            now = self.clock()
            observation = self.gate.observation
            if (self._closed or timeout_scope.expired() or time.monotonic() >= deadline or
                    not self.gate.valid(request.lease, now) or observation is None):
                self.results_discarded += 1
                return
            if type(result) is dict and set(result) == {'candidate'}:
                result = result['candidate']
                if result is not None and type(result) is not str:
                    raise ValueError('Invalid candidate response')
            if result is None:
                self.reflections_completed += 1
                self.gate.acknowledge_summary(request.lease, now)
                self.last_error = None
                self.last_resolution = 'no_override'
                return
            if request.reflection_only:
                self.results_discarded += 1
                return
            # Exact candidate responses cannot bypass typed pickup eligibility.
            if isinstance(result, str):
                target = next((t for t in request.pickup_targets if t.candidate == result), None)
                if target is not None:
                    result = ActionIntent('pick_up', target.kind, 'specific', target.object_id)
            if isinstance(result, (ActionIntent, dict)):
                intent = parse_intent(result) if isinstance(result, dict) else result
                resolution = resolve_intent(intent, request.pickup_targets,
                    observation.pickup_targets, request.candidates, observation.candidates)
                self.last_resolution = resolution.status
                if resolution.action is None:
                    self.results_discarded += 1
                    return
                self._proposal = Proposal(request.lease, resolution.action.target.candidate,
                    now + self.gate.max_age, resolution.action)
                self.last_error = None
                return
            if result not in request.candidates or result not in observation.candidates:
                self.results_discarded += 1
                return
            self._proposal = Proposal(request.lease, result, now + self.gate.max_age)
            self.last_error = None
        except asyncio.CancelledError:
            self.results_discarded += 1
        except Exception as error:
            # Never expose provider URLs, credentials, response bodies or prompts.
            self.last_error = type(error).__name__

    def take_proposal(self) -> Proposal | None:
        proposal, self._proposal = self._proposal, None
        now = self.clock()
        if proposal is None:
            return None
        observation = self.gate.observation
        if (now > proposal.expires_at or not self.gate.valid(proposal.lease, now)
                or observation is None or proposal.candidate not in observation.candidates
                or proposal.resolved_action is not None and not target_still_valid(
                    proposal.resolved_action, observation.pickup_targets, observation.candidates)):
            self.results_discarded += 1
            return None
        self.gate.acknowledge_summary(proposal.lease, now)
        return proposal

    async def close(self) -> None:
        self._closed = True
        self._proposal = None
        if self._task and not self._task.done():
            self._task.cancel()
            # A provider that ignores cancellation must not hang shutdown.
            await asyncio.wait({self._task}, timeout=1)
