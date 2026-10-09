import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from bwv.control import ControlGate, ControlObservation, from_observer
from bwv.planner import CreaturePlanner


def playable(now=0, **changes):
    base = ControlObservation("save-A", int(now * 10), now, creature_id="creature-A",
        connected=True, supported=True, world_ready=True, loading=False, paused=False,
        cutscene=False, player_has_control=True, creature_present=True,
        script_owns_creature=False, in_temple=False, candidates=("eat:fruit-1", "rest:home"))
    return replace(base, **changes)


class GateTests(unittest.TestCase):
    def test_short_monitoring_gap_with_fresh_sample_waits_for_settling(self):
        gate=ControlGate(resume_delay=.75,preserve_stale_seconds=5)
        gate.update(playable(10),10);gate.update(playable(11),11);lease=gate.issue(11)
        gate.update(playable(13),13)
        self.assertTrue(gate.waiting_for_observation)
        self.assertFalse(gate.valid(lease,13))
        gate.update(playable(14),14)
        self.assertTrue(gate.valid(lease,14))

    def test_optional_sample_gap_suspends_lease_until_fresh_without_reviving_scene_locks(self):
        gate=ControlGate(resume_delay=0,preserve_stale_seconds=5)
        gate.update(playable(10),10);lease=gate.issue(10)
        self.assertFalse(gate.valid(lease,12))
        self.assertTrue(gate.waiting_for_observation)
        self.assertEqual(gate.generation,lease.generation)
        gate.update(playable(12.1),12.1)
        self.assertTrue(gate.valid(lease,12.1))
        self.assertFalse(gate.waiting_for_observation)
        gate.poll(14)
        gate.update(playable(14.1,paused=True),14.1)
        self.assertFalse(gate.waiting_for_observation)
        gate.update(playable(14.2),14.2)
        self.assertFalse(gate.valid(lease,14.2))

    def test_sample_gap_expiry_and_creature_change_revoke_retained_lease(self):
        for change in ('timeout','identity'):
            gate=ControlGate(resume_delay=0,preserve_stale_seconds=5)
            gate.update(playable(10),10);lease=gate.issue(10);gate.poll(12)
            if change=='timeout':
                gate.poll(16);gate.update(playable(16.1),16.1)
            else:gate.update(playable(12.1,creature_id='another'),12.1)
            self.assertFalse(gate.valid(lease,16.1 if change=='timeout' else 12.1))

    def test_all_control_locks_revoke_and_require_stable_resume(self):
        for lock in [dict(in_temple=True), dict(creature_present=False, creature_id=None), dict(cutscene=True),
                     dict(script_owns_creature=True), dict(player_has_control=False),
                     dict(paused=True), dict(loading=True), dict(world_ready=False),
                     dict(connected=False), dict(supported=False), dict(cutscene=None)]:
            with self.subTest(lock=lock):
                gate = ControlGate()
                self.assertFalse(gate.update(playable(), 0).allowed)
                self.assertTrue(gate.update(playable(1), 1).allowed)
                old = gate.issue(1)
                self.assertFalse(gate.update(playable(2, **lock), 2).allowed)
                self.assertFalse(gate.valid(old, 2))
                self.assertFalse(gate.update(playable(3), 3).allowed)
                self.assertTrue(gate.update(playable(4), 4).allowed)
                self.assertFalse(gate.valid(old, 4))

    def test_stale_data_cannot_allow_a_call(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(), 0)
        lease = gate.issue(0)
        self.assertFalse(gate.valid(lease, 2))
        self.assertIn("stale", gate.poll(2).reasons)

    def test_local_cutscene_summary_only_after_resume(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(), 0)
        self.assertIsNone(gate.update(playable(1, cutscene=True,
            context_event="Side quest cutscene observed"), 1).summary)
        gate.update(playable(2, cutscene=True, script_owns_creature=True), 2)
        status = gate.update(playable(20), 20)
        self.assertIn("19.0s", status.summary)
        self.assertIn("Side quest cutscene observed", status.summary)
        self.assertIn("script directing the creature", status.summary)
        gate.acknowledge_summary(gate.issue(20), 20)
        self.assertIsNone(gate.poll(20).summary)

    def test_save_and_creature_changes_revoke_old_decisions(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(), 0)
        lease = gate.issue(0)
        gate.update(playable(1, creature_id="creature-B"), 1)
        self.assertFalse(gate.valid(lease, 1))
        lease = gate.issue(1)
        gate.update(playable(2, session="save-B"), 2)
        self.assertFalse(gate.valid(lease, 2))

    def test_save_load_discards_future_context(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(1, cutscene=True, context_event="future event"), 1)
        self.assertIsNotNone(gate.update(playable(2), 2).summary)
        self.assertIsNone(gate.update(playable(3, session="older-save"), 3).summary)

    def test_unknown_and_malformed_boolean_flags_deny(self):
        gate = ControlGate(resume_delay=0)
        for value in [None, "false", 0, 1]:
            self.assertFalse(gate.update(playable(cutscene=value), 0).allowed)

    def test_observer_does_not_invent_control_from_vm_readiness(self):
        obs = from_observer({"protocol": 1, "pid": 42, "session": 1, "ticks": 3, "ready": True}, 0)
        self.assertFalse(ControlGate(resume_delay=0).update(obs, 0).allowed)

    def test_unknown_creature_identity_is_not_reported_as_verified_absence(self):
        gate = ControlGate(resume_delay=0)
        status = gate.update(playable(creature_id=None, creature_present=None), 0)
        self.assertIn("unverified_creature", status.reasons)
        self.assertNotIn("no_creature", status.reasons)

    def test_cached_observer_frame_keeps_original_age(self):
        obs = from_observer({"protocol": 1, "ticks": 3, "source_age_ms": 6000}, 10)
        self.assertEqual(obs.observed_at, 4)
        self.assertIn("stale", ControlGate().update(obs, 10).reasons)

    def test_cached_frame_clock_skew_does_not_fake_disconnect_or_refresh_age(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(1), 1)
        lease = gate.issue(1)
        repeated = playable(1, observed_at=1.01)
        self.assertTrue(gate.update(repeated, 1.2).allowed)
        self.assertTrue(gate.valid(lease, 1.2))
        self.assertEqual(gate.observation.observed_at, 1)
        status = gate.update(playable(1, observed_at=3), 3)
        self.assertFalse(status.allowed)
        self.assertIn("stale", status.reasons)

    def test_reordered_or_future_frame_denies(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(1), 1)
        self.assertFalse(gate.update(playable(0), 2).allowed)
        self.assertFalse(gate.update(playable(0), 2.5).allowed)
        self.assertFalse(gate.update(playable(100), 3).allowed)

    def test_duplicate_sequence_cannot_refresh_or_unlock(self):
        gate = ControlGate(resume_delay=0)
        gate.update(playable(1, cutscene=True), 1)
        self.assertFalse(gate.update(playable(2, sequence=10), 2).allowed)
        self.assertTrue(gate.update(playable(3), 3).allowed)
        self.assertFalse(gate.update(playable(3), 5).allowed)

    def test_monitoring_gap_requires_stable_resume_and_revokes(self):
        gate = ControlGate()
        gate.update(playable(), 0)
        gate.update(playable(1), 1)
        lease = gate.issue(1)
        self.assertFalse(gate.update(playable(10), 10).allowed)
        self.assertTrue(gate.update(playable(11), 11).allowed)
        self.assertFalse(gate.valid(lease, 11))


