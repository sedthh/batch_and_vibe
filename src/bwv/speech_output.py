"""Speak new creature messages once; scene changes discard stale playback."""
import time
from .speech_audio import SpeechPlayer
from .speech_text import spoken_text


class SpeechOutput:
    def __init__(self,window,config,settings_path,*,service=None,clock=time.monotonic):
        self.window=window;self.config=config;self.clock=clock
        self.service=service or SpeechPlayer(config,settings_path)
        self.key=None;self.identity=None;self.pending=None
        self.was_busy=False;self.quiet_until=0;self.failed=False
        self.last_fresh_at=clock();self.retry_at=0;self.last_error_notice=None

    @property
    def blocks_microphone(self):
        return self.config.feedback_protection and (self.service.busy or self.clock()<self.quiet_until)

    def cancel(self):
        self.pending=None;self.service.cancel()
        if self.was_busy:self.quiet_until=self.clock()+.4

    def tick(self):
        w=self.window;model=w.model;now=self.clock()
        source=getattr(model,'message_source',None)
        key=(model.identity,model.message_time,model.last_message,source)
        busy=self.service.busy
        if self.was_busy and not busy:
            self.quiet_until=now+.4
            if self.service.error and not self.failed:
                retryable=getattr(self.service,'retryable_error',False) is True
                self.failed=not retryable
                self.retry_at=now+5 if retryable else 0
                if not retryable:self.pending=None
                if self.last_error_notice!=self.service.error:
                    message=self.service.error+(' Later replies will retry.' if retryable else '')
                    w.notice='[ERROR: Creature speech: '+message+']'
                    if getattr(w,'captions',None) is not None:
                        w.captions.queue_voice_notice('Voice error: Creature speech: '+message,model.identity)
                    self.last_error_notice=self.service.error
            elif not self.service.error:self.last_error_notice=None
        self.was_busy=busy
        focused=bool(w.game and w.win.u.GetForegroundWindow() in (w.game,w.hwnd))
        fresh=model.can_send()
        if fresh:self.last_fresh_at=now
        # Sending commands requires fresh observations. Finishing an already
        # spoken line does not: brief read gaps retain the verified scene.
        if (model.identity!=self.identity or not model.can_show() or not focused
                or (not fresh and now-self.last_fresh_at>=3)):
            self.cancel();self.identity=model.identity;self.key=key;return
        if key!=self.key:
            self.key=key
            self.pending=None
            text=spoken_text(model.last_message) if source in (None,'god_question','action_comment') else ''
            if text and not self.failed:self.pending=(text,now+10)
        if self.failed or self.pending is None:return
        if now>=self.pending[1]:self.pending=None;return
        if not fresh or now<self.retry_at:return
        voice=getattr(w,'voice',None)
        if self.config.feedback_protection and voice is not None:
            # A held push-to-talk button takes priority over creature playback.
            if not voice.automatic:
                from .voice import HOTKEYS
                if voice.service.busy or all(w.win.u.GetAsyncKeyState(k)&0x8000 for k in HOTKEYS[voice.config.hotkey]):return
            elif voice.service.busy:
                voice.cancel(notify=False);return
        if not busy and now>=self.quiet_until:
            text,_=self.pending;self.pending=None
            if self.service.start(text):self.was_busy=True

    def close(self):self.service.close()
