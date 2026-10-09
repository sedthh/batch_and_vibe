from unittest import TestCase
from unittest.mock import patch
import struct

from bwv import conditions_live as live
from test_holding_live import HoldingMemory


class FireReaderTests(TestCase):
    def memory(self):
        m=HoldingMemory();m.fire=0x6200000;m.info=0x6300000
        table=0x5009000
        m.put(m.info,table);m.put(table-4,table+0x100);m.put(table+0x10c,table+0x200)
        m.bytes(table+0x208,b'.?AVCreatureInfo@@'.ljust(64,b'\0'))
        m.put(m.creature+live.OBJECT_INFO,m.info)
        m.put(m.creature+live.OBJECT_FIRE_DATA,m.fire)
        m.put(m.fire+live.FIRE_OBJECT,m.creature)
        m.bytes(m.fire+live.FIRE_VALUE,struct.pack('<f',60))
        m.bytes(m.info+live.INFO_FIRE_THRESHOLD,struct.pack('<f',50))
        return m

    def sample(self,m):
        with patch.object(live,'snapshot',return_value=m.observer):
            return live.read_fire(m.reader,m.observer)

    def test_fire_comparison_no_record_and_clamped_threshold(self):
        m=self.memory()
        self.assertTrue(self.sample(m).on_fire)
        m.bytes(m.info+live.INFO_FIRE_THRESHOLD,struct.pack('<f',70))
        self.assertFalse(self.sample(m).on_fire)
        m.bytes(m.info+live.INFO_FIRE_THRESHOLD,struct.pack('<f',10))
        m.bytes(m.fire+live.FIRE_VALUE,struct.pack('<f',40))
        sample=self.sample(m)
        self.assertTrue(sample.on_fire)
        self.assertEqual(sample.ignition_threshold_engine_units,40)
        self.assertIn('pending',sample.validation)
        m.put(m.creature+live.OBJECT_FIRE_DATA,0)
        self.assertFalse(self.sample(m).on_fire)

    def test_wrong_owner_nonfinite_and_torn_records_fail_closed(self):
        m=self.memory();m.put(m.fire+live.FIRE_OBJECT,m.creature+4)
        with self.assertRaises(ValueError):self.sample(m)
        m=self.memory();m.bytes(m.fire+live.FIRE_VALUE,struct.pack('<f',float('nan')))
        with self.assertRaises(ValueError):self.sample(m)
        m=self.memory();original=m.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==m.fire+live.FIRE_VALUE:
                calls+=1
                return struct.pack('<f',float(calls))
            return original(address,size)
        m.reader.read=changing
        with self.assertRaises(ValueError):self.sample(m)
