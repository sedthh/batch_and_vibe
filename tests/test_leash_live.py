from dataclasses import replace
import hashlib
from unittest import TestCase
from unittest.mock import patch

from bwv.control import ControlObservation
from bwv.leash import LeashContext
from bwv import leash_live as live
from test_training_live import MemoryFixture


class LeashMemory(MemoryFixture):
    def __init__(self):
        super().__init__()
        self.interface, self.leash, self.target = 0x6000000, 0x6100000, 0x6200000
        for i, (address, name) in enumerate(((self.hand, b'.?AVGInterface@@'),
                (self.interface, b'.?AVGInterfaceStatus@@'),
                (self.leash, b'.?AVGLeashStatus@@'), (self.target, b'.?AVTree@@')), start=3):
            table = 0x5000000 + i * 0x1000
            self.put(address, table)
            self.put(table - 4, table + 0x100)
            self.put(table + 0x10c, table + 0x200)
            self.bytes(table + 0x208, name.ljust(64, b'\0'))
        self.put(self.hand + live.HAND_INTERFACE, self.interface)
        self.put(self.interface + live.INTERFACE_LEASH, self.leash)
        self.state(1, 2, 0)

    def state(self, active, variant, target):
        for offset, value in ((live.LEASH_ACTIVE, active), (live.LEASH_VARIANT, variant),
                              (live.LEASH_TARGET, target)):
            self.put(self.leash + offset, value)

    def sample(self, final=None):
        with patch.object(live, 'snapshot', return_value=self.observer if final is None else final):
            return live.read_leash(self.reader, self.observer)


class LiveLeashTests(TestCase):
    def test_variants_hand_attachment_and_full_removal(self):
        memory = LeashMemory()
        for value, name in ((1, 'aggression'), (2, 'learning'), (3, 'compassion'), (9, None)):
            memory.state(1, value, 0)
            self.assertEqual(memory.sample().context, LeashContext(True, name, 'hand'))
        memory.state(0, 3, memory.target)
        self.assertEqual(memory.sample().context, LeashContext(False, attachment='none'))

    def test_target_is_opaque_and_scoped_to_session(self):
        memory = LeashMemory()
        memory.state(1, 2, memory.target)
        first = memory.sample()
        self.assertEqual(first.context.target_kind, 'tree')
        self.assertEqual(first.context.attachment, 'object')
        self.assertEqual(len(first.context.target_id), 24)
        self.assertEqual(first.context.target_id,hashlib.sha256(
            f'{first.session}:{memory.creature}:{memory.target}'.encode()).hexdigest()[:24])
        memory.observer['session'] = 6
        second = memory.sample()
        self.assertNotEqual(first.context.target_id, second.context.target_id)
        self.assertNotEqual(first.creature_id, second.creature_id)

    def test_shared_world_types_include_town_scaffold_and_dynamic_objects(self):
        for runtime,expected in ((b'.?AVTownCentre@@','town_centre'),(b'.?AVScaffold@@','scaffolding'),
                (b'.?AVAbode@@','house'),(b'.?AVCow@@','animal'),(b'.?AVBall@@','toy'),
                (b'.?AVUnknownThing@@','object'),(b'.?AVMobileStatic@@','fence')):
            memory=LeashMemory();memory.state(1,2,memory.target)
            memory.bytes(0x5006208,runtime.ljust(64,b'\0'))
            if expected=='fence':
                info=0x6300000;table=0x5007000
                memory.put(memory.target+0x28,info);memory.put(info,table)
                memory.put(table-4,table+0x100);memory.put(table+0x10c,table+0x200)
                memory.bytes(table+0x208,b'.?AVGMobileStaticInfo@@'.ljust(64,b'\0'))
                memory.put(info+0x120,56)
            with self.subTest(runtime=runtime):
                self.assertEqual(memory.sample().context.target_kind,expected)

    def test_tick_can_advance_without_losing_verified_attachment(self):
        memory=LeashMemory();memory.state(1,2,memory.target)
        original=memory.reader._head;calls=0
        def advancing():
            nonlocal calls
            calls+=1
            game,tick,player,creature,hand=original()
            return game,tick+calls,player,creature,hand
        memory.reader._head=advancing
        self.assertEqual(memory.sample().context.target_kind,'tree')

    def test_bad_type_owner_and_session_refuse_samples(self):
        for change in ('type', 'owner', 'stale', 'session', 'active'):
            memory = LeashMemory()
            final = None
            if change == 'type':
                memory.bytes(0x5005208, b'.?AVTown@@'.ljust(64, b'\0'))
            elif change == 'owner':
                memory.put(memory.creature + live.CREATURE_OWNER, memory.player + 1)
            elif change == 'stale':
                memory.observer['source_age_ms'] = 2000
            elif change == 'session':
                final = dict(memory.observer, session=6)
            else:
                memory.state(2, 1, 0)
            with self.subTest(change=change), self.assertRaises(ValueError):
                memory.sample(final)

    def test_missing_creature_clears_context(self):
        from bwv.training_live import PLAYER_CREATURE
        memory = LeashMemory()
        memory.put(memory.player + PLAYER_CREATURE, 0)
        self.assertEqual(memory.sample().context, LeashContext())
        self.assertIsNone(memory.sample().creature_id)

    def test_changing_attachment_is_not_published(self):
        memory = LeashMemory()
        original = memory.read
        calls = 0
        def changing(address, size):
            nonlocal calls
            if address == memory.leash + live.LEASH_ACTIVE:
                calls += 1
                return (calls % 2).to_bytes(4, 'little')
            return original(address, size)
        memory.reader.read = changing
        with self.assertRaises(ValueError):
            memory.sample()

    def test_context_merge_checks_identity_and_preserves_source_age(self):
        signal = LeashMemory().sample()
        obs = ControlObservation(signal.session, 1, 0, creature_id=signal.creature_id)
        updated = live.with_leash(obs, signal, signal.observed_at)
        self.assertEqual(updated.leash, signal.context)
        self.assertEqual(updated.observed_at, 0)
        self.assertFalse(updated.supported)
        for changed in (replace(signal, session='other'), replace(signal, creature_id='other'),
                        replace(signal, observed_at=signal.observed_at-2)):
            self.assertEqual(live.with_leash(updated, changed, signal.observed_at).leash, LeashContext())
