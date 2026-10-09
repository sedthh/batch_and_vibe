"""Synthetic provider recovery; never sends game commands or paid requests."""
import json
import unittest
from dataclasses import replace
import httpx
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm import LLMConfig, LLMProvider, ProviderError
import test_direct_tools
from test_creature_tools import call
from test_llm import envelope, reply


class CastingPromiseTests(unittest.IsolatedAsyncioTestCase):
    async def test_movement_promise_requires_hand_action_on_both_protocols(self):
        from test_action_tools import action_request
        from test_creature_tools import frame
        for api in ('responses','chat_completions'):
            request=action_request(('approach_hand',))
            request.current['god_question']='hey buddy come here'
            request=replace(request,messages=({'role':'system','content':'Synthetic creature'},
                {'role':'user','content':json.dumps(request.current)}))
            session=DirectCreatureToolSession(frame,action_candidates=('approach_hand',))
            wires=[]
            def handler(req):
                wires.append(json.loads(req.content))
                if len(wires)==2:
                    data=call(api,'move_to',{'destination':{'special':'god_hand'},'pace':None},'move')
                else:data=envelope(api,reply(answer="I'm coming, buddy."))
                return httpx.Response(200,json=data)
            provider=LLMProvider(LLMConfig('synthetic',api=api),environ={'OPENAI_API_KEY':'synthetic'},transport=httpx.MockTransport(handler))
            provider.tool_factory=lambda:session
            try:
                result=await provider.respond(request)
                self.assertEqual(result['decision'],{'candidate':'approach_hand'})
                self.assertEqual(len(wires),3)
                self.assertEqual(wires[1]['tool_choice'],'required')
                self.assertEqual(len(session.trace),1)
            finally:await provider.close()

    def session(self, *, unbound=False):
        h,offer,session,request=test_direct_tools.DirectToolsTests().session()
        request=replace(request,messages=({'role':'system','content':'Synthetic creature'},
            {'role':'user','content':json.dumps(request.current)}))
        if unbound:
            session=DirectCreatureToolSession(session.supplier,
                action_candidates=request.current['snapshot']['candidates'])
        return h,offer,session,request

    async def test_promise_corrected_into_inspection_and_one_selected_field_cast(self):
        for api in ('responses','chat_completions'):
            _,_,session,request=self.session(unbound=True)
            wires=[]
            def handler(req):
                wire=json.loads(req.content);wires.append(wire)
                if len(wires)==1:
                    data=envelope(api,reply(answer="I'll try to water the fields now."))
                elif len(wires)==2:
                    data=call(api,'inspect_surrounding',{'kind':'field','limit':3,'action':'cast_miracle','miracle':'water'},'inspect')
                elif len(wires)==3:
                    items=wire['input' if api=='responses' else 'messages']
                    outputs=[i.get('output',i.get('content')) for i in items
                        if i.get('type')=='function_call_output' or i.get('role')=='tool']
                    target=json.loads(outputs[-1])['action_targets'][0]['target_id']
                    data=call(api,'do_action',{'action':'cast_miracle','miracle':'water','target':target},'select')
                else:data=envelope(api,reply(answer="I'll cast water on that field."))
                return httpx.Response(200,json=data)
            provider=LLMProvider(LLMConfig('synthetic',api=api),environ={'OPENAI_API_KEY':'synthetic'},transport=httpx.MockTransport(handler))
            provider.tool_factory=lambda:session
            try:
                result=await provider.respond(request)
                self.assertEqual(result['decision']['candidate'],'cast_water_field')
                self.assertEqual(len(wires),4)
                self.assertEqual(sum(t['tool']=='do_action' for t in session.trace),1)
                self.assertIn('No action has been selected',json.dumps(wires[1]))
                self.assertEqual(wires[0]['tool_choice'],'auto')
                self.assertEqual(wires[1]['tool_choice'],'required')
                self.assertEqual(wires[2]['tool_choice'],'auto')
                self.assertEqual(provider.tool_rounds,3)
                self.assertEqual(provider.usage['input_tokens'],40)
            finally:await provider.close()

    async def test_repeated_empty_promise_fails_after_one_correction(self):
        _,_,session,request=self.session(unbound=True)
        provider=LLMProvider(LLMConfig('synthetic'),environ={'OPENAI_API_KEY':'synthetic'},
            transport=httpx.MockTransport(lambda req:httpx.Response(200,json=envelope(result=reply(answer="I'll cast Water.")))) )
        provider.tool_factory=lambda:session
        try:
            with self.assertRaisesRegex(ProviderError,'action_not_selected'):await provider.respond(request)
            self.assertEqual(provider.calls,2)
            self.assertIsNone(session.inner.selected_action)
        finally:await provider.close()

    def test_honest_limitation_or_silence_does_not_request_correction(self):
        _,_,session,_=self.session()
        for text in ("I can't find a field target.","",'Water puts out fires.','I can try a partially learned spell.'):
            self.assertIsNone(session.reply_correction(reply(answer=text)))

    def test_observed_water_promises_and_progress_phrases_require_real_selection(self):
        _,_,session,_=self.session()
        for text in ("I'll try to water the tree now.","I'll water the tree again to help it grow.",
                     'Water on the tree, coming right up!',"I’ll cast Water.",
                     "I will attempt to heal myself.",'Watering the tree now.'):
            with self.subTest(text=text):
                self.assertIsNotNone(session.reply_correction(reply(answer=text)))
                self.assertIsNone(session.inner.selected_action)

    async def test_required_correction_cannot_be_evaded_by_another_text_reply(self):
        for api in ('responses','chat_completions'):
            _,_,session,request=self.session(unbound=True)
            wires=[]
            def handler(req):
                wires.append(json.loads(req.content))
                text="I'll water the tree now." if len(wires)==1 else 'Coming right up!'
                return httpx.Response(200,json=envelope(api,reply(answer=text)))
            provider=LLMProvider(LLMConfig('synthetic',api=api),environ={'OPENAI_API_KEY':'synthetic'},
                transport=httpx.MockTransport(handler))
            provider.tool_factory=lambda:session
            try:
                with self.assertRaisesRegex(ProviderError,'action_not_selected'):await provider.respond(request)
                self.assertEqual(len(wires),2)
                self.assertEqual(wires[-1]['tool_choice'],'required')
                self.assertIsNone(session.inner.selected_action)
            finally:await provider.close()

    def test_water_object_and_field_share_public_spell_without_cross_target_selection(self):
        _,_,session,_=self.session()
        field=session.call('inspect_surrounding',{'kind':'field','limit':3,'action':'cast_miracle','miracle':'water'})
        self.assertEqual(len(field['action_targets']),1)
        target=field['action_targets'][0]['target_id']
        result=session.call('do_action',{'action':'cast_miracle','miracle':'fireball','target':target})
        self.assertEqual(result['status'],'invalid_target')
        session.call('do_action',{'action':'cast_miracle','miracle':'water','target':target})
        self.assertEqual(session.inner.selected_action,'cast_water_field')

    def test_missing_field_is_a_target_failure_not_missing_spell_or_water(self):
        _,_,session,_=self.session()
        session.inner.action_targets.pop('cast_water_field')
        result=session.call('inspect_surrounding',{'kind':'field','limit':3,'action':'cast_miracle','miracle':'water'})
        self.assertEqual(result['status'],'no_compatible_target_nearby')
        self.assertEqual(result['miracle'],'water')
        self.assertNotIn('action_targets',result)
        self.assertIsNone(session.inner.selected_action)
