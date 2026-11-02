"""Review the assembled provider prompt, using only synthetic game/model data."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import httpx

from bwv.bridge_prompt import ACTION_FEEDBACK_GUIDANCE, SPEECH_GUIDANCE
from bwv.conversation import current_prompt, model_system_prompt, REPLY_FORMAT
from bwv.creature import CreatureContext
from bwv.prompt_cache import system_parts
from bwv.species_prompts import SPECIES_NOTES, UNKNOWN_SPECIES_NOTE
from bwv.llm import LLMConfig, LLMProvider, WIRE_CONTRACT
from bwv.llm_context import CONTEXT_CONTRACT
from bwv.prompt import ACTION_CONTRACT
from bwv.prompt_settings import PromptSettings
from bwv.session_greeting import SESSION_GREETING_GUIDANCE
from bwv.temple_targets import HOME_GUIDANCE
from bwv.tool_docs import tool_documentation
from test_control import playable
from test_tool_interface import bound
from test_world_actions import WorldHarness


class PromptAssemblyTests(unittest.IsolatedAsyncioTestCase):
    def test_species_note_follows_current_form_and_keeps_cached_rules_stable(self):
        from bwv.creature_live import SPECIES
        self.assertEqual(set(SPECIES.values()), set(SPECIES_NOTES))
        stable_rules = None
        for species in (*SPECIES_NOTES, 'unknown form', None):
            with self.subTest(species=species):
                creature = CreatureContext(species, 'engine' if species else 'unknown')
                observation = replace(playable(), creature=creature)
                system, context = current_prompt(observation, 0, question='Hello')
                stable, data = system_parts(model_system_prompt(system))
                if stable_rules is None:
                    stable_rules = stable
                self.assertEqual(stable, stable_rules)
                note = SPECIES_NOTES.get(species, UNKNOWN_SPECIES_NOTE)
                self.assertEqual(data.count(note), 1)
                for other in (set(SPECIES_NOTES.values()) | {UNKNOWN_SPECIES_NOTE}) - {note}:
                    self.assertNotIn(other, system)
                self.assertNotIn('species_note', json.dumps(context))
        stale, _ = current_prompt(replace(playable(),
            creature=CreatureContext('tiger', 'engine')), 10)
        self.assertNotIn('species_note', stale)

    async def test_provider_has_one_output_contract_with_and_without_tool_rounds(self):
        for api in ('responses', 'chat_completions'):
            for rounds in (0, 6):
                with self.subTest(api=api, rounds=rounds):
                    session, _, request = bound(WorldHarness())
                    system, _ = current_prompt(playable(), 0, question='Hello')
                    system += '\n\n## Observations\n\n'+CONTEXT_CONTRACT+'\n\n'+HOME_GUIDANCE
                    request = replace(request, messages=(
                        {'role':'system', 'content':system},
                        {'role':'user', 'content':json.dumps(request.current)}))
                    if rounds:
                        request = session.project_request(request)
                    original = deepcopy(request)
                    provider = LLMProvider(LLMConfig('synthetic', api=api,
                        max_tool_rounds=rounds, max_tool_calls=4),
                        environ={'OPENAI_API_KEY':'synthetic'},
                        transport=httpx.MockTransport(lambda _:self.fail('No HTTP expected')))
                    try:
                        body, messages = provider._body(request)
                        text = messages[0]['content']
                        self.assertEqual(request, original)
                        self.assertNotIn(ACTION_CONTRACT, text)
                        self.assertNotIn(REPLY_FORMAT, text)
                        self.assertNotIn('## Reply format', text)
                        self.assertNotIn('## Action selection', text)
                        self.assertNotIn('conversation_only requires', text)
                        self.assertNotIn('move_to(destination=', text)
                        self.assertNotIn('inspect_surrounding', text)
                        self.assertNotIn('cast_held_miracle', text)
                        self.assertIn('move_to(target="god_hand")', text)
                        self.assertIn('move_to(target="player_temple"', text)
                        for section in (ACTION_FEEDBACK_GUIDANCE, SPEECH_GUIDANCE,
                                        SESSION_GREETING_GUIDANCE, CONTEXT_CONTRACT, HOME_GUIDANCE):
                            self.assertEqual(text.count(section), 1)
                        self.assertEqual(text.count(WIRE_CONTRACT), 1)
                        self.assertIn(f'At most {rounds} tool rounds and 4 individual tool calls', text)
                        if rounds:
                            self.assertIn(tool_documentation(session.definitions()), text)
                        key = 'input' if api=='responses' else 'messages'
                        self.assertEqual(body[key], messages)
                        self.assertEqual(provider.calls, 0)
                    finally:
                        await provider.close()

    async def test_normalization_is_idempotent_and_preserves_literal_general_prompt(self):
        general = '## Custom character\n\nLiteral {x}, $value, "quotes", café 🐾 and C:\\example.'
        system, _ = current_prompt(playable(), 0,
            prompt_settings=PromptSettings(general=general))
        normalized = model_system_prompt(system)
        self.assertIn(general, normalized)
        self.assertEqual(model_system_prompt(normalized), normalized)
        self.assertIn(ACTION_FEEDBACK_GUIDANCE, normalized)
        self.assertIn(ACTION_CONTRACT, system)  # Legacy planner is unchanged.


if __name__=='__main__':
    unittest.main()
