"""Engine eligibility and complete, bounded search without category assumptions."""
import unittest
from dataclasses import asdict
from bwv.action_probe import pickup_targets_available
from bwv.direct_tools import TARGET_LOOKUPS_PER_PAGE
from bwv.object_aliases import target_matches
from bwv.object_details import public_object
from bwv.world_actions import WORLD_KINDS
import test_object_types
from test_pickup_actions import PickupHarness
from test_world_actions import WorldHarness
from test_creature_tools import frame
from test_tool_interface import bound


class EngineTargetDiscoveryTests(unittest.TestCase):
    def test_engine_can_offer_pickup_for_every_world_type(self):
        h=PickupHarness()
        for kind in WORLD_KINDS[1:]:
            with self.subTest(kind=kind):
                h.probe['pickup_action']['targets']=[{'token':1,'kind':kind,
                    'distance_engine_units':20,'pickup_possible':True}]
                self.assertEqual(len(pickup_targets_available(h.probe)),1)
                h.probe['pickup_action']['targets'][0]['pickup_possible']=False
                self.assertEqual(pickup_targets_available(h.probe),())

    def test_unmapped_object_is_inspectable_without_claiming_a_semantic_type(self):
        ctx=test_object_types.ObjectTypesTests().sample(b'.?AVPuzzleCow@@')
        obj=ctx.nearby_known_objects[0]
        self.assertEqual(obj.kind,'object')
        self.assertEqual(obj.engine_class,'PuzzleCow')
        source=frame();source['observation']['surroundings']=asdict(ctx)
        session,_,_=bound(WorldHarness(),source)
        result=session.call('inspect_object',{'object_id':obj.object_id})['object']
        self.assertEqual(result['type'],'object')
        self.assertEqual(result['engine_class'],'PuzzleCow')
        self.assertNotIn('lookup_handle',result)
        self.assertTrue(target_matches('puzzle cow',public_object(asdict(obj))))
        self.assertFalse(target_matches('animal',public_object(asdict(obj))))
        self.assertNotIn('engine_class',public_object({'kind':'object','engine_class':'bad/path'}))

    def search(self,count,resolve,objects=None):
        h=WorldHarness();h.probe['world_actions']['offers']=[]
        h.probe['target_lookup_protocol']=12
        h.probe['world_actions']['available_recipes']=['move_to']
        source=frame();source['observation']['surroundings']={'available':True,'known_objects':objects or [
            {'object_id':str(i),'kind':'tree','distance_engine_units':i+1} for i in range(count)]}
        return bound(h,source,resolve)[0],h

    @staticmethod
    def target(identity,kind='tree'):
        return {'object_id':identity,'kind':kind,'distance_engine_units':20,'target_id':'world-'+identity.zfill(32)}

    def test_one_requested_result_checks_past_failed_candidates(self):
        checked=[]
        def resolve(identity,recipe):
            checked.append(identity)
            return [self.target(identity)] if identity=='2' else []
        session,h=self.search(4,resolve)
        found=session.call('find_targets',{'type':'tree','limit':1})
        self.assertEqual(checked,['0','1','2'])
        self.assertEqual(found['action_targets'][0]['object_id'],'2')
        self.assertEqual(found['next_offset'],3)
        self.assertFalse(h.sent)

    def test_work_budget_reports_partial_search_and_can_continue(self):
        checked=[];last=TARGET_LOOKUPS_PER_PAGE+2
        def resolve(identity,recipe):
            checked.append(identity)
            return [self.target(identity)] if identity==str(last) else []
        session,h=self.search(last+1,resolve)
        first=session.call('find_targets',{'type':'tree','limit':1})
        self.assertEqual(first['status'],'search_incomplete')
        self.assertFalse(first['search_complete'])
        self.assertEqual(len(checked),TARGET_LOOKUPS_PER_PAGE)
        second=session.call('find_targets',{'type':'tree','limit':1,'offset':first['next_offset']})
        self.assertEqual(second['action_targets'][0]['object_id'],str(last))
        self.assertTrue(second['search_complete'])
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len(set(checked)),last+1)
        self.assertFalse(h.sent)

    def test_search_failure_does_not_claim_absence(self):
        def resolve(*args):raise TimeoutError('Temporary lookup failure')
        session,_=self.search(2,resolve)
        result=session.call('find_targets',{'type':'tree','limit':1})
        self.assertEqual(result['status'],'target_lookup_failed')
        self.assertIn('Retry',result['reason'])

    def test_pickup_rejection_does_not_consume_the_requested_result(self):
        checked=[]
        def resolve(identity,recipe):
            checked.append(identity)
            return [{**self.target(identity),'pickup_possible':identity=='1',
                     **({'pickup_reason':'too_heavy_to_lift'} if identity=='0' else {})}]
        session,_=self.search(3,resolve)
        found=session.call('find_targets',{'type':'tree','action':'pick_up','limit':1})
        self.assertEqual(checked,['0','1'])
        self.assertEqual(found['action_targets'][0]['object_id'],'1')
        self.assertEqual(found['unavailable_targets'][0]['pickup_reason'],'too_heavy_to_lift')
        self.assertEqual(found['next_offset'],2)

    def test_important_objects_order_before_generic_objects_and_trees(self):
        objects=[{'object_id':kind,'kind':kind,'distance_engine_units':distance}
                 for kind,distance in [('tree',1),('rock',2),('object',3),('food_pile',4),
                     ('miracle_bubble',5),('storage',6),('villager',7),('creature',8),
                     ('mushroom',40),('scaffolding',1),('toy',100)]]
        objects.append({'object_id':'burning','kind':'tree','distance_engine_units':30,'on_fire':True})
        objects.append({'object_id':'dead','kind':'villager','distance_engine_units':40,'person':{'life_status':'dead'}})
        looked=[]
        def resolve(identity,recipe):
            looked.append(identity)
            obj=next(o for o in objects if o['object_id']==identity)
            return [{**self.target(str(len(looked)),obj['kind']),**obj}]
        session,_=self.search(0,resolve,objects)
        result=session.call('find_targets',{'type':None,'limit':10})
        self.assertEqual([o['object_id'] for o in result['action_targets']],
            ['toy','burning','creature','dead','villager','storage','miracle_bubble','food_pile','mushroom','scaffolding'])
        following=session.call('find_targets',{'type':None,'limit':10,'offset':result['next_offset']})
        self.assertEqual([o['object_id'] for o in following['action_targets']],['object','tree','rock'])

    def test_existing_offer_does_not_hide_more_important_unsampled_object(self):
        checked=[]
        def resolve(identity,recipe):
            checked.append(identity)
            return [self.target(identity,'creature')]
        session,_=self.search(0,resolve,[{'object_id':'creature','kind':'creature','distance_engine_units':90}])
        session.inner.action_targets['move_to']={'objects':[self.target('near-tree')]}
        result=session.call('find_targets',{'type':None,'limit':1})
        self.assertEqual(result['action_targets'][0]['object_id'],'creature')
        self.assertEqual(checked,['creature'])
