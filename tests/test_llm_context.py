import unittest
import hashlib
from bwv.llm_context import summarize_context, god_hand_context
from bwv.cadence import events, activity_tier, routine_signature


class CompactContextTests(unittest.TestCase):
    def test_hand_is_bound_fresh_at_any_distance_not_inferred_from_camera(self):
        identity=hashlib.sha256(b'session:3:11:22').hexdigest()[:24]
        source={'observation':{'session':'session:3','creature_id':identity,'observed_at':10},
                'diagnostics':{'action_probe':{'supported':True,'epoch':3,
                'conversation_actions':{'creature_handle':11,'mental_handle':22},
                'movement':{'verified':True,'distance_to_hand_engine_units':12.34}}}}
        self.assertEqual(god_hand_context(source,10),{'target_id':'god_hand','distance_engine_units':12.3})
        self.assertIsNone(god_hand_context(source,12))
        source['diagnostics']['action_probe']['movement']['distance_to_hand_engine_units']=80
        self.assertEqual(god_hand_context(source,10),{'target_id':'god_hand','distance_engine_units':80})
        source['diagnostics']['action_probe']['movement']['distance_to_hand_engine_units']=0
        self.assertEqual(god_hand_context(source,10),{'target_id':'god_hand','distance_engine_units':0})
        for invalid in (float('inf'),float('nan'),-1,True):
            source['diagnostics']['action_probe']['movement']['distance_to_hand_engine_units']=invalid
            self.assertIsNone(god_hand_context(source,10))
        source['diagnostics']['action_probe']['movement']['distance_to_hand_engine_units']=800
        source['observation']['creature_id']='other'
        self.assertIsNone(god_hand_context(source,10))

    def test_distant_hand_is_not_scheduled_as_nearby_activity(self):
        raw={'observation':{'surroundings':{'available':True}}}
        context={'god_hand':{'target_id':'god_hand','distance_engine_units':800}}
        self.assertEqual(activity_tier(raw,context)[0],'quiet')
        context['god_hand']['distance_engine_units']=20
        self.assertEqual(activity_tier(raw,context)[0],'active')

    def test_full_compact_projection_omits_unknowns_and_preserves_zero_needs(self):
        source={'body_engine_units':{'health':0,'energy':.4,'fatness':.5},
            'learned_miracles':['heal'],'learning_miracles':['water'],
            'instinct':{'native_plan':'CastWater'},'current_miracle_plan':'water',
            'surroundings':{'people_nearby':10,'homes_nearby':3,'food_piles_nearby':2,'trees_nearby':0},
            'feedback':[{'event_id':'e1','kind':'praise'}],
            'learning_events':[{'event_id':'e2','spell':'water'}],
            'action_results':[{'event_id':'e3','status':'completed'}],
            'candidates':['unsupported'],'private_debug':'not for model'}
        result=summarize_context(source,{'holding':{'hunger_desire':0}})
        self.assertEqual(result['self']['health_engine_units'],0)
        self.assertEqual(result['self']['hunger_motivation_engine_units'],0)
        self.assertNotIn('fatness_engine_units',result['self'])
        self.assertEqual(result['activity']['native_plan'],'cast water')
        self.assertEqual(result['surroundings'],{'people_nearby':10,'houses_nearby':3,'food_piles_nearby':2})
        self.assertNotIn('world',result)
        self.assertNotIn('private_debug',result)
        self.assertNotIn('candidates',result)
        self.assertIn(('learning_events','e2'),events(result))
        self.assertIn(('action_results','e3'),events(result))
        self.assertEqual(activity_tier({},result)[0],'active')
        result['events']={}
        self.assertEqual(activity_tier({},result)[0],'settlement')
        self.assertEqual(summarize_context({}),{})

    def test_held_object_is_reported_without_an_inspection_call(self):
        context={'held_object':{'kind':'toy','object_type':'ball'},
                 'happening':['You are holding a ball.']}
        self.assertEqual(summarize_context(context)['self']['held_object'],context['held_object'])
        self.assertNotIn('activity',summarize_context(context))
        raw={'holding':{'holding':True,'kind':'animal','object_type':'cow','item_id':'private-id'}}
        self.assertEqual(summarize_context({},raw)['self']['held_object'],
                         {'kind':'animal','object_type':'cow','name':'cow'})
        self.assertNotIn('self',summarize_context(context,{'holding':{'holding':False}}))

    def test_native_plan_changes_and_small_drift_do_not_flood_cadence(self):
        a=summarize_context({'body_engine_units':{'energy':.321},'instinct':{'native_plan':'LookAround'}})
        b=summarize_context({'body_engine_units':{'energy':.323},'instinct':{'native_plan':'Idle'}})
        self.assertEqual(routine_signature(a,'system'),routine_signature(b,'system'))
        self.assertNotIn('activity',summarize_context({'body_engine_units':{'energy':.4}}))
