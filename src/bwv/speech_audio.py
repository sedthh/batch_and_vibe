"""Bounded synthesis and interruptible audio. No recordings or raw errors saved."""
from array import array
import base64
import io
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import quote
import wave

from .credentials import credential_environment

MAX_AUDIO=24*1024*1024


class SpeechError(ValueError):pass


class SpeechPlaybackError(SpeechError):
    """The output device may recover; later replies can try playback again."""


def windows_speech(data,cancelled,timeout=60):
    # Inputs are JSON on stdin, never executable shell text or SSML.
    script=r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $request=[Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.list) {
        $voices=@($s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name })
        $bytes=[System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $voices -Compress))
    } else {
        if ($request.voice) { $s.SelectVoice($request.voice) }
        $s.Rate=[int]$request.rate
        $stream=New-Object System.IO.MemoryStream
        $s.SetOutputToWaveStream($stream)
        $s.Speak([string]$request.text)
        $s.SetOutputToNull()
        $bytes=$stream.ToArray()
        $stream.Dispose()
    }
    [Console]::OpenStandardOutput().Write($bytes,0,$bytes.Length)
} finally { $s.Dispose() }
'''
    command=[str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'),
             '-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode('ascii')]
    try:
        with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
            deadline=time.monotonic()+timeout
            first=json.dumps(data).encode('utf-8')
            while True:
                if cancelled.is_set() or time.monotonic()>deadline:
                    process.kill();process.communicate()
                    if cancelled.is_set():return b''
                    raise SpeechError('Windows speech timed out.')
                try:
                    output,_=process.communicate(first,timeout=.1)
                    break
                except subprocess.TimeoutExpired:first=None
            if process.returncode:raise SpeechError('Windows speech failed. Refresh the installed voices and check the selected voice.')
            if len(output)>MAX_AUDIO:raise SpeechError('Speech audio is too large.')
            return output
    except OSError:
        raise SpeechError('Windows speech is unavailable on this computer.') from None


def windows_voices():
    try:return tuple(json.loads(windows_speech({'list':True},threading.Event(),10)))
    except (ValueError,TypeError):raise SpeechError('Cannot list Windows speech voices.') from None


def decode_wav(data):
    try:
        with wave.open(io.BytesIO(data),'rb') as stream:
            rate,channels=stream.getframerate(),stream.getnchannels()
            if (stream.getsampwidth()!=2 or channels not in (1,2) or not 8000<=rate<=48000
                    or stream.getnframes()>rate*180 or stream.getcomptype()!='NONE'):raise ValueError()
            pcm=stream.readframes(stream.getnframes())
            if len(pcm)!=stream.getnframes()*channels*2:raise ValueError()
            if not pcm:raise ValueError()
            return pcm,rate,channels
    except (wave.Error,EOFError,ValueError):
        raise SpeechError('Speech provider returned unsupported audio; expected 16-bit PCM WAV.') from None


class Synthesizer:
    def __init__(self,config,settings_path,*,transport=None,environ=None):
        self.config=config;self.settings_path=Path(settings_path)
        self.transport=transport;self.environ=environ;self.requests=0;self.characters=0

    def synthesize(self,text,cancelled):
        c=self.config
        if cancelled.is_set():return None
        if not isinstance(text,str) or not text.strip() or len(text)>3000:
            raise SpeechError('Creature speech text must contain 1 to 3000 characters.')
        from .speech_text import spoken_text
        text=spoken_text(text)
        if not text:return None
        if c.provider=='windows':
            rate=max(-10,min(10,round(10*math.log2(c.speed))))
            audio=windows_speech({'text':text,'voice':c.voice,'rate':rate},cancelled,c.timeout_seconds)
            return None if cancelled.is_set() else decode_wav(audio)
        if c.max_requests is not None and self.requests>=c.max_requests:
            raise SpeechError('Speech request limit reached. Change Creature speech limits and Save & apply.')
        if c.max_characters is not None and self.characters+len(text)>c.max_characters:
            raise SpeechError('Speech character limit reached. Change Creature speech limits and Save & apply.')
        headers={}
        if c.provider!='piper':
            try:env=credential_environment(c.api_key_env,self.settings_path.parent/'.local'/'.env',environ=self.environ)
            except ValueError:raise SpeechError('Cannot read the speech API key.') from None
            key=env.get(c.api_key_env) if c.api_key_env else None
            if c.api_key_env and not key:raise SpeechError('Speech API key is missing. Check Creature speech settings.')
            if key:headers={'xi-api-key':key} if c.provider=='elevenlabs' else {'Authorization':'Bearer '+key}
        if c.provider=='openai':
            url=c.base_url.rstrip('/')+'/audio/speech'
            body={'input':text,'model':c.model,'voice':c.voice,'speed':c.speed,'response_format':'pcm'}
            if c.instructions:body['instructions']=c.instructions
        elif c.provider=='elevenlabs':
            url=c.base_url.rstrip('/')+'/text-to-speech/'+quote(c.voice,safe='')+'?output_format=pcm_24000'
            body={'text':text,'model_id':c.model,'voice_settings':{'speed':c.speed}}
        else:
            url=c.base_url.rstrip('/')+'/synthesize'
            body={'text':text,'length_scale':1/c.speed}
            if c.voice:body['voice']=c.voice
            if c.speaker_id is not None:body['speaker_id']=c.speaker_id
        try:
            import httpx
            with httpx.Client(timeout=c.timeout_seconds,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                if cancelled.is_set():return None
                self.requests+=1;self.characters+=len(text)
                deadline=time.monotonic()+c.timeout_seconds
                with client.stream('POST',url,json=body,headers=headers) as response:
                    if response.status_code!=200:
                        messages={401:'Speech API key was rejected.',403:'Speech API access was denied.',
                            429:'Speech provider limit reached.',404:'Speech model, voice or endpoint was not found.'}
                        raise SpeechError(messages.get(response.status_code,'Speech synthesis failed. Check the provider and Creature speech settings.'))
                    content_type=response.headers.get('content-type','').split(';')[0].strip()
                    if not (content_type.startswith('audio/') or content_type=='application/octet-stream'):
                        raise SpeechError('Speech provider returned a non-audio response.')
                    if c.provider!='piper' and content_type not in ('audio/pcm','audio/x-pcm','application/octet-stream'):
                        raise SpeechError('Speech provider did not return the requested raw PCM format.')
                    audio=bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if cancelled.is_set():return None
                        if time.monotonic()>deadline:raise SpeechError('Speech request timed out.')
                        audio.extend(chunk)
                        if len(audio)>MAX_AUDIO:raise SpeechError('Speech provider returned too much audio.')
            if cancelled.is_set():return None
            if c.provider=='piper':return decode_wav(audio)
            if not audio or len(audio)%2 or len(audio)>24000*2*180:
                raise SpeechError('Speech provider returned invalid PCM audio.')
            return bytes(audio),24000,1
        except SpeechError:raise
        except ImportError:raise SpeechError('Speech API support is missing. Run Setup-Mod again.') from None
        except Exception:raise SpeechError('Cannot reach the speech provider or read its audio. Check Creature speech settings.') from None


def play_audio(audio,config,cancelled):
    if cancelled.is_set() or audio is None:return
    try:
        import sounddevice as sd
        pcm,rate,channels=audio
        samples=array('h',pcm)
        pcm=array('h',(round(sample*config.volume/100) for sample in samples)).tobytes()
        with sd.RawOutputStream(samplerate=rate,channels=channels,dtype='int16',
                                device=config.output_device or None) as stream:
            step=max(2,rate//20*channels*2)
            for offset in range(0,len(pcm),step):
                if cancelled.is_set():stream.abort();return
                stream.write(pcm[offset:offset+step])
    except Exception:raise SpeechPlaybackError('Cannot play creature speech. Check the output device and audio support.') from None


class SpeechPlayer:
    def __init__(self,config,settings_path,*,synthesizer=None,player=play_audio):
        self.config=config;self.synthesizer=synthesizer or Synthesizer(config,settings_path)
        self.player=player;self.thread=None;self.cancelled=threading.Event();self.error=None;self.retryable_error=False

    @property
    def busy(self):return self.thread is not None and self.thread.is_alive()

    def start(self,text):
        if self.busy:return False
        self.cancelled=threading.Event();self.error=None;self.retryable_error=False
        def run():
            try:
                audio=self.synthesizer.synthesize(text,self.cancelled)
                if not self.cancelled.is_set():self.player(audio,self.config,self.cancelled)
            except SpeechError as error:
                if not self.cancelled.is_set():
                    self.error=str(error);self.retryable_error=isinstance(error,SpeechPlaybackError)
            except Exception:
                if not self.cancelled.is_set():self.error='Creature speech failed. Check its settings.'
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start();return True

    def cancel(self):self.cancelled.set()

    def close(self):
        self.cancel()
        if self.thread is not None:self.thread.join(.5)
