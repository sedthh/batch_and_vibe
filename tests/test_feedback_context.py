import unittest

from bwv.feedback_context import FeedbackContext


def frame(now, events=(), *, plan=None, attention=False, creature='creature'):
    return {'observation': {'session': 'session', 'creature_id': creature,
        'training': {'attention_active': attention, 'feedback': list(events)}},
        'prompt': {'context': {'instinct': {'native_plan': plan}} if plan else {}}}


def feedback(now, key='one', kind='praise', **extra):
    return {'event_id': key, 'kind': kind, 'observed_at': now,
            'magnitude': .3 if kind == 'praise' else -.2, **extra}


class FeedbackTests(unittest.TestCase):
    def test_targeted_native_intentions_survive_between_ticks_without_claiming_success(self):
        context=FeedbackContext()
        source=frame(1,plan='Kick')
        source['prompt']['context']['instinct']['intended_target']={'kind':'fence','observed_object_id':'a'*24}
        context.ingest(source,1)
        sent=context.project({})
        event=sent['events']['native_activity_observed'][0]
        self.assertEqual(event['native_plan'],'kick')
        self.assertEqual(event['intended_target']['kind'],'fence')
        self.assertEqual(event['status'],'observed_intention_not_confirmed')
        context.ingest(source,2)
        self.assertEqual(len(context.pending_activity),1)
        context.ingest(frame(70,plan='Sleep'),70)
        self.assertEqual(context.project({})['events']['native_activity_observed'],[event])
        context.accepted(sent)
        self.assertEqual(context.project({}),{})
        for i in range(20):
            source['prompt']['context']['instinct']['intended_target']['observed_object_id']=f'{i:024x}'
            context.ingest(source,71+i)
        self.assertEqual(len(context.pending_activity),16)
        context.ingest(frame(100,creature='another'),100)
        self.assertEqual(context.project({}),{})

    def test_feedback_survives_long_tick_and_does_not_become_current(self):
        context = FeedbackContext()
        context.ingest(frame(10, [feedback(10)], attention=True), 10)
        first = context.project({})
        self.assertEqual(first['activity']['recent_player_feedback'], 'praise')
        context.ingest(frame(260), 260)
        later = context.project({})
        self.assertNotIn('activity', later)
        self.assertEqual(later['events'], first['events'])
        context.accepted(later)
        self.assertEqual(context.project({}), {})

    def test_late_reply_only_acknowledges_events_in_its_request(self):
        context = FeedbackContext()
        context.ingest(frame(10, [feedback(10)]), 10)
        sent = context.project({})
        context.ingest(frame(11, [feedback(10), feedback(11, 'two', 'punishment')]), 11)
        context.accepted(sent)
        self.assertEqual([e['event_id'] for e in context.project({})['events']['player_feedback']], ['two'])
        context.ingest(frame(12, [feedback(10), feedback(11, 'two', 'punishment')]), 12)
        self.assertEqual(len(context.pending), 1)

    def test_recent_plans_are_temporal_context_never_claimed_training_reason(self):
        context = FeedbackContext()
        context.ingest(frame(5, plan='CastWater'), 5)
        context.ingest(frame(7, plan='EatFood'), 7)
        context.ingest(frame(10, [feedback(9)], plan='LookAround'), 10)
        event = context.project({})['events']['player_feedback'][0]
        self.assertEqual(event['recent_native_plans_not_confirmed_reasons'], ['cast water', 'eat food'])
        self.assertEqual(event['training_reason'], 'unknown')
        self.assertNotIn('confirmed_training_behavior', event)

    def test_confirmed_reason_is_only_taken_from_sensor(self):
        context = FeedbackContext()
        context.ingest(frame(10, [feedback(10, behavior_id='eating grain')]), 10)
        event = context.project({})['events']['player_feedback'][0]
        self.assertEqual(event['confirmed_training_behavior'], 'eating grain')
        self.assertNotIn('training_reason', event)

    def test_identity_change_drops_previous_creatures_feedback(self):
        context = FeedbackContext()
        context.ingest(frame(10, [feedback(10)]), 10)
        context.ingest(frame(11, creature='different'), 11)
        self.assertEqual(context.project({}), {})

    def test_overflow_is_bounded_and_reported_without_losing_new_counts(self):
        context = FeedbackContext()
        for n in range(70):
            context.ingest(frame(n, [feedback(n, str(n))]), n)
        sent = context.project({})
        self.assertEqual(len(sent['events']['player_feedback']), 64)
        self.assertEqual(sent['events']['older_player_feedback_counts'], {'praise': 6})
        context.ingest(frame(71, [feedback(71, 'last')]), 71)
        context.accepted(sent)
        remaining = context.project({})['events']
        self.assertEqual(remaining['older_player_feedback_counts'], {'praise': 1})
        self.assertEqual([e['event_id'] for e in remaining['player_feedback']], ['last'])

    def test_old_feedback_with_attention_is_not_ongoing_praise(self):
        context = FeedbackContext()
        context.ingest(frame(15, [feedback(10)], attention=True), 15)
        self.assertNotIn('activity', context.project({}))

    def test_does_not_replay_delivered_window_or_forward_arbitrary_fields(self):
        context = FeedbackContext()
        context.ingest(frame(10, [feedback(10, private='never forward')]), 10)
        snapshot = context.project({})
        self.assertNotIn('private', snapshot['events']['player_feedback'][0])
        context.accepted(snapshot)
        context.ingest(frame(11, [feedback(10)]), 11)
        self.assertEqual(context.project({'events': {'player_feedback': [{'event_id': 'one'}]}}), {})
