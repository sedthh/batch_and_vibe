from dataclasses import replace
import json
import unittest

from bwv.control import ControlGate
from bwv.holding import HoldingContext
from bwv.mock import MockProvider
from bwv.planner import CreaturePlanner
from bwv.training import TrainingContext, TrainingFeedback
from test_training import observation


class HoldingTests(unittest.TestCase):
    def test_compact_drives_are_bounded_and_do_not_duplicate_hunger(self):
        from bwv.drives import DRIVES, CreatureDrive
        from bwv.prompt import preview_prompt
        drives=tuple(CreatureDrive(name,i/10) for i,name in enumerate(DRIVES))
        context=HoldingContext(False,hunger_desire=.4,drives=drives)
        data=json.loads(preview_prompt(observation(holding=context),None).context_json)
        self.assertEqual(len(data['instinct_motivations_engine_units']),6)
        self.assertNotIn('hunger',data['instinct_motivations_engine_units'])
        self.assertEqual(data['hunger_motivation_engine_units'],.4)
        empty=HoldingContext(False,drives=(CreatureDrive('tiredness',0),))
        self.assertNotIn('instinct_motivations_engine_units',json.loads(preview_prompt(observation(holding=empty),None).context_json))
        with self.assertRaises(ValueError):HoldingContext(drives=(drives[0],drives[0]))

    def test_partial_learning_and_miracle_plan_coexist_without_claiming_success(self):
        from bwv.miracle import MiracleContext, MiracleKnowledge
        from bwv.prompt import preview_prompt
        obs = observation(holding=HoldingContext(False, miracle_plan='water'),
            miracles=MiracleContext(knowledge=(MiracleKnowledge('water', 9, 100, 9.27),)))
        data = json.loads(preview_prompt(obs, None).context_json)
        self.assertEqual(data['current_miracle_plan'], 'water')
        self.assertEqual(data['learning_miracles'], ['water'])
        self.assertNotIn('learned_miracles', data)
        self.assertNotIn('learning_events', data)
        with self.assertRaises(ValueError): HoldingContext(miracle_plan='made_up')

    def test_empty_unknown_and_unidentified_object_are_distinct(self):
        self.assertIsNone(HoldingContext().holding)
        self.assertFalse(HoldingContext(False).holding)
        self.assertIsNone(HoldingContext(True, 'opaque-item').kind)
        for fields in (dict(holding=True), dict(holding=False, kind='food_pile'),
                       dict(holding=False, item_id='old'), dict(hunger_desire=float('nan'))):
            with self.assertRaises(ValueError):
                HoldingContext(**fields)

    def test_plan_survives_empty_hand_without_claiming_consumption(self):
        context = HoldingContext(False, active_plan='eat', hunger_desire=1.9)
        self.assertEqual(context.active_plan, 'eat')
        self.assertIsNone(context.kind)
        self.assertEqual(context.hunger_desire, 1.9)


class HoldingPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_praise_and_held_food_reach_prompt_without_invented_attribution(self):
        context = HoldingContext(True, 'food-1', 'food_pile', 'eat', 1.9)
        training = TrainingContext(True, (TrainingFeedback('praise-1','praise',0,magnitude=.1),))
        provider = MockProvider()
        planner = CreaturePlanner(provider, clock=lambda:0, gate=ControlGate(resume_delay=0))
        try:
            obs = observation(holding=context,training=training)
            self.assertTrue(planner.update(obs).allowed)
            planner.tick(); await planner._task
            data = json.loads(provider.requests[0].to_prompt().context_json)
            self.assertIn('You are holding a food pile.', data['happening'])
            self.assertEqual(data['hunger_motivation_engine_units'], 1.9)
            self.assertNotIn('behavior', data['feedback'][0])
            self.assertIsNone(planner.take_proposal())
            for lock in (dict(paused=True),dict(cutscene=True),dict(script_owns_creature=True),
                         dict(player_has_control=None),dict(supported=False)):
                self.assertFalse(ControlGate(resume_delay=0).update(replace(obs,**lock),0).allowed)
        finally:
            await planner.close()
