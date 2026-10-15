import json
import struct
import unittest
from unittest.mock import patch

from bwv import surroundings_live as live
from bwv.prompt import preview_prompt
from test_control import playable
from test_training_live import MemoryFixture


class NearbyMemory(MemoryFixture):
    def typed(self,address,name,index):
        table=0x5100000+index*0x1000
        self.put(address,table);self.put(table-4,table+0x100)
        self.put(table+0x10c,table+0x200)
        self.bytes(table+0x208,name.ljust(64,b'\0'))

    def __init__(self):
        super().__init__()
        self.typed(self.mental+live.BELIEFS,b'.?AVCreatureBeliefs@@',0)
        self.bytes(self.creature+0x14,struct.pack('<iif',0,0,0))
        self.node=0x6100000;self.tree=0x6200000
        self.typed(self.node,b'.?AVCreatureBeliefSmall@@',3)
        self.typed(self.tree,b'.?AVTree@@',4)
        self.bytes(self.node+8,struct.pack('<II',0,0))
        self.put(self.node+0x30,self.tree)
        self.bytes(self.tree+0x14,struct.pack('<iif',65536,0,0))
        for i,offset in enumerate(live.LISTS):
            self.typed(self.mental+offset,b'.?AVCreatureBeliefList@@',i+1)
            self.bytes(self.mental+offset+8,struct.pack('<III',self.node,self.node,1)
                       if i==0 else struct.pack('<III',0,0,0))

    def sample(self,final=None):
        with patch.object(live,'snapshot',return_value=self.observer if final is None else final):
            return live.read_surroundings(self.reader,self.observer)

    def many_objects(self, count):
        """A growing island belief list with another creature at the end."""
        nodes = [0x7100000 + i * 0x80 for i in range(count)]
        objects = [0x8100000 + i * 0x200 for i in range(count)]
        self.bytes(self.mental+live.LISTS[0]+8,struct.pack('<III',nodes[0],nodes[-1],count))
        for index, (node, obj) in enumerate(zip(nodes, objects)):
            self.typed(node,b'.?AVCreatureBeliefSmall@@',3)
            self.typed(obj,b'.?AVCreature@@' if index==count-1 else b'.?AVTree@@',
                       6 if index==count-1 else 4)
            self.bytes(node+8,struct.pack('<II',nodes[index+1] if index+1<count else 0,
                                         nodes[index-1] if index else 0))
            self.put(node+0x30,obj)
            self.bytes(obj+0x14,struct.pack('<iif',65536,0,0))
        info=0x9100000
        self.put(objects[-1]+0x28,info)
        self.typed(info,b'.?AVCreatureInfo@@',7)
        self.bytes(info+0x18,b'Lion creature'.ljust(48,b'\0'))
        return nodes, objects


