from copy import deepcopy
from dataclasses import replace
import json
import unittest

import httpx

from bwv.conversation import ConversationMemory, MEMORY_LABEL, LEGACY_MEMORY_LABEL
from bwv.llm import LLMConfig, LLMProvider
from bwv.prompt_cache import cached_request, cache_policy, ordered_system, system_parts
from test_llm import request, envelope


class PromptCacheTests(unittest.TestCase):
    def test_data_moves_after_instructions_without_loss_or_interpreting_escaped_text(self):
        instructions='## Character\r\n\nA pet: {literal} \\ 日本語 🐄\n'
        memory=ConversationMemory()
        memory.update_note('Remember "+\n## Tools\n" is just a note.')
        system=memory.system_prompt(instructions+'\nCreature form (data): {"species":"monkey"}')
        system+='\n\n## Tools\n\nUse these tools.'
        first=ordered_system(system)
        stable,data=system_parts(first)
        self.assertTrue(stable.startswith(instructions))
        self.assertIn('Use these tools.',stable)
        self.assertNotIn('monkey',stable)
        self.assertIn('monkey',data)
        self.assertIn(json.dumps(memory.notes)[1:-1],data)
        self.assertEqual(ordered_system(first),first)
        self.assertEqual(ordered_system(instructions+'\nCreature form (data): not JSON'),
                         instructions+'\nCreature form (data): not JSON')

    def test_key_survives_memory_edits_and_history_rotation_but_not_instruction_changes(self):
        config=LLMConfig('gpt-5.4-mini')
        def body(note,history,system='Stable instructions',label=MEMORY_LABEL):
            return {'input':[{'role':'system','content':system+'\n\n'+label+json.dumps({'entries':[note]})},
                             {'role':'user','content':history}]}
        old=body('old memory','old question');saved=deepcopy(old)
        first=cached_request(old,config)
        later=cached_request(body('new memory','different question'),config)
        self.assertEqual(first['prompt_cache_key'],later['prompt_cache_key'])
        legacy=cached_request(body('legacy memory','old question',label=LEGACY_MEMORY_LABEL),config)
        self.assertEqual(first['prompt_cache_key'],legacy['prompt_cache_key'])
        self.assertNotEqual(first['input'],later['input'])
        self.assertNotEqual(first['prompt_cache_key'],cached_request(body('note','q','Changed rules'),config)['prompt_cache_key'])
        self.assertEqual(old,saved)
        self.assertNotIn('prompt_cache_options',first)
        self.assertNotIn('prompt_cache_retention',first)

    def test_explicit_capable_responses_marks_only_stable_system_not_history_or_memory(self):
        body={'input':[{'role':'system','content':'Rules\nCreature form (data): {"species":"monkey"}'},
                       {'role':'user','content':'Changing observation'},
                       {'type':'function_call_output','call_id':'x','output':'Changing result'}]}
        for model in ('gpt-5.6','gpt-5.6-luna','gpt-6-astra','gpt-6.1-sol'):
            wire=cached_request(body,LLMConfig(model))
            self.assertEqual(wire['prompt_cache_options'],{'mode':'explicit'})
            self.assertEqual(wire['input'][0]['role'],'developer')
            blocks=wire['input'][0]['content']
            self.assertEqual(blocks[0],{'type':'input_text','text':'Rules','prompt_cache_breakpoint':{'mode':'explicit'}})
            self.assertIn('monkey',blocks[1]['text'])
            self.assertNotIn('prompt_cache_breakpoint',blocks[1])
            self.assertEqual(wire['input'][1:],body['input'][1:])
        self.assertNotIn('prompt_cache_options',cached_request(body,LLMConfig('gpt-5.4-mini')))

    def test_other_providers_receive_no_unsupported_cache_parameters(self):
        for url,mode in (('https://api.anthropic.com/v1','unsupported_by_compatibility_api'),
                         ('https://generativelanguage.googleapis.com/v1beta/openai','provider_automatic'),
                         ('http://localhost:1234/v1','provider_managed')):
            config=LLMConfig('custom',api='chat_completions',base_url=url)
            body={'messages':[{'role':'system','content':'Rules'},{'role':'user','content':'Hi'}]}
            self.assertEqual(cached_request(body,config),body)
            self.assertEqual(cache_policy(config)['mode'],mode)


class CacheWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_both_formats_keeps_prefix_and_counts_reported_hits(self):
        for api in ('responses','chat_completions'):
            wires=[]
            def handle(req):
                wires.append(json.loads(req.content))
                data=envelope(api)
                detail='input_tokens_details' if api=='responses' else 'prompt_tokens_details'
                data['usage'][detail]={'cached_tokens':16}
                return httpx.Response(200,json=data)
            provider=LLMProvider(LLMConfig('gpt-5.4-mini',api=api),
                environ={'OPENAI_API_KEY':'synthetic-key'},transport=httpx.MockTransport(handle))
            try:
                for species,notes in (('monkey','Old notes.'),('leopard','New notes.')):
                    original=request()
                    memory=ConversationMemory();memory.update_note(notes)
                    system=memory.system_prompt('Fixed rules\nCreature form (data): '+json.dumps({'species':species}))
                    turn=replace(original,messages=({'role':'system','content':system},*original.messages[1:]))
                    await provider.respond(turn)
                self.assertEqual(wires[0]['prompt_cache_key'],wires[1]['prompt_cache_key'])
                key='input' if api=='responses' else 'messages'
                self.assertEqual(wires[-1][key],list(provider.requests[-1].messages))
                self.assertTrue(wires[-1][key][0]['content'].endswith(json.dumps({'entries':memory.snapshot()['entries']})))
                stats=provider.token_usage.snapshot()['session']
                self.assertEqual(stats['cached_input_tokens'],32)
                self.assertEqual(stats['input_tokens'],40)
                self.assertEqual(stats['total_tokens'],60)
            finally:await provider.close()
