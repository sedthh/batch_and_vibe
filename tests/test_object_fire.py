import json
import math
import struct
import unittest
from dataclasses import asdict

from bwv.object_fire import read_object_fire
from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from bwv.creature_tools import CreatureToolSession
from test_control import playable
from test_surroundings import NearbyMemory
from test_creature_tools import frame


class ObjectFireTests(unittest.TestCase):
    def memory(self, value=80, threshold=60):
        m=NearbyMemory()
        m.put(m.reader.word(m.tree)+0x298,0x402AA0)
        self.fire,self.info=0x6400000,0x6500000
        m.put(m.tree+0x44,self.fire)
        m.put(self.fire+0x1C,m.tree)
        m.put(m.tree+0x28,self.info)
        m.bytes(self.fire+0x14,struct.pack('<f',value))
        m.bytes(self.info+0xB4,struct.pack('<f',threshold))
        return m

    def test_threshold_and_minimum_match_engine_predicate(self):
        for value,threshold,expected in ((59,60,False),(60,60,True),(80,60,True),
                                         (39,0,False),(40,0,True)):
            m=self.memory(value,threshold)
            self.assertIs(read_object_fire(m.reader,m.tree),expected)
        m.put(m.tree+0x44,0)
        self.assertIs(read_object_fire(m.reader,m.tree),False)

    def test_unsupported_predicate_is_unknown_not_false(self):
        m=self.memory()
        m.put(m.reader.word(m.tree)+0x298,0x405470)
        self.assertIsNone(read_object_fire(m.reader,m.tree))

    def test_invalid_or_changing_records_are_not_presented_as_facts(self):
        for fault in ('owner','nan','negative','changed'):
            m=self.memory()
            if fault=='owner':m.put(self.fire+0x1C,m.tree+1)
            elif fault=='nan':m.bytes(self.fire+0x14,struct.pack('<f',math.nan))
            elif fault=='negative':m.bytes(self.info+0xB4,struct.pack('<f',-1))
            else:
                original=m.reader.read
                count=0
                def read(address,size):
                    nonlocal count
                    if address==self.fire+0x14:
                        count+=1
                        if count>1:return struct.pack('<f',20)
                    return original(address,size)
                m.reader.read=read
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                read_object_fire(m.reader,m.tree)

    def test_summary_and_inspection_expose_fire_and_omit_unknown(self):
        for burning in (True,False,None):
            m=self.memory(80 if burning else 20)
            if burning is None:m.put(m.reader.word(m.tree)+0x298,0)
            context=m.sample().context
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
            self.assertEqual(compact['surroundings'].get('burning_objects_nearby'),
                             {'tree':1} if burning else None)
            def source():
                f=frame();f['observation']['surroundings']=asdict(context);return f
            result=CreatureToolSession(source).call('inspect_surrounding',{'kind':None,'limit':10})
            item=result['objects'][0]
            if burning is None:self.assertNotIn('on_fire',item)
            else:self.assertIs(item['on_fire'],burning)

    def test_failed_fire_read_preserves_object_and_counts_diagnostic(self):
        m=self.memory();m.put(self.fire+0x1C,1)
        context=m.sample().context
        self.assertEqual(len(context.nearby_known_objects),1)
        self.assertIsNone(context.nearby_known_objects[0].on_fire)
        self.assertEqual(context.fire_read_errors,1)

    def test_scaffolding_target_and_debris_count_only(self):
        from bwv.world_actions import WORLD_KINDS
        self.assertIn('scaffolding',WORLD_KINDS)
        self.assertNotIn('debris',WORLD_KINDS)
        for name,kind,key in ((b'.?AVScaffold@@','scaffolding','scaffolding_nearby'),
                              (b'.?AVFragment@@','debris','debris_pieces_nearby')):
            m=NearbyMemory();m.typed(m.tree,name,4)
            context=m.sample().context
            compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
            self.assertEqual(compact['surroundings'],{key:1})
            def source():
                f=frame();f['observation']['surroundings']=asdict(context);return f
            result=CreatureToolSession(source).call('inspect_surrounding',{'kind':None,'limit':10})
            self.assertEqual(len(result['objects']),0 if kind=='debris' else 1)