class ImmediateProvider:
    def __init__(self): self.requests = []
    async def choose(self, request):
        self.requests.append(request)
        return request.candidates[0]


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_rejects_cancellation_resistant_reply(self):
        entered = asyncio.Event()
        class StubbornProvider:
            async def choose(self, request):
                entered.set()
                try: await asyncio.sleep(60)
                except asyncio.CancelledError: return request.candidates[0]
        planner = CreaturePlanner(StubbornProvider(), clock=lambda: 0, gate=ControlGate(resume_delay=0))
        planner.update(playable())
        planner.tick()
        await entered.wait()
        await planner.close()
        self.assertIsNone(planner.take_proposal())
        planner.tick()
        self.assertEqual(planner.calls_started, 1)

    async def test_timeout_rejects_provider_that_swallows_cancellation(self):
        class StubbornProvider:
            async def choose(self, request):
                try: await asyncio.sleep(60)
                except asyncio.CancelledError: return request.candidates[0]
        planner = CreaturePlanner(StubbornProvider(), clock=lambda: 0,
            gate=ControlGate(resume_delay=0), timeout=0.01)
        planner.update(playable())
        planner.tick()
        # Timer cancellation is authoritative even if a coarse clock has not
        # advanced to the separately sampled wall-clock deadline yet.
        with patch('bwv.planner.time') as planner_time:
            planner_time.monotonic.return_value=0
            await planner._task
        self.assertIsNone(planner.take_proposal())
        self.assertEqual(planner.results_discarded, 1)
        await planner.close()

    async def test_no_calls_without_control(self):
        provider = ImmediateProvider()
        now = [0]
        planner = CreaturePlanner(provider, clock=lambda: now[0], gate=ControlGate(resume_delay=0))
        for locks in [dict(creature_present=False, creature_id=None), dict(cutscene=True),
                      dict(script_owns_creature=True), dict(player_has_control=False), dict(cutscene=None)]:
            now[0] += 1
            planner.update(playable(now[0], **locks))
            planner.tick()
            await asyncio.sleep(0)
        self.assertEqual(provider.requests, [])
        self.assertEqual(planner.calls_started, 0)
        await planner.close()

    async def test_late_response_after_cutscene_cannot_execute(self):
        entered, complete = asyncio.Event(), asyncio.Event()
        class SlowProvider:
            async def choose(self, request):
                entered.set()
                try: await complete.wait()
                except asyncio.CancelledError: await complete.wait()
                return request.candidates[0]
        now = [0]
        planner = CreaturePlanner(SlowProvider(), clock=lambda: now[0], gate=ControlGate(resume_delay=0))
        planner.update(playable())
        planner.tick()
        await entered.wait()
        now[0] = 1
        planner.update(playable(1, cutscene=True))
        await asyncio.sleep(0)
        now[0] = 2
        planner.update(playable(2))
        complete.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIsNone(planner.take_proposal())
        self.assertEqual(planner.calls_started, 1)
        await planner.close()

    async def test_queued_proposal_is_removed_on_loss_of_control(self):
        now = [0]
        planner = CreaturePlanner(ImmediateProvider(), clock=lambda: now[0], gate=ControlGate(resume_delay=0))
        planner.update(playable())
        planner.tick()
        await asyncio.sleep(0)
        now[0] = 1
        planner.update(playable(1, script_owns_creature=True))
        self.assertIsNone(planner.take_proposal())
        await planner.close()

    async def test_resume_gets_summary_and_budget_applies(self):
        now = [0]
        provider = ImmediateProvider()
        planner = CreaturePlanner(provider, clock=lambda: now[0], gate=ControlGate(resume_delay=0), hourly_limit=1, cooldown=0)
        planner.update(playable(cutscene=True, context_event="side quest"))
        planner.tick()
        now[0] = 10
        planner.update(playable(10))
        planner.tick()
        await asyncio.sleep(0)
        self.assertIn("side quest", provider.requests[0].interruption_summary)
        self.assertEqual(planner.take_proposal().candidate, "eat:fruit-1")
        planner.tick()
        await asyncio.sleep(0)
        self.assertEqual(planner.calls_started, 1)
        await planner.close()


if __name__ == "__main__": unittest.main()
