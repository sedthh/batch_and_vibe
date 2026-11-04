"""Fixed provider schemas never relax per-request execution permissions."""
from dataclasses import replace
import json
import unittest

import httpx

from bwv.creature_tools import ToolArgumentError
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm import LLMConfig, LLMProvider, ProviderError
from bwv.prompt_cache import cached_request
from test_tool_interface import bound
from test_world_actions import WorldHarness
from test_positions import PositionHarness
from test_held_actions import HeldHarness
from test_held_miracle import HeldMiracleHarness
from test_creature_tools import call
from test_llm import envelope


class StableToolsTests(unittest.TestCase):
    def test_same_schema_and_reference_for_different_hands_spells_and_map_sizes(self):
        fixtures = [WorldHarness(), HeldHarness('rock'), HeldMiracleHarness(), PositionHarness()]
        larger = PositionHarness()
        larger.probe['position_movement']['map_size'] = {'x':5120, 'z':2560}
        fixtures.append(larger)
        expected = None
        references = []
        capabilities = []
        for fixture in fixtures:
            session, _, request = bound(fixture)
            schemas = session.wire_definitions()
            if expected is None: expected = schemas
            self.assertEqual(schemas, expected)
            request = replace(request, messages=({'role':'system', 'content':'Rules'},
                {'role':'user', 'content':json.dumps(request.current)}))
            projected = session.project_request(request)
            references.append(projected.messages[0]['content'])
            capabilities.append(projected.current['capabilities'])
            wire = cached_request({'input':list(projected.messages)}, LLMConfig('gpt-5.4-mini'))
            if len(references)==1: cache_key=wire['prompt_cache_key']
            self.assertEqual(wire['prompt_cache_key'], cache_key)
        self.assertTrue(all(r == references[0] for r in references))
        self.assertNotEqual(capabilities[0], capabilities[1])
        self.assertNotEqual(capabilities[-2]['coordinate_bounds'], capabilities[-1]['coordinate_bounds'])

    def test_advertised_but_unavailable_tools_actions_and_spells_are_rejected(self):
        session, _, _ = bound(PositionHarness())
        known = {d['name'] for d in session.wire_definitions()}
        self.assertIn('use_held_object', known)
        for name, arguments in (
            ('use_held_object', {'action':'eat', 'target':None}),
            ('do_action', {'action':'cast_miracle', 'miracle':'water', 'target':'self'}),
            ('find_targets', {'action':'cast_miracle', 'miracle':'water'}),
            ('move_to', {'target':{'x':3000,'z':20}, 'pace':None}),
        ):
            with self.subTest(name=name), self.assertRaises(ToolArgumentError):
                session.call(name, arguments)
            self.assertIsNone(session.inner.selected_action)

    def test_supported_calls_still_select_exactly_one_request_bound_action(self):
        h = HeldHarness('rock')
        session, _, request = bound(h)
        self.assertIn('put_down', session.project_request(request).current['capabilities']['held_actions'])
        self.assertEqual(session.call('use_held_object', {'action':'put_down'})['status'], 'selected_pending_final_reply')
        self.assertEqual(session.finalize_reply({})['decision']['candidate'], 'put_down')
        self.assertFalse(h.sent)  # Tool selection is never native dispatch.

    def test_cast_tool_cannot_borrow_an_unoffered_spell_from_fixed_syntax(self):
        h=WorldHarness()
        h.probe['world_actions']['offers']=[e for e in h.probe['world_actions']['offers']
                                           if e['action'] in ('attack','cast_food')]
        h.probe['candidates']=['attack','cast_food']
        session,_,_=bound(h)
        self.assertIn('cast_miracle',session.menu)
        self.assertIn('food',session.spells)
        self.assertNotIn('water',session.spells)
        for name,args in (('do_action',{'action':'cast_miracle','miracle':'water','target':'self'}),
                          ('find_targets',{'action':'cast_miracle','miracle':'water'})):
            with self.assertRaises(ToolArgumentError):session.call(name,args)
        self.assertIsNone(session.inner.selected_action)
        self.assertFalse(h.sent)


class StableToolsWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_spell_feedback_allows_one_valid_cast_without_relaxing_checks(self):
        h=WorldHarness()
        h.probe['world_actions']['offers']=[e for e in h.probe['world_actions']['offers'] if e['action']=='cast_food']
        h.probe['candidates']=['cast_food']
        bound_session,_,request=bound(h)
        session=DirectCreatureToolSession(bound_session.supplier,
            action_candidates=bound_session.inner._action_capabilities)
        target=request.current['snapshot']['action_targets']['cast_food']['objects'][0]['target_id']
        request=replace(request,messages=({'role':'system','content':'Synthetic rules'},
            {'role':'user','content':json.dumps(request.current)}))
        wires=[]
        def handle(req):
            wire=json.loads(req.content);wires.append(wire)
            if len(wires)==1:
                self.assertIsNone(session.inner.selected_action)
                return httpx.Response(200,json=call('responses','do_action',
                    {'action':'cast_miracle','miracle':'water','target':target},'unavailable'))
            if len(wires)==2:
                result=json.loads(next(m['output'] for m in reversed(wire['input'])
                                       if m.get('type')=='function_call_output'))
                self.assertEqual(result['allowed_values'],[None,'food'])
                self.assertEqual(result['parameter'],'miracle')
                self.assertFalse(result['executed'])
                self.assertIsNone(session.inner.selected_action)
                return httpx.Response(200,json=call('responses','do_action',
                    {'action':'cast_miracle','miracle':'food','target':target},'available'))
            return httpx.Response(200,json=envelope())
        provider=LLMProvider(LLMConfig('synthetic'),environ={'OPENAI_API_KEY':'synthetic'},
            transport=httpx.MockTransport(handle))
        provider.tool_factory=lambda:session
        try:
            result=await provider.respond(request)
            self.assertEqual(result['decision']['candidate'],'cast_food')
            self.assertEqual(len(wires),3)
            self.assertEqual(provider.tool_rounds,2)
            self.assertTrue(all(w['tools']==wires[0]['tools'] for w in wires))
            self.assertFalse(h.sent)
        finally:
            await provider.close()

    async def test_provider_excludes_unavailable_tools_and_rejects_a_violating_reply(self):
        for api in ('responses', 'chat_completions'):
            session, _, request = bound(PositionHarness())
            session = DirectCreatureToolSession(session.supplier,
                action_candidates=session.inner._action_capabilities)
            request = replace(request, messages=({'role':'system','content':'Synthetic rules'},
                {'role':'user','content':json.dumps(request.current)}))
            count = 0
            def handle(req):
                nonlocal count
                count += 1
                wire=json.loads(req.content)
                if api=='responses':
                    self.assertEqual(wire['tool_choice']['type'],'allowed_tools')
                    self.assertNotIn('use_held_object',{d['name'] for d in wire['tool_choice']['tools']})
                    self.assertIn('use_held_object',{d['name'] for d in wire['tools']})
                else:
                    self.assertNotIn('use_held_object',{d['function']['name'] for d in wire['tools']})
                if count == 1:
                    return httpx.Response(200, json=call(api,'use_held_object',{'action':'eat','target':None}))
                return httpx.Response(200, json=envelope(api))
            provider = LLMProvider(LLMConfig('synthetic',api=api),
                environ={'OPENAI_API_KEY':'synthetic'}, transport=httpx.MockTransport(handle))
            provider.tool_factory=lambda:session
            try:
                with self.assertRaisesRegex(ProviderError,'invalid_tool_call'):
                    await provider.respond(request)
                self.assertEqual(count,1)
                self.assertIsNone(session.inner.selected_action)
            finally:
                await provider.close()

    async def test_compatible_servers_keep_original_dynamic_schemas(self):
        session,_,_=bound(PositionHarness())
        for api,url in (('responses','http://localhost:1234/v1'),
                        ('chat_completions','https://api.openai.com/v1')):
            config=LLMConfig('synthetic',api=api,base_url=url)
            session.configure_provider(config)
            self.assertEqual(session.wire_definitions(),session.definitions())
            provider=LLMProvider(config,environ={'OPENAI_API_KEY':'synthetic'},
                transport=httpx.MockTransport(lambda _:self.fail('No HTTP expected')))
            try:
                body={}
                provider._tools(body,session,True)
                self.assertEqual(body['tool_choice'],'auto')
            finally:
                await provider.close()
