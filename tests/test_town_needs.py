"""Synthetic town demand data through sensing, compact context and tool inspection."""
from dataclasses import asdict, replace
import ctypes
import json
import struct
from types import MethodType
import unittest

from bwv import town_needs_live as live
from bwv.direct_tools import DirectCreatureToolSession
from bwv.llm_context import summarize_context
from bwv.prompt import preview_prompt
from bwv.towns import TOWN_NEEDS, compact_towns
from test_control import playable
from test_creature_tools import frame
from test_towns import TownMemory


class TownNeedsTests(unittest.TestCase):
    def test_demands_work_through_production_bounded_process_reader(self):
        from bwv.training_live import TrainingReader
        m=TownMemory();m.needs([.5]*17,[.25]*17)
        sizes=[]
        def read_process(handle,address,buffer,size,received):
            sizes.append(size)
            ctypes.memmove(buffer,m.read(address,size),size)
            received._obj.value=size
            return True
        m.reader.kernel.ReadProcessMemory=read_process
        m.reader.read=MethodType(TrainingReader.read,m.reader)
        context=m.sample().context
        self.assertEqual(context.town_need_read_errors,0)
        self.assertEqual(context.towns[0].needs['food'],.75)
        self.assertLessEqual(max(sizes),256)

    def test_only_storage_flag_categories_include_modifiers_without_clamping(self):
        m=TownMemory()
        m.needs([i/8 for i in range(17)],[.25]*17,[-.5]*17)
        context=m.sample().context
        self.assertEqual(context.town_need_read_errors,0)
        needs=context.towns[0].needs
        self.assertEqual(set(needs),{'food','wood','protection','mercy','homes','children'})
        self.assertEqual(list(needs),['children','homes','mercy','protection','wood','food'])
        self.assertEqual(needs['food'],-.25)
        self.assertEqual(needs['children'],.75)
        # This alternative desire array is not the one used by storage flags.
        m.bytes(m.town+0x14C,struct.pack('<17f',*[999]*17))
        self.assertEqual(m.sample().context.towns[0].needs,needs)

    def test_tick_summary_has_only_three_strongest_positive_needs(self):
        m=TownMemory();m.needs([.4,2,999,-1,.7,1]+[999]*11)
        # Hidden drives must not crowd out actual flag demand.
        m.bytes(m.town+live.BASE_VALUES_START+8*4,struct.pack('<f',0))
        context=m.sample().context
        compact=summarize_context(json.loads(preview_prompt(playable(surroundings=context),None).context_json))
        town=compact['towns_nearby'][0]
        self.assertEqual(town['top_needs'],{'wood':2,'homes':1,'mercy':.7})
        self.assertNotIn('needs',town)
        self.assertEqual(context.towns[0].needs['food'],.4)

    def test_town_and_storage_inspection_return_only_flag_needs(self):
        m=TownMemory();m.needs([i/4 for i in range(17)])
        m.typed(m.tree,b'.?AVStoragePit@@',4)
        context=m.sample().context
        state=frame();state['observation']['surroundings']=asdict(context)
        session=DirectCreatureToolSession(lambda:state)
        obj=context.nearby_known_objects[0];town=context.towns[0]
        for identity in (obj.object_id,town.town_id):
            result=session.call('inspect_object',{'object_id':identity})
            self.assertEqual(result['status'],'observed')
            self.assertEqual(result['town']['needs'],town.needs)
            self.assertEqual(set(result['town']['needs']),
                {'food','wood','protection','mercy','homes','children'})
            self.assertFalse(result['executed'])
        result=session.call('find_objects',{'kind':'warehouse','limit':3, 'scope':'nearby', 'detail':'detailed'})
        self.assertEqual(result['objects'][0]['type'],'storage')
        self.assertEqual(result['towns'][0]['needs'],town.needs)

    def test_flag_selection_comes_from_engine_objects_not_a_fixed_six_item_list(self):
        m=TownMemory();m.flags((0,1,5,6,8));m.needs([.5]*17)
        self.assertEqual(set(m.sample().context.towns[0].needs),
                         {'food','wood','homes','civic_buildings','children'})
        m.flags(())
        self.assertEqual(m.sample().context.towns[0].needs,{})

    def test_unused_desire_values_cannot_invalidate_flag_readings(self):
        m=TownMemory();values=[.5]*17
        for index in (2,6,7,9,10,11,12,13,14,15,16):values[index]=float('nan')
        m.needs(values)
        self.assertEqual(m.sample().context.town_need_read_errors,0)
        self.assertEqual(set(m.sample().context.towns[0].needs.values()),{.5})

    def test_invalid_or_changing_flags_omit_needs_without_falling_back_to_internal_drives(self):
        for failure in ('unreadable','type','owner','category','layout','changed'):
            with self.subTest(failure=failure):
                m=TownMemory();flags=m.flags();m.needs([.5]*17)
                if failure=='unreadable':del m.data[m.town+live.TOWN_FLAGS]
                elif failure=='type':m.typed(flags[0],b'.?AVRock@@',14)
                elif failure=='owner':m.put(flags[0]+live.FLAG_TOWN,m.town+4)
                elif failure=='category':m.put(flags[0]+live.FLAG_CATEGORY,14)
                elif failure=='layout':m.bytes(live.FLAG_LAYOUT_CODE[0][0],b'\x90')
                else:
                    original=m.reader.read
                    def read(address,size):
                        result=original(address,size)
                        if address==m.town+live.BASE_VALUES_START:m.flags(())
                        return result
                    m.reader.read=read
                context=m.sample().context
                self.assertEqual(context.town_need_read_errors,1)
                self.assertIsNone(context.towns[0].needs)
                self.assertNotIn('top_needs',compact_towns(context.towns)[0])

    def test_many_buildings_share_one_demand_read_per_refresh(self):
        m=TownMemory();m.needs([.5]*17)
        _,objects=m.many_objects(100)
        for obj in objects:
            m.typed(obj,b'.?AVAbode@@',4)
            m.put(obj+0x98,m.town)
        original=m.reader.read;blocks=[];bases=[];code=[]
        def read(address,size):
            if address==m.town+live.VALUES_START:blocks.append(size)
            if address==m.town+live.BASE_VALUES_START:bases.append(size)
            if address==live.GET_RAW_DESIRE:code.append(size)
            return original(address,size)
        m.reader.read=read
        context=m.sample().context
        self.assertEqual(len(context.known_objects),100)
        self.assertEqual(len(context.towns),1)
        self.assertEqual(context.town_need_read_errors,0)
        self.assertEqual(blocks,[live.MODIFIERS_SIZE])
        self.assertEqual(bases,[live.BASE_VALUES_SIZE])
        self.assertEqual(code,[len(live.GET_RAW_DESIRE_CODE)])
        m.sample()
        self.assertEqual(len(blocks),2)
        self.assertEqual(len(bases),2)

    def test_normal_demand_changes_preserve_town_and_refresh_values(self):
        m=TownMemory();m.needs([.25]*17)
        original=m.reader.read;reads=0
        def read(address,size):
            nonlocal reads
            result=original(address,size)
            if address==m.town+live.BASE_VALUES_START:
                reads+=1
                m.needs([.25+reads]*17)
            return result
        m.reader.read=read
        first=m.sample().context;second=m.sample().context
        self.assertEqual(first.towns[0].town_id,second.towns[0].town_id)
        self.assertEqual(first.towns[0].needs['food'],.25)
        self.assertEqual(second.towns[0].needs['food'],1.25)
        self.assertEqual((first.town_need_read_errors,second.town_need_read_errors),(0,0))

    def test_invalid_demand_preserves_town_and_objects_without_stale_needs(self):
        for failure in ('type','owner','code','unreadable','nan','infinity','range','total'):
            with self.subTest(failure=failure):
                m=TownMemory();m.needs([.5]*17)
                state=frame();state['observation']['surroundings']=asdict(m.sample().context)
                session=DirectCreatureToolSession(lambda:state)
                town_id=state['observation']['surroundings']['towns'][0]['town_id']
                self.assertEqual(session.call('inspect_object',{'object_id':town_id})['town']['needs']['food'],.5)
                if failure=='type':m.typed(m.town+live.TOWN_DESIRE,b'.?AVRock@@',12)
                elif failure=='owner':m.put(m.town+live.TOWN_DESIRE+live.DESIRE_TOWN,m.town+4)
                elif failure=='code':m.bytes(live.GET_RAW_DESIRE,b'\x90')
                elif failure=='unreadable':del m.data[m.town+live.VALUES_START]
                elif failure=='total':m.needs([8e8]*17,[8e8]*17)
                else:m.needs([{'nan':float('nan'),'infinity':float('inf'),'range':1e10}[failure]]+[0]*16)
                current=m.sample().context
                self.assertEqual(len(current.nearby_known_objects),1)
                self.assertEqual(current.towns[0].town_id,town_id)
                self.assertEqual(current.town_read_errors,0)
                self.assertEqual(current.town_need_read_errors,1)
                self.assertIsNone(current.towns[0].needs)
                self.assertNotIn('top_needs',compact_towns(current.towns)[0])
                state['observation']['surroundings']=asdict(current)
                self.assertIsNone(session.call('inspect_object',{'object_id':town_id})['town'].get('needs'))

    def test_owner_change_during_demand_read_discards_only_needs(self):
        m=TownMemory();original=m.reader.read
        def read(address,size):
            result=original(address,size)
            if address==m.town+live.VALUES_START:
                m.put(m.town+live.TOWN_DESIRE+live.DESIRE_TOWN,m.town+4)
            return result
        m.reader.read=read
        context=m.sample().context
        self.assertEqual(context.town_need_read_errors,1)
        self.assertIsNone(context.towns[0].needs)
        self.assertEqual(context.nearby_known_objects[0].town_id,context.towns[0].town_id)

    def test_known_zero_needs_differ_from_unavailable(self):
        town=TownMemory().sample().context.towns[0]
        self.assertEqual(compact_towns((town,))[0]['top_needs'],{})
        self.assertEqual(len(town.needs),6)
        self.assertNotIn('top_needs',compact_towns((replace(town,needs=None),))[0])

    def test_invalid_serialized_needs_are_rejected(self):
        town=TownMemory().sample().context.towns[0]
        for values in ([],{'unknown_need':1},dict(town.needs,food=True),dict(town.needs,food=float('nan'))):
            with self.subTest(values=values),self.assertRaises(ValueError):
                replace(town,needs=values)
