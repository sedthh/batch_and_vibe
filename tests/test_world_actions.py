from copy import deepcopy
from pathlib import Path
import json
import re
import unittest
import httpx
from bwv.action_probe import COMMAND, WorldActionAdapter, encode
from bwv.world_actions import WORLD_ACTIONS, HELD_MIRACLE_ACTIONS, world_offers_available
from bwv.dispatch import DecisionDispatcher
from bwv.creature_tools import CreatureToolSession
from test_pickup_actions import PickupHarness
from test_action_tools import action_request
from test_creature_tools import frame, call
from test_llm import envelope
import test_action_tools as action_tests


class WorldHarness(PickupHarness):
    def __init__(self):
        super().__init__()
        self.probe['candidates'].extend(WORLD_ACTIONS)
        self.probe['world_actions']={'version':10,'offers':[
            {'action':name,'token':100+i,'kind':'self' if self.self_action(name) else 'storage' if name=='fish_to_storage' else 'worship_site' if name=='fish_to_worship' else 'field' if name=='cast_water_field' else 'villager' if name=='dance_with_villagers' else 'teleport' if name=='use_teleport' else 'tree',
                'distance_engine_units':0 if self.self_action(name) else 20}
            for i,name in enumerate(WORLD_ACTIONS)]}

    @staticmethod
    def self_action(name):
        return name.startswith('cast_creature') or name in (
            'sit_down','look_at_player','howl','laugh','cry','show_realization','pray','vomit','fish','fish_to_hand','fish_home','drink')

    def factory(self,obs,*,world_action=None,expected_world_token=None,**kwargs):
        if world_action is None:return super().factory(obs,**kwargs)
        return DecisionDispatcher(WorldActionAdapter(123,expected_identity=self.identity,
            world_action=world_action,expected_world_token=expected_world_token,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def select(self,action):
        offer=self.pending.factory(self.obs());data=offer.offer()
        target=data['action_targets'][action]['objects'][0]['target_id']
        offer.dispatch({'candidate':action,'target':target})


class WorldActionTests(unittest.TestCase):
    def test_sleep_does_not_insert_an_extra_wake_before_native_world_command(self):
        h=WorldHarness();h.probe.update(wake_protocol=3,activity={'verified':True,'sleep_in_sequence':True,
            'controller_mode':3,'animation':29,'current_subaction':132})
        h.probe['candidates'].append('wake_up')
        def unwanted_wake(obs):raise AssertionError('World action already supports native interruption')
        h.pending.wake_factory=unwanted_wake
        h.select('poo_on');h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1);self.assertEqual(COMMAND.unpack(h.sent[0])[-2],19)

    def test_all_named_recipes_dispatch_bound_version_ten_packets_once(self):
        for i,action in enumerate(WORLD_ACTIONS):
            if action in HELD_MIRACLE_ACTIONS:continue  # exercised with a held bubble in test_held_miracle
            h=WorldHarness();h.select(action)
            h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,(action,h.pending.snapshot()))
            wire=COMMAND.unpack(h.sent[0]);self.assertEqual((wire[1],wire[-2],wire[-1]),(10,19,100+i))

    def test_no_compatible_target_or_learning_revocation_cancels(self):
        h=WorldHarness();h.select('attack');h.probe['world_actions']['offers'].pop(0)
        h.pending.advance(h.obs(),allowed=True)
        self.assertFalse(h.sent);self.assertEqual(h.pending.snapshot()['status'],'cancelled_world_target_or_learning_changed')
        h=WorldHarness();d=h.factory(h.obs(),world_action='attack',expected_world_token=100);d.offer()
        h.probe['world_actions']['offers'].clear()
        self.assertEqual(d.dispatch({'candidate':'attack'})['status'],'conditions_changed');self.assertFalse(h.sent)

    def test_packet_cannot_use_another_actions_token(self):
        h=WorldHarness()
        with self.assertRaises(ValueError):encode(h.native,{'candidate':'attack'},world_token=101)

    def test_invalid_or_old_offers_fail_closed(self):
        for bad in ({'version':9,'offers':[]},{'version':10,'offers':[{'action':'arbitrary_native_call','token':1}]}):
            h=WorldHarness();h.probe['world_actions']=bad;self.assertFalse(world_offers_available(h.probe))

    def test_specific_cast_search_is_not_hidden_by_generic_cast_matches(self):
        h=WorldHarness();data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=CreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        found=s.call('search_actions',{'query':'cast creature strong'})['actions']
        self.assertEqual(found[0]['action'],'cast_creature_strong');self.assertLessEqual(len(found),8)
        target=found[0]['target_options'][0]
        s.call('do_action',{'action':'cast_creature_strong','target':target})
        self.assertEqual(s.finalize_reply({'answer':''})['decision'],{'candidate':'cast_creature_strong','target':target})

    def test_native_and_python_names_match(self):
        header=(Path(__file__).parents[1]/'native/world_actions.h').read_text()
        self.assertEqual(set(re.findall(r'\{"([a-z0-9_]+)",\d+,\d+,',header)),set(WORLD_ACTIONS))

    def test_mushroom_qa_offer_is_not_a_model_capability(self):
        h=WorldHarness()
        h.probe['world_actions']['offers']=[
            {'action':'qa_mushroom_effect','token':123,'kind':'self','distance_engine_units':0}]
        self.assertNotIn('qa_mushroom_effect',WORLD_ACTIONS)
        self.assertFalse(world_offers_available(h.probe))
        with self.assertRaises(ValueError):
            WorldActionAdapter(123,world_action='qa_mushroom_effect',expected_world_token=123)


class WorldProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=action_tests.ActionProtocolTests.asyncSetUp
    asyncTearDown=action_tests.ActionProtocolTests.asyncTearDown
    start=action_tests.ActionProtocolTests.start

    async def test_both_provider_protocols_search_select_and_execute(self):
        for api in ('responses','chat_completions'):
            for action in ('attack','poo_on','cast_creature_strong','laugh'):
                h=WorldHarness();wires=[]
                def handler(req):
                    wires.append(json.loads(req.content))
                    if action=='laugh':response=envelope(api,{'answer':'😂','decision':{'candidate':None}})
                    elif len(wires)==1:response=call(api,'find_targets',{'kind':None,'limit':10,'action':'cast_miracle' if action.startswith('cast_') else action,'miracle':'creature_strong' if action.startswith('cast_') else None, 'scope':'nearby'},'inspect')
                    elif len(wires)==2:
                        wire=wires[-1]
                        outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                            if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                        target=json.loads(outputs[-1])['action_targets'][0]['target_id']
                        response=call(api,'do_action',{'action':'cast_miracle' if action.startswith('cast_') else action,'miracle':'creature_strong' if action.startswith('cast_') else None,'target':target},'action')
                    else:response=envelope(api)
                    return httpx.Response(200,json=response)
                runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
                await runtime.question({'question':'Try the selected action'});await runtime.step()
                self.assertEqual(len(h.sent),1,(api,action,runtime.pending_actions.snapshot()))
                await runtime.close();self.runtime=None

    async def test_distant_field_find_select_and_dispatch_in_both_apis(self):
        for api in ('responses','chat_completions'):
            for action in ('move_to','poo_on'):
                h=WorldHarness();wires=[]
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']==action)
                entry.update(kind='field',distance_engine_units=600)
                def handler(req):
                    wires.append(json.loads(req.content))
                    if len(wires)==1:
                        response=call(api,'find_targets',{'kind':'field','limit':3,
                            'scope':'distant','action':action,'miracle':None},'find')
                    elif len(wires)==2:
                        wire=wires[-1]
                        outputs=([i['output'] for i in wire['input'] if i.get('type')=='function_call_output']
                            if api=='responses' else [i['content'] for i in wire['messages'] if i.get('role')=='tool'])
                        target=json.loads(outputs[-1])['action_targets'][0]['target_id']
                        response=(call(api,'move_to',{'destination':{'target_id':target},'pace':None},'act') if action=='move_to' else
                            call(api,'do_action',{'action':action,'target':target,'miracle':None},'act'))
                    else:response=envelope(api)
                    return httpx.Response(200,json=response)
                runtime=self.start(handler,api,factory=h.factory,wait=True,clock=lambda:h.now,source=h.source())
                await runtime.question({'question':'Find a distant field and go there'});await runtime.step()
                self.assertEqual(len(h.sent),1,(api,action,runtime.pending_actions.snapshot()))
                self.assertEqual(COMMAND.unpack(h.sent[0])[-1],entry['token'])
                await runtime.close();self.runtime=None
