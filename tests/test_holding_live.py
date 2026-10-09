from dataclasses import asdict, replace
import struct
from unittest import TestCase
from unittest.mock import patch

from bwv import holding_live as live
from bwv.control import ControlObservation
from bwv.holding import HoldingContext
from bwv.training_live import PLAYER_CREATURE, HAND_TARGET
from test_training_live import MemoryFixture


class HoldingMemory(MemoryFixture):
    def __init__(self):
        super().__init__()
        self.physical, self.held = 0x6000000, 0x6100000
        objects = ((self.physical,b'.?AVCreaturePhysical@@'),
                   (self.held,b'.?AVPileFood@@'),
                   (self.mental+live.MENTAL_PLAN,b'.?AVCreaturePlan@@'),
                   (self.mental+live.MENTAL_DESIRES,b'.?AVCreatureDesires@@'))
        for i,(address,name) in enumerate(objects,start=3):
            table=0x5000000+i*0x1000
            self.put(address,table);self.put(table-4,table+0x100)
            self.put(table+0x10c,table+0x200)
            self.bytes(table+0x208,name.ljust(64,b'\0'))
        self.put(self.creature+live.CREATURE_PHYSICAL,self.physical)
        self.put(self.physical+live.PHYSICAL_HELD_OBJECT,self.held)
        self.put(self.mental+live.MENTAL_PLAN+live.PLAN_ACTION,11)
        self.bytes(self.mental+live.MENTAL_DESIRES+live.DESIRE_VALUES,bytes(160))
        self.bytes(self.mental+live.MENTAL_DESIRES+live.HUNGER_DESIRE,struct.pack('<f',1.9))

    def sample(self,final=None):
        with patch.object(live,'snapshot',return_value=self.observer if final is None else final):
            return live.read_holding(self.reader,self.observer)


