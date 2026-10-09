import json
import struct
import unittest
from dataclasses import asdict, replace

from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from bwv.direct_tools import DirectCreatureToolSession
from test_control import playable
from test_surroundings import NearbyMemory
from test_creature_tools import frame
from test_world_actions import WorldHarness
from test_action_tools import action_request


class HandSurroundingsTests(unittest.TestCase):
    def memory(self,object_x=500,hand_x=500):
        m=NearbyMemory();status=0x6800000
        m.typed(m.hand,b'.?AVGInterface@@',20)
        m.typed(status,b'.?AVGInterfaceStatus@@',21)
        m.put(m.hand+0x39C,status)
        m.bytes(status+0xC8,struct.pack('<fff',hand_x,0,10))
        m.put(m.game+0x59C8,1000);m.put(m.game+0x59C4,1000)
        m.bytes(m.tree+0x14,struct.pack('<iif',round(object_x*65536/10),65536,0))
        return m,status

    def test_separate_centers_and_overlapping_objects(self):
        for x,hx,in_creature,in_hand in ((500,500,False,True),(10,500,True,False),(10,20,True,True)):
            m,_=self.memory(x,hx);c=m.sample().context
            self.assertTrue(c.god_hand_available)
            self.assertEqual(bool(c.nearby_known_objects),in_creature)
            self.assertEqual(bool(c.god_hand_objects),in_hand)
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=c),None).context_json))
            self.assertEqual(bool(compact.get('surroundings')),in_creature)
            self.assertEqual(bool(compact.get('surroundings_near_god_hand')),in_hand and not in_creature)
            self.assertEqual(compact.get('surroundings_also_near_god_hand',False),in_hand and in_creature)
            if in_hand and in_creature:
                self.assertEqual(c.god_hand_objects[0].object_id,c.nearby_known_objects[0].object_id)

    def test_equal_counts_of_different_objects_are_not_merged(self):
        m,_=self.memory(10,20);c=m.sample().context
        c=replace(c,god_hand_objects=(replace(c.god_hand_objects[0],object_id='another-tree'),))
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=c),None).context_json))
        self.assertEqual(compact['surroundings'],compact['surroundings_near_god_hand'])
        self.assertNotIn('surroundings_also_near_god_hand',compact)

    def test_partial_overlap_keeps_both_views_and_raw_objects(self):
        m,_=self.memory(10,20);c=m.sample().context
        c=replace(c,god_hand_objects=c.god_hand_objects+(replace(c.god_hand_objects[0],object_id='extra-tree'),))
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=c),None).context_json))
        self.assertEqual(compact['surroundings']['trees_nearby'],1)
        self.assertEqual(compact['surroundings_near_god_hand']['trees_nearby'],2)
        self.assertNotIn('surroundings_also_near_god_hand',compact)
        self.assertEqual(len(c.god_hand_objects),2)

    def test_moving_hand_keeps_its_sampled_view(self):
        m,status=self.memory(10,20)
        original=m.reader.read;count=0
        def read(address,size):
            nonlocal count
            if address==status+0xC8:
                count+=1
                return struct.pack('<fff',20 if count==1 else 500,0,10)
            return original(address,size)
        m.reader.read=read
        c=m.sample().context
        self.assertTrue(c.god_hand_available)
        self.assertEqual(len(c.god_hand_objects),1)
        self.assertEqual(c.god_hand_objects[0].distance_to_god_hand_engine_units,10)
        self.assertEqual(count,1)

    def test_invalid_and_replaced_hand_preserve_creature_view(self):
        for fault in ('unreadable','invalid','replaced'):
            m,status=self.memory(10,20)
            if fault=='unreadable':m.put(m.hand+0x39C,0)
            elif fault=='invalid':m.bytes(status+0xC8,struct.pack('<fff',float('nan'),0,0))
            else:
                original=m.reader.read;count=0
                def read(address,size):
                    nonlocal count
                    if address==m.hand+0x39C:
                        count+=1
                        if count>1:return struct.pack('<I',0)
                    return original(address,size)
                m.reader.read=read
            c=m.sample().context
            self.assertEqual(len(c.nearby_known_objects),1)
            self.assertFalse(c.god_hand_available)
            self.assertEqual(c.god_hand_objects,())
            self.assertIsNone(c.nearby_known_objects[0].distance_to_god_hand_engine_units)
            self.assertEqual(c.hand_read_errors,1)

    def test_hand_inspection_can_see_far_from_creature_without_debris_ids(self):
        for name,count in ((b'.?AVTree@@',1),(b'.?AVFragment@@',0)):
            m,_=self.memory();m.typed(m.tree,name,4);c=m.sample().context
            def source():
                f=frame();f['observation']['surroundings']=asdict(c);return f
            s=DirectCreatureToolSession(source)
            result=s.call('inspect_surrounding',{'center':'god_hand','kind':None,'limit':3})
            self.assertEqual(result['center'],'god_hand')
            self.assertEqual(len(result['objects']),count)
            if count:
                self.assertEqual(result['objects'][0]['distance_to_god_hand'],0)
                self.assertGreater(result['objects'][0]['distance'],360)

    def session(self,h):
        offer=h.pending.factory(h.obs());data=offer.offer()
        request=action_request(data['candidates']);request.current['snapshot'].update(data)
        s=DirectCreatureToolSession(frame,action_candidates=data['candidates']);s.bind_request(request)
        return s,offer

    def test_hand_target_and_remembered_identity_get_fresh_action_id(self):
        h=WorldHarness()
        entry=next(e for e in h.probe['world_actions']['offers'] if e['action']=='move_to')
        entry.update(distance_engine_units=1500,distance_to_god_hand_engine_units=5,object_handle=0x6200000)
        s,_=self.session(h)
        args={'center':'god_hand','kind':'tree','limit':3,'action':'move_to'}
        target=s.call('inspect_surrounding',args)['action_targets'][0]
        self.assertEqual(target['distance'],1500)
        self.assertNotIn('object_handle',target)
        self.assertFalse(s.call('inspect_surrounding',{**args,'center':'creature'}).get('action_targets'))
        s,offer=self.session(h)
        fresh=s.call('find_targets',{'object_id':target['object_id'],'limit':3,'center':'god_hand'})['action_targets'][0]
        self.assertEqual(fresh['object_id'],target['object_id'])
        self.assertNotEqual(fresh['target_id'],target['target_id'])
        s.call('move_to',{'destination':{'target_id':fresh['target_id']}})
        offer.dispatch(s.finalize_reply({})['decision'])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        entry['object_handle']+=1
        s,_=self.session(h)
        missing=s.call('find_targets',{'object_id':target['object_id'],'limit':3})
        self.assertEqual(missing['status'],'no_matching_known_target')

    def test_hand_target_distance_omitted_does_not_fall_back_to_creature(self):
        h=WorldHarness();s,_=self.session(h)
        result=s.call('find_targets',{'center':'god_hand','kind':'tree','limit':3})
        self.assertNotIn('action_targets',result)
