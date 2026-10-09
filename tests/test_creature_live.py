from unittest import TestCase
from unittest.mock import patch
import struct

from bwv import creature_live as live
from bwv.training_live import CREATURE_OWNER, PLAYER_CREATURE
from bwv.profile_names import ProfileNames
from test_miracle_live import MiracleMemory


class CreatureMemory(MiracleMemory):
    def __init__(self):
        super().__init__()
        self.bytes(self.info+live.INFO_NAME, b'Ape creature'.ljust(48, b'\0'))
        self.put(self.creature+live.CREATURE_FLAGS, 0)
        self.put(self.creature+live.CREATURE_SCRIPT, 0)

    def sample(self, final=None):
        with patch.object(live, 'snapshot', return_value=self.observer if final is None else final), \
                patch.object(live, 'read_profile_names', return_value=ProfileNames()):
            return live.read_creature(self.reader, self.observer)


class SpeciesTests(TestCase):
    def test_script_control_tracks_both_native_flags_and_release(self):
        memory=CreatureMemory()
        identity=memory.sample().creature_id
        for flags,script,expected in ((0,0,False),(0x400,0,True),(0,123,True),
                                      (0x401,123,True),(1,0,False)):
            memory.put(memory.creature+live.CREATURE_FLAGS,flags)
            memory.put(memory.creature+live.CREATURE_SCRIPT,script)
            result=memory.sample()
            self.assertIs(result.script_owns_creature,expected)
            self.assertEqual(result.creature_id,identity)
        memory.put(memory.player+PLAYER_CREATURE,0)
        self.assertIsNone(memory.sample().script_owns_creature)

    def test_torn_script_ownership_read_does_not_release_control(self):
        memory=CreatureMemory();original=memory.reader.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==memory.creature+live.CREATURE_FLAGS:
                calls+=1
                return (0x400 if calls%2 else 0).to_bytes(size,'little')
            return original(address,size)
        memory.reader.read=changing
        with self.assertRaises(live.CreatureReadError):memory.sample()

    def test_size_and_both_alignments_do_not_invalidate_species(self):
        memory=CreatureMemory()
        def typed(address, name, slot):
            table=0x7100000+slot*0x1000
            memory.put(address,table);memory.put(table-4,table+0x100)
            memory.put(table+0x10c,table+0x200)
            memory.bytes(table+0x208,name.ljust(64,b'\0'))
        physical,controller,alignment,god_alignment=0x7200000,0x7300000,0x7400000,0x7500000
        memory.put(memory.creature+0x160,physical);typed(physical,b'.?AVCreaturePhysical@@',1)
        memory.put(physical+0x58,controller);memory.put(controller+0x4834,memory.creature)
        memory.bytes(controller+0x90,struct.pack('<f',.6))
        for owner,offset,address,value,slot in ((memory.creature,0x168,alignment,.4,2),
                                               (memory.player,0x60,god_alignment,-.8,3)):
            memory.put(owner+offset,address);typed(address,b'.?AVGAlignment@@',slot)
            memory.bytes(address+8,struct.pack('<f',value))
        result=memory.sample().context
        self.assertEqual(result.species,'monkey')
        self.assertEqual((result.alignment,result.god_alignment),(.4,-.8))
        self.assertEqual(result.individual_traits[0].name,'body_size_scale')
        self.assertAlmostEqual(result.individual_traits[0].value,.6)
        self.assertEqual(result.trait_read_errors,0)
        memory.bytes(alignment+8,struct.pack('<f',float('nan')))
        result=memory.sample().context
        self.assertIsNone(result.alignment)
        self.assertEqual(result.species,'monkey')
        self.assertEqual(result.trait_read_errors,1)

    def test_advancing_game_tick_does_not_disconnect_stable_creature(self):
        memory = CreatureMemory()
        original = memory.reader._head
        calls = 0
        def advancing_head():
            nonlocal calls
            calls += 1
            head = original()
            return (head[0], head[1] + calls, *head[2:])
        memory.reader._head = advancing_head
        self.assertEqual(memory.sample().context.species, 'monkey')
        self.assertEqual(calls, 2)

    def test_changing_creature_head_still_rejects_sample(self):
        memory = CreatureMemory()
        original = memory.reader._head
        calls = 0
        def changing_head():
            nonlocal calls
            calls += 1
            head = original()
            return (head[0], head[1], head[2], head[3] + (4 if calls % 2 == 0 else 0), head[4])
        memory.reader._head = changing_head
        with self.assertRaisesRegex(live.CreatureReadError, 'identity_or_species_changed'):
            memory.sample()

    def test_slow_live_read_still_rejected(self):
        memory = CreatureMemory()
        with patch.object(live.time, 'monotonic', side_effect=(10, 11)):
            with self.assertRaisesRegex(live.CreatureReadError, 'freshness_limit'):
                memory.sample()

    def test_species_changes_follow_current_info_not_previous_species(self):
        memory = CreatureMemory()
        initial = memory.sample()
        self.assertEqual(initial.context.species, 'monkey')
        self.assertEqual(initial.context.species_source, 'engine')
        memory.bytes(memory.info+live.INFO_NAME, b'Horse creature'.ljust(48, b'\0'))
        horse = memory.sample()
        self.assertEqual(horse.context.species, 'horse')
        self.assertEqual(horse.creature_id, initial.creature_id)
        memory.bytes(memory.info+live.INFO_NAME, b'Unknown creature'.ljust(48, b'\0'))
        self.assertIsNone(memory.sample().context.species)
        memory.put(memory.player+PLAYER_CREATURE, 0)
        self.assertIsNone(memory.sample().creature_id)

    def test_owner_type_epoch_and_torn_species_reads_fail_closed(self):
        for kind in ('owner', 'type', 'epoch', 'torn'):
            memory = CreatureMemory()
            final = None
            if kind == 'owner':
                memory.put(memory.creature+CREATURE_OWNER, memory.player+1)
            elif kind == 'type':
                memory.bytes(0x5004208, b'.?AVTown@@'.ljust(64, b'\0'))
            elif kind == 'epoch':
                final = dict(memory.observer, session=6)
            else:
                original = memory.read
                count = 0
                def read(address, size):
                    nonlocal count
                    if address == memory.info+live.INFO_NAME:
                        count += 1
                        return (b'Ape creature' if count % 2 else b'Horse creature').ljust(48,b'\0')
                    return original(address,size)
                memory.reader.read = read
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                memory.sample(final)
