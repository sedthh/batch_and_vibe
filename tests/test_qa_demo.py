import asyncio
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.parse import urlsplit

from bwv.qa import Inspector, make_server
from bwv.qa_demo import DemoRuntime


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=TemporaryDirectory()
        self.demo=DemoRuntime(self.directory.name,speech_seconds=.02)

    async def asyncTearDown(self):
        await self.demo.close()
        self.directory.cleanup()

    async def test_notes_persist_separately_and_speech_expires(self):
        await self.demo.question({'question':'remember: Be kind'})
        reply=await self.demo.question({'question':'memory'})
        self.assertIn('Be kind',reply['reply']['answer'])
        await self.demo.question({'question':'say: Hello!'})
        self.assertEqual(self.demo.inspector.payload()['current']['speech']['current']['text'],'Hello!')
        await asyncio.sleep(.03)
        self.demo.refresh()
        self.assertIsNone(self.demo.inspector.payload()['current']['speech']['current'])
        await self.demo.close()
        self.demo=DemoRuntime(self.directory.name)
        self.assertEqual(self.demo.memory.notes,'Be kind')
        self.assertEqual([p.name for p in Path(self.directory.name).iterdir()],['dummy-memory.json'])
        self.assertNotIn(self.directory.name,json.dumps(self.demo.inspector.payload()))

    async def test_empty_and_blocked_skip_automatic_but_allow_questions(self):
        for scenario in ('empty','paused','cutscene','temple'):
            await self.demo.configure({'scenario':scenario})
            count=self.demo.broker.calls_started
            await self.demo.step()
            await asyncio.sleep(.01)
            self.assertEqual(self.demo.broker.calls_started,count)
            if scenario=='paused':
                with self.assertRaises(ValueError):await self.demo.question({'question':'status'})
                self.assertEqual(self.demo.broker.calls_started,count)
                continue
            await self.demo.question({'question':'status'})
            self.assertEqual(self.demo.broker.calls_started,count+1)
            request=self.demo.provider.requests[-1]
            self.assertEqual(request.current['snapshot'],{})
            self.assertTrue(request.current['conversation_only'])

    async def test_no_creature_rejects_before_provider_call(self):
        await self.demo.configure({'scenario':'no_creature'})
        with self.assertRaisesRegex(ValueError,'no_creature'):
            await self.demo.question({'question':'Hello'})
        await self.demo.step()
        self.assertEqual(self.demo.broker.calls_started,0)

    async def test_automatic_tick_and_fresh_snapshot_after_resume(self):
        await self.demo.step()
        async with asyncio.timeout(2):
            while len(self.demo.memory.history) < 1:
                await asyncio.sleep(.01)
        self.assertEqual(self.demo.broker.calls_started,1)
        await self.demo.configure({'scenario':'paused'})
        await self.demo.step()
        await self.demo.configure({'scenario':'hurt'})
        await self.demo.step()
        async with asyncio.timeout(2):
            while len(self.demo.memory.history) < 2:
                await asyncio.sleep(.01)
        self.assertEqual(self.demo.broker.calls_started,2)
        self.assertEqual(self.demo.provider.requests[-1].current['snapshot']['body_engine_units']['health'],.4)

    async def test_serial_queue_and_session_change_rejects_late_memory(self):
        self.demo.provider.delay=.03
        replies=await asyncio.gather(self.demo.question({'question':'one'}),self.demo.question({'question':'two'}))
        self.assertEqual(self.demo.provider.max_active,1)
        self.assertIn('one',replies[0]['reply']['answer'])
        pending=asyncio.create_task(self.demo.question({'question':'remember: stale'}))
        await asyncio.sleep(.01)
        await self.demo.configure({'new_session':True})
        with self.assertRaises(RuntimeError):
            await pending
        self.assertEqual(self.demo.memory.notes,'')
        self.assertIsNone(self.demo.broker.speech.snapshot(0)['current'])

    async def test_invalid_configuration_is_atomic(self):
        with self.assertRaises(ValueError):
            await self.demo.configure({'scenario':'hurt','delay_seconds':50})
        self.assertEqual(self.demo.scenario,'ready')

    async def test_http_chat_route_is_demo_only_and_origin_checked(self):
        server,url=make_server(self.demo.inspector,post_handlers={
            'question':self.demo.handler(self.demo.question)},page_name='qa_demo.html')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        parsed=urlsplit(url)
        def post(origin):
            connection=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=3)
            try:
                connection.request('POST',parsed.path+'question',json.dumps({'question':'Hello'}),
                    {'Origin':origin,'Content-Type':'application/json'})
                response=connection.getresponse()
                return response.status,response.read()
            finally:connection.close()
        try:
            status,body=await asyncio.to_thread(post,f'http://127.0.0.1:{parsed.port}')
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['mode'],'offline_dummy_demo')
            self.assertEqual((await asyncio.to_thread(post,'https://example.com'))[0],403)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close();thread.join(2)
        server,url=make_server(Inspector())
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        parsed=urlsplit(url)
        try:
            self.assertEqual((await asyncio.to_thread(post,f'http://127.0.0.1:{parsed.port}'))[0],404)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close();thread.join(2)
