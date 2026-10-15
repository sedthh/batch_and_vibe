"""Synthetic metadata and request-scoped lookup; no game or provider calls."""
from copy import deepcopy
from dataclasses import asdict
import json
import struct
import unittest
from unittest.mock import patch

from bwv.object_details import details, public_object, lookup_order
from bwv.object_details_live import read_details
from bwv.creature_tools import CreatureToolSession
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm_context import summarize_context
from test_surroundings import NearbyMemory
from test_creature_tools import frame
from test_world_actions import WorldHarness
from test_action_tools import action_request


class ObjectDetailTests(unittest.TestCase):
    def test_advancing_tick_keeps_stable_known_object_scan(self):
        m=NearbyMemory();original=m.reader._head;calls=0
        def advancing():
            nonlocal calls
            calls+=1
            game,tick,*rest=original()
            return (game,tick+calls,*rest)
        m.reader._head=advancing
        self.assertEqual(len(m.sample().context.known_objects),1)
        self.assertEqual(calls,2)

    def test_building_resources_require_matching_getter_and_preserve_zero(self):
        m=NearbyMemory();obj=m.tree;m.typed(obj,b'.?AVAbode@@',4)
        table=m.reader.word(obj)
        m.put(table+0x98,0x404D30);m.put(table+0x11C,0x402600)
        m.put(table+0x880,0);m.put(table+0x884,0)
        m.bytes(obj+0x48,struct.pack('<f',.75));m.put(obj+0xBC,1200);m.put(obj+0xC0,0)
        data,errors=read_details(m.reader,obj,b'.?AVAbode@@')
        self.assertEqual(data,{'health_engine_units':.75,'food_engine_units':1200,'wood_engine_units':0})
        self.assertEqual(errors,0)
        m.put(table+0x98,123)
        data,_=read_details(m.reader,obj,b'.?AVAbode@@')
        self.assertNotIn('food_engine_units',data)
        self.assertEqual(data['health_engine_units'],.75)

    def test_building_construction_and_damage_are_independent(self):
        m=NearbyMemory();obj=m.tree;m.typed(obj,b'.?AVAbode@@',4)
        table=m.reader.word(obj)
        for offset,fn in ((0x98,0),(0x11C,0x402600),(0x880,0x4014F0),
                          (0x884,0x401500),(0x890,0x4016C0)):
            m.put(table+offset,fn)
        for built,health,flags,complete,damaged in ((1,.5,0,True,True),
                (.25,1,0,False,False),(1,1,2,False,False)):
            with self.subTest(built=built,health=health,flags=flags):
                m.bytes(obj+0x48,struct.pack('<f',health))
                m.bytes(obj+0x5C,struct.pack('<f',built));m.bytes(obj+0x58,bytes([flags]))
                data,errors=read_details(m.reader,obj,b'.?AVAbode@@')
                self.assertEqual(errors,0)
                self.assertEqual(data['construction_fraction'],built)
                self.assertEqual(data['construction_complete'],complete)
                self.assertEqual(data['repair_fraction'],health)
                self.assertEqual(data['damaged'],damaged)
        m.bytes(obj+0x5C,struct.pack('<f',float('nan')))
        data,errors=read_details(m.reader,obj,b'.?AVAbode@@')
        self.assertEqual(errors,1)
        self.assertNotIn('construction_fraction',data)
        self.assertEqual(data['repair_fraction'],1)
        m.put(table+0x880,0);m.put(table+0x884,0)
        data,errors=read_details(m.reader,obj,b'.?AVAbode@@')
        self.assertEqual(errors,0)
        self.assertEqual(data,{'health_engine_units':1})

    def test_worship_energy_and_food_are_local_and_optional(self):
        m=NearbyMemory();obj=m.tree;m.typed(obj,b'.?AVWorshipSite@@',4)
        table=m.reader.word(obj)
        for offset,fn in ((0x98,0x77BD80),(0x11C,0),(0x880,0),(0x884,0)):
            m.put(table+offset,fn)
        m.bytes(obj+0xF8,struct.pack('<ffIII',125,25,0,0,0));m.put(obj+0xB4,0)
        data,errors=read_details(m.reader,obj,b'.?AVWorshipSite@@')
        self.assertEqual(errors,0)
        self.assertEqual(data,{'prayer_energy_total_engine_units':125,
            'prayer_energy_available_engine_units':100,'food_engine_units':0})
        pile=0x6600000;m.typed(pile,b'.?AVPileFood@@',5)
        m.put(m.reader.word(pile)+0x94,0x66D390);m.put(pile+0x70,200);m.put(obj+0xB4,pile)
        m.put(obj+0x108,1)
        data,errors=read_details(m.reader,obj,b'.?AVWorshipSite@@')
        self.assertEqual(errors,0)
        self.assertEqual(data['food_engine_units'],200)
        self.assertEqual(data['prayer_energy_available_engine_units'],1_000_000)
        m.bytes(obj+0xF8,struct.pack('<ffIII',10,20,0,0,0))
        data,errors=read_details(m.reader,obj,b'.?AVWorshipSite@@')
        self.assertEqual(errors,1)
        self.assertEqual(data,{'food_engine_units':200})
        m.typed(pile,b'.?AVTree@@',5)
        data,errors=read_details(m.reader,obj,b'.?AVWorshipSite@@')
        self.assertEqual(errors,2)
        self.assertEqual(data,{})

    def test_invalid_optional_details_do_not_leak_to_model(self):
        self.assertEqual(details({'alignment':-1,'construction_complete':False,
            'construction_fraction':2,'repair_fraction':float('nan'),
            'prayer_energy_available_engine_units':-1,'damaged':'yes','secret':'omit'}),
            {'alignment':-1,'construction_complete':False})

    def test_field_fullness_and_food_distinguish_unripe_from_ripe(self):
        m=NearbyMemory();obj=m.tree;m.typed(obj,b'.?AVField@@',4)
        table=m.reader.word(obj);m.put(table+0x98,123);m.put(table+0x11C,123)
        info=0x6500000;m.put(obj+0x120,info);m.typed(info,b'.?AVGFieldInfo@@',5)
        m.bytes(info+0x128,struct.pack('<f',10));m.bytes(info+0x124,struct.pack('<f',.8))
        m.bytes(obj+0xCC,b'\x05');m.bytes(obj+0xDC,struct.pack('<f',700))
        for growth,ripe,food in ((.2,False,0),(.9,True,700)):
            m.bytes(obj+0xD0,struct.pack('<f',growth))
            result,errors=read_details(m.reader,obj,b'.?AVField@@')
            self.assertEqual(errors,0)
            self.assertEqual(result,{'field_fullness_fraction':.5,'field_ripe':ripe,'ripe_food_engine_units':food})

    def test_known_objects_outside_summary_are_inspectable_without_expanding_tick(self):
        m=NearbyMemory();m.bytes(m.tree+0x14,struct.pack('<iif',65536*20,0,0))
        context=m.sample().context
        self.assertEqual(context.nearby_known_objects,())
        self.assertEqual(len(context.known_objects),1)
        source=frame();source['observation']['surroundings']=asdict(context)
        session=CreatureToolSession(lambda:source)
        result=session.call('inspect_surrounding',{'kind':'tree','center':None,'limit':10})
        self.assertEqual(result['objects'][0]['distance_engine_units'],200)
        self.assertNotIn('lookup_handle',json.dumps(result))

    def test_all_objects_paginate_and_urgent_objects_stay_first_in_specific_search(self):
        source=frame();objects=[]
        for i,kind in enumerate(('tree','rock','food_pile','miracle_bubble','house','creature')):
            objects.append({'object_id':str(i),'kind':kind,'distance_engine_units':i+1,'lookup_handle':100+i})
        source['observation']['surroundings']={'available':True,'known_objects':objects}
        session=CreatureToolSession(lambda:source)
        args={'kind':None,'center':None,'offset':0,'limit':3}
        first=session.call('find_objects',args)
        second=session.call('find_objects',{**args,'offset':first['next_offset']})
        self.assertEqual([o['kind'] for o in first['objects']+second['objects']],
            ['creature','house','miracle_bubble','food_pile','tree','rock'])
        self.assertIsNone(second['next_offset'])
        self.assertNotIn('lookup_handle',json.dumps(session.trace))
        burn={**objects[0],'on_fire':True}
        self.assertLess(lookup_order(burn),lookup_order(objects[-1]))
        self.assertLess(lookup_order(burn,explicit_kind=True),lookup_order(objects[0],explicit_kind=True))
        self.assertLess(lookup_order(objects[0],explicit_kind=True),lookup_order({**objects[0],'distance_engine_units':20},explicit_kind=True))

    def test_species_search_and_shared_metadata_whitelist(self):
        source=frame();source['observation']['surroundings']['known_objects']=[{
            'object_id':'cow-1','kind':'animal','species':'cow','distance_engine_units':50,
            'details':{'health_engine_units':.5,'secret':'no'},'lookup_handle':123}]
        session=CreatureToolSession(lambda:source)
        found=session.call('find_objects',{'kind':'cows','center':None,'offset':0,'limit':10})
        self.assertEqual(found['objects'][0]['species'],'cow')
        inspected=session.call('inspect_object',{'object_id':'cow-1'})
        self.assertEqual(inspected['object']['details'],{'health_engine_units':.5})
        self.assertNotIn('secret',json.dumps(inspected));self.assertNotIn('lookup_handle',json.dumps(inspected))
        self.assertEqual(details({'food_engine_units':float('nan')}),{})

    def test_alignment_values_reach_every_compact_step_without_inventing_missing_values(self):
        raw={'creature':{'alignment':0,'god_alignment':-.6}}
        compact=summarize_context({'world':{'time_of_day':'night'}},raw)
        self.assertEqual(compact['self']['alignment'],0)
        self.assertEqual(compact['world']['god_alignment'],-.6)
        self.assertEqual(summarize_context({},{}),{})

    def test_lookup_extends_original_intention_with_one_validated_capability(self):
        h=WorldHarness();original=h.pending.source
        obj={'object_id':'remembered-field','kind':'field','distance_engine_units':600,'lookup_handle':987}
        def source():
            data=original();data['observation']['surroundings']['known_objects']=[obj]
            return data
        h.pending.source=source
        offer=h.pending.factory(h.obs());data=offer.offer()
        probe=deepcopy(h.probe)
        probe['world_actions']['offers'].append({'action':'move_to','token':999,'kind':'field',
            'distance_engine_units':600,'object_handle':987})
        calls=[]
        def lookup(pid,handle,**identity):
            calls.append(handle)
            self.assertEqual((pid,handle),(123,987));self.assertEqual(identity,{'expected_session':3,'expected_creature':42})
            return probe
        resolved=offer.resolve_object(obj['object_id'],'move_to',lookup)
        self.assertEqual(resolved[0]['object_id'],obj['object_id'])
        self.assertNotIn('lookup_handle',resolved[0])
        result=offer.dispatch({'candidate':'move_to','target':resolved[0]['target_id']})
        self.assertEqual(result['status'],'waiting_for_native')
        self.assertEqual(h.pending.pending['world_token'],999)
        self.assertEqual(calls,[987,987])  # Selected identity is re-resolved last.
        with self.assertRaisesRegex(ValueError,'expired'):offer.resolve_object(obj['object_id'],'move_to',lookup)

    def test_recipe_without_sampled_targets_is_searchable_but_cannot_act_blindly(self):
        h=WorldHarness()
        h.probe['target_lookup_protocol']=12
        h.probe['world_actions']['offers']=[]
        h.probe['world_actions']['available_recipes']=['eat_from_field']
        data=h.pending.factory(h.obs()).offer()
        session=DirectCreatureToolSession(frame,action_candidates=data['candidates'])
        request=action_request(data['candidates']);request.current['snapshot'].update(data);session.bind_request(request)
        self.assertIn('eat',session.menu)
        result=session.call('do_action',{'action':'eat','target':None,'miracle':None})
        self.assertEqual(result['status'],'invalid_target')
        found=session.call('find_targets',{'kind':'field','limit':3,'scope':None,'action':'eat','miracle':None,'center':None,'object_id':None})
        self.assertEqual(found['status'],'no_matching_known_target')

    def test_lookup_waits_for_game_thread_scan_and_rejects_stale_source(self):
        from bwv import action_probe
        state={'session':3,'source_age_ms':0,'action_probe':{'target_lookup_protocol':12,
            'conversation_actions':{'creature_handle':42},'epoch':3,'generation':1,
            'offered_at_uptime_ms':50,'lookup_observed_object':0}}
        complete=deepcopy(state);complete['action_probe']['lookup_observed_object']=987
        with patch.object(action_probe,'snapshot',side_effect=[state,state,complete]), \
                patch.object(action_probe,'submit',return_value=True) as submit, \
                patch.object(action_probe.time,'sleep'):
            result=action_probe.lookup_target(123,987,expected_session=3,expected_creature=42)
            self.assertEqual(result['lookup_observed_object'],987)
            packet=action_probe.COMMAND.unpack(submit.call_args.args[1])
            self.assertEqual((packet[1],packet[-2],packet[-1]),(12,0,987))
        state['source_age_ms']=2000
        with patch.object(action_probe,'snapshot',return_value=state), \
                patch.object(action_probe,'submit') as submit:
            with self.assertRaisesRegex(ValueError,'stale'):
                action_probe.lookup_target(123,987,expected_session=3,expected_creature=42)
            submit.assert_not_called()

    def test_tool_search_uses_resolver_for_known_object_missing_from_sample(self):
        h=WorldHarness();data=h.pending.factory(h.obs()).offer()
        source=frame();source['observation']['surroundings']['known_objects']=[{
            'object_id':'field-far','kind':'field','distance_engine_units':600}]
        called=[]
        def resolve(identity,action):
            called.append((identity,action))
            return [{'object_id':identity,'kind':'field','distance_engine_units':600,'target_id':'world-'+'f'*32}]
        session=DirectCreatureToolSession(lambda:source,action_candidates=data['candidates'],target_resolver=resolve)
        request=action_request(data['candidates']);request.current['snapshot'].update(data);session.bind_request(request)
        result=session.call('find_targets',{'kind':'field','limit':3,'scope':'distant','action':'move_to','miracle':None,'center':None,'object_id':'field-far'})
        self.assertEqual(called,[('field-far','move_to')])
        self.assertEqual(result['action_targets'][0]['object_id'],'field-far')
        selected=session.call('move_to',{'destination':{'target_id':result['action_targets'][0]['target_id']}})
        self.assertEqual(selected['status'],'selected_pending_final_reply')
