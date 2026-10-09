import json
import unittest

import httpx

from bwv.errors import ErrorLog, error_banner
from bwv.llm import LLMConfig, LLMProvider, ProviderError
from test_llm import request, envelope


class ErrorLogTests(unittest.TestCase):
    def test_bounded_log_groups_repeats_and_omits_exception_contents(self):
        log=ErrorLog(clock=lambda:10,limit=2)
        try:raise ValueError('synthetic-secret C:/Users/ExampleUser/private.env')
        except ValueError as error:
            log.record('invalid_response','parsing',error=error)
            log.record('invalid_response','parsing',error=error)
        entry=log.snapshot()[0]
        self.assertEqual(entry['occurrences'],2)
        self.assertTrue(entry['stack'])
        encoded=json.dumps(entry)
        for private in ('synthetic-secret','ExampleUser','private.env'):
            self.assertNotIn(private,encoded)
        self.assertEqual(entry['stack'][-1]['file'],'test_errors.py')
        log.record('one','test');log.record('two','test')
        self.assertEqual([e['code'] for e in log.snapshot()],['one','two'])
        changed=log.snapshot();changed[0]['code']='changed'
        self.assertEqual(log.snapshot()[0]['code'],'one')

    def test_banner_uses_fixed_reasons_only(self):
        self.assertEqual(error_banner('invalid_tool_call'),'[ERROR: Creature tool call failed; see the LLM debugger]')
        self.assertNotIn('synthetic-secret',error_banner('synthetic-secret'))

    def test_budget_error_is_distinct_from_provider_rate_limit_and_explains_settings(self):
        banner=error_banner('message_limit_reached')
        self.assertIn('Mod message cap',banner)
        self.assertIn('increase or clear',banner)
        self.assertIn('Settings > Limits',banner)
        self.assertIn('rate limit',error_banner('rate_limited'))


class ProviderDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_survives_recovery_without_headers_or_raw_body(self):
        now=[100];responses=[httpx.Response(429,json={'error':{'message':'synthetic-secret'}},
            headers={'x-private':'synthetic-header'}),httpx.Response(200,json=envelope())]
        provider=LLMProvider(LLMConfig('test-model'),environ={'OPENAI_API_KEY':'synthetic-api-key'},
            transport=httpx.MockTransport(lambda req:responses.pop(0)),clock=lambda:now[0])
        try:
            with self.assertRaisesRegex(ProviderError,'rate_limited'):await provider.respond(request())
            self.assertEqual(provider.status()['errors'][0]['details']['http_status'],429)
            now[0]+=61
            await provider.respond(request())
            self.assertIsNone(provider.last_error)
            self.assertTrue(provider.status()['errors'])
            encoded=json.dumps(provider.status())
            for private in ('synthetic-secret','synthetic-header','synthetic-api-key'):
                self.assertNotIn(private,encoded)
        finally:await provider.close()
