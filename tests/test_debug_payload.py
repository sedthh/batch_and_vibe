"""Debugging must not copy whole worlds or histories for collapsed panels."""
from copy import deepcopy
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
