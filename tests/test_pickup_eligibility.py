from copy import deepcopy
import unittest
from bwv.action_probe import pickup_targets_available
from bwv.direct_tools import DirectCreatureToolSession
from bwv.world_actions import valid_target_metadata
from test_world_actions import WorldHarness
from test_action_tools import action_request
from test_creature_tools import frame


def session(h):
    offer=h.pending.factory(h.obs());data=offer.offer()
    request=action_request(data['candidates']);request.current['snapshot'].update(data)
    s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
    return offer,s


class PickupEligibilityTests(unittest.TestCase):
    def test_native_rejection_retains_size_reason(self):
        from bwv.action_feedback import action_result
        result=action_result({'result_current_creature':True,'requested_action':'pick_up',
            'request_at_uptime_ms':5,'result_phase':'rejected','outcome':'too_large_for_current_size'},'session')
        self.assertEqual(result['reason'],'too_large_for_current_size')
        self.assertEqual(result['status'],'rejected')

    def test_blocked_objects_remain_findable_with_reason_without_dispatch(self):
        for kind in ('tree','rock','villager','animal','food_pile','wood_pile','toy','mushroom','miracle_bubble'):
            h=WorldHarness()
            h.probe['pickup_action']['targets']=[{'token':91,'kind':kind,'distance_engine_units':12,
                'pickup_possible':False,'pickup_reason':'too_large_for_current_size'}]
            offer,s=session(h)
            target=s.call('find_targets',{'kind':kind,'action':'pick_up','limit':3})['unavailable_targets'][0]
            self.assertFalse(target['pickup_possible'])
            result=s.call('do_action',{'action':'pick_up','target':target['target_id']})
            self.assertEqual(result['reason'],'too_large_for_current_size')
            self.assertEqual(result['status'],'pickup_not_possible')
            self.assertIsNone(s.inner.selected_action)
            self.assertFalse(h.sent)
            self.assertFalse(pickup_targets_available(h.probe))

    def test_spell_names_reach_pickup_and_movement_search_without_inventing_unknown(self):
        h=WorldHarness()
        h.probe['pickup_action']['targets']=[
            {'token':91,'kind':'miracle_bubble','distance_engine_units':12,'object_handle':500,
             'bubble_spell':22,'pickup_possible':True},
            {'token':92,'kind':'miracle_bubble','distance_engine_units':15,'object_handle':600,
             'bubble_spell':10,'pickup_possible':False,'pickup_reason':'engine_pickup_not_allowed'},
            {'token':93,'kind':'miracle_bubble','distance_engine_units':16,'object_handle':700}]
        h.probe['world_actions']['offers']=[{'token':401,'action':'move_to','kind':'miracle_bubble',
            'object_handle':500,'distance_engine_units':12,'bubble_spell':22}]
        offer,s=session(h)
        for action in ('pick_up','move_to'):
            found=s.call('find_targets',{'kind':'water miracle','action':action,'limit':5})['action_targets']
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['miracle'],'water')
            self.assertTrue(found[0]['pickup_possible'])
        found=s.call('find_targets',{'kind':'healing bubble','action':'pick_up','limit':5})['unavailable_targets']
        self.assertFalse(found[0]['pickup_possible'])
        result=s.call('find_targets',{'kind':'miracle bubbles','action':'pick_up','limit':5})
        all_bubbles=result['action_targets']
        self.assertEqual(len(all_bubbles),2)
        self.assertEqual(result['unavailable_targets'][0]['miracle'],'heal')
        self.assertNotIn('miracle',all_bubbles[-1])
        water=s.call('find_targets',{'kind':'water','action':'pick_up','limit':1})['action_targets'][0]
        self.assertEqual(s.call('do_action',{'action':'pick_up','target':water['object_id']})['status'],
                         'selected_pending_final_reply')

    def test_invalid_metadata_and_missing_flag_are_distinct(self):
        for extra in ({'pickup_possible':1},{'pickup_possible':False,'pickup_reason':'invented'},
                      {'bubble_spell':True},{'bubble_spell':99}):
            self.assertFalse(valid_target_metadata({'kind':'miracle_bubble',**extra}))
        h=WorldHarness()
        original=deepcopy(h.probe['pickup_action']['targets'])
        self.assertEqual(len(pickup_targets_available(h.probe)),len(original))
        for t in h.probe['pickup_action']['targets']:t['pickup_possible']=False
        self.assertFalse(pickup_targets_available(h.probe))
        self.assertEqual(len(pickup_targets_available(h.probe,planning=True)),len(original))
