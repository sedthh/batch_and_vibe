from dataclasses import asdict, replace
import json
import unittest

from bwv.control import ControlGate
from bwv.leash import LeashContext
from bwv.mock import MockProvider
from bwv.planner import CreaturePlanner
from test_training import observation


class LeashTests(unittest.TestCase):
    def test_unknown_and_inactive_do_not_claim_a_selected_variant(self):
        self.assertIsNone(LeashContext().active)
        self.assertEqual(LeashContext(False, attachment="none").attachment, "none")
        for kwargs in (dict(active=False, attachment="hand"),
                       dict(variant="compassion"), dict(active=1),
                       dict(active=True, attachment="none"),
                       dict(active=True, attachment="hand", target_id="tree")):
            with self.assertRaises(ValueError):
                LeashContext(**kwargs)

    def test_leash_neither_suspends_thought_nor_overrides_control_locks(self):
        leash = LeashContext(True, "learning", "hand")
        obs = observation(leash=leash)
        self.assertTrue(ControlGate(resume_delay=0).update(obs, 0).allowed)
        for lock in (dict(paused=True), dict(cutscene=True), dict(script_owns_creature=True),
                     dict(supported=False), dict(creature_present=False),
                     dict(player_has_control=None)):
            self.assertFalse(ControlGate(resume_delay=0).update(replace(obs, **lock), 0).allowed)


class LeashPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_and_unknown_target_reach_prompt(self):
        for target in (None, "session-object-1"):
            leash = LeashContext(True, "compassion", "object", target, "tree" if target else None)
            provider = MockProvider()
            planner = CreaturePlanner(provider, clock=lambda: 0, gate=ControlGate(resume_delay=0))
            try:
                planner.update(observation(leash=leash))
                planner.tick()
                await planner._task
                request = provider.requests[0]
                self.assertEqual(request.leash, leash)
                payload = json.loads(request.to_prompt().context_json)
                self.assertEqual(payload["leash"].get("target"),
                    {"kind":"tree","object_id":target} if target else None)
                self.assertEqual(payload["leash"]["variant"], "compassion")
                self.assertEqual(planner.reflections_completed, 1)
                self.assertIsNone(planner.take_proposal())
            finally:
                await planner.close()


class LeashInspectionTests(unittest.TestCase):
    def test_target_reaches_tick_and_inspection_with_same_world_id(self):
        from bwv.prompt import preview_prompt
        from bwv.llm_context import summarize_context
        from bwv.model_projection import model_snapshot
        from bwv.direct_tools import DirectCreatureToolSession
        from test_creature_tools import frame
        leash=LeashContext(True,'learning','object','new-town-centre','town_centre')
        f=frame();f['observation']['leash']=asdict(leash)
        context=json.loads(preview_prompt(observation(leash=leash),None).context_json)
        obj={'object_id':'new-town-centre','kind':'town_centre','town_id':'new-town',
             'distance_engine_units':12,'details':{'construction_fraction':.35}}
        f['observation']['surroundings']['known_objects']=[obj]
        compact=model_snapshot(summarize_context(context,f['observation']))
        target=compact['activity']['leash']['target']
        self.assertEqual(target['object_id'],obj['object_id'])
        self.assertEqual(target['type'],'town centre')
        self.assertEqual(target['town_id'],'new-town')
        self.assertEqual(target['distance'],12)
        session=DirectCreatureToolSession(lambda:f)
        result=session.call('inspect_object',{'object_id':target['object_id']})
        self.assertEqual(result['object']['details']['construction_fraction'],.35)
        self.assertFalse(result['executed'])
        # Still identify a verified attachment even before surrounding discovery.
        f['observation']['surroundings']['known_objects']=[]
        result=session.call('inspect_object',{'object_id':target['object_id']})
        self.assertEqual(result['object'],{'object_id':'new-town-centre','type':'town centre'})
        # Once detached, do not recycle the old leash's target.
        f['observation']['leash']=asdict(LeashContext(True,'learning','hand'))
        result=session.call('inspect_object',{'object_id':target['object_id']})
        self.assertEqual(result['status'],'object_not_currently_known')
