from dataclasses import replace
import math
import unittest

from bwv.training_detector import TrainingDetector, TrainingSample


def sample(t=0, **changes):
    return replace(TrainingSample("save-A", "monkey-A", t,
        100 + round(t * 10), 90, 0.3, True), **changes)


class TrainingDetectorTests(unittest.TestCase):
    def test_zoom_only_does_not_replay_old_praise(self):
        detector = TrainingDetector()
        for i in range(101):
            context = detector.update(sample(i / 10))
            self.assertTrue(context.attention_active)
            self.assertEqual(context.feedback, ())

    def test_positive_and_negative_feedback_commit_on_release(self):
        for amount, kind in ((0.4, "praise"), (-0.1, "punishment")):
            detector = TrainingDetector()
            detector.update(sample())
            context = detector.update(sample(.1, feedback_tick=101, magnitude=amount,
                                             attention_active=False))
            event, = context.feedback
            self.assertEqual(event.kind, kind)
            self.assertAlmostEqual(event.magnitude, amount)
            self.assertIsNone(event.behavior_id)
            repeated = detector.update(sample(.2, feedback_tick=101, magnitude=amount,
                                               attention_active=False))
            self.assertEqual(repeated.feedback, context.feedback)

    def test_same_amount_new_timestamp_is_new_feedback(self):
        detector = TrainingDetector()
        detector.update(sample())
        first = detector.update(sample(.1, feedback_tick=101, magnitude=1))
        second = detector.update(sample(.2, feedback_tick=102, magnitude=1))
        self.assertEqual(len(second.feedback), 2)
        self.assertNotEqual(first.feedback[0].event_id, second.feedback[1].event_id)

    def test_zero_result_and_unattended_changes_are_not_feedback(self):
        for attention, magnitude in ((True, 0), (False, -.3)):
            detector = TrainingDetector()
            detector.update(sample(attention_active=attention))
            self.assertFalse(detector.update(sample(.1, attention_active=attention,
                feedback_tick=101, magnitude=magnitude)).feedback)

    def test_late_release_is_not_attributed_to_old_attention(self):
        detector = TrainingDetector()
        detector.update(sample())
        detector.update(sample(.5, attention_active=False))
        self.assertFalse(detector.update(sample(1, attention_active=False,
            feedback_tick=110, magnitude=-.5)).feedback)

    def test_load_creature_change_gap_and_clock_rewind_rebaseline(self):
        changes = [dict(session="save-B"), dict(creature_id="cow-B"),
                   dict(observed_at=3), dict(observed_at=-1), dict(game_tick=10, feedback_tick=9)]
        for change in changes:
            with self.subTest(change=change):
                detector = TrainingDetector()
                detector.update(sample())
                detector.update(sample(.1, feedback_tick=101))
                self.assertFalse(detector.update(replace(sample(.2, feedback_tick=102), **change)).feedback)

    def test_disconnect_and_absence_clear_history(self):
        detector = TrainingDetector()
        detector.update(sample())
        self.assertEqual(len(detector.update(sample(.1, feedback_tick=101)).feedback), 1)
        self.assertFalse(detector.reset().feedback)
        self.assertFalse(detector.update(sample(.2, feedback_tick=101)).feedback)
        self.assertFalse(detector.update(sample(.3, creature_id=None, attention_active=False)).feedback)
        self.assertFalse(detector.update(sample(.4, feedback_tick=102)).feedback)

    def test_future_or_inconsistent_feedback_is_rejected(self):
        detector = TrainingDetector()
        detector.update(sample())
        self.assertIsNone(detector.update(sample(.1, feedback_tick=1000)).attention_active)
        detector.update(sample(.2, feedback_tick=102))
        self.assertFalse(detector.update(sample(.3, feedback_tick=102, magnitude=-.5)).feedback)

    def test_window_is_bounded_and_expires(self):
        detector = TrainingDetector(retention=2)
        detector.update(sample())
        for i in range(1, 12):
            context = detector.update(sample(i / 10, feedback_tick=100 + i))
        self.assertEqual(len(context.feedback), 8)
        for i in range(12, 35):
            context = detector.update(sample(i / 10, feedback_tick=111))
        self.assertFalse(context.feedback)

    def test_invalid_engine_values_do_not_become_feedback(self):
        for change in (dict(magnitude=math.nan), dict(magnitude=1.01),
                       dict(feedback_tick=-1), dict(attention_active=1)):
            with self.assertRaises(ValueError):
                sample(**change)
