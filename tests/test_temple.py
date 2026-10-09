from dataclasses import replace
import unittest

from bwv.control import ControlGate
from bwv.control_live import read_temple, with_temple, TempleSample, INTERFACE_HAND, HAND_MODE, HAND_HANDLERS
from bwv.prompt import preview_prompt
from bwv.updates import ContextUpdates
from test_control import playable
from test_surroundings import NearbyMemory


class TempleTests(unittest.TestCase):
    def fixture(self):
        memory=NearbyMemory();controller=0x6400000;handler=0x6500000
        memory.typed(memory.hand,b'.?AVGInterface@@',6)
        memory.typed(controller,b'.?AVCHand@@',7)
        memory.typed(handler,b'.?AVHandStateCitadel@@',8)
        memory.put(memory.hand+INTERFACE_HAND,controller)
        memory.put(controller+HAND_MODE,1)
        memory.put(controller+HAND_HANDLERS+10*4,handler)
        memory.put(handler+4,controller)
        return memory,controller,handler

    def test_temple_mode_is_independent_of_pause_and_creature_pen(self):
        memory,controller,handler=self.fixture()
        self.assertFalse(read_temple(memory.reader).in_temple)
        memory.put(controller+HAND_MODE,10)
        self.assertTrue(read_temple(memory.reader).in_temple)
        obs=playable(in_temple=True,paused=False)
        gate=ControlGate(resume_delay=0).update(obs,0)
        self.assertIn('temple',gate.reasons)
        self.assertFalse(gate.allowed)
        status=ContextUpdates().update(obs,preview_prompt(obs,None),gate,0)
        self.assertEqual(status['reason'],'temple')
        memory.put(controller+HAND_MODE,1)
        self.assertFalse(read_temple(memory.reader).in_temple)

    def test_unknown_stale_or_wrong_process_temple_data_cannot_unlock(self):
        obs=playable(session='1234:5',observed_at=0,in_temple=True)
        for signal in (TempleSample(1234,0,False),TempleSample(12,10,False)):
            updated=with_temple(obs,signal,10)
            self.assertIsNone(updated.in_temple)
            self.assertEqual(updated.observed_at,0)
            self.assertFalse(ControlGate(resume_delay=0).update(updated,10).allowed)

    def test_mode_races_invalid_mode_and_wrong_handler_owner_fail_closed(self):
        for variant in ('owner','mode','race'):
            memory,controller,handler=self.fixture()
            if variant=='owner':memory.put(handler+4,controller+4)
            elif variant=='mode':memory.put(controller+HAND_MODE,11)
            else:
                original=memory.reader.word;count=0
                def word(address):
                    nonlocal count
                    if address==controller+HAND_MODE:
                        count+=1;return 1 if count%2 else 10
                    return original(address)
                memory.reader.word=word
            with self.subTest(variant=variant),self.assertRaises(ValueError):read_temple(memory.reader)
