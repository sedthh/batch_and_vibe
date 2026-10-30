"""Windowed-game chat overlay client. No process-memory or engine-write APIs."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.error import HTTPError

from .conversation import validate_question
from .qa_live import InspectorSource
from .profile_names import ProfileNames
from .expressions import display_speech
from .errors import error_banner, ChatNotSubmitted


class ChatInputHistory:
    """Session-only user input history; browsing never sends a message."""
    def __init__(self, limit=10):
        self.limit=limit;self.items=[];self.index=None;self.draft=''

    def submitted(self, text):
        if text.strip() and (not self.items or self.items[-1]!=text):
            self.items.append(text)
            self.items=self.items[-self.limit:]
        self.index=None;self.draft=''

    def move(self, direction, current):
        if not self.items:return current
        if self.index is None:
            if direction>0:return current
            self.draft=current;self.index=len(self.items)
        self.index=max(0,min(len(self.items),self.index+direction))
        if self.index==len(self.items):
            self.index=None
            return self.draft
        return self.items[self.index]


class ReplyReveal:
    """Reveal a single reply in word-sized bursts, driven by the UI clock."""
    interval = .27

    def __init__(self):
        self.clear()

    def clear(self):
        self.key=None;self.text='';self.ends=();self.started=0

    def reveal(self, text, key, now):
        if not text:
            self.clear()
            return ''
        if key!=self.key or text!=self.text:
            self.key=key;self.text=text;self.started=now
            # At least ~32 characters per burst, at most twenty-four bursts. Keep
            # words/Unicode intact and retain the exact reply, including spaces.
            size=max(32,math.ceil(len(text)/24))
            ends=[];previous=0
            for match in re.finditer(r'\S+\s*',text):
                end=match.end()
                if end-previous>=size:
                    ends.append(end);previous=end
            if not ends or ends[-1]!=len(text):ends.append(len(text))
            self.ends=tuple(ends)
        step=min(len(self.ends)-1,int(max(0,now-self.started)/self.interval))
        return self.text[:self.ends[step]]


class OverlayClient:
    def __init__(self, url):
        self.pipe = None
        if url.startswith('bwv-pipe:'):
            from .chat_pipe import ChatPipeClient
            self.pipe = ChatPipeClient(url)
            return
        self.source = InspectorSource(url)  # loopback, token path, no redirects/proxy
        self.base = url
        parsed = urlsplit(url)
        self.origin = f'http://127.0.0.1:{parsed.port}'

    def _json(self, request, timeout):
        with self.source.opener.open(request,timeout=timeout) as response:
            raw = response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:
            raise ValueError('Overlay response is too large')
        return json.loads(raw)

    def state(self):
        if self.pipe is not None:
            return self.pipe.call('state')
        return self._json(Request(self.base+'overlay-state',headers={'Cache-Control':'no-cache'}),1)

    def ask(self, text, identity):
        text = validate_question(text)
        if not isinstance(identity,tuple) or len(identity)!=2 or not all(isinstance(x,str) and x for x in identity):
            raise ValueError('Current creature identity required')
        payload = {'question':text,'expected_identity':list(identity)}
        if self.pipe is not None:
            return self.pipe.call('question', payload, timeout=1200)
        body = json.dumps(payload).encode('utf-8')
        try:
            return self._json(Request(self.base+'question',data=body,headers={
                'Content-Type':'application/json','Origin':self.origin}),1200)
        except HTTPError as error:
            admission=error.headers.get('X-BWV-Chat-Not-Submitted')
            if error.code==409 and admission in ('retry','stop'):
                error.close()
                raise ChatNotSubmitted(retryable=admission=='retry') from None
            raise


@dataclass
class ChatSubmission:
    """One input, held briefly until admission; never replay an accepted request."""
    text: str
    identity: tuple[str,str]
    deadline: float
    sending: bool = False
    next_attempt: float = 0

    def readiness(self, model, now):
        if model.identity is not None and model.identity!=self.identity:return 'changed'
        if self.sending:return 'sending'
        if now>=self.deadline:return 'expired'
        if now>=self.next_attempt and model.identity==self.identity and model.can_send(now):return 'ready'
        return 'waiting'

    def retry(self, now):
        self.sending=False
        self.next_attempt=now+.5

    def notice(self, now):
        return f'[Waiting to send your message; retrying for up to {max(0,math.ceil(self.deadline-now))}s]'


@dataclass
class OverlayModel:
    game_pid: int | None = None
    identity: tuple[str,str] | None = None
    available: bool = False
    scene_hidden: bool = True
    speech: str = ''
    speech_expires: float | None = None
    received_at: float = -math.inf
    observed_at: float = -math.inf
    reason: str = 'Waiting for live creature chat'
    is_dummy: bool = True
    thinking: bool = False
    last_answer: str = ''
    creature_name: str | None = None
    last_message: str = ''
    message_time: float = -math.inf
    llm_active: bool = False
    call_count: int | None = None
    call_finished_until: float = -math.inf
    provider_error: str | None = None
    wait_notice: str = ''

    def send_failure_notice(self):
        return error_banner(self.provider_error or 'chat_transport_failed')

    @property
    def speaker_name(self):
        return self.creature_name or 'Creature'

    @property
    def chat_title(self):
        return 'Chat with '+(self.creature_name or 'creature')

    def disconnect(self):
        self.identity=None; self.available=False; self.speech=''
        self.scene_hidden=True
        self.creature_name=None
        self.thinking=False; self.last_answer=''
        self.wait_notice=''
        self.last_message='';self.message_time=-math.inf;self.llm_active=False
        self.call_count=None;self.call_finished_until=-math.inf
        self.speech_expires=None; self.observed_at=-math.inf
        self.reason='Creature chat is unavailable'
        # Retain the last game PID only to position a disabled panel while paused.
        # The window adapter checks HWND/PID/visibility again before showing it.

    def read_gap(self):
        """Disable sending without removing the last verified chat display."""
        self.available=False
        self.reason='Waiting for fresh creature data'

    def can_show(self):
        return bool(self.identity and not self.scene_hidden)

    def update(self, payload, now=None):
        now=time.monotonic() if now is None else now
        try:
            if payload['runtime']['mode'] not in ('live_dummy_preview','live_llm_chat'):
                raise ValueError('Unsupported chat service')
            self.is_dummy = payload['runtime']['mode']=='live_dummy_preview'
            self.provider_error=(payload['runtime'].get('last_chat_error') or
                (payload['runtime'].get('provider') or {}).get('last_error'))
            self.wait_notice=('[Waiting for fresh game data]'
                if payload['runtime'].get('waiting_for_observation') else '')
            frame=payload['current']; source=payload.get('source')
            hidden_reasons={'paused','cutscene','temple','scripted_creature','loading',
                'scene_state_unverified',
                'fullscreen_overlay_unavailable','game_display_unavailable','chat_overlay_unavailable'}
            reasons=set(frame.get('gate',{}).get('reasons',[]))
            if reasons & hidden_reasons or frame.get('question_unavailable') in hidden_reasons:
                self.scene_hidden=True
            if not source:
                self.received_at=now; self.read_gap(); return
            obs=source['observation']; stamp=obs['observed_at']
            diagnostics=source.get('diagnostics',{})
            # Scene facts can remain available while the creature snapshot is
            # stale or lacks an identity. Process locks before those early exits.
            scene_locked=(any(obs.get(name) is True for name in
                ('paused','in_temple','cutscene','loading','script_owns_creature'))
                or any(diagnostics.get(name) is True for name in
                       ('paused','in_temple','cutscene','script_camera_owned')))
            if scene_locked:self.scene_hidden=True
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or not -.1<=now-stamp<=1.5:
                self.received_at=now; self.read_gap(); return
            session=obs['session']; creature=obs.get('creature_id')
            pid_text,separator,_=session.partition(':')
            if not separator or not pid_text.isdecimal() or not 0<int(pid_text)<=0xffffffff:
                raise ValueError('Missing live game process')
            if self.game_pid is not None and self.game_pid!=int(pid_text):
                self.disconnect()
            self.game_pid=int(pid_text)
            identity=(session,creature) if isinstance(creature,str) and creature else None
            if identity is None or obs.get('connected') is not True:
                self.read_gap(); return
            if identity!=self.identity:
                self.last_message='';self.message_time=-math.inf
                self.call_count=None;self.call_finished_until=-math.inf;self.llm_active=False
            self.identity=identity
            self.creature_name=ProfileNames(creature_name=
                frame.get('character_names',{}).get('creature_name')).creature_name if self.identity else None
            self.scene_hidden=bool(scene_locked or reasons & hidden_reasons
                or frame.get('question_unavailable') in hidden_reasons
                or source.get('diagnostics',{}).get('script_camera_owned') is True)
            scene_locked=scene_locked or obs.get('player_has_control') is False
            scene_locked=scene_locked or source.get('diagnostics',{}).get('script_camera_owned') is True
            self.available=bool(self.identity and obs.get('connected') is True
                                and not self.scene_hidden and not scene_locked
                                and frame.get('question_unavailable') is None)
            self.received_at=now; self.observed_at=stamp
            self.reason='Talk to your creature' if self.available else 'Creature unavailable in this scene'
            self.thinking=frame.get('answer_status',{}).get('thinking') is True
            provider=payload['runtime'].get('provider') or {}
            remaining=provider.get('retry_after_seconds',0)
            queued=frame.get('answer_status',{}).get('waiting_for_provider') is True
            self.wait_notice=''
            if (provider.get('ready') is False and not provider.get('disabled')
                    and type(remaining) in (int,float) and math.isfinite(remaining) and remaining>0):
                cause=('Game data was interrupted' if provider.get('last_error')=='tool_context_unavailable' else
                       'Model rate limit reached' if provider.get('last_error')=='rate_limited' else
                       'Model temporarily unavailable')
                next_step='Your message is queued; retrying' if queued else 'Chat ready again'
                self.wait_notice=f'[{cause}. {next_step} in {math.ceil(remaining)}s]'
            active=payload['runtime'].get('active_requests',0)
            was_active=self.llm_active
            self.llm_active=not self.is_dummy and type(active) is int and active>0
            count=payload['runtime'].get('external_model_calls')
            if type(count) is int and count>=0:
                advanced=self.call_count is not None and count>self.call_count
                if self.call_count is not None and count<self.call_count:
                    self.call_finished_until=-math.inf  # Backend restarted.
                elif not self.llm_active and (was_active or advanced):
                    self.call_finished_until=now+1.5
                self.call_count=count
            elif was_active and not self.llm_active:
                self.call_finished_until=now+1.5
            if 'last_message' in frame['speech']:
                retained=frame['speech']['last_message']
                if retained is None:
                    self.last_message='';self.message_time=-math.inf
                else:self.accept_message(retained)
            answer=frame['speech'].get('last_answer')
            self.last_answer=answer['text'] if answer else ''
            if not isinstance(self.last_answer,str) or len(self.last_answer)>3000:
                raise ValueError('Invalid retained answer')
            speech=frame['speech'].get('current')
            self.speech=''; self.speech_expires=None
            if speech and self.identity:
                text=speech['text']; expiry=speech['expires_at']
                if not isinstance(text,str) or len(text)>3000:
                    raise ValueError('Invalid speech')
                if expiry is not None and (type(expiry) not in (int,float) or not math.isfinite(expiry)):
                    raise ValueError('Invalid speech expiry')
                if expiry is None or now<expiry:
                    self.speech=display_speech(text); self.speech_expires=expiry
        except (KeyError,TypeError,ValueError,AttributeError):
            self.disconnect()

    def can_send(self, now=None):
        now=time.monotonic() if now is None else now
        return bool(self.available and self.identity and -.1<=now-self.received_at<=1.5
                    and -.1<=now-self.observed_at<=1.5)

    def visible_speech(self, now=None):
        now=time.monotonic() if now is None else now
        if not self.can_show():
            return ''
        return self.last_message or (self.speech if self.speech_expires is None or now<self.speech_expires else '')

    def accept_message(self, message):
        text=message['text'];stamp=message['created_at']
        if (not isinstance(text,str) or len(text)>3000 or type(stamp) not in (int,float)
                or not math.isfinite(stamp)):
            raise ValueError('Invalid persistent message')
        text=display_speech(text).strip()
        if text and stamp>=self.message_time:
            self.last_message=text;self.message_time=stamp

    def banner_text(self, *, pending=False, notice='', now=None):
        now=time.monotonic() if now is None else now
        if not self.can_show():return ''
        if self.wait_notice and now-self.received_at<=2:
            if self.wait_notice!='[Waiting for fresh game data]' or pending or self.thinking:
                return self.wait_notice
        if pending or self.is_thinking(now):return '.'*(1+int(now*2)%3)
        return notice or self.visible_speech(now)

    def call_state(self, now=None, *, pending=False):
        now=time.monotonic() if now is None else now
        if self.is_dummy or not self.can_send(now):return 'hidden'
        if self.llm_active:return 'active'
        if pending:return 'queued'
        if now<self.call_finished_until:return 'finished'
        return 'idle'

    def call_indicator(self, now=None, *, pending=False):
        return '●' if self.call_state(now,pending=pending)=='active' else ''

    def is_thinking(self, now=None):
        return self.thinking and self.can_send(now)

    def readable_answer(self, now=None):
        return self.last_answer if self.can_send(now) else ''


def overlay_bounds(rect, expanded=False, speaking=False, *, input_height=None):
    """Client-screen rectangle -> top-center panel, including negative monitors."""
    left,top,right,bottom=rect
    width,height=right-left,bottom-top
    if width<320 or height<240:
        return None
    panel_width=min(500 if expanded or speaking else 340,width-24)
    base_height=78 if speaking else 30
    extra=(input_height if input_height is not None else (34 if speaking else 32)) if expanded else 0
    panel_height=min(base_height+extra,height-24)
    return (left+(width-panel_width)//2,top,panel_width,panel_height)


def run(url, *, seconds=43200, diagnostics_file=None, stop_on_stdin=False, settings_path=None, vr=False):
    from .overlay_windows import run_window
    return run_window(OverlayClient(url),seconds=seconds,diagnostics_file=diagnostics_file,
                      stop_on_stdin=stop_on_stdin,settings_path=settings_path,vr=vr)
