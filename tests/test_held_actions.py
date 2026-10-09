"""Held-object intentions bind one object across inference and dispatch."""
import unittest
import json
import re
import httpx
from copy import deepcopy
from bwv.action_probe import HeldActionAdapter,HELD_ACTIONS,ACTIONS,COMMAND,dummy_decision
from bwv.held_objects import HELD_KINDS
from bwv.dispatch import DecisionDispatcher
from bwv.creature_tools import CreatureToolSession
from test_pending_actions import Harness
from test_action_tools import action_request
from test_creature_tools import frame
from test_creature_tools import call
from test_llm import envelope
import test_action_tools as action_tools


class HeldHarness(Harness):
    def __init__(self,kind='food_pile'):
        super().__init__()
        self.probe['held_actions']={'version':6,'verified':True,'kind':kind,'target_generation':7,'discard_version':1}
        self.probe['candidates']=list(HELD_ACTIONS)
    def factory(self,obs,*,expected_held_generation=None,**kwargs):
        if expected_held_generation is None:return super().factory(obs,**kwargs)
        return DecisionDispatcher(HeldActionAdapter(123,expected_identity=self.identity,
            expected_held_generation=expected_held_generation,read=lambda pid:deepcopy(self.native),
            send=self.send),clock=lambda:self.now)


