"""Opt-in push-to-talk capture and transcription, independent of the chat model.

Audio stays in memory. Provider failures never expose response bodies or keys.
Optional audio/model libraries are imported only when voice input is used.
"""
from dataclasses import dataclass, fields
import io
import ipaddress
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
import wave


HOTKEYS = {'F11': (0x7A,), 'Ctrl+F11': (0x11, 0x7A)}


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    provider: str = 'local'
    model: str = 'base'
    base_url: str = 'https://api.openai.com/v1'
    api_key_env: str | None = 'OPENAI_API_KEY'
    language: str = ''
    hotkey: str = 'F11'
    auto_send: bool = True
    microphone: str = ''
    max_recording_seconds: int = 60
    timeout_seconds: int = 60
    max_requests: int | None = None
    max_audio_minutes: float | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.auto_send) is not bool:
            raise ValueError('Voice enabled and auto-send must be booleans.')
        if self.provider not in ('local', 'api'):
            raise ValueError('Voice provider must be local or api.')
        if not isinstance(self.model, str) or len(self.model) > 512:
            raise ValueError('Enter a transcription model name or local model folder.')
        if not isinstance(self.microphone, str) or len(self.microphone) > 256:
            raise ValueError('Invalid microphone name.')
        if not isinstance(self.language, str) or not re.fullmatch(r'[a-z]{2,3}|', self.language):
            raise ValueError('Voice language must be a lowercase language code, such as en, or blank.')
        if self.hotkey not in HOTKEYS:
            raise ValueError('Choose a supported push-to-talk shortcut.')
        for name, low, high in (('max_recording_seconds', 5, 300), ('timeout_seconds', 5, 300)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'Voice {name} must be a whole number between {low} and {high}.')
        if self.max_requests is not None and (type(self.max_requests) is not int or self.max_requests < 1):
            raise ValueError('Voice API request limit must be a positive whole number, or blank for unlimited.')
        if self.max_audio_minutes is not None and (type(self.max_audio_minutes) not in (int, float) or
                not math.isfinite(self.max_audio_minutes) or self.max_audio_minutes <= 0):
            raise ValueError('Voice API audio limit must be positive minutes, or blank for unlimited.')
        if self.api_key_env is not None and (not isinstance(self.api_key_env, str) or
                not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', self.api_key_env)):
            raise ValueError('Voice API key variable must be an environment-variable name, or blank.')
        if not isinstance(self.base_url, str):
            raise ValueError('Voice API base URL must be text.')
        # Keep unused API fields when switching to local speech or text-only chat.
        # A complete connection is required only when API voice is enabled.
        if self.provider != 'api' or not self.input_enabled:
            return
        try:
            url = urlsplit(self.base_url)
            local = url.hostname == 'localhost'
            if not local:
                try: local = ipaddress.ip_address(url.hostname or '').is_loopback
                except ValueError: pass
            if (not url.hostname or url.username or url.password or url.query or url.fragment or
                    url.scheme not in ('https', 'http') or (url.scheme == 'http' and not local) or
                    '\\' in self.base_url or any(c.isspace() or ord(c) < 32 for c in self.base_url) or url.port == 0):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('Use an HTTPS transcription base URL, or HTTP on localhost, without credentials.') from None
        if self.provider == 'api' and not local and self.api_key_env is None:
            raise ValueError('Remote transcription needs an API key environment variable.')

    @property
    def input_enabled(self):
        return self.enabled and bool(self.model.strip())

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or not data.keys() <= {f.name for f in fields(cls)}:
            raise ValueError('Invalid voice settings.')
        return cls(**data)


class VoiceError(ValueError):
    """Only authored, user-readable messages may cross the UI boundary."""


def transcript_text(text):
    if not isinstance(text, str):
        raise VoiceError('The transcription service returned an unreadable reply.')
    text = ' '.join(text.split())
    if not text:
        raise VoiceError('No speech recognized. Hold the voice shortcut and try again.')
    if len(text) > 2000:
        raise VoiceError('That transcript is too long for chat. Please use a shorter recording.')
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise VoiceError('The transcription service returned invalid text.')
    return text


class Transcriber:
    def __init__(self, config, settings_path, *, report=None):
        self.config = config
        self.settings_path = Path(settings_path)
        self.local_model = None
        self.report = report
        self.requests = 0
        self.audio_seconds = 0.0

    def check_available(self):
        """One worker owns these counters; only API attempts consume the run budget."""
        if self.config.provider != 'api': return
        if self.config.max_requests is not None and self.requests >= self.config.max_requests:
            raise VoiceError('Voice API request limit reached for this run. You can still type in chat. '
                             'Change the limit in Settings > Voice to continue speaking.')
        if self.config.max_audio_minutes is not None and self.audio_seconds >= self.config.max_audio_minutes * 60 - 1e-9:
            raise VoiceError('Voice API audio limit reached for this run. You can still type in chat. '
                             'Change the limit in Settings > Voice to continue speaking.')

    def _check_recording(self, audio):
        self.check_available()
        try:
            with wave.open(io.BytesIO(audio), 'rb') as stream:
                seconds = stream.getnframes() / stream.getframerate()
        except (ValueError, wave.Error, EOFError):
            raise VoiceError('Cannot read the recording. Please record your message again.') from None
        if self.config.max_audio_minutes is not None:
            remaining = max(0.0, self.config.max_audio_minutes * 60 - self.audio_seconds)
            if seconds > remaining + 1e-9:
                raise VoiceError(f'This recording exceeds your voice API audio limit; {remaining:.1f} seconds remain. '
                                 'Try a shorter recording or change the limit in Settings > Voice. No audio was sent.')
        return seconds

    def transcribe(self, audio, *, cancelled=None):
        if cancelled is not None and cancelled.is_set(): return ''
        if self.config.provider == 'local':
            return self._local(audio, cancelled=cancelled)
        return self._api(audio, cancelled=cancelled)

    def _local(self, audio, *, cancelled=None):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise VoiceError('Local voice needs the voice-local extra. See the voice setup instructions.') from None
        try:
            if self.local_model is None:
                self.local_model = WhisperModel(self.config.model, device='cpu', compute_type='int8',
                    cpu_threads=2, num_workers=1,
                    download_root=str(self.settings_path.parent / '.local' / 'speech-models'))
            if cancelled is not None and cancelled.is_set(): return ''
            segments, info = self.local_model.transcribe(io.BytesIO(audio),
                language=self.config.language or None, beam_size=1, vad_filter=True,
                condition_on_previous_text=False)
            text = ' '.join(segment.text for segment in segments)
            if self.report:
                self.report({'detected_languages': [getattr(info, 'language', None)], 'returned_text': text[:2000]})
            return transcript_text(text)
        except VoiceError:
            raise
        except Exception:
            raise VoiceError('Local transcription failed. Check the model and its download, then try again.') from None

    def _api(self, audio, *, cancelled=None):
        seconds = self._check_recording(audio)
        try:
            import httpx
        except ImportError:
            raise VoiceError('API voice needs the voice extra. Run Setup-Mod again or install that extra.') from None
        from .credentials import credential_environment
        config = self.config
        try:
            env = credential_environment(config.api_key_env, self.settings_path.parent / '.local' / '.env')
        except ValueError:
            raise VoiceError('Cannot read the voice API key. Check the saved key in settings.') from None
        key = env.get(config.api_key_env) if config.api_key_env else None
        if config.api_key_env and not key:
            raise VoiceError('Voice API key is missing. Add it in Settings > Voice.')
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        data = {'model': config.model, 'response_format': 'json'}
        if config.language:
            # GPT Transcribe takes an array of language hints; compatible and
            # older transcription models use the singular language parameter.
            field = 'languages[]' if config.model == 'gpt-transcribe' else 'language'
            data[field] = config.language
        try:
            # No redirects, implicit proxies, retries, audio logs or file uploads
            # other than the one explicitly captured push-to-talk recording.
            deadline = time.monotonic() + config.timeout_seconds
            with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
                if cancelled is not None and cancelled.is_set(): return ''
                # Count attempts once, including failures or cancellation after
                # sending: the provider may already have processed the audio.
                self.requests += 1
                self.audio_seconds += seconds
                with client.stream('POST', config.base_url.rstrip('/') + '/audio/transcriptions',
                        headers=headers, data=data, files={'file': ('speech.wav', audio, 'audio/wav')}) as response:
                    if self.report: self.report({'http_status': response.status_code})
                    if response.status_code != 200:
                        messages = {401: 'Voice API key was rejected.', 403: 'Voice API access was denied.',
                            429: 'Voice provider limit reached. Wait a little before recording again.',
                            404: 'Transcription endpoint or model was not found. Check the Voice settings.',
                            413: 'The voice provider rejected the recording size. Use a shorter recording.'}
                        raise VoiceError(messages.get(response.status_code,
                            'Transcription failed. Check the voice model, endpoint and provider availability.'))
                    raw = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise VoiceError('Transcription timed out. Try again with a shorter recording.')
                        raw.extend(part)
                        if len(raw) > 65536:
                            raise VoiceError('The transcription service returned too much data.')
                    import json
                    result = json.loads(raw)
                    if self.report and isinstance(result, dict):
                        languages = result.get('languages')
                        self.report({'detected_languages': [v['code'][:16] for v in languages[:10]
                            if isinstance(v, dict) and isinstance(v.get('code'), str)] if isinstance(languages, list) else [],
                            'returned_text': result['text'][:2000] if isinstance(result.get('text'), str) else None})
                    return transcript_text(result.get('text') if isinstance(result, dict) else None)
        except VoiceError:
            raise
        except httpx.TimeoutException:
            raise VoiceError('Transcription timed out. Try again with a shorter recording.') from None
        except (httpx.HTTPError, ValueError, OSError):
            raise VoiceError('Cannot read the transcription response. Check the voice server and connection.') from None


def microphone_level(pcm):
    """A bounded RMS sample, scaled from -60 dBFS to full scale for the UI."""
    from array import array
    samples = array('h', pcm[-4096:])
    if not samples: return 0.0
    samples = samples[::max(1, len(samples) // 256)]
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return max(0.0, min(1.0, (20 * math.log10(rms / 32768) + 60) / 60)) if rms else 0.0


def capture(config, released, cancelled, on_recording, *, on_level=None):
    """Open the microphone only for this press; cap memory even if the key sticks."""
    try:
        import sounddevice as sd
    except ImportError:
        raise VoiceError('Microphone support is missing. Run Setup-Mod again or install the voice extra.') from None
    pcm = bytearray()
    full = threading.Event()
    failed = threading.Event()
    device = config.microphone or None
    rate = 16000
    try:
        try:
            sd.check_input_settings(device=device, channels=1, dtype='int16', samplerate=rate)
        except Exception:
            # Some Windows drivers accept only their native rate. Keep that
            # rate in the WAV; both transcription backends decode it themselves.
            rate = int(sd.query_devices(device, 'input')['default_samplerate'])
            if not 8000 <= rate <= 384000: raise ValueError()
            sd.check_input_settings(device=device, channels=1, dtype='int16', samplerate=rate)
    except Exception:
        raise VoiceError('Cannot use this microphone. Check the selected input device in Voice settings.') from None
    if cancelled.is_set(): return b''
    if released.is_set():
        raise VoiceError('Recording was too short. Hold the voice shortcut while speaking.')
    maximum = config.max_recording_seconds * rate * 2
    latest_level_audio = (b'', 0.0)

    def receive(data, frames, timing, status):
        nonlocal latest_level_audio
        if cancelled.is_set() or released.is_set(): return
        if status: failed.set()
        remaining = maximum - len(pcm)
        chunk = bytes(data)[:remaining]
        pcm.extend(chunk)
        if on_level is not None:
            latest_level_audio = (chunk[-4096:], time.monotonic())
        if len(pcm) >= maximum: full.set()

    try:
        with sd.RawInputStream(samplerate=rate, channels=1, dtype='int16',
                device=device, callback=receive):
            on_recording()
            deadline = time.monotonic() + config.max_recording_seconds
            next_level = 0.0
            while not released.wait(.025):
                now = time.monotonic()
                if cancelled.is_set() or full.is_set() or now >= deadline: break
                if on_level is not None and now >= next_level:
                    data, updated = latest_level_audio
                    on_level(microphone_level(data) if now - updated < .25 else 0.0)
                    next_level = now + .1
    except Exception:
        raise VoiceError('Cannot record from the microphone. Check the selected device and Windows microphone permission.') from None
    finally:
        if on_level is not None: on_level(0.0)
    if cancelled.is_set(): return b''
    if failed.is_set():
        raise VoiceError('Microphone audio was interrupted. Please try again.')
    if len(pcm) < rate // 2:
        raise VoiceError('Recording was too short. Hold the voice shortcut while speaking.')
    # Do not pay to transcribe digital silence or send silence hallucinations.
    from array import array
    samples = array('h', pcm)
    if math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples)) < 32:
        raise VoiceError('No microphone audio detected. Check your microphone and try again.')
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(pcm)
    return output.getvalue()


@dataclass(eq=False)
class Recording:
    identity: tuple
    released: threading.Event
    cancelled: threading.Event
    level: float = 0.0


class VoiceInput:
    """At most one capture/transcription job, with cancellation discarding late replies."""
    def __init__(self, config, settings_path, emit, *, recorder=capture, transcriber=None):
        from .voice_diagnostics import VoiceDiagnostics
        self.config, self.emit, self.recorder = config, emit, recorder
        self.diagnostics = VoiceDiagnostics(settings_path)
        self.details = {}
        self.transcriber = transcriber or Transcriber(config, settings_path,
            report=self.details.update if self.diagnostics.enabled else None)
        self.job = None
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, identity):
        if self.busy: return None
        job = Recording(identity, threading.Event(), threading.Event())
        self.job = job
        def work():
            from datetime import datetime, timezone
            started = time.monotonic()
            self.details.clear()
            if self.diagnostics.enabled:
                self.details.update(started_at=datetime.now(timezone.utc).isoformat(),
                    provider=self.config.provider, model=self.config.model if self.config.provider == 'api' else 'local',
                    language_hint=self.config.language or 'auto')
            try:
                self.transcriber.check_available()
                audio = self.recorder(self.config, job.released, job.cancelled,
                    lambda: self.emit('voice_status', (job, 'Listening')),
                    on_level=lambda level: setattr(job, 'level', level))
                if job.cancelled.is_set(): return
                if self.diagnostics.enabled:
                    from .voice_diagnostics import audio_summary
                    try: self.details['audio'] = audio_summary(audio)
                    except (ValueError, wave.Error, EOFError): self.details['audio'] = {'unreadable_wav': True}
                if job.cancelled.is_set(): return
                self.emit('voice_status', (job, 'Transcribing'))
                transcribing = time.monotonic()
                text = self.transcriber.transcribe(audio, cancelled=job.cancelled)
                if self.diagnostics.enabled:
                    self.details.update(transcript=text, transcription_seconds=round(time.monotonic()-transcribing, 3))
                if not job.cancelled.is_set(): self.emit('voice_text', (job, text))
            except VoiceError as error:
                if self.diagnostics.enabled: self.details['error'] = str(error)
                if not job.cancelled.is_set(): self.emit('voice_error', (job, str(error)))
            except Exception:
                if self.diagnostics.enabled: self.details['error'] = 'Voice input failed.'
                if not job.cancelled.is_set():
                    self.emit('voice_error', (job, 'Voice input failed. Check the Voice settings and try again.'))
            finally:
                job.level = 0.0
                if self.diagnostics.enabled:
                    self.details.update(cancelled=job.cancelled.is_set(), seconds_total=round(time.monotonic()-started, 3))
                    self.diagnostics.write(dict(self.details))
                self.emit('voice_done', (job, None))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return job

    def release(self):
        if self.job: self.job.released.set()

    def cancel(self):
        if self.job:
            self.job.cancelled.set(); self.job.released.set()

    def close(self):
        self.cancel()
        if self.thread: self.thread.join(.3)
