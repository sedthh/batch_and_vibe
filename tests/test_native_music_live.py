"""Synthetic layouts and fingerprints only, never a game process or game bytes."""
import hashlib
import struct
import unittest
from unittest.mock import patch
from bwv import native_music_live as live


class Memory:
    def __init__(self):self.data={}
    def put(self, address, value, fmt='I'):self.data[address]=struct.pack('<'+fmt,value)
    def read(self, address, size):
        if address not in self.data:raise OSError('Unmapped fixture address')
        return self.data[address][:size]


class NativeMusicTests(unittest.TestCase):
    def setUp(self):
        self.reader=Memory()
        self.reader.data[0x10000]=b'synthetic mood accessor'
        self.reader.data[0x20000]=b'synthetic line timing'
        def signature(addr):
            data=self.reader.data[addr]
            return addr,len(data),hashlib.sha256(data).hexdigest()
        self.enterContext(patch.object(live,'MOOD_SIGNATURES',(signature(0x10000),)))
        self.enterContext(patch.object(live,'LINE_SIGNATURE',signature(0x20000)))
        self.entry,self.table,self.line=0x30000,0x40000,0x50000
        for addr,value in ((0xd06434,1),(0xd06438,1),(0xd410e0,self.entry),(0xd410e4,1),
                           (self.entry,42),(self.entry+8,1),(self.entry+12,self.table),
                           (0xd01964,self.line),(self.line+0x14,1),
                           (self.line+0x3508,9000),(self.line+0x3058,143)):
            self.reader.put(addr,value)
        for addr,value in ((self.table,.25),(0xc64140,-.1),(self.line+0x102c,1000),
                           (self.line+0x350c,1500),(self.line+0x3050,10)):
            self.reader.put(addr,value,'f')

    def test_native_mood_and_detected_tempo_are_distinct_sources(self):
        result=live.read_native_music(self.reader,tick_ms=10000)
        self.assertEqual(result['mood']['mood_engine_units'],.25)
        self.assertEqual(result['mood']['entry_track_id'],'0000002a')
        self.assertEqual(result['line_input']['source'],'game_line_input')
        self.assertEqual(result['line_input']['detected_tempo_bpm'],143)

    def test_stale_beat_and_native_default_do_not_become_measured_tempo(self):
        self.reader.put(self.line+0x3058,120)
        result=live.read_native_music(self.reader,tick_ms=15000)['line_input']
        self.assertFalse(result['recent_beat_detected'])
        self.assertNotIn('detected_tempo_bpm',result)
        self.reader.put(self.line+0x350c,500,'f')
        result=live.read_native_music(self.reader,tick_ms=10000)['line_input']
        self.assertNotIn('detected_tempo_bpm',result)

    def test_disabled_missing_and_unverified_features_are_omitted_independently(self):
        self.reader.put(0xd06438,0);self.reader.put(0xd01964,0)
        result=live.read_native_music(self.reader,tick_ms=10000)
        self.assertNotIn('mood_engine_units',result['mood'])
        self.assertFalse(result['line_input']['active'])
        self.reader.data[0x10000]=b'changed code'
        self.assertNotIn('mood',live.read_native_music(self.reader,tick_ms=10000))
        self.reader.data[0x20000]=b'changed code'
        self.assertIsNone(live.read_native_music(self.reader,tick_ms=10000))

    def test_nonfinite_or_torn_data_does_not_replace_valid_other_sensor(self):
        self.reader.put(self.table,float('nan'),'f')
        result=live.read_native_music(self.reader,tick_ms=10000)
        self.assertNotIn('mood',result);self.assertIn('line_input',result)
        self.reader.put(self.table,.25,'f')
        original=self.reader.read
        def changing(addr,size):
            result=original(addr,size)
            if addr==self.table:self.reader.put(addr,.5,'f')
            return result
        self.reader.read=changing
        self.assertNotIn('mood',live.read_native_music(self.reader,tick_ms=10000))
