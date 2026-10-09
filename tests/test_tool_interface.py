"""Public tools exercised through native adapters and synthetic model turns."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
import httpx

from bwv.action_probe import COMMAND
from bwv.conversation import ConversationMemory
from bwv.creature_tools import TOOL_CONTRACT, ToolArgumentError
from bwv.direct_tools import DirectCreatureToolSession, EATING_RECIPES
from bwv.llm import LLMConfig, LLMProvider
from bwv.object_aliases import target_matches
from test_action_tools import action_request
from test_creature_tools import frame, call
from test_llm import envelope
from test_world_actions import WorldHarness
from test_positions import PositionHarness
from test_held_actions import HeldHarness


def bound(h, source=None, resolver=None, memory=None):
    offer=h.pending.factory(h.obs());data=offer.offer()
    request=action_request(data['candidates']);request.current['snapshot'].update(data)
    session=DirectCreatureToolSession(lambda:source if source is not None else frame(),
        action_candidates=data['candidates'],target_resolver=resolver,memory=memory)
    session.bind_request(request)
    return session,offer,request


class ToolInterfaceTests(unittest.TestCase):
    def test_fishing_cannot_pretend_to_select_a_particular_fishing_area(self):
        h=WorldHarness();session,offer,_=bound(h)
        entry=next(t for t in h.probe['world_actions']['offers'] if t['action']=='fish')
        invalid=session.call('do_action',{'action':'fish','target':'fishing-area-id'})
        self.assertEqual(invalid['status'],'invalid_target')
        self.assertIsNone(session.inner.selected_action)
        result=session.call('do_action',{'action':'fish','target':'self'})
        self.assertEqual(result['status'],'selected_pending_final_reply')
        offer.dispatch(session.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(19,entry['token']))

    def test_legacy_fishing_target_is_omitted_without_hiding_other_actions(self):
        h=WorldHarness()
        entry=next(t for t in h.probe['world_actions']['offers'] if t['action']=='fish')
        entry.update(kind='fishing_area',distance_engine_units=20)
        session,_,_=bound(h)
        self.assertNotIn('fish',session.menu)
        self.assertIn('attack',session.menu)
        self.assertIn('fish uses target=self',next(d['description'] for d in session.definitions() if d['name']=='do_action'))

    def test_discard_needs_no_destination_and_shares_action_limit(self):
        h=HeldHarness('tree');session,offer,_=bound(h)
        self.assertIn('discard',session.held_menu)
        self.assertNotIn('discard_held',session.menu)
        invalid=session.call('use_held_object',{'action':'discard','target':'some-tree'})
        self.assertEqual(invalid['status'],'invalid_target')
        self.assertIsNone(session.inner.selected_action)
        selected=session.call('use_held_object',{'action':'discard','target':None})
        self.assertEqual(selected['status'],'selected_pending_final_reply')
        self.assertFalse(selected['executed'])
        second=session.call('use_held_object',{'action':'put_down','target':None})
        self.assertNotEqual(second['status'],'selected_pending_final_reply')
        decision=session.finalize_reply({})['decision']
        self.assertEqual(decision['candidate'],'discard_held')
        offer.dispatch(decision);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        packet=COMMAND.unpack(h.sent[0])
        self.assertEqual((packet[1],packet[-2],packet[-1]),(6,21,7))

    def test_vomit_is_self_action_not_emoji_or_targeted_world_interaction(self):
        h=WorldHarness()
        entry=next(t for t in h.probe['world_actions']['offers'] if t['action']=='vomit')
        entry.update(kind='self',distance_engine_units=0)
        session,offer,_=bound(h)
        self.assertIn('vomit',session.menu)
        result=session.call('do_action',{'action':'vomit','target':'self'})
        self.assertEqual(result['status'],'selected_pending_final_reply')
        self.assertFalse(result['executed'])
        decision=session.finalize_reply({})['decision']
        offer.dispatch(decision);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        packet=COMMAND.unpack(h.sent[0])
        self.assertEqual((packet[1],packet[-2],packet[-1]),(10,19,entry['token']))
        self.assertIn('vomit: Attempt native vomiting on the spot',next(d['description'] for d in session.definitions() if d['name']=='do_action'))

    def test_inspection_pages_remain_stable_refresh_and_keep_priority(self):
        source=frame();near=source['observation']['surroundings']
        original=[{'object_id':str(i),'kind':'tree','distance_engine_units':i+1,
                   'lookup_handle':100+i} for i in range(23)]
        original.append({'object_id':'bubble','kind':'miracle_bubble','distance_engine_units':100})
        near['nearby_known_objects']=original
        session,_,_=bound(WorldHarness(),source)
        args={'kind':None,'limit':10,'offset':0}
        first=session.call('inspect_surrounding',args)
        self.assertEqual(first['objects'][0]['type'],'miracle bubble')
        self.assertEqual(first['next_offset'],10)
        # Later movement/destruction must not reorder unvisited pages in this turn.
        near['nearby_known_objects']=[{'object_id':'new','kind':'house','distance_engine_units':1}]
        second=session.call('inspect_surrounding',{**args,'offset':10})
        third=session.call('inspect_surrounding',{**args,'offset':second['next_offset']})
        ids=[o['object_id'] for page in (first,second,third) for o in page['objects']]
        self.assertEqual(len(ids),24);self.assertEqual(len(set(ids)),24)
        self.assertIsNone(third['next_offset'])
        self.assertNotIn('lookup_handle',json.dumps(session.trace))
        refreshed=session.call('inspect_surrounding',args)
        self.assertEqual([o['object_id'] for o in refreshed['objects']],['new'])
        fresh_session,_,_=bound(WorldHarness(),source)
        self.assertEqual(fresh_session.call('inspect_surrounding',args)['objects'],refreshed['objects'])

    def test_later_inspection_page_resolves_its_own_target_and_town(self):
        h=WorldHarness();h.probe['world_actions']['offers']=[]
        h.probe['target_lookup_protocol']=12
        h.probe['world_actions']['available_recipes']=['move_to']
        source=frame();source['observation']['surroundings']={'available':True,
            'known_objects':[{'object_id':str(i),'kind':'house','distance_engine_units':i+1,
                              'town_id':'town-a' if i==10 else 'town-b'} for i in range(11)],
            'towns':[{'town_id':'town-a'},{'town_id':'town-b'}]}
        resolved=[]
        def resolve(identity,recipe):
            resolved.append((identity,recipe))
            return [{'object_id':identity,'kind':'house','distance_engine_units':11,
                     'target_id':'world-'+'f'*32}]
        session,_,_=bound(h,source,resolve)
        result=session.call('inspect_surrounding',{'kind':'house','limit':10,'offset':10,'action':'move_to'})
        self.assertEqual(resolved,[('10','move_to')])
        self.assertEqual(result['towns'],[{'town_id':'town-a'}])
        self.assertEqual(result['action_targets'][0]['object_id'],'10')
        self.assertEqual(result['action_targets'][0]['type'],'house')
        self.assertEqual(session.call('move_to',{'destination':{'target_id':result['action_targets'][0]['target_id']}})['status'],
                         'selected_pending_final_reply')
        self.assertFalse(h.sent)

    def test_inspection_pagination_keeps_hand_and_creature_views_separate(self):
        source=frame();source['observation']['surroundings']={'available':True,'god_hand_available':True,
            'known_objects':[{'object_id':'near-self','kind':'house','distance_engine_units':10,'distance_to_god_hand_engine_units':500},
                             {'object_id':'near-hand','kind':'house','distance_engine_units':500,'distance_to_god_hand_engine_units':10}]}
        session,_,_=bound(WorldHarness(),source)
        for center,expected in [('creature','near-self'),('god_hand','near-hand')]:
            result=session.call('inspect_surrounding',{'kind':'house','limit':10,'offset':0,'center':center})
            self.assertEqual([o['object_id'] for o in result['objects']],[expected])

    def test_grouped_eat_dispatches_correct_native_recipe_for_every_source(self):
        for kind,recipe in EATING_RECIPES.items():
            with self.subTest(kind=kind):
                h=WorldHarness()
                h.probe['world_actions']['offers']=[{'action':r,'kind':k,'token':100+i,'distance_engine_units':10+i}
                    for i,(k,r) in enumerate(EATING_RECIPES.items())]
                session,offer,_=bound(h)
                found=session.call('find_targets',{'kind':'food_source','limit':10,'action':'eat'})
                self.assertEqual({o['type'] for o in found['action_targets']},{k.replace('_',' ') for k in EATING_RECIPES})
                target=next(o for o in found['action_targets'] if o['type']==kind.replace('_',' '))
                result=session.call('do_action',{'action':'eat','target':target['target_id']})
                self.assertEqual(result['selected_object']['type'],kind.replace('_',' '))
                decision=session.finalize_reply({})['decision']
                self.assertEqual(decision['candidate'],recipe)
                offer.dispatch(decision);h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
                self.assertEqual(len(h.sent),1)
                self.assertEqual(COMMAND.unpack(h.sent[0])[-1],next(o['token'] for o in h.probe['world_actions']['offers'] if o['action']==recipe))

    def test_move_by_identity_resolves_exact_object_not_an_arbitrary_replacement(self):
        calls=[]
        def resolve(identity,recipe):
            calls.append((identity,recipe))
            return [{'object_id':'house-a','kind':'house','target_id':'world-'+'f'*32}] if identity=='house-a' else []
        session,_,_=bound(WorldHarness(),resolver=resolve)
        result=session.call('move_to',{'destination':{'object_id':'missing'}})
        self.assertEqual(result['status'],'object_not_currently_approachable')
        self.assertIsNone(session.inner.selected_action)
        result=session.call('move_to',{'destination':{'object_id':'house-a'},'pace':'run'})
        self.assertEqual(result['selected_object']['object_id'],'house-a')
        self.assertEqual(result['selected_object']['type'],'house')
        self.assertEqual(calls,[('missing','move_to'),('house-a','move_to')])

    def test_bad_move_destinations_do_not_select_and_menu_has_no_duplicate_actions(self):
        session,_,_=bound(PositionHarness())
        for destination in ({'x':5,'z':8,'object_id':'a'},{'x':float('nan'),'z':8},
                            {'x':0,'z':8},{'object_id':'a','target_id':'b'}, {'special':'moon'},None):
            with self.subTest(destination=destination),self.assertRaises(ToolArgumentError):
                session.call('move_to',{'destination':destination})
            self.assertIsNone(session.inner.selected_action)
        session,_,_=bound(WorldHarness())
        names={d['name'] for d in session.definitions()}
        self.assertIn('move_to',names);self.assertNotIn('move_to_position',names);self.assertNotIn('search_actions',names)
        self.assertFalse(set(session.menu)&{'move_to','approach_hand','approach_object','put_down','eat_held',*EATING_RECIPES.values()})

    def test_held_eating_and_put_down_need_no_inspection_loop(self):
        for action,code in [('eat',14),('put_down',12)]:
            h=HeldHarness('mushroom');h.probe['held_actions'].update(edibility_version=1,can_eat=True)
            session,offer,_=bound(h)
            result=session.call('use_held_object',{'action':action})
            self.assertEqual(result['selected_object']['type'],'mushroom')
            self.assertFalse(result['executed'])
            offer.dispatch(session.finalize_reply({})['decision']);h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(code,7))

    def test_action_limit_is_shared_but_memory_can_follow(self):
        for first in ('move_to','do_action','use_held_object'):
            h=WorldHarness();h.probe['held_actions']={'version':6,'verified':True,'kind':'tree','target_generation':7}
            h.probe['candidates'].append('put_down');h.probe['hand_preparation_protocol']=1
            memory=ConversationMemory();session,_,_=bound(h,memory=memory)
            target=session.call('find_targets',{'kind':None,'limit':1,'action':'attack'})['action_targets'][0]['target_id']
            choices={'move_to':{'destination':{'special':'god_hand'}},
                     'do_action':{'action':'attack','target':target},'use_held_object':{'action':'put_down'}}
            self.assertEqual(session.call(first,choices[first])['status'],'selected_pending_final_reply')
            selected=session.finalize_reply({})['decision']
            for name,args in choices.items():
                self.assertEqual(session.call(name,args)['status'],'action_already_selected')
            session.call('update_memory',{'memory':'God calls the storage a warehouse.'})
            self.assertIn('warehouse',memory.notes)
            self.assertEqual(session.finalize_reply({})['decision'],selected)

    def test_nonbuilding_aliases_preserve_canonical_kinds(self):
        for alias,kind in [('bushes','tree'),('stones','rock'),('fungi','mushroom'),('balls','toy'),
                           ('grain heaps','food_pile'),('lumber','wood_pile'),('fishing spots','fishing_area')]:
            self.assertTrue(target_matches(alias,{'kind':kind}),alias)
        for kind in EATING_RECIPES:
            self.assertTrue(target_matches('food_source',{'kind':kind}))
            for other in EATING_RECIPES:
                self.assertEqual(target_matches(kind,{'kind':other}),kind==other)
        self.assertTrue(target_matches('cattle',{'kind':'animal','species':'cow'}))
        self.assertFalse(target_matches('cattle',{'kind':'animal'}))
        for alias,flag in [('actors',{'person':{'is_actor':True}}),('bodies',{'person':{'life_status':'dead'}}),('burning objects',{'on_fire':True})]:
            self.assertTrue(target_matches(alias,{'kind':'villager',**flag}))
            self.assertFalse(target_matches(alias,{'kind':'villager'}))

    def test_alias_names_consistent_across_search_inspection_and_selection(self):
        h=WorldHarness();entry=next(o for o in h.probe['world_actions']['offers'] if o['action']=='move_to')
        entry.update(kind='storage',object_handle=123456)
        session,_,_=bound(h)
        found=session.call('find_targets',{'kind':'warehouse','limit':1})['action_targets'][0]
        source=frame();source['observation']['surroundings']['known_objects']=[{**deepcopy(found),'kind':'storage'}]
        session.supplier=lambda:source;session.inner.supplier=lambda:source
        obj=session.call('inspect_object',{'object_id':found['object_id']})['object']
        selected=session.call('move_to',{'destination':{'target_id':found['target_id']}})['selected_object']
        for result in (found,obj,selected):
            self.assertEqual(result['type'],'storage');self.assertNotIn('name',result)
            self.assertEqual(result['object_id'],found['object_id'])


class SixRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_allows_six_dependent_tool_calls_then_one_final_response(self):
        for api in ('responses','chat_completions'):
            h=WorldHarness();memory=ConversationMemory();wires=[];made=[]
            offer=h.pending.factory(h.obs());data=offer.offer()
            request=action_request(data['candidates']);request.current['snapshot'].update(data)
            request=replace(request,messages=({'role':'system','content':'Synthetic creature prompt'},
                {'role':'user','content':json.dumps(request.current)}))
            def session_factory():
                s=DirectCreatureToolSession(frame,action_candidates=data['candidates'],memory=memory)
                made.append(s);return s
            def handler(req):
                wires.append(json.loads(req.content));n=len(wires)
                if n==1:tool,args='inspect_self',{}
                elif n==2:tool,args='inspect_surrounding',{'kind':'tree','offset':0,'limit':1}
                elif n==3:tool,args='find_targets',{'kind':'tree','limit':1,'action':'poo_on'}
                elif n==4:
                    target=made[0].trace[-1]['result']['action_targets'][0]['target_id']
                    tool,args='do_action',{'action':'poo_on','target':target}
                elif n==5:tool,args='update_memory',{'memory':'God calls trees shrubs.'}
                elif n==6:
                    tool,args='update_memory',{'memory':'Terminology: God calls trees shrubs.',
                        'memory_id':memory.snapshot()['entries'][0]['id']}
                else:return httpx.Response(200,json=envelope(api))
                return httpx.Response(200,json=call(api,tool,args,'call-'+str(n)))
            p=LLMProvider(LLMConfig('fixture',api=api),environ={'OPENAI_API_KEY':'synthetic'},transport=httpx.MockTransport(handler))
            p.tool_factory=session_factory
            try:
                result=await p.respond(request)
                self.assertEqual(p.tool_rounds,6);self.assertEqual(len(wires),7)
                self.assertEqual(wires[-1]['tool_choice'],'none')
                self.assertEqual(result['decision']['candidate'],'poo_on')
                self.assertEqual(memory.notes,'Terminology: God calls trees shrubs.')
                self.assertFalse(h.sent)
                messages=wires[0]['input' if api=='responses' else 'messages']
                self.assertIn('no discovery',messages[0]['content'])
                for name in ('move_to','use_held_object','inspect_surrounding','find_targets','update_memory'):
                    self.assertIn(name,messages[0]['content'])
            finally:await p.close()
