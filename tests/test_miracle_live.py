from unittest import TestCase
from unittest.mock import patch
import struct

from bwv import miracle_live as live
from bwv.training_live import GAME_TICK, PLAYER_CREATURE
from test_training_live import MemoryFixture


class MiracleMemory(MemoryFixture):
    def __init__(self):
        super().__init__()
        address = self.mental + live.MENTAL_KNOWN_ACTIONS
        table = 0x5003000
        self.put(address, table)
        self.put(table-4, table+0x100)
        self.put(table+0x10c, table+0x200)
        self.bytes(table+0x208, b'.?AVCreatureActionsKnownAbout@@'.ljust(64,b'\0'))
        self.put(self.mental+live.MENTAL_CREATURE, self.creature)
        self.put(self.mental+live.HEAL_COUNTER, 7)
        self.put(self.mental+live.HEAL_LEARNING_TICK, 99)
        self.put(self.player+live.PLAYER_CAST_TYPE, 10)
        self.put(self.player+live.PLAYER_CAST_TICK, 98)
        self.info = 0x6000000
        self.put(self.creature+live.CREATURE_INFO,self.info)
        for i,(address,name) in enumerate(((self.info,b'.?AVCreatureInfo@@'),
                (live.HEAL_LEARNING_INFO,b'.?AVCreatureMagicActionKnownAboutEntry@@')),start=4):
            table=0x5000000+i*0x1000
            self.put(address,table);self.put(table-4,table+0x100)
            self.put(table+0x10c,table+0x200)
            self.bytes(table+0x208,name.ljust(64,b'\0'))
        self.put(live.HEAL_LEARNING_INFO+live.BASE_LEARNING_REQUIREMENT,15)
        self.bytes(self.info+live.INFO_MAGIC_LEARNING_FACTOR,struct.pack('<f',1.03))
        for kind in live.SPELLS:
            if kind == live.HEAL_TYPE:
                continue
            entry = 0xCA9C70+kind*0x70
            self.put(entry,self.reader.word(live.HEAL_LEARNING_INFO))
            self.put(entry+live.BASE_LEARNING_REQUIREMENT,18)
            self.put(self.mental+0x17D38+kind*4,0)
            self.put(self.mental+0x17DE0+kind*4,0)


    def sample(self, final=None):
        with patch.object(live, 'snapshot', return_value=self.observer if final is None else final):
            return live.read_miracle(self.reader, self.observer)


class MiracleReaderTests(TestCase):
    def test_typed_creature_learning_and_player_history_stay_separate(self):
        sample = MiracleMemory().sample()
        self.assertEqual((sample.heal_counter, sample.heal_learning_tick), (7,99))
        self.assertEqual((sample.player_cast_type, sample.player_cast_tick), (10,98))
        self.assertIsNotNone(sample.creature_id)
        self.assertAlmostEqual(sample.heal_learning_requirement,15.45,places=5)

    def test_changed_species_factor_and_invalid_requirement_are_rejected(self):
        memory=MiracleMemory();original=memory.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==memory.info+live.INFO_MAGIC_LEARNING_FACTOR:
                calls+=1
                return struct.pack('<f',1.0 if calls%2 else 2.0)
            return original(address,size)
        memory.reader.read=changing
        with self.assertRaises(ValueError):memory.sample()
        for value in (0,float('nan'),float('inf'),-1):
            memory=MiracleMemory()
            memory.bytes(memory.info+live.INFO_MAGIC_LEARNING_FACTOR,struct.pack('<f',value))
            with self.assertRaises(ValueError):memory.sample()

    def test_no_creature_leaves_learning_unknown(self):
        memory = MiracleMemory()
        memory.put(memory.player+PLAYER_CREATURE, 0)
        self.assertIsNone(memory.sample().creature_id)

    def test_saved_learning_survives_an_island_clock_reset(self):
        memory = MiracleMemory()
        memory.put(memory.game+GAME_TICK, 5)
        sample = memory.sample()
        self.assertEqual(sample.game_tick, 5)
        self.assertEqual((sample.heal_counter, sample.heal_learning_tick), (7, 99))
        self.assertAlmostEqual(sample.heal_learning_requirement, 15.45, places=5)
        self.assertEqual(len(sample.spells()), 41)

    def test_type_identity_and_lifecycle_refuse_samples(self):
        for change in ('type','identity','owner','stale','session'):
            memory = MiracleMemory()
            final = None
            if change == 'type':
                memory.bytes(0x5003208,b'.?AVTown@@'.ljust(64,b'\0'))
            elif change == 'identity':
                memory.put(memory.mental+live.MENTAL_CREATURE, memory.creature+1)
            elif change == 'owner':
                memory.put(memory.creature+live.CREATURE_OWNER, memory.player+1)
            elif change == 'stale':
                memory.observer['source_age_ms'] = 2000
            else:
                final = dict(memory.observer, session=6)
            with self.subTest(change=change), self.assertRaises(ValueError):
                memory.sample(final)

    def test_learning_write_during_read_is_not_published(self):
        memory = MiracleMemory()
        original = memory.read
        calls = 0
        def changing(address, size):
            nonlocal calls
            if address == memory.mental+live.HEAL_COUNTER:
                calls += 1
                return (7+calls%2).to_bytes(4,'little')
            return original(address,size)
        memory.reader.read = changing
        with self.assertRaises(ValueError):
            memory.sample()
