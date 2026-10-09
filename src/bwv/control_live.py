"""Individually verified read-only control facts, never blanket permission."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time

from .control import ControlObservation
from .training_live import GAME_POINTER, TrainingReader


# Exact-build interoperability facts: Esc toggles this bit, and the engine
# simulation update tests it before advancing the game tick. Other bits are
# unrelated and are deliberately not interpreted as control permissions.
GAME_FLAGS = 0x14
PAUSED_MASK = 0x04
SCRIPT_POINTER = 0x250090
CAMERA_RELEASE, CAMERA_ENABLED, CAMERA_OWNER = 0x78, 0x80, 0xA8
INTERFACE_HAND = 0x3A0
HAND_MODE, HAND_HANDLERS, TEMPLE_MODE = 0x4878, 0x487C, 10


@dataclass(frozen=True)
class PauseSample:
    pid: int
    observed_at: float
    paused: bool

    def __post_init__(self):
        if (type(self.pid) is not int or not 0 < self.pid <= 0xFFFFFFFF or
                type(self.observed_at) not in (int, float) or
                not math.isfinite(self.observed_at) or type(self.paused) is not bool):
            raise ValueError("Invalid pause observation")


def read_pause(reader: TrainingReader) -> PauseSample:
    """Read the flag independently of a VM snapshot, which goes stale in pause.

    This supplies only pause state. It cannot establish world readiness, creature
    control, absence of a cutscene, or a fresh VM lifecycle observation.
    """
    reader.ensure_running()
    started = time.monotonic()
    for _ in range(3):
        game = reader.word(GAME_POINTER)
        reader.require_type(game, b".?AVGGame@@")
        paused = bool(reader.read(game + GAME_FLAGS, 1)[0] & PAUSED_MASK)
        if (reader.word(GAME_POINTER) != game or
                bool(reader.read(game + GAME_FLAGS, 1)[0] & PAUSED_MASK) != paused):
            continue
        now = time.monotonic()
        if now - started > .75:
            raise ValueError("Pause read exceeded freshness limit")
        return PauseSample(reader.pid, now, paused)
    raise ValueError("Pause state changed during observation")


def with_pause(observation: ControlObservation, pause: PauseSample,
               now: float) -> ControlObservation:
    # Never refresh the cached VM source time or fill in any other control fact.
    valid = (observation.session.startswith(f"{pause.pid}:") and math.isfinite(now)
             and 0 <= now - pause.observed_at <= .75)
    return replace(observation, paused=pause.paused if valid else None)


@dataclass(frozen=True)
class CameraControlSample:
    pid: int
    observed_at: float
    script_owned: bool | None
    task_number: int | None = None

    def __post_init__(self):
        PauseSample(self.pid, self.observed_at, False)
        if self.script_owned is not None and type(self.script_owned) is not bool:
            raise ValueError("Invalid camera ownership state")
        if self.task_number is not None and (
                type(self.task_number) is not int or not 0 < self.task_number <= 0x7FFFFFFF):
            raise ValueError("Invalid camera owner")
        if (self.script_owned is True) != (self.task_number is not None):
            raise ValueError("Camera ownership needs a verified task")


def camera_ownership(release: int, enabled: int, owner: int) -> tuple[bool | None, int | None]:
    if release == enabled == 0 and 0 < owner <= 0x7FFFFFFF:
        return True, owner
    if release == enabled == 1 and owner == 0:
        return False, None
    return None, None


def read_camera_control(reader: TrainingReader) -> CameraControlSample:
    reader.ensure_running()
    started = time.monotonic()
    for _ in range(3):
        game = reader.word(GAME_POINTER)
        reader.require_type(game, b".?AVGGame@@")
        script = reader.word(game + SCRIPT_POINTER)
        reader.require_type(script, b".?AVGScript@@")
        values = tuple(reader.word(script + offset)
                       for offset in (CAMERA_RELEASE, CAMERA_ENABLED, CAMERA_OWNER))
        if (reader.word(GAME_POINTER) != game or reader.word(game + SCRIPT_POINTER) != script
                or values != tuple(reader.word(script + offset)
                                   for offset in (CAMERA_RELEASE, CAMERA_ENABLED, CAMERA_OWNER))):
            continue
        now = time.monotonic()
        if now - started > .75:
            raise ValueError("Camera-control read exceeded freshness limit")
        owned, task = camera_ownership(*values)
        return CameraControlSample(reader.pid, now, owned, task)
    raise ValueError("Camera ownership changed during observation")


def with_camera_control(observation: ControlObservation, camera: CameraControlSample,
                        now: float) -> ControlObservation:
    valid = (observation.session.startswith(f"{camera.pid}:") and math.isfinite(now)
             and 0 <= now - camera.observed_at <= .75)
    if valid and camera.script_owned is True:
        return replace(observation, cutscene=True,
                       context_event="Scripted camera control active")
    # Clear ownership does not prove the absence of all other cutscene types.
    # Do not erase a lock established independently by another adapter either.
    return observation


@dataclass(frozen=True)
class TempleSample:
    pid: int
    observed_at: float
    in_temple: bool

    def __post_init__(self):
        PauseSample(self.pid, self.observed_at, self.in_temple)


def read_temple(reader: TrainingReader) -> TempleSample:
    reader.ensure_running()
    started = time.monotonic()
    for _ in range(3):
        head = reader._head()
        interface = head[4]
        reader.require_type(interface, b'.?AVGInterface@@')
        hand = reader.word(interface + INTERFACE_HAND)
        reader.require_type(hand, b'.?AVCHand@@')
        temple = reader.word(hand + HAND_HANDLERS + TEMPLE_MODE*4)
        reader.require_type(temple, b'.?AVHandStateCitadel@@')
        if reader.word(temple+4) != hand:
            raise ValueError('Temple handler owner mismatch')
        mode = reader.word(hand + HAND_MODE)
        if not 0 <= mode <= TEMPLE_MODE:
            raise ValueError('Unknown hand controller mode')
        final = reader._head()
        if (head[0:1]+head[2:] != final[0:1]+final[2:]
                or reader.word(interface+INTERFACE_HAND) != hand
                or reader.word(hand+HAND_MODE) != mode
                or reader.word(hand+HAND_HANDLERS+TEMPLE_MODE*4) != temple
                or reader.word(temple+4) != hand):
            continue
        now = time.monotonic()
        if now-started > .75:
            raise ValueError('Temple read exceeded freshness limit')
        return TempleSample(reader.pid, now, mode == TEMPLE_MODE)
    raise ValueError('Temple state changed during observation')


def with_temple(observation: ControlObservation, sample: TempleSample, now: float) -> ControlObservation:
    valid = (observation.session.startswith(f'{sample.pid}:') and math.isfinite(now)
             and 0 <= now-sample.observed_at <= .75)
    return replace(observation, in_temple=sample.in_temple if valid else None)
