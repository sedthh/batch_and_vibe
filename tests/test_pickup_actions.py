"""Request-bound native pickup selection, hand changes and observed completion."""
import json
import unittest
from copy import deepcopy
import httpx
from bwv.action_probe import PickupActionAdapter, COMMAND, encode, pickup_targets_available
from bwv.action_feedback import action_result
from bwv.creature_tools import CreatureToolSession
from bwv.dispatch import DecisionDispatcher
from test_pending_actions import Harness
from test_action_tools import action_request
import test_action_tools as action_tests
from test_creature_tools import frame, call
from test_llm import envelope, reply


class PickupHarness(Harness):
    def __init__(self):
        super().__init__()
        self.probe['held_actions']={'version':6,'verified':True,'target_generation':0,'kind':'unknown'}
        self.probe['candidates'].append('pick_up')
        self.probe['pickup_action']={'version':8,'verified':True,'hand_empty':True,'targets':[
            {'token':61,'kind':'tree','distance_engine_units':10},
            {'token':62,'kind':'food_pile','distance_engine_units':20}]}

    def factory(self,obs,*,expected_pickup_generation=None,**kwargs):
        if expected_pickup_generation is None:return super().factory(obs,**kwargs)
        return DecisionDispatcher(PickupActionAdapter(123,expected_identity=self.identity,
            expected_pickup_generation=expected_pickup_generation,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def enqueue_pickup(self,index=1):
        offer=self.pending.factory(self.obs());data=offer.offer()
        target=data['action_targets']['pick_up']['objects'][index]['target_id']
        return offer.dispatch({'candidate':'pick_up','target':target})


class PickupActionTests(unittest.TestCase):
    def test_second_object_selected_and_sent_once(self):
        h=PickupHarness();h.enqueue_pickup();h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        wire=COMMAND.unpack(h.sent[0]);self.assertEqual((wire[1],wire[-2],wire[-1]),(8,16,62))

    def test_target_disappearance_or_occupied_hand_cancels(self):
        for change in ('missing','held','hand_not_empty'):
            h=PickupHarness();h.enqueue_pickup()
            if change=='missing':h.probe['pickup_action']['targets'].pop()
            elif change=='held':h.probe['held_actions']['target_generation']=7
            else:h.probe['pickup_action']['hand_empty']=False
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(h.pending.last['status'],'cancelled_pickup_target_changed');self.assertFalse(h.sent)

    def test_adapter_final_read_rejects_new_object_in_hand(self):
        h=PickupHarness();d=h.factory(h.obs(),expected_pickup_generation=62);d.offer()
        h.probe['held_actions']['target_generation']=2
        self.assertEqual(d.dispatch({'candidate':'pick_up'})['status'],'conditions_changed')
        self.assertFalse(h.sent)

    def test_explicit_target_required_and_old_bridge_not_offered(self):
        h=PickupHarness()
        with self.assertRaises(ValueError):encode(h.native,{'candidate':'pick_up'})
        o=h.pending.factory(h.obs());o.offer()
        with self.assertRaises(ValueError):o.dispatch({'candidate':'pick_up'})
        h.probe.pop('pickup_action')
        self.assertNotIn('pick_up',h.pending.factory(h.obs()).offer()['candidates'])

    def test_bad_target_values_not_offered(self):
        for bad in (0,-1,20001,float('nan'),True):
            h=PickupHarness();h.probe['pickup_action']['targets'][0]['distance_engine_units']=bad
            self.assertFalse(pickup_targets_available(h.probe))

    def test_pickup_uses_own_interruption_without_separate_wake(self):
        h=PickupHarness();h.probe['candidates'].append('wake_up');h.probe['wake_protocol']=3
        def unexpected_wake(obs):self.fail('Pickup should use its native interruption directly')
        h.pending.wake_factory=unexpected_wake;h.enqueue_pickup();h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1);self.assertEqual(COMMAND.unpack(h.sent[0])[-2],16)

    def test_lost_receipt_never_replays(self):
        h=PickupHarness()
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost;h.enqueue_pickup();h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1);self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry')

    def test_search_inspection_share_target_and_reject_other_ids(self):
        h=PickupHarness();data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=CreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        found=s.call('search_actions',{'query':'pickup'})['actions']
        self.assertEqual(len(found),1);targets=found[0]['target_options']
        inspected=s.call('inspect_surrounding',{'kind':'food_pile','limit':10})
        self.assertEqual(inspected['action_targets'][0]['target_id'],targets[1])
        for bad in (None,'tree','observed-object-id'):
            self.assertEqual(s.call('do_action',{'action':'pick_up','target':bad})['status'],'invalid_target')
        s.call('do_action',{'action':'pick_up','target':targets[1]})
        self.assertEqual(s.finalize_reply(reply())['decision'],{'candidate':'pick_up','target':targets[1]})

    def test_completion_requires_observed_selected_object_not_plan_end(self):
        p={'result_current_creature':True,'requested_action':'pick_up','request_at_uptime_ms':100,
            'result_phase':'finished','outcome':'native_plan_changed_after_action','plan_observed':True,'script_owned':False}
        self.assertEqual(action_result(p,'synthetic')['status'],'plan_ended')
        p['completion']='requested_object_held_observed'
        self.assertEqual(action_result(p,'synthetic')['status'],'completed')
        p['plan_observed']=False
        self.assertNotEqual(action_result(p,'synthetic')['status'],'completed')


class PickupProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tests.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tests.ActionProtocolTests.asyncTearDown
    start=action_tests.ActionProtocolTests.start

    async def test_search_pickup_selected_food_both_provider_protocols(self):
        for api in ('responses','chat_completions'):
            h=PickupHarness();wires=[]
            def handler(req):
                self.assertFalse(h.sent);wires.append(json.loads(req.content))
                if len(wires)==1:response=call(api,'find_targets',{'kind':None,'limit':10,'action':'pick_up','miracle':None, 'scope':'nearby'})
                elif len(wires)==2:
                    wire=wires[-1]
                    outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                        if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                    objects=json.loads(outputs[-1])['action_targets']
                    food=next(t for t in objects if t['type']=='food pile')
                    response=call(api,'do_action',{'action':'pick_up','target':food['target_id']},'pickup-choice')
                else:response=envelope(api)
                return httpx.Response(200,json=response)
            runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
            await runtime.question({'question':'pick up the food'})
            await runtime.step()
            self.assertEqual(len(h.sent),1,(api,runtime.pending_actions.snapshot()))
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(16,62))
            await runtime.close();self.runtime=None


if __name__=='__main__':unittest.main()
