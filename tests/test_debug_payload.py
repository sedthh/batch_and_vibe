"""Debugging must not copy whole worlds or histories for collapsed panels."""
from copy import deepcopy
from dataclasses import asdict, replace
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from bwv.conversation import ConversationMemory
from bwv.qa import Inspector, make_server
from bwv.qa_live import LiveChatRuntime
from test_control import playable


class NoCopy(dict):
    def __deepcopy__(self, memo):
        raise AssertionError('Collapsed diagnostics must not be copied')


class DebugPayloadTests(unittest.TestCase):
    def test_shared_details_remain_current_and_do_not_reencode_on_fast_updates(self):
        from bwv.surroundings import NearbyObject, SurroundingsContext
        from bwv.qa import _debug_value
        objects=tuple(NearbyObject(f'object-{i}','tree',i+1) for i in range(300))
        details=SurroundingsContext(available=True,known_objects=objects,
                                   nearby_known_objects=objects[:100])
        obs=playable(100,surroundings=details)
        observer=Inspector(limit=0)
        observer.update(obs,{},None,None,100)
        first=observer.latest_frame()
        with patch('bwv.qa._debug_value',wraps=_debug_value) as encode:
            for i in range(1,21):
                now=100+i*.25
                current=replace(obs,sequence=i,observed_at=now,paused=bool(i%2))
                observer.update(current,{},None,None,now)
                self.assertEqual(observer.latest_frame()['observation'],_debug_value(asdict(current)))
            self.assertFalse(any(call.args[0] is details for call in encode.call_args_list))
        self.assertEqual(first['observation']['observed_at'],100)
        replacement=replace(details,known_objects=(),nearby_known_objects=())
        observer.update(replace(obs,observed_at=106,surroundings=replacement),{},None,None,106)
        self.assertEqual(observer.latest_frame()['observation']['surroundings']['known_objects'],())
        self.assertEqual(len(first['observation']['surroundings']['known_objects']),300)
        observer.update(replace(obs,session='new-game',observed_at=107),{},None,None,107)
        self.assertEqual(observer.latest_frame()['observation'],_debug_value(asdict(replace(obs,session='new-game',observed_at=107))))
        self.assertEqual(len(observer._detail_encoding),3)

    def test_cached_details_preserve_null_sanitization_and_raw_observation(self):
        observer=Inspector(limit=0)
        obs=playable(100)
        unavailable=replace(obs,observed_at=float('-inf'))
        observer.update(unavailable,{},None,None,100)
        self.assertIsNone(observer.latest_frame()['observation']['observed_at'])
        self.assertEqual(unavailable.observed_at,float('-inf'))

    def test_observer_summary_skips_raw_catalog_and_closed_memory_panel(self):
        observer=Inspector()
        observer.update(playable(0),{},None,None,0)
        full=observer.payload()
        observer.latest['prompt']=NoCopy({'system':'large immutable prompt'})
        observer.latest['diagnostics']['action_probe']=NoCopy({'catalog':'large'})
        observer.latest['memory']=NoCopy({'recent_pairs':['large history']})
        observer.latest['observation']['raw_objects']=NoCopy({'world':'large'})
        compact=observer.debug_payload()
        self.assertNotIn('prompt',compact['current'])
        self.assertNotIn('memory',compact['current'])
        self.assertNotIn('action_probe',compact['current']['diagnostics'])
        self.assertEqual(compact['current']['model_context'],full['current']['model_context'])
        self.assertEqual(compact['history'],full['history'])
        compact['current']['model_context']['changed']=True
        self.assertNotIn('changed',observer.latest['model_context'])
        observer.latest['memory']={'recent_pairs':['requested history']}
        self.assertEqual(observer.debug_payload(['history'])['current']['memory'],observer.latest['memory'])
        self.assertIs(observer.debug_payload(['full'])['current'],observer.latest)

    def setUp(self):
        self.runtime = LiveChatRuntime.__new__(LiveChatRuntime)
        self.runtime._state_lock = threading.Lock()
        self.runtime._published = {
            'current': {
                'prompt': {'system': 'Fixture system'},
                'model_context': {'surroundings': {'trees': 12}},
                'memory': {'memory': 'Remember the field.', 'entries': [],
                           'recent_pairs': [{'input': {'snapshot': {'trees': 12}},
                                             'decision': {'candidate': 'water'},
                                             'action_status': 'plan_ended'}]},
                'dialogue': [{'input': {'god_question': 'Water it', 'snapshot': {'trees': 12}},
                              'answer': 'I will try.', 'say': ''}],
                'status_updates': {'tier': 'active', 'last_update': {'llm_input': {'trees': 12}}},
            },
            'runtime': {'settings': {'model': 'fixture'}, 'last_request': [{'content': 'Fixture request'}],
                        'active_requests': 1, 'provider': {'errors': [{'code': 'fixture_error'}],
                            'tool_trace': [{'name': 'inspect_self'}], 'offered_tools': [{'name': 'move_to'}],
                            'token_usage': {'input_tokens': 123}}},
            'source': {'observation': {'raw_objects': list(range(1000))}},
        }

    def test_compact_keeps_status_notes_errors_and_counts_without_copying_hidden_data(self):
        original = deepcopy(self.runtime._published)
        full = self.runtime._published
        full['source'] = NoCopy(full['source'])
        full['current']['prompt'] = NoCopy(full['current']['prompt'])
        full['current']['memory']['recent_pairs'][0]['input'] = NoCopy({'large': 'history'})
        full['current']['dialogue'][0]['input']['snapshot'] = NoCopy({'large': 'world'})
        full['current']['status_updates']['last_update'] = NoCopy({'large': 'update'})
        full['runtime']['settings'] = NoCopy(full['runtime']['settings'])
        full['runtime']['last_request'][0] = NoCopy(full['runtime']['last_request'][0])
        full['runtime']['provider']['tool_trace'][0] = NoCopy({'large': 'lookup'})
        compact = self.runtime.debug_payload()
        self.assertNotIn('source', compact)
        self.assertNotIn('prompt', compact['current'])
        self.assertNotIn('recent_pairs', compact['current']['memory'])
        self.assertEqual(compact['current']['memory']['recent_pair_count'], 1)
        self.assertEqual(compact['current']['memory']['memory'], 'Remember the field.')
        self.assertEqual(compact['current']['last_decision']['status'], 'plan_ended')
        self.assertEqual(compact['current']['dialogue'][0]['input'], {'god_question': 'Water it'})
        self.assertEqual(compact['current']['status_updates'], {'tier': 'active'})
        self.assertEqual(compact['runtime']['provider']['errors'], [{'code': 'fixture_error'}])
        self.assertEqual(compact['runtime']['provider']['token_usage'], {'input_tokens': 123})
        compact['current']['model_context']['surroundings']['trees'] = 99
        self.assertEqual(full['current']['model_context'], original['current']['model_context'])
        self.assertIn('recent_pairs', full['current']['memory'])
        self.assertNotIn('recent_pair_count', full['current']['memory'])

    def test_expanding_panels_restores_exact_raw_sections_and_full_state(self):
        full = self.runtime.payload()
        for section, key in (('settings', 'settings'), ('request', 'last_request')):
            selected = self.runtime.debug_payload([section])
            self.assertEqual(selected['runtime'][key], full['runtime'][key])
        selected = self.runtime.debug_payload(['history', 'tools'])
        self.assertEqual(selected['current']['memory']['recent_pairs'], full['current']['memory']['recent_pairs'])
        self.assertEqual(selected['runtime']['provider']['tool_trace'], full['runtime']['provider']['tool_trace'])
        self.assertEqual(selected['runtime']['provider']['offered_tools'], full['runtime']['provider']['offered_tools'])
        self.assertEqual(self.runtime.debug_payload(['full']), full)
        self.assertEqual(self.runtime.payload(), full)
        with self.assertRaises(ValueError):self.runtime.debug_payload(['credentials'])

    def test_http_compact_route_keeps_existing_api_and_origin_checks(self):
        server, url = make_server(self.runtime, debug=True, page_name='qa_live.html', observer=Inspector())
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        parsed = urlsplit(url)
        try:
            for route, code, headers in (
                ('llm/debug-state?sections=', 200, {}),
                ('llm/debug-state?sections=history%2Ctools', 200, {}),
                ('llm/state', 200, {}),
                ('llm/debug-state?sections=unknown', 400, {}),
                ('llm/debug-state?sections=history&sections=tools', 400, {}),
                ('llm/debug-state?other=value', 400, {}),
                ('llm/debug-state', 403, {'Origin': 'https://untrusted.example'}),
            ):
                with self.subTest(route=route, headers=headers):
                    connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=2)
                    try:
                        connection.request('GET', parsed.path + route, headers=headers)
                        response = connection.getresponse(); data = response.read()
                        self.assertEqual(response.status, code)
                        if code == 200:
                            body = json.loads(data)
                            self.assertEqual('source' in body, route == 'llm/state')
                            self.assertEqual('recent_pairs' in body['current']['memory'],
                                             route != 'llm/debug-state?sections=')
                    finally:connection.close()
        finally:server.shutdown(); server.server_close(); thread.join(2)

    def test_notes_prompt_does_not_copy_conversation_history(self):
        memory = ConversationMemory()
        memory.record({'god_question': 'Remember'}, 'Okay', {'candidate': None}, 'Keep this note.')
        expected = memory.system_prompt('Fixture system')
        with patch.object(memory, 'snapshot', side_effect=AssertionError('Do not copy history for notes')):
            self.assertEqual(memory.system_prompt('Fixture system'), expected)
        self.assertEqual(len(memory.history), 1)

    def test_history_sampling_does_not_delay_current_observations_or_change_old_frames(self):
        inspector = Inspector(limit=120, history_interval=1.0)
        for i in range(11):
            inspector.update(playable(i / 10), {}, None, None, i / 10)
            self.assertEqual(inspector.latest_frame()['observation']['observed_at'], i / 10)
        payload = inspector.payload()
        self.assertEqual(len(payload['history']), 2)
        first_id = payload['history'][-1]['id']
        self.assertEqual(inspector.payload(first_id)['current']['observation']['observed_at'], 0)
        inspector.set_history_limit(0)
        inspector.update(playable(1.1), {}, None, None, 1.1)
        self.assertEqual(inspector.payload()['history'], [])
        self.assertEqual(inspector.retained_bytes, 0)
        inspector.set_history_limit(120)
        inspector.update(playable(1.2), {}, None, None, 1.2)
        self.assertEqual(len(inspector.frames), 1)
