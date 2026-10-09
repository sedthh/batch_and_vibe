"""Bounded player feedback for the creature's context, not a control lock."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal


@dataclass(frozen=True)
class TrainingFeedback:
    event_id: str
    kind: Literal["praise", "punishment"]
    observed_at: float
    # Only set when the adapter verifies which behavior the feedback targets.
    behavior_id: str | None = None
    # Signed final engine feedback for an interaction, not a gesture count.
    magnitude: float | None = None

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not 0 < len(self.event_id) <= 96:
            raise ValueError("Invalid feedback event identity")
        if self.kind not in ("praise", "punishment"):
            raise ValueError("Invalid feedback kind")
        if type(self.observed_at) not in (int, float) or not math.isfinite(self.observed_at):
            raise ValueError("Invalid feedback time")
        if self.behavior_id is not None and (
                not isinstance(self.behavior_id, str) or not 0 < len(self.behavior_id) <= 96):
            raise ValueError("Invalid feedback behavior identity")
        if self.magnitude is not None and (
                type(self.magnitude) not in (int, float) or
                not math.isfinite(self.magnitude) or not 0 < abs(self.magnitude) <= 1 or
                (self.magnitude > 0) != (self.kind == "praise")):
            raise ValueError("Invalid signed feedback magnitude")


@dataclass(frozen=True)
class TrainingContext:
    # Engine-confirmed attention mode; camera distance alone is not this.
    attention_active: bool | None = None
    feedback: tuple[TrainingFeedback, ...] = ()

    def __post_init__(self):
        if self.attention_active is not None and type(self.attention_active) is not bool:
            raise ValueError("Invalid training attention state")
        if (type(self.feedback) is not tuple or len(self.feedback) > 8 or
                any(not isinstance(event, TrainingFeedback) for event in self.feedback)):
            raise ValueError("Invalid training feedback window")
        if len({event.event_id for event in self.feedback}) != len(self.feedback):
            raise ValueError("Repeated feedback event identity")
