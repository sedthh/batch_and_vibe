"""One readiness check per VR settings session; no recording or provider requests."""
from importlib.util import find_spec
from pathlib import Path
import threading

from .credentials import credential_environment


def ready_notice(config):
    return ('Voice input ready. Hold '+config.hotkey+' to speak.' if config.push_to_talk
            else 'Voice input ready. Speak, then pause to send.')


def check_microphone(config):
    import sounddevice as sd
    device=config.microphone or None
    # Match the actual capture paths, including drivers that reject 16 kHz.
    rates=(16000,) if config.push_to_talk else (16000,48000,32000,8000)
    for rate in rates:
        try:sd.check_input_settings(device=device,channels=1,dtype='int16',samplerate=rate)
        except Exception:continue
        return
    if config.push_to_talk:
        rate=int(sd.query_devices(device,'input')['default_samplerate'])
        if 8000<=rate<=384000:
            sd.check_input_settings(device=device,channels=1,dtype='int16',samplerate=rate)
            return
    raise ValueError('Microphone format unavailable')


def initial_notice(config,path,*,available=None,microphone=check_microphone,environ=None):
    available=available or (lambda name:find_spec(name) is not None)
    def unavailable(reason):return 'Voice input unavailable. '+reason+' in Voice settings.'
    if not config.enabled:return 'Voice input off. Enable it in Voice settings.'
    if not config.model.strip():return unavailable('Choose a transcription model')
    if not config.auto_send:return unavailable('Enable Send transcript immediately')
    if not available('sounddevice'):return 'Voice input unavailable. Audio support is missing; run Setup-Mod again.'
    if config.provider=='local' and not available('faster_whisper'):
        return 'Voice input unavailable. Install local voice support; see Voice settings.'
    if not config.push_to_talk and not available('webrtcvad'):
        return 'Voice input unavailable. Voice activation support is missing; run Setup-Mod again.'
    if config.provider=='api' and config.api_key_env:
        try:
            env=credential_environment(config.api_key_env,Path(path).parent/'.local'/'.env',environ=environ)
            if not env.get(config.api_key_env):return unavailable('Set the transcription API key')
        except ValueError:return unavailable('Check the transcription API key')
    try:microphone(config)
    except Exception:return 'Voice input stopped: microphone unavailable. Check Voice settings and Windows microphone access.'
    return ready_notice(config)


class VoiceReadiness:
    def __init__(self,config,path,report,*,check=initial_notice):
        self.config=config;self.path=path;self.report=report;self.check=check
        self.lock=threading.Lock();self.closed=False;self.runtime_result=False

    def start(self):
        def work():
            try:text=self.check(self.config,self.path)
            except Exception:text='Voice input unavailable. Check Voice settings and run Setup-Mod again.'
            with self.lock:
                if not self.closed and not self.runtime_result:self.report(text)
        threading.Thread(target=work,daemon=True).start()

    def failed(self,message):
        with self.lock:
            self.runtime_result=True
            if not self.closed:self.report('Voice input stopped: '+message)

    def succeeded(self):
        with self.lock:
            self.runtime_result=True
            if not self.closed:
                # A transcript awaiting desktop review still cannot send from VR.
                self.report(ready_notice(self.config) if self.config.auto_send else
                    'Voice input unavailable. Enable Send transcript immediately in Voice settings.')

    def close(self):
        with self.lock:self.closed=True
