from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from bwv.native_text_live import match_message, read_native_text, NativeMessage
from bwv.feedback_context import FeedbackContext
from bwv.experience import Experience
from bwv.llm_context import summarize_context
from bwv.prompt import preview_prompt
from test_context_probe import Memory
from test_control import playable
from test_feedback_context import frame


def help_message(kind=41, argument=3):
    base = {0:462, 1:523, 4:744, 5:784, 41:624}[kind]
    return ({'visible': True, 'message_ids': [base+argument], 'last_message_game_tick': 101,
             'lines': [{'ring_age': 0, 'text': 'Synthetic creature information.'}]},
            [{'message_kind': kind, 'argument': argument, 'last_game_tick': 100, 'pending': 0}])


class NativeTextTests(unittest.TestCase):
    def test_matches_owner_record_for_thought_and_both_training_signs(self):
        for kind, category in ((41,'instinct'), (4,'praise'), (5,'punishment'), (0,'praise'), (1,'punishment')):
            help_text, records = help_message(kind)
            self.assertEqual(match_message(help_text, records, 105)[1], category)

    def test_rejects_unrelated_hidden_stale_pending_ambiguous_or_formatted_text(self):
        help_text, records = help_message()
        for changes in ({'visible':False}, {'message_ids':[9999]}, {'last_message_game_tick':90},
                        {'last_message_game_tick':106}, {'lines':[]},
                        {'lines':[{'ring_age':1,'text':'Old line'}]},
                        {'lines':[{'ring_age':0,'text':'Template %s'}]}):
            self.assertIsNone(match_message({**help_text, **changes}, records, 105))
        self.assertIsNone(match_message(help_text, records, 202))
        self.assertIsNone(match_message(help_text, [{**records[0], 'pending':1}], 105))
        self.assertIsNone(match_message(help_text, records*2, 105))

    def test_reader_checks_identity_and_dispatch_callback(self):
        reader = Memory(); reader.ensure_running = lambda: None
        reader._session = lambda observer: 'session'; reader._head = lambda: (1000,105,2000,3000,4000)
        reader.types[3000] = b'.?AVCreature@@'; reader.types[5000] = b'.?AVCreatureMental@@'
        reader.words(3000+0x1070,2000); reader.words(3000+0x164,5000)
        table = 0x9D9CAC+41*296; reader.words(table, 0x4CAA80,40)
        help_text, records = help_message()
        with patch('bwv.native_text_live._help', return_value=help_text), patch('bwv.native_text_live._messages', return_value=records):
            first = read_native_text(reader,{})
            self.assertEqual(first.message.kind, 'instinct')
            self.assertEqual(read_native_text(reader,{}).message.event_id, first.message.event_id)
            reader.words(table,123,40)
            with self.assertRaisesRegex(ValueError,'dispatch'):read_native_text(reader,{})
            reader.words(3000+0x1070,9999)
            with self.assertRaisesRegex(ValueError,'owner'):read_native_text(reader,{})

    def test_projection_separates_thought_from_native_plan_and_feedback(self):
        message = NativeMessage('one','praise','Synthetic learned preference.',10)
        experience = Experience(hover_thought_text='Synthetic current instinct.', native_training_feedback=(message,))
        raw = json.loads(preview_prompt(playable(experience=experience),None).context_json)
        compact = summarize_context(raw)
        self.assertEqual(compact['activity'], {'native_thought_text':'Synthetic current instinct.'})
        self.assertEqual(compact['events']['native_training_feedback'][0]['explanation'], message.text)
        self.assertNotIn('observed_at', compact['events']['native_training_feedback'][0])
        self.assertEqual(summarize_context({}), {})

    def test_training_text_survives_ticks_and_acknowledges_only_delivered_entries(self):
        context = FeedbackContext()
        message = asdict(NativeMessage('one','praise','Synthetic learned preference.',10))
        source = frame(10); source['observation']['experience'] = {'native_training_feedback':[message]}
        context.ingest(source,10)
        context.ingest(frame(250),250)
        sent = context.project({})
        self.assertEqual(sent['events']['native_training_feedback'][0]['explanation'], message['text'])
        later = deepcopy(source);later['observation']['experience']['native_training_feedback']=[
            {**message,'event_id':'two','observed_at':250}]
        context.ingest(later,250);context.accepted(sent)
        self.assertEqual(list(context.pending_text),['two'])
        context.ingest(source,10) # Repeated UI record cannot replay an accepted explanation.
        self.assertEqual(list(context.pending_text),['two'])
        context.ingest(frame(251,creature='another'),251)
        self.assertEqual(context.project({}),{})

    def test_unattributed_or_stale_text_is_not_retained_and_queue_is_bounded(self):
        context = FeedbackContext(); source=frame(10)
        good=asdict(NativeMessage('one','punishment','Synthetic learning result.',10))
        for changes in ({'source':'unverified_game_help_text'}, {'observed_at':0}, {'text':''}, {'kind':'instinct'}):
            source['observation']['experience']={'native_training_feedback':[{**good,**changes}]}
            context.ingest(source,10)
        self.assertEqual(context.project({}),{})
        for n in range(70):
            source['observation']['experience']={'native_training_feedback':[{**good,'event_id':str(n)}]}
            context.ingest(source,10)
        sent=context.project({})
        self.assertEqual(len(sent['events']['native_training_feedback']),64)
        self.assertEqual(sent['events']['older_native_training_explanations_omitted'],6)
        context.accepted(sent)
        self.assertEqual(context.project({}),{})
