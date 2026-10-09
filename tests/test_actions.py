import asyncio
from dataclasses import replace
import json
import unittest

from bwv.actions import (ActionIntent, PickupTarget, decode_intent, parse_intent,
    eligible_pickups, intent_options, resolve_intent, target_options)
from bwv.control import ControlGate
from bwv.mock import MockProvider
from bwv.planner import CreaturePlanner
from bwv.prompt import preview_prompt
from bwv.surroundings import NearbyObject, SurroundingsContext
from test_control import playable


INTENT = ActionIntent('pick_up','tree')


def tree(name, distance, **changes):
    return replace(PickupTarget(name,'instance-'+name,'tree','pickup:'+name,distance,True,True), **changes)


def candidates(targets):
    return tuple(t.candidate for t in targets)


class ResolutionTests(unittest.TestCase):
    def test_strict_input_rejects_extra_fields_addresses_duplicates_and_unknown_actions(self):
        self.assertEqual(decode_intent(json.dumps(INTENT.as_dict())),INTENT)
        for bad in ({'action':'destroy','target':{'kind':'tree','selection':'nearest_reachable'}},
                    {'action':'pick_up','target':{'kind':'tree','selection':'nearest_reachable','address':123}},
                    {'action':'pick_up','target':{'kind':'tree','selection':'nearest_reachable'},'ignore_pause':True},
                    {'action':'pick_up','target':{'kind':'tree','selection':'specific'}},
                    {'action':'pick_up','target':{'kind':'tree','selection':False}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_intent(bad)
        with self.assertRaises(ValueError):decode_intent('{"action":"pick_up","action":"destroy"}')
        with self.assertRaises(ValueError):decode_intent(' '*2049)

    def test_selects_nearest_eligible_with_order_independent_tie_break(self):
        objects=(tree('blocked',1,reachable=False),tree('unknown',2,pickup_allowed=None),
                 tree('far',9),tree('b',4),tree('a',4))
        for ordering in (objects,tuple(reversed(objects))):
            result=resolve_intent(INTENT,objects,ordering,candidates(objects),candidates(objects))
            self.assertEqual(result.status,'resolved')
            self.assertEqual(result.action.target.object_id,'a')

    def test_new_objects_recycled_identity_and_withdrawn_candidates_are_rejected(self):
        old=(tree('one',2),)
        for current in ((),(tree('new',1),),(replace(old[0],instance_id='replacement'),),
                        (replace(old[0],reachable=None),),(replace(old[0],kind='rock'),)):
            result=resolve_intent(INTENT,old,current,candidates(old),candidates(current))
            self.assertEqual(result.status,'no_suitable_target')
        self.assertEqual(resolve_intent(INTENT,old,old,candidates(old),()).status,'no_suitable_target')

    def test_specific_target_never_falls_back_to_nearest_and_details_are_bounded(self):
        objects=tuple(tree('tree-'+str(i),i+1) for i in range(10))
        explicit=ActionIntent('pick_up','tree','specific','tree-8')
        result=resolve_intent(explicit,objects,objects,candidates(objects),candidates(objects))
        self.assertEqual(result.action.target.object_id,'tree-8')
        missing=replace(explicit,target_id='missing')
        self.assertEqual(resolve_intent(missing,objects,objects,candidates(objects),candidates(objects)).status,
                         'no_suitable_target')
        options=target_options('tree',objects,candidates(objects))
        self.assertEqual(len(options),5)
        self.assertEqual(set(options[0]),{'id','kind','distance_engine_units'})

    def test_observation_is_not_a_capability_and_prompt_only_offers_eligible_kinds(self):
        obs=playable(candidates=(),surroundings=SurroundingsContext(True,(NearbyObject('one','tree',2),)))
        self.assertNotIn('available_intents',preview_prompt(obs,None).context_json)
        objects=(tree('yes',2),tree('no',1,reachable=None))
        obs=replace(obs,pickup_targets=objects,candidates=(*candidates(objects),'rest:home'))
        payload=json.loads(preview_prompt(obs,None).context_json)
        self.assertEqual(payload['available_intents'],[INTENT.as_dict()])
        self.assertEqual(payload['candidates'],['rest:home'])
        self.assertNotIn('pickup:yes',json.dumps(payload))
        self.assertEqual(intent_options((objects[1],),candidates(objects)),[])
        with self.assertRaises(ValueError):eligible_pickups((objects[0],objects[0]),candidates(objects))


class PlannerIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_intent_uses_current_distance_then_keeps_chosen_identity(self):
        now=[0];release=asyncio.Event();objects=(tree('a',2),tree('b',4))
        provider=MockProvider(reply=INTENT.as_dict(),release=release)
        planner=CreaturePlanner(provider,clock=lambda:now[0],gate=ControlGate(resume_delay=0))
        try:
            planner.update(playable(pickup_targets=objects,candidates=candidates(objects)))
            planner.tick();await provider.entered.wait()
            now[0]=.2
            current=(replace(objects[0],distance_engine_units=5),objects[1])
            planner.update(playable(.2,pickup_targets=current,candidates=candidates(current)))
            release.set();await planner._task
            self.assertEqual(planner.last_resolution,'resolved')
            proposal=planner.take_proposal()
            self.assertEqual(proposal.candidate,'pickup:b')
            self.assertEqual(proposal.resolved_action.target.object_id,'b')
            self.assertEqual(len(provider.requests),1)
        finally:release.set();await planner.close()

    async def test_pending_target_loss_or_story_lock_revokes_without_retargeting(self):
        for change in ('target','instance','permission','reachability','pause','cutscene','temple','script','stale'):
            now=[0];objects=(tree('a',2),tree('b',4))
            planner=CreaturePlanner(MockProvider(reply=INTENT),clock=lambda:now[0],gate=ControlGate(resume_delay=0))
            try:
                obs=playable(pickup_targets=objects,candidates=candidates(objects))
                planner.update(obs);planner.tick();await planner._task
                self.assertIsNotNone(planner._proposal)
                now[0]=.2
                edits={'observed_at':.2,'sequence':2}
                if change=='target':edits['pickup_targets']=(objects[1],)
                elif change=='instance':edits['pickup_targets']=(replace(objects[0],instance_id='reused'),objects[1])
                elif change=='permission':edits['pickup_targets']=(replace(objects[0],pickup_allowed=False),objects[1])
                elif change=='reachability':edits['pickup_targets']=(replace(objects[0],reachable=None),objects[1])
                elif change=='pause':edits['paused']=True
                elif change=='cutscene':edits['cutscene']=True
                elif change=='temple':edits['in_temple']=True
                elif change=='script':edits['script_owns_creature']=True
                else:now[0]=5;edits['observed_at']=0
                planner.update(replace(obs,**edits))
                with self.subTest(change=change):self.assertIsNone(planner.take_proposal())
            finally:await planner.close()

    async def test_exact_candidate_cannot_bypass_pickup_checks_and_null_is_no_override(self):
        for reply in ('pickup:a',{'candidate':'pickup:a'},INTENT.as_dict(),{'candidate':None}):
            objects=(tree('a',2,reachable=None),)
            planner=CreaturePlanner(MockProvider(reply=reply),clock=lambda:0,gate=ControlGate(resume_delay=0))
            try:
                planner.update(playable(pickup_targets=objects,candidates=(*candidates(objects),'rest:home')))
                planner.tick();await planner._task
                with self.subTest(reply=reply):self.assertIsNone(planner.take_proposal())
                if reply=={'candidate':None}:self.assertEqual(planner.last_resolution,'no_override')
            finally:await planner.close()

    async def test_intent_response_after_session_change_is_discarded(self):
        now=[0];release=asyncio.Event();objects=(tree('a',2),)
        provider=MockProvider(reply=INTENT,release=release,ignore_cancel=True)
        planner=CreaturePlanner(provider,clock=lambda:now[0],gate=ControlGate(resume_delay=0))
        try:
            planner.update(playable(pickup_targets=objects,candidates=candidates(objects)))
            planner.tick();await provider.entered.wait()
            now[0]=.2
            planner.update(playable(.2,session='new',pickup_targets=objects,candidates=candidates(objects)))
            await asyncio.sleep(0);release.set();await planner._task
            self.assertIsNone(planner.take_proposal())
            self.assertEqual(planner.results_discarded,1)
        finally:release.set();await planner.close()
