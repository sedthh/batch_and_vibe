import asyncio
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from bwv.overlay import ChatInputHistory, OverlayClient, OverlayModel, overlay_bounds
from bwv.qa import make_server
from bwv.qa_live import LiveDummyRuntime
from bwv.profile_names import ProfileNames


def payload(now=100, **changes):
    obs={'session':'123:1','creature_id':'monkey','observed_at':now,'connected':True}
    obs.update(changes)
    return {'runtime':{'mode':'live_dummy_preview'},
        'source':{'observation':obs},
        'current':{'question_unavailable':None,'speech':{'current':{
            'text':'Hello, God!','expires_at':now+15}}}}


class OverlayModelTests(unittest.TestCase):
    def test_observation_gap_keeps_last_reply_and_restores_sending_on_fresh_sample(self):
        model=OverlayModel();data=payload()
        data['current']['speech']['last_message']={'text':'Coming over.','created_at':100}
        model.update(data,100)
        data['runtime']['waiting_for_observation']=True
        model.update(data,102)
        self.assertTrue(model.can_show())
        self.assertFalse(model.can_send(102))
        self.assertEqual(model.last_message,'Coming over.')
        self.assertEqual(model.banner_text(now=102),'Coming over.')
        self.assertIn('Waiting for fresh game data',model.banner_text(pending=True,now=102))
        data['source']['observation']['observed_at']=102.5
        data['runtime']['waiting_for_observation']=False
        model.update(data,102.5)
        self.assertTrue(model.can_send(102.5))
        self.assertEqual(model.banner_text(now=102.5),'Coming over.')

    def test_cooldown_notice_explains_wait_and_preserves_last_reply(self):
        model=OverlayModel();data=payload()
        data['current']['speech']['last_message']={'text':'Hello, God!','created_at':90}
        data['runtime']['provider']={'ready':False,'retry_after_seconds':8,'last_error':'rate_limited'}
        data['current']['answer_status']={'waiting_for_provider':True}
        model.update(data,100)
        self.assertEqual(model.banner_text(pending=True,now=100),
            '[Model rate limit reached. Your message is queued; retrying in 8s]')
        self.assertEqual(model.last_message,'Hello, God!')
        data['runtime']['provider'].update(ready=True,retry_after_seconds=0)
        data['current']['answer_status']={}
        model.update(data,100)
        self.assertEqual(model.banner_text(now=100),'Hello, God!')
        data['runtime']['provider'].update(ready=False,retry_after_seconds=2,last_error='tool_context_unavailable')
        model.update(data,100)
        self.assertEqual(model.banner_text(now=100),
            '[Game data was interrupted. Chat ready again in 2s]')
        data['runtime']['provider'].update(disabled=True)
        model.update(data,100)
        self.assertEqual(model.wait_notice,'')

    def test_scene_lock_hides_even_with_stale_or_missing_creature_data(self):
        for changes in ({'observed_at':90},{'creature_id':None}):
            model=OverlayModel();model.update(payload(),100)
            data=payload(**changes)
            data['source']['diagnostics']={'script_camera_owned':True}
            model.update(data,100)
            self.assertFalse(model.can_show())
            model.read_gap()
            self.assertFalse(model.can_show())
            model.update(payload(),100)
            self.assertTrue(model.can_show())

    def test_unverified_scene_hides_chat_without_treating_plain_read_gaps_as_scenes(self):
        model=OverlayModel();model.update(payload(),100)
        data=payload();data['current']['gate']={'reasons':['scene_state_unverified']}
        model.update(data,100)
        self.assertFalse(model.can_show())
        self.assertFalse(model.can_send(100))
        model.update(payload(),100)
        self.assertTrue(model.can_show())

    def test_script_ownership_alone_hides_chat_then_restores_it(self):
        model=OverlayModel();data=payload(cutscene=False,script_owns_creature=False)
        model.update(data,100)
        self.assertTrue(model.can_show())
        data['source']['observation']['script_owns_creature']=True
        data['current']['question_unavailable']='scripted_creature'
        model.update(data,100)
        self.assertFalse(model.can_show())
        self.assertFalse(model.can_send(100))
        data['source']['observation']['script_owns_creature']=False
        data['current']['question_unavailable']=None
        model.update(data,100)
        self.assertTrue(model.can_show())
        self.assertTrue(model.can_send(100))

    def test_input_history_bounded_deduplicated_and_restores_unsent_draft(self):
        history=ChatInputHistory()
        self.assertEqual(history.move(-1,'draft'),'draft')
        for n in range(12):history.submitted('message '+str(n))
        history.submitted('message 11')
        self.assertEqual(len(history.items),10)
        self.assertEqual(history.move(-1,'unfinished'),'message 11')
        self.assertEqual(history.move(-1,'message 11'),'message 10')
        self.assertEqual(history.move(1,'edited historical message'),'message 11')
        self.assertEqual(history.move(1,'message 11'),'unfinished')
        for n in range(20):old=history.move(-1,'unfinished')
        self.assertEqual(old,'message 2')
        history.submitted('failed request can be recalled')
        self.assertEqual(history.move(-1,''),'failed request can be recalled')

    def test_failed_send_explains_provider_error_without_showing_raw_errors(self):
        model=OverlayModel();data=payload()
        data['runtime']['provider']={'last_error':'invalid_tool_call'}
        model.update(data,100)
        self.assertIn('tool call failed',model.send_failure_notice())
        self.assertTrue(model.send_failure_notice().startswith('[ERROR: '))
        self.assertTrue(model.send_failure_notice().endswith(']'))
        data['runtime']['provider']['last_error']='synthetic-private-provider-detail'
        model.update(data,100)
        self.assertNotIn('synthetic-private',model.send_failure_notice())

    def test_persistent_message_and_call_indicator_are_independent(self):
        model=OverlayModel();data=payload()
        data['runtime'].update(mode='live_llm_chat',active_requests=1)
        data['current']['speech']['last_message']={'text':'I am nearby 😕','created_at':90}
        data['current']['speech']['current']=None
        model.update(data,100)
        self.assertEqual(model.banner_text(now=100),'I am nearby')
        self.assertEqual(model.call_indicator(100),'●')
        self.assertFalse(model.is_thinking(100))  # automatic tick, not a direct answer
        self.assertEqual(model.banner_text(pending=True,now=100),'...')
        self.assertEqual(model.banner_text(pending=True,now=100.5),'.')
        self.assertEqual(model.chat_title,'Chat with creature')
        data['source']['observation']['observed_at']=130
        data['runtime']['active_requests']=0
        model.update(data,130)
        self.assertEqual(model.banner_text(now=130),'I am nearby')
        self.assertEqual(model.call_indicator(130),'')
        model.accept_message({'text':'😕  ','created_at':131})
        self.assertEqual(model.banner_text(now=130),'I am nearby')
        model.accept_message({'text':'New reply','created_at':132})
        model.accept_message({'text':'Old poll','created_at':129})
        self.assertEqual(model.banner_text(now=130),'New reply')
        self.assertEqual(model.banner_text(now=140),'New reply')
        data['source']['observation'].update(observed_at=140,creature_id='other')
        data['current']['speech']['last_message']=None
        model.update(data,140)
        self.assertEqual(model.banner_text(now=140),'')

    def test_indicator_only_shows_a_circle_during_active_calls(self):
        model=OverlayModel();data=payload()
        data['runtime'].update(mode='live_llm_chat',active_requests=0,external_model_calls=4)
        model.update(data,100)
        self.assertEqual(model.call_indicator(100),'')
        self.assertEqual(model.call_indicator(100,pending=True),'')
        data['runtime']['external_model_calls']=5
        model.update(data,100.2)
        self.assertEqual(model.call_indicator(100.2),'')
        data['source']['observation']['observed_at']=101
        model.update(data,101)
        self.assertEqual(model.call_indicator(101),'')
        data['source']['observation']['observed_at']=102
        model.update(data,102)
        self.assertEqual(model.call_indicator(102),'')
        data['runtime']['active_requests']=1
        model.update(data,102)
        self.assertEqual(model.call_state(102),'active')
        self.assertEqual(model.call_indicator(102),'●')
        self.assertEqual(model.call_indicator(102.5),'●')
        data['runtime'].update(active_requests=0,external_model_calls=0)
        model.update(data,102.5)
        self.assertEqual(model.call_indicator(102.5),'')
        self.assertEqual(model.call_indicator(110),'')

    def test_banner_never_projects_tool_calls_decisions_or_memory(self):
        data=payload();data['runtime'].update(mode='live_llm_chat',active_requests=1,
            provider={'tool_trace':[{'tool':'update_memory','arguments':{'memory':'Private lesson'}}]},
            action_dispatch={'candidate':'approach_hand','status':'queued'})
        data['current']['memory']={'memory':'Private lesson'}
        data['current']['speech']['last_message']={'text':'Coming over.','created_at':100,'source':'action_comment'}
        model=OverlayModel();model.update(data,100)
        self.assertEqual(model.banner_text(now=100),'Coming over.')
        self.assertNotIn('approach_hand',model.banner_text(now=100))
        self.assertNotIn('Private lesson',model.banner_text(now=100))

    def test_speaker_name_refreshes_and_falls_back_when_absent(self):
        model=OverlayModel(); data=payload()
        self.assertEqual(model.speaker_name,'Creature')
        self.assertEqual(model.chat_title,'Chat with creature')
        for name,expected in [('Pebble','Pebble'),('Fern','Fern'),(None,'Creature'),('  ','Creature')]:
            data['current']['character_names']={'creature_name':name}
            model.update(data,100)
            self.assertEqual(model.speaker_name,expected)
            self.assertEqual(model.chat_title,'Chat with '+(expected if expected!='Creature' else 'creature'))
        data['current']['character_names']={'creature_name':'Pebble'}
        model.update(data,100); model.disconnect()
        self.assertEqual(model.speaker_name,'Creature')

    def test_thinking_and_retained_answer_follow_freshness_and_scene_availability(self):
        data=payload();data['current']['answer_status']={'thinking':True}
        data['current']['speech']['current']['expires_at']=99
        data['current']['speech']['last_answer']={'text':'The full earlier answer.'}
        model=OverlayModel();model.update(data,100)
        self.assertTrue(model.is_thinking(100))
        self.assertEqual(model.visible_speech(100),'')
        self.assertEqual(model.readable_answer(100),'The full earlier answer.')
        self.assertFalse(model.is_thinking(102))
        self.assertEqual(model.readable_answer(102),'')
        data['current']['question_unavailable']='cutscene'
        model.update(data,100)
        self.assertFalse(model.is_thinking(100))
        self.assertEqual(model.readable_answer(100),'')
        data['current']['question_unavailable']=None
        data['current']['answer_status']={'thinking':False}
        model.update(data,100)
        self.assertFalse(model.is_thinking(100))
        self.assertEqual(model.readable_answer(100),'The full earlier answer.')
        for field in ('paused','in_temple','cutscene','loading','script_owns_creature'):
            data['source']['observation'][field]=True
            model.update(data,100)
            self.assertFalse(model.is_thinking(100))
            self.assertFalse(model.can_send(100))
            del data['source']['observation'][field]
        model.disconnect()
        self.assertEqual(model.readable_answer(100),'')

    def test_real_llm_mode_uses_same_freshness_and_identity_guards(self):
        data=payload();data['runtime']['mode']='live_llm_chat'
        model=OverlayModel();model.update(data,100)
        self.assertTrue(model.can_send(100))
        self.assertFalse(model.is_dummy)
        self.assertFalse(model.can_send(102))
        data['runtime']['mode']='unknown'
        model.update(data,100)
        self.assertFalse(model.can_send(100))

    def test_reads_game_identity_and_speech_without_refreshing_source_time(self):
        model=OverlayModel(); data=payload()
        model.update(data,100)
        self.assertEqual(model.game_pid,123)
        self.assertEqual(model.identity,('123:1','monkey'))
        self.assertTrue(model.can_send(100))
        self.assertEqual(model.visible_speech(100),'Hello, God!')
        self.assertFalse(model.can_send(102))
        self.assertEqual(model.visible_speech(102),'Hello, God!')
        model.update(data,102)
        self.assertEqual(model.identity,('123:1','monkey'))
        self.assertFalse(model.can_send(102))
        self.assertEqual(model.game_pid,123)  # placement only, never chat permission

    def test_read_gaps_keep_banner_but_never_grant_send_permission(self):
        model=OverlayModel();data=payload()
        data['current']['speech']['last_message']={'text':'Still here.','created_at':100}
        model.update(data,100)
        for gap in (None,payload(now=90),payload(creature_id=None),payload(connected=False),
                    {'runtime':{'mode':'live_dummy_preview'},'source':None,'current':{}}):
            if gap is None:model.read_gap()
            else:model.update(gap,101)
            self.assertTrue(model.can_show())
            self.assertEqual(model.banner_text(now=101),'Still here.')
            self.assertFalse(model.can_send(101))
        model.update(payload(now=102),102)
        self.assertTrue(model.can_send(102))

    def test_scene_lock_survives_missing_source_until_fresh_outside_reading(self):
        for reason in ('paused','cutscene','temple'):
            model=OverlayModel();model.update(payload(),100)
            data={'runtime':{'mode':'live_dummy_preview'},'source':None,
                  'current':{'gate':{'reasons':[reason]}}}
            model.update(data,100)
            self.assertFalse(model.can_show())
            model.read_gap()
            model.update(payload(now=90),101)
            self.assertFalse(model.can_show())
            model.update(payload(now=102),102)
            self.assertTrue(model.can_show())
            self.assertTrue(model.can_send(102))

    def test_control_and_action_denials_do_not_hide_the_banner(self):
        model=OverlayModel();data=payload(player_has_control=False)
        data['current']['question_unavailable']='control_locked'
        model.update(data,100)
        self.assertTrue(model.can_show())
        self.assertFalse(model.can_send(100))
        self.assertEqual(model.banner_text(now=100),'Hello, God!')

    def test_missing_identity_disconnect_and_server_denial_disable_chat(self):
        for data in (payload(creature_id=None),payload(connected=False),
                     {'runtime':{'mode':'live_dummy_preview'},'source':None,'current':{}},{}):
            model=OverlayModel();model.update(data,100)
            self.assertFalse(model.can_send(100))
        data=payload();data['current']['question_unavailable']='no_creature_available'
        model=OverlayModel();model.update(data,100)
        self.assertFalse(model.can_send(100))

    def test_expired_or_malformed_speech_not_rendered(self):
        for expiry in (99,float('nan'),'tomorrow'):
            data=payload();data['current']['speech']['current']['expires_at']=expiry
            model=OverlayModel();model.update(data,100)
            self.assertEqual(model.visible_speech(100),'')
        data=payload();data['current']['speech']['current']['expires_at']=None
        model=OverlayModel();model.update(data,100)
        self.assertEqual(model.visible_speech(100),'Hello, God!')

    def test_bounds_follow_small_window_negative_monitor_and_resize(self):
        for form_height in (28,33,42):
            closed=overlay_bounds((0,0,800,600),False,True)
            opened=overlay_bounds((0,0,800,600),True,True,input_height=form_height)
            self.assertEqual(opened[:3],closed[:3])
            self.assertEqual(opened[3]-form_height,closed[3])
        for rect in ((0,0,800,600),(-1000,100,-200,700),(0,0,320,240)):
            for expanded in (True,False):
                x,y,w,h=overlay_bounds(rect,expanded,True)
                self.assertGreaterEqual(x,rect[0]);self.assertGreaterEqual(y,rect[1])
                self.assertLessEqual(x+w,rect[2]);self.assertLessEqual(y+h,rect[3])
                self.assertEqual(y,rect[1])
        self.assertIsNone(overlay_bounds((0,0,50,30)))

    def test_remote_urls_are_rejected(self):
        with self.assertRaises(ValueError):OverlayClient('https://example.com/')


class OverlayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlay_routes_question_and_rejects_changed_identity(self):
        with TemporaryDirectory() as directory:
            runtime=LiveDummyRuntime(directory,names_reader=ProfileNames)
            now=time.monotonic()
            frame={'observation':{'session':'123:1','sequence':1,'observed_at':now,
                'creature_id':'monkey','creature_present':True,'connected':True,
                'paused':False,'in_temple':False},'diagnostics':{'script_camera_owned':False},
                'prompt':{'system':'Test','context':{'learned_miracles':['heal']}}}
            runtime.ingest(frame)
            server,url=make_server(runtime,post_handlers={'question':runtime.handler(runtime.question)},page_name='qa_live.html')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=OverlayClient(url)
            try:
                state=await asyncio.to_thread(client.state)
                model=OverlayModel();model.update(state)
                self.assertTrue(model.can_send())
                reply=await asyncio.to_thread(client.ask,'status',model.identity)
                self.assertIn('heal',reply['reply']['answer'])
                self.assertEqual(reply['game_actions'],0)
                before=runtime.broker.calls_started
                with self.assertRaises(OSError):
                    await asyncio.to_thread(client.ask,'status',('123:old','monkey'))
                self.assertEqual(runtime.broker.calls_started,before)
            finally:
                await asyncio.to_thread(server.shutdown);server.server_close();thread.join(2)
                await runtime.close()
