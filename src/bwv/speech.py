"""Opt-in creature speech configuration, independent of microphone input."""
from dataclasses import dataclass, fields
import ipaddress
import math
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SpeechSettings:
    enabled: bool = False
    provider: str = 'windows'
    voice: str = ''
    model: str = ''
    base_url: str = 'https://api.openai.com/v1'
    api_key_env: str | None = 'OPENAI_API_KEY'
    speed: float = 1.0
    volume: int = 80
    output_device: str = ''
    instructions: str = ''
    speaker_id: int | None = None
    timeout_seconds: int = 60
    max_requests: int | None = None
    max_characters: int | None = None
    feedback_protection: bool = True

    def __post_init__(self):
        for name in ('enabled','feedback_protection'):
            if type(getattr(self,name)) is not bool:raise ValueError('Speech checkboxes must be booleans.')
        if self.provider not in ('windows','openai','elevenlabs','piper'):
            raise ValueError('Choose a supported creature speech provider.')
        for name,limit in (('voice',256),('model',256),('base_url',2048),('output_device',256),('instructions',2000)):
            value=getattr(self,name)
            if not isinstance(value,str) or len(value)>limit or '\0' in value:
                raise ValueError('Invalid creature speech text setting.')
        if type(self.speed) not in (int,float) or not math.isfinite(self.speed) or not .25<=self.speed<=4:
            raise ValueError('Speech speed must be between 0.25 and 4 (1 = normal).')
        for name,low,high in (('volume',0,100),('timeout_seconds',1,120)):
            value=getattr(self,name)
            if type(value) is not int or not low<=value<=high:raise ValueError('Invalid speech volume or timeout.')
        for name,low in (('speaker_id',0),('max_requests',1),('max_characters',1)):
            value=getattr(self,name)
            if value is not None and (type(value) is not int or value<low):
                raise ValueError('Speech speaker ID and limits must be whole numbers, or blank.')
        if self.api_key_env is not None and (not isinstance(self.api_key_env,str) or
                not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',self.api_key_env)):
            raise ValueError('Speech API key variable must be an environment-variable name, or blank.')
        if not self.enabled or self.provider=='windows':return
        if self.provider in ('openai','elevenlabs') and (not self.model.strip() or not self.voice.strip()):
            raise ValueError('Choose a speech model and voice ID.')
        if self.provider=='elevenlabs' and not .7<=self.speed<=1.2:
            raise ValueError('ElevenLabs speech speed must be between 0.7 and 1.2.')
        try:
            url=urlsplit(self.base_url)
            local=url.hostname=='localhost'
            try:local=local or ipaddress.ip_address(url.hostname or '').is_loopback
            except ValueError:pass
            if (not url.hostname or url.username or url.password or url.query or url.fragment
                    or url.scheme not in ('http','https') or (url.scheme=='http' and not local)
                    or '\\' in self.base_url or any(c.isspace() for c in self.base_url) or url.port==0):
                raise ValueError()
            if self.provider=='piper' and not local:raise ValueError()
        except ValueError:
            raise ValueError('Use an HTTPS speech base URL or HTTP on loopback; Piper must run locally. No URL credentials or queries.') from None
        if not local and self.api_key_env is None:raise ValueError('Remote speech needs an API key variable.')

    @classmethod
    def parse(cls,data):
        if not isinstance(data,dict) or not data.keys()<={f.name for f in fields(cls)}:
            raise ValueError('Invalid creature speech settings.')
        return cls(**data)