class HeldActionTests(unittest.TestCase):
    def test_old_bridge_does_not_grant_discard_even_if_listed(self):
        h=HeldHarness('tree');h.probe['held_actions'].pop('discard_version')
        self.assertNotIn('discard_held',h.pending.factory(h.obs()).offer()['candidates'])
        self.assertIsNone(dummy_decision(h.native,'discard_held')['candidate'])

    def test_discard_binds_current_item_and_never_retargets_after_replacement(self):
        for kind in HELD_KINDS:
            h=HeldHarness(kind);h.enqueue('discard_held')
            h.probe['held_actions']['target_generation']=8
            h.pending.advance(h.obs(),allowed=True)
            self.assertFalse(h.sent,kind)
            self.assertEqual(h.pending.last['status'],'cancelled_held_object_changed')

    def test_engine_edibility_allows_every_held_kind_and_is_rechecked(self):
        for kind in HELD_KINDS:
            h=HeldHarness(kind)
            h.probe['held_actions'].update(edibility_version=1,can_eat=True)
            offer=h.pending.factory(h.obs()).offer()
            self.assertIn('eat_held',offer['candidates'],kind)
            request=action_request(tuple(HELD_ACTIONS))
            request.current['snapshot']['action_targets']=offer['action_targets']
            session=CreatureToolSession(frame,action_candidates=HELD_ACTIONS)
            session.bind_request(request)
            selected=session.call('do_action',{'action':'eat_held','target':None})
            self.assertEqual(selected['status'],'selected_pending_final_reply',kind)
            h.enqueue('eat_held');h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(14,7))
            h=HeldHarness(kind)
            h.probe['held_actions'].update(edibility_version=1,can_eat=False)
            self.assertNotIn('eat_held',h.pending.factory(h.obs()).offer()['candidates'])

    def test_edibility_change_cancels_queued_eating(self):
        h=HeldHarness('mushroom')
        h.probe['held_actions'].update(edibility_version=1,can_eat=True)
        h.enqueue('eat_held')
        h.probe['held_actions']['can_eat']=False
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'cancelled_held_object_changed')
        self.assertFalse(h.sent)

    def test_unclassified_held_object_can_be_put_down(self):
        h=HeldHarness('object')
        self.assertEqual(h.pending.factory(h.obs()).offer()['candidates'],['put_down','discard_held'])
        h.enqueue('put_down');h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(12,7))

    def test_each_food_command_uses_bound_v6_packet_and_only_once(self):
        for action in HELD_ACTIONS:
            code=ACTIONS[action]
            h=HeldHarness();h.enqueue(action);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,action)
            packet=COMMAND.unpack(h.sent[0]);self.assertEqual((packet[1],packet[-2],packet[-1]),(6,code,7))
            h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_eating_is_not_offered_for_tree_rock_or_unknown(self):
        for kind in (*[k for k in HELD_KINDS if k!='food_pile'],'unknown'):
            h=HeldHarness(kind);offer=h.pending.factory(h.obs()).offer()
            self.assertNotIn('eat_held',offer['candidates'])
            self.assertIsNone(dummy_decision(h.native,'eat_held')['candidate'])
            if kind!='unknown':self.assertEqual(set(offer['candidates']),{'put_down','discard_held'})

    def test_old_or_unverified_bridge_never_grants_held_commands(self):
        for metadata in ({},{'version':6,'verified':False,'kind':'food_pile','target_generation':7},
                         {'version':6,'verified':True,'kind':'food_pile','target_generation':True}):
            h=HeldHarness();h.probe['held_actions']=metadata
            self.assertFalse(set(h.pending.factory(h.obs()).offer()['candidates'])&set(HELD_ACTIONS))
            self.assertIsNone(dummy_decision(h.native,'put_down')['candidate'])

    def test_release_replace_or_reacquire_cancels_without_retargeting(self):
        for token in (0,8,9):
            h=HeldHarness();h.enqueue('eat_held');h.probe['held_actions']['target_generation']=token
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(h.pending.last['status'],'cancelled_held_object_changed')
            self.assertFalse(h.sent)

    def test_target_change_between_capture_and_submit_sends_nothing(self):
        h=HeldHarness();d=h.factory(h.obs(),expected_held_generation=7);d.offer()
        h.probe['held_actions']['target_generation']=8
        self.assertEqual(d.dispatch({'candidate':'put_down'})['status'],'conditions_changed')
        self.assertFalse(h.sent)

    def test_lost_receipt_is_not_retried(self):
        h=HeldHarness()
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost;h.enqueue('put_down');h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry');self.assertEqual(len(h.sent),1)

    def test_search_exposes_only_request_bound_held_target(self):
        h=HeldHarness();offer=h.pending.factory(h.obs()).offer()
        request=action_request(tuple(HELD_ACTIONS));request.current['snapshot']['action_targets']=offer['action_targets']
        s=CreatureToolSession(frame,action_candidates=HELD_ACTIONS);s.bind_request(request)
        found=s.call('search_actions',{'query':'held'})['actions'];self.assertEqual(len(found),3)
        targets={a['default_target'] for a in found};self.assertEqual(len(targets),1)
        target=targets.pop();self.assertTrue(target.startswith('held-'))
        self.assertEqual(s.call('do_action',{'action':'eat_held','target':'tree-wrong'})['status'],'invalid_target')
        self.assertEqual(s.call('do_action',{'action':'eat_held','target':target})['status'],'selected_pending_final_reply')

    def test_missing_target_cannot_be_selected_even_if_named_in_candidates(self):
        s=CreatureToolSession(frame,action_candidates=HELD_ACTIONS);s.bind_request(action_request(HELD_ACTIONS))
        with self.assertRaisesRegex(ValueError,'Tool not offered'):
            s.call('do_action',{'action':'eat_held','target':None})

class HeldProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tools.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tools.ActionProtocolTests.asyncTearDown
    start=action_tools.ActionProtocolTests.start

    async def test_search_select_and_dispatch_through_both_provider_protocols(self):
        for api in ('responses','chat_completions'):
            h=HeldHarness();wires=[]
            def handler(req):
                self.assertFalse(h.sent);wires.append(json.loads(req.content))
                if len(wires)==1:response=call(api,'use_held_object',{'action':'eat','target':None},'held-choice')
                else:response=envelope(api)
                return httpx.Response(200,json=response)
            runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
            await runtime.question({'question':'eat the food in your hand'})
            await runtime.step()
            self.assertEqual(len(h.sent),1,(api,runtime.pending_actions.snapshot()))
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(14,7))

if __name__=='__main__':unittest.main()
