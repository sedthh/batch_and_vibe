from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from bwv.conversation import ConversationMemory
from bwv.object_details import known_objects
from bwv.qa import Inspector
from bwv.spells import SPELLS
from bwv.target_metadata import describe_target
from bwv.tool_docs import tool_documentation
from bwv.action_probe import COMMAND
from test_action_recovery import RecoveryHarness
from test_control import playable
from test_creature_tools import frame
from test_tool_interface import bound
from test_world_actions import WorldHarness


class ConsolidationTests(unittest.TestCase):
    def test_action_accepts_object_identity_without_extra_lookup(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='attack')
        entry['object_handle']=12345
        session,offer,_=bound(h,resolver=lambda *a:self.fail('Already offered; no extra engine lookup'))
        target=session._targets('attack')[0]
        result=session.call('do_action',{'action':'attack','target':target['object_id']})
        self.assertEqual(result['selected_object']['object_id'],target['object_id'])
        self.assertFalse(h.sent)
        offer.dispatch(session.finalize_reply({})['decision']);h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_unoffered_identity_resolves_only_relevant_eating_recipe(self):
        source=frame();source['observation']['surroundings']={'known_objects':[
            {'object_id':'store','kind':'storage','distance_engine_units':40}]}
        calls=[]
        def resolve(identity,recipe):
            calls.append((identity,recipe))
            return [{'object_id':identity,'kind':'storage','target_id':'world-'+'a'*32,'distance_engine_units':40}]
        session,_,_=bound(WorldHarness(),source,resolve)
        selected=session.call('do_action',{'action':'eat','target':'store'})
        self.assertEqual(selected['selected_object']['type'],'storage')
        self.assertEqual(calls,[('store','eat_from_storage')])

    def test_lookup_reuses_all_recipes_but_dispatch_refreshes_selected_object(self):
        h=WorldHarness();source=h.pending.source
        obj={'object_id':'field','kind':'field','distance_engine_units':20,'lookup_handle':987}
        def read():
            value=source();value['observation']['surroundings']['known_objects']=[obj];return value
        h.pending.source=read
        offer=h.pending.factory(h.obs());offer.offer()
        calls=[];probe=deepcopy(h.probe)
        probe['world_actions']['offers']=[
            {'action':action,'token':900+i,'kind':'field','object_handle':987,'distance_engine_units':20}
            for i,action in enumerate(('move_to','poo_on'))]
        def lookup(*args,**kwargs):calls.append(1);return probe
        offer.resolve_object('field','move_to',lookup)
        selected=offer.resolve_object('field','poo_on',lookup)
        self.assertEqual(len(calls),1)
        offer.dispatch({'candidate':'poo_on','target':selected[0]['target_id']})
        self.assertEqual(len(calls),2)

    def test_vanished_inspected_target_can_retry_another_before_dispatch(self):
        h=RecoveryHarness();source=h.pending.source;visible=True
        obj={'object_id':'tree-a','kind':'tree','distance_engine_units':20,'lookup_handle':987}
        def read():
            value=source();value['observation']['surroundings']['known_objects']=[obj] if visible else [];return value
        h.pending.source=read
        offer=h.pending.factory(h.obs());offer.offer()
        probe=deepcopy(h.probe)
        probe['world_actions']['offers'].append({'action':'attack','token':990,'kind':'tree',
            'object_handle':987,'distance_engine_units':20})
        selected=offer.resolve_object('tree-a','attack',lambda *a,**kw:probe)
        visible=False
        queued=offer.dispatch({'candidate':'attack','target':selected[0]['target_id']})
        self.assertEqual(queued['status'],'waiting_for_native')
        self.assertIn('no longer known',queued['target_resolution_note'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(h.pending.last['retarget_count'],1)

    def test_recipes_share_metadata_only_until_next_tool_call(self):
        h=WorldHarness();source=frame()
        session,_,_=bound(h,source)
        with patch('bwv.direct_tools.known_objects',wraps=known_objects) as scan:
            session.call('find_targets',{'kind':'tree','limit':10,'action':'cast_miracle','miracle':'water'})
            self.assertEqual(scan.call_count,2) # one metadata index, one search list
        self.assertLessEqual(len(session._object_metadata),len(known_objects(source['observation']['surroundings'])))

    def test_generated_reference_tracks_actual_schema_options_and_bounds(self):
        session,_,request=bound(WorldHarness())
        definitions=session.definitions()
        doc=tool_documentation(definitions)
        for definition in definitions:
            self.assertIn('### '+definition['name'],doc)
            self.assertIn(definition['description'],doc)
        changed=deepcopy(definitions)
        inspect=next(d for d in changed if d['name']=='find_objects')
        inspect['parameters']['properties']['limit']['maximum']=7
        self.assertIn('<=7',tool_documentation(changed))
        projected=session.project_request(replace(request,messages=({'role':'system','content':'Synthetic game.'},)))
        self.assertIn(doc,projected.messages[0]['content'])

    def test_retry_water_bubble_uses_another_water_not_closer_fireball(self):
        h=RecoveryHarness()
        water=next(k for k,v in SPELLS.items() if v=='water')
        fire=next(k for k,v in SPELLS.items() if v=='fireball')
        original={'token':61,'kind':'miracle_bubble','object_handle':10001,
                  'distance_engine_units':10,'bubble_spell':water}
        h.probe['pickup_action']['targets']=[original]
        h.enqueue_pickup(0)
        h.probe['pickup_action']['targets']=[
            {**original,'token':62,'object_handle':10002,'distance_engine_units':1,'bubble_spell':fire},
            {**original,'token':63,'object_handle':10003,'distance_engine_units':15}]
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],63)
        result=h.pending.snapshot()
        self.assertEqual(result['target']['miracle'],'water')
        self.assertIn('object_id',result['target'])
        self.assertEqual(result['retarget_count'],1)

    def test_explicit_pickup_rejection_retries_an_eligible_alternative(self):
        h=RecoveryHarness()
        h.probe['pickup_action']['targets']=[{'token':61,'kind':'tree','distance_engine_units':10},
            {'token':62,'kind':'tree','distance_engine_units':15,'pickup_possible':True}]
        h.enqueue_pickup(0);h.pending.advance(h.obs(),allowed=True)
        h.probe.update(requested_action='pick_up',request_at_uptime_ms=41,
            result_current_creature=True,result_phase='rejected',outcome='too_heavy_to_lift')
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual([COMMAND.unpack(p)[-1] for p in h.sent],[61,62])

    def test_history_keeps_target_meaning_and_retarget_without_dead_leases(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'memory.json';memory=ConversationMemory(path=path)
            target={'object_id':'bubble-a','kind':'miracle_bubble','miracle':'water',
                    'name':'water miracle bubble','target_id':'pickup-lease','lookup_handle':123}
            current={'god_question':'Fetch water','snapshot':{'surroundings':{'trees_nearby':8},
                'action_targets':{'pick_up':{'objects':[target]}},'candidates':['pick_up']}}
            memory.record(current,'',{'candidate':'pick_up','target':'pickup-lease'})
            memory.record_dispatch({'intent_id':'test','status':'waiting_for_native','target':target})
            memory.update_dispatch({'intent_id':'test','status':'queued','target':{**target,'object_id':'bubble-b'}})
            reloaded=ConversationMemory(path=path).snapshot()['recent_pairs'][0]
            self.assertEqual(reloaded['action_target']['object_id'],'bubble-b')
            self.assertEqual(reloaded['action_target']['miracle'],'water')
            self.assertEqual(reloaded['action_target']['type'],'miracle bubble')
            self.assertNotIn('kind',reloaded['action_target']);self.assertNotIn('name',reloaded['action_target'])
            text=json.dumps(reloaded)
            self.assertNotIn('pickup-lease',text);self.assertNotIn('lookup_handle',text)
            self.assertNotIn('action_targets',text)
            self.assertEqual(reloaded['input']['god_question'],'Fetch water')

    def test_dense_debug_history_has_byte_bound_and_latest_survives(self):
        inspector=Inspector(limit=120,history_bytes=130000)
        for i in range(50):
            inspector.update(playable(i),{'action_probe':{'payload':'x'*4000}},None,None,i)
        self.assertLessEqual(inspector.retained_bytes,130000)
        self.assertLess(len(inspector.frames),50)
        oldest=inspector.payload()['history'][-1]['id']
        self.assertEqual(inspector.payload(oldest)['current']['diagnostics']['action_probe']['payload'],'x'*4000)
        inspector.set_history_limit(0)
        self.assertEqual(inspector.retained_bytes,0)
        self.assertIsNotNone(inspector.payload()['current'])
        huge=Inspector(history_bytes=1)
        huge.update(playable(0),{},None,None,0)
        self.assertEqual(huge.payload()['history'],[])
        self.assertIsNotNone(huge.payload()['current'])


if __name__=='__main__':unittest.main()
