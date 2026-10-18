from copy import deepcopy
from dataclasses import replace
import json
import unittest
from bwv.direct_tools import DirectCreatureToolSession
from bwv.expressions import EXPRESSION_GESTURES, expression_from_speech, display_speech, speech_expression
from test_world_actions import WorldHarness
from test_action_tools import action_request
from test_creature_tools import frame


class DirectToolsTests(unittest.TestCase):
    def test_other_species_follows_object_identity_in_inspection_and_search(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
        entry.update(kind='creature',object_handle=123456,distance_engine_units=20)
        data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        current=frame()
        s=DirectCreatureToolSession(lambda:current,action_candidates=data['candidates']);s.bind_request(request)
        target=s.call('find_targets',{'kind':'creature','limit':3,'scope':'known'})['action_targets'][0]
        object_id=target['object_id']
        current['observation']['surroundings']['nearby_known_objects']=[
            {'object_id':object_id,'kind':'creature','species':'lion','distance_engine_units':20,
             'physical_traits':{'fatness_engine_units':.4,'size_compared_to_you':'larger_than_you'}},
            {'object_id':'unrelated','kind':'creature','species':'bear','distance_engine_units':30}]
        inspected=s.call('find_targets',{'kind':'creature','limit':3,'action':'move_to', 'scope':'nearby'})
        self.assertEqual(inspected['action_targets'][0]['type'],'lion creature')
        self.assertEqual(inspected['action_targets'][0]['type'],'lion creature')
        self.assertEqual(inspected['action_targets'][0]['physical_traits']['fatness'],.4)
        found=s.call('find_targets',{'kind':'creature','limit':3,'scope':'known'})
        self.assertEqual(found['action_targets'][0]['type'],'lion creature')
        self.assertEqual(found['action_targets'][0]['physical_traits']['size_compared_to_you'],'larger_than_you')
        current['observation']['surroundings']['nearby_known_objects'].pop(0)
        unknown=s.call('find_targets',{'kind':'creature','limit':3,'scope':'known'})
        self.assertNotIn('species',unknown['action_targets'][0])
        self.assertNotIn('physical_traits',unknown['action_targets'][0])
        self.assertFalse(h.sent)

    def test_inspection_reaches_360_and_search_can_go_farther_without_acting(self):
        for distance in (200,360,361,600):
            with self.subTest(distance=distance):
                h=WorldHarness()
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
                entry.update(kind='house',distance_engine_units=distance)
                offer=h.pending.factory(h.obs());data=offer.offer()
                request=action_request(data['candidates']);request.current['snapshot'].update(data)
                s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
                near=s.call('find_targets',{'kind':'house','limit':3,'action':'move_to', 'scope':'nearby'})
                self.assertEqual(bool(near.get('action_targets')),distance<=360)
                far=s.call('find_targets',{'kind':'house','limit':3,'scope':'distant'})
                self.assertEqual(bool(far.get('action_targets')),distance>360)
                known=s.call('find_targets',{'kind':'house','limit':3,'scope':'known'})
                self.assertEqual(known['action_targets'][0]['distance'],distance)
                self.assertFalse(h.sent)

    def test_new_building_and_fence_kinds_support_search_and_exact_target_dispatch(self):
        from bwv.action_probe import COMMAND
        from bwv.world_actions import WORLD_KINDS
        for kind in WORLD_KINDS[16:]:
            with self.subTest(kind=kind):
                h=WorldHarness()
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='attack')
                entry.update(kind=kind,distance_engine_units=400)
                offer=h.pending.factory(h.obs());data=offer.offer()
                request=action_request(data['candidates']);request.current['snapshot'].update(data)
                s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
                found=s.call('find_targets',{'kind':kind,'limit':3,'scope':'distant','action':'attack'})
                self.assertEqual(found['action_targets'][0]['type'],kind.replace('_',' '))
                self.assertFalse(h.sent)
                s.call('do_action',{'action':'attack','target':found['action_targets'][0]['target_id']})
                offer.dispatch(s.finalize_reply({})['decision'])
                h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
                self.assertEqual(len(h.sent),1)
                self.assertEqual(COMMAND.unpack(h.sent[0])[-1],entry['token'])

    def test_wildlife_search_provides_a_usable_movement_target(self):
        from bwv.action_probe import COMMAND
        for kind in ('animal', 'animal_group', 'fishing_area'):
            with self.subTest(kind=kind):
                h=WorldHarness()
                entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
                entry.update(kind=kind, distance_engine_units=600)
                offer=h.pending.factory(h.obs());data=offer.offer()
                request=action_request(data['candidates']);request.current['snapshot'].update(data)
                s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
                result=s.call('find_targets',{'kind':kind,'limit':3,'scope':'distant'})
                target=result['action_targets'][0]
                self.assertEqual(target['type'],kind.replace('_',' '))
                self.assertFalse(h.sent)
                s.call('move_to',{'destination':{'target_id':target['target_id']}})
                offer.dispatch(s.finalize_reply({})['decision'])
                h.pending.advance(h.obs(),allowed=True);h.pending.advance(h.obs(),allowed=True)
                self.assertEqual(len(h.sent),1)
                self.assertEqual(COMMAND.unpack(h.sent[0])[-1],entry['token'])

    def test_followup_and_status_messages_keep_distinct_roles_and_chronology(self):
        _,_,session,request=self.session()
        inputs=[{'snapshot':{},'god_question':'Cast Water'},
                {'snapshot':{'trees_nearby':3}},
                {'snapshot':{},'god_question':'Then on trees'}]
        request=replace(request,current=inputs[-1],messages=tuple(
            {'role':'user','content':json.dumps(value)} for value in inputs))
        values=[json.loads(m['content']) for m in session.project_request(request).messages]
        self.assertEqual([v['message_type'] for v in values],
                         ['god_question','world_observation','god_question'])
        self.assertEqual(values[-1]['god_question'],'Then on trees')
        self.assertEqual(values[0]['god_question'],'Cast Water')

    def session(self):
        h=WorldHarness();offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        return h,offer,s,request

    def test_public_menu_has_no_search_gestures_or_individual_cast_tools(self):
        _,_,s,_=self.session()
        definitions={d['name']:d for d in s.definitions()}
        self.assertNotIn('search_actions',definitions)
        self.assertIn('find_targets',definitions)
        actions=definitions['do_action']['parameters']['properties']['action']['enum']
        self.assertIn('cast_miracle',actions)
        self.assertIn('attack',actions)
        self.assertFalse(set(actions)&set(EXPRESSION_GESTURES.values()))
        self.assertEqual([a for a in actions if a.startswith('cast_')],['cast_miracle'])
        with self.assertRaises(ValueError):s.call('search_actions',{'query':'all'})
        with self.assertRaises(ValueError):s.call('do_action',{'action':'laugh','target':None})

    def test_distant_field_search_is_read_only_then_move_dispatches_exact_id(self):
        from bwv.action_probe import COMMAND
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
        entry.update(kind='field',distance_engine_units=600)
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        near=s.call('find_targets',{'kind':'field','limit':10,'action':'move_to', 'scope':'nearby'})
        self.assertNotIn('action_targets',near)
        self.assertEqual(near['status'],'no_compatible_target_nearby')
        found=s.call('find_targets',{'kind':'field','limit':3,'scope':'distant'})
        self.assertEqual(found['action'],'move_to')
        self.assertEqual(found['action_targets'][0]['distance'],600)
        self.assertFalse(found['executed']);self.assertFalse(h.sent)
        self.assertIsNone(s.inner.selected_action)
        target=found['action_targets'][0]['target_id']
        self.assertEqual(s.call('do_action',{'action':'poo_on','target':target})['status'],'invalid_target')
        s.call('move_to',{'destination':{'target_id':target}})
        offer.dispatch(s.finalize_reply({})['decision']);h.pending.advance(h.obs(),allowed=True)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],entry['token'])

    def test_known_search_filters_scope_kind_and_spell_without_claiming_global_absence(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='cast_water_field')
        entry.update(distance_engine_units=600)
        data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        args={'kind':'field','limit':3,'scope':'distant','action':'cast_miracle','miracle':'water'}
        found=s.call('find_targets',args)
        self.assertEqual(len(found['action_targets']),1)
        self.assertEqual(found['miracle'],'water')
        missing=s.call('find_targets',{**args,'kind':'temple'})
        self.assertEqual(missing['status'],'no_matching_known_target')
        self.assertIn('not_entire_island',missing['coverage'])
        self.assertIn('does not establish absence',missing['reason'])
        with self.assertRaises(ValueError):s.call('find_targets',{**args,'action':'move_to'})
        with self.assertRaises(ValueError):s.call('find_targets',{**args,'scope':'anywhere'})

    def test_field_poo_remains_available_with_full_near_and_far_target_lists(self):
        from bwv.world_actions import KNOWN_TARGET_LIMIT
        h=WorldHarness()
        h.probe['world_actions']['offers']=[{'action':'poo_on','token':100+i,
            'kind':'field' if i==KNOWN_TARGET_LIMIT-1 else 'tree',
            'distance_engine_units':20+i} for i in range(KNOWN_TARGET_LIMIT)]
        data=h.pending.factory(h.obs()).offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        result=s.call('find_targets',{'kind':'field','limit':3,'action':'poo_on', 'scope':'nearby'})
        self.assertEqual(len(result['action_targets']),1)
        self.assertEqual(s.call('do_action',{'action':'poo_on',
            'target':result['action_targets'][0]['target_id']})['status'],'selected_pending_final_reply')

    def test_tool_contract_explains_search_movement_and_action_scoped_ids(self):
        from bwv.creature_tools import TOOL_CONTRACT
        from bwv.tool_docs import tool_documentation
        _,_,session,_=self.session()
        generated=tool_documentation(session.definitions())
        self.assertIn('### find_targets',generated)
        self.assertIn('### move_to',generated)
        self.assertIn('- target:',generated)
        self.assertIn('object_id directly',TOOL_CONTRACT)
        self.assertIn('Search is read-only',TOOL_CONTRACT)
        self.assertIn('Inspecting your own held item can select examination',TOOL_CONTRACT)

    def test_inspect_returns_only_compatible_spell_targets_and_selects_once(self):
        _,_,s,_=self.session()
        args={'kind':None,'limit':10,'action':'cast_miracle','miracle':'food'}
        target=s.call('find_targets',{**args, 'scope':'nearby'})['action_targets'][0]['target_id']
        bad=s.call('do_action',{'action':'cast_miracle','miracle':'wood','target':target})
        self.assertEqual(bad['status'],'invalid_target')
        result=s.call('do_action',{'action':'cast_miracle','miracle':'food','target':target})
        self.assertEqual(result['status'],'selected_pending_final_reply')
        self.assertFalse(result['executed'])
        self.assertEqual(s.finalize_reply({'answer':''})['decision'],{'candidate':'cast_food','target':target})
        self.assertEqual(s.call('do_action',{'action':'cast_miracle','miracle':'food','target':target})['status'],'action_already_selected')

    def test_self_alias_uses_only_offered_self_target(self):
        _,_,s,_=self.session()
        result=s.call('do_action',{'action':'cast_miracle','miracle':'creature_strong','target':'self'})
        self.assertEqual(result['status'],'selected_pending_final_reply')
        self.assertEqual(s.finalize_reply({})['decision']['candidate'],'cast_creature_strong')
        _,_,s,_=self.session()
        self.assertEqual(s.call('do_action',{'action':'cast_miracle','miracle':'food','target':'self'})['status'],'invalid_target')

    def test_cast_with_emoji_keeps_spell_and_discards_gesture(self):
        from bwv.action_probe import COMMAND
        for emoji in ('👋','🔍','👂','👃','🫳'):
            h,offer,session,_=self.session()
            target=session.call('find_targets',{'kind':None,'limit':10,
                'action':'cast_miracle','miracle':'food','scope':'nearby'})['action_targets'][0]['target_id']
            session.call('do_action',{'action':'cast_miracle','miracle':'food','target':target})
            reply=session.finalize_reply({'answer':'I will try. '+emoji})
            self.assertEqual(reply['decision'],{'candidate':'cast_food','target':target})
            offer.dispatch(reply['decision'])
            self.assertEqual(speech_expression(reply['answer'],reply['decision'],dispatcher=offer)['status'],
                             'skipped_action_conflict')
            self.assertEqual(display_speech(reply['answer']),'I will try.')
            for _ in range(3):h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1)
            self.assertEqual(COMMAND.unpack(h.sent[0])[-2],19)

    def test_bad_arguments_do_not_select_any_action(self):
        for args in ({'action':'cast_miracle','miracle':'made_up','target':'self'},
                     {'action':'attack','miracle':None,'target':12},
                     {'action':'attack','miracle':None,'target':None,'address':123}):
            _,_,s,_=self.session()
            with self.assertRaises(ValueError):s.call('do_action',args)
            self.assertIsNone(s.inner.selected_action)

    def test_projection_drops_internal_leases_in_current_and_history_without_mutation(self):
        _,_,s,request=self.session()
        request=replace(request,messages=({'role':'system','content':'Prompt'},
            {'role':'user','content':json.dumps(request.current)},
            {'role':'assistant','content':'Historical answer'},
            {'role':'user','content':json.dumps(request.current)}))
        original=deepcopy(request)
        projected=s.project_request(request)
        self.assertEqual(request,original)
        for message in projected.messages:
            if message['role']=='user':
                self.assertNotIn('action_targets',json.loads(message['content'])['snapshot'])
                self.assertNotIn('candidates',json.loads(message['content'])['snapshot'])
        self.assertNotIn('action_targets',projected.current['snapshot'])
        self.assertNotIn('gestures',projected.current['capabilities'])
        self.assertEqual(json.loads(projected.messages[-1]['content']),projected.current)

    def test_no_gesture_menu_without_a_bound_action_offer(self):
        s=DirectCreatureToolSession(frame,action_candidates=('wave',))
        request=action_request(('wave',),conversation_only=True)
        s.bind_request(request)
        self.assertNotIn('capabilities',s.project_request(request).current)

    def test_tool_prompt_removes_legacy_candidate_rules_and_lists_real_casts(self):
        from bwv.prompt import ACTION_CONTRACT
        from bwv.conversation import REPLY_FORMAT
        _,_,s,request=self.session()
        request=replace(request,messages=({'role':'system','content':'Role\n'+ACTION_CONTRACT+'\n'+REPLY_FORMAT},
            {'role':'user','content':json.dumps(request.current)}))
        projected=s.project_request(request)
        self.assertNotIn(ACTION_CONTRACT,projected.messages[0]['content'])
        self.assertNotIn(REPLY_FORMAT,projected.messages[0]['content'])
        self.assertIn('cast_miracle',projected.current['capabilities']['actions'])
        self.assertIn('food',projected.current['capabilities']['miracle_attempts'])
        self.assertNotIn('made_up',projected.current['capabilities']['miracle_attempts'])

    def test_offered_attempt_does_not_imply_learned_and_aliases_match_sensors(self):
        from bwv.direct_tools import observed_spell_name
        _,_,s,request=self.session()
        s.spells=('forest','water','megablast_pu2','spiritual_shield','creature_nice','food')
        request.current['snapshot']['self']={'miracles':{
            'learning_threshold_met':['water','shield','creature_compassion'],
            'partially_learned':['forest','explosion_power_up_2']}}
        result=s.project_request(request).current
        self.assertNotIn('castable_miracles',result)
        self.assertEqual(result['capabilities']['miracle_attempts'],{
            'forest':'still_learning_may_fail','water':'learning_threshold_met',
            'megablast_pu2':'still_learning_may_fail','spiritual_shield':'learning_threshold_met',
            'creature_nice':'learning_threshold_met','food':'learning_unverified'})
        self.assertEqual(observed_spell_name('fireball_pu1'),'fireball_power_up_1')
        self.assertEqual(observed_spell_name('heal_pu1'),'heal_power_up_1')

    def test_all_cues_are_hidden_and_world_cues_bind_self_without_extra_tool(self):
        for emoji,action in EXPRESSION_GESTURES.items():
            self.assertEqual(display_speech(emoji),'')
            self.assertEqual(expression_from_speech(emoji)['candidate'],action)
            h,offer,_,_=self.session()
            if action not in ('sit_down','look_at_player','howl','laugh','cry','show_realization','pray'):continue
            result=speech_expression(emoji,{'candidate':None},dispatcher=offer)
            self.assertEqual(result['status'],'waiting_for_native',action)
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1,action)
            h.pending.advance(h.obs(),allowed=True)
            self.assertEqual(len(h.sent),1)
