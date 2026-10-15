import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from bwv.chat_pipe import ChatPipeServer, ChatPipeClient, parse_endpoint
from bwv.overlay import OverlayClient
from bwv.qa import make_server
from bwv.errors import ChatNotSubmitted


@unittest.skipUnless(os.name == 'nt', 'Windows named pipe transport')
class PipeTests(unittest.TestCase):
    def test_only_explicit_pre_admission_rejections_can_be_retried(self):
        processed=[]
        def question(body):
            if body['question']=='wait':raise ChatNotSubmitted()
            if body['question']=='changed':raise ChatNotSubmitted(retryable=False)
            processed.append(body)
            if body['question']=='failed':raise ValueError('Failure after admission')
            return {'ok':True}
        server=ChatPipeServer(lambda:{},question)
        try:
            client=OverlayClient(server.endpoint)
            for text,retryable in (('wait',True),('changed',False)):
                with self.assertRaises(ChatNotSubmitted) as caught:client.ask(text,('s','c'))
                self.assertEqual(caught.exception.retryable,retryable)
            self.assertEqual(processed,[])
            with self.assertRaises(ValueError) as caught:client.ask('failed',('s','c'))
            self.assertNotIsInstance(caught.exception,ChatNotSubmitted)
            self.assertEqual(len(processed),1)
            self.assertEqual(client.ask('ready',('s','c')),{'ok':True})
            self.assertEqual(len(processed),2)
        finally:server.close()

    def test_state_question_and_invalid_request_without_http(self):
        calls=[]
        server=ChatPipeServer(lambda:{'runtime':{'mode':'fixture'}},lambda body:calls.append(body) or {'ok':True})
        try:
            overlay=OverlayClient(server.endpoint)
            self.assertEqual(overlay.state()['runtime']['mode'],'fixture')
            self.assertEqual(overlay.ask('hello',('fixture-session','fixture-creature')),{'ok':True})
            self.assertEqual(calls,[{'question':'hello','expected_identity':['fixture-session','fixture-creature']}])
            with self.assertRaises(ValueError):ChatPipeClient(server.endpoint).call('reset')
            self.assertEqual(len(calls),1)
        finally:server.close()

    def test_only_local_generated_endpoint_is_accepted(self):
        for endpoint in ('http://example.test','bwv-pipe:remote:path','bwv-pipe:'+'../'*50):
            with self.assertRaises(ValueError):parse_endpoint(endpoint)


class TwoPageTests(unittest.TestCase):
    def test_both_pages_share_port_but_have_separate_state_and_post_routes(self):
        class View:
            def __init__(self,name):self.name=name
            def payload(self,number=None):return {'view':self.name,'frame':number}
            def preview_question(self,body):return {'preview':body}
        observer=View('observer');llm=View('llm')
        server,url=make_server(llm,debug=True,page_name='qa_live.html',observer=observer,
                               post_handlers={'question':lambda body:{'reply':body['question']}})
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for name in ('observer','llm'):
                with urlopen(url+name+'/') as response:page=response.read()
                self.assertIn(b'Full observer',page)
                with urlopen(url+name+'/state') as response:
                    self.assertEqual(json.load(response)['view'],name)
            with urlopen(url+'observer/state?frame=7') as response:
                self.assertEqual(json.load(response)['frame'],7)
            origin='http://127.0.0.1:'+str(server.server_port)
            for path,key in (('llm/question','reply'),('observer/question-preview','preview')):
                request=Request(url+path,data=b'{"question":"fixture"}',headers={'Content-Type':'application/json','Origin':origin})
                with urlopen(request) as response:self.assertEqual(json.load(response)[key],'fixture')
            with self.assertRaises(HTTPError):urlopen(origin+'/observer/state')
        finally:
            server.shutdown();server.server_close();thread.join(2)


@unittest.skipUnless(os.name == 'nt', 'Windows named pipe transport')
class NoHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_launcher_transport_never_creates_http_server(self):
        from bwv.qa_live import serve
        with TemporaryDirectory() as directory:
            ready=asyncio.Event(); endpoints=[]
            def started(endpoint,runtime):endpoints.append(endpoint);ready.set()
            with patch('bwv.qa_live.make_server',side_effect=AssertionError('Unexpected HTTP listener')):
                task=asyncio.create_task(serve(None,seconds=3,state_dir=Path(directory),source_reader=lambda:None,
                    local_pipe=True,on_ready=started,automatic=False))
                try:
                    await asyncio.wait_for(ready.wait(),2)
                    state=await asyncio.to_thread(OverlayClient(endpoints[0]).state)
                    self.assertEqual(state['runtime']['external_model_calls'],0)
                finally:
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
