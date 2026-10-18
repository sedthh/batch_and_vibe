"""Request-bound tree selection and transport, entirely synthetic."""
import json
import re
import unittest

import httpx

from bwv.action_probe import COMMAND
from bwv.creature_tools import CreatureToolSession
import test_action_tools as action_tools
from test_creature_tools import call, frame
from test_llm import envelope
from test_pending_actions import Harness


def tree_harness():
    h = Harness()
    h.probe['candidates'].append('approach_object')
    h.probe['object_movement'] = {'protocol':4, 'manual_only':True,
        'target_generation':11, 'kind':'tree', 'distance_engine_units':28}
    return h


class TreeBindingTests(unittest.TestCase):
    def test_search_inspect_select_same_bound_target(self):
        h=tree_harness();offer=h.pending.factory(h.obs()).offer()
        request=action_tools.action_request(offer['candidates'])
        request.current['snapshot']['action_targets']=offer['action_targets']
        s=CreatureToolSession(frame,action_candidates=('approach_object',));s.bind_request(request)
        found=s.call('search_actions',{'query':'walk to tree'})['actions'][0]
        target=found['default_target']
        self.assertRegex(target,r'^tree-[a-f0-9]{32}$')
        objects=s.call('inspect_surrounding',{'kind':'tree','limit':2})
        self.assertEqual(objects['action_targets'][0]['target_id'],target)
        self.assertEqual(s.call('do_action',{'action':'approach_object','target':'observed-tree'})['status'],'invalid_target')
        self.assertEqual(s.call('do_action',{'action':'approach_object','target':target})['target'],target)
        self.assertEqual(h.sent,[])

    def test_no_target_binding_no_action_tool(self):
        s=CreatureToolSession(frame,action_candidates=('approach_object',))
        s.bind_request(action_tools.action_request(('approach_object',)))
        self.assertNotIn('do_action',{d['name'] for d in s.definitions()})

    def test_fresh_v4_packet_once_and_no_retarget(self):
        h=tree_harness();h.enqueue('approach_object')
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'queued')
        packet=COMMAND.unpack(h.sent[0])
        self.assertEqual((packet[1],packet[-2],packet[-1]),(4,5,11))
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_changed_or_revoked_target_cancels(self):
        for change in ({'target_generation':12},{'target_generation':0},{'kind':'rock'}):
            h=tree_harness();h.enqueue('approach_object')
            h.probe['object_movement'].update(change)
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(h.pending.last['status'],'cancelled_target_changed')
            self.assertEqual(h.sent,[])

    def test_native_target_race_cannot_substitute(self):
        h=tree_harness();old=h.source();h.pending.source=lambda:old
        h.enqueue('approach_object');h.probe['object_movement']['target_generation']=12
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.sent,[])
        h.pending.source=h.source;h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'cancelled_target_changed')

    def test_rock_and_unavailable_tree_are_not_offered(self):
        for change in ({'kind':'rock'},{'target_generation':0},{'distance_engine_units':31}):
            h=tree_harness();h.probe['object_movement'].update(change)
            self.assertNotIn('approach_object',h.pending.factory(h.obs()).offer()['candidates'])

    def test_late_response_and_lost_receipt_do_not_retry(self):
        h=tree_harness();offer=h.pending.factory(h.obs());offer.offer();h.now+=31
        self.assertEqual(offer.dispatch({'candidate':'approach_object'})['status'],'expired')
        self.assertEqual(h.sent,[])
        h=tree_harness()
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost;h.enqueue('approach_object');h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry');self.assertEqual(len(h.sent),1)


class TreeProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tools.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tools.ActionProtocolTests.asyncTearDown
    start=action_tools.ActionProtocolTests.start

    async def test_tree_search_selection_dispatch_both_protocols(self):
        for api in ('responses','chat_completions'):
            h=tree_harness();wires=[]
            def handler(req):
                self.assertEqual(h.sent,[])
                wires.append(json.loads(req.content))
                if len(wires)==1:
                    response=call(api,'find_targets',{'kind':None,'limit':10,'action':'move_to','miracle':None, 'scope':'nearby'})
                elif len(wires)==2:
                    target=re.findall(r'tree-[a-f0-9]{32}',req.content.decode())[-1]
                    response=call(api,'move_to',{'destination':{'target_id':target},'pace':None},'tree-choice')
                else:response=envelope(api)
                return httpx.Response(200,json=response)
            runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
            await runtime.question({'question':'approach that tree'})
            await runtime.step()
            self.assertEqual(len(h.sent),1, (api,runtime.pending_actions.snapshot(),runtime.provider.last_tool_trace,list(runtime.memory.history)))
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(5,11))
            self.assertEqual(runtime.memory.history[-1]['action_status'],'queued_unconfirmed')
            await runtime.close();self.runtime=None


if __name__ == '__main__':unittest.main()
