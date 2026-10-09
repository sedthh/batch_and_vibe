import hashlib
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from bwv.action_probe import COMMAND, ConversationActionAdapter, TreeApproachAdapter
from bwv.conversation import ConversationMemory
from bwv.dispatch import DecisionDispatcher
from bwv.pending_actions import PendingActions
from bwv.profile_names import ProfileNames
from bwv.qa_live import LiveChatRuntime
from test_control import playable
from test_dispatch import frame


class Harness:
    def __init__(self):
        self.now = 100.
        self.native = frame()
        self.probe = self.native['action_probe']
        self.probe.update(candidates=['look_confused','approach_hand'],
            movement={'verified':True,'distance_to_hand_engine_units':15},
            action_lifecycle={'version':1,'repeated_actions_enabled':True,
                              'in_flight':False,'cooldown_remaining_ms':0},
            conversation_actions={'version':2,'max_offer_age_ms':30000,
                'hand_target_generation':7,'creature_handle':42,'mental_handle':43})
        self.identity = ('123:3', hashlib.sha256(b'123:3:42:43').hexdigest()[:24])
        self.sent = []
        self.pending = PendingActions(self.factory,self.source,clock=lambda:self.now)

    def obs(self):
        return playable(self.now,sequence=int(self.now*10),session=self.identity[0],creature_id=self.identity[1])

    def source(self):
        return {'observation':asdict(self.obs()),
                'diagnostics':{'source_age_ms':20,'action_probe':deepcopy(self.probe)},
                'prompt':{'system':'Synthetic test','context':{'body_engine_units':{'energy':.8}}}}

    def send(self, pid, packet):
        self.sent.append(packet)
        return True

    def factory(self, obs, *, expected_hand_generation=None, expected_object_generation=None,player_command=False):
        if expected_object_generation is not None:
            return DecisionDispatcher(TreeApproachAdapter(123,
                player_command=player_command,
                expected_identity=(obs.session,obs.creature_id), expected_object_generation=expected_object_generation,
                read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)
        return DecisionDispatcher(ConversationActionAdapter(123,
            player_command=player_command,
            expected_identity=(obs.session,obs.creature_id),expected_hand_generation=expected_hand_generation,
            read=lambda pid:deepcopy(self.native),send=self.send),clock=lambda:self.now)

    def enqueue(self, candidate='look_confused'):
        offer=self.pending.factory(self.obs());offer.offer()
        return offer.dispatch({'candidate':candidate})


class PendingActionTests(unittest.TestCase):
    def setUp(self):self.h=Harness()

    def test_negative_queue_ack_refreshes_offer_without_duplicate_execution(self):
        h=self.h;calls=[]
        def race(pid,packet):
            calls.append(packet)
            if len(calls)==1:
                h.probe['generation']+=1
                return False
            h.sent.append(packet);return True
        h.send=race;h.enqueue('approach_hand');deadline=h.pending.pending['expires']
        h.pending.advance(h.obs(),allowed=True)
        self.assertFalse(h.sent)
        self.assertEqual(h.pending.last['dispatch_check']['stage'],'before_native_queue')
        self.assertEqual(h.pending.pending['expires'],deadline)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(calls),2);self.assertEqual(len(h.sent),1)
        self.assertNotEqual(calls[0],calls[1])
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(calls),2)

    def test_queue_retries_are_bounded_and_do_not_survive_scene_or_deadline(self):
        for stop in ('limit','scene','expiry'):
            h=Harness();calls=[]
            h.send=lambda pid,packet:calls.append(packet) or False
            h.enqueue();h.pending.advance(h.obs(),allowed=True)
            if stop=='expiry':h.now+=31
            for _ in range(8):h.pending.advance(h.obs(),allowed=stop!='scene')
            self.assertEqual(len(calls),4 if stop=='limit' else 1)
            self.assertEqual(h.pending.last['status'],{
                'limit':'cancelled_retry_limit','scene':'cancelled_control_changed','expiry':'expired'}[stop])

    def test_unspecified_rejection_never_grants_retry_permission(self):
        from bwv.pending_actions import retryable_before_execution
        self.assertFalse(retryable_before_execution({'status':'rejected','queued':False}))
        self.assertFalse(retryable_before_execution({'status':'not_confirmed_no_retry','queued':False}))

    def test_player_origin_survives_waiting_for_native_dispatch(self):
        from bwv.action_probe import PLAYER_COMMAND_FLAG
        h=self.h;h.probe['player_command_priority']={'version':1,'available':True}
        offer=h.pending.factory(h.obs());offer.mark_player_command();offer.offer()
        offer.dispatch({'candidate':'approach_hand'})
        self.assertTrue(h.pending.pending['player_command'])
        h.pending.advance(h.obs(),allowed=False,temporary_unavailable=True)
        self.assertFalse(h.sent)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(COMMAND.unpack(h.sent[-1])[1],2|PLAYER_COMMAND_FLAG)


    def test_sample_gap_retains_unsent_intention_without_dispatching_or_extending_deadline(self):
        h=self.h;h.enqueue();deadline=h.pending.pending['expires']
        h.now+=2
        h.pending.advance(h.obs(),allowed=False,temporary_unavailable=True)
        self.assertFalse(h.sent)
        self.assertEqual(h.pending.snapshot()['waiting_reason'],'waiting_for_fresh_observation')
        self.assertEqual(h.pending.pending['expires'],deadline)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_sample_gap_cannot_keep_an_expired_intention(self):
        h=self.h;h.enqueue();h.now+=31
        h.pending.advance(h.obs(),allowed=False,temporary_unavailable=True)
        self.assertEqual(h.pending.snapshot()['status'],'expired')
        self.assertFalse(h.sent)

    def test_unsent_precheck_can_recover_without_extending_deadline(self):
        h=self.h;h.enqueue();original_deadline=h.pending.pending['expires']
        factory=h.pending.native_factory
        def intermittent(obs,**kwargs):
            dispatcher=factory(obs,**kwargs)
            def failed_read(offer,candidate):raise OSError('Synthetic transient read')
            dispatcher.adapter.still_valid=failed_read
            return dispatcher
        h.pending.native_factory=intermittent
        h.pending.advance(h.obs(),allowed=True)
        self.assertFalse(h.sent)
        self.assertEqual(h.pending.last['waiting_reason'],'validation_unavailable')
        self.assertEqual(h.pending.pending['expires'],original_deadline)
        h.pending.native_factory=factory;h.now+=1
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_busy_wait_then_fresh_offer_single_send(self):
        h=self.h;h.probe.update(phase='unavailable',reason='native_animation_busy')
        result=h.enqueue()
        self.assertEqual(result['status'],'waiting_for_native');self.assertFalse(result['queued'])
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)
        h.now+=2;h.probe.update(phase='ready',generation=12,offered_at_uptime_ms=500)
        h.native['captured_uptime_ms']=510
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'queued')
        self.assertEqual(COMMAND.unpack(h.sent[0])[3:5],(12,500))
        for _ in range(5):h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_deadline_is_from_offer_not_late_response(self):
        h=self.h;offer=h.pending.factory(h.obs());offer.offer();h.now+=31
        self.assertEqual(offer.dispatch({'candidate':'look_confused'})['status'],'expired')
        self.assertIsNone(h.pending.pending)

    def test_wait_expires_and_does_not_revive(self):
        h=self.h;h.enqueue();h.now+=31
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'expired')
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)

    def test_control_and_identity_changes_cancel(self):
        for allowed,obs in ((False,self.h.obs()),(True,replace(self.h.obs(),creature_id='replaced'))):
            h=Harness();h.enqueue();h.pending.advance(obs,allowed=allowed)
            self.assertEqual(h.pending.last['status'],'cancelled_control_changed');self.assertFalse(h.sent)

    def test_new_question_revokes_wait_and_inference_offer(self):
        h=self.h;offer=h.pending.factory(h.obs());offer.offer()
        h.pending.cancel('superseded_by_question')
        self.assertEqual(offer.dispatch({'candidate':'look_confused'})['status'],'superseded')
        h.enqueue();h.pending.cancel('superseded_by_question')
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)

    def test_hand_movement_refreshes_target_without_extending_intention(self):
        h=self.h;h.enqueue('approach_hand')
        h.probe['request_at_uptime_ms']=0
        deadline=h.pending.pending['expires']
        h.probe['conversation_actions']['hand_target_generation']+=2
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'queued');self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],9)
        self.assertEqual(h.pending.inflight[0]['expires'],deadline)

    def test_distant_hand_can_be_selected_and_dispatched_once(self):
        h=self.h
        h.probe['movement']['distance_to_hand_engine_units']=1500
        h.enqueue('approach_hand')
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2:],(3,7))
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_hand_race_between_cached_frame_and_native_capture_is_rejected(self):
        h=self.h;old=h.source();h.pending.source=lambda:old;h.enqueue('approach_hand')
        h.probe['conversation_actions']['hand_target_generation']+=1
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'waiting_for_native');self.assertFalse(h.sent)
        h.pending.source=h.source;h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'queued')
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-1],8)

    def test_hand_move_interrupts_sleep_without_another_wake_loop(self):
        h=self.h;h.enqueue('approach_hand')
        h.probe.update(wake_protocol=3,candidates=['wake_up','approach_hand'])
        h.probe['activity']={'verified':True,'controller_mode':3,'animation':29,
                             'current_subaction':132,'sleep_in_sequence':True}
        def unexpected_wake(obs):raise AssertionError('Movement must exit sleep itself')
        h.pending.wake_factory=unexpected_wake
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)
        self.assertEqual(COMMAND.unpack(h.sent[0])[-2],3)

    def test_unavailable_hand_waits_without_guessing_and_still_expires(self):
        h=self.h;h.enqueue('approach_hand')
        h.probe['conversation_actions']['hand_target_generation']=0
        h.pending.advance(h.obs(),allowed=True)
        self.assertFalse(h.sent)
        self.assertEqual(h.pending.last['waiting_reason'],'hand_position_unavailable')
        h.now+=31;h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'expired')

    def test_capture_race_may_wait_but_never_extends_expiry(self):
        h=self.h;h.enqueue();factory=h.pending.native_factory
        def unavailable(*args,**kwargs):raise ValueError('No native offer yet')
        h.pending.native_factory=unavailable
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'waiting_for_native')
        self.assertFalse(h.sent)
        h.pending.native_factory=factory;h.now+=1
        h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(len(h.sent),1)

    def test_unknown_receipt_never_retries(self):
        h=self.h
        def lost(pid,packet):h.sent.append(packet);raise TimeoutError()
        h.send=lost;h.enqueue();h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'not_confirmed_no_retry')
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_stale_frame_cannot_execute_or_extend_deadline(self):
        h=self.h;h.enqueue();old=h.source();h.pending.source=lambda:old;h.now+=2
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)
        h.now+=30;h.pending.advance(h.obs(),allowed=True)
        self.assertEqual(h.pending.last['status'],'expired')

    def test_source_age_is_already_in_observation_timestamp(self):
        h=self.h;frame=h.source()
        frame['diagnostics']['source_age_ms']=500
        frame['observation']['observed_at']=h.now-.5
        h.pending.source=lambda:frame
        h.now+=.3  # 500ms native age + 300ms delivery/polling, not 1300ms.
        self.assertIn('look_confused',h.pending.factory(h.obs()).offer()['candidates'])
        state,_=h.pending._state(h.obs())
        self.assertAlmostEqual(state['source_age_ms'],800)
        h.now+=.21
        with self.assertRaisesRegex(ValueError,'Stale action observation'):
            h.pending._state(h.obs())

    def test_invalid_or_stale_native_age_cannot_grant_an_offer(self):
        h=self.h;frame=h.source();h.pending.source=lambda:frame
        for age in (-1,1501):
            frame['diagnostics']['source_age_ms']=age
            with self.assertRaises(ValueError):h.pending.factory(h.obs())

    def test_chat_age_window_can_select_but_cannot_execute_until_fresh(self):
        h=self.h;frame=h.source();frame['observation']['observed_at']=h.now-1.2
        h.pending.source=lambda:frame
        offer=h.pending.factory(h.obs());offer.offer()
        offer.dispatch({'candidate':'look_confused'})
        h.pending.advance(h.obs(),allowed=True);self.assertFalse(h.sent)
        frame['observation']['observed_at']=h.now
        h.pending.advance(h.obs(),allowed=True);self.assertEqual(len(h.sent),1)

    def test_old_bridge_and_mismatched_identity_cannot_advertise(self):
        h=self.h
        with self.assertRaises(ValueError):h.pending.factory(replace(h.obs(),creature_id='different'))
        h.probe['action_lifecycle']['repeated_actions_enabled']=False
        with self.assertRaises(ValueError):h.pending.factory(h.obs())

    def test_no_sleep_or_invented_action_and_no_double_dispatch(self):
        for candidate in ('sleep_here','wake_up','invented'):
            with self.assertRaises(ValueError):self.h.enqueue(candidate)
        h=self.h;offer=h.pending.factory(h.obs());offer.offer()
        offer.dispatch({'candidate':'look_confused'})
        with self.assertRaises(ValueError):offer.dispatch({'candidate':'look_confused'})
        self.assertFalse(h.sent)


class PendingChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h=Harness();self.tmp=TemporaryDirectory()
        h=self.h
        self.runtime=LiveChatRuntime(self.tmp.name,clock=lambda:h.now,names_reader=ProfileNames,
                                    action_factory=h.factory,wait_actions=True)
        self.runtime.gate.resume_delay=0
        self.runtime.provider.delay=0;self.runtime.automatic=False
        h.probe.update(phase='unavailable',reason='native_animation_busy')
        self.runtime.ingest(h.source())

    async def asyncTearDown(self):
        await self.runtime.close();self.tmp.cleanup()

    async def test_chat_returns_without_waiting_for_physical_execution(self):
        h=self.h;r=self.runtime
        result=await r.question({'question':'look confused'})
        self.assertEqual(result['action_dispatch']['status'],'waiting_for_native')
        self.assertFalse(h.sent)
        h.now+=1;h.probe.update(phase='ready');r.ingest(h.source());await r.step()
        self.assertEqual(len(h.sent),1)
        self.assertEqual(r.payload()['runtime']['pending_action']['status'],'queued')
        self.assertEqual(r.memory.history[-1]['action_status'],'queued_unconfirmed')
        restored=ConversationMemory(path=Path(self.tmp.name)/'dummy-memory.json')
        self.assertEqual(restored.history[-1]['action_status'],'queued_unconfirmed')

    async def test_new_question_updates_old_history_without_overwriting_new_pair(self):
        r=self.runtime
        await r.question({'question':'look confused'})
        await r.question({'question':'hello'})
        self.assertEqual(r.memory.history[-2]['action_status'],'superseded_by_question')
        self.assertEqual(r.memory.history[-1]['action_status'],'no_action')
        self.assertFalse(self.h.sent)

    async def test_emoji_request_tracks_one_pending_action_and_empty_text(self):
        r=self.runtime;h=self.h
        result=await r.question({'question':'say: 😕'})
        self.assertEqual(result['action_dispatch']['status'],'waiting_for_native')
        self.assertIn('is obeying your command',result['last_message']['text'])
        h.probe['phase']='ready';h.now+=1;r.ingest(h.source());await r.step()
        self.assertEqual(len(h.sent),1)
        self.assertEqual(r.memory.history[-1]['action_status'],'queued_unconfirmed')

    async def test_menu_cancels_without_later_replay(self):
        r=self.runtime;h=self.h
        await r.question({'question':'look confused'})
        source=h.source();source['observation']['paused']=True;source['diagnostics']['paused']=True
        r.ingest(source)
        h.probe['phase']='ready';h.now+=1;r.ingest(h.source());await r.step()
        self.assertFalse(h.sent)
        self.assertEqual(r.memory.history[-1]['action_status'],'cancelled_control_changed')

    async def test_restart_does_not_restore_pending_physical_action(self):
        r=self.runtime
        await r.question({'question':'look confused'})
        restored=ConversationMemory(path=Path(self.tmp.name)/'dummy-memory.json')
        self.assertEqual(restored.history[-1]['action_status'],'cancelled_restart')

    async def test_memory_reset_cancels_pending_action(self):
        r=self.runtime
        await r.question({'question':'look confused'})
        await r.reset({'scope':'memory'})
        self.assertIsNone(r.pending_actions.pending)
        self.assertEqual(r.memory.history[-1]['action_status'],'cancelled_reset')
