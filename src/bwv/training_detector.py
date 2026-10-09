"""Convert sampled engine feedback into bounded context, never control rights."""
from __future__ import annotations

from dataclasses import dataclass
import math
import uuid

from .training import TrainingContext, TrainingFeedback


@dataclass(frozen=True)
class TrainingSample:
    session: str
    creature_id: str | None
    observed_at: float
    game_tick: int
    feedback_tick: int
    magnitude: float
    attention_active: bool

    def __post_init__(self):
        if not self.session or len(self.session) > 128:
            raise ValueError("Invalid training session")
        if self.creature_id is not None and not 0 < len(self.creature_id) <= 96:
            raise ValueError("Invalid training creature identity")
        if type(self.observed_at) not in (int, float) or not math.isfinite(self.observed_at):
            raise ValueError("Invalid training sample time")
        for tick in (self.game_tick, self.feedback_tick):
            if type(tick) is not int or not 0 <= tick <= 0xFFFFFFFF:
                raise ValueError("Invalid training tick")
        if (type(self.magnitude) not in (int, float) or not math.isfinite(self.magnitude)
                or abs(self.magnitude) > 1 or type(self.attention_active) is not bool):
            raise ValueError("Invalid training signal")
        if self.creature_id is None and self.attention_active:
            raise ValueError("Attention requires the selected creature")


class TrainingDetector:
    """A new feedback timestamp is required, even when the amount stays the same.

    Zoom alone produces attention, not reward. Initial attachment, a read gap,
    a load, or a changed creature establishes a baseline without replaying old
    feedback. A short release grace covers the engine committing feedback just
    after leaving attention mode. A sample is not evidence of every hand gesture.
    """
    def __init__(self, *, max_gap: float = 0.75, release_grace: float = 0.75,
                 retention: float = 30):
        if any(not math.isfinite(v) or v <= 0 for v in (max_gap, release_grace, retention)):
            raise ValueError("Invalid detector timing")
        self.max_gap, self.release_grace, self.retention = max_gap, release_grace, retention
        self._nonce = uuid.uuid4().hex[:16]
        self._epoch = 0
        self.reset()

    def reset(self) -> TrainingContext:
        self._previous: TrainingSample | None = None
        self._last_attention = -math.inf
        self._events: tuple[TrainingFeedback, ...] = ()
        self._epoch += 1
        return TrainingContext()

    def update(self, sample: TrainingSample) -> TrainingContext:
        previous = self._previous
        if previous is not None and (
                (sample.session, sample.creature_id) != (previous.session, previous.creature_id)
                or not 0 <= sample.observed_at - previous.observed_at <= self.max_gap
                or sample.game_tick < previous.game_tick
                or sample.feedback_tick < previous.feedback_tick
                or (sample.feedback_tick == previous.feedback_tick and
                    sample.magnitude != previous.magnitude)):
            self.reset()
            previous = None
        if sample.feedback_tick > sample.game_tick:
            return self.reset()
        if sample.creature_id is None:
            self.reset()
            return TrainingContext(False)
        if sample.attention_active:
            self._last_attention = sample.observed_at
        self._events = tuple(e for e in self._events
                             if sample.observed_at - e.observed_at <= self.retention)
        if (previous is not None and sample.feedback_tick > previous.feedback_tick
                and sample.feedback_tick >= previous.game_tick
                and sample.observed_at - self._last_attention <= self.release_grace
                and sample.magnitude != 0):
            kind = "praise" if sample.magnitude > 0 else "punishment"
            event = TrainingFeedback(
                f"{self._nonce}:{self._epoch}:{sample.feedback_tick}", kind,
                sample.observed_at, magnitude=sample.magnitude)
            self._events = (*self._events, event)[-8:]
        self._previous = sample
        return TrainingContext(sample.attention_active, self._events)
