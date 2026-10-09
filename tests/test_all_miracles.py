from dataclasses import replace
import json
import unittest

from bwv.miracle import MiracleDetector, SpellSample
from bwv.prompt import preview_prompt
from bwv.spells import SPELLS
from test_miracle import sample
from test_miracle_live import MiracleMemory
from test_control import playable


class AllMiraclesTests(unittest.TestCase):
    def test_all_build_slots_have_independent_learning_and_thresholds(self):
        self.assertEqual(set(SPELLS),set(range(1,42)))
        for kind,name in SPELLS.items():
            if kind == 10:
                continue
            with self.subTest(spell=name):
                detector=MiracleDetector()
                baseline=sample(other_spells=(SpellSample(kind,4,90,18.54),))
                context=detector.update(baseline)
                self.assertEqual(context.learning,())
                self.assertEqual(context.knowledge[0].spell,name)
                current=replace(baseline,observed_at=.2,game_tick=102,
                    other_spells=(SpellSample(kind,5,102,18.54),))
                event,=detector.update(current).learning
                self.assertEqual((event.spell,event.counter_delta),(name,1))
                self.assertEqual(detector.update(replace(current,observed_at=.3)).learning,(event,))

    def test_existing_progress_is_visible_without_replaying_cast_events(self):
        memory=MiracleMemory()
        memory.put(memory.mental+0x17D38+4,4)
        memory.put(memory.mental+0x17DE0+4,90)
        detector=MiracleDetector()
        context=detector.update(memory.sample())
        self.assertEqual(len(context.knowledge),41)
        self.assertEqual(context.learning,())
        data=json.loads(preview_prompt(playable(miracles=context),None).context_json)
        self.assertIn('fireball',data['learning_miracles'])
        self.assertNotIn('learning_events',data)
        self.assertNotIn('fireball',data.get('learned_miracles',[]))

    def test_other_spell_rollback_and_future_timestamp_do_not_emit_false_learning(self):
        for altered in (SpellSample(1,3,90,18),SpellSample(1,5,90,18),SpellSample(1,5,103,18)):
            detector=MiracleDetector()
            detector.update(sample(other_spells=(SpellSample(1,4,90,18),)))
            result=detector.update(sample(observed_at=.2,game_tick=102,other_spells=(altered,)))
            self.assertEqual(result.learning,())
