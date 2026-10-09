"""Synthetic miracle requests: no game calls, provider traffic or learned-value writes."""
from copy import deepcopy
import json
import unittest
import httpx
from bwv.action_probe import COMMAND, MiracleActionAdapter, encode, miracle_actions_available
from bwv.action_feedback import action_result
from bwv.creature_tools import CreatureToolSession
from bwv.dispatch import DecisionDispatcher
from test_pickup_actions import PickupHarness
from test_action_tools import action_request
import test_action_tools as action_tests
from test_creature_tools import frame, call
from test_llm import envelope, reply


class MiracleHarness(PickupHarness):
    def __init__(self):
        super().__init__()
        self.probe['candidates'].extend(('cast_heal_self','cast_water_tree'))
        self.probe['miracle_actions']={'version':9,'verified':True,
            'heal_learning_observed':True,'water_learning_observed':True}

    def factory(self,obs,*,miracle_action=None,expected_miracle_target=None,**kwargs):
        if miracle_action is None:return super().factory(obs,**kwargs)
        return DecisionDispatcher(MiracleActionAdapter(123,expected_identity=self.identity,
            miracle_action=miracle_action,expected_miracle_target=expected_miracle_target,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def enqueue_cast(self,action):
        offer=self.pending.factory(self.obs());data=offer.offer()
        decision={'candidate':action}
        if action=='cast_water_tree':decision['target']=data['action_targets'][action]['objects'][0]['target_id']
        return offer.dispatch(decision)


class MiracleActionTests(unittest.TestCase):
    def test_both_casts_have_distinct_bound_packets_and_only_one_submission(self):
        for action,code,target in (('cast_heal_self',17,0),('cast_water_tree',18,61)):
            h=MiracleHarness();h.enqueue_cast(action)
            h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,h.pending.snapshot())
            wire=COMMAND.unpack(h.sent[0]);self.assertEqual((wire[1],wire[-2],wire[-1]),(9,code,target))

    def test_learning_absent_or_old_bridge_never_grants_casting(self):
        for field,value in (('version',8),('verified',False)):
            h=MiracleHarness();h.probe['miracle_actions'][field]=value
            self.assertFalse(miracle_actions_available(h.probe))
        h=MiracleHarness();h.probe['miracle_actions']['heal_learning_observed']=False
        self.assertNotIn('cast_heal_self',h.pending.factory(h.obs()).offer()['candidates'])
        h.probe['miracle_actions']['water_learning_observed']=False
        self.assertFalse(miracle_actions_available(h.probe))

    def test_learning_or_target_changes_cancel_before_sending(self):
        for change in ('learning','missing','wrong_kind','hand'):
            h=MiracleHarness();h.enqueue_cast('cast_water_tree')
            if change=='learning':h.probe['miracle_actions']['water_learning_observed']=False
            elif change=='missing':h.probe['pickup_action']['targets'].pop(0)
            elif change=='wrong_kind':h.probe['pickup_action']['targets'][0]['kind']='rock'
            else:h.probe['held_actions']['target_generation']=4
            h.pending.advance(h.obs(),allowed=True)
            self.assertFalse(h.sent)
            self.assertTrue(h.pending.snapshot()['status'].startswith('cancelled_'))

    def test_disappeared_tree_retargets_another_tree_once(self):
        h=MiracleHarness();h.enqueue_cast('cast_water_tree')
        h.probe['pickup_action']['targets'][0]['token']=70
        h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],70)
        self.assertEqual(h.pending.last['retarget_count'],1)

    def test_final_native_read_rechecks_learning_and_target(self):
        h=MiracleHarness();d=h.factory(h.obs(),miracle_action='cast_water_tree',expected_miracle_target=61)
        d.offer();h.probe['pickup_action']['targets'][0]['token']=70
        self.assertEqual(d.dispatch({'candidate':'cast_water_tree'})['status'],'conditions_changed')
        self.assertFalse(h.sent)
        with self.assertRaises(ValueError):encode(h.native,{'candidate':'cast_water_tree'},miracle_target=62)

    def test_search_and_selection_keep_water_tree_id_and_never_claim_effect(self):
        h=MiracleHarness();data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=CreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        actions=s.call('search_actions',{'query':'cast water'})['actions']
        water=next(a for a in actions if a['action']=='cast_water_tree')
        self.assertEqual(len(water['objects']),1)
        self.assertEqual(water['objects'][0]['kind'],'tree')
        for target in (None,'self','tree','pickup-'+'a'*32):
            self.assertEqual(s.call('do_action',{'action':'cast_water_tree','target':target})['status'],'invalid_target')
        target=water['target_options'][0]
        self.assertFalse(s.call('do_action',{'action':'cast_water_tree','target':target})['executed'])
        self.assertEqual(s.finalize_reply(reply())['decision'],{'candidate':'cast_water_tree','target':target})
        p={'result_current_creature':True,'requested_action':'cast_water_tree','request_at_uptime_ms':100,
            'result_phase':'finished','outcome':'native_plan_changed_after_action','plan_observed':True,'script_owned':False}
        self.assertEqual(action_result(p,'fixture')['status'],'plan_ended')


class MiracleProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tests.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tests.ActionProtocolTests.asyncTearDown
    start=action_tests.ActionProtocolTests.start

    async def test_bad_inspection_arguments_can_be_corrected_before_one_cast(self):
        for api in ('responses','chat_completions'):
            h=MiracleHarness();wires=[]
            def handler(req):
                wire=json.loads(req.content);wires.append(wire);n=len(wires)
                outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                    if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                self.assertEqual(h.sent,[])
                if n<=2:
                    if n==2:
                        rejected=json.loads(outputs[-1])
                        self.assertEqual(rejected['reason'],'invalid_limit')
                        self.assertFalse(rejected['executed'])
                    response=call(api,'inspect_surrounding',{'kind':'tree','limit':99 if n==1 else 3,
                        'action':'cast_miracle','miracle':'water'},'inspect'+str(n))
                elif n==3:
                    target=json.loads(outputs[-1])['action_targets'][0]['target_id']
                    response=call(api,'do_action',{'action':'cast_miracle','miracle':'water','target':target},'cast')
                else:response=envelope(api)
                return httpx.Response(200,json=response)
            runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
            await runtime.question({'question':'Water a tree'})
            self.assertEqual(runtime.provider.calls,4)
            self.assertIsNone(runtime.provider.last_error)
            self.assertEqual(runtime.provider.status()['tool_error']['parameter'],'limit')
            self.assertTrue(any(e['code']=='invalid_tool_arguments' for e in runtime.provider.status()['errors']))
            await runtime.step();await runtime.step()
            self.assertEqual(len(h.sent),1)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2],18)
            await runtime.close();self.runtime=None

    async def test_repeated_argument_errors_exhaust_budget_without_casting(self):
        h=MiracleHarness();calls=[]
        def handler(req):
            calls.append(1)
            return httpx.Response(200,json=call('responses','inspect_surrounding',
                {'kind':'tree','limit':99,'action':'cast_miracle','miracle':'water'},'bad'+str(len(calls))))
        runtime=self.start(handler,'responses',factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
        with self.assertRaisesRegex(RuntimeError,'ProviderError'):
            await runtime.question({'question':'Water a tree'})
        self.assertEqual(runtime.provider.last_error,'tool_limit_reached')
        await runtime.step()
        self.assertEqual(len(calls),runtime.provider.config.max_tool_rounds+1)
        self.assertEqual(h.sent,[])

    async def test_both_protocols_search_select_cast_without_paid_calls(self):
        for api in ('responses','chat_completions'):
            for action in ('cast_heal_self','cast_water_tree'):
                h=MiracleHarness();wires=[]
                def handler(req):
                    wires.append(json.loads(req.content))
                    if len(wires)==1:response=call(api,'inspect_surrounding',{'kind':None,'limit':10,'action':'cast_miracle','miracle':'heal' if action=='cast_heal_self' else 'water'},'inspect')
                    elif len(wires)==2:
                        wire=wires[-1]
                        outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                            if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                        target='self' if action=='cast_heal_self' else json.loads(outputs[-1])['action_targets'][0]['target_id']
                        response=call(api,'do_action',{'action':'cast_miracle','miracle':'heal' if action=='cast_heal_self' else 'water','target':target},'cast')
                    else:response=envelope(api)
                    return httpx.Response(200,json=response)
                runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
                await runtime.question({'question':'Please cast the requested miracle'})
                await runtime.step()
                self.assertEqual(len(h.sent),1,(api,action,runtime.pending_actions.snapshot()))
                self.assertEqual(COMMAND.unpack(h.sent[0])[-2],17 if action=='cast_heal_self' else 18)
                await runtime.close();self.runtime=None
