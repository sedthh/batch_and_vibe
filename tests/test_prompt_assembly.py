"""Review the assembled provider prompt, using only synthetic game/model data."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import httpx

from bwv.bridge_prompt import ACTION_FEEDBACK_GUIDANCE, SPEECH_GUIDANCE
from bwv.conversation import current_prompt, model_system_prompt, REPLY_FORMAT
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
