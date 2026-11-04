"""Compact configuration checks; never start devices, load models or call providers."""
from importlib.util import find_spec
from pathlib import Path
import re

from .credentials import credential_environment


COLORS={'ok':'#21833b','warning':'#a87500','disabled':'#bf3030'}


def short_name(value,limit=30):
    value=re.split(r'[/\\]',value.strip().rstrip('/\\'))[-1]
    value=' '.join(value.split())
    return value if len(value)<=limit else value[:limit-1]+'…'


def configuration_status(config,path,*,environ=None,available=None):
    available=available or (lambda name:find_spec(name) is not None)
    def key_issue(name):
        if not name:return ''
        try:
            env=credential_environment(name,Path(path).parent/'.local'/'.env',environ=environ)
            return '' if env.get(name) else 'missing API key'
        except ValueError:return 'check API key'

    def row(label,description,enabled,issue=''):
        if not enabled:return (label,'disabled','disabled')
        return (label,description+(' — '+issue if issue else ''),'warning' if issue else 'ok')

    model=config.model.strip()
    llm=row('LLM thinking',short_name(model),bool(model and model!='YOUR_MODEL_ID'),key_issue(config.api_key_env))
    v=config.voice
    issue=''
    if v.enabled:
        if not v.model.strip():issue='choose a model'
        elif not available('sounddevice'):issue='audio support missing'
        elif v.provider=='local' and not available('faster_whisper'):issue='local voice support missing'
        elif not v.push_to_talk and not available('webrtcvad'):issue='voice activation support missing'
        elif v.provider=='api':issue=key_issue(v.api_key_env)
        if not issue and config.vr_mode and not v.auto_send:issue='enable automatic sending for VR'
    voice=row('Voice Input',short_name(v.model) or 'no model',v.enabled,issue)
    s=config.speech
    provider={'windows':'Windows','piper':'Piper','openai':'OpenAI-compatible','elevenlabs':'ElevenLabs'}[s.provider]
    description=provider+' · '+(short_name(s.voice.removeprefix('Microsoft '),24) or
        ('default voice' if s.provider=='windows' else 'server default'))
    issue=''
    if s.enabled:
        if not available('sounddevice'):issue='audio support missing'
        elif s.volume==0:issue='muted'
        elif s.provider in ('openai','elevenlabs'):issue=key_issue(s.api_key_env)
    return (llm,voice,row('Creature Speech',description,s.enabled,issue))


def runtime_status(rows,runtime):
    """Update thinking status from the existing runtime, even with debugging off."""
    if runtime is None:return rows
    label,description,state=rows[0]
    if not runtime.automatic or not runtime.is_llm:
        first=(label,'disabled','disabled')
    elif runtime.provider.status_error():
        first=(label,short_name(runtime.provider.config.model)+' — provider issue','warning')
    else:first=(label,description,state)
    return (first,*rows[1:])
