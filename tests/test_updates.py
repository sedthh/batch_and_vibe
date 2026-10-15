from dataclasses import replace
import json
import unittest

from bwv.control import GateStatus
from bwv.creature import CreatureContext
from bwv.holding import HoldingContext
from bwv.leash import LeashContext
from bwv.miracle import HealKnowledge, MiracleContext
from bwv.prompt import preview_prompt, SYSTEM
from bwv.training import TrainingContext, TrainingFeedback
from bwv.updates import ContextUpdates
from bwv.surroundings import NearbyObject, SurroundingsContext
from test_control import playable


class CompactContextTests(unittest.TestCase):
    def test_species_is_stable_system_data_and_refreshes_on_form_change(self):
        obs = playable(candidates=(), creature=CreatureContext('monkey','engine'))
        monkey = preview_prompt(obs,None)
        self.assertEqual(monkey.context_json, '{}')
        self.assertFalse(monkey.has_context)
        self.assertIn('"species":"monkey"', monkey.system)
        active = preview_prompt(replace(obs, holding=HoldingContext(False,hunger_desire=1)),None)
        self.assertEqual(active.system, monkey.system)
        horse = preview_prompt(replace(obs,creature=CreatureContext('horse','engine')),None)
        self.assertIn('"species":"horse"',horse.system)
        self.assertNotIn('monkey',horse.system)
        self.assertEqual(horse.context_json,'{}')
        unknown = preview_prompt(replace(obs,creature=CreatureContext()),None)
        self.assertEqual(unknown.system,SYSTEM)

    def test_only_active_facts_reach_model_while_candidates_remain_exact(self):
        obs = playable(creature=CreatureContext('monkey','engine'), leash=LeashContext(False, attachment='none'),
            holding=HoldingContext(False, hunger_desire=0),
            miracles=MiracleContext(heal_knowledge=HealKnowledge(7,15.45)))
        payload = json.loads(preview_prompt(obs,None).context_json)
        self.assertEqual(payload, {'candidates':list(obs.candidates)})
        # Compactness budget for the base prompt, not a provider input-token cap.
        # Includes game background, roleplay and autonomous decision guidance.
        self.assertLess(len(SYSTEM),12500)
        learned = replace(obs, miracles=MiracleContext(heal_knowledge=HealKnowledge(16,15.45)))
        self.assertEqual(json.loads(preview_prompt(learned,None).context_json)['learned_miracles'], ['heal'])

    def test_attention_does_not_become_feedback_and_unknown_held_object_is_not_invented(self):
        obs = playable(training=TrainingContext(True), holding=HoldingContext(True,'opaque'))
        payload = json.loads(preview_prompt(obs,None).context_json)
        self.assertNotIn('feedback',payload)
        self.assertIn('You are holding an unidentified object.',payload['happening'])
        self.assertNotIn('opaque',json.dumps(payload))


