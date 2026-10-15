import asyncio
from dataclasses import asdict
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from bwv.errors import ChatNotSubmitted
from urllib.parse import urlsplit

from bwv.control import ControlObservation
from bwv.creature import CreatureContext
from bwv.qa import make_server
from bwv.qa_live import InspectorSource, LiveDummyRuntime
from bwv.dispatch import ActionOffer, DecisionDispatcher
from bwv.profile_names import ProfileNames


class LiveDummyTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_observer_frame_stays_read_only_and_still_expires(self):
        from copy import deepcopy
        from unittest.mock import patch
        self.runtime.automatic=False
        frame=self.frame(context={'surroundings':{'trees_nearby':2}})
        original=deepcopy(frame)
        copy=deepcopy
        def checked_copy(value,*args,**kwargs):
            self.assertIsNot(value,frame,'Internal ingest copied the whole observation')
            return copy(value,*args,**kwargs)
        with patch('bwv.qa_live.deepcopy',side_effect=checked_copy):
            self.runtime.ingest(frame,copy_source=False)
            await self.runtime.step()
        self.assertIs(self.runtime.source,frame)
        self.assertEqual(frame,original)
        exported=self.runtime.payload()
        exported['source']['prompt']['context']['surroundings']['trees_nearby']=999
        self.assertEqual(frame,original)
        old_stamp=self.runtime.gate.observation.observed_at
        self.now+=6
        self.runtime.ingest(frame,copy_source=False)
        self.assertFalse(self.runtime.gate.poll(self.now).allowed)
        self.assertIsNone(self.runtime.source)
        self.assertEqual(frame['observation']['observed_at'],old_stamp)
        self.assertEqual(frame,original)
        self.runtime.ingest(self.frame(paused=True),copy_source=False)
        self.assertTrue(self.runtime.gate.observation.paused)

    async def test_default_ingest_keeps_mutable_callers_isolated(self):
        frame=self.frame(context={'surroundings':{'trees_nearby':2}})
        self.runtime.ingest(frame)
        frame['prompt']['context']['surroundings']['trees_nearby']=999
        self.assertEqual(self.runtime.source['prompt']['context']['surroundings']['trees_nearby'],2)

    async def test_island_roster_in_system_is_stable_across_hand_motion_and_replaced_on_change(self):
        from copy import deepcopy
        from bwv.island import IslandContext
        self.runtime.automatic=False
        roster=asdict(IslandContext(True,({'god_id':'god_0','is_player':True},
            {'god_id':'god_1','name':'Example ally','relation_to_player':'ally',
             'hand':{'position':{'x':100,'z':200},'distance':25},'alignment':.2}),True))
        self.runtime.ingest(self.frame(island=roster))
        first,current=self.runtime._prompt(self.runtime.gate.observation,self.now,question='Who is nearby?')
        self.assertEqual(first.count('## Current island gods and registered creatures (data)'),1)
        self.assertEqual(current['snapshot']['other_gods'][0]['hand']['distance'],25)
        self.assertNotIn('name',current['snapshot']['other_gods'][0])
        moved=deepcopy(roster);moved['gods'][1]['hand']['position']['x']=110;moved['gods'][1]['alignment']=.3
        self.runtime.ingest(self.frame(island=moved))
        second,_=self.runtime._prompt(self.runtime.gate.observation,self.now,question='Who is nearby?')
        self.assertEqual(first,second)
        removed=deepcopy(roster);removed['gods']=removed['gods'][:1]
        self.runtime.ingest(self.frame(island=removed))
        third,current=self.runtime._prompt(self.runtime.gate.observation,self.now,question='Who remains?')
        self.assertNotIn('Example ally',third)
        self.assertNotIn('other_gods',current['snapshot'])
        self.runtime.ingest(self.frame(island=asdict(IslandContext())))
        fourth,current=self.runtime._prompt(self.runtime.gate.observation,self.now,question='Who remains?')
        self.assertNotIn('## Current island gods and registered creatures (data)',fourth)

    async def test_god_hand_contents_survive_movement_enrichment_and_clear_next_frame(self):
        from unittest.mock import patch
        self.runtime.automatic=False
        for contents in ({'contents':'holding_object','held_object':{'kind':'tree','object_id':'god-tree'}},
                         {'contents':'activated_miracle','activated_miracle':'water'},
                         {'contents':'empty'}, {}):
            f=self.frame(context={'surroundings':{'trees_nearby':2},'surroundings_also_near_god_hand':True},
                         god_hand=contents)
            self.runtime.ingest(f)
            with patch('bwv.qa_live.god_hand_context',return_value={'target_id':'god_hand','distance_engine_units':5}), \
                    patch('bwv.qa_live.creature_position_context',return_value={'x':1,'z':2}):
                _,current=self.runtime._prompt(self.runtime.gate.observation,self.now,question='What am I holding?')
            hand=current['snapshot']['god_hand']
            self.assertEqual(hand['target_id'],'god_hand')
            self.assertEqual(hand.get('contents'),contents.get('contents'))
            self.assertEqual(hand.get('activated_miracle'),contents.get('activated_miracle'))
            self.assertEqual(bool(hand.get('held_object')),bool(contents.get('held_object')))
            self.assertTrue(current['snapshot']['surroundings_also_near_god_hand'])

    async def test_holding_miracle_bubble_does_not_stop_live_service(self):
        from test_held_miracle import HeldMiracleHarness
        h=HeldMiracleHarness()
        self.runtime.automatic=False
        frame=self.frame();frame['diagnostics']['action_probe']=h.probe
        self.runtime.ingest(frame)
        await self.runtime.step()
        held=self.runtime.payload()['current']['model_context']['self']['held_object']
        self.assertEqual(held['type'],'miracle bubble')
        self.assertEqual(held['miracle']['spell'],'water')
        self.assertFalse(held['miracle']['cast_success_guaranteed'])
        answer=await self.runtime.question({'question':'say: Hello'})
        self.assertEqual(answer['reply']['say'],'Hello')
        self.runtime.ingest(self.frame())
        await self.runtime.step()
        self.assertNotIn('held_object',self.runtime.payload()['current']['model_context']['self'])
        self.assertEqual(self.runtime.payload()['runtime']['external_model_calls'],0)

    async def test_turn_waits_for_requested_detail_scan_and_stops_on_scene_change(self):
        from unittest.mock import Mock
        self.runtime.automatic=False
        self.runtime.ingest(self.frame())
        self.runtime.request_details=Mock(return_value=1)
        waiting=asyncio.create_task(self.runtime._wait_for_turn_context())
        await asyncio.sleep(.01)
        self.assertFalse(waiting.done())
        frame=self.frame()
        frame['diagnostics']['detailed_sensors']={'revision':1,'observed_at':self.now}
        self.runtime.ingest(frame)
        await asyncio.wait_for(waiting,1)
        self.runtime.request_details.assert_called_once()
        await self.runtime._refresh_details()
        self.runtime.request_details.assert_called_once()  # Tool rounds reuse a recent scan.
        self.runtime.request_details.return_value=2
        waiting=asyncio.create_task(self.runtime._wait_for_turn_context())
        await asyncio.sleep(.01)
        self.runtime.ingest(self.frame(cutscene=True))
        await asyncio.wait_for(waiting,1)
        self.assertFalse(self.runtime.gate.poll(self.now).allowed)

    async def test_overlay_payload_preserves_display_without_world_or_history(self):
        from bwv.overlay import OverlayModel
        self.runtime.ingest(self.frame(session='123:live'))
        self.runtime.broker.speech.retain_answer('Still here.',self.now)
        self.runtime.publish()
        full=self.runtime.payload()
        small=self.runtime.overlay_payload()
        full_model=OverlayModel();small_model=OverlayModel()
        full_model.update(full,self.now);small_model.update(small,self.now)
        self.assertEqual(full_model,small_model)
        self.assertTrue(small_model.can_send(self.now))
        self.assertEqual(small_model.visible_speech(self.now),'Still here.')
        self.assertNotIn('prompt',small['current'])
        self.assertNotIn('memory',small['current'])
        self.assertNotIn('action_probe',small['source']['diagnostics'])
        self.assertNotIn('surroundings',small['source']['observation'])
        self.assertNotIn('settings',small['runtime'])
        small['current']['speech'].clear()
        self.assertEqual(self.runtime.payload()['current']['speech'],full['current']['speech'])

    async def test_delayed_frame_keeps_reply_identity_and_in_progress_question(self):
        self.runtime.automatic=False
        old=self.frame();self.runtime.ingest(old)
        self.runtime.broker.speech.retain_answer('Still here.',self.now)
        release=asyncio.Event()
        async def respond(request):
            await release.wait()
            return {'answer':'Found it.','decision':{'candidate':None}}
        self.runtime.provider.respond=respond
        task=asyncio.create_task(self.runtime.question({'question':'Find a field'}))
        await self.until(lambda:self.runtime.broker._active is not None)
        epoch=self.runtime.broker._epoch
        self.now+=2;self.runtime.ingest(old);await self.runtime.step()
        self.assertFalse(self.runtime.gate.poll(self.now).allowed)
        self.assertTrue(self.runtime.gate.waiting_for_observation)
        self.assertEqual(self.runtime.broker._epoch,epoch)
        self.assertEqual(self.runtime.payload()['current']['speech']['last_message']['text'],'Still here.')
        release.set();await asyncio.sleep(.06)
        self.assertFalse(task.done())
        self.runtime.ingest(self.frame())
        result=await asyncio.wait_for(task,1)
        self.assertEqual(result['reply']['answer'],'Found it.')
        self.assertEqual(self.runtime.broker.calls_started,1)
        self.assertEqual(self.runtime.broker._epoch,epoch)

    async def test_read_only_tool_waits_for_fresh_context_and_aborts_on_real_scene_change(self):
        from bwv.creature_tools import ToolContextUnavailable
        for interrupted in (False,True):
            old=self.frame();self.runtime.ingest(old);session=self.runtime._tool_session()
            self.now+=2;self.runtime.ingest(old)
            task=asyncio.create_task(session.call_async('inspect_self',{}))
            await asyncio.sleep(.02);self.assertFalse(task.done())
            new=self.frame(cutscene=interrupted)
            new['observation']['experience']['body']['health']=.7
            self.runtime.ingest(new)
            if interrupted:
                with self.assertRaises(ToolContextUnavailable):await task
            else:
                self.assertEqual((await task)['body']['damage'],30)

    async def test_model_preview_matches_wire_names_without_changing_internal_context(self):
        self.runtime.automatic=False
        self.runtime.ingest(self.frame({'body_engine_units':{
            'health':.8,'energy':.7,'exhaustion':.1},'surroundings':{'trees_nearby':3}}))
        await self.runtime.step()
        current=self.runtime.payload()['current']
        self.assertEqual(current['model_context']['self'],{'damage':20,'hunger':30,'tiredness':10})
        self.assertEqual(current['model_context']['surroundings'],{'trees':3})
        self.assertEqual(current['prompt']['context']['self']['health_engine_units'],.8)
        self.assertEqual(current['prompt']['context']['surroundings'],{'trees_nearby':3})
        self.assertEqual(self.runtime.broker.calls_started,0)

    async def test_session_greeting_uses_existing_ticks_and_waits_for_auto_and_awake(self):
        from unittest.mock import patch
        self.runtime.automatic=False
        self.runtime.ingest(self.frame())
        await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,0)
        self.runtime.automatic=True
        with patch('bwv.cadence.sleep_state',return_value=True):
            self.now+=1;self.runtime.ingest(self.frame())
            await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,0)
        with patch('bwv.cadence.sleep_state',return_value=False):
            self.now+=1;self.runtime.ingest(self.frame())
            await self.runtime.step()
            await self.until(lambda:len(self.runtime.memory.history)==1)
        self.assertEqual(self.runtime.provider.requests[-1].current['session_event'],'connection_started')
        self.runtime.ingest(None)
        self.now+=1;self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'Hello again'})
        self.assertNotIn('session_event',self.runtime.provider.requests[-1].current)

    async def test_unknown_camera_blocks_chat_before_provider_then_recovers(self):
        scene=self.frame(cutscene=None)
        scene['diagnostics']['script_camera_owned']=None
        self.runtime.ingest(scene)
        with self.assertRaises(ChatNotSubmitted) as caught:
            await self.runtime.question({'question':'Hello'})
        self.assertEqual(caught.exception.reason,'scene_state_unverified')
        await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,0)
        self.assertEqual(self.runtime.payload()['current']['question_unavailable'],'scene_state_unverified')
        self.now+=1;self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'Hello'})
        self.assertEqual(self.runtime.broker.calls_started,1)

    async def test_stale_cutscene_snapshot_preserves_hide_signal_without_context(self):
        self.runtime.ingest(self.frame())
        scene=self.frame(observed_at=None)
        scene['diagnostics']['script_camera_owned']=True
        self.runtime.ingest(scene)
        await self.runtime.step()
        state=self.runtime.payload()
        self.assertIn('cutscene',state['current']['gate']['reasons'])
        self.assertIsNone(state['source'])
        self.assertEqual(state['current']['prompt']['context'],{})
        with self.assertRaises(ValueError):await self.runtime.question({'question':'Hello'})
        self.assertEqual(self.runtime.broker.calls_started,0)

    async def test_script_ownership_blocks_chat_and_all_ticks_then_resumes_fresh(self):
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':2}},
                                       script_owns_creature=True,cutscene=False))
        await self.runtime.step()
        with self.assertRaises(ChatNotSubmitted) as caught:
            await self.runtime.question({'question':'Hello'})
        self.assertEqual(caught.exception.reason,'scripted_creature')
        with self.assertRaises(ValueError):await self.runtime.force_tick({})
        self.assertEqual(self.runtime.broker.calls_started,0)
        self.assertEqual(self.runtime.payload()['current']['question_unavailable'],'scripted_creature')
        self.assertEqual(self.runtime.payload()['current']['status_updates']['state'],'suspended')
        self.now+=1
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':7}},
                                       script_owns_creature=False,cutscene=False))
        await self.runtime.step()
        await self.until(lambda:len(self.runtime.memory.history)==1)
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],
                         {'surroundings':{'trees_nearby':7}})
        await self.runtime.question({'question':'Hello again'})
        self.assertEqual(self.runtime.broker.calls_started,2)

    async def test_display_block_stops_ticks_questions_and_forced_ticks_then_resumes(self):
        blocked=['fullscreen_overlay_unavailable']
        self.runtime.set_display_blocker(lambda:blocked[0])
        self.runtime.ingest(self.frame())
        await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,0)
        with self.assertRaises(ChatNotSubmitted) as caught:
            await self.runtime.question({'question':'status'})
        self.assertEqual(caught.exception.reason,'fullscreen_overlay_unavailable')
        with self.assertRaises(ValueError):await self.runtime.force_tick({})
        self.assertEqual(self.runtime.broker.calls_started,0)
        self.assertEqual(self.runtime.payload()['current']['question_unavailable'],blocked[0])
        blocked[0]=None;self.now+=1
        self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'status'})
        self.assertEqual(self.runtime.broker.calls_started,1)
        self.assertIsNone(self.runtime.payload()['current']['question_unavailable'])

    async def test_late_target_cancellation_replaces_action_claim_with_one_error(self):
        from unittest.mock import Mock
        self.runtime.automatic=False
        self.runtime.ingest(self.frame())
        self.runtime.broker.speech.publish('Watering now.',self.now,{'candidate':None},source='god_question')
        result={'intent_id':'synthetic-water','candidate':'cast_water_tree',
                'status':'cancelled_miracle_target_changed','queued':False}
        pending=Mock();pending.snapshot.return_value=result
        self.runtime.pending_actions=pending
        await self.runtime.step()
        await self.runtime.step()
        data=self.runtime.payload()
        self.assertEqual(data['current']['speech']['last_message']['text'],
            '[ERROR: Action cancelled before execution: its target is no longer available]')
        errors=[e for e in data['runtime']['errors'] if e['stage']=='action_execution']
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['occurrences'],1)
        self.assertEqual(errors[0]['details'],result)
        self.runtime.pending_actions=None

    async def test_chat_failure_banner_and_debug_record_survive_later_success(self):
        self.runtime.ingest(self.frame())
        original=self.runtime.provider.respond
        async def failed(request):raise ValueError('synthetic-private-error')
        self.runtime.provider.respond=failed
        with self.assertRaises(RuntimeError):await self.runtime.question({'question':'status'})
        data=self.runtime.payload()
        self.assertTrue(data['current']['speech']['last_message']['text'].startswith('[ERROR: '))
        self.assertEqual(data['current']['speech']['last_message']['source'],'error')
        self.assertEqual(data['runtime']['errors'][-1]['stage'],'direct_question')
        self.assertNotIn('synthetic-private-error',json.dumps(data['runtime']['errors']))
        self.runtime.provider.respond=original
        await self.runtime.question({'question':'status'})
        self.assertIsNone(self.runtime.payload()['runtime']['last_chat_error'])
        self.assertTrue(any(e['stage']=='direct_question' for e in self.runtime.payload()['runtime']['errors']))

    async def test_missing_held_item_reports_changed_hand_without_claiming_eating_failed(self):
        from unittest.mock import Mock
        self.runtime.automatic=False
        self.runtime.ingest(self.frame())
        result={'intent_id':'synthetic-mushroom','candidate':'eat_held',
                'status':'cancelled_held_object_changed','queued':False}
        pending=Mock();pending.snapshot.return_value=result
        self.runtime.pending_actions=pending
        try:
            await self.runtime.step()
            await self.runtime.step()
            data=self.runtime.payload()
            self.assertEqual(data['current']['speech']['last_message']['text'],
                '[ERROR: The held object changed or is no longer available; the command was not sent]')
            errors=[e for e in data['runtime']['errors'] if e['stage']=='action_execution']
            self.assertEqual(len(errors),1)
            self.assertEqual(errors[0]['code'],'held_object_changed')
            self.assertEqual(errors[0]['details'],result)
        finally:self.runtime.pending_actions=None

    async def test_feedback_waits_for_success_and_keeps_events_during_reply(self):
        await self.runtime.configure({'automatic':False})
        event={'event_id':'feedback-one','kind':'praise','observed_at':self.now,'magnitude':.1}
        sample=self.frame();sample['observation']['training']['feedback']=[event]
        self.runtime.ingest(sample)
        self.now+=240
        self.runtime.ingest(self.frame())
        async def failed(request):raise ValueError('Dummy failure')
        original=self.runtime.provider.respond
        self.runtime.provider.respond=failed
        with self.assertRaises(RuntimeError):await self.runtime.question({'question':'status'})
        self.assertIn('feedback-one',self.runtime.feedback_context.pending)
        async def delayed(request):
            next_frame=self.frame()
            next_frame['observation']['training']['feedback']=[{
                **event,'event_id':'feedback-two','observed_at':self.now,'kind':'punishment','magnitude':-.2}]
            self.runtime.ingest(next_frame)
            return await original(request)
        self.runtime.provider.respond=delayed
        await self.runtime.question({'question':'status'})
        self.assertEqual(list(self.runtime.feedback_context.pending),['feedback-two'])
        self.assertEqual(self.runtime.memory.history[-1]['input']['snapshot']['events']['player_feedback'][0]['event_id'],
                         'feedback-one')

    async def test_menu_states_block_chat_force_tick_and_automatic_updates(self):
        for changes in ({'paused':True},{'paused':None},{'loading':True},
                        {'world_ready':False},{'creature_id':None}):
            self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':3}},**changes))
            before=self.runtime.broker.calls_started
            with self.assertRaises(ValueError):await self.runtime.question({'question':'Hello'})
            with self.assertRaises(ValueError):await self.runtime.force_tick({})
            await self.runtime.step();await asyncio.sleep(.01)
            self.assertEqual(self.runtime.broker.calls_started,before)
            self.assertIsNotNone(self.runtime.payload()['current']['question_unavailable'])

    async def test_entering_menu_keeps_paid_reply_without_allowing_new_requests(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def stubborn(request):
            entered.set()
            try:await release.wait()
            except asyncio.CancelledError:await release.wait()
            return {'answer':'Late answer','decision':{'candidate':None},'memory':'Late notes'}
        self.runtime.provider.respond=stubborn
        self.runtime.ingest(self.frame())
        request=asyncio.create_task(self.runtime.question({'question':'Hello'}))
        await entered.wait()
        menu=self.frame(paused=True,observed_at=None,creature_id=None)
        menu['diagnostics']['paused']=True
        self.runtime.ingest(menu);await self.runtime.step()
        self.assertFalse(request.done())
        with self.assertRaises(ValueError):await self.runtime.question({'question':'New request'})
        with self.assertRaises(ValueError):await self.runtime.force_tick({})
        release.set();reply=await request
        self.assertEqual(reply['reply']['answer'],'Late answer')
        self.assertEqual(self.runtime.memory.notes,'Late notes')
        self.assertEqual(len(self.runtime.memory.snapshot()['recent_pairs']),1)
        self.assertEqual(self.runtime.payload()['current']['question_unavailable'],'game_paused_or_menu')
        self.assertEqual(self.runtime.payload()['current']['prompt']['context'],{})
        self.runtime.ingest(self.frame())
        self.assertEqual(self.runtime.broker.speech.snapshot(self.now)['last_answer']['text'],'Late answer')

    async def test_force_tick_sends_fresh_observation_with_automatic_off_and_no_question(self):
        await self.runtime.configure({'automatic':False})
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':2}}))
        result=await self.runtime.force_tick({})
        self.assertTrue(result['sent']);self.assertFalse(self.runtime.automatic)
        request=self.runtime.provider.requests[-1]
        self.assertEqual(request.kind,'observation')
        self.assertNotIn('god_question',request.current)
        self.assertEqual(request.current['snapshot'],{'surroundings':{'trees_nearby':2}})
        await self.runtime.force_tick({}) # Explicit click bypasses unchanged-context suppression.
        self.assertEqual(self.runtime.broker.calls_started,2)
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':7}}))
        await self.runtime.force_tick({})
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{'surroundings':{'trees_nearby':7}})

    async def test_force_tick_guard_and_pending_request_cancellation(self):
        await self.runtime.configure({'automatic':False})
        for context,changes in (({},{}),({'surroundings':{'trees_nearby':1}},{'paused':True}),
                ({'surroundings':{'trees_nearby':1}},{'in_temple':True}),({'surroundings':{'trees_nearby':1}},{'cutscene':True}),
                ({'surroundings':{'trees_nearby':1}},{'creature_id':None})):
            self.runtime.ingest(self.frame(context,**changes))
            with self.assertRaises(ValueError):await self.runtime.force_tick({})
        self.assertEqual(self.runtime.broker.calls_started,0)
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}}));self.runtime.provider.delay=10
        task=asyncio.create_task(self.runtime.force_tick({}))
        await self.until(lambda:self.runtime.provider.active==1)
        await self.runtime.step()
        self.assertFalse(task.done()) # Turning automatic observations off does not cancel this click.
        with self.assertRaises(ValueError):await self.runtime.force_tick({})
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}},cutscene=True))
        await self.runtime.step()
        with self.assertRaises(ValueError):await task
        self.assertEqual(self.runtime.memory.snapshot()['recent_pairs'],[])

    async def test_force_tick_does_not_wake_sleeping_creature(self):
        from test_sleep import sleep_frame
        self.runtime.ingest(sleep_frame(self.frame()))
        with self.assertRaises(ValueError):await self.runtime.force_tick({})
        self.assertEqual(self.runtime.broker.calls_started,0)

    async def test_separate_resets_persist_without_model_calls_or_cross_clearing(self):
        from bwv.conversation import ConversationMemory
        self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'remember: Be gentle.'})
        before=self.runtime.broker.calls_started
        notes=self.runtime.memory.notes
        self.assertTrue(notes)
        await self.runtime.reset({'scope':'history'})
        state=self.runtime.payload()
        self.assertEqual(state['current']['memory']['memory'],notes)
        self.assertEqual(state['current']['memory']['recent_pairs'],[])
        self.assertEqual(state['current']['dialogue'],[])
        self.assertIsNone(state['runtime']['last_request'])
        self.assertIsNone(state['current']['speech']['last_answer'])
        restored=ConversationMemory(path=self.runtime.memory.store.path)
        self.assertEqual(restored.notes,notes);self.assertEqual(list(restored.history),[])
        self.assertEqual(self.runtime.broker.calls_started,before)
        await self.runtime.question({'question':'Hello'})
        history=self.runtime.memory.snapshot()['recent_pairs']
        before=self.runtime.broker.calls_started
        await self.runtime.reset({'scope':'memory'})
        restored=ConversationMemory(path=self.runtime.memory.store.path)
        self.assertEqual(restored.notes,'')
        self.assertEqual(list(restored.history),history)
        self.assertEqual(self.runtime.broker.calls_started,before)

    async def test_reset_rejects_pending_reply_and_invalid_scope(self):
        self.runtime.ingest(self.frame());self.runtime.provider.delay=10
        question=asyncio.create_task(self.runtime.question({'question':'Hello'}))
        await self.until(lambda:self.runtime.provider.active==1)
        self.assertFalse(self.runtime.reset_available())
        for scope in ('history','memory'):
            with self.assertRaises(ValueError):await self.runtime.reset({'scope':scope})
        question.cancel();await asyncio.gather(question,return_exceptions=True)
        for payload in ({},{'scope':'all'},{'scope':'memory','other':True}):
            with self.assertRaises(ValueError):await self.runtime.reset(payload)

    async def test_question_reports_executor_restart_mismatch_without_claiming_wake(self):
        from bwv.action_probe import ActionProcessChanged
        from test_sleep import sleep_frame
        def stale_executor(obs):
            raise ActionProcessChanged('Synthetic restart')
        await self.runtime.close()
        self.runtime = LiveDummyRuntime(self.directory.name, clock=lambda: self.now,
            names_reader=ProfileNames, wake_factory=stale_executor)
        self.runtime.gate.resume_delay = 0
        self.runtime.ingest(sleep_frame(self.frame()))
        await self.runtime.question({'question': 'Hello'})
        self.assertEqual(self.runtime.broker.last_wake['status'], 'action_process_changed')
        self.assertFalse(self.runtime.broker.last_wake['queued'])
        self.assertNotIn('wake_request', self.runtime.provider.requests[-1].current)

    async def test_direct_message_requests_wake_without_waiting_for_model(self):
        from test_sleep import sleep_frame
        sent=[]
        class WakeAdapter:
            def capture(adapter,now):return ActionOffer(('synthetic',),(('wake_up',b'wake'),),now+1)
            def still_valid(adapter,offer,candidate):return True
            def submit(adapter,packet):sent.append(packet);return True
        await self.runtime.close()
        self.runtime=LiveDummyRuntime(self.directory.name,clock=lambda:self.now,names_reader=ProfileNames,
            wake_factory=lambda obs:DecisionDispatcher(WakeAdapter(),clock=lambda:self.now))
        self.runtime.gate.resume_delay=0
        self.runtime.ingest(sleep_frame(self.frame()))
        await self.runtime.step()
        self.assertEqual(sent,[]) # Sleeping ticks cannot wake.
        with self.assertRaises(ValueError):await self.runtime.question({'question':' '})
        self.assertEqual(sent,[])
        self.runtime.provider.delay=10
        question=asyncio.create_task(self.runtime.question({'question':'Hello!'}))
        await self.until(lambda:self.runtime.provider.active==1)
        self.assertEqual(sent,[b'wake'])
        request=self.runtime.provider.requests[-1]
        self.assertTrue(request.current['snapshot']['activity']['sleeping'])
        self.assertEqual(request.current['wake_request'],{'status':'queued','queued':True})
        self.assertNotIn('candidates',request.current['snapshot'])
        question.cancel()
        await asyncio.gather(question,return_exceptions=True)
        await self.until(lambda:self.runtime.provider.active==0)
        self.assertEqual(sent,[b'wake']) # No retry or attempt to undo a wake.

    async def test_question_wake_obeys_scene_identity_and_uncertain_receipt(self):
        from test_sleep import sleep_frame
        attempts=[]
        class LostWake:
            def offer(adapter):return {'candidates':['wake_up']}
            def dispatch(adapter,decision):attempts.append(decision);raise OSError('lost receipt')
        await self.runtime.close()
        self.runtime=LiveDummyRuntime(self.directory.name,clock=lambda:self.now,names_reader=ProfileNames,
                                     wake_factory=lambda obs:LostWake())
        self.runtime.gate.resume_delay=0
        asleep=sleep_frame(self.frame())
        self.runtime.ingest(asleep)
        with self.assertRaises(ValueError):
            await self.runtime.question({'question':'Hello','expected_identity':['old','creature']})
        self.assertEqual(attempts,[])
        self.now+=.1
        asleep=sleep_frame(self.frame(in_temple=True))
        self.runtime.ingest(asleep)
        await self.runtime.question({'question':'Hello'})
        self.assertEqual(attempts,[])
        self.now+=.1
        asleep=sleep_frame(self.frame())
        self.runtime.ingest(asleep)
        await self.runtime.question({'question':'Hello'})
        self.assertEqual(attempts,[{'candidate':'wake_up'}])
        self.assertEqual(self.runtime.broker.last_wake['status'],'not_confirmed_no_retry')
        self.assertEqual(self.runtime.provider.requests[-1].current['wake_request']['status'],
                         'not_confirmed_no_retry')

    async def test_sleep_cancels_automatic_but_direct_question_is_immediate(self):
        from test_sleep import sleep_frame
        self.runtime.provider.delay=10
        self.runtime.ingest(sleep_frame(self.frame(),False))
        await self.runtime.step()
        await self.until(lambda:self.runtime.provider.active==1)
        self.now+=.5
        self.runtime.ingest(sleep_frame(self.frame()))
        await self.runtime.step()
        await self.until(lambda:not self.runtime._tasks and self.runtime.provider.active==0)
        self.assertEqual(self.runtime.scheduled['state'],'suspended_sleep')
        self.assertEqual(len(self.runtime.memory.history),0)
        self.runtime.provider.delay=0
        await self.runtime.question({'question':'Are you asleep?'})
        self.assertTrue(self.runtime.provider.requests[-1].current['snapshot']['activity']['sleeping'])
        self.assertEqual(len(self.runtime.memory.history),1)
        self.now+=.5
        self.runtime.ingest(sleep_frame(self.frame()))
        await self.runtime.step()
        self.assertEqual(self.runtime.scheduled['state'],'suspended_sleep')
        self.assertEqual(len(self.runtime.memory.history),1)
        self.now+=.5
        self.runtime.ingest(sleep_frame(self.frame({'surroundings':{'people_nearby':7}}),False))
        await self.runtime.step()
        await self.until(lambda:len(self.runtime.memory.history)==2)
        snapshot=self.runtime.provider.requests[-1].current['snapshot']
        self.assertEqual(snapshot,{'activity':{'sleep_phase':'awake'},'surroundings':{'people_nearby':7}})
        self.assertEqual(self.runtime.payload()['runtime']['game_actions'],0)

    async def test_completed_action_reaches_next_provider_call_without_heartbeat_wait(self):
        from test_action_feedback import finished
        self.runtime.ingest(self.frame())
        await self.runtime.step()
        await self.until(lambda: len(self.runtime.memory.history)==1 and not self.runtime._tasks)
        self.now += .5
        frame=self.frame()
        frame['diagnostics']['action_probe']=finished()
        self.runtime.ingest(frame)
        await self.runtime.step()
        await self.until(lambda: len(self.runtime.memory.history)==2)
        event=self.runtime.provider.requests[-1].current['snapshot']['events']['action_results'][0]
        self.assertEqual(event['status'],'completed')
        self.assertEqual(event['action'],'approach_hand')
        self.now += .5; self.runtime.ingest(self.frame())
        await self.runtime.step(); await asyncio.sleep(.01)
        self.assertEqual(len(self.runtime.memory.history),2)

    async def test_opt_in_direct_dummy_question_dispatches_and_reports_receipt(self):
        sent=[]
        class SyntheticAdapter:
            def capture(adapter, now):
                return ActionOffer(('synthetic',), (('approach_hand', b'original'),), now+30)
            def still_valid(adapter, offer, candidate): return True
            def submit(adapter, packet): sent.append(packet); return True
        await self.runtime.close()
        self.runtime=LiveDummyRuntime(self.directory.name,clock=lambda:self.now,names_reader=ProfileNames,
            action_factory=lambda obs:DecisionDispatcher(SyntheticAdapter(),clock=lambda:self.now))
        self.runtime.gate.resume_delay=0
        self.runtime.ingest(self.frame())
        reply=await self.runtime.question({'question':'come to me'})
        self.assertEqual(sent,[b'original'])
        self.assertEqual(reply['reply']['decision'],{'candidate':'approach_hand'})
        self.assertEqual(reply['action_dispatch'],{'status':'queued','queued':True})
        self.assertIsNone(reply['game_actions']) # No engine confirmation supplied.
        self.assertTrue(self.runtime.payload()['runtime']['engine_execution_enabled'])
        original=self.frame()
        original['diagnostics']['action_probe']={'agenda_calls':1}
        self.runtime.ingest(original)
        self.assertEqual(self.runtime.payload()['runtime']['game_actions'],1)

    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.now = 100.0
        self.runtime = LiveDummyRuntime(self.directory.name, clock=lambda:self.now, names_reader=ProfileNames)
        self.runtime.gate.resume_delay = 0
        self.sequence = 0

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(.01)

    def frame(self, context=None, **changes):
        self.sequence += 1
        obs = ControlObservation('live-session',self.sequence,self.now,
            connected=True,creature_id='owned-monkey',paused=False,in_temple=False,
            creature=CreatureContext('monkey','engine'))
        raw = asdict(obs); raw.update(changes)
        return {'observation':raw,'diagnostics':{'script_camera_owned':False},
            'gate':{'allowed':False,'reasons':['unsupported','unknown_control']},
            'prompt':{'system':'Test creature system prompt', 'context':
                {'instinct':{'native_plan':'SleepAtHome'},'body_engine_units':{'energy':.5}}
                if context is None else context}}

    async def test_live_snapshot_chat_speech_memory_and_engine_gate_stay_separate(self):
        original = self.frame()
        self.runtime.ingest(original)
        await self.runtime.step(); await self.until(lambda:len(self.runtime.memory.history)==1)
        self.assertEqual(self.runtime.broker.calls_started,1)
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{'self':{'energy_engine_units':.5},'activity':{'native_plan':'sleep at home'}})
        self.assertTrue(self.runtime.payload()['current']['gate']['allowed'])
        self.assertFalse(self.runtime.payload()['source']['gate']['allowed'])
        await self.runtime.question({'question':'status'})
        await self.runtime.question({'question':'remember: Help villagers'})
        await self.runtime.question({'question':'say: Hello!'})
        self.assertEqual(self.runtime.broker.speech.snapshot(self.now)['current']['text'],'Hello!')
        self.assertEqual(self.runtime.memory.notes,'Help villagers')
        self.runtime.publish()
        self.assertEqual(len(self.runtime.payload()['current']['dialogue']),3)
        self.assertEqual(self.runtime.provider.max_active,1)
        self.assertEqual(self.runtime.payload()['runtime']['game_actions'],0)
        self.assertNotIn(self.directory.name,json.dumps(self.runtime.payload()))
        await self.runtime.close()
        self.runtime = LiveDummyRuntime(self.directory.name,clock=lambda:self.now,names_reader=ProfileNames)
        self.assertEqual(self.runtime.memory.notes,'Help villagers')
        self.assertEqual([p.name for p in Path(self.directory.name).iterdir()],['dummy-memory.json'])

    async def test_empty_snapshot_skips_tick_but_explicit_question_sends(self):
        self.runtime.ingest(self.frame({}))
        await self.runtime.step(); await asyncio.sleep(.01)
        self.assertEqual(self.runtime.broker.calls_started,0)
        await self.runtime.question({'question':'Hello'})
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{})
        self.assertTrue(self.runtime.provider.requests[-1].current['conversation_only'])

    async def test_textless_direct_replies_acknowledge_without_fabricating_model_text(self):
        self.runtime.ingest(self.frame())
        await self.runtime.question({'question': 'say: Visible text 😕'})
        initial = self.runtime.broker.speech.snapshot(self.now)['current']
        self.assertEqual(initial['text'], 'Visible text')
        for command in ('say:', 'say:   ', 'say: 😕', 'say: 🙂'):
            result = await self.runtime.question({'question': command})
            self.assertEqual(result['reply']['answer'], '')
            self.assertEqual(result['reply']['say'], '')
            current = self.runtime.broker.speech.snapshot(self.now)['current']
            self.assertEqual(current['text'],'[The creature is obeying your command]')
            self.assertEqual(current['source'],'acknowledgement')
            self.assertEqual(result['last_message']['text'],current['text'])
            self.assertEqual(self.runtime.payload()['current']['dialogue'][-1]['say'], '')
        self.assertEqual(self.runtime.payload()['runtime']['game_actions'], 0)
        self.assertEqual(self.runtime.memory.snapshot()['recent_pairs'][-1]['say'], '🙂')

    async def test_named_acknowledgement_is_persistent_and_not_an_execution_receipt(self):
        self.runtime.names_reader=lambda:ProfileNames(creature_name='Pebble')
        self.runtime.ingest(self.frame())
        reply=await self.runtime.question({'question':'say: 😕'})
        self.assertEqual(reply['last_message']['text'],'[Pebble is obeying your command]')
        self.assertEqual(reply['game_actions'],0)
        self.now+=30
        state=self.runtime.broker.speech.snapshot(self.now)
        self.assertIsNone(state['current'])
        self.assertEqual(state['last_message'],reply['last_message'])
        self.assertNotIn('is obeying your command',str(self.runtime.memory.snapshot()['recent_pairs']))

    async def test_delayed_answer_status_hides_in_scene_and_clears_on_failure(self):
        release=asyncio.Event();entered=asyncio.Event()
        async def respond(request):
            entered.set();await release.wait();raise ValueError('Synthetic failure')
        self.runtime.provider.respond=respond
        self.runtime.ingest(self.frame())
        question=asyncio.create_task(self.runtime.question({'question':'Why?'}))
        await entered.wait()
        self.runtime.publish()
        self.assertFalse(self.runtime.payload()['current']['answer_status']['thinking'])
        self.now+=1;self.runtime.ingest(self.frame())
        self.assertTrue(self.runtime.payload()['current']['answer_status']['thinking'])
        self.runtime.ingest(self.frame(cutscene=True))
        self.assertFalse(self.runtime.payload()['current']['answer_status']['thinking'])
        self.runtime.ingest(self.frame())
        self.assertTrue(self.runtime.payload()['current']['answer_status']['thinking'])
        release.set()
        with self.assertRaises(RuntimeError):await question
        self.runtime.publish()
        self.assertFalse(self.runtime.payload()['current']['answer_status']['thinking'])
        self.assertIsNone(self.runtime.payload()['current']['speech']['last_answer'])

    async def test_profile_names_refresh_in_actual_requests_and_debug_system(self):
        names=[ProfileNames('Guide','Pebble')]
        self.runtime.names_reader=lambda:names[0]
        self.runtime.ingest(self.frame({}))
        await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,0)
        await self.runtime.question({'question':'Hello'})
        system=self.runtime.provider.requests[-1].messages[0]['content']
        self.assertIn('"god_player_name":"Guide"',system)
        self.assertIn('"creature_name":"Pebble"',system)
        names[0]=ProfileNames(creature_name='Fern')
        await self.runtime.question({'question':'Hello again'})
        self.runtime.publish()
        system=self.runtime.payload()['current']['prompt']['system']
        self.assertIn('"creature_name":"Fern"',system)
        self.assertNotIn('god_player_name',system)
        self.assertNotIn('Pebble',system)
        self.assertEqual(self.runtime.payload()['current']['character_names']['creature_name'],'Fern')
        names[0]=ProfileNames()
        self.runtime.publish()
        self.assertIsNone(self.runtime.payload()['current']['character_names']['creature_name'])

    async def test_known_locks_block_ticks_and_questions_require_current_identity(self):
        for lock in ({'paused':True},{'cutscene':True},{'in_temple':True},
                     {'loading':True},{'script_owns_creature':True},{'player_has_control':False}):
            self.runtime.ingest(self.frame(**lock))
            count = self.runtime.broker.calls_started
            await self.runtime.step(); await asyncio.sleep(.01)
            self.assertEqual(self.runtime.broker.calls_started,count)
            if lock.get('paused') or lock.get('loading') or lock.get('script_owns_creature'):
                with self.assertRaises(ValueError):await self.runtime.question({'question':'status'})
            else:
                await self.runtime.question({'question':'status'})
                self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{})
        # Current live sensors hide identity in some locked scenes. Never reuse it.
        for changes in ({'creature_id':None}, {'creature_present':False},
                        {'paused':True,'creature_id':None}, {'connected':False}):
            self.runtime.ingest(self.frame(**changes))
            count = self.runtime.broker.calls_started
            with self.assertRaises(ValueError):
                await self.runtime.question({'question':'Hello'})
            await self.runtime.step(); await asyncio.sleep(.01)
            self.assertEqual(self.runtime.broker.calls_started,count)

    async def test_stale_repeated_source_is_not_refreshed_and_unknown_scene_blocks(self):
        frame = self.frame()
        self.runtime.ingest(frame)
        self.now += 2
        self.runtime.ingest(frame)
        self.assertIsNotNone(self.runtime.source)
        self.assertTrue(self.runtime.gate.waiting_for_observation)
        self.assertEqual(self.runtime.gate.observation.observed_at,frame['observation']['observed_at'])
        with self.assertRaises(ValueError):
            await self.runtime.question({'question':'status'})
        frame = self.frame(paused=None)
        self.runtime.ingest(frame); await self.runtime.step()
        self.assertFalse(self.runtime.payload()['current']['gate']['allowed'])
        frame = self.frame(); frame['diagnostics']['script_camera_owned'] = None
        self.runtime.ingest(frame); await self.runtime.step()
        self.assertFalse(self.runtime.payload()['current']['gate']['allowed'])

    async def test_resume_rebuilds_snapshot_and_disable_cancels_late_automatic_reply(self):
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}}))
        self.runtime.provider.delay = .05
        await self.runtime.step(); await self.until(lambda:self.runtime.provider.active==1)
        await self.runtime.configure({'automatic':False})
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':2}}))
        await asyncio.sleep(.06)
        self.assertEqual(len(self.runtime.memory.history),0)
        await self.runtime.question({'question':'status'})
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{'surroundings':{'trees_nearby':2}})
        await self.runtime.configure({'automatic':True,'delay_seconds':0})
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':3}}))
        await self.runtime.step()
        await self.until(lambda:self.runtime.provider.requests[-1].current['snapshot']=={'surroundings':{'trees_nearby':3}})
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{'surroundings':{'trees_nearby':3}})

    async def test_busy_ticks_coalesce_without_queueing_and_question_uses_latest_snapshot(self):
        self.runtime.provider.delay = .06
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}})); await self.runtime.step()
        await asyncio.sleep(.01)
        for value in (2,3,4):
            self.now += 2
            self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':value}})); await self.runtime.step()
            await asyncio.sleep(.005)
        pending = asyncio.create_task(self.runtime.question({'question':'status'}))
        await asyncio.sleep(.2); await pending
        self.assertEqual(self.runtime.broker.calls_started,2)
        self.assertIsNone(self.runtime.broker._auto)
        self.assertEqual(self.runtime.provider.max_active,1)
        self.assertTrue(all(r.current['snapshot']=={'surroundings':{'trees_nearby':4}} for r in list(self.runtime.provider.requests)[1:]))

    async def test_question_resets_deadline_and_settings_persist_without_paths(self):
        await self.runtime.configure({'cadence':{'jitter_percent':0}})
        self.runtime.ingest(self.frame())
        # A question may arrive before the first scheduler step.
        await self.runtime.question({'question':'status'})
        self.assertEqual(self.runtime.updates.deadline,160)
        self.now=120;self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':2}}))
        await self.runtime.step()
        self.assertEqual(self.runtime.broker.calls_started,1)
        await self.runtime.question({'question':'status'})
        self.assertEqual(self.runtime.updates.deadline,180)
        path=Path(self.directory.name)/'config.local.json'
        data=json.loads(path.read_text())
        self.assertEqual(data['active_seconds'],30)
        self.assertNotIn(self.directory.name,path.read_text())
        await self.runtime.close()
        self.runtime=LiveDummyRuntime(self.directory.name,clock=lambda:self.now,names_reader=ProfileNames)
        self.assertEqual(self.runtime.cadence.jitter_percent,0)

    async def test_tool_session_refreshes_snapshot_but_rejects_scene_identity_and_age(self):
        from bwv.creature_tools import ToolContextUnavailable
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':1}}))
        session=self.runtime._tool_session()
        self.runtime.ingest(self.frame({'surroundings':{'trees_nearby':2}}))
        self.assertEqual(session.supplier()['prompt']['context'],{'surroundings':{'trees_nearby':2}})
        for changes in ({'in_temple':True},{'cutscene':True},{'creature_id':'other'}):
            self.runtime.ingest(self.frame(**changes))
            with self.assertRaises(ToolContextUnavailable):session.validate()
        self.runtime.ingest(self.frame())
        self.now+=2
        with self.assertRaises(ToolContextUnavailable):session.validate()

    async def test_disconnect_even_same_session_reconnect_rejects_late_memory(self):
        self.runtime.provider.delay = .06
        self.runtime.ingest(self.frame())
        pending = asyncio.create_task(self.runtime.question({'question':'remember: stale note'}))
        await asyncio.sleep(.01)
        self.runtime.ingest(None)
        self.runtime.ingest(self.frame())
        with self.assertRaises(ValueError):
            await pending
        self.assertEqual(self.runtime.memory.notes,'')
        await self.runtime.question({'question':'status'})
        self.assertIn('memory_reconciliation',self.runtime.provider.requests[-1].current)

    async def test_last_reply_survives_read_failure_but_not_a_different_creature(self):
        self.runtime.ingest(self.frame())
        await self.runtime.question({'question':'say: I am here.'})
        original=self.runtime.broker.speech.snapshot(self.now)['last_message']
        self.runtime.ingest(None)
        self.now+=10
        self.runtime.ingest(self.frame())
        self.assertEqual(self.runtime.broker.speech.snapshot(self.now)['last_message'],original)
        self.runtime.ingest(self.frame(creature_id='different-creature'))
        self.assertIsNone(self.runtime.broker.speech.snapshot(self.now)['last_message'])

    async def test_configuration_atomic_and_candidates_never_become_capabilities(self):
        with self.assertRaises(ValueError):
            await self.runtime.configure({'automatic':False,'delay_seconds':100})
        self.assertTrue(self.runtime.automatic)
        self.runtime.ingest(self.frame({'candidates':['danger'], 'available_intents':['anything'],'surroundings':{'trees_nearby':1}}))
        await self.runtime.step(); await self.until(lambda:len(self.runtime.memory.history)==1)
        self.assertEqual(self.runtime.provider.requests[-1].current['snapshot'],{'surroundings':{'trees_nearby':1}})
        self.assertEqual(self.runtime.memory.history[-1]['decision'],{'candidate':None})

    async def test_http_page_chat_route_and_origin_guard(self):
        self.runtime.ingest(self.frame())
        server,url = make_server(self.runtime,post_handlers={
            'question':self.runtime.handler(self.runtime.question),
            'reset':self.runtime.handler(self.runtime.reset),
            'force-tick':self.runtime.handler(self.runtime.force_tick)},page_name='qa_live.html',debug=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        parsed=urlsplit(url)
        def request(origin, method='POST',route='question',payload=None):
            connection=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=3)
            try:
                connection.request(method,parsed.path+(route if method=='POST' or route!='question' else ''),
                    json.dumps({'question':'status'} if payload is None else payload) if method=='POST' else None,
                    {'Origin':origin,'Content-Type':'application/json'})
                response=connection.getresponse();return response.status,response.read()
            finally:connection.close()
        try:
            origin=f'http://127.0.0.1:{parsed.port}'
            code,body=await asyncio.to_thread(request,origin)
            self.assertEqual(code,200);self.assertEqual(json.loads(body)['mode'],'live_dummy_preview')
            self.assertEqual((await asyncio.to_thread(request,'https://example.com'))[0],403)
            code,page=await asyncio.to_thread(request,origin,'GET')
            self.assertEqual(code,200);self.assertIn(b'Live dummy',page)
            code,body=await asyncio.to_thread(request,origin,'GET','overlay-state')
            self.assertEqual(code,200)
            self.assertEqual(json.loads(body),self.runtime.overlay_payload())
            self.assertNotIn('prompt',json.loads(body)['current'])
            self.assertEqual((await asyncio.to_thread(request,'https://example.com','POST',
                'reset',{'scope':'history'}))[0],403)
            code,body=await asyncio.to_thread(request,origin,'POST','reset',{'scope':'history'})
            self.assertEqual(code,200);self.assertEqual(json.loads(body),{'reset':'history'})
            self.assertEqual((await asyncio.to_thread(request,'https://example.com','POST','force-tick',{}))[0],403)
            code,body=await asyncio.to_thread(request,origin,'POST','force-tick',{})
            self.assertEqual(code,200);self.assertTrue(json.loads(body)['sent'])
        finally:
            await asyncio.to_thread(server.shutdown);server.server_close();thread.join(2)


class SourceTests(unittest.TestCase):
    def test_source_rejects_remote_ambiguous_and_credential_urls(self):
        for url in ('https://example.com/a/','http://localhost:1234/'+'a'*24+'/',
                    'http://user@127.0.0.1:1234/'+'a'*24+'/',
                    'http://127.0.0.1:1234/'+'a'*24+'/?other=1'):
            with self.assertRaises(ValueError):InspectorSource(url)
        self.assertTrue(InspectorSource('http://127.0.0.1:1234/'+'a'*24+'/').url.endswith('/state'))
