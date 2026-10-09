"""Scaffold metadata and inspection, with synthetic memory only."""
from dataclasses import asdict
import json
import struct
from unittest import TestCase
from unittest.mock import patch

from bwv import god_hand_live
from bwv.scaffolding import scaffold_details
from bwv.scaffolding_live import ABODE_INFO_BASE, ABODE_INFO_STRIDE, read_scaffolding
from bwv.object_details_live import read_details
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm_context import CONTEXT_CONTRACT, summarize_context
from bwv.model_projection import model_turn
from bwv.prompt import preview_prompt
from test_god_hand import HandMemory
from test_control import playable
from test_creature_tools import frame


class ScaffoldMemory(HandMemory):
    def __init__(self):
        super().__init__()
        self.hold(b'.?AVScaffold@@')
        table = self.reader.word(self.held)
        for off, value in ((0x98,0),(0x11C,0x402600),(0x120,0x402520)):
            self.put(table+off,value)
        self.bytes(self.held+0x48,struct.pack('<f',1))
        self.bytes(self.held+0x50,struct.pack('<f',1.1))
        self.put(self.held+0x6C,0);self.put(self.held+0x70,0)

    def plan(self, *, alternate=False, description='ABODE_CRECHE', tribe=7, count=3, size=1.2):
        planned = self.held+0x1000+(0x1000 if alternate else 0)
        info = ABODE_INFO_BASE+ABODE_INFO_STRIDE*(1 if alternate else 0)
        self.typed(planned,b'.?AVPlannedAbode@@')
        table = self.reader.word(planned)
        self.put(table+0x2C,0x401810);self.put(table+0x120,0x4050C0)
        self.bytes(planned+0xA,b'\0');self.put(planned+0x40,info)
        self.bytes(planned+0x2C,struct.pack('<f',size))
        self.typed(info,b'.?AVGAbodeInfo@@')
        data=bytearray(0x50);struct.pack_into('<I',data,0,count)
        data[0x1C:0x4C]=description.encode().ljust(48,b'\0')
        struct.pack_into('<I',data,0x4C,tribe)
        self.bytes(info+0x10C,data)
        self.put(self.held+(0x70 if alternate else 0x6C),planned)
        return planned,info


class ScaffoldTests(TestCase):
    def test_combined_count_matches_native_scale_rounding(self):
        m=ScaffoldMemory()
        for size,count in ((.5,0),(.7,1),(.9,2),(1.1,3),(2.5,10)):
            m.bytes(m.held+0x50,struct.pack('<f',size))
            data,errors=read_scaffolding(m.reader,m.held)
            self.assertEqual(errors,0)
            self.assertEqual(data,{'combined_scaffolds':count,'size_scale':size})

    def test_primary_and_alternate_buildings_are_distinct_and_not_built(self):
        m=ScaffoldMemory();m.plan();m.plan(alternate=True,description='ABODE_F',tribe=0,count=4,size=.9)
        data,errors=read_details(m.reader,m.held,b'.?AVScaffold@@')
        self.assertEqual(errors,0)
        self.assertEqual(data['health_engine_units'],1)
        self.assertEqual(data['planned_building'],{'type':'nursery','tribe':'norse','required_scaffolds':3,'size_scale':1.2})
        self.assertEqual(data['alternative_building'],{'type':'house','tribe':'celtic','house_variant':'F',
                                                    'required_scaffolds':4,'size_scale':.9})
        self.assertNotIn('construction_complete',data)
        self.assertEqual(scaffold_details(data)['combined_scaffolds'],3)

    def test_deleted_null_unknown_or_corrupt_plan_preserves_count_and_other_plan(self):
        m=ScaffoldMemory();p,info=m.plan();m.plan(alternate=True,description='ABODE_STORAGE_PIT')
        m.bytes(p+0xA,b'\1')
        data,errors=read_scaffolding(m.reader,m.held)
        self.assertEqual(errors,0);self.assertNotIn('planned_building',data)
        self.assertEqual(data['alternative_building']['type'],'storage')
        m.bytes(p+0xA,b'\0');m.put(p+0x40,info+4)
        data,errors=read_scaffolding(m.reader,m.held)
        self.assertEqual(errors,1);self.assertEqual(data['combined_scaffolds'],3)
        self.assertNotIn('planned_building',data)
        m.put(p+0x40,0)
        self.assertEqual(read_scaffolding(m.reader,m.held)[1],0)
        m.plan(description='UNKNOWN_BUILDING')
        self.assertNotIn('planned_building',read_scaffolding(m.reader,m.held)[0])

    def test_bad_scale_keeps_health_and_valid_plan(self):
        m=ScaffoldMemory();m.plan()
        for size in (float('nan'),float('inf'),-.5,.4):
            m.bytes(m.held+0x50,struct.pack('<f',size))
            data,errors=read_details(m.reader,m.held,b'.?AVScaffold@@')
            self.assertEqual(errors,1)
            self.assertEqual(data['health_engine_units'],1)
            self.assertNotIn('combined_scaffolds',data)
            self.assertEqual(data['planned_building']['type'],'nursery')

    def test_swapped_links_or_count_during_read_dont_mix_old_and_new_plans(self):
        for changed in ('links','count'):
            m=ScaffoldMemory();m.plan();original=m.reader.read;calls=0
            address=m.held+(0x6C if changed=='links' else 0x50)
            def read(pointer,size):
                nonlocal calls
                if pointer==address:
                    calls+=1
                    if calls>=3:return bytes(size)
                return original(pointer,size)
            m.reader.read=read
            if changed=='count':
                with self.assertRaisesRegex(ValueError,'changed'):read_scaffolding(m.reader,m.held)
            else:
                data,errors=read_scaffolding(m.reader,m.held)
                self.assertEqual(errors,1);self.assertNotIn('planned_building',data)
                self.assertEqual(data['combined_scaffolds'],3)

    def test_player_hand_and_surrounding_object_inspection_keep_details(self):
        m=ScaffoldMemory();m.plan()
        with patch.object(god_hand_live,'snapshot',return_value=m.observer):
            hand=god_hand_live.read_god_hand(m.reader,m.observer).context
        self.assertEqual(hand.detail_read_errors,0)
        obj=hand.held_object
        for held in (True,False):
            f=frame()
            if held:f['observation']['god_hand']=asdict(hand)
            else:f['observation']['surroundings']['known_objects']=[obj]
            result=DirectCreatureToolSession(lambda:f).call('inspect_object',{'object_id':obj['object_id']})
            self.assertEqual(result['object']['details']['planned_building']['type'],'nursery')
            self.assertEqual(result['object']['details']['combined_scaffolds'],3)
            self.assertFalse(result['executed'])
        context=json.loads(preview_prompt(playable(god_hand=hand),None).context_json)
        projected=model_turn({'snapshot':summarize_context(context)})['snapshot']['god_hand']['held_object']
        self.assertEqual(projected['details']['planned_building']['tribe'],'norse')
        self.assertNotIn('_engine_units',json.dumps(projected))
        self.assertIn('combined_scaffolds',CONTEXT_CONTRACT)

    def test_projection_omits_private_and_invalid_details(self):
        data=scaffold_details({'combined_scaffolds':-1,'size_scale':float('nan'),
            'planned_building':{'type':'nursery','pointer':1234,'description':'ABODE_CRECHE','tribe':'invalid',
                                'required_scaffolds':True,'size_scale':0},
            'alternative_building':{'type':'unknown'}})
        self.assertEqual(data,{'planned_building':{'type':'nursery'}})
