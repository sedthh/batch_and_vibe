"""Display derived live training context without providers or gameplay writes."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
import time

from .training_detector import TrainingDetector
from .training_live import TrainingReader


def run(pid: int, seconds: float = 15, *, emit=print) -> dict:
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Training watch duration must be between 0 and 300 seconds")
    detector = TrainingDetector()
    count = failures = 0
    previous = None
    events: set[str] = set()
    with TrainingReader(pid) as reader:
        deadline = time.monotonic() + seconds
        emit(json.dumps({"recording": True, "mode": "read_only_training", "pid": pid,
                         "seconds": seconds, "provider_calls": 0, "game_actions": 0}))
        while time.monotonic() < deadline:
            try:
                sample = reader.sample()
                context = detector.update(sample)
                row = {"session": sample.session, "creature_id": sample.creature_id,
                       "training": asdict(context), "error": None}
                events.update(event.event_id for event in context.feedback)
                count += 1
            except (OSError, ValueError) as error:
                failures += 1
                context = detector.reset()
                row = {"training": asdict(context), "error": str(error)}
            if row != previous:
                emit(json.dumps(row, allow_nan=False))
                previous = row
            time.sleep(0.05)
    return {"mode": "read_only_training", "samples": count, "read_failures": failures,
            "feedback_events": len(events), "provider_calls": 0, "game_actions": 0}
