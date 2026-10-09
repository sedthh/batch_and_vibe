"""Explicit throw targets survive tool rounds, never silently retarget or replay."""
import json
import unittest
from copy import deepcopy

import httpx

from bwv.action_probe import COMMAND, ThrowActionAdapter, encode, throw_targets_available
from bwv.creature_tools import CreatureToolSession
from bwv.dispatch import DecisionDispatcher
from test_action_tools import action_request
import test_action_tools as action_tests
from test_creature_tools import frame, call
from test_held_actions import HeldHarness
from test_llm import envelope, reply


class ThrowHarness(HeldHarness):
    def __init__(self):
        super().__init__('tree')
        self.probe['candidates'].append('throw_held_at')
        self.probe['throw_action']={'version':7,'verified':True,'held_generation':7,'targets':[
            {'token':51,'kind':'tree','distance_engine_units':10},
            {'token':52,'kind':'rock','distance_engine_units':20}]}

    def factory(self,obs,*,expected_throw_generation=None,expected_held_generation=None,**kwargs):
        if expected_throw_generation is None:
            return super().factory(obs,expected_held_generation=expected_held_generation,**kwargs)
        return DecisionDispatcher(ThrowActionAdapter(123,expected_identity=self.identity,
            expected_throw_generation=expected_throw_generation,expected_held_generation=expected_held_generation,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def enqueue_throw(self,index=1):
        offer=self.pending.factory(self.obs());data=offer.offer()
        target=data['action_targets']['throw_held_at']['destinations'][index]['target_id']
        return offer.dispatch({'candidate':'throw_held_at','target':target})


class ThrowActionTests(unittest.TestCase):
    def test_throw_destinations_include_fields_and_dispensers_with_larger_catalog(self):
        from bwv.world_actions import OBJECT_TARGET_LIMIT, WORLD_KINDS
        h=ThrowHarness()
        h.probe['throw_action']['targets']=[{'token':100+i,
            'kind':WORLD_KINDS[1+i%(len(WORLD_KINDS)-1)],'distance_engine_units':20+i}
            for i in range(OBJECT_TARGET_LIMIT)]
        offer=h.pending.factory(h.obs());data=offer.offer()
        self.assertIn('throw_held_at',data['candidates'])
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=CreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        targets=s.action_targets['throw_held_at']['destinations']
        self.assertTrue({'field','miracle_dispenser'}<={t['kind'] for t in targets})
        target=next(t for t in targets if t['kind']=='field')
        offer.dispatch({'candidate':'throw_held_at','target':target['target_id']})
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_explicit_second_target_reaches_packet_once(self):
        h=ThrowHarness();h.enqueue_throw();h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        packet=COMMAND.unpack(h.sent[0]);self.assertEqual((packet[1],packet[-2],packet[-1]),(7,15,52))

    def test_projectile_target_session_or_control_change_cancels(self):
        for change,status in (('held','cancelled_held_object_changed'),('target','cancelled_throw_target_changed'),
                              ('control','cancelled_control_changed')):
            h=ThrowHarness();h.enqueue_throw()
            if change=='held':h.probe['held_actions']['target_generation']=8
            if change=='target':h.probe['throw_action']['targets'].pop()
            h.pending.advance(h.obs(),allowed=change!='control')
            self.assertEqual(h.pending.last['status'],status);self.assertFalse(h.sent)

    def test_moving_target_keeps_identity_and_no_default_is_allowed(self):
        h=ThrowHarness();h.enqueue_throw()
        h.probe['throw_action']['targets'][1]['distance_engine_units']=21
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)
        h=ThrowHarness()
        with self.assertRaises(ValueError):encode(h.native,{'candidate':'throw_held_at'})
        offer=h.pending.factory(h.obs());offer.offer()
        with self.assertRaises(ValueError):offer.dispatch({'candidate':'throw_held_at'})

    def test_adapter_rechecks_both_bindings_before_transport(self):
        for change in ('target','held','session'):
            h=ThrowHarness();d=h.factory(h.obs(),expected_throw_generation=52,expected_held_generation=7);d.offer()
            if change=='target':h.probe['throw_action']['targets'][1]['token']=53
            elif change=='held':h.probe['held_actions']['target_generation']=8
            else:h.native['session']=4
            self.assertEqual(d.dispatch({'candidate':'throw_held_at'})['status'],'conditions_changed')
            self.assertFalse(h.sent)

    def test_missing_or_malformed_contract_not_offered(self):
        for bad in ({}, {'version':6}, {'version':7,'verified':False}):
            h=ThrowHarness();h.probe['throw_action']=bad
            self.assertNotIn('throw_held_at',h.pending.factory(h.obs()).offer()['candidates'])
        for distance in (-1,0,20001,float('nan'),float('inf'),True):
            h=ThrowHarness();h.probe['throw_action']['targets'][0]['distance_engine_units']=distance
            self.assertEqual(throw_targets_available(h.probe),())

    def test_lost_receipt_never_replayed(self):
        h=ThrowHarness()
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost;h.enqueue_throw();h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1);self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry')

    def test_search_inspection_and_selection_share_explicit_targets(self):
        h=ThrowHarness();offer=h.pending.factory(h.obs()).offer()
        request=action_request(offer['candidates']);request.current['snapshot'].update(offer)
        s=CreatureToolSession(frame,action_candidates=offer['candidates']);s.bind_request(request)
        found=s.call('search_actions',{'query':'throw'})['actions']
        self.assertEqual({item['action'] for item in found},{'throw_held_at','discard_held'})
        aimed=next(item for item in found if item['action']=='throw_held_at')
        targets=aimed['target_options'];self.assertEqual(len(targets),2);self.assertNotIn(None,targets)
        self.assertEqual(aimed['projectile']['location'],'in_creature_hand')
        detailed=s.call('inspect_surrounding',{'kind':'rock','limit':10})
        self.assertEqual(detailed['action_targets'][0]['target_id'],targets[1])
        for invalid in (None,'rock','throw-invented'):
            self.assertEqual(s.call('do_action',{'action':'throw_held_at','target':invalid})['status'],'invalid_target')
        selected=s.call('do_action',{'action':'throw_held_at','target':targets[1]})
        self.assertEqual(selected['target'],targets[1]);self.assertFalse(selected['executed'])
        self.assertEqual(s.finalize_reply(reply())['decision'],{'candidate':'throw_held_at','target':targets[1]})


class ThrowProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tests.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tests.ActionProtocolTests.asyncTearDown
    start=action_tests.ActionProtocolTests.start

    async def test_explicit_nonfirst_target_through_both_provider_protocols(self):
        for api in ('responses','chat_completions'):
            h=ThrowHarness();wires=[]
            def handler(req):
                self.assertFalse(h.sent);wires.append(json.loads(req.content))
                if len(wires)==1:response=call(api,'inspect_surrounding',{'kind':None,'limit':10,'action':'throw_at','miracle':None})
                elif len(wires)==2:
                    wire=wires[-1]
                    outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                        if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                    targets=[t['target_id'] for t in json.loads(outputs[-1])['action_targets']]
                    self.assertEqual(len(targets),2)
                    response=call(api,'use_held_object',{'action':'throw_at','target':targets[1]},'throw-choice')
                else:response=envelope(api)
                return httpx.Response(200,json=response)
            runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
            await runtime.question({'question':'throw what you hold at the rock'})
            await runtime.step()
            self.assertEqual(len(h.sent),1,(api,runtime.pending_actions.snapshot()))
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(15,52))
            await runtime.close();self.runtime=None


if __name__=='__main__':unittest.main()