class StatusUpdateTests(unittest.TestCase):
    def test_body_drift_uses_heartbeat_but_health_event_and_native_plan_trigger_update(self):
        from bwv.experience import Experience, BodyState, NativeInstinct, HealthChange
        scheduler=ContextUpdates();gate=GateStatus(True,(),0)
        for now,experience,expected in (
                (0,Experience(BodyState(health=1,energy=.5)), 'preview_prepared'),
                (3,Experience(BodyState(health=1,energy=.49)), 'waiting'),
                (4,Experience(BodyState(health=.8),health_changes=(HealthChange('damage','decreased',.2),)), 'preview_prepared'),
                (6,Experience(BodyState(health=.8),NativeInstinct(11,'Eat')), 'preview_prepared')):
            obs=playable(now,experience=experience)
            result=scheduler.update(obs,preview_prompt(obs,None),gate,now)
            self.assertEqual(result['state'],expected)

    def test_continuous_native_drives_ride_heartbeat_without_event_flood(self):
        from bwv.drives import CreatureDrive
        scheduler=ContextUpdates();gate=GateStatus(True,(),0)
        for now,value,expected in ((0,.2,'preview_prepared'),(3,.3,'waiting'),(15,.4,'preview_prepared')):
            obs=playable(now,holding=HoldingContext(False,drives=(CreatureDrive('tiredness',value),)))
            result=scheduler.update(obs,preview_prompt(obs,None),gate,now)
            self.assertEqual(result['state'],expected)
        self.assertEqual(result['last_update']['trigger'],'heartbeat')
        self.assertEqual(result['last_update']['context']['instinct_motivations_engine_units'],{'tiredness':.4})

    def test_nearby_motion_and_equivalent_entities_do_not_trigger_events(self):
        scheduler=ContextUpdates();gate=GateStatus(True,(),0)
        for now,identity,distance,expected in ((0,'tree-one',10,'preview_prepared'),
                (3,'tree-one',11,'waiting'),(4,'tree-two',11,'waiting'),
                (15,'tree-two',12,'preview_prepared')):
            obs=playable(now,surroundings=SurroundingsContext(True,
                (NearbyObject(identity,'tree',distance),)))
            result=scheduler.update(obs,preview_prompt(obs,None),gate,now)
            self.assertEqual(result['state'],expected)

    def test_empty_heartbeat_and_species_change_never_prepare_update(self):
        scheduler = ContextUpdates()
        gate = GateStatus(True,(),0)
        for now, species in ((0,'monkey'),(15,'monkey'),(30,'horse')):
            obs = playable(now,candidates=(),creature=CreatureContext(species,'engine'))
            result = scheduler.update(obs,preview_prompt(obs,None),gate,now)
            self.assertEqual(result['state'],'skipped_empty')
            self.assertIsNone(result['last_update'])
        obs = playable(31,candidates=(),creature=CreatureContext('horse','engine'),
                       holding=HoldingContext(False,hunger_desire=1))
        result = scheduler.update(obs,preview_prompt(obs,None),gate,31)
        self.assertEqual(result['last_update']['number'],1)
        self.assertEqual(result['last_update']['context'],{'hunger_motivation_engine_units':1})
        obs = playable(46,candidates=())
        result = scheduler.update(obs,preview_prompt(obs,None),gate,46)
        self.assertEqual(result['state'],'skipped_empty')
        self.assertEqual(result['last_update']['number'],1)

    def test_event_coalescing_heartbeat_and_delivery_block_are_separate(self):
        scheduler = ContextUpdates()
        blocked = GateStatus(False,('unknown_control',),0)
        def update(now, **changes):
            obs = playable(now, **changes)
            return scheduler.update(obs,preview_prompt(obs,None),blocked,now)
        first = update(0)
        self.assertEqual(first['last_update']['trigger'],'initial_or_resumed')
        self.assertEqual(first['last_update']['delivery'],'preview_only_not_sent')
        self.assertFalse(first['last_update']['control_gate_allowed_at_preparation'])
        self.assertEqual(update(1, holding=HoldingContext(False,hunger_desire=1))['state'],'waiting')
        feedback = TrainingContext(True,(TrainingFeedback('new','praise',1,magnitude=.1),))
        self.assertEqual(update(1.5,training=feedback)['state'],'waiting')
        event = update(2,training=feedback)['last_update']
        self.assertEqual(event['trigger'],'context_changed')
        self.assertEqual(update(16,training=feedback)['state'],'waiting')
        self.assertEqual(update(17,training=feedback)['last_update']['trigger'],'heartbeat')

    def test_control_locks_suspend_preparation_and_identity_reset_discards_old_context(self):
        for lock in ({'paused':True},{'cutscene':True},{'script_owns_creature':True},
                     {'loading':True},{'player_has_control':False},{'creature_id':None}):
            scheduler = ContextUpdates()
            gate = GateStatus(False,('unknown_control',),0)
            obs = playable(0)
            scheduler.update(obs,preview_prompt(obs,None),gate,0)
            obs = playable(20,**lock)
            self.assertEqual(scheduler.update(obs,preview_prompt(obs,None),gate,20)['state'],'suspended')
            obs = playable(21,creature_id='new',creature=CreatureContext('horse','engine'))
            result = scheduler.update(obs,preview_prompt(obs,None),gate,21)
            self.assertEqual(result['last_update']['trigger'],'initial_or_resumed')
            self.assertIn('"species":"horse"', preview_prompt(obs,None).system)
            self.assertNotIn('creature', result['last_update']['context'])
