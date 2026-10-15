from copy import deepcopy
import hashlib
import unittest

from bwv.sleep import sleep_state, sleep_phase
from bwv.llm_context import summarize_context


def sleep_frame(frame, sleeping=True):
    frame=deepcopy(frame)
    obs=frame['observation']
    obs['session']='synthetic:9'
    obs['creature_id']=hashlib.sha256(b'synthetic:9:100:200').hexdigest()[:24]
    frame.setdefault('diagnostics',{})['action_probe']={
        'supported':True,'epoch':9,
        'conversation_actions':{'creature_handle':100,'mental_handle':200},
        'activity':{'verified':True,'controller_mode':3 if sleeping else 0,
                    'animation':29 if sleeping else 0,'sleep_in_sequence':False}}
    return frame


class SleepTests(unittest.TestCase):
    def test_speech_distinguishes_queued_entry_loop_and_exit_without_changing_scheduling(self):
        frame=sleep_frame({'observation':{'observed_at':100}})
        for mode,animation,pending,expected in (
                (0,0,True,'preparing_to_sleep'),(2,29,True,'falling_asleep'),
                (3,29,True,'asleep'),(4,29,True,'waking_up')):
            with self.subTest(phase=expected):
                frame['diagnostics']['action_probe']['activity'].update(
                    controller_mode=mode,animation=animation,sleep_in_sequence=pending)
                self.assertTrue(sleep_state(frame,100))
                self.assertEqual(sleep_phase(frame,100),expected)
                result=summarize_context({'sleep_phase':expected,'sleeping':True})
                self.assertEqual(result['activity']['sleep_phase'],expected)
                self.assertEqual(result['activity'].get('sleeping',False),expected=='asleep')
        self.assertIsNone(sleep_phase(frame,102))
        self.assertEqual(sleep_phase(sleep_frame(frame,False),100),'awake')
        context=summarize_context({'sleep_phase':'awake','sleeping':True})
        self.assertEqual(context['activity']['sleep_phase'],'awake')
        self.assertNotIn('sleeping',context['activity'])

    def test_only_verified_current_creature_activity_establishes_sleep(self):
        frame=sleep_frame({'observation':{'observed_at':100}})
        self.assertTrue(sleep_state(frame,100))
        self.assertFalse(sleep_state(sleep_frame(frame,False),100))
        for mode in (2,3,4):
            frame['diagnostics']['action_probe']['activity']['controller_mode']=mode
            self.assertTrue(sleep_state(frame,100))
        frame=sleep_frame(frame,False)
        frame['diagnostics']['action_probe']['activity']['sleep_in_sequence']=True
        self.assertTrue(sleep_state(frame,100))
        self.assertIsNone(sleep_state(frame,102))
        frame['observation']['creature_id']='another-creature'
        self.assertIsNone(sleep_state(frame,100))
        frame=sleep_frame(frame)
        frame['diagnostics']['action_probe']['epoch']=10
        self.assertIsNone(sleep_state(frame,100))
        self.assertIsNone(sleep_state({'prompt':{'context':{
            'instinct':{'native_plan':'SleepAtHome'}}}},100))
