"""Synthetic HTTP fixtures only; never contacts a model provider or game."""
import asyncio
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading

import httpx

from bwv.conversation import ConversationRequest
from bwv.control import ControlObservation
from bwv.creature import CreatureContext
from bwv.llm import LLMConfig, LLMProvider, ProviderError, WIRE_CONTRACT
from bwv.profile_names import ProfileNames
from bwv.qa_live import LiveChatRuntime
from bwv.prompt import build_system
from bwv.conversation import reply_contract


def request(question=True):
    current = {'snapshot': {'trees_nearby': 3}}
    if question:
        current['god_question'] = 'What do you notice?'
    return ConversationRequest('god_question' if question else 'observation',
        ({'role': 'system', 'content': 'Synthetic creature prompt'},
         {'role': 'user', 'content': json.dumps(current)}), current)


def reply(**changes):
    return {'answer': 'I notice trees.', 'decision': {'candidate': None},
            'memory': None, 'say': None, **changes}


def envelope(api='responses', result=None):
    text = json.dumps(reply() if result is None else result)
    if api == 'responses':
        return {'status': 'completed', 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}],
            'usage': {'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30}}
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}


class ConfigTests(unittest.TestCase):
    def test_config_bounds_and_private_error_messages(self):
        for changes in ({'base_url': 'http://remote.invalid/v1'},
                        {'base_url': 'https://user:secret@remote.invalid/v1'},
                        {'base_url': 'https://remote.invalid/v1?key=secret'},
                        {'base_url': 'https://remote.invalid/v1#secret'},
                        {'base_url': 'https://remote.invalid:invalid/v1'},
                        {'api_key_env': None}, {'max_messages': True},
                        {'timeout_seconds': float('nan')}, {'event_cooldown_seconds': 60},
                        {'api': 'unknown'}, {'output_format': 'plain_text'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as error:
                LLMConfig('test-model', **changes)
            self.assertNotIn('secret', str(error.exception))
        for host in ('localhost', '127.0.0.1', '[::1]'):
            LLMConfig('local-model', api_key_env=None, base_url=f'http://{host}:1234/v1')
        with TemporaryDirectory() as directory:
            path = Path(directory)/'settings.local.json'
            path.write_text('{"model":"test-model","api_key":"synthetic-secret"}')
            with self.assertRaises(ValueError) as error:
                LLMConfig.load(path)
            self.assertNotIn('synthetic-secret', str(error.exception))
            self.assertNotIn(directory, str(error.exception))
        self.assertNotIn('test-model', repr(LLMConfig('test-model')))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_context_error_has_short_backoff_without_repeating_http(self):
        from bwv.creature_tools import ToolContextUnavailable
        from unittest.mock import Mock
        provider=self.provider(lambda req:httpx.Response(200,json=envelope()))
        session=Mock()
        session.bind_request.side_effect=ToolContextUnavailable('Synthetic stale observation')
        provider.tool_factory=lambda:session
        with self.assertRaisesRegex(ProviderError,'tool_context_unavailable'):
            await provider.respond(request())
        self.assertEqual(provider.calls,0)
        self.assertEqual(provider.status()['retry_after_seconds'],2)
        self.now+=2
        self.assertTrue(provider.ready())
        provider.tool_factory=None
        await provider.respond(request())
        self.assertEqual(provider.calls,1)

    async def test_display_block_prevents_http_and_tool_round_posts_without_spending_budget(self):
        for api in ('responses','chat_completions'):
            with self.subTest(api=api):
                captured=[];blocked=['fullscreen_overlay_unavailable']
                def handle(req):
                    captured.append(req)
                    return httpx.Response(200,json=envelope(api))
                provider=self.provider(handle,api=api)
                provider.request_blocker=lambda:blocked[0]
                with self.assertRaisesRegex(ProviderError,'fullscreen_overlay_unavailable'):
                    await provider._post({'model':'fixture'})
                self.assertEqual(provider.calls,0)
                self.assertEqual(captured,[])
                blocked[0]=None
                await provider._post({'model':'fixture'})
                self.assertEqual(provider.calls,1)
                blocked[0]='fullscreen_overlay_unavailable'
                # Every provider/tool round shares the same final HTTP boundary.
                with self.assertRaises(ProviderError):await provider._post({'model':'fixture'})
                self.assertEqual(provider.calls,1)
                self.assertEqual(len(captured),1)

    async def asyncSetUp(self):
        self.providers = []
        self.now = 100.

    async def asyncTearDown(self):
        for provider in self.providers:
            await provider.close()

    def provider(self, handler, **kwargs):
        config = LLMConfig('test-model', **kwargs)
        provider = LLMProvider(config, environ={'OPENAI_API_KEY': 'synthetic-secret'},
            transport=httpx.MockTransport(handler), clock=lambda:self.now)
        self.providers.append(provider)
        return provider

    async def test_broken_speech_gets_one_bounded_format_repair_both_protocols(self):
        for api in ('responses','chat_completions'):
            wires=[]
            def handler(req):
                wires.append(json.loads(req.content))
                return httpx.Response(200,json=envelope(api,reply(answer='{' if len(wires)==1 else 'Thank you!',say=None)))
            p=self.provider(handler,api=api)
            self.assertEqual((await p.respond(request()))['answer'],'Thank you!')
            self.assertEqual(p.calls,2)
            self.assertEqual(p.usage['input_tokens'],40)
            messages=wires[-1]['input' if api=='responses' else 'messages']
            self.assertIn('do not repeat tool calls',messages[-1]['content'])
            self.assertTrue(any(e['code']=='malformed_speech' for e in p.errors.snapshot()))

    async def test_repeated_broken_speech_and_request_budget_do_not_loop(self):
        for budget,expected in ((5,2),(1,2),(None,2)):
            p=self.provider(lambda req:httpx.Response(200,json=envelope(result=reply(answer='{'))),max_messages=budget)
            with self.assertRaisesRegex(ProviderError,'malformed_speech'):await p.respond(request())
            self.assertEqual(p.calls,expected)
            self.assertEqual(p.messages,1)

    async def test_silent_and_emoji_only_answers_do_not_need_repair(self):
        for answer in ('','👋','...','I like {that}.','[I nod.]'):
            p=self.provider(lambda req:httpx.Response(200,json=envelope(result=reply(answer=answer))))
            self.assertEqual((await p.respond(request()))['answer'],answer)
            self.assertEqual(p.calls,1)

    async def test_special_prompt_characters_survive_settings_and_both_http_formats(self):
        text=('## Character\n\n"Quotes" \'apostrophes\' ’ \\ {species} ${literal} %s\n'
              'Árvíz 日本語 🐄 👩‍🌾 e\u0301\n\n## Style\n\n**Markdown** <tags> & `code`\n'
              'Literal escape: \\n; real newline:\n\tindented\r\nWindows newline\n')
        for api in ('responses','chat_completions'):
            with self.subTest(api=api), TemporaryDirectory() as directory:
                path=Path(directory)/'settings.json'
                LLMConfig('fixture',api=api,prompts={'general':text}).save(path)
                config=LLMConfig.load(path)
                self.assertEqual(config.prompts.general,text)
                system=build_system(config.prompts)+'\n\n'+reply_contract(config.prompts)
                original=request()
                req=replace(original,messages=({'role':'system','content':system},original.messages[1]))
                captured=[]
                def handle(http_request):
                    captured.append(json.loads(http_request.content))
                    return httpx.Response(200,json=envelope(api))
                provider=self.provider(handle,api=api,prompts=config.prompts)
                await provider.respond(req)
                messages=captured[0]['input' if api=='responses' else 'messages']
                self.assertTrue(messages[0]['content'].startswith(text+'\n\n## Bridge rules'))
                self.assertIn('## Available tools\n\n',messages[0]['content'])
                self.assertEqual(req.messages[0]['content'],system)

    async def test_responses_wire_format_normalization_usage_and_no_credentials_in_debug(self):
        captured = []
        def handle(req):
            captured.append(req)
            return httpx.Response(200, json=envelope())
        provider = self.provider(handle, reasoning_effort='low')
        result = await provider.respond(request())
        self.assertEqual(result, {'answer': 'I notice trees.', 'decision': {'candidate': None}})
        wire = json.loads(captured[0].content)
        self.assertEqual(captured[0].url.path, '/v1/responses')
        self.assertEqual(captured[0].headers['authorization'], 'Bearer synthetic-secret')
        self.assertFalse(wire['store'])
        self.assertEqual(wire['reasoning'], {'effort': 'low'})
        self.assertTrue(wire['text']['format']['strict'])
        self.assertEqual(wire['max_output_tokens'], 2560)
        self.assertEqual(wire['input'], list(provider.requests[-1].messages))
        self.assertIn(WIRE_CONTRACT, wire['input'][0]['content'])
        self.assertNotIn(WIRE_CONTRACT, request().messages[0]['content'])
        self.assertEqual(provider.status()['last_usage']['total_tokens'], 30)
        self.assertEqual(provider.status()['token_usage']['session']['input_tokens'],20)
        await provider.respond(request())
        self.assertEqual(provider.status()['token_usage']['session']['input_tokens'],40)
        self.assertEqual(provider.status()['token_usage']['session']['output_tokens'],20)
        self.assertEqual(provider.status()['last_usage']['input_tokens'],20)
        self.assertNotIn('synthetic-secret', json.dumps(provider.status())+repr(provider.requests))

    async def test_chat_compatible_local_no_key_legacy_tokens_and_json_mode(self):
        for output_format in ('json_schema', 'json_object'):
            captured = []
            def handle(req):
                captured.append(req)
                return httpx.Response(200, json=envelope('chat', reply(memory='Be gentle.', say='Hello.')))
            provider = self.provider(handle, api='chat_completions',
                base_url='http://127.0.0.1:1234/v1', api_key_env=None,
                output_format=output_format, token_parameter='max_tokens')
            result = await provider.respond(request())
            self.assertEqual(result['memory'], 'Be gentle.')
            self.assertEqual(result['say'], 'Hello.')
            self.assertEqual(provider.status()['token_usage']['session']['input_tokens'],20)
            self.assertEqual(provider.status()['token_usage']['session']['output_tokens'],10)
            wire = json.loads(captured[0].content)
            self.assertEqual(captured[0].url.path, '/v1/chat/completions')
            self.assertNotIn('authorization', captured[0].headers)
            self.assertNotIn('store', wire)
            self.assertIn('max_tokens', wire)
            self.assertEqual(wire['response_format']['type'], output_format)

    async def test_rejected_reply_still_records_reported_usage(self):
        data=envelope();data['status']='incomplete'
        provider=self.provider(lambda req:httpx.Response(200,json=data))
        with self.assertRaises(ProviderError):await provider.respond(request())
        self.assertEqual(provider.status()['token_usage']['total']['input_tokens'],20)
        self.assertEqual(provider.status()['token_usage']['total']['output_tokens'],10)

    async def test_status_failures_never_echo_server_content_retry_or_follow_redirects(self):
        for code, error, disabled in ((401, 'authentication_failed', True),
                (400, 'model_or_configuration_rejected', True), (429, 'rate_limited', False),
                (503, 'provider_unavailable', False), (302, 'redirect_rejected', True)):
            calls = []
            def handle(req):
                calls.append(req)
                return httpx.Response(code, text='synthetic-secret private details',
                                      headers={'Location': 'https://elsewhere.invalid/'})
            provider = self.provider(handle)
            for _ in range(2):
                with self.assertRaises(ProviderError) as caught:
                    await provider.respond(request())
                self.assertEqual(str(caught.exception), error)
            self.assertEqual(len(calls), 1)
            self.assertEqual(provider.disabled, disabled)
            self.assertNotIn('private details', repr(provider.status()))
            self.assertEqual(provider.status()['retry_after_seconds'],30)
            self.now += 29.9
            self.assertFalse(provider.ready())
            self.now += .1
            self.assertEqual(provider.ready(), not disabled)

    async def test_malformed_incomplete_refusal_actions_and_oversized_replies_rejected(self):
        bad = [envelope(result=reply(decision={'candidate': 'guessed-action'})),
               envelope(result=reply(answer=123)), envelope(result=reply(memory='x'*2001)),
               envelope(result=reply(say='x'*301)), envelope(result=reply(extra='bad')),
               {'status': 'incomplete', 'output': []},
               {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'refusal', 'refusal': 'private details'}]}]},
               {'status': 'completed', 'output': [{'type': 'function_call'}]}, [], None]
        for data in bad:
            provider = self.provider(lambda req:httpx.Response(200, json=data))
            with self.assertRaises(ProviderError):
                await provider.respond(request())
            self.assertEqual(provider.active, 0)
        provider = self.provider(lambda req:httpx.Response(200, content=b'x'*(1024*1024+1)))
        with self.assertRaisesRegex(ProviderError, '^response_too_large$'):
            await provider.respond(request())

    async def test_chat_truncated_and_tool_reply_rejected(self):
        for changed in ({'finish_reason':'length'}, {'finish_reason':'stop',
                       'message': {'content': json.dumps(reply()), 'tool_calls':[{}]}}):
            data = envelope('chat'); data['choices'][0].update(changed)
            provider = self.provider(lambda req:httpx.Response(200, json=data), api='chat_completions')
            with self.assertRaises(ProviderError):
                await provider.respond(request())

    async def test_empty_and_whitespace_reply_text_is_valid_silence(self):
        for api in ('responses', 'chat_completions'):
            for text in ('', ' \t\n'):
                data = envelope('chat' if api == 'chat_completions' else 'responses',
                                reply(answer=text, say=text))
                provider = self.provider(lambda req: httpx.Response(200, json=data), api=api)
                result = await provider.respond(request())
                self.assertEqual(result['answer'], text)
                self.assertEqual(result['say'], text)

    async def test_budget_cancel_and_single_flight(self):
        started = asyncio.Event()
        async def slow(req):
            started.set()
            await asyncio.Event().wait()
        provider = self.provider(slow, max_messages=1)
        pending = asyncio.create_task(provider.respond(request()))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(provider.active, 0)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.messages, 1)
        self.assertFalse(provider.ready())
        with self.assertRaisesRegex(ProviderError, '^message_limit_reached$'):
            await provider.respond(request())

    async def test_questions_and_ticks_share_optional_message_cap(self):
        provider=self.provider(lambda req:httpx.Response(200,json=envelope()),max_messages=2)
        await provider.respond(request(question=True))
        await provider.respond(request(question=False))
        self.assertEqual(provider.status()['messages'],2)
        self.assertEqual(provider.status()['messages_remaining'],0)
        with self.assertRaisesRegex(ProviderError,'message_limit_reached'):
            await provider.respond(request())
        self.assertEqual(provider.calls,2)
        provider.token_usage.reset()
        self.assertEqual(provider.status()['messages_remaining'],0)
        unlimited=self.provider(lambda req:httpx.Response(200,json=envelope()))
        unlimited.messages=10000
        await unlimited.respond(request())
        self.assertTrue(unlimited.ready())
        self.assertIsNone(unlimited.status()['message_limit'])
        self.assertIsNone(unlimited.status()['messages_remaining'])

    async def test_preflight_block_does_not_consume_message_but_http_failure_does(self):
        provider=self.provider(lambda req:httpx.Response(503),max_messages=1)
        provider.request_blocker=lambda:'fullscreen_overlay_unavailable'
        with self.assertRaisesRegex(ProviderError,'fullscreen_overlay_unavailable'):
            await provider.respond(request())
        self.assertEqual(provider.messages,0)
        self.now+=61;provider.request_blocker=None
        with self.assertRaisesRegex(ProviderError,'provider_unavailable'):
            await provider.respond(request())
        self.assertEqual(provider.messages,1)
        self.assertEqual(provider.calls,1)

    async def test_network_timeout_and_missing_key_sanitized(self):
        def broken(req):
            raise httpx.ConnectError('synthetic-secret endpoint', request=req)
        provider = self.provider(broken)
        with self.assertRaisesRegex(ProviderError, '^connection_failed$'):
            await provider.respond(request())
        self.assertEqual(provider.status()['retry_after_seconds'],30)
        async def slow(req):
            await asyncio.sleep(10)
        provider = self.provider(slow, timeout_seconds=1)
        with self.assertRaisesRegex(ProviderError, '^request_timeout$'):
            await provider.respond(request())
        self.assertEqual(provider.status()['retry_after_seconds'],30)
        with self.assertRaises(ValueError):
            LLMProvider(LLMConfig('test-model'), environ={})

    async def test_real_async_http_transport_on_loopback(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                captured.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                raw = json.dumps(envelope('chat')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        provider = LLMProvider(LLMConfig('local-test', api='chat_completions', api_key_env=None,
            base_url=f'http://127.0.0.1:{server.server_port}/v1'))
        self.providers.append(provider)
        try:
            result = await provider.respond(request())
            self.assertEqual(result['answer'], 'I notice trees.')
            self.assertEqual(captured[0][0], '/v1/chat/completions')
            self.assertEqual(len(captured), 1)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close(); thread.join(2)


class LiveLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_cooldown_queues_question_and_sends_fresh_context_once(self):
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}}))
        self.provider.last_error='rate_limited';self.provider.blocked_until=self.now+30
        task=asyncio.create_task(self.runtime.question({'question':'What is here?'}))
        try:
            await asyncio.sleep(.02);self.runtime.publish()
            self.assertFalse(task.done());self.assertEqual(self.provider.calls,0)
            self.assertTrue(self.runtime.payload()['current']['answer_status']['waiting_for_provider'])
            self.assertFalse(self.runtime.reset_available())
            self.now+=30
            self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':9}}))
            await asyncio.wait_for(task,1)
            self.assertEqual(self.provider.calls,1)
            sent=json.loads(self.wires[0]['input'][-1]['content'])
            self.assertEqual(sent['snapshot']['surroundings']['trees'],9)
            self.assertEqual(sent['god_question'],'What is here?')
            self.assertNotIn('waiting_for_provider',self.runtime.payload()['current']['answer_status'])
        finally:
            if not task.done():task.cancel()

    async def test_close_cancels_cooldown_queue_without_model_request(self):
        self.runtime.ingest(self.frame())
        self.provider.blocked_until=self.now+60
        task=asyncio.create_task(self.runtime.question({'question':'Wait here'}))
        await asyncio.sleep(.02)
        await self.runtime.close()
        await asyncio.gather(task,return_exceptions=True)
        self.assertTrue(task.done());self.assertEqual(self.provider.calls,0)

    async def test_statistics_reset_preserves_history_memory_and_request_budget(self):
        self.write_note=True;self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'Remember to help villagers'})
        history=list(self.runtime.memory.history);dialogue=list(self.runtime.dialogue)
        calls=self.provider.calls;notes=self.runtime.memory.notes
        self.assertGreater(calls,0)
        result=await self.runtime.reset({'scope':'statistics'})
        self.assertEqual(result,{'reset':'statistics'})
        self.assertEqual(self.provider.calls,calls)
        self.assertEqual(self.provider.status()['last_usage'],{})
        self.assertEqual(self.provider.token_usage.snapshot()['total']['requests'],0)
        self.assertEqual(list(self.runtime.memory.history),history)
        self.assertEqual(list(self.runtime.dialogue),dialogue)
        self.assertEqual(self.runtime.memory.notes,notes)
        self.provider.active=1
        with self.assertRaises(ValueError):await self.runtime.reset({'scope':'statistics'})
        self.provider.active=0

    async def test_real_chat_wakes_on_question_without_model_action_tools(self):
        from test_sleep import sleep_frame
        from bwv.dispatch import ActionOffer, DecisionDispatcher
        await self.runtime.close()
        sent=[];release=asyncio.Event();entered=asyncio.Event()
        class WakeAdapter:
            def capture(adapter,now):return ActionOffer(('test',),(('wake_up',b'wake'),),now+1)
            def still_valid(adapter,offer,candidate):return True
            def submit(adapter,packet):sent.append(packet);return True
        async def handle(req):
            entered.set();await release.wait()
            return httpx.Response(200,json=envelope())
        self.provider=LLMProvider(LLMConfig('test-model',wake_on_question=True),
            environ={'OPENAI_API_KEY':'synthetic-key'},transport=httpx.MockTransport(handle))
        self.runtime=LiveChatRuntime(self.directory.name,provider=self.provider,clock=lambda:self.now,
            names_reader=ProfileNames,
            wake_factory=lambda obs:DecisionDispatcher(WakeAdapter(),clock=lambda:self.now))
        self.runtime.gate.resume_delay=0
        self.runtime.ingest(sleep_frame(self.frame()))
        await self.runtime.configure({'automatic':True})
        await self.runtime.step()
        self.assertEqual(sent,[])
        pending=asyncio.create_task(self.runtime.question({'question':'Hello'}))
        try:
            await asyncio.wait_for(entered.wait(),1)
            self.assertEqual(sent,[b'wake'])
            self.assertFalse(pending.done())
            self.assertEqual(self.provider.requests[-1].current['wake_request']['status'],'queued')
            self.assertNotIn('do_action',{d['name'] for d in self.runtime._tool_session().definitions()})
        finally:release.set();await pending
        self.assertEqual(sent,[b'wake'])

    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.now = 100.
        self.sequence = 0
        self.wires = []
        self.delay = 0
        self.request_started=asyncio.Event()
        self.request_hold=None
        self.write_note=False
        async def handle(req):
            self.wires.append(json.loads(req.content))
            self.request_started.set()
            if self.request_hold is not None:await self.request_hold.wait()
            await asyncio.sleep(self.delay)
            if self.write_note and len(self.wires)==1:
                from test_creature_tools import call
                data=call('responses','update_memory',{'memory':'Help villagers.','memory_id':None})
                data['usage']=envelope()['usage']
                return httpx.Response(200,json=data)
            return httpx.Response(200, json=envelope())
        self.provider = LLMProvider(LLMConfig('test-model'),
            environ={'OPENAI_API_KEY':'synthetic-secret'}, transport=httpx.MockTransport(handle),
            clock=lambda:self.now)
        self.runtime = LiveChatRuntime(self.directory.name, provider=self.provider,
            clock=lambda:self.now, names_reader=ProfileNames)
        self.runtime.gate.resume_delay = 0

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    def frame(self, context=None, **changes):
        self.sequence += 1
        obs = ControlObservation('synthetic-session', self.sequence, self.now, connected=True,
            creature_id='test-creature', paused=False, in_temple=False,
            creature=CreatureContext('monkey', 'engine'))
        raw = asdict(obs); raw.update(changes)
        return {'observation':raw, 'diagnostics':{'script_camera_owned':False},
            'gate':{'allowed':False,'reasons':['unsupported','unknown_control']},
            'prompt':{'system':'Test prompt', 'context':{'surroundings':{'trees_nearby':3}} if context is None else context}}

    async def test_off_by_default_direct_chat_persistent_memory_and_debug(self):
        self.write_note=True
        self.runtime.ingest(self.frame())
        await self.runtime.step(); await asyncio.sleep(.01)
        self.assertEqual(self.provider.calls, 0)
        result = await self.runtime.question({'question':'What do you notice?'})
        self.assertEqual(result['mode'], 'live_llm_chat')
        self.assertEqual(result['game_actions'], 0)
        self.assertEqual(self.runtime.memory.notes, 'Help villagers.')
        data = self.runtime.payload()
        self.assertFalse(data['source']['gate']['allowed'])
        self.assertFalse(data['runtime']['engine_execution_enabled'])
        self.assertEqual(data['runtime']['last_request'], self.wires[0]['input'])
        self.assertNotIn('synthetic-secret', json.dumps(data))
        self.assertNotIn(self.directory.name, json.dumps(data))
        effective=data['runtime']['settings']
        self.assertEqual(set(effective),set(asdict(self.provider.config))-{'api_key_env','voice'})
        self.assertNotIn('OPENAI_API_KEY',json.dumps(effective))
        self.assertEqual(effective['model'],'test-model')
        self.assertEqual(effective['max_tool_calls'],self.provider.config.max_tool_calls)
        self.assertEqual(data['runtime']['provider']['token_usage']['session']['input_tokens'],40)
        self.assertTrue((Path(self.directory.name)/'creature-memory.json').exists())
        self.assertFalse((Path(self.directory.name)/'dummy-memory.json').exists())

    async def test_empty_scene_lock_absence_and_latest_snapshot_after_resume(self):
        await self.runtime.configure({'automatic':True})
        for context, changes in (({}, {}), ({'x':1},{'paused':True}),
                                ({'x':1},{'in_temple':True}), ({'x':1},{'cutscene':True}),
                                ({'x':1},{'creature_present':False})):
            self.runtime.ingest(self.frame(context, **changes))
            await self.runtime.step(); await asyncio.sleep(.01)
        self.assertEqual(self.provider.calls, 0)
        with self.assertRaises(ValueError):
            await self.runtime.question({'question':'Hello?'})
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':7}, 'candidates':['invented']}))
        await self.runtime.step(); await asyncio.sleep(.04)
        current = json.loads(self.wires[-1]['input'][-1]['content'])
        self.assertEqual(current['snapshot'], {'surroundings':{'trees':7}})
        self.assertIn('memory_reconciliation', current)

    async def test_disable_cancels_inflight_and_does_not_write_late_memory(self):
        self.request_hold=asyncio.Event()
        await self.runtime.configure({'automatic':True})
        self.runtime.ingest(self.frame()); await self.runtime.step()
        await asyncio.wait_for(self.request_started.wait(),1)
        self.assertEqual(self.provider.active, 1)
        await self.runtime.configure({'automatic':False})
        await asyncio.sleep(.01)
        self.request_hold.set()
        await asyncio.sleep(.01)
        self.assertEqual(self.runtime.memory.notes, '')
        self.assertEqual(self.provider.active, 0)
        self.assertEqual(self.provider.calls, 1)
        with self.assertRaises(ValueError):
            await self.runtime.configure({'delay_seconds':0})

    async def test_provider_cooldown_does_not_consume_unsent_observation(self):
        await self.runtime.configure({'automatic':True})
        self.provider.blocked_until=110
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':3}}))
        await self.runtime.step()
        self.assertEqual(self.runtime.scheduled['state'],'waiting_for_provider')
        self.now=111
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':7}}))
        await self.runtime.step();await asyncio.sleep(.03)
        self.assertEqual(self.provider.calls,1)
        current=json.loads(self.wires[-1]['input'][-1]['content'])
        self.assertEqual(current['snapshot'],{'surroundings':{'trees':7}})
