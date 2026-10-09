from copy import deepcopy
import unittest
from bwv.action_probe import COMMAND
from bwv.direct_tools import DirectCreatureToolSession
from bwv.world_actions import HELD_MIRACLE_ACTIONS, held_miracle_context, world_offers_available
from bwv.llm_context import summarize_context
from test_world_actions import WorldHarness
from test_action_tools import action_request
from test_creature_tools import frame


class HeldMiracleHarness(WorldHarness):
    def __init__(self, action='use_held_miracle_compassionate'):
        super().__init__()
        self.action=action
        self.probe['hand_preparation_protocol']=1
        self.probe['held_actions']={'version':6,'verified':True,'kind':'miracle_bubble','target_generation':7}
        self.probe['held_miracle']={'version':1,'verified':True,'held_generation':7,'spell':22,'bubble_spell':22}
        self.probe['world_actions']['offers']=[{'action':action,'token':900,'kind':'self' if action.endswith('_self') else 'tree',
            'distance_engine_units':0 if action.endswith('_self') else 20,'held_generation':7,'held_spell':22}]
        self.probe['candidates']=[action,'put_down']

    def session(self):
        offer=self.pending.factory(self.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        session=DirectCreatureToolSession(frame,action_candidates=data['candidates']);session.bind_request(request)
        return offer,session,request


class HeldMiracleTests(unittest.TestCase):
    def test_each_native_category_dispatches_once_without_put_down(self):
        for action in HELD_MIRACLE_ACTIONS:
            h=HeldMiracleHarness(action);offer,s,request=h.session()
            menu=s.project_request(request).current
            self.assertIn('cast_miracle',menu['capabilities']['held_object_actions'])
            self.assertNotIn('miracle_attempts',menu['capabilities'])
            self.assertFalse(any(a in HELD_MIRACLE_ACTIONS for a in menu['capabilities'].get('actions',[])))
            targets=s.call('find_targets',{'kind':None,'limit':3,'action':'cast_held_miracle'})['action_targets']
            s.call('use_held_object',{'action':'cast_miracle','target':'self' if action.endswith('_self') else targets[0]['target_id']})
            offer.dispatch(s.finalize_reply({})['decision'])
            h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,(action,h.pending.snapshot()))
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(19,900))

    def test_losing_or_replacing_bubble_cancels_without_retarget_or_drop(self):
        for change in ('lost','replaced','spell','target'):
            h=HeldMiracleHarness();h.select(h.action)
            if change=='lost':h.probe['held_actions']['target_generation']=0
            if change=='replaced':
                h.probe['held_actions']['target_generation']=8
                h.probe['held_miracle']['held_generation']=8
                h.probe['world_actions']['offers'][0].update(held_generation=8,token=901)
            if change=='spell':
                h.probe['held_miracle']['spell']=1
                h.probe['world_actions']['offers'][0].update(held_spell=1,token=901)
            if change=='target':h.probe['world_actions']['offers'][0]['token']=901
            h.pending.advance(h.obs(),allowed=True)
            self.assertFalse(h.sent,change)
            self.assertTrue(h.pending.snapshot()['status'].startswith('cancelled'),change)

    def test_only_verified_bubble_enables_held_casting(self):
        h=HeldMiracleHarness()
        self.assertEqual(len(world_offers_available(h.probe)),1)
        for key,value in (('verified',False),('spell',0),('spell',True),('bubble_spell',99),('held_generation',8)):
            probe=deepcopy(h.probe);probe['held_miracle'][key]=value
            self.assertFalse(world_offers_available(probe),(key,value))
            self.assertIsNone(held_miracle_context(probe))
        h.probe['held_actions']['kind']='toy'
        self.assertFalse(world_offers_available(h.probe))

    def test_compact_context_distinguishes_bubble_variant_from_native_cast(self):
        h=HeldMiracleHarness();h.probe['held_miracle']['bubble_spell']=23
        compact=summarize_context({},probe=h.probe)
        bubble=compact['self']['held_object']['miracle']
        self.assertEqual(bubble['spell'],'water_power_up_1')
        self.assertEqual(bubble['cast_spell'],'water')
        self.assertTrue(bubble['consumed_when_attempted'])
        self.assertFalse(bubble['cast_success_guaranteed'])
        self.assertNotIn('miracles',compact['self'])
        h.probe['held_actions']['target_generation']=0
        self.assertEqual(summarize_context({},probe=h.probe),{})

    def test_normal_spells_still_require_empty_hand_at_dispatch(self):
        h=HeldMiracleHarness()
        h.probe['world_actions']['offers'].append({'action':'cast_water_object','token':901,'kind':'tree','distance_engine_units':20})
        self.assertEqual([e['action'] for e in world_offers_available(h.probe)],[h.action])
        h.probe['hand_preparation_protocol']=1
        self.assertEqual(len(world_offers_available(h.probe,planning=True)),2)

    def test_held_cast_cannot_select_a_different_spell(self):
        h=HeldMiracleHarness();_,s,_=h.session()
        self.assertEqual(s._recipes('cast_held_miracle','fireball'),[])


if __name__=='__main__':unittest.main()
