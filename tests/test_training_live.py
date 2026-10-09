from unittest import TestCase
from unittest.mock import patch
import struct

from bwv import training_live as live


class MemoryFixture:
    """Synthetic metadata only: no executable or captured game data."""
    def __init__(self):
        self.data = {}
        self.game, self.creature, self.mental, self.hand = 0x1000000, 0x2000000, 0x3000000, 0x4000000
        self.player = self.game + live.PLAYER_START
        self.put(live.GAME_POINTER, self.game)
        self.put(self.game + live.GAME_TICK, 100)
        self.data[self.game + live.PLAYER_INDEX] = 0
        self.data[self.game + live.HAND_INDEX] = 0
        self.put(self.player + live.PLAYER_CREATURE, self.creature)
        self.put(self.player + live.PLAYER_HANDS, self.hand)
        self.put(self.creature + live.CREATURE_OWNER, self.player)
        self.put(self.creature + live.CREATURE_MENTAL, self.mental)
        self.put(self.hand + live.HAND_STATE, 19)
        self.put(self.hand + live.HAND_TARGET, self.creature)
        self.bytes(self.mental + live.INTERACTION_MAGNITUDE, struct.pack('<fI', -.1, 99))
        for i, (address, name) in enumerate(((self.player, b'.?AVGPlayer@@'),
                (self.creature, b'.?AVCreature@@'), (self.mental, b'.?AVCreatureMental@@'))):
            table = 0x5000000 + i * 0x1000
            self.put(address, table)
            self.put(table - 4, table + 0x100)
            self.put(table + 0x10c, table + 0x200)
            self.bytes(table + 0x208, name.ljust(64, b'\0'))
        self.reader = live.TrainingReader.__new__(live.TrainingReader)
        self.reader.pid, self.reader.handle = 1234, 1
        class Kernel:
            @staticmethod
            def GetExitCodeProcess(handle, code):
                code._obj.value = 259
                return True
        self.reader.kernel = Kernel()
        self.reader.read = self.read
        self.observer = dict(protocol=1, pid=1234, session=5, source_age_ms=0)

    def bytes(self, address, data):
        self.data.update((address + i, b) for i, b in enumerate(data))

    def put(self, address, value):
        self.bytes(address, struct.pack('<I', value))

    def read(self, address, size):
        try:
            return bytes(self.data[address + i] for i in range(size))
        except KeyError:
            raise OSError('Synthetic unreadable memory') from None

    def sample(self, final=None):
        with patch.object(live, 'snapshot', return_value=self.observer if final is None else final):
            return self.reader.sample(self.observer)


class TrainingReaderTests(TestCase):
    def test_selected_creature_feedback_and_attention(self):
        memory = MemoryFixture()
        sample = memory.sample()
        self.assertTrue(sample.attention_active)
        self.assertAlmostEqual(sample.magnitude, -.1)
        self.assertEqual(sample.feedback_tick, 99)
        self.assertIsNotNone(sample.creature_id)

    def test_other_target_does_not_establish_attention(self):
        memory = MemoryFixture()
        memory.put(memory.hand + live.HAND_TARGET, 0x6000000)
        self.assertFalse(memory.sample().attention_active)

    def test_owner_and_type_mismatches_fail_closed(self):
        for address in ('owner', 'type'):
            memory = MemoryFixture()
            if address == 'owner':
                memory.put(memory.creature + live.CREATURE_OWNER, memory.player + 1)
            else:
                memory.bytes(0x5001208, b'.?AVTown@@'.ljust(64, b'\0'))
            with self.assertRaises(ValueError):
                memory.sample()

    def test_missing_creature_is_not_dereferenced(self):
        memory = MemoryFixture()
        memory.put(memory.player + live.PLAYER_CREATURE, 0)
        self.assertIsNone(memory.sample().creature_id)

    def test_load_and_stale_lifecycle_refuse_sample(self):
        for final in (dict(protocol=1, pid=1234, session=6, source_age_ms=0),
                      dict(protocol=1, pid=1234, session=5, source_age_ms=2000)):
            with self.assertRaises(ValueError):
                MemoryFixture().sample(final)

    def test_changing_feedback_does_not_escape_consistency_check(self):
        memory = MemoryFixture()
        original = memory.read
        changes = 0
        def changing(address, size):
            nonlocal changes
            if address == memory.mental + live.INTERACTION_MAGNITUDE:
                changes += 1
                return struct.pack('<fI', .1 if changes % 2 else .2, 99)
            return original(address, size)
        memory.reader.read = changing
        with self.assertRaises(ValueError):
            memory.sample()
