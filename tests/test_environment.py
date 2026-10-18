import json
import struct
import unittest
from unittest.mock import patch

from bwv.environment import Environment, visual_day_period, weather_context
from bwv import environment_live as live
from bwv.llm_context import summarize_context
from bwv.prompt import preview_prompt
from test_control import playable
from test_training_live import MemoryFixture


class EnvironmentTests(unittest.TestCase):
    def weather_memory(self, rain=0, snow=0):
        from test_surroundings import NearbyMemory
        m = MemoryFixture()
        m.put(live.GRID_REFERENCE, live.WEATHER_GRID)
        m.put(live.GENERATION_REFERENCE, live.WEATHER_GENERATION)
        m.put(m.game + 0x250534, 0x6300000)
        NearbyMemory.typed(m, 0x6300000, b'.?AVGClimate@@', 20)
        m.put(live.WEATHER_GRID, 0x6400000)
        m.bytes(live.WEATHER_GENERATION, b'\x04')
        # Creature is at cell (2,3), not at the grid origin/camera.
        m.bytes(m.creature + 0x14, struct.pack('<ii', 2*262144, 3*262144))
        self.cell = 0x6400000 + (3*128+2)*8
        m.bytes(self.cell, struct.pack('<bbbbbbbB', 0,rain,snow,0,0,0,0,4))
        return m

    def test_local_rain_snow_and_mixed_precipitation_reach_context(self):
        for rain, snow, expected in ((10,0,'rain'),(0,10,'snow'),(5,8,'rain_and_snow'),
                                     (0,0,'no_rain_or_snow'),(-1,-20,'no_rain_or_snow')):
            m = self.weather_memory(rain, snow)
            data = live.local_precipitation(m.reader, m.reader._head())
            self.assertEqual(data, (expected,rain,snow,'current_creature_cell'))
            env = Environment(weather_near_creature=data[0])
            compact = summarize_context(json.loads(preview_prompt(playable(environment=env),None).context_json))
            self.assertEqual(compact['world'], {'weather_near_creature':{
                'raining':rain > 0, 'snowing':snow > 0}})

    def test_ground_snow_is_independent_of_falling_snow_and_preserves_false(self):
        for falling, ground in (('no_rain_or_snow',True), ('snow',False), (None,True),
                                (None,False), ('rain',None)):
            env=Environment(weather_near_creature=falling,snow_on_ground=ground)
            prompt=json.loads(preview_prompt(playable(environment=env),None).context_json)
            compact=summarize_context(prompt)['world']['weather_near_creature']
            if falling is None:
                self.assertEqual(compact,{'snow_on_ground':ground})
            else:
                self.assertEqual(compact['snowing'],falling=='snow')
                self.assertEqual(compact['raining'],falling=='rain')
                if ground is None:self.assertNotIn('snow_on_ground',compact)
                else:self.assertIs(compact['snow_on_ground'],ground)
            self.assertTrue(all(type(value) is bool for value in compact.values()))

    def test_weather_projection_only_exposes_known_booleans(self):
        self.assertEqual(weather_context({'raining':False,'snowing':None,'snow_on_ground':0,
                                           'coverage':100}),{'raining':False})
        self.assertEqual(weather_context(None),{})

    def test_stale_missing_or_changing_weather_is_not_reported_as_dry(self):
        for variant in ('stale','missing','moving','changed','layout','outside'):
            m = self.weather_memory(20)
            if variant == 'stale':m.bytes(live.WEATHER_GENERATION,b'\x05')
            elif variant == 'missing':m.put(live.WEATHER_GRID,0)
            elif variant == 'layout':m.put(live.GRID_REFERENCE,0)
            elif variant == 'outside':m.bytes(m.creature+0x14,struct.pack('<ii',128*262144,0))
            else:
                original=m.reader.read; count=0
                watched=m.creature+0x14 if variant=='moving' else self.cell
                def changing(address,size):
                    nonlocal count
                    data=original(address,size)
                    if address==watched:
                        count+=1
                        if count>1:return bytes([data[0]^1])+data[1:]
                    return data
                m.reader.read=changing
            self.assertIsNone(live.local_precipitation(m.reader,m.reader._head())[0],variant)

    def test_weather_uses_creature_cell_not_another_local_storm(self):
        m=self.weather_memory(20)
        m.bytes(m.creature+0x14,struct.pack('<ii',3*262144,3*262144))
        m.bytes(self.cell+8,bytes([0,0,0,0,0,0,0,4]))
        self.assertEqual(live.local_precipitation(m.reader,m.reader._head())[0],'no_rain_or_snow')

    def test_sky_thresholds_and_midnight_wrap(self):
        limits = (4, 5, 6, 7)
        for hour, expected in ((0,'night'),(3,'night'),(4,'sunrise'),(6,'sunrise'),
                               (7,'morning'),(12,'afternoon'),(17,'afternoon'),
                               (18,'sunset'),(21,'night'),(24,'night')):
            self.assertEqual(visual_day_period(hour, limits), expected)
        for hour, limits in ((float('nan'),limits),(-1,limits),(25,limits),(8,(0,0,0,0)),
                             (8,(4,3,6,7)),(8,(4,5,6,float('inf')))):
            with self.assertRaises(ValueError):visual_day_period(hour,limits)

    def test_reader_and_compact_context_use_engine_clock_not_wall_time(self):
        memory = MemoryFixture()
        memory.put(live.CLOCK_REFERENCE, live.VISUAL_TIME)
        memory.put(live.THRESHOLD_REFERENCE, live.SKY_THRESHOLDS+12)
        memory.bytes(live.VISUAL_TIME,struct.pack('<f',18))
        memory.bytes(live.SKY_THRESHOLDS,struct.pack('<4f',7,6,5,4))
        with patch('bwv.pipe.snapshot',return_value=memory.observer):
            environment = live.read_environment(memory.reader,memory.observer)
        self.assertEqual(environment.time_of_day,'sunset')
        prompt = preview_prompt(playable(environment=environment),None)
        summary = summarize_context(json.loads(prompt.context_json))
        self.assertEqual(summary['world'],{'time_of_day':'sunset'})
        self.assertNotIn('weather',summary['world'])
        self.assertNotIn('visual_hour',summary['world'])
        memory.put(live.CLOCK_REFERENCE,0)
        with self.assertRaises(ValueError):live.read_environment(memory.reader,memory.observer)
        self.assertNotIn('world',json.loads(preview_prompt(playable(environment=Environment()),None).context_json))

    def test_load_or_threshold_change_does_not_return_stale_phase(self):
        memory = MemoryFixture()
        memory.put(live.CLOCK_REFERENCE,live.VISUAL_TIME)
        memory.put(live.THRESHOLD_REFERENCE,live.SKY_THRESHOLDS+12)
        memory.bytes(live.VISUAL_TIME,struct.pack('<f',8))
        memory.bytes(live.SKY_THRESHOLDS,struct.pack('<4f',7,6,5,4))
        with patch('bwv.pipe.snapshot',return_value={**memory.observer,'session':99}):
            with self.assertRaises(ValueError):live.read_environment(memory.reader,memory.observer)
