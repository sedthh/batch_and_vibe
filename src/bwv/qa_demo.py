"""Isolated interactive QA: synthetic world, deterministic replies, no game access."""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeout
import json
from pathlib import Path
import threading
import time

from .conversation import ConversationBroker, ConversationMemory, MEMORY_LABEL, LEGACY_MEMORY_LABEL
from .control import ControlObservation
from .creature import CreatureContext
from .experience import Experience, BodyState, NativeInstinct
from .memory_store import default_memory_path
from .planner import PlanningRequest
from .qa import Inspector, make_server

SCENARIOS = ('ready','hurt','paused','cutscene','temple','no_creature','empty')


class DemoProvider:
    """Small explicit commands test transport; no language model is simulated."""
    def __init__(self):
        self.delay = 0
        self.requests = deque(maxlen=8)
        self.active = 0
        self.max_active = 0

    async def respond(self,request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active,self.active)
        try:
            await asyncio.sleep(self.delay)
            question = request.current.get('god_question')
            result = {'answer':'','decision':{'candidate':None}}
            if question is None:
                return result
            command = question.strip()
            notes = ''
            for message in request.messages:
                if message['role']=='system':
                    for line in message['content'].splitlines():
                        for label in (MEMORY_LABEL, LEGACY_MEMORY_LABEL):
                            if line.startswith(label):
                                notes='\n\n'.join(e['text'] for e in json.loads(line[len(label):])['entries'])
                if message['role']=='user':
                    data=json.loads(message['content'])
                    if 'model_memory_unverified' in data:
                        notes=data['model_memory_unverified']
            if command.lower().startswith('remember:'):
                result['memory'] = command.split(':',1)[1].strip()
                result['answer'] = 'Dummy: saved this in demo memory.'
            elif command.lower()=='memory':
                result['answer'] = 'Dummy memory: '+(notes or '(empty)')
            elif command.lower()=='forget':
                result.update(memory='',answer='Dummy: cleared demo memory.')
            elif command.lower().startswith('say:'):
                text=command.split(':',1)[1].strip()[:300]
                # Exercise actual speech (including silence), without a visible
                # diagnostic acknowledgement that could mask an empty reply.
                result.update(answer='', say=text)
            elif command.lower()=='status':
                result['answer'] = 'Dummy current snapshot: '+json.dumps(request.current['snapshot'])
            else:
                result['answer'] = 'Dummy received: '+question+'\nThis is a deterministic echo, not an LLM.'
            return result
        finally:
            self.active -= 1


class DemoInspector(Inspector):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.demo = {}

    def payload(self,number=None):
        result = super().payload(number)
        from .expressions import display_reply
        if result['current'] is not None:
            frame = result['current']
            result['current'] = {**frame, 'dialogue': [display_reply(pair)
                for pair in frame.get('memory', {}).get('recent_pairs', [])]}
        # Separate metadata avoids altering stored historical observer frames.
        with self._lock:
            result['demo'] = dict(self.demo)
        return result


