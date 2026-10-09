"""Synthetic snapshots and provider traffic; no game or paid API calls."""
import asyncio
from copy import deepcopy
import json
import unittest
import httpx

from bwv.creature_tools import CreatureToolSession, ToolContextUnavailable
from bwv.llm import LLMConfig, LLMProvider, ProviderError
from test_llm import request, envelope


def frame():
    return {'prompt':{'context':{'surroundings':{'trees_nearby':2}}},'observation':{
        'miracles':{'knowledge':[{'spell':'water','learning_counter':7,'learning_requirement':12,
            'learning_threshold_met':False,'learning_tick':1000}]},
        'surroundings':{'available':True,'nearby_known_objects':[
            {'object_id':'tree-a','kind':'tree','distance_engine_units':8,'private_debug':'omit'},
            {'object_id':'tree-b','kind':'tree','distance_engine_units':3}]}}}


def call(api,name,arguments,cid='c1'):
    if api=='responses':
        return {'status':'completed','output':[{'type':'reasoning','id':'r1','summary':[],
            'encrypted_content':'opaque-synthetic-reasoning'},
            {'type':'function_call','name':name,'arguments':json.dumps(arguments),'call_id':cid}]}
    return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
        'tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}}]}


class CatalogTests(unittest.TestCase):
    def test_inspection_projection_bounds_and_unavailable_capabilities(self):
        session=CreatureToolSession(frame)
        self.assertEqual({d['name'] for d in session.definitions()},
                         {'inspect_self','inspect_surrounding','search_actions','find_objects','inspect_object'})
        result=session.call('inspect_surrounding',{'kind':'tree','limit':1})
        self.assertEqual(result['objects'],[{'object_id':'tree-b','kind':'tree','distance_engine_units':3,'name':'tree'}])
        self.assertEqual(result['matching_known_objects'],2)
        self.assertFalse(result['visibility_verified'])
        knowledge=session.call('inspect_self',{})['miracle_learning'][0]
        self.assertFalse(knowledge['learning_threshold_met'])
        self.assertNotIn('learning_tick',knowledge)
        self.assertEqual(session.call('search_actions',{'query':'move to hand'})['actions'],[])
        self.assertNotIn('private_debug',json.dumps(session.trace))
        for args in ({'kind':None,'limit':True},{'kind':None,'limit':11},{'limit':1},
                     {'kind':None,'limit':1,'path':'forbidden'}):
            with self.assertRaises(ValueError):session.call('inspect_surrounding',args)


class ToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.providers=[]
    async def asyncTearDown(self):
        for p in self.providers:await p.close()

    def provider(self,handler,api='responses',supplier=frame,**kwargs):
        p=LLMProvider(LLMConfig('test-model',api=api,**kwargs),environ={'OPENAI_API_KEY':'synthetic-key'},
                      transport=httpx.MockTransport(handler))
        p.tool_factory=lambda:CreatureToolSession(supplier)
        self.providers.append(p)
        return p

    async def test_search_inspect_answer_both_protocols_and_transient_reasoning(self):
        for api in ('responses','chat_completions'):
            wires=[]
            def handler(req):
                wires.append(json.loads(req.content))
                n=len(wires)
                data=(call(api,'search_actions',{'query':'cast water'}) if n==1 else
                    call(api,'inspect_self',{},'c2') if n==2 else envelope(api))
                data['usage']=envelope(api)['usage']
                data['usage']['output_tokens_details' if api=='responses' else 'completion_tokens_details']={'reasoning_tokens':4}
                return httpx.Response(200,json=data)
            p=self.provider(handler,api,max_messages=1)
            self.assertEqual((await p.respond(request()))['answer'],'I notice trees.')
            self.assertEqual(p.calls,3)
            self.assertEqual(p.status()['messages'],1)
            self.assertEqual(p.status()['messages_remaining'],0)
            with self.assertRaisesRegex(ProviderError,'message_limit_reached'):
                await p.respond(request())
            self.assertEqual(p.calls,3)
            self.assertEqual(p.status()['token_usage']['session']['input_tokens'],60)
            self.assertEqual(p.status()['token_usage']['session']['output_tokens'],30)
            self.assertEqual(p.status()['token_usage']['session']['reasoning_tokens'],12)
            self.assertEqual(p.status()['token_usage']['session']['non_reasoning_output_tokens'],18)
            self.assertEqual(p.status()['last_usage']['reasoning_tokens'],12)
            self.assertEqual(p.status()['token_usage']['session']['total_tokens'],90)
            self.assertEqual(p.status()['token_usage']['session']['requests_with_unknown_usage'],0)
            def names(wire):return {d['name'] if api=='responses' else d['function']['name'] for d in wire['tools']}
            self.assertIn('inspect_self',names(wires[0]))
            self.assertNotIn('do_action',names(wires[1]))
            if api=='responses':
                self.assertTrue(any(i.get('encrypted_content')=='opaque-synthetic-reasoning' for i in wires[1]['input']))
                self.assertEqual(wires[1]['input'][-1]['call_id'],'c1')
            else:self.assertEqual(wires[1]['messages'][-1]['tool_call_id'],'c1')
            self.assertEqual([t['tool'] for t in p.last_tool_trace],['search_actions','inspect_self'])
            self.assertNotIn('opaque-synthetic-reasoning',repr(p.requests)+json.dumps(p.status()))

    async def test_round_limit_forces_final_and_counts_every_http_attempt(self):
        wires=[]
        def handler(req):
            wires.append(json.loads(req.content))
            return httpx.Response(200,json=call('responses','inspect_self',{},'c'+str(len(wires))))
        p=self.provider(handler,max_tool_rounds=1)
        with self.assertRaisesRegex(ProviderError,'tool_limit_reached'):await p.respond(request())
        self.assertEqual(len(wires),2)
        self.assertEqual(wires[-1]['tool_choice'],'none')
        p=self.provider(handler,max_messages=1)
        with self.assertRaisesRegex(ProviderError,'tool_limit_reached'):await p.respond(request())
        self.assertEqual(p.calls,7)
        self.assertEqual(p.messages,1)
        self.assertFalse(p.ready())

    async def test_undiscovered_bad_arguments_and_duplicate_call_id_rejected(self):
        for name,args in (('inspect_miracles',{'spell':'water'}),('inspect_self',{'path':'bad'}),
                          ('search_actions',{'query':None}),('invented_action',{})):
            p=self.provider(lambda req:httpx.Response(200,json=call('responses',name,args)))
            with self.assertRaisesRegex(ProviderError,'invalid_tool_call'):await p.respond(request())
            self.assertEqual(p.calls,1)
        p=self.provider(lambda req:httpx.Response(200,json=call('responses','inspect_self',{})))
        with self.assertRaisesRegex(ProviderError,'invalid_tool_call'):await p.respond(request())
        self.assertEqual(p.calls,2)

    async def test_search_cannot_enable_an_unavailable_action_in_same_batch(self):
        data=call('responses','search_actions',{'query':'miracles'})
        data['output'].append(call('responses','do_action',{'action':'cast','arguments':{'spell':'heal'}},'c2')['output'][-1])
        p=self.provider(lambda req:httpx.Response(200,json=data))
        with self.assertRaisesRegex(ProviderError,'invalid_tool_call'):await p.respond(request())
        self.assertEqual(p.calls,1)

    async def test_tools_disabled_compatibility_mode_and_total_call_limit(self):
        wires=[]
        def handler(req):
            wires.append(json.loads(req.content));return httpx.Response(200,json=envelope())
        p=self.provider(handler,max_tool_rounds=0)
        await p.respond(request())
        self.assertNotIn('tools',wires[0])
        data=call('responses','inspect_self',{})
        data['output'].append(call('responses','inspect_self',{},'c2')['output'][-1])
        p=self.provider(lambda req:httpx.Response(200,json=data),max_tool_calls=1)
        with self.assertRaisesRegex(ProviderError,'tool_limit_reached'):await p.respond(request())
        self.assertEqual(p.last_tool_trace,[])

    async def test_stale_mid_round_does_not_send_followup(self):
        fresh=True
        def supplier():
            if not fresh:raise ToolContextUnavailable('unavailable')
            return frame()
        def handler(req):
            nonlocal fresh
            fresh=False
            return httpx.Response(200,json=call('responses','inspect_self',{}))
        p=self.provider(handler,supplier=supplier)
        with self.assertRaisesRegex(ProviderError,'tool_context_unavailable'):await p.respond(request())
        self.assertEqual(p.calls,1)

    async def test_completed_text_can_return_without_further_scene_lookups(self):
        fresh=True
        def supplier():
            if not fresh:raise ToolContextUnavailable('unavailable')
            return frame()
        def handler(req):
            nonlocal fresh
            fresh=False
            return httpx.Response(200,json=envelope())
        p=self.provider(handler,supplier=supplier)
        self.assertEqual((await p.respond(request()))['answer'],'I notice trees.')
        self.assertEqual(p.calls,1)

    async def test_cancel_during_followup_releases_lane_and_has_no_final_answer(self):
        second=asyncio.Event();n=0
        async def handler(req):
            nonlocal n
            n+=1
            if n==1:return httpx.Response(200,json=call('responses','inspect_self',{}))
            second.set();await asyncio.Event().wait()
        p=self.provider(handler)
        pending=asyncio.create_task(p.respond(request()))
        await second.wait();pending.cancel()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.assertEqual(p.calls,2);self.assertEqual(p.active,0)
