"""Synthetic body metadata; no game process, executable or provider calls."""
from dataclasses import asdict, replace
import json
import math
import struct
import unittest

from bwv.nearby_creatures import creature_summary, physical_traits, summary_profiles
from bwv.nearby_creatures_live import STATS, read_body_radius, read_physical_traits
from bwv.llm_context import summarize_context
from bwv.prompt import preview_prompt
from test_control import playable
from test_surroundings import NearbyMemory


def body(memory, creature, physical, controller, index, radius):
    memory.put(creature+0x160,physical)
    memory.typed(physical,b'.?AVCreaturePhysical@@',index)
    table=memory.reader.word(creature)
    memory.put(table+0x64,0x477F40)
    memory.put(physical+0x58,controller)
    memory.put(controller+0x4834,creature)
    memory.bytes(controller+0x5228,struct.pack('<f',radius))
    memory.bytes(creature+0x48,struct.pack('<f',.75))
    for name,offset in STATS.items():
        memory.bytes(physical+offset,struct.pack('<f',-.25 if name=='warmth' else .5))


def fixture():
    m=NearbyMemory()
    m.typed(m.tree,b'.?AVCreature@@',10)
    m.put(m.tree+0x1070,0)  # No engine owner for this synthetic creature.
    info=0x6300000
    m.put(m.tree+0x28,info);m.typed(info,b'.?AVCreatureInfo@@',11)
    m.bytes(info+0x18,b'Lion creature'.ljust(48,b'\0'))
    body(m,m.creature,0x6400000,0x6500000,12,2)
    body(m,m.tree,0x6600000,0x6700000,13,4)
    return m


class NearbyCreatureTests(unittest.TestCase):
    def test_other_creature_alignment_reaches_inspection_independently(self):
        from bwv.creature_tools import CreatureToolSession
        from test_creature_tools import frame
        m=fixture();alignment=0x6800000
        m.typed(alignment,b'.?AVGAlignment@@',14);m.put(m.tree+0x168,alignment)
        m.bytes(alignment+8,struct.pack('<f',-.5))
        state=frame();state['observation']['surroundings']=asdict(m.sample().context)
        obj=state['observation']['surroundings']['nearby_known_objects'][0]
        result=CreatureToolSession(lambda:state).call('inspect_object',{'object_id':obj['object_id']})
        self.assertEqual(result['object']['details']['alignment'],-.5)
        self.assertEqual(result['object']['physical_traits']['size_compared_to_you'],'larger_than_you')
        self.assertNotIn('controlling_god_id',result['object'])
        m.bytes(alignment+8,struct.pack('<f',float('nan')))
        obj=m.sample().context.nearby_known_objects[0]
        self.assertNotIn('alignment',obj.details or {})
        self.assertEqual(obj.physical_traits['size_compared_to_you'],'larger_than_you')

    def test_physical_details_and_compact_profile_reach_context(self):
        m=fixture()
        context=m.sample().context
        obj=context.nearby_known_objects[0]
        traits=obj.physical_traits
        self.assertEqual(traits['size_compared_to_you'],'larger_than_you')
        self.assertEqual(traits['body_radius_engine_units'],4)
        self.assertEqual(traits['fatness_engine_units'],.5)
        self.assertEqual(traits['warmth_engine_units'],-.25)
        self.assertNotIn('strength',traits)
        self.assertEqual(context.creature_read_errors,0)
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
        profile=compact['surroundings']['other_creatures'][0]
        self.assertEqual(profile['object_id'],obj.object_id)
        self.assertEqual(profile['species'],'lion')
        self.assertEqual(profile['health_engine_units'],.75)
        self.assertEqual(profile['size_compared_to_you'],'larger_than_you')
        self.assertNotIn('fatness_engine_units',profile)
        at_hand=replace(context,nearby_known_objects=(),god_hand_available=True,
                        god_hand_objects=(obj,))
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=at_hand),None).context_json))
        self.assertNotIn('surroundings',compact)
        self.assertEqual(compact['surroundings_near_god_hand']['other_creatures'][0],profile)

    def test_invalid_and_torn_fields_do_not_hide_other_valid_traits(self):
        m=fixture()
        m.bytes(0x6600000+STATS['energy'],struct.pack('<f',math.nan))
        m.put(0x6700000+0x4834,m.creature)
        traits,errors=read_physical_traits(m.reader,m.tree,2)
        self.assertEqual(errors,2)
        self.assertNotIn('energy_engine_units',traits)
        self.assertNotIn('body_radius_engine_units',traits)
        self.assertNotIn('size_compared_to_you',traits)
        self.assertEqual(traits['health_engine_units'],.75)
        original=m.reader.read;calls=0
        def read(address,size):
            nonlocal calls
            if address==m.tree+0x48:
                calls+=1
                return struct.pack('<f',.25 if calls%2 else .75)
            return original(address,size)
        m.reader.read=read
        traits,errors=read_physical_traits(m.reader,m.tree)
        self.assertNotIn('health_engine_units',traits)
        self.assertEqual(errors,3)

    def test_size_requires_verified_getter_and_coherent_controller(self):
        for variant in ('getter','controller','radius','pointer_change'):
            m=fixture()
            if variant=='getter':m.put(m.reader.word(m.tree)+0x64,123)
            elif variant=='controller':m.put(0x6700000+0x4834,m.creature)
            elif variant=='radius':m.bytes(0x6700000+0x5228,struct.pack('<f',math.inf))
            else:
                original=m.reader.read;calls=0
                def read(address,size):
                    nonlocal calls
                    if address==m.tree+0x160:
                        calls+=1
                        if calls>1:return struct.pack('<I',0)
                    return original(address,size)
                m.reader.read=read
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                read_body_radius(m.reader,m.tree)

    def test_size_comparison_and_missing_reference(self):
        m=fixture()
        for own,expected in ((8,'smaller_than_you'),(4,'similar_size_to_you'),(2,'larger_than_you'),(None,None)):
            traits,_=read_physical_traits(m.reader,m.tree,own)
            self.assertEqual(traits.get('size_compared_to_you'),expected)

    def test_metadata_failure_retains_generic_creature_with_diagnostics(self):
        m=fixture();m.put(m.tree+0x160,0)
        context=m.sample().context
        obj=context.nearby_known_objects[0]
        self.assertEqual(obj.species,'lion')
        self.assertEqual(obj.physical_traits,{'health_engine_units':.75})
        self.assertGreater(context.creature_read_errors,0)

    def test_summaries_bounded_and_public_fields_filtered(self):
        m=fixture();obj=asdict(m.sample().context.nearby_known_objects[0])
        summary=creature_summary([obj]*20)
        self.assertEqual(len(summary),4)
        summary[0].update(secret='no',strength=99,energy_engine_units=1)
        clean=summary_profiles(summary)
        self.assertNotIn('secret',clean[0]);self.assertNotIn('energy_engine_units',clean[0])
        self.assertEqual(physical_traits({'health_engine_units':True,'strength':5,
            'fatness_engine_units':math.inf,'body_radius_engine_units':0}),{})
