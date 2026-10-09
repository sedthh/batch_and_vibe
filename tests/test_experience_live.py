import struct
from unittest import TestCase
from unittest.mock import patch
from dataclasses import replace

from bwv import experience_live as live
from bwv.conditions_live import FireSample
from bwv.training_live import CREATURE_OWNER
from test_holding_live import HoldingMemory


class ExperienceMemory(HoldingMemory):
    def __init__(self):
        super().__init__()
        self.bytes(self.creature+live.HEALTH,struct.pack('<f',.8))
        for i,offset in enumerate(live.PHYSICAL_STATS.values()):
            self.bytes(self.physical+offset,struct.pack('<f',i/10))
        self.action=11;self.info=live.ACTION_INFO+self.action*live.ACTION_STRIDE
        table=0x500A000
        self.put(self.info,table);self.put(table-4,table+0x100);self.put(table+0x10c,table+0x200)
        self.bytes(table+0x208,b'.?AVCreatureActionInfo@@'.ljust(64,b'\0'))
        self.bytes(self.info+live.ACTION_NAME,b'Eat\0'.ljust(32,b'\0'))

    def sample_experience(self):
        import hashlib,time
        session=self.reader._session(self.observer)
        identity=hashlib.sha256(f'{session}:{self.creature}:{self.mental}'.encode()).hexdigest()[:24]
        fire=FireSample(session,identity,time.monotonic(),False,None,None)
        with patch.object(live,'snapshot',return_value=self.observer),patch.object(live,'read_fire',return_value=fire):
            return live.read_experience(self.reader,self.observer)


class ExperienceReaderTests(TestCase):
    def test_negative_warmth_preserves_health_and_native_plan(self):
        memory=ExperienceMemory()
        memory.bytes(memory.physical+live.PHYSICAL_STATS['warmth'],struct.pack('<f',-.25))
        result=memory.sample_experience()
        self.assertEqual(result.body.warmth,-.25)
        self.assertAlmostEqual(result.body.health,.8)
        self.assertEqual(result.instinct.plan_name,'Eat')
        for value in (float('nan'),float('inf'),-1e10):
            with self.assertRaises(ValueError):live.BodyState(warmth=value)
        with self.assertRaises(ValueError):live.BodyState(health=-.25)

    def test_primary_plan_target_is_described_without_claiming_a_hit(self):
        from test_surroundings import NearbyMemory
        memory=ExperienceMemory()
        belief,obj,info=0x6400000,0x6500000,0x6600000
        memory.put(memory.mental+live.MENTAL_PLAN+0x10,belief)
        NearbyMemory.typed(memory,belief,b'.?AVCreatureBeliefSmall@@',20)
        memory.put(belief+0x30,obj)
        NearbyMemory.typed(memory,obj,b'.?AVMobileStatic@@',21)
        memory.put(obj+0x28,info)
        NearbyMemory.typed(memory,info,b'.?AVGMobileStaticInfo@@',22)
        memory.put(info+0x120,56)
        result=memory.sample_experience()
        self.assertEqual(result.instinct.target_kind,'fence')
        self.assertEqual(len(result.instinct.target_id),24)
        self.assertEqual(result.effects,())
        memory.put(memory.mental+live.MENTAL_PLAN+0x10,0)
        self.assertIsNone(memory.sample_experience().instinct.target_kind)

    def test_reads_body_and_named_native_plan_without_claiming_effect_source(self):
        memory=ExperienceMemory();result=memory.sample_experience()
        self.assertAlmostEqual(result.body.health,.8)
        self.assertAlmostEqual(result.body.exhaustion,.4)
        self.assertEqual(result.instinct.plan_name,'Eat')
        self.assertFalse(result.body.on_fire)
        self.assertEqual(result.effects,())

    def test_unknown_plan_and_unterminated_bounded_label(self):
        memory=ExperienceMemory()
        memory.bytes(memory.info+live.ACTION_NAME,b'A'*32)
        self.assertTrue(memory.sample_experience().instinct.name_truncated)
        memory.put(memory.mental+live.MENTAL_PLAN+live.PLAN_ACTION,0xffffffff)
        self.assertIsNone(memory.sample_experience().instinct.plan_name)

    def test_owner_bad_type_and_torn_health_fail_closed(self):
        memory=ExperienceMemory();memory.put(memory.creature+CREATURE_OWNER,memory.player+1)
        with self.assertRaises(ValueError):memory.sample_experience()
        memory=ExperienceMemory();memory.bytes(0x500A208,b'.?AVTown@@'.ljust(64,b'\0'))
        with self.assertRaises(ValueError):memory.sample_experience()
        memory=ExperienceMemory();original=memory.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==memory.creature+live.HEALTH:
                calls+=1
                return struct.pack('<f',calls/10)
            return original(address,size)
        memory.reader.read=changing
        with self.assertRaises(ValueError):memory.sample_experience()