class DemoRuntime:
    def __init__(self, state_dir=None, *, speech_seconds=15):
        self.loop = asyncio.get_running_loop()
        root=Path(state_dir) if state_dir is not None else default_memory_path().parent/'offline-demo'
        path=root/'dummy-memory.json'
        if path.resolve()==default_memory_path().resolve():
            raise ValueError('Demo memory must be isolated')
        self.memory=ConversationMemory(path=path)
        self.inspector=DemoInspector(memory=self.memory)
        self.inspector.gate.resume_delay=0
        self.provider=DemoProvider()
        self.broker=ConversationBroker(self.provider,self.inspector.gate,memory=self.memory,
                                       speech_seconds=speech_seconds,timeout=5)
        self.inspector.speech=self.broker.speech
        self.scenario='ready'
        self.automatic=True
        self.session=1
        self.sequence=0
        self._last_auto=0
        self._tasks=set()
        self.last_error=None
        self.refresh()

    def observation(self,now):
        available=self.scenario!='no_creature'
        context=Experience(BodyState(health=.4 if self.scenario=='hurt' else 1,energy=.7),
                           NativeInstinct(11,'SyntheticEat'),validation='synthetic_demo')
        return ControlObservation(f'offline-demo-{self.session}',self.sequence,now,
            creature_id='synthetic-creature' if available else None,connected=True,supported=True,
            world_ready=True,loading=False,paused=self.scenario=='paused',cutscene=self.scenario=='cutscene',
            player_has_control=True,creature_present=available,script_owns_creature=False,
            in_temple=self.scenario=='temple',creature=CreatureContext('monkey','engine') if available else CreatureContext(),
            experience=context if available and self.scenario in ('ready','hurt') else Experience())

    def refresh(self):
        now=time.monotonic()
        self.sequence += 1
        observation=self.observation(now)
        self.inspector.update(observation,{},None,None,now)
        requests=self.provider.requests
        metadata={'mode':'offline_dummy_demo','synthetic_world':True,'scenario':self.scenario,
            'automatic':self.automatic,'delay_seconds':self.provider.delay,
            'dummy_requests':self.broker.calls_started,'network_calls':0,'game_actions':0,
            'pending_questions':len(self.broker._questions),'pending_tick':self.broker._auto is not None,
            'merged_ticks':self.broker.merged_ticks,'active_requests':self.provider.active,
            'max_active_requests':self.provider.max_active,'last_error':self.last_error or self.broker.last_error,
            'last_request':list(requests[-1].messages) if requests else None}
        with self.inspector._lock:
            self.inspector.demo=metadata
        return observation

    async def step(self):
        observation=self.refresh()
        scheduled=self.inspector.updates.latest
        if (self.automatic and scheduled and scheduled['number']!=self._last_auto
                and self.inspector.latest['prompt']['context']
                and self.inspector.gate.poll(time.monotonic()).allowed):
            self._last_auto=scheduled['number']
            lease=self.inspector.gate.issue(time.monotonic())
            if lease:
                request=PlanningRequest(lease,(),None,reflection_only=True,
                                        creature=observation.creature,experience=observation.experience)
                task=asyncio.create_task(self.broker.choose(request))
                self._tasks.add(task)
                task.add_done_callback(self._finished)

    def _finished(self,task):
        self._tasks.discard(task)
        if not task.cancelled():
            error=task.exception()
            if error:
                self.last_error=type(error).__name__

    async def question(self,payload):
        if type(payload) is not dict or set(payload)!={'question'}:
            raise ValueError('Invalid question')
        self.refresh()
        reply=await self.broker.ask(payload['question'])
        self.refresh()
        return {'mode':'offline_dummy_demo','reply':reply,'network_calls':0,'game_actions':0}

    async def configure(self,payload):
        if type(payload) is not dict or not payload or not payload.keys()<= {'scenario','automatic','delay_seconds','new_session'}:
            raise ValueError('Invalid demo configuration')
        if 'scenario' in payload and payload['scenario'] not in SCENARIOS:
            raise ValueError('Invalid demo scenario')
        if 'automatic' in payload and type(payload['automatic']) is not bool:
            raise ValueError('Invalid automatic flag')
        if 'delay_seconds' in payload and (type(payload['delay_seconds']) not in (int,float) or payload['delay_seconds'] not in (0,1,3)):
            raise ValueError('Invalid delay')
        if 'new_session' in payload and payload['new_session'] is not True:
            raise ValueError('Invalid session switch')
        self.scenario=payload.get('scenario',self.scenario)
        self.automatic=payload.get('automatic',self.automatic)
        if not self.automatic:
            for task in tuple(self._tasks):
                task.cancel()
        self.provider.delay=payload.get('delay_seconds',self.provider.delay)
        if payload.get('new_session'):
            self.session+=1
            self._last_auto=0
        self.refresh()
        self.broker._sync_identity()
        self.refresh()
        return {'mode':'offline_dummy_demo','configured':True}

    def handler(self,method):
        def handle(payload):
            future=asyncio.run_coroutine_threadsafe(method(payload),self.loop)
            try:
                return future.result(timeout=6)
            except FutureTimeout:
                future.cancel()
                raise RuntimeError('Demo request timed out') from None
        return handle

    async def close(self):
        for task in tuple(self._tasks):
            task.cancel()
        await self.broker.close()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks),return_exceptions=True)


async def serve(port=0,seconds=43200,*,state_dir=None,token=None):
    if type(seconds) is not int or not 1<=seconds<=43200 or type(port) is not int or not 0<=port<=65535:
        raise ValueError('Invalid demo server limits')
    runtime=DemoRuntime(state_dir)
    server,url=make_server(runtime.inspector,port,token,post_handlers={
        'question':runtime.handler(runtime.question),'configure':runtime.handler(runtime.configure)},page_name='qa_demo.html',debug=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    print(json.dumps({'qa_url':url,'mode':'offline_dummy_demo','network_calls':0,'game_actions':0}),flush=True)
    deadline=time.monotonic()+seconds
    try:
        while time.monotonic()<deadline:
            await runtime.step()
            await asyncio.sleep(.25)
    finally:
        await runtime.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
    return {'stopped':True,'mode':'offline_dummy_demo','network_calls':0,'game_actions':0}


def run(port=0,seconds=43200,*,state_dir=None):
    return asyncio.run(serve(port,seconds,state_dir=state_dir))
