import asyncio
import json
import socket
import unittest
from unittest.mock import Mock
from urllib.request import urlopen

from bwv.debug_session import DebugSession
from bwv.qa import Inspector
from test_control import playable


def read_json(url):
    with urlopen(url, timeout=2) as response:
        return json.load(response)


class DebugSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Mock()
        self.runtime.payload.return_value = {'memory': 'fixture memory', 'active_request': True}
        self.observer = Inspector(limit=0)
        self.messages = []
        self.debug = DebugSession(emit=self.messages.append)
        await self.debug.attach(self.runtime, self.observer)

    async def asyncTearDown(self):
        await self.debug.close()

    async def test_toggle_preserves_runtime_and_starts_fresh_history(self):
        self.assertIsNone(self.debug.server)
        self.observer.update(playable(0), {}, None, None, 0)
        self.assertEqual(len(self.observer.frames), 0)
        await self.debug.configure(True)
        old_url = self.debug.url
        old_port = self.debug.server.server_port
        await self.debug.configure(True, old_port)
        self.assertEqual(self.debug.url, old_url)
        self.assertEqual(self.debug.server.server_address[0], '127.0.0.1')
        self.observer.update(playable(1), {}, None, None, 1)
        self.assertEqual(len(self.observer.frames), 1)
        state = await asyncio.to_thread(read_json, old_url + 'llm/state')
        self.assertTrue(state['active_request'])
        observer = await asyncio.to_thread(read_json, old_url + 'observer/state')
        self.assertEqual(len(observer['history']), 1)
        await self.debug.configure(False)
        self.assertIsNone(self.debug.url)
        self.assertEqual(len(self.observer.frames), 0)
        self.assertIsNotNone(self.observer.latest)
        with self.assertRaises(OSError):
            await asyncio.to_thread(socket.create_connection, ('127.0.0.1', old_port), .5)
        self.observer.update(playable(2), {}, None, None, 2)
        await self.debug.configure(True)
        self.assertNotEqual(self.debug.url, old_url)
        self.assertEqual(len(self.observer.frames), 0)
        self.assertIs(self.debug.runtime, self.runtime)
        self.runtime.close.assert_not_called()
        self.runtime.question.assert_not_called()
        self.runtime.reset.assert_not_called()
        self.assertEqual((await asyncio.to_thread(read_json, self.debug.url + 'llm/state'))['memory'], 'fixture memory')

    async def test_busy_port_keeps_existing_debugger_and_can_recover(self):
        await self.debug.configure(True)
        original = self.debug.url
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            with self.assertRaisesRegex(OSError, 'port may be in use'):
                await self.debug.configure(True, occupied.getsockname()[1])
        self.assertEqual(self.debug.url, original)
        self.assertTrue(self.debug.enabled)
        self.assertTrue((await asyncio.to_thread(read_json, original + 'llm/state'))['active_request'])
        await self.debug.configure(False)
        await self.debug.configure(True)
        self.assertTrue(self.debug.enabled)

    async def test_requested_startup_port_failure_does_not_stop_chat(self):
        await self.debug.close()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            self.debug = DebugSession(True, occupied.getsockname()[1], self.messages.append)
            await self.debug.attach(self.runtime, self.observer)
        self.assertFalse(self.debug.enabled)
        self.assertIsNone(self.debug.server)
        self.assertTrue(any('ERROR:' in line for line in self.messages))
        self.runtime.close.assert_not_called()
        await self.debug.configure(True)
        self.assertTrue(self.debug.enabled)

    async def test_invalid_ports_do_not_change_live_debugger(self):
        await self.debug.configure(True)
        original = self.debug.url
        for port in (-1, 65536, '8080', True):
            with self.assertRaises(ValueError):
                await self.debug.configure(True, port)
        self.assertEqual(self.debug.url, original)
        await self.debug.close()
        self.assertIsNone(self.debug.runtime)
        self.assertEqual(len(self.observer.frames), 0)
