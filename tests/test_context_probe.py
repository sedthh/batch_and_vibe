import struct
import unittest

from bwv.context_probe import _wide, _help, _learning, _messages, read_context_probe
from bwv.experience_live import ACTION_INFO, ACTION_STRIDE, ACTION_NAME


class Memory:
    def __init__(self):
        self.data = {}
        self.types = {}

    def put(self, address, data):
        self.data.update({address+i: value for i, value in enumerate(data)})

    def words(self, address, *values):
        self.put(address, struct.pack('<'+'I'*len(values), *values))

    def read(self, address, size):
        if not 0 < size <= 256:
            raise ValueError('Read exceeds bound')
        try:
            return bytes(self.data[address+i] for i in range(size))
        except KeyError:
            raise OSError('Unmapped memory') from None

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def require_type(self, address, name):
        if self.types.get(address) != name:
            raise ValueError('Unexpected type')


class ProbeTests(unittest.TestCase):
    def test_text_read_stops_at_terminator_and_handles_unicode(self):
        m = Memory();m.put(100, 'Synthetic animal says héllo\0'.encode('utf-16-le'))
        self.assertEqual(_wide(m, 100), 'Synthetic animal says héllo')
        self.assertIsNone(_wide(m, 0))
        m.put(100, b'\0\xd8\0\0')
        with self.assertRaises(ValueError):_wide(m, 100)
        m.put(100, b'a\0'*4)
        with self.assertRaises(ValueError):_wide(m, 100, limit=4)

    def test_help_ring_reads_current_first_and_keeps_unverified_attribution(self):
        m = Memory();game, system, text = 1000, 2000, 3000
        m.words(game+0x25005C, system);m.words(system+0x14, text)
        m.types[system] = b'.?AVHelpSystem@@';m.types[text] = b'.?AVHelpText@@'
        m.words(text+0x94, 1);m.put(text+0x34, bytes(96))
        m.words(text+0x34+16, 4000, 0, 0, 0)
        m.put(4000, 'Synthetic thought\0'.encode('utf-16-le'))
        m.put(text+0xB8, b'\1');m.words(system+0x584, 9, 0, 0, 0, 0, 0)
        m.words(system+0x45D8, 50)
        result = _help(m, game)
        self.assertEqual(result['lines'][0]['text'], 'Synthetic thought')
        self.assertEqual(result['attribution'], 'unverified_game_help_text')
        m.words(text+0x94, 6)
        with self.assertRaises(ValueError):_help(m, game)

    def test_learning_bounds_fail_before_reading_unbounded_entries(self):
        m = Memory();mental = 1000;learning = mental+0x2AD8
        m.types[learning] = b'.?AVCreatureLearning@@'
        m.words(learning+8+0x20, 0, 0)
        self.assertEqual(_learning(m, mental)['contexts'], [])
        m.words(learning+8+0x20, 5, 0)
        with self.assertRaises(ValueError):_learning(m, mental)

    def test_learning_context_plan_is_read_as_diagnostic_not_training_reason(self):
        m = Memory();mental = 1000;learning = mental+0x2AD8;ring = learning+8
        m.types[learning] = b'.?AVCreatureLearning@@'
        m.words(ring+0x20, 1, 1)
        entry = ring+0x28
        m.types[entry] = b'.?AVCreatureContext@@';m.types[entry+8] = b'.?AVCreaturePlan@@'
        m.put(entry, bytes(0x70));m.words(entry+0x20, 11)
        info = ACTION_INFO+11*ACTION_STRIDE
        m.types[info] = b'.?AVCreatureActionInfo@@'
        m.put(info+ACTION_NAME, b'SyntheticPlan\0'.ljust(32, b'\0'))
        result = _learning(m, mental)
        self.assertEqual(result['contexts'][0]['native_plan'], 'SyntheticPlan')
        self.assertEqual(result['attribution'], 'not_confirmed_training_targets')
        m.words(entry+0x20, 328)
        with self.assertRaises(ValueError):_learning(m, mental)
        m.words(learning+8+0x20, 0, 6)
        with self.assertRaises(ValueError):_learning(m, mental)

    def test_message_list_is_bounded_and_cycles_are_rejected(self):
        m = Memory();creature = 1000;slot = creature+0x220
        m.put(slot, bytes(42*8));m.words(slot, 5000, 1)
        m.words(5000, 0, 6000);m.words(6000, 4, 0, 0, 0, 0, 19, 1)
        self.assertEqual(_messages(m, creature), [
            {'message_kind': 0, 'argument': 4, 'last_game_tick': 19, 'pending': 1}])
        m.words(5000, 5000, 6000);m.words(slot, 5000, 2)
        with self.assertRaises(ValueError):_messages(m, creature)
        m.words(slot, 5000, 65)
        with self.assertRaises(ValueError):_messages(m, creature)

    def test_wrong_owner_rejected_before_optional_reads(self):
        m = Memory();m.ensure_running = lambda: None
        m._head = lambda: (1000, 20, 2000, 3000, 4000)
        m.types[3000] = b'.?AVCreature@@'
        m.words(3000+0x1070, 9999)
        with self.assertRaisesRegex(ValueError, 'owner'):read_context_probe(m)
