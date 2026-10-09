from dataclasses import replace
import unittest
from unittest.mock import Mock

from bwv.dispatch import ActionOffer, DecisionDispatcher
from bwv.expressions import expression_from_speech, speech_expression, EXPRESSION_CONTRACT, EXPRESSION_GESTURES, display_speech
from bwv.conversation import CreatureSpeech, reply_contract
from bwv.prompt import build_system
from bwv.prompt_settings import DEFAULT_PROMPTS


class ExpressionTests(unittest.TestCase):
    def test_protocol_fragments_never_become_visible_chat(self):
        from bwv.expressions import malformed_speech
        for value in ('{',' { 💡 ','{}','[', '{"answer":"hello"}', '```json\n{'):
            self.assertTrue(malformed_speech(value))
            self.assertEqual(display_speech(value),'')
        for value in ('Hello!','[ERROR: test]','I like {that}.','...','👋',''):
            self.assertFalse(malformed_speech(value))

    def test_every_wired_cue_has_a_named_description_and_first_person_rule(self):
        for emoji,action in EXPRESSION_GESTURES.items():
            self.assertIn(emoji+' '+action+': ',EXPRESSION_CONTRACT)
        self.assertIn('NEVER refer to yourself in the\nthird person',DEFAULT_PROMPTS.general)
    def test_display_hides_supported_and_unsupported_emoji_sequences(self):
        for emoji in ('😕', '😕\ufe0f', '🙂', '❤️', '👨‍👩‍👧‍👦', '👍🏽', '🇭🇺', '1️⃣'):
            with self.subTest(emoji=emoji):
                self.assertEqual(display_speech('Hello '+emoji+' friend'), 'Hello friend')
                self.assertEqual(display_speech(emoji), '')
        self.assertEqual(display_speech('Árvíz — 10 trees. 日本語'), 'Árvíz — 10 trees. 日本語')

    def test_display_repairs_apostrophes_and_filters_controls_without_losing_languages(self):
        raw="It\x19;s fine. I\u2019m here. \u2018Hello\u2019, Árvíz 日本語.\x00\x1b\x7f"
        expected="It's fine. I'm here. 'Hello', Árvíz 日本語."
        self.assertEqual(display_speech(raw),expected)
        self.assertEqual(display_speech(expected),expected)
        self.assertEqual(display_speech('\x00\x1b😕'),'')
        self.assertEqual(display_speech('First\nSecond\tline'),'First\nSecond\tline')
        self.assertIn('\x19;',raw)

    def test_emoji_only_speech_has_diagnostics_without_banner_or_retained_reply(self):
        speech = CreatureSpeech()
        speech.publish('😕', 0, {'candidate': None}, source='god_question')
        speech.retain_answer('😕')
        frame = speech.snapshot(0)
        self.assertIsNone(frame['current'])
        self.assertIsNone(frame['last_answer'])
        self.assertEqual(frame['last_expression']['candidate'], 'look_confused')
        speech.publish('Still here.', 1, {'candidate': None}, source='god_question')
        speech.retain_answer('Still here.')
        speech.publish('😕', 2, {'candidate': None}, source='god_question')
        speech.retain_answer('😕')
        self.assertEqual(speech.snapshot(2)['current']['text'], 'Still here.')
        self.assertEqual(speech.snapshot(2)['current']['created_at'], 1)
        self.assertEqual(speech.snapshot(2)['last_answer']['text'], 'Still here.')

    def dispatcher(self):
        adapter = Mock()
        adapter.capture.return_value = ActionOffer(('synthetic',), (('look_confused', b'fixture'),), 11)
        adapter.still_valid.return_value = True
        adapter.submit.return_value = True
        dispatcher = DecisionDispatcher(adapter, clock=lambda: 10)
        dispatcher.offer()
        return dispatcher, adapter

    def test_first_supported_emoji_anywhere_in_text(self):
        expected = {'emoji': '😕', 'candidate': 'look_confused'}
        for text in ('I am confused 😕', 'I am confused 😕\ufe0f \n',
                     '😕 I am confused', 'I am 😕 confused.', '"😕"',
                     '🙂 😕 👋', '😕😕', '😕 followed by unsupported 🙂'):
            with self.subTest(text=text):
                self.assertEqual(expression_from_speech(text), expected)
        self.assertEqual(expression_from_speech('👋🏽 hello 😕'),
                         {'emoji':'👋','candidate':'wave'})
        for emoji, candidate in EXPRESSION_GESTURES.items():
            self.assertEqual(expression_from_speech('Before '+emoji+' after.'),
                             {'emoji':emoji,'candidate':candidate})
        for text in ('I am happy 🙂', '', None):
            self.assertIsNone(expression_from_speech(text))

    def test_fixed_prompt_contract_survives_editable_prose(self):
        settings = replace(DEFAULT_PROMPTS, general='Custom behavior prose.')
        self.assertIn(EXPRESSION_CONTRACT, reply_contract(settings))
        self.assertIn('use no emoji cues in answer or say',
                      reply_contract(settings))
        self.assertIn('Custom behavior prose.', build_system(settings))
        self.assertNotIn('capabilities.gestures', reply_contract(settings))
        self.assertIn('silently skips cues the engine cannot', reply_contract(settings))

    def test_unavailable_cue_preserves_speech_without_sending_an_action(self):
        dispatcher, adapter = self.dispatcher()  # Only confused is available.
        speech = CreatureSpeech()
        speech.publish('Hello 👋', 10, {'candidate': None}, source='god_question', dispatcher=dispatcher)
        snapshot = speech.snapshot(10)
        self.assertEqual(snapshot['current']['text'], 'Hello')
        self.assertEqual(snapshot['last_expression']['status'], 'not_confirmed_no_retry')
        adapter.submit.assert_not_called()

    def test_cue_uses_existing_dispatcher_without_refreshing_offer(self):
        dispatcher, adapter = self.dispatcher()
        result = speech_expression('🙂 I am 😕 confused 👋 😕.', {'candidate': None}, dispatcher=dispatcher)
        self.assertEqual(result['status'], 'queued')
        adapter.capture.assert_called_once()
        adapter.submit.assert_called_once_with(b'fixture')
        # A repeated call cannot replay the gesture.
        self.assertEqual(speech_expression('😕', {'candidate': None}, dispatcher=dispatcher)['status'],
                         'not_confirmed_no_retry')
        adapter.submit.assert_called_once()

    def test_block_or_action_conflict_skips_gesture(self):
        dispatcher, adapter = self.dispatcher()
        self.assertEqual(speech_expression('😕', {'candidate': 'walk'}, dispatcher=dispatcher)['status'],
                         'skipped_action_conflict')
        adapter.submit.assert_not_called()
        adapter.still_valid.return_value = False
        self.assertEqual(speech_expression('😕', {'candidate': None}, dispatcher=dispatcher)['status'],
                         'conditions_changed')
        adapter.submit.assert_not_called()

    def test_no_executor_and_uncertain_receipt_do_not_retry(self):
        self.assertEqual(speech_expression('😕', {'candidate': None})['status'],
                         'skipped_executor_unavailable')
        dispatcher, adapter = self.dispatcher()
        adapter.submit.side_effect = TimeoutError()
        self.assertEqual(speech_expression('😕', {'candidate': None}, dispatcher=dispatcher)['status'],
                         'not_confirmed_no_retry')
        adapter.submit.assert_called_once()
