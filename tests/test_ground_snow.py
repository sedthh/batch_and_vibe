import json
import struct
import unittest
from unittest.mock import patch

from bwv import ground_snow_live as live
from bwv.environment_live import read_environment, CLOCK_REFERENCE, VISUAL_TIME, THRESHOLD_REFERENCE, SKY_THRESHOLDS
from bwv.prompt import preview_prompt
from bwv.llm_context import summarize_context
from test_control import playable
from test_training_live import MemoryFixture


class GroundSnowTests(unittest.TestCase):
    def memory(self, material=1, corners=(0,0,0,0), position=(100,140)):
        m=MemoryFixture()
        for at,value in live.LAYOUT_REFERENCES.items():m.put(at,value)
        m.bytes(0x8D150C,struct.pack('<f',.025))
        m.bytes(0x999A10,struct.pack('<f',27))
        x,z=(round(v*65536/10) for v in position)
        m.bytes(m.creature+0x14,struct.pack('<ii',x,z))
        cx,cz=x//65536,z//65536
        self.index=live.BLOCK_INDEX+(cx//16)*32+cz//16
        m.bytes(self.index,b'\x03')
        m.put(live.BLOCKS+3*4,0x6300000)
        self.cell=0x6300000+((cx%16)*17+cz%16)*8
        m.bytes(self.cell,bytes([0,0,0,0,42,0,2,0]))
        self.country=0x6400000
        m.put(live.COUNTRIES+2*4,self.country)
        m.put(self.country+42*12+8,9)
        m.put(live.MATERIALS+9*4,0)
        m.put(live.DEFAULT_MATERIALS+9*4,material)
        self.cover=0x6500000
        m.put(live.SNOW_COVER,self.cover)
        column,row=x//262144,z//262144
        self.top=self.cover+(row*128+column)*4
        self.bottom=self.top+128*4
        m.bytes(self.top,struct.pack('<ff',*corners[:2]))
        m.bytes(self.bottom,struct.pack('<ff',*corners[2:]))
        return m

    def snow(self,m):return live.snow_on_ground(m.reader,m.reader._head())

    def test_static_snow_material_without_precipitation_or_accumulation(self):
        self.assertIs(self.snow(self.memory(material=27)),True)
        self.assertIs(self.snow(self.memory(material=1)),False)

    def test_loaded_material_overrides_default(self):
        m=self.memory(material=1)
        m.put(live.MATERIALS+9*4,0x6600000)
        m.bytes(0x6600000,struct.pack('<H',27))
        self.assertIs(self.snow(m),True)

    def test_accumulated_snow_matches_native_surface_threshold(self):
        for amount,expected in ((0,False),(53.99,False),(54,True),(120,True)):
            self.assertIs(self.snow(self.memory(corners=(amount,)*4)),expected)

    def test_interpolates_at_creature_instead_of_nearby_grid_corner(self):
        # At the centre of the four grid points, one snowy corner is insufficient.
        self.assertIs(self.snow(self.memory(corners=(100,0,0,0))),False)
        self.assertIs(self.snow(self.memory(corners=(40,40,80,80))),True)

    def test_material_lookup_uses_correct_land_block_axis_order(self):
        self.assertIs(self.snow(self.memory(material=27,position=(1230,2710))),True)

    def test_no_snow_grid_means_only_terrain_material_is_used(self):
        for material,expected in ((27,True),(1,False)):
            m=self.memory(material=material)
            m.put(live.SNOW_COVER,0)
            self.assertIs(self.snow(m),expected)

    def test_map_edge_matches_native_sampler_without_reading_past_grid(self):
        m=self.memory(position=(5090,5090))
        self.assertIs(self.snow(m),False)

    def test_unreadable_invalid_or_moving_data_is_omitted_not_false(self):
        for problem in ('layout','scale','missing_block','missing_country','bad_material_index',
                        'bad_material','nan','unreadable','moving','changed_cell','changed_grid'):
            m=self.memory(corners=(80,)*4)
            if problem=='layout':m.put(next(iter(live.LAYOUT_REFERENCES)),0)
            elif problem=='scale':m.bytes(0x8D150C,struct.pack('<f',.05))
            elif problem=='missing_block':m.bytes(self.index,b'\0')
            elif problem=='missing_country':m.put(live.COUNTRIES+8,0)
            elif problem=='bad_material_index':m.put(self.country+42*12+8,50)
            elif problem=='bad_material':m.put(live.DEFAULT_MATERIALS+36,43)
            elif problem=='nan':m.bytes(self.top,struct.pack('<f',float('nan')))
            elif problem=='unreadable':del m.data[self.top]
            else:
                address={'moving':m.creature+0x14,'changed_cell':self.cell,
                         'changed_grid':live.SNOW_COVER}[problem]
                original=m.reader.read;count=0
                def changing(at,size):
                    nonlocal count
                    data=original(at,size)
                    if at==address:
                        count+=1
                        if count>1:return bytes([data[0]^1])+data[1:]
                    return data
                m.reader.read=changing
            self.assertIsNone(self.snow(m),problem)

    def test_ground_reading_survives_unavailable_precipitation(self):
        m=self.memory(material=27)
        m.put(CLOCK_REFERENCE,VISUAL_TIME)
        m.put(THRESHOLD_REFERENCE,SKY_THRESHOLDS+12)
        m.bytes(VISUAL_TIME,struct.pack('<f',8))
        m.bytes(SKY_THRESHOLDS,struct.pack('<4f',7,6,5,4))
        with patch('bwv.pipe.snapshot',return_value=m.observer):
            environment=read_environment(m.reader,m.observer)
        context=summarize_context(json.loads(preview_prompt(playable(environment=environment),None).context_json))
        self.assertEqual(context['world'],{'time_of_day':'morning',
            'weather_near_creature':{'snow_on_ground':True}})

    def test_only_reads_local_cells_with_bounded_work(self):
        m=self.memory(corners=(80,)*4)
        head=m.reader._head()
        original=m.reader.read;reads=[]
        def tracked(at,size):
            reads.append((at,size))
            return original(at,size)
        m.reader.read=tracked
        self.assertIs(live.snow_on_ground(m.reader,head),True)
        self.assertLess(len(reads),50)
        self.assertLessEqual(max(size for _,size in reads),8)
        self.assertEqual({at for at,_ in reads if self.cover<=at<self.cover+65536},
                         {self.top,self.bottom})
