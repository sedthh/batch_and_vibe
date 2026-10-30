"""Opt-in native subtitles for BW1-VR; no OpenXR session or render injection."""
import hashlib
import textwrap
import threading
import time

from .action_probe import COMMAND, submit
from .overlay import OverlayModel
from .pipe import snapshot


def voice_settings(settings):
    """Keep the player's voice input and transcript preferences in VR."""
    return settings


def caption_pages(text,width=240):
    # The legacy renderer does not handle surrogate pairs or markup safely.
    text=''.join(c if 32<=ord(c)<127 or 160<=ord(c)<0xD800 or 0xF900<=ord(c)<0xFFFE else ' ' for c in text)
    text=text.replace('%',' percent ').replace('<',' ').replace('>',' ').replace('$',' ').replace('\\',' ')
    return textwrap.wrap(' '.join(text.split()),width=width,break_long_words=True,break_on_hyphens=False)


def creature_pages(text,name):
    label=' '.join(caption_pages(name or 'Creature'))[:48].strip() or 'Creature'
    prefix=label+': '
    return [prefix+page for page in caption_pages(text,width=240-len(prefix))]


def caption_packet(state,text,*,notice=False):
    probe=state['action_probe']
    if probe.get('vr_caption',{}).get('version')!=15:
        raise ValueError('This bridge does not support VR captions; rebuild and reinstall it.')
    encoded=text.encode('utf-16-le')
    if notice and probe.get('vr_caption',{}).get('notices') is not True:
        raise ValueError('This bridge needs an update for voice-result subtitles.')
    if not text or len(encoded)>510 or '\0' in text or caption_pages(text)!=[text]:
        raise ValueError('Invalid native caption')
    return COMMAND.pack(0x42575041,15,probe['epoch'],probe['generation'],
        probe['offered_at_uptime_ms'],int(notice),probe['conversation_actions']['creature_handle'])+encoded.ljust(512,b'\0')


class CaptionMirror:
    def __init__(self,reader=snapshot,sender=submit,clock=time.monotonic):
        self.model=OverlayModel();self.reader=reader;self.sender=sender;self.clock=clock
        self.key=None;self.pages=[];self.next_poll=0;self.status='waiting'
        self.voice_lock=threading.Lock();self.notice=None;self.last_notice=None

    def queue_voice_notice(self,text,identity):
        # Only completed transcriptions/errors cross this UI-thread mailbox.
        text=' '.join(caption_pages(text))
        if not text:return
        text='['+(text if len(text)<=238 else text[:235].rstrip()+'…')+']'
        now=self.clock()
        with self.voice_lock:
            if self.last_notice and self.last_notice[:2]==(identity,text) and now-self.last_notice[2]<30:return
            self.last_notice=(identity,text,now)
            self.notice=(identity,text,now+30)

    def take_notice(self,now):
        with self.voice_lock:
            notice=self.notice;self.notice=None
        if not notice or notice[0]!=self.model.identity or now>=notice[2]:return ''
        return notice[1]

    def update(self,payload):
        now=self.clock();self.model.update(payload,now)
        model=self.model
        text=model.last_message or model.speech
        key=(model.identity,model.message_time,text,model.creature_name)
        if not model.can_send(now):
            self.pages=[]
            with self.voice_lock:self.notice=None
            self.key=key;self.status='scene_unavailable';return
        if key!=self.key:
            self.key=key;self.pages=creature_pages(model.visible_speech(now),model.creature_name)
        if now<self.next_poll:return
        self.next_poll=now+.5
        state=self.reader(model.game_pid)
        probe=state.get('action_probe',{})
        age=state.get('source_age_ms')
        if state.get('pid')!=model.game_pid or type(age) is not int or not 0<=age<=1500 or probe.get('conditions_allow_action') is not True:return
        handles=probe.get('conversation_actions',{})
        session=f"{model.game_pid}:{state.get('session')}"
        creature=hashlib.sha256(f"{session}:{handles.get('creature_handle')}:{handles.get('mental_handle')}".encode()).hexdigest()[:24]
        if (session,creature)!=model.identity:
            self.pages=[];self.status='context_changed';return
        capability=probe.get('vr_caption',{})
        if capability.get('version')!=15:
            self.pages=[];self.status='bridge_update_required';return
        if capability.get('busy') or capability.get('queued'):return
        notice=self.take_notice(now)
        if notice and capability.get('notices') is not True:
            self.status='bridge_update_required';notice=''
        if not notice and not self.pages:return
        # Consume before sending: a lost acknowledgement must not repeat text.
        page=notice or self.pages.pop(0)
        accepted=self.sender(model.game_pid,caption_packet(state,page,notice=bool(notice)))
        self.status='queued' if accepted else 'rejected'
        self.next_poll=now+2
