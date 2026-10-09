from dataclasses import replace
import json
import unittest

from bwv.control import ControlGate
from bwv.miracle import HealKnowledge, MiracleContext, MiracleDetector, MiracleLearning, MiracleSample, SpellSample
from bwv.mock import MockProvider
from bwv.planner import CreaturePlanner
from test_training import observation


def sample(**changes):
    return replace(MiracleSample('session', 'monkey', 0, 100, 10, 90, 4, 91), **changes)


class MiracleDetectorTests(unittest.TestCase):
    def test_island_clock_reset_keeps_knowledge_without_replaying_learning(self):
        detector = MiracleDetector()
        before = sample(game_tick=1000, heal_counter=16, heal_learning_tick=900,
                        heal_learning_requirement=15.45,
                        other_spells=(SpellSample(22, 20, 950, 18.54),))
        detector.update(before)
        loaded = replace(before, observed_at=.2, game_tick=10)
        context = detector.update(loaded)
        self.assertTrue(context.heal_knowledge.learning_threshold_met)
        self.assertEqual([k.learning_counter for k in context.knowledge], [16, 20])
        self.assertEqual(context.learning, ())
        later = replace(loaded, observed_at=.4, game_tick=12)
        self.assertEqual(detector.update(later), context)
        # A fresh registration can replace a larger saved timestamp, while
        # another spell still carries its timestamp from the previous island.
        learned = replace(later, observed_at=.6, game_tick=14,
                          heal_counter=17, heal_learning_tick=13)
        result = detector.update(learned)
        event, = result.learning
        self.assertEqual((event.spell, event.counter_delta), ('heal', 1))
        self.assertEqual(result.knowledge[-1].learning_tick, 950)
        self.assertEqual(detector.update(replace(learned, observed_at=.7)).learning, (event,))

    def test_retained_tick_is_not_replayed_when_island_clock_catches_up(self):
        detector = MiracleDetector()
        baseline = sample(game_tick=90, heal_learning_tick=91, heal_learning_requirement=15)
        for index, tick in enumerate((90, 91, 92)):
            result = detector.update(replace(baseline, game_tick=tick, observed_at=index*.2))
            self.assertIsNotNone(result.heal_knowledge)
            self.assertEqual(result.learning, ())

    def test_known_heal_survives_no_new_learning_after_player_cast(self):
        detector=MiracleDetector()
        baseline=sample(heal_counter=16,heal_learning_requirement=15.45)
        first=detector.update(baseline)
        self.assertTrue(first.heal_knowledge.learning_threshold_met)
        self.assertEqual(first.learning,())
        after=detector.update(replace(baseline,observed_at=.2,game_tick=102,player_cast_tick=102))
        self.assertEqual(after,first)
        changed=detector.update(replace(baseline,creature_id='new',observed_at=.4,
                                       game_tick=104,heal_counter=7))
        self.assertFalse(changed.heal_knowledge.learning_threshold_met)
        self.assertEqual(changed.learning,())

    def test_threshold_uses_current_requirement_and_does_not_round_it(self):
        self.assertFalse(HealKnowledge(15,15.45).learning_threshold_met)
        self.assertTrue(HealKnowledge(16,15.45).learning_threshold_met)
        self.assertTrue(HealKnowledge(15,15).learning_threshold_met)

    def test_initial_history_and_player_cast_alone_are_not_learning(self):
        detector = MiracleDetector()
        self.assertEqual(detector.update(sample()), MiracleContext())
        self.assertEqual(detector.update(sample(observed_at=.2, game_tick=102,
            player_cast_tick=102)), MiracleContext())

    def test_engine_registration_produces_one_weighted_event(self):
        detector = MiracleDetector()
        detector.update(sample())
        current = sample(observed_at=.2, game_tick=102, player_cast_tick=101,
                         heal_counter=7, heal_learning_tick=102)
        event, = detector.update(current).learning
        self.assertEqual((event.spell, event.counter_delta, event.counter_value), ('heal', 3, 7))
        self.assertEqual(detector.update(replace(current, observed_at=.4)).learning, (event,))

    def test_learning_registration_does_not_require_assuming_player_was_caster(self):
        detector = MiracleDetector()
        detector.update(sample())
        event, = detector.update(sample(observed_at=.2, game_tick=102,
            heal_learning_tick=102)).learning
        self.assertEqual(event.counter_delta, 0)  # Engine may rate-limit increments.

    def test_lifecycle_changes_and_inconsistent_counters_reset_baseline(self):
        for change in (dict(session='new'), dict(creature_id='other'),
                       dict(creature_id=None), dict(observed_at=2), dict(game_tick=99),
                       dict(heal_learning_tick=89), dict(heal_counter=3),
                       dict(heal_learning_tick=105), dict(heal_counter=5)):
            detector = MiracleDetector()
            detector.update(sample())
            current = replace(sample(observed_at=.2, game_tick=102), **change)
            self.assertEqual(detector.update(current), MiracleContext())

    def test_events_bounded_and_reset_changes_their_ids(self):
        detector = MiracleDetector()
        detector.update(sample())
        for i in range(1, 12):
            context = detector.update(sample(observed_at=i*.2, game_tick=100+i,
                heal_counter=4+i, heal_learning_tick=100+i))
        self.assertEqual(len(context.learning), 8)
        first_id = context.learning[-1].event_id
        detector.reset()
        detector.update(sample())
        event, = detector.update(sample(observed_at=.2, game_tick=111,
            heal_counter=15, heal_learning_tick=111)).learning
        self.assertNotEqual(first_id, event.event_id)

    def test_invalid_context_is_rejected(self):
        event = MiracleLearning('one', 0, 'heal', 3, 3)
        for events in ((event, event), (event,)*9, [event]):
            with self.assertRaises(ValueError):
                MiracleContext(events)
        with self.assertRaises(ValueError):
            MiracleLearning('one', 0, 'heal', 1, 3)


class MiraclePlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_registered_learning_reaches_prompt_and_locks_remain(self):
        context = MiracleContext((MiracleLearning('one', 0, 'heal', 7, 3),),HealKnowledge(16,15.45))
        provider = MockProvider()
        planner = CreaturePlanner(provider, clock=lambda: 0, gate=ControlGate(resume_delay=0))
        try:
            obs = observation(miracles=context)
            self.assertTrue(planner.update(obs).allowed)
            planner.tick()
            await planner._task
            prompt = provider.requests[0].to_prompt()
            data = json.loads(prompt.context_json)
            self.assertEqual(data['learning_events'][0]['spell'], 'heal')
            self.assertNotIn('counter_delta', prompt.context_json)
            self.assertEqual(data['learned_miracles'], ['heal'])
            self.assertNotIn('player_cast', prompt.context_json)
            self.assertEqual(planner.reflections_completed, 1)
            for lock in (dict(cutscene=True), dict(paused=True), dict(script_owns_creature=True),
                         dict(player_has_control=None), dict(supported=False)):
                self.assertFalse(ControlGate(resume_delay=0).update(replace(obs, **lock), 0).allowed)
        finally:
            await planner.close()
