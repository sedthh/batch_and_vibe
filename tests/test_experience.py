from dataclasses import replace
import json
import unittest

from bwv.experience import BodyState, NativeInstinct, Experience, ExperienceSample, ExperienceTracker, ReceivedEffect
from bwv.prompt import preview_prompt
from test_control import playable


def sample(now=0, health=1, **changes):
    return replace(ExperienceSample('save','creature',now,int(now*100),BodyState(health=health)),**changes)


class ExperienceTests(unittest.TestCase):
    def test_health_delta_has_unknown_cause_and_retained_stable_id(self):
        tracker=ExperienceTracker()
        self.assertEqual(tracker.update(sample(),0).health_changes,())
        changed=tracker.update(sample(.2,.8),.2)
        self.assertEqual(changed.health_changes[0].direction,'decreased')
        self.assertEqual(changed.health_changes[0].attribution,'unknown')
        self.assertAlmostEqual(changed.health_changes[0].amount_engine_units,.2)
        repeated=tracker.update(sample(.3,.8),.3)
        self.assertEqual(repeated.health_changes,changed.health_changes)
        healed=tracker.update(sample(.4,.9),.4)
        self.assertEqual(healed.health_changes[-1].direction,'increased')

    def test_identity_load_gap_and_explicit_pause_reset_never_create_damage(self):
        for changes in ({'session':'other'}, {'creature_id':'other'}, {'game_tick':0}, {'observed_at':5}):
            tracker=ExperienceTracker();tracker.update(sample(1),1)
            changed=sample(1.2,.2,**changes)
            self.assertEqual(tracker.update(changed,changed.observed_at).health_changes,())
        tracker=ExperienceTracker();tracker.update(sample(),0);tracker.reset()
        self.assertEqual(tracker.update(sample(.1,.2),.1).health_changes,())
        with self.assertRaises(ValueError):tracker.update(sample(),5)

    def test_effect_attribution_requires_fresh_target_linked_record_and_is_deduplicated(self):
        tracker=ExperienceTracker()
        old=ReceivedEffect('old','creature',0,'damage','god_player',miracle='fireball_power_up_2')
        self.assertEqual(tracker.update(sample(effects=(old,)),0).received_effects,())
        new=ReceivedEffect('new','creature',.2,'healing','other_creature','friend','heal_power_up_1')
        wrong=replace(new,event_id='wrong',target_id='villager')
        future=replace(new,event_id='future',observed_at=20)
        result=tracker.update(sample(.2,.8,effects=(old,new,wrong,future)),.2)
        self.assertEqual(result.received_effects,(new,))
        self.assertEqual(result.health_changes[0].attribution,'unknown')
        self.assertEqual(tracker.update(sample(.3,.8,effects=(new,)),.3).received_effects,(new,))
        with self.assertRaises(ValueError):replace(new,evidence='nearby_cast')

    def test_old_effects_expire_and_bounded_health_history_does_not_grow_forever(self):
        tracker=ExperienceTracker();tracker.update(sample(),0)
        for i in range(1,101):
            result=tracker.update(sample(i*.2,1-i*.001),i*.2)
        self.assertLessEqual(len(result.health_changes),16)
        for i in range(101,190):
            result=tracker.update(sample(i*.2,.9),i*.2)
        self.assertEqual(result.health_changes,())

    def test_compact_context_distinguishes_native_plan_health_and_spoken_text(self):
        tracker=ExperienceTracker();tracker.update(sample(),0)
        experience=tracker.update(sample(.2,.8,body=BodyState(health=.8,energy=0,on_fire=True),
            instinct=NativeInstinct(11,'Eat')), .2)
        prompt=preview_prompt(playable(experience=experience),None)
        context=json.loads(prompt.context_json)
        self.assertEqual(context['instinct'],{'native_plan':'Eat'})
        self.assertEqual(context['body_engine_units']['energy'],0)
        self.assertTrue(context['on_fire'])
        self.assertNotIn('spoken_thought',context)
        self.assertNotIn('received_effects',context)
        empty=json.loads(preview_prompt(playable(candidates=(),experience=Experience()),None).context_json)
        self.assertEqual(empty,{})
        with self.assertRaises(ValueError):BodyState(health=float('nan'))
        with self.assertRaises(ValueError):NativeInstinct(328,'Invalid')


class DummyConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_passes_new_experience_to_dummy_without_game_action(self):
        from bwv.mock import MockProvider
        from bwv.control import ControlGate
        from bwv.planner import CreaturePlanner
        provider=MockProvider()
        planner=CreaturePlanner(provider,clock=lambda:0,gate=ControlGate(resume_delay=0))
        try:
            planner.update(playable(candidates=(),experience=Experience(BodyState(health=.8),NativeInstinct(11,'Eat'))))
            planner.tick();await planner._task
            current=json.loads(provider.requests[0].to_prompt().context_json)
            self.assertEqual(current['body_engine_units']['health'],.8)
            self.assertEqual(current['instinct']['native_plan'],'Eat')
            self.assertIsNone(planner.take_proposal())
        finally:await planner.close()

    async def test_dummy_exercises_memory_speech_and_direct_question_without_action(self):
        from bwv.mock import MockConversationProvider
        from bwv.conversation import ConversationBroker, ConversationMemory
        from bwv.control import ControlGate
        from bwv.qa import Inspector
        provider=MockConversationProvider([{'answer':'Synthetic answer',
            'decision':{'candidate':'invented'},'memory':'Synthetic QA note','say':'Synthetic speech'}])
        gate=ControlGate(resume_delay=0);gate.update(playable(),0)
        memory=ConversationMemory()
        broker=ConversationBroker(provider,gate,memory=memory,clock=lambda:0)
        try:
            reply=await broker.ask('How are you?')
            self.assertEqual(reply['decision'],{'candidate':None})
            inspector=Inspector(memory=memory,speech=broker.speech)
            inspector.update(playable(),{},None,None,0)
            frame=inspector.payload()['current']
            self.assertEqual(frame['memory']['memory'],'Synthetic QA note')
            self.assertEqual(frame['speech']['current']['text'],'Synthetic speech')
            self.assertEqual(frame['memory']['persistence'],'session_only')
            self.assertEqual(len(provider.requests),1)
        finally:await broker.close()
