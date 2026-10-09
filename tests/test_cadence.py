from dataclasses import replace
import json
import unittest

from bwv.cadence import AdaptiveUpdates, CadenceSettings, activity_tier
from bwv.control import GateStatus
from bwv.prompt import CreaturePrompt
from test_control import playable


class CadenceTests(unittest.TestCase):
    def test_sleep_suppresses_events_and_resumes_with_current_world(self):
        from test_sleep import sleep_frame
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        def update(now, sleeping, context, unknown=False):
            frame=sleep_frame({'observation':{'observed_at':now}},sleeping)
            obs=replace(playable(now),session=frame['observation']['session'],
                        creature_id=frame['observation']['creature_id'])
            if unknown:frame['diagnostics']['action_probe']['activity']['verified']=False
            return self.update(scheduler,now,context,source=frame,observation=obs)
        update(0,False,{'people_nearby':1})
        event={'action_results':[{'event_id':'result','status':'completed'}]}
        self.assertEqual(update(1,True,event)['state'],'suspended_sleep')
        self.assertEqual(update(300,True,event)['state'],'suspended_sleep')
        scheduler.conversation_activity(301,{'sleeping':True},'Test system')
        self.assertEqual(update(302,False,event,unknown=True)['state'],'suspended_sleep')
        resumed=update(303,False,{'people_nearby':7})
        self.assertEqual(resumed['last_update']['trigger'],'initial_or_resumed')
        self.assertEqual(resumed['last_update']['context'],{'people_nearby':7})
        self.assertEqual(resumed['last_update']['number'],2)

    def update(self, scheduler, now, context=None, **kwargs):
        context={'surroundings':{'trees_nearby':3}} if context is None else context
        obs=kwargs.pop('observation',playable(now))
        gate=kwargs.pop('gate',GateStatus(True,(),0))
        source=kwargs.pop('source',{'observation':{'surroundings':{'available':True}}})
        return scheduler.update(obs,CreaturePrompt('Test system',json.dumps(context)),gate,now,source=source,**kwargs)

    def test_tiers_do_not_invent_attention_or_miracle_activity(self):
        frame={'observation':{'surroundings':{'available':True}}}
        self.assertEqual(activity_tier(frame,{'surroundings':{'miracle_bubbles_nearby':8}})[0],'quiet')
        self.assertEqual(activity_tier(frame,{'surroundings':{'people_nearby':10}})[0],'settlement')
        self.assertEqual(activity_tier(frame,{'surroundings':{'other_creatures_nearby':1}})[0],'active')
        self.assertEqual(activity_tier({},{}),('settlement','surroundings_or_attention_unknown'))
        frame['observation']['attention']={'hand_far':True,'player_idle':True,'verified':False}
        self.assertEqual(activity_tier(frame,{})[0],'quiet')
        frame['observation']['attention']['verified']=True
        self.assertEqual(activity_tier(frame,{})[0],'distant')

    def test_jitter_is_bounded_and_sampled_once_per_deadline(self):
        samples=[]
        def uniform(lo,hi):samples.append((lo,hi));return hi
        scheduler=AdaptiveUpdates(uniform=uniform)
        first=self.update(scheduler,0)
        self.assertEqual(first['interval_seconds'],144)
        for t in range(1,20):self.update(scheduler,t)
        self.assertEqual(samples,[(.8,1.2)])
        unchanged=self.update(scheduler,144)
        self.assertEqual(unchanged['state'],'skipped_unchanged')
        self.assertEqual(len(samples),2)

    def test_unchanged_and_native_plan_churn_do_not_call_but_real_change_does(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        context={'body_engine_units':{'energy':.61},'instinct':{'native_plan':'Walk'}}
        self.update(scheduler,0,context)
        context={'body_engine_units':{'energy':.609},'instinct':{'native_plan':'LookConfused'}}
        self.assertEqual(self.update(scheduler,120,context)['state'],'skipped_unchanged')
        context['body_engine_units']['energy']=.3
        changed=self.update(scheduler,240,context)
        self.assertEqual(changed['last_update']['trigger'],'adaptive_update')
        self.assertEqual(changed['last_update']['context'],context)

    def test_important_events_deduplicate_and_clear_without_extra_call(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        self.update(scheduler,0)
        context={'feedback':[{'event_id':'new','kind':'praise'}]}
        self.assertEqual(self.update(scheduler,5,context)['state'],'waiting')
        self.assertEqual(self.update(scheduler,10,context)['last_update']['trigger'],'important_event')
        self.assertEqual(self.update(scheduler,20,context)['state'],'waiting')
        self.assertEqual(self.update(scheduler,21)['state'],'waiting')

    def test_slowdown_waits_for_stable_scene_and_speedup_is_immediate(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        active={'surroundings':{'other_creatures_nearby':1}}
        self.assertEqual(self.update(scheduler,0,active)['tier'],'active')
        self.assertEqual(self.update(scheduler,1)['tier'],'active')
        self.assertEqual(self.update(scheduler,5)['tier'],'active')
        self.assertEqual(self.update(scheduler,6)['tier'],'quiet')
        self.assertEqual(self.update(scheduler,7,active)['tier'],'active')

    def test_busy_question_reset_empty_and_resume_use_fresh_snapshot(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        self.update(scheduler,0)
        self.assertEqual(self.update(scheduler,140,{'surroundings':{'trees_nearby':9}},busy=True)['state'],
                         'conversation_in_progress')
        scheduler.conversation_activity(140,{'surroundings':{'trees_nearby':9}},'Test system')
        scheduler.conversation_activity(150)
        self.assertEqual(scheduler.deadline,270)
        self.assertEqual(self.update(scheduler,160,{})['state'],'skipped_empty')
        self.assertEqual(self.update(scheduler,170,gate=GateStatus(False,('cutscene',),1))['state'],'suspended')
        result=self.update(scheduler,180,{'surroundings':{'trees_nearby':2}})
        self.assertEqual(result['last_update']['trigger'],'initial_or_resumed')
        self.assertEqual(result['last_update']['context']['surroundings']['trees_nearby'],2)
        stale=replace(playable(180),observed_at=170)
        self.assertEqual(self.update(scheduler,180,observation=stale)['state'],'suspended')

    def test_settings_validation(self):
        for data in ({'active_seconds':0},{'quiet_seconds':20},{'jitter_percent':51},
                     {'active_seconds':True},{'jitter_percent':float('nan')},{'secret':'bad'}):
            with self.subTest(data=data),self.assertRaises(ValueError):CadenceSettings.parse(data)
        self.assertEqual(CadenceSettings.parse({'jitter_percent':0}).distant_seconds,240)

    def test_burning_overrides_only_slower_tiers_and_keeps_jitter(self):
        for source,nearby in (
            ({'observation':{'surroundings':{'available':True}}},{}),
            ({'observation':{}},{'people_nearby':3}),
            ({'observation':{'attention':{'verified':True,'hand_far':True}}},{}),
        ):
            scheduler=AdaptiveUpdates(uniform=lambda lo,hi:hi)
            context={'self':{'on_fire':True},'surroundings':nearby}
            result=self.update(scheduler,0,context,source=source)
            self.assertEqual(result['tier'],'active')
            self.assertEqual(result['reason'],'burning_or_receiving_damage')
            self.assertEqual(result['interval_seconds'],36)
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        context={'self':{'on_fire':True},'surroundings':{'other_creatures_nearby':1}}
        result=self.update(scheduler,0,context)
        self.assertEqual(result['reason'],'interaction_or_creature_activity')
        self.assertEqual(result['interval_seconds'],30)

    def test_current_health_loss_speeds_up_without_replaying_old_damage(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        def context(health):
            return {'self':{'health_engine_units':health},'events':{
                'health_changes':[{'event_id':'old','direction':'decreased'}]}}
        self.assertEqual(self.update(scheduler,0,context(.5))['tier'],'quiet')
        result=self.update(scheduler,1,context(.4))
        self.assertEqual(result['tier'],'active')
        self.assertEqual(result['next_update_in_seconds'],29)
        # Repeated snapshot/history and low health cannot prolong the override.
        self.update(scheduler,5,context(.4))
        self.update(scheduler,6,context(.4))
        self.assertEqual(self.update(scheduler,11,context(.4))['tier'],'quiet')
        # A health decrease across a telemetry gap is not ongoing damage.
        self.assertEqual(self.update(scheduler,20,context(.2))['tier'],'quiet')
        # A new fresh decrease reactivates it, healing does not.
        self.assertEqual(self.update(scheduler,21,context(.1))['tier'],'active')
        self.update(scheduler,26,context(.3))
        self.assertEqual(self.update(scheduler,31,context(.3))['tier'],'quiet')

    def test_danger_does_not_bypass_menu_gate_or_survive_creature_change(self):
        scheduler=AdaptiveUpdates(CadenceSettings(jitter_percent=0))
        burning={'self':{'on_fire':True,'health_engine_units':1}}
        result=self.update(scheduler,0,burning,gate=GateStatus(False,('menu',),1))
        self.assertEqual(result['state'],'suspended')
        self.update(scheduler,1,{'self':{'health_engine_units':1}})
        other=replace(playable(2),creature_id='different')
        result=self.update(scheduler,2,{'self':{'health_engine_units':.2}},observation=other)
        self.assertEqual(result['tier'],'quiet')
