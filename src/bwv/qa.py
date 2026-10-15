"""Loopback-only, read-only prompt inspector. No provider or executor is created."""
from __future__ import annotations

import asyncio
from dataclasses import fields, is_dataclass
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .control import ControlGate, ControlObservation, LABELS, CONTROL_EXPECTATIONS
from .prompt import preview_prompt
from .watch import observations
from .updates import ContextUpdates
from .conversation import current_prompt, question_unavailable, ConversationMemory, CreatureSpeech
from .errors import ChatNotSubmitted
from .memory_store import default_memory_path
from .llm_context import summarize_context
from .model_projection import model_snapshot, model_turn
from .sampling import DetailRefresh


def _debug_value(value):
    """Represent unavailable numeric sentinels as JSON null, only for display."""
    if type(value) in (str, int, bool, type(None)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _debug_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_debug_value(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _debug_value(getattr(value, field.name)) for field in fields(value)}
    return value


DEBUG_SECTIONS = frozenset(('settings', 'history', 'request', 'tools', 'full'))


class Inspector:
    def __init__(self, limit=240, *, memory=None, speech=None, history_bytes=16*1024*1024,
                 history_interval=0):
        self.gate = ControlGate()
        self.updates = ContextUpdates()
        self.frames = deque(maxlen=limit)
        self.history_bytes=history_bytes
        self.retained_bytes=0
        self.history_interval = history_interval
        self._next_history_at = 0
        self.latest = None
        self._observation = None
        self._signature = None
        self._number = 0
        self._detail_encoding = {}
        self._encoding_identity = None
        self.detail_refresh = DetailRefresh()
        self._lock = threading.Lock()
        # A conversation runtime can share its actual state here. The standalone
        # read-only inspector starts empty and never fabricates model responses.
        self.memory = memory if memory is not None else ConversationMemory()
        self.speech = speech if speech is not None else CreatureSpeech()

    def set_history_limit(self, limit):
        """Drop retained debug frames immediately when debugging is disabled."""
        with self._lock:
            self.frames = deque(self.frames, maxlen=limit)
            self.retained_bytes=sum(len(f['payload']) for f in self.frames)
            self._signature = None
            self._next_history_at = 0

    def update(self, observation, state, camera, error, now=None):
        now = time.monotonic() if now is None else now
        status = self.gate.update(observation, now)
        prompt = preview_prompt(observation, status.summary)
        scheduled = self.updates.update(observation, prompt, status, now)
        frame = {
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'preview_only_not_sent', 'provider_calls': 0, 'game_actions': 0,
            'provider_connected': False,
            'memory': self.memory.snapshot(),
            'speech': self.speech.snapshot(now),
            'question_unavailable': question_unavailable(observation,now),
            'gate': {'allowed': status.allowed, 'reasons': list(status.reasons),
                     'explanations': [LABELS.get(r, r) for r in status.reasons],
                     'unverified_fields': [name for name, _, _ in CONTROL_EXPECTATIONS
                                           if type(getattr(observation, name)) is not bool]},
            'diagnostics': {'error': error, 'source_age_ms': state.get('source_age_ms'),
                'detailed_sensors': state.get('detailed_sensors'),
                'bridge_performance': state.get('bridge_performance'),
                'action_probe': state.get('action_probe'),
                'paused': observation.paused, 'cutscene': observation.cutscene,
                'in_temple': observation.in_temple,
                'experience_validation': observation.experience.validation,
                'script_camera_owned': camera.script_owned if camera else None},
            'status_updates': scheduled,
            'prompt': {'system': prompt.system, 'context': json.loads(prompt.context_json)},
        }
        # An unavailable source timestamp can be -inf during startup or loss of
        # connection. Keep the original observation for gating; JSON has no inf.
        frame = _debug_value(frame)
        # The watcher replaces these immutable detail snapshots on refresh and
        # shares them between fast observations. Encode each once, retaining at
        # most one version per field. Published frames are read-only internally.
        identity = (observation.session, observation.creature_id)
        if identity != self._encoding_identity:
            self._detail_encoding.clear()
            self._encoding_identity = identity
        encoded = {}
        for field in fields(observation):
            value = getattr(observation, field.name)
            if field.name in ('surroundings', 'environment', 'island'):
                previous = self._detail_encoding.get(field.name)
                if previous is None or previous[0] is not value:
                    previous = (value, _debug_value(value))
                    self._detail_encoding[field.name] = previous
                encoded[field.name] = previous[1]
            else:
                encoded[field.name] = _debug_value(value)
        frame['observation'] = encoded
        frame['model_context'] = model_snapshot(summarize_context(
            frame['prompt']['context'], frame['observation']))
        update = scheduled.get('last_update')
        frame['model_update'] = model_turn({**update['llm_input'],
            'snapshot':summarize_context(update['llm_input'].get('snapshot',{}))}) if update else None
        # Normal play retains only the latest frame and skips history comparison
        # serialization. Debug history ignores timestamp/sequence-only changes.
        signature = None
        capture_history = bool(self.frames.maxlen) and now >= self._next_history_at
        if capture_history:
            facts = {k: v for k, v in frame['observation'].items() if k not in ('sequence', 'observed_at')}
            signature = json.dumps([frame['gate'], frame['prompt'], facts,
                                   scheduled.get('last_update'), error, frame['memory'], frame['speech'],
                                   observation.paused, observation.cutscene], sort_keys=True)
        with self._lock:
            if not capture_history or signature != self._signature:
                self._number += 1
                frame['id'] = self._number
                if capture_history:
                    # Keep serialized history rather than hundreds of full Python
                    # object graphs. Dense scenes are bounded by bytes as well as
                    # frame count. The latest full frame always remains available.
                    payload=json.dumps(frame,ensure_ascii=False,separators=(',',':')).encode('utf-8')
                    while self.frames and (len(self.frames)>=self.frames.maxlen
                            or self.retained_bytes+len(payload)>self.history_bytes):
                        self.retained_bytes-=len(self.frames.popleft()['payload'])
                    if len(payload)<=self.history_bytes:
                        self.frames.append({'id':frame['id'],'recorded_at':frame['recorded_at'],
                            'reasons':frame['gate']['reasons'],'payload':payload})
                        self.retained_bytes+=len(payload)
                if capture_history:self._signature = signature
            else:
                frame['id'] = self._number
            self.latest = frame
            self._observation = observation
            if capture_history:self._next_history_at = now + self.history_interval

    def latest_frame(self):
        """Internal read-only view; published frames are replaced, never edited."""
        with self._lock:
            return self.latest

    def preview_question(self, question, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            observation = self._observation
            reasons = self.latest['gate']['reasons'] if self.latest else ['disconnected']
        system, current = current_prompt(observation,now,question=question,blockers=reasons)
        return {'mode':'preview_only_not_sent','reason':'no_provider_connected',
                'provider_calls':0,'game_actions':0,
                'memory':self.memory.snapshot(),
                'messages':self.memory.messages(system,current)}

    def payload(self, number=None):
        with self._lock:
            selected = self.latest if number is None or (self.latest and number==self.latest['id']) else next(
                (f for f in self.frames if f['id'] == number), None)
            if selected is not None and 'payload' in selected:
                selected=json.loads(selected['payload'])
            return {'current': selected, 'history': [
                {'id': f['id'], 'recorded_at': f['recorded_at'],
                 'reasons': f['reasons']} for f in reversed(self.frames)],
                'history_retention':{'frames':len(self.frames),'bytes':self.retained_bytes,
                                     'byte_limit':self.history_bytes}}

    def debug_payload(self, sections=()):
        """The default browser view needs summaries, not the raw engine catalog."""
        sections=frozenset(sections)
        if not sections <= DEBUG_SECTIONS:raise ValueError('Unknown debug section')
        if 'full' in sections:return self.payload()
        with self._lock:
            frame=self.latest
            if frame is not None:
                frame={k:frame[k] for k in ('id','recorded_at','gate','question_unavailable',
                    'speech','model_context','model_update') if k in frame}
                frame['diagnostics']={'error':self.latest.get('diagnostics',{}).get('error')}
                frame['observation']={'connected':self.latest.get('observation',{}).get('connected')}
                updates=self.latest.get('status_updates',{})
                frame['status_updates']={k:v for k,v in updates.items() if k!='last_update'}
                last=updates.get('last_update')
                frame['status_updates']['last_update']=({k:last[k] for k in ('number','trigger') if k in last}
                                                        if last else None)
                if 'history' in sections:frame['memory']=self.latest.get('memory')
            return deepcopy({'current':frame,'history':[
                {k:f[k] for k in ('id','recorded_at','reasons')} for f in reversed(self.frames)],
                'history_retention':{'frames':len(self.frames),'bytes':self.retained_bytes,
                                     'byte_limit':self.history_bytes}})


def make_server(inspector, port=0, token=None, *, post_handlers=None, page_name='qa.html', debug=False,
                observer=None):
    token = secrets.token_urlsafe(24) if token is None else token
    if not re.fullmatch(r'[A-Za-z0-9_-]{24,64}', token):
        raise ValueError('Invalid local inspector path token')
    if page_name not in ('qa.html','qa_demo.html','qa_live.html'):
        raise ValueError('Unknown inspector page')
    page = Path(__file__).with_name(page_name).read_bytes() if debug else None
    observer_page = Path(__file__).with_name('qa.html').read_bytes() if debug and observer is not None else None
    def preview(payload):
        if type(payload) is not dict or set(payload) != {'question'}:
            raise ValueError('Invalid question fields')
        return inspector.preview_question(payload['question'])
    routes = {'question-preview':preview, **(post_handlers or {})}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reject_post(self, code):
            # Closing a Windows socket with an unread small POST body can reset
            # the connection before its HTTP error reaches the caller. Drain
            # only bounded, explicitly sized bodies; never parse rejected input.
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not self.headers.get('Transfer-Encoding') and 0 < length <= 8193:
                    self.connection.settimeout(.25)
                    self.rfile.read(length)
            except (ValueError,OSError):
                pass
            self.send_error(code)

        def do_POST(self):
            if observer is not None and self.path.startswith(f'/{token}/llm/'):
                self.path = f'/{token}/' + self.path[len(f'/{token}/llm/'):]
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin') != f'http://{host}':
                self.reject_post(403)
                return
            route = next((handler for name,handler in routes.items() if self.path==f'/{token}/{name}'),None)
            if observer is not None and self.path == f'/{token}/observer/question-preview':
                def route(payload):
                    if type(payload) is not dict or set(payload) != {'question'}:
                        raise ValueError('Invalid question fields')
                    return observer.preview_question(payload['question'])
            if route is None:
                self.reject_post(404)
                return
            if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
                self.reject_post(400)
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 8192:
                    self.reject_post(400)
                    return
                self.connection.settimeout(2)
                payload = json.loads(self.rfile.read(length))
                result = route(payload)
            except ChatNotSubmitted as error:
                self.send_response(409)
                self.send_header('X-BWV-Chat-Not-Submitted','retry' if error.retryable else 'stop')
                self.send_header('Content-Length','0')
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                return
            except (ValueError, OSError):
                self.send_error(400, 'Question unavailable or invalid')
                return
            except RuntimeError:
                self.send_error(409, 'Request unavailable, changed session or queue full')
                return
            body = json.dumps(result,allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            if self.headers.get('Host') != host or (origin and origin != f'http://{host}'):
                self.send_error(403)
                return
            selected, selected_page = inspector, page
            if observer is not None:
                for suffix, target, target_page in (('observer', observer, observer_page), ('llm', inspector, page)):
                    prefix = f'/{token}/{suffix}/'
                    if self.path.startswith(prefix):
                        self.path = f'/{token}/' + self.path[len(prefix):]
                        selected, selected_page = target, target_page
                        break
            if self.path == f'/{token}/' and selected_page is not None:
                body, kind = selected_page, 'text/html; charset=utf-8'
                if observer is not None:
                    links = (f'<nav style="padding:12px"><a href="/{token}/llm/">LLM debugger</a> | '
                             f'<a href="/{token}/observer/">Full observer</a></nav>').encode()
                    body = (body.replace(b'<body>', b'<body>' + links, 1) if b'<body>' in body
                            else body.replace(b'</style>', b'</style>' + links, 1))
            elif urlsplit(self.path).path == f'/{token}/debug-state' and hasattr(selected, 'debug_payload'):
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {'sections'} or len(query.get('sections', [])) > 1:
                    self.send_error(400)
                    return
                sections = set(filter(None, query.get('sections', [''])[0].split(',')))
                if not sections <= DEBUG_SECTIONS:
                    self.send_error(400)
                    return
                body, kind = json.dumps(selected.debug_payload(sections), allow_nan=False).encode(), 'application/json'
            elif self.path == f'/{token}/state':
                body, kind = json.dumps(selected.payload(), allow_nan=False).encode(), 'application/json'
            elif self.path == f'/{token}/overlay-state' and hasattr(selected,'overlay_payload'):
                body, kind = json.dumps(selected.overlay_payload(), allow_nan=False).encode(), 'application/json'
            elif self.path.startswith(f'/{token}/state?frame='):
                try:
                    number = int(self.path.split('=', 1)[1])
                except ValueError:
                    self.send_error(400)
                    return
                body, kind = json.dumps(selected.payload(number), allow_nan=False).encode(), 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server, f'http://127.0.0.1:{server.server_port}/{token}/'


async def collect(inspector, pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        stream = observations(pid, training=True, control=True, leash=True,
                              miracles=True, holding=True, experience=True,
                              detail_refresh=inspector.detail_refresh)
        try:
            async for observation, state, camera, error in stream:
                inspector.update(observation, state, camera, error)
                if time.monotonic() >= deadline:
                    break
        except (OSError, ValueError) as error:
            inspector.update(ControlObservation('disconnected', 0, time.monotonic()),
                             {}, None, type(error).__name__)
            await asyncio.sleep(1)
        finally:
            await stream.aclose()


def run(pid, port=0, seconds=43200, *, token=None, memory_path=None, debug=False):
    if type(seconds) is not int or not 1 <= seconds <= 43200:
        raise ValueError('QA duration must be 1 to 43200 seconds')
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid local port')
    inspector = Inspector(limit=240 if debug else 0,
                          memory=ConversationMemory(path=memory_path if memory_path is not None else default_memory_path()))
    server, url = make_server(inspector, port, token, debug=debug)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if debug:
        print(f'Debug inspector: {url}', flush=True)
    print(json.dumps({'api_url': url, 'qa_url': url if debug else None, 'debug_ui':debug,
                      'pid': pid, 'max_seconds': seconds,
                      'provider_calls': 0, 'game_actions': 0}), flush=True)
    try:
        asyncio.run(collect(inspector, pid, seconds))
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return {'stopped': True, 'provider_calls': 0, 'game_actions': 0}
