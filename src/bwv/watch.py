"""Bounded read-only live smoke test: real observations, mock provider, no executor."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import math
import time

from .control import ControlObservation, from_observer
from .mock import MockProvider
from .pipe import snapshot
from .planner import CreaturePlanner
from .surroundings import SurroundingsContext
from .sampling import DetailRefresh


async def observations(pid: int, *, training: bool = False, control: bool = False,
                       leash: bool = False, miracles: bool = False, holding: bool = False,
                       experience: bool = False, detail_refresh=None):
    """Shared continuous read-only sampler; callers must close the async generator."""
    sequence = 0
    detail_refresh = detail_refresh if detail_refresh is not None else DetailRefresh()
    reader = None
    try:
        if training or control or leash or miracles or holding or experience:
            from .training_live import TrainingReader
            from .training_detector import TrainingDetector
            from .control_live import read_pause, with_pause, read_camera_control, with_camera_control, read_temple, with_temple
            from .leash_live import read_leash, with_leash
            from .miracle import MiracleDetector
            from .miracle_live import read_miracle
            from .holding_live import read_holding, with_holding
            from .god_hand_live import read_god_hand, with_god_hand
            from .god_hand import GodHandContext
            from .creature_live import read_creature, CreatureReadError
            from .surroundings_live import read_surroundings, SurroundingsReadError
            from .experience_live import read_experience
            from .experience import ExperienceTracker, Experience
            from .native_text_live import read_native_text
            from .environment_live import read_environment
            from .environment import Environment
            from .island import IslandContext
            from .island_live import read_island
            reader, detector = TrainingReader(pid), TrainingDetector()
            miracle_detector = MiracleDetector()
            experience_tracker = ExperienceTracker()
        while True:
            error = None
            camera = None
            stage = 'bridge'
            try:
                state = await asyncio.to_thread(snapshot, pid)
                observation = from_observer(state, time.monotonic())
                if reader is not None:
                    stage = 'pause'
                    pause = await asyncio.to_thread(read_pause, reader)
                    observation = with_pause(observation, pause, time.monotonic())
                    stage = 'camera'
                    camera = await asyncio.to_thread(read_camera_control, reader)
                    observation = with_camera_control(observation, camera, time.monotonic())
                    stage = 'temple'
                    temple = await asyncio.to_thread(read_temple, reader)
                    observation = with_temple(observation, temple, time.monotonic())
                    blocked = pause.paused or observation.cutscene is True or observation.in_temple is not False
                    if blocked:
                        experience_tracker.reset()
                        detail_refresh.clear()
                    # Combined observations have their own sequence. The VM's
                    # cached source time still governs freshness and permission.
                    observation = replace(observation, sequence=sequence)
                    if not blocked:
                        stage = 'creature'
                        species_signal = await asyncio.to_thread(read_creature, reader, state)
                        observation = replace(observation, creature_id=species_signal.creature_id,
                                              creature=species_signal.context,
                                              script_owns_creature=species_signal.script_owns_creature)
                        detail_identity=(observation.session,observation.creature_id,observation.creature.species)
                        read_details=detail_refresh.due(detail_identity,time.monotonic(),state.get('ticks'))
                        detail_started=time.monotonic()
                        detail_revision=detail_refresh.requested
                        stage = 'environment'
                        try:
                            environment=(await asyncio.to_thread(read_environment, reader, state) if read_details
                                         else detail_refresh.value[0])
                            observation = replace(observation, environment=environment)
                        except (OSError, ValueError) as failure:
                            observation = replace(observation, environment=Environment(validation='read_unavailable'))
                            error = f'{stage}:{type(failure).__name__}'
                        stage = 'surroundings'
                        try:
                            surroundings_signal = (await asyncio.to_thread(read_surroundings, reader, state) if read_details
                                                   else detail_refresh.value[1])
                        except (OSError, ValueError) as failure:
                            # Optional context must not erase a fresh creature
                            # identity or other independent sensors. Clear this
                            # frame's context; never retain stale counts/targets.
                            surroundings_signal = None
                            observation = replace(observation, surroundings=SurroundingsContext())
                            reason = failure.reason if isinstance(failure, SurroundingsReadError) else type(failure).__name__
                            error = f'{stage}:{reason}'
                        if surroundings_signal is not None:
                            if (surroundings_signal.session != observation.session or
                                    surroundings_signal.creature_id != observation.creature_id):
                                raise ValueError('Creature changed during surroundings observation')
                            observation = replace(observation, surroundings=surroundings_signal.context)
                            if surroundings_signal.context.town_read_errors:
                                error = 'towns:read_unavailable'
                        stage = 'island'
                        island_error = None
                        try:
                            island_signal = (await asyncio.to_thread(read_island, reader, state) if read_details
                                             else detail_refresh.value[2])
                            if (island_signal.session != observation.session or
                                    island_signal.creature_id != observation.creature_id):
                                raise ValueError('Island identity changed during observation')
                            observation = replace(observation, island=island_signal.context)
                            if island_signal.context.read_errors:island_error = 'island:details_unavailable'
                        except (OSError, ValueError) as failure:
                            from .island_live import IslandSample
                            island_signal = IslandSample(observation.session, observation.creature_id, IslandContext(read_errors=1))
                            observation = replace(observation, island=island_signal.context)
                            island_error = f'island:{type(failure).__name__}'
                        if read_details:
                            if surroundings_signal is None or error:
                                # Failed reads are never relabelled as fresh cached facts.
                                detail_refresh.clear()
                                detail_refresh.completed=detail_revision
                            else:
                                detail_refresh.store(detail_identity,(observation.environment,surroundings_signal,island_signal),
                                    detail_started,round((time.monotonic()-detail_started)*1000,2),detail_revision)
                        # Optional roster errors stay visible, but do not force a
                        # whole surroundings rescan on every fast control sample.
                        error = error or island_error
                        if surroundings_signal is not None and surroundings_signal.context.portal_read_errors:
                            # Keep optional portal diagnostics visible without
                            # turning an unreadable export into a tight rescan loop.
                            error = error or 'portals:read_unavailable'
                    if training:
                        stage = 'training'
                        if blocked:
                            detector.reset()
                        else:
                            signal = await asyncio.to_thread(reader.sample, state)
                            if (signal.session != observation.session or
                                    signal.creature_id != observation.creature_id):
                                raise ValueError('Creature changed during training observation')
                            context = detector.update(signal)
                            observation = replace(observation,
                                creature_id=signal.creature_id, training=context)
                    if leash and not blocked:
                        stage = 'leash'
                        leash_signal = await asyncio.to_thread(read_leash, reader, state)
                        observation = with_leash(observation, leash_signal, time.monotonic())
                    if holding and not blocked:
                        stage = 'holding'
                        holding_signal = await asyncio.to_thread(read_holding, reader, state)
                        if (holding_signal.session != observation.session or
                                (observation.creature_id is not None and
                                 holding_signal.creature_id != observation.creature_id)):
                            raise ValueError('Creature changed during combined observation')
                        observation = with_holding(observation, holding_signal, time.monotonic())
                        stage = 'god_hand'
                        try:
                            hand_signal = await asyncio.to_thread(read_god_hand, reader, state)
                            observation = with_god_hand(observation, hand_signal, time.monotonic())
                            if hand_signal.context.detail_read_errors:
                                error = error or 'god_hand:details_unavailable'
                        except (OSError, ValueError) as failure:
                            observation = replace(observation, god_hand=GodHandContext())
                            error = error or f'{stage}:{type(failure).__name__}'
                    if experience and not blocked:
                        stage = 'experience'
                        try:
                            experience_signal = await asyncio.to_thread(read_experience,reader,state)
                            if (experience_signal.session != observation.session
                                    or experience_signal.creature_id != observation.creature_id):
                                raise ValueError('Creature changed during experience read')
                            context = experience_tracker.update(experience_signal,time.monotonic())
                            observation = replace(observation,experience=context)
                        except (OSError,ValueError) as failure:
                            # New sensor failure must not suppress the established
                            # observer or invent a health delta across the gap.
                            experience_tracker.reset()
                            observation = replace(observation,experience=Experience(validation='read_failed_live_qa_pending'))
                            error = f'{stage}:{type(failure).__name__}'
                        # UI messages have their own optional sensor. Its failure
                        # must not erase body readings or block creature control.
                        stage = 'native_text'
                        try:
                            text_signal = await asyncio.to_thread(read_native_text, reader, state)
                            if (text_signal.session != observation.session
                                    or text_signal.creature_id != observation.creature_id):
                                raise ValueError('Creature changed during text observation')
                            message = text_signal.message
                            observation = replace(observation, experience=replace(observation.experience,
                                hover_thought_text=message.text if message and message.kind == 'instinct' else None,
                                native_training_feedback=(message,) if message and message.kind != 'instinct' else (),
                                native_text_validation='owner_linked_native_help'))
                        except (OSError, ValueError):
                            observation = replace(observation, experience=replace(observation.experience,
                                hover_thought_text=None, native_training_feedback=(),
                                native_text_validation='read_unavailable'))
                    if miracles:
                        stage = 'miracles'
                        if blocked:
                            miracle_detector.reset()
                        else:
                            try:
                                miracle_signal = await asyncio.to_thread(read_miracle, reader, state)
                            except (OSError, ValueError) as failure:
                                # Missing optional knowledge must not hide the
                                # creature or erase independently verified control
                                # facts. Drop knowledge/events until the next read.
                                miracle_signal = None
                                observation = replace(observation, miracles=miracle_detector.reset())
                                error = f'{stage}:{type(failure).__name__}'
                            if miracle_signal is not None and (miracle_signal.session != observation.session or
                                    (observation.creature_id is not None and
                                     miracle_signal.creature_id != observation.creature_id)):
                                raise ValueError('Creature changed during combined observation')
                            if miracle_signal is not None:
                                observation = replace(observation, creature_id=miracle_signal.creature_id,
                                    miracles=miracle_detector.update(miracle_signal))
            except (OSError, ValueError) as failure:
                detail_refresh.clear()
                if reader is not None:
                    detector.reset()
                    miracle_detector.reset()
                    experience_tracker.reset()
                # Fixed stage and exception class only: OS error messages may
                # contain local file paths or other private data.
                error = f'{stage}:{type(failure).__name__}'
                if reader is not None and isinstance(failure, (CreatureReadError, SurroundingsReadError)):
                    error = f'{stage}:{failure.reason}'
                state = {}
                observation = ControlObservation("disconnected", 0, time.monotonic())
            state={**state,'detailed_sensors':detail_refresh.diagnostics(time.monotonic())}
            yield observation, state, camera, error
            sequence += 1
            await asyncio.sleep(0.25)
    finally:
        if reader is not None:
            reader.close()


async def run(pid: int, seconds: float = 5, *, training: bool = False,
              control: bool = False, leash: bool = False, miracles: bool = False,
              holding: bool = False, experience: bool = False) -> dict:
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError("Watch duration must be between 0 and 60 seconds")
    provider = MockProvider()
    planner = CreaturePlanner(provider)
    samples = []
    deadline = time.monotonic() + seconds
    stream = observations(pid, training=training, control=control, leash=leash,
                          miracles=miracles, holding=holding, experience=experience)
    has_reader = training or control or leash or miracles or holding or experience
    try:
        async for observation, state, camera, error in stream:
            planner.update(observation)
            status = planner.tick()
            # Diagnostic metadata only; do not write raw game globals to reports.
            samples.append({"session": state.get("session"), "ticks": state.get("ticks"),
                "source_age_ms": state.get("source_age_ms"), "allowed": status.allowed,
                "reasons": status.reasons, "error": error,
                **({"paused": observation.paused} if has_reader else {}),
                **({"creature": asdict(observation.creature)} if has_reader else {}),
                **({"script_camera_owned": camera.script_owned if camera else None,
                    "cutscene": observation.cutscene, "in_temple": observation.in_temple} if has_reader else {}),
                **({"training": asdict(observation.training)} if training else {}),
                **({"leash": asdict(observation.leash)} if leash else {}),
                **({"miracles": asdict(observation.miracles)} if miracles else {}),
                **({"holding": asdict(observation.holding)} if holding else {}),
                **({"experience": asdict(observation.experience)} if experience else {})})
            if time.monotonic() >= deadline:
                break
        return {"mode": "live_observer_mock_provider", "pid": pid,
                "network_calls": 0, "game_actions": 0,
                "mock_requests": planner.calls_started, "samples": samples}
    finally:
        await stream.aclose()
        await planner.close()
