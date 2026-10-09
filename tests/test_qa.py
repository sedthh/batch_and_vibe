from dataclasses import replace
import http.client
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from bwv.control import ControlObservation, Lease
from bwv.planner import PlanningRequest
from bwv.prompt import preview_prompt
from bwv.qa import Inspector, make_server
from bwv.training import TrainingContext, TrainingFeedback


class InspectorTests(unittest.TestCase):
    def test_model_preview_and_scheduled_preview_preserve_raw_observer_fields(self):
        from test_control import playable
        from bwv.experience import Experience, BodyState
        from bwv.surroundings import SurroundingsContext, NearbyObject
        inspector=Inspector()
        obs=playable(100,experience=Experience(BodyState(health=.8,energy=.6,exhaustion=.2)),
                     surroundings=SurroundingsContext(True,(NearbyObject('tree','tree',5),)))
        inspector.update(obs,{},None,None,100)
        frame=inspector.payload()['current']
        self.assertEqual(frame['model_context']['self'],{'damage':20,'hunger':40,'tiredness':20})
        self.assertEqual(frame['model_context']['surroundings'],{'trees':1})
        self.assertEqual(frame['observation']['experience']['body']['health'],.8)
        self.assertIsNotNone(frame['model_update'])
        self.assertEqual(frame['model_update']['snapshot']['self'],frame['model_context']['self'])
        self.assertNotIn('_engine_units',json.dumps(frame['model_update']))

    def test_debug_page_opt_in_preserves_local_api(self):
        for debug in (False, True):
            server, url = make_server(Inspector(), debug=debug,
                post_handlers={'question': lambda payload: {'received': payload['question']}})
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            parsed = urlsplit(url)
            try:
                connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=2)
                for suffix, expected in (('', 200 if debug else 404), ('state', 200)):
                    connection.request('GET', parsed.path+suffix)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                connection.request('POST', parsed.path+'question', json.dumps({'question':'Hello'}),
                    {'Origin':f'http://127.0.0.1:{parsed.port}', 'Content-Type':'application/json'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {'received':'Hello'})
                connection.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(2)

    def test_unknown_source_age_remains_blocked_and_serves_valid_json(self):
        from bwv.control import from_observer
        inspector = Inspector()
        observation = from_observer({'protocol': 1, 'pid': 123, 'session': 1}, 10)
        inspector.update(observation, {}, None, None, 10)
        frame = inspector.payload()['current']
        self.assertIsNone(frame['observation']['observed_at'])
        self.assertIn('invalid_observation', frame['gate']['reasons'])
        self.assertFalse(frame['gate']['allowed'])
        self.assertEqual(observation.observed_at, -float('inf'))
        server, url = make_server(inspector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        parsed = urlsplit(url)
        try:
            for suffix in ('state', f"state?frame={frame['id']}"):
                connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=2)
                try:
                    connection.request('GET', parsed.path + suffix)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read())
                    self.assertIsNone(payload['current']['observation']['observed_at'])
                    json.dumps(payload, allow_nan=False)
                finally:
                    connection.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_shared_memory_and_speech_appear_in_debug_and_history(self):
        from bwv.conversation import ConversationMemory, CreatureSpeech
        from test_control import playable
        memory=ConversationMemory();speech=CreatureSpeech(2)
        inspector=Inspector(memory=memory,speech=speech)
        inspector.update(playable(0),{},None,None,0)
        memory.record({'god_question':'Hello'},'Hi',{'candidate':None},'A lesson.')
        speech.publish('Hi',.1,{'candidate':None},source='god_question')
        inspector.update(playable(.1),{},None,None,.1)
        frame=inspector.payload()['current'];saved_id=frame['id']
        self.assertEqual(frame['memory']['memory'],'A lesson.')
        self.assertEqual(frame['speech']['current']['text'],'Hi')
        preview=inspector.preview_question('What did you learn?',.1)
        self.assertIn('A lesson.',preview['messages'][0]['content'])
        inspector.update(playable(3),{},None,None,3)
        self.assertIsNone(inspector.payload()['current']['speech']['current'])
        self.assertEqual(inspector.payload(saved_id)['current']['speech']['current']['text'],'Hi')
        memory.clear()
        self.assertEqual(inspector.payload(saved_id)['current']['memory']['memory'],'A lesson.')

    def test_question_preview_uses_latest_state_and_requires_available_creature(self):
        from test_control import playable
        inspector=Inspector()
        inspector.update(playable(0,in_temple=True),{},None,None,0)
        result=inspector.preview_question('Can you hear me?',0)
        current=json.loads(result['messages'][-1]['content'])
        self.assertEqual(current['god_question'],'Can you hear me?')
        self.assertEqual(current['snapshot'],{})
        self.assertEqual(result['provider_calls'],0)
        inspector.update(playable(1,creature_present=False,creature_id=None),{},None,None,1)
        with self.assertRaises(ValueError):inspector.preview_question('Hello?',1)

    def test_question_post_enforces_origin_path_size_and_creature_availability(self):
        from test_control import playable
        inspector=Inspector();now=time.monotonic()
        inspector.update(playable(now),{},None,None,now)
        server,url=make_server(inspector)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        parsed=urlsplit(url);origin=f'http://127.0.0.1:{parsed.port}'
        def post(body,headers,path=None):
            connection=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=2)
            try:
                connection.request('POST',path or parsed.path+'question-preview',body,headers)
                response=connection.getresponse();data=response.read()
                return response.status,data
            finally:connection.close()
        try:
            headers={'Origin':origin,'Content-Type':'application/json'}
            status,body=post('{"question":"Hello"}',headers)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['mode'],'preview_only_not_sent')
            for body,h,path,expected in (
                ('{}',headers,None,400),('{"question":""}',headers,None,400),
                ('{"question":"Hi"}',{},None,403),
                ('{"question":"Hi"}',dict(headers,Origin='https://evil.example'),None,403),
                ('{}',headers,'/question-preview',404),('x'*8193,headers,None,400)):
                with self.subTest(body=body[:40],headers=h,path=path):
                    self.assertEqual(post(body,h,path)[0],expected)
            now=time.monotonic();inspector.update(playable(now,creature_present=False),{},None,None,now)
            self.assertEqual(post('{"question":"Hello"}',headers)[0],400)
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_preview_matches_real_prompt_without_issuing_lease(self):
        obs = ControlObservation('session', 0, 0, creature_id='monkey',
            training=TrainingContext(True, (TrainingFeedback('one', 'praise', 0, magnitude=.1),)))
        request = PlanningRequest(Lease(1, obs.session, obs.creature_id), (), 'Resumed',
            obs.training, reflection_only=True, creature=obs.creature, leash=obs.leash,
            miracles=obs.miracles, holding=obs.holding)
        self.assertEqual(preview_prompt(obs, 'Resumed'), request.to_prompt())
        inspector = Inspector()
        with patch.object(inspector.gate, 'issue', side_effect=AssertionError('No lease for QA')):
            inspector.update(obs, {'raw_globals': ['private'], 'source_age_ms': 12}, None, None, 0)
        payload = inspector.payload()['current']
        self.assertFalse(payload['gate']['allowed'])
        self.assertEqual(payload['provider_calls'], 0)
        self.assertFalse(payload['provider_connected'])
        self.assertIn('player_has_control', payload['gate']['unverified_fields'])
        self.assertNotIn('raw_globals', json.dumps(payload))

    def test_unknown_checks_do_not_hide_verified_temple_exit_or_stop_preview_ticks(self):
        from bwv.holding import HoldingContext
        inspector = Inspector()
        obs = ControlObservation('session', 1, 0, connected=True, creature_id='monkey',
            paused=False, in_temple=False, holding=HoldingContext(False, hunger_desire=1))
        for now in (0, 15):
            inspector.update(replace(obs, sequence=int(now)+1, observed_at=now), {}, None, None, now)
        frame = inspector.payload()['current']
        self.assertEqual(frame['status_updates']['last_update']['number'], 2)
        self.assertEqual(frame['status_updates']['last_update']['trigger'], 'heartbeat')
        self.assertEqual(frame['status_updates']['last_update']['delivery'], 'preview_only_not_sent')
        self.assertFalse(frame['gate']['allowed'])
        self.assertEqual(frame['gate']['unverified_fields'], ['loading', 'world_ready',
            'creature_present', 'cutscene', 'script_owns_creature', 'player_has_control'])

    def test_history_keeps_events_after_expiry_and_does_not_replay_them_as_current(self):
        inspector = Inspector(limit=2)
        obs = ControlObservation('session', 0, 0)
        inspector.update(obs, {}, None, None, 0)
        praise = replace(obs, sequence=1, observed_at=.1,
            training=TrainingContext(True, (TrainingFeedback('one', 'praise', .1, magnitude=.1),)))
        inspector.update(praise, {}, None, None, .1)
        inspector.update(replace(obs, sequence=2, observed_at=.2), {}, None, None, .2)
        self.assertEqual(len(inspector.payload()['history']), 2)
        self.assertIsNone(inspector.payload(1)['current'])
        self.assertEqual(inspector.payload(2)['current']['observation']['training']['feedback'][0]['event_id'], 'one')
        self.assertEqual(inspector.payload()['current']['observation']['training']['feedback'], ())
        inspector.update(replace(obs, sequence=3, observed_at=.3), {'source_age_ms': 10}, None, None, .3)
        self.assertEqual(inspector.payload()['current']['id'], 3)

    def test_latest_only_mode_preserves_live_context_without_retaining_snapshots(self):
        from test_control import playable
        live = Inspector(limit=0)
        debug = Inspector(limit=120)
        for sequence in range(150):
            now = sequence / 4
            obs = replace(playable(now), sequence=sequence, paused=bool(sequence % 2))
            for inspector in (live, debug):
                inspector.update(obs, {}, None, None, now)
            current = live.payload()['current']
            reference = debug.payload()['current']
            for key in ('observation', 'gate', 'prompt', 'status_updates', 'question_unavailable'):
                self.assertEqual(current[key], reference[key])
        self.assertEqual(live.payload()['history'], [])
        self.assertEqual(len(live.frames), 0)
        self.assertIsNone(live._signature)
        self.assertIsNone(live.payload(1)['current'])
        self.assertEqual(len(debug.frames), 120)
        self.assertEqual(live.payload()['current']['observation']['sequence'], 149)

    def test_latest_only_mode_keeps_memory_and_clears_stale_state_on_disconnect(self):
        from bwv.conversation import ConversationMemory
        from test_control import playable
        memory = ConversationMemory()
        memory.record({'god_question': 'Remember this'}, 'Okay', {'candidate': None}, 'A lesson.')
        inspector = Inspector(limit=0, memory=memory)
        inspector.update(playable(0), {}, None, None, 0)
        inspector.update(ControlObservation('disconnected', 0, 1), {}, None, 'OSError', 1)
        current = inspector.payload()['current']
        self.assertIn('disconnected', current['gate']['reasons'])
        self.assertEqual(current['diagnostics']['error'], 'OSError')
        self.assertEqual(current['memory']['memory'], 'A lesson.')
        self.assertEqual(inspector.payload()['history'], [])

    def test_disconnect_clears_current_context_and_marks_unknown(self):
        inspector = Inspector()
        obs = ControlObservation('old', 0, 0, creature_id='old-creature')
        inspector.update(obs, {}, None, None, 0)
        inspector.update(ControlObservation('disconnected', 0, 1), {}, None, 'OSError', 1)
        frame = inspector.payload()['current']
        self.assertIn('disconnected', frame['gate']['reasons'])
        self.assertIsNone(frame['observation']['creature_id'])
        self.assertEqual(frame['diagnostics']['error'], 'OSError')

    def test_server_only_serves_token_path_and_correct_host_without_cors(self):
        inspector = Inspector()
        server, url = make_server(inspector, debug=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        parsed = urlsplit(url)
        try:
            for path, headers, code in (
                (parsed.path, {}, 200), (parsed.path+'state', {}, 200),
                ('/', {}, 404), (parsed.path, {'Host': 'untrusted.test'}, 403),
                (parsed.path, {'Origin': 'https://untrusted.test'}, 403),
                (parsed.path+'state?frame=bad', {}, 400)):
                with self.subTest(path=path, headers=headers):
                    connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=2)
                    try:
                        connection.request('GET', path, headers=headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, code)
                        self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
                        response.read()
                    finally:
                        connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