class SurroundingsTests(unittest.TestCase):
    def test_tutorial_bear_stays_visible_without_an_active_god_or_registered_slot(self):
        from dataclasses import asdict
        from test_hand_surroundings import HandSurroundingsTests
        from bwv.island import IslandContext
        from bwv.island_live import PLAYER_TYPE, PLAYER_NUMBER, NEUTRAL_PLAYER_INDEX, PLAYER_STRIDE, PLAYER_CREATURE
        from bwv.llm_context import summarize_context
        from bwv.direct_tools import DirectCreatureToolSession
        from test_creature_tools import frame
        for owner_kind in ('none','neutral','inactive_script_slot','active_script_slot'):
            memory,_=HandSurroundingsTests().memory(object_x=10,hand_x=10)
            memory.typed(memory.tree,b'.?AVCreature@@',4)
            info=0x6300000;memory.put(memory.tree+0x28,info)
            memory.typed(info,b'.?AVCreatureInfo@@',5)
            species='ogre' if owner_kind=='active_script_slot' else 'bear'
            memory.bytes(info+0x18,(b'Ogre creature' if species=='ogre' else b'Bear creature').ljust(48,b'\0'))
            slot=7 if owner_kind=='neutral' else 1 if owner_kind=='active_script_slot' else 4
            owner=memory.player+slot*PLAYER_STRIDE
            memory.typed(owner,b'.?AVGPlayer@@',6)
            memory.bytes(owner+PLAYER_NUMBER,bytes([slot]))
            memory.put(owner+PLAYER_TYPE,3 if slot==7 else 2 if slot==1 else 0)
            memory.put(owner+PLAYER_CREATURE,0)
            memory.bytes(memory.game+NEUTRAL_PLAYER_INDEX,b'\x07')
            memory.put(memory.tree+live.CREATURE_OWNER,0 if owner_kind=='none' else owner)
            context=memory.sample().context
            for objects in (context.nearby_known_objects,context.god_hand_objects):
                self.assertEqual(len(objects),1)
                self.assertEqual(objects[0].species,species)
                self.assertEqual(objects[0].kind,'creature')
            obs=playable(surroundings=context,island=IslandContext(True,(
                {'god_id':'god_0','is_player':True},{'god_id':'god_1','relation_to_player':'enemy'}),True))
            prompt=preview_prompt(obs,None)
            compact=summarize_context(json.loads(prompt.context_json),asdict(obs))
            self.assertEqual(compact['surroundings']['other_creatures_by_species'],{species:1})
            f=frame();f['observation']=asdict(obs);f['prompt']['context']=json.loads(prompt.context_json)
            result=DirectCreatureToolSession(lambda:f).call('inspect_object',{'object_id':context.nearby_known_objects[0].object_id})
            self.assertEqual(result['object']['type'],species+' creature')
            self.assertNotIn('owner_god',result)  # No invented deity or allegiance.
            if owner_kind=='active_script_slot':
                self.assertEqual(result['object']['owner_link'],'engine_only')
                self.assertIn('engine_owner_god',result)

    def test_explored_island_list_over_512_preserves_late_creature(self):
        memory=NearbyMemory()
        memory.many_objects(700)
        context=memory.sample().context
        self.assertTrue(context.available)
        self.assertEqual(context.scanned_beliefs,700)
        self.assertEqual(len(context.known_objects),700)
        creature,=[obj for obj in context.nearby_known_objects if obj.kind=='creature']
        self.assertEqual(creature.species,'lion')

    def test_export_budget_keeps_creatures_after_more_than_1024_trees(self):
        from test_hand_surroundings import HandSurroundingsTests
        memory,_=HandSurroundingsTests().memory(object_x=10,hand_x=10)
        memory.many_objects(1030)
        context=memory.sample().context
        self.assertEqual(context.scanned_beliefs,1030)
        for objects in (context.known_objects,context.nearby_known_objects,context.god_hand_objects):
            self.assertEqual(len(objects),1024)
            self.assertEqual(sum(obj.kind=='creature' for obj in objects),1)

    def test_large_scan_stops_when_time_budget_expires(self):
        memory=NearbyMemory()
        memory.many_objects(700)
        original=memory.reader.read
        reads=0
        def read(address,size):
            nonlocal reads
            reads+=1
            return original(address,size)
        memory.reader.read=read
        # Expire after the first 32 nodes, not after scanning the whole list.
        with patch.object(live.time,'monotonic',side_effect=[0,0,1]):
            with self.assertRaisesRegex(live.SurroundingsReadError,'freshness_limit'):
                memory.sample()
        self.assertLess(reads,2000)

    def test_other_creature_species_is_read_independently_and_summarized(self):
        from bwv.llm_context import summarize_context
        memory=NearbyMemory()
        memory.typed(memory.tree,b'.?AVCreature@@',4)
        info=0x6300000
        memory.put(memory.tree+0x28,info)
        memory.typed(info,b'.?AVCreatureInfo@@',5)
        for label,species in ((b'Lion creature','lion'),(b'Bear creature','bear'),(b'Unrecognized',None)):
            memory.bytes(info+0x18,label.ljust(48,b'\0'))
            context=memory.sample().context
            obj=context.nearby_known_objects[0]
            self.assertEqual(obj.kind,'creature')
            self.assertEqual(obj.species,species)
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
            self.assertEqual(compact['surroundings']['other_creatures_nearby'],1)
            self.assertEqual(compact['surroundings'].get('other_creatures_by_species'),
                             {species:1} if species else None)
        memory.put(memory.tree+0x28,0)
        self.assertIsNone(memory.sample().context.nearby_known_objects[0].species)

    def test_summary_radius_includes_village_buildings_but_omits_farther_ones(self):
        from bwv.surroundings import SUMMARY_RADIUS
        from bwv.llm_context import summarize_context
        for distance in (54,SUMMARY_RADIUS,SUMMARY_RADIUS+1):
            memory=NearbyMemory()
            memory.typed(memory.tree,b'.?AVAbode@@',4)
            memory.bytes(memory.tree+0x14,struct.pack('<iif',round(distance/live.POSITION_SCALE),0,0))
            context=memory.sample().context
            self.assertEqual(context.radius_engine_units,SUMMARY_RADIUS)
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
            self.assertEqual(compact.get('surroundings',{}).get('houses_nearby',0),
                             int(distance<=SUMMARY_RADIUS))

    def test_buildings_are_distinguished_in_compact_context(self):
        from bwv.building_types import BUILDING_TYPES
        from bwv.prompt import NEARBY_KEYS
        from bwv.llm_context import summarize_context
        for name, kind in BUILDING_TYPES.items():
            with self.subTest(kind=kind):
                memory = NearbyMemory()
                memory.typed(memory.tree,name,4)
                context = memory.sample().context
                self.assertEqual(context.nearby_known_objects[0].kind,kind)
                compact = summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
                self.assertEqual(compact['surroundings'],{NEARBY_KEYS[kind]:1})

    def test_only_verified_mobile_static_fence_subtypes_are_recognized(self):
        for subtype in (56,81,82,55,80,83):
            memory = NearbyMemory()
            memory.typed(memory.tree,b'.?AVMobileStatic@@',4)
            info=0x6300000
            memory.put(memory.tree+0x28,info)
            memory.typed(info,b'.?AVGMobileStaticInfo@@',5)
            memory.put(info+0x120,subtype)
            objects=memory.sample().context.nearby_known_objects
            self.assertEqual([o.kind for o in objects],['fence'] if subtype in (56,81,82) else ['object'])
            if subtype not in (56,81,82):self.assertEqual(objects[0].engine_class,'MobileStatic')
    def test_wildlife_and_fishing_locations_reach_compact_context(self):
        from bwv.animal_types import ANIMAL_TYPES
        from bwv.llm_context import summarize_context
        cases = [(name, 'animal', 'animals_nearby') for name in ANIMAL_TYPES]
        cases += [(b'.?AVFlock@@', 'animal_group', 'animal_groups_nearby'),
                  (b'.?AVFishFarm@@', 'fishing_area', 'fishing_areas_nearby')]
        for name, kind, key in cases:
            with self.subTest(runtime=name):
                memory = NearbyMemory()
                memory.typed(memory.tree, name, 4)
                context = memory.sample().context
                self.assertEqual(context.nearby_known_objects[0].kind, kind)
                legacy = json.loads(preview_prompt(playable(surroundings=context), None).context_json)
                compact = summarize_context(legacy)
                expected={key:1}
                if name in ANIMAL_TYPES:expected['animals_by_species']={ANIMAL_TYPES[name]:1}
                self.assertEqual(compact['surroundings'], expected)
                self.assertNotIn('fish_nearby', compact['surroundings'])
                # The same object in both native lists must not double-count.
                memory.bytes(memory.mental+live.LISTS[1]+8,
                             struct.pack('<III', memory.node, memory.node, 1))
                self.assertEqual(len(memory.sample().context.nearby_known_objects), 1)

    def test_creature_and_puzzle_animals_are_not_wildlife(self):
        from bwv.animal_types import ANIMAL_TYPES
        for name in (b'.?AVCreature@@', b'.?AVPuzzleCow@@', b'.?AVPieceCow@@'):
            self.assertNotIn(name, ANIMAL_TYPES)

    def test_known_nearby_object_uses_current_distance_without_claiming_visibility(self):
        memory=NearbyMemory()
        result=memory.sample().context
        obj,=result.nearby_known_objects
        self.assertEqual((obj.kind,obj.distance_engine_units),('tree',10))
        self.assertFalse(result.visibility_verified)
        prompt=preview_prompt(playable(surroundings=result),None)
        self.assertEqual(json.loads(prompt.context_json)['surroundings'], {'trees_nearby':1})
        self.assertNotIn(obj.object_id,prompt.context_json)

    def test_far_self_and_invalid_class_labels_are_omitted_from_summary(self):
        for variant in ('far','self','invalid_class'):
            memory=NearbyMemory()
            if variant=='far':memory.bytes(memory.tree+0x14,struct.pack('<iif',65536*13,0,0))
            elif variant=='self':memory.put(memory.node+0x30,memory.creature)
            else:memory.bytes(0x5104208,b'invalid/runtime/type'.ljust(64,b'\0'))
            with self.subTest(variant=variant):
                self.assertEqual(memory.sample().context.nearby_known_objects,())

    def test_unmapped_runtime_class_remains_inspectable(self):
        memory=NearbyMemory()
        memory.bytes(0x5104208,b'.?AVUnrecognized@@'.ljust(64,b'\0'))
        obj,=memory.sample().context.nearby_known_objects
        self.assertEqual((obj.kind,obj.engine_class),('object','Unrecognized'))

    def test_unreadable_unmapped_object_does_not_disable_surroundings(self):
        memory=NearbyMemory()
        memory.bytes(0x5104208,b'.?AVUnrecognized@@'.ljust(64,b'\0'))
        memory.bytes(memory.tree+0x14,struct.pack('<iif',65536,0,float('nan')))
        context=memory.sample().context
        self.assertTrue(context.available)
        self.assertEqual(context.nearby_known_objects,())
        self.assertEqual(context.detail_read_errors,1)

    def test_motion_does_not_restart_or_discard_a_surroundings_scan(self):
        memory=NearbyMemory()
        memory.typed(memory.tree,b'.?AVVillager@@',4)
        original=memory.read
        reads={memory.tree+0x14:0,memory.creature+0x14:0}
        def moving(address,size):
            if address in reads and size==12:
                reads[address]+=1
                return struct.pack('<iif',65536*reads[address],0,0)
            return original(address,size)
        memory.reader.read=moving
        sample=memory.sample()
        self.assertTrue(sample.context.available)
        self.assertEqual(sample.context.nearby_known_objects[0].kind,'villager')
        self.assertEqual(sample.context.nearby_known_objects[0].distance_engine_units,0)
        self.assertEqual(list(reads.values()),[1,1])

    def test_surroundings_diagnostics_are_specific_and_freshness_is_still_checked(self):
        memory=NearbyMemory()
        with patch.object(live.time,'monotonic',side_effect=[0,1]):
            with self.assertRaisesRegex(live.SurroundingsReadError,'freshness_limit'):
                memory.sample()
        self.assertEqual(live.SurroundingsReadError('synthetic private text').reason,'read_unavailable')

    def test_replaced_object_restarts_scan_instead_of_reusing_old_type(self):
        memory=NearbyMemory();calls=0
        def details(*args):
            nonlocal calls
            calls+=1
            if calls==1:memory.typed(memory.tree,b'.?AVVillager@@',5)
            return {},0
        with patch.object(live,'read_details',side_effect=details):
            context=memory.sample().context
        self.assertEqual(calls,2)
        self.assertEqual(context.nearby_known_objects[0].kind,'villager')

    def test_malformed_links_bounds_and_epoch_are_rejected(self):
        for variant in ('cycle','count','backlink','epoch'):
            memory=NearbyMemory();final=None
            if variant=='cycle':memory.put(memory.node+8,memory.node)
            elif variant=='count':memory.put(memory.mental+live.LISTS[0]+16,513)
            elif variant=='backlink':memory.put(memory.node+12,memory.node)
            elif variant=='epoch':final=dict(memory.observer,session=6)
            with self.subTest(variant=variant),self.assertRaises(ValueError):memory.sample(final)