class HoldingReaderTests(TestCase):
    def test_held_animal_species_survives_advancing_tick(self):
        from bwv.llm_context import summarize_context
        memory=HoldingMemory()
        memory.bytes(0x5004208,b'.?AVCow@@'.ljust(64,b'\0'))
        original=memory.reader._head;calls=0
        def advancing():
            nonlocal calls
            calls+=1
            game,tick,*rest=original()
            return game,tick+calls,*rest
        memory.reader._head=advancing
        holding=memory.sample().context
        self.assertEqual(holding.species,'cow')
        self.assertEqual(calls,2)
        compact=summarize_context({}, {'holding':asdict(holding)})
        self.assertEqual(compact['self']['held_object']['species'],'cow')

    def test_held_person_is_inspectable_without_surrounding_entry(self):
        from bwv.creature_tools import CreatureToolSession
        from bwv.llm_context import summarize_context
        from bwv.nearby_people_live import TICKS_PER_YEAR
        from test_creature_tools import frame
        from test_surroundings import NearbyMemory
        memory=HoldingMemory();obj=memory.held
        memory.bytes(0x5004208,b'.?AVVillager@@'.ljust(64,b'\0'))
        memory.put(memory.reader.word(obj)+0x8D0,0x5ECAF0)
        memory.bytes(obj+0x25,b'\0');memory.bytes(obj+0x8C,bytes((118,121,15)))
        memory.bytes(obj+0xF2,b'\x01');memory.put(TICKS_PER_YEAR,100)
        memory.put(obj+0xA0,(100-32*100)&0xFFFFFFFF)
        info=0x6300000;memory.put(obj+0x28,info)
        NearbyMemory.typed(memory,info,b'.?AVGVillagerInfo@@',11)
        memory.put(info+0x1F8,1)
        holding=asdict(memory.sample().context)
        person=holding['person']
        self.assertEqual((person['sex'],person['age_years'],person['assigned_role']),
            ('female',32,'farmer'))
        self.assertEqual(person['current_activity'],'eating')
        state=frame();state['observation']['holding']=holding
        session=CreatureToolSession(lambda:state)
        result=session.call('inspect_object',{'object_id':holding['object_id']})
        self.assertEqual(result['object']['person'],person)
        state['observation']['surroundings']['known_objects']=[{
            'object_id':holding['object_id'],'kind':'villager','person':{'current_activity':'sleeping'}}]
        self.assertEqual(session.call('inspect_object',{'object_id':holding['object_id']})['object']['person'],person)
        self.assertEqual(session.call('inspect_self',{})['holding_and_motivations']['person'],person)
        self.assertEqual(summarize_context({},state['observation'])['self']['held_object']['person'],person)

    def test_all_drives_include_tiredness_but_not_inferred_physical_conditions(self):
        memory=HoldingMemory()
        memory.bytes(memory.mental+live.MENTAL_DESIRES+live.DESIRE_VALUES+8*4,struct.pack('<f',.7))
        drives={d.name:d.value for d in memory.sample().context.drives}
        self.assertEqual(len(drives),40)
        self.assertAlmostEqual(drives['tiredness'],.7)
        self.assertAlmostEqual(drives['hunger'],1.9)
        self.assertNotIn('health',drives)

    def test_nonfinite_and_torn_drive_arrays_are_rejected(self):
        memory=HoldingMemory()
        memory.bytes(memory.mental+live.MENTAL_DESIRES+live.DESIRE_VALUES,struct.pack('<f',float('nan')))
        with self.assertRaises(ValueError):memory.sample()
        memory=HoldingMemory();original=memory.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==memory.mental+live.MENTAL_DESIRES+live.DESIRE_VALUES:
                calls+=1
                return struct.pack('<40f',*([0]*8+[calls%2]+[0]*31))
            return original(address,size)
        memory.reader.read=changing
        with self.assertRaises(ValueError):memory.sample()
    def test_miracle_plan_is_independent_of_held_object_and_clears_on_plan_change(self):
        memory = HoldingMemory()
        memory.put(memory.physical+live.PHYSICAL_HELD_OBJECT, 0)
        for action, spell in ((101, 'heal'), (58, 'heal'), (240, 'water'),
                              (239, 'water'), (325, 'water_power_up_1'), (11, None), (999, None)):
            memory.put(memory.mental+live.MENTAL_PLAN+live.PLAN_ACTION, action)
            context = memory.sample().context
            self.assertEqual(context.miracle_plan, spell)
            self.assertFalse(context.holding)

    def test_torn_miracle_plan_is_not_published(self):
        memory = HoldingMemory(); original = memory.read; calls = 0
        def changing(address, size):
            nonlocal calls
            if address == memory.mental+live.MENTAL_PLAN+live.PLAN_ACTION:
                calls += 1
                return (101 if calls % 2 else 240).to_bytes(4, 'little')
            return original(address, size)
        memory.reader.read = changing
        with self.assertRaises(ValueError): memory.sample()

    def test_creature_hand_is_independent_of_god_hand(self):
        memory=HoldingMemory()
        signal=memory.sample()
        self.assertEqual(signal.context.kind,'food_pile')
        self.assertEqual(signal.context.active_plan,'eat')
        self.assertAlmostEqual(signal.context.hunger_desire,1.9)
        memory.put(memory.physical+live.PHYSICAL_HELD_OBJECT,0)
        memory.put(memory.hand+HAND_TARGET,memory.held)
        self.assertFalse(memory.sample().context.holding)
        self.assertIsNone(memory.sample().context.item_id)

    def test_food_tree_and_unknown_kind(self):
        memory=HoldingMemory()
        for name,kind in (*live.KINDS.items(),(b'.?AVUnclassified@@','object')):
            memory.bytes(0x5004208,name.ljust(64,b'\0'))
            self.assertEqual(memory.sample().context.kind,kind)
            self.assertTrue(memory.sample().context.holding)
        for name,detail in live.RUNTIME_HELD_DETAILS.items():
            memory.bytes(0x5004208,name.ljust(64,b'\0'))
            self.assertEqual(memory.sample().context.object_type,detail)
        memory.put(memory.mental+live.MENTAL_PLAN+live.PLAN_ACTION,999)
        self.assertIsNone(memory.sample().context.active_plan)

    def test_no_creature_clears_context_and_session_scopes_item_identity(self):
        memory=HoldingMemory()
        old=memory.sample()
        memory.observer['session']=6
        self.assertNotEqual(old.context.item_id,memory.sample().context.item_id)
        memory.put(memory.player+PLAYER_CREATURE,0)
        self.assertEqual(memory.sample().context,HoldingContext())

    def test_type_owner_staleness_and_load_refuse_samples(self):
        for change in ('type','owner','stale','load'):
            memory=HoldingMemory();final=None
            if change=='type':memory.bytes(0x5003208,b'.?AVTown@@'.ljust(64,b'\0'))
            elif change=='owner':memory.put(memory.creature+live.CREATURE_OWNER,memory.player+1)
            elif change=='stale':memory.observer['source_age_ms']=2000
            else:final=dict(memory.observer,session=6)
            with self.subTest(change=change),self.assertRaises(ValueError):memory.sample(final)

    def test_changing_held_object_is_not_published(self):
        memory=HoldingMemory();original=memory.read;calls=0
        def changing(address,size):
            nonlocal calls
            if address==memory.physical+live.PHYSICAL_HELD_OBJECT:
                calls+=1
                return (memory.held if calls%2 else 0).to_bytes(4,'little')
            return original(address,size)
        memory.reader.read=changing
        with self.assertRaises(ValueError):memory.sample()

    def test_merge_preserves_age_and_rejects_other_creature(self):
        signal=HoldingMemory().sample()
        obs=ControlObservation(signal.session,1,0,creature_id=signal.creature_id)
        updated=live.with_holding(obs,signal,signal.observed_at)
        self.assertEqual(updated.observed_at,0)
        self.assertEqual(updated.holding.kind,'food_pile')
        for invalid in (replace(signal,creature_id='other'),replace(signal,session='other'),
                        replace(signal,observed_at=signal.observed_at-2)):
            self.assertEqual(live.with_holding(updated,invalid,signal.observed_at).holding,HoldingContext())
