"""Public context contract, using synthetic readings and providers only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import unittest

from bwv.conversation import ConversationBroker, ConversationMemory
from bwv.control import ControlGate
from bwv.llm_context import summarize_context
from bwv.model_projection import model_turn, model_objects, model_request, model_snapshot
from bwv.object_aliases import target_matches
from bwv.planner import PlanningRequest
from test_control import playable
from test_conversation import Provider
from test_creature_tools import frame
from test_tool_interface import bound
from test_world_actions import WorldHarness


class ModelProjectionTests(unittest.TestCase):
    def test_bubble_metadata_survives_preview_tools_and_history_projection(self):
        from test_held_miracle import HeldMiracleHarness
        h=HeldMiracleHarness();h.probe['held_miracle']['bubble_spell']=23
        compact=summarize_context({},probe=h.probe)
        details=deepcopy(compact['self']['held_object']['miracle'])
        projected=model_snapshot(compact)
        self.assertEqual(projected['self']['held_object'],{'type':'miracle bubble','miracle':details})
        self.assertEqual(model_snapshot(projected),projected)
        _,session,request=h.session()
        request=replace(request,current={'snapshot':compact},messages=(
            {'role':'user','content':json.dumps({'snapshot':compact})},))
        for public in (model_request(request),session.project_request(request)):
            self.assertEqual(public.current['snapshot']['self']['held_object']['miracle'],details)
            self.assertEqual(json.loads(public.messages[-1]['content'])['snapshot'],public.current['snapshot'])
        for miracle in ('water',{'spell':'water','cast_success_guaranteed':False}):
            public=model_objects({'kind':'miracle_bubble','name':'water miracle bubble','miracle':miracle})
            self.assertNotIn('name',public)
            self.assertEqual(public['miracle'],miracle)

    def test_old_gesture_menus_are_removed_from_current_and_history(self):
        session,_,request=bound(WorldHarness(),frame())
        raw={'snapshot':{},'capabilities':{'gestures':{'👋':'wave'},'actions':['pick_up']}}
        original=deepcopy(raw)
        request=replace(request,current=raw,messages=tuple(
            {'role':'user','content':json.dumps(raw)} for _ in range(2)))
        for projected in (session.project_request(request),model_request(request)):
            self.assertNotIn('gestures',projected.current.get('capabilities',{}))
            for message in projected.messages:
                self.assertNotIn('gestures',json.loads(message['content']).get('capabilities',{}))
        self.assertEqual(model_turn(raw)['capabilities'],{'actions':['pick_up']})
        self.assertNotIn('capabilities',model_turn({'snapshot':{},'capabilities':{'gestures':{}}}))
        self.assertEqual(raw,original)

    def test_surroundings_drop_redundant_suffix_without_losing_creature_profiles(self):
        raw={'surroundings':{'people_nearby':3,'trees_nearby':8,'other_creatures_nearby':2,
                'other_creatures':[{'object_id':'lion','species':'lion','health_engine_units':.9}],
                'burning_objects_nearby':{'tree':1}},
             'surroundings_near_god_hand':{'houses_nearby':4,'miracle_bubbles_nearby':1},
             'towns_nearby':[{'town_id':'town'}], 'surroundings_also_near_god_hand':False}
        original=deepcopy(raw)
        expected={'surroundings':{'people':3,'trees':8,'other_creatures':2,
                'other_creature_details':[{'object_id':'lion','type':'lion creature','damage':10}],
                'burning_objects':{'tree':1}},
             'surroundings_near_god_hand':{'houses':4,'miracle_bubbles':1},
             'towns_nearby':[{'town_id':'town'}], 'surroundings_also_near_god_hand':False}
        self.assertEqual(model_snapshot(raw),expected)
        self.assertEqual(model_snapshot(expected),expected)
        self.assertEqual(raw,original)
        session,_,request=bound(WorldHarness(),frame())
        turn={'snapshot':raw}
        request=replace(request,current=turn,messages=(
            {'role':'user','content':json.dumps(turn)},
            {'role':'assistant','content':json.dumps({'answer':'Hello'})},
            {'role':'user','content':json.dumps(turn)}))
        for projected in (session.project_request(request),model_request(request)):
            self.assertEqual(projected.current['snapshot'],expected)
            for message in projected.messages:
                if message['role']=='user':
                    self.assertEqual(json.loads(message['content'])['snapshot'],expected)

    def test_timing_alignment_and_condition_meters_preserve_internal_readings(self):
        raw={'time':{'observed_at':'2030-01-01T23:59:59Z'},'conversation_only':False,
             'time_delta_since_last_message_seconds':45,
             'snapshot':{'world':{'god_alignment':0,'time_of_day':'night'},
                         'self':{'health_engine_units':0,'energy_engine_units':.6,'exhaustion_engine_units':.153,
                                 'hunger_motivation_engine_units':1.4},
                         'god_hand':{'position':{'x':100,'z':200},'distance_engine_units':5}}}
        original=deepcopy(raw);value=model_turn(raw)
        self.assertEqual(value['snapshot']['self'],{'damage':100,'hunger':40,'tiredness':15,'hunger_motivation':1.4})
        self.assertEqual(value['snapshot']['world'],{'time_of_day':'night'})
        self.assertEqual(value['snapshot']['god_hand']['alignment'],0)
        self.assertEqual(value['snapshot']['god_hand']['distance'],5)
        self.assertNotIn('time',value);self.assertNotIn('conversation_only',value)
        self.assertEqual(value['time_delta_since_last_message_seconds'],45)
        self.assertEqual(raw,original)
        self.assertEqual(model_objects({'hunger_desire':0}),{'hunger_motivation':0})
        self.assertEqual(model_turn(value),value)

    def test_condition_percentages_are_bounded_integers_and_missing_stays_unknown(self):
        for health, energy, exhaustion, expected in (
                (1, 1, 0, (0, 0, 0)), (0, 0, 1, (100, 100, 100)),
                (.876, .432, .789, (12, 57, 79)), (1.1, 1.2, 1.4, (0, 0, 100))):
            with self.subTest(readings=(health,energy,exhaustion)):
                result=model_turn({'snapshot':{'self':{'health_engine_units':health,
                    'energy_engine_units':energy,'exhaustion_engine_units':exhaustion}}})['snapshot']['self']
                self.assertEqual(tuple(result[key] for key in ('damage','hunger','tiredness')),expected)
                self.assertTrue(all(type(v) is int for v in result.values()))
        self.assertEqual(model_objects({'body_engine_units':{'health':None,'warmth':-.2}}),{'body':{'warmth':-.2}})
        self.assertEqual(model_turn({'snapshot':{}})['snapshot'],{})

    def test_inspection_and_history_use_the_same_meters_without_rescaling_drives_or_buildings(self):
        source=frame()
        source['observation']['experience']={'body':{'health':.88,'energy':.7,'exhaustion':.24,'strength':.6}}
        drives=[{'name':'hunger','value':1.4},{'name':'tiredness','value':.8}]
        source['observation']['holding']={'holding':False,'hunger_desire':1.4,'drives':drives}
        session,_,request=bound(WorldHarness(),source)
        inspected=session.call('inspect_self',{})
        self.assertEqual(inspected['body'],{'damage':12,'hunger':30,'tiredness':24,'strength':.6})
        self.assertEqual(inspected['holding_and_motivations']['drives'],drives)
        self.assertEqual(inspected['holding_and_motivations']['hunger_motivation'],1.4)
        raw={'snapshot':{'self':{'health_engine_units':.88,'energy_engine_units':.7,'exhaustion_engine_units':.24}}}
        request=replace(request,current=deepcopy(raw),messages=tuple(
            {'role':'user','content':json.dumps(raw)} for _ in range(2)))
        for projected in (session.project_request(request),model_request(request)):
            for message in projected.messages:
                self.assertEqual(json.loads(message['content'])['snapshot']['self'],
                                 {'damage':12,'hunger':30,'tiredness':24})
        other=model_objects({'kind':'creature','physical_traits':{'health_engine_units':.5,
            'energy_engine_units':.3,'exhaustion_engine_units':.6}})
        self.assertEqual(other['physical_traits'],{'damage':50,'hunger':70,'tiredness':60})
        for kind in ('house','tree','villager','animal'):
            self.assertEqual(model_objects({'kind':kind,'details':{'health_engine_units':.5}})['details'],{'health':.5})
        from bwv.llm_context import CONTEXT_CONTRACT
        self.assertIn('integers from 0 to 100',CONTEXT_CONTRACT)
        self.assertIn('Higher is worse',CONTEXT_CONTRACT)

    def test_object_types_and_event_kinds_are_distinct_and_actual_names_survive(self):
        self.assertEqual(model_objects({'object_id':'a','kind':'tree','name':'tree'}),
                         {'object_id':'a','type':'tree'})
        cow={'object_id':'b','kind':'animal','species':'cow','name':'cow'}
        self.assertEqual(model_objects(cow),{'object_id':'b','type':'cow'})
        creature={'object_id':'c','kind':'creature','species':'leopard','name':'Astra'}
        self.assertEqual(model_objects(creature),{'object_id':'c','type':'leopard creature','name':'Astra'})
        self.assertTrue(target_matches('leopard creature',creature))
        self.assertFalse(target_matches('leopard creature',{'kind':'animal','species':'leopard'}))
        result=model_objects({'other_creatures':[{'object_id':'c','species':'leopard'}],
                              'feedback':[{'kind':'punishment'}]})
        self.assertEqual(result['other_creatures'][0]['type'],'leopard creature')
        self.assertEqual(result['feedback'][0],{'kind':'punishment'})

    def test_native_activity_gets_its_actual_target_not_a_nearby_substitute(self):
        context={'instinct':{'native_plan':'WalkToObject',
                            'intended_target':{'kind':'storage','observed_object_id':'store'}}}
        raw={'surroundings':{'known_objects':[
            {'object_id':'other','kind':'storage','distance_engine_units':1,'position':{'x':10,'z':20}},
            {'object_id':'store','kind':'storage','distance_engine_units':25,'position':{'x':120,'z':240}}]}}
        value=model_turn({'snapshot':summarize_context(context,raw)})
        self.assertEqual(value['snapshot']['activity']['native_plan_target'],{
            'type':'storage','object_id':'store','distance':25,'position':{'x':120,'z':240}})
        raw['surroundings']['known_objects'].pop()
        target=model_turn({'snapshot':summarize_context(context,raw)})['snapshot']['activity']['native_plan_target']
        self.assertEqual(target,{'type':'storage','object_id':'store'})

    def test_schemas_prompts_tool_results_and_history_share_the_public_shape(self):
        source=frame();source['observation']['surroundings']['known_objects']=[{
            'object_id':'cow','kind':'animal','species':'cow','distance_engine_units':20,
            'details':{'health_engine_units':.8},'lookup_handle':123}]
        session,_,request=bound(WorldHarness(),source)
        definitions={d['name']:d for d in session.definitions()}
        for name in ('find_targets','find_objects','inspect_surrounding'):
            self.assertIn('type',definitions[name]['parameters']['properties'])
            self.assertNotIn('kind',definitions[name]['parameters']['properties'])
        found=session.call('find_objects',{'type':'cow','center':None,'offset':0,'limit':10})
        self.assertEqual(found['objects'][0],{'object_id':'cow','type':'cow','distance':20,'details':{'health':.8}})
        history={'answer':'On it.','action_target':{'object_id':'cow','kind':'animal','species':'cow','name':'cow'}}
        request=replace(request,messages=({'role':'system','content':'Example.'},
            {'role':'assistant','content':json.dumps(history)},
            {'role':'user','content':json.dumps(request.current)}))
        projected=session.project_request(request)
        self.assertIn('capabilities',projected.current)
        self.assertNotIn('available_actions',projected.current)
        self.assertNotIn('conversation_only',projected.current)
        self.assertEqual(json.loads(projected.messages[1]['content'])['action_target'],
                         {'object_id':'cow','type':'cow'})
        self.assertIn('- type:',projected.messages[0]['content'])
        self.assertEqual(json.loads(projected.messages[-1]['content']),projected.current)
        disabled=model_request(request)
        self.assertNotIn('conversation_only',disabled.current)
        self.assertEqual(json.loads(disabled.messages[-1]['content']),disabled.current)


class ElapsedObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_question_and_skipped_tick_use_last_sent_sample_and_reset_on_identity_change(self):
        now=[100];gate=ControlGate(resume_delay=0);gate.update(playable(now[0]),now[0])
        provider=Provider();broker=ConversationBroker(provider,gate,clock=lambda:now[0],memory=ConversationMemory())
        def tick():return broker.choose(PlanningRequest(gate.issue(now[0]),gate.observation.candidates,None))
        try:
            await tick()
            self.assertNotIn('time_delta_since_last_message_seconds',provider.requests[-1].current)
            builder=broker.prompt_builder
            broker.prompt_builder=lambda *a,**kw:('Example',{'snapshot':{}})
            now[0]=105;gate.update(playable(now[0]),now[0]);await tick()
            self.assertEqual(len(provider.requests),1)
            broker.prompt_builder=builder
            now[0]=110;gate.update(playable(now[0]),now[0]);await broker.ask('Hello')
            self.assertEqual(provider.requests[-1].current['time_delta_since_last_message_seconds'],10)
            now[0]=155;gate.update(playable(now[0]),now[0]);await tick()
            self.assertEqual(provider.requests[-1].current['time_delta_since_last_message_seconds'],45)
            now[0]=160;gate.update(replace(playable(now[0]),session='another'),now[0]);await broker.ask('Hello again')
            self.assertNotIn('time_delta_since_last_message_seconds',provider.requests[-1].current)
        finally:await broker.close()

    async def test_queued_question_uses_dispatch_observation_not_receipt_time(self):
        now=[100];gate=ControlGate(resume_delay=0);gate.update(playable(now[0]),now[0])
        release=asyncio.Event();provider=Provider(release)
        broker=ConversationBroker(provider,gate,clock=lambda:now[0],memory=ConversationMemory())
        try:
            first=asyncio.create_task(broker.ask('First'));await provider.entered.wait()
            now[0]=102;gate.update(playable(now[0]),now[0])
            second=asyncio.create_task(broker.ask('Second'));await asyncio.sleep(0)
            now[0]=112;gate.update(playable(now[0]),now[0]);release.set()
            await first;await second
            self.assertEqual(provider.requests[-1].current['time_delta_since_last_message_seconds'],12)
        finally:await broker.close()
