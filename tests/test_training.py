import asyncio
from dataclasses import replace
import json
import unittest

from bwv.control import ControlGate, ControlObservation
from bwv.mock import MockProvider
from bwv.holding import HoldingContext
from bwv.creature import CreatureContext
from bwv.planner import CreaturePlanner
from bwv.training import TrainingContext, TrainingFeedback


def observation(now=0, **changes):
    return replace(ControlObservation("test-save", int(now * 100), now,
        creature_id="selected-monkey", connected=True, supported=True,
        world_ready=True, loading=False, paused=False, cutscene=False,
        player_has_control=True, creature_present=True, script_owns_creature=False, in_temple=False,
        training=TrainingContext(attention_active=True)), **changes)


class TrainingGateTests(unittest.TestCase):
    def test_verified_control_allows_reflection_without_attention_or_actions(self):
        gate = ControlGate(resume_delay=0)
        self.assertTrue(gate.update(observation(), 0).allowed)
        for state in (None, False):
            gate = ControlGate(resume_delay=0)
            result = gate.update(observation(training=TrainingContext(state)), 0)
            self.assertTrue(result.allowed)
            self.assertNotIn("no_actions", result.reasons)

    def test_training_does_not_override_real_control_locks(self):
        for lock in (dict(cutscene=True), dict(paused=True), dict(loading=True),
                     dict(script_owns_creature=True), dict(player_has_control=False),
                     dict(creature_present=False, creature_id=None),
                     dict(world_ready=False), dict(supported=False),
                     dict(connected=False), dict(observed_at=-10)):
            with self.subTest(lock=lock):
                self.assertFalse(ControlGate(resume_delay=0).update(observation(**lock), 0).allowed)

    def test_feedback_is_bounded_and_identified(self):
        event = TrainingFeedback("stroke-1", "praise", 0)
        self.assertIsNone(event.behavior_id)
        with self.assertRaises(ValueError):
            TrainingContext(True, (event, event))
        with self.assertRaises(ValueError):
            TrainingContext(True, tuple(TrainingFeedback(str(i), "praise", i) for i in range(9)))
        with self.assertRaises(ValueError):
            TrainingContext("zoomed")


class TrainingPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_context_never_calls_provider_or_consumes_cooldown(self):
        now = [0]
        provider = MockProvider()
        planner = CreaturePlanner(provider, clock=lambda: now[0],
            gate=ControlGate(resume_delay=0), cooldown=60, hourly_limit=1)
        try:
            for tick, species in ((0,'monkey'),(.1,'horse')):
                now[0] = tick
                planner.update(observation(tick,training=TrainingContext(False),
                    creature=CreatureContext(species,'engine')))
                planner.tick()
                self.assertEqual(planner.calls_started,0)
                self.assertEqual(provider.requests,[])
                self.assertIsNone(planner._task)
            now[0] = .2
            planner.update(observation(.2,training=TrainingContext(False),
                creature=CreatureContext('horse','engine'),
                holding=HoldingContext(False,hunger_desire=1)))
            planner.tick()
            await planner._task
            self.assertEqual(planner.calls_started,1)
            self.assertIn('"species":"horse"',provider.requests[0].to_prompt().system)
            self.assertIsNone(planner.take_proposal())
        finally:
            await planner.close()

    async def test_free_play_status_reflection_without_candidates_cannot_create_action(self):
        provider = MockProvider(reply='invented-action')
        planner = CreaturePlanner(provider, clock=lambda: 0, gate=ControlGate(resume_delay=0))
        try:
            planner.update(observation(training=TrainingContext(False),
                holding=HoldingContext(False, hunger_desire=1)))
            planner.tick()
            await planner._task
            self.assertTrue(provider.requests[0].reflection_only)
            self.assertEqual(planner.results_discarded, 1)
            self.assertIsNone(planner.take_proposal())
        finally:
            await planner.close()

    async def test_training_feedback_reaches_provider_without_actions(self):
        for kind, behavior in (("praise", "eat:fruit-1"), ("punishment", None)):
            with self.subTest(kind=kind):
                magnitude = .4 if kind == "praise" else -.1
                context = TrainingContext(True, (TrainingFeedback("feedback-1", kind, 0,
                    behavior, magnitude),))
                provider = MockProvider()
                planner = CreaturePlanner(provider, clock=lambda: 0, gate=ControlGate(resume_delay=0))
                try:
                    self.assertTrue(planner.update(observation(training=context)).allowed)
                    planner.tick()
                    await planner._task
                    self.assertEqual(provider.requests[0].training, context)
                    payload = json.loads(provider.requests[0].to_prompt().context_json)
                    self.assertEqual(payload["feedback"][0]["magnitude"], magnitude)
                    self.assertTrue(provider.requests[0].reflection_only)
                    self.assertEqual(planner.reflections_completed, 1)
                    self.assertIsNone(planner.take_proposal())
                finally:
                    await planner.close()

    async def test_pending_thought_is_not_cancelled_by_training(self):
        now = [0]
        release = asyncio.Event()
        provider = MockProvider(release=release)
        planner = CreaturePlanner(provider, clock=lambda: now[0],
            gate=ControlGate(resume_delay=0), cooldown=0)
        try:
            planner.update(observation(candidates=("walk:tree",), training=TrainingContext(False)))
            planner.tick()
            await provider.entered.wait()
            now[0] = 0.5
            planner.update(observation(0.5, training=TrainingContext(True,
                (TrainingFeedback("stroke-2", "praise", 0.5),))))
            self.assertEqual(planner._task.cancelling(), 0)
            release.set()
            await planner._task
            self.assertIsNone(planner.take_proposal())  # Old movement is no longer offered.
            planner.tick()
            await planner._task
            self.assertTrue(provider.requests[-1].reflection_only)
            self.assertEqual(provider.requests[-1].training.feedback[0].kind, "praise")
            self.assertEqual(planner.reflections_completed, 1)
        finally:
            release.set()
            await planner.close()

    async def test_training_drops_queued_movement_and_reflects(self):
        now = [0]
        planner = CreaturePlanner(MockProvider(), clock=lambda: now[0],
            gate=ControlGate(resume_delay=0), cooldown=0)
        try:
            planner.update(observation(candidates=("walk:tree",)))
            planner.tick()
            await planner._task
            now[0] = 0.5
            planner.update(observation(0.5))
            planner.tick()
            await planner._task
            self.assertEqual(planner.calls_started, 2)
            self.assertEqual(planner.reflections_completed, 1)
            self.assertIsNone(planner.take_proposal())
        finally:
            await planner.close()

    async def test_reflection_cannot_smuggle_a_game_action(self):
        planner = CreaturePlanner(MockProvider(reply="walk:tree"), clock=lambda: 0,
            gate=ControlGate(resume_delay=0))
        try:
            planner.update(observation())
            planner.tick()
            await planner._task
            self.assertEqual(planner.results_discarded, 1)
            self.assertEqual(planner.reflections_completed, 0)
            self.assertIsNone(planner.take_proposal())
        finally:
            await planner.close()

    async def test_training_reflection_still_obeys_spending_limit(self):
        now = [0]
        planner = CreaturePlanner(MockProvider(), clock=lambda: now[0],
            gate=ControlGate(resume_delay=0), cooldown=0, hourly_limit=1)
        try:
            planner.update(observation())
            planner.tick()
            await planner._task
            now[0] = 1
            planner.update(observation(1))
            planner.tick()
            self.assertEqual(planner.calls_started, 1)
        finally:
            await planner.close()

    async def test_cutscene_discards_late_training_reflection(self):
        now = [0]
        release = asyncio.Event()
        provider = MockProvider(release=release, ignore_cancel=True)
        planner = CreaturePlanner(provider, clock=lambda: now[0], gate=ControlGate(resume_delay=0))
        try:
            planner.update(observation())
            planner.tick()
            await provider.entered.wait()
            now[0] = 0.5
            planner.update(observation(0.5, cutscene=True))
            await asyncio.sleep(0)
            now[0] = 1
            planner.update(observation(1))
            release.set()
            await planner._task
            self.assertEqual(planner.reflections_completed, 0)
            self.assertEqual(planner.results_discarded, 1)
        finally:
            release.set()
            await planner.close()
