"""Local speech detection for opt-in, keyboard-free VR chat."""
from collections import deque
from array import array
import io
import queue
import threading
import time
import wave

from .voice import VoiceError,microphone_level


# WebRTC can label quiet background noise as speech. Require sustained
# energy above -55 dBFS as an additional noise filter.
# This affects automatic segmentation only, never push-to-talk recordings.
MIN_SPEECH_MEAN_SQUARE = (32768 * 10 ** (-55 / 20)) ** 2


class SpeechSegment:
    """20 ms frames: keep a short pre-roll, require speech, end after 1.2 s quiet."""
    def __init__(self,rate,maximum_seconds,detector):
        self.rate=rate;self.size=rate//50*2;self.detector=detector
        self.maximum=maximum_seconds*50
        self.pre=deque(maxlen=15);self.votes=deque(maxlen=10)
        self.frames=[];self.quiet=0;self.voiced=0;self.started=False;self.finished=False

    def feed(self,frame):
        if self.finished:return
        if len(frame)!=self.size:raise ValueError('Invalid speech frame size')
        detected=bool(self.detector(frame,self.rate))
        samples=array('h',frame)
        speech=detected and sum(sample*sample for sample in samples)/len(samples)>=MIN_SPEECH_MEAN_SQUARE
        if not self.started:
            self.pre.append(frame);self.votes.append(speech)
            if len(self.votes)==10 and sum(self.votes)>=8:
                self.started=True;self.frames=list(self.pre);self.voiced=sum(self.votes)
            return
        self.frames.append(frame)
        self.voiced+=speech
        self.quiet=0 if speech else self.quiet+1
        self.finished=self.quiet>=60 or len(self.frames)>=self.maximum

    def audio(self):
        if not self.finished or self.voiced<15:return b''
        output=io.BytesIO()
        with wave.open(output,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(self.rate)
            stream.writeframes(b''.join(self.frames))
        return output.getvalue()


def capture_activated(config,released,cancelled,on_recording,*,on_level=None):
    """Listen locally for one phrase. Empty idle windows never reach a provider."""
    try:
        import sounddevice as sd
        import webrtcvad
    except ImportError:
        raise VoiceError('VR voice activation needs microphone support and webrtcvad-wheels. Run Setup-Mod again.') from None
    detector=webrtcvad.Vad(2)
    device=config.microphone or None
    rate=None
    for candidate in (16000,48000,32000,8000):
        try:sd.check_input_settings(device=device,channels=1,dtype='int16',samplerate=candidate)
        except Exception:continue
        rate=candidate;break
    if rate is None:raise VoiceError('The microphone needs a supported mono speech rate (16 or 48 kHz preferred).')
    if cancelled.is_set() or released.is_set():return b''
    segment=SpeechSegment(rate,config.max_recording_seconds,detector.is_speech)
    chunks=queue.Queue(maxsize=100);failed=threading.Event()

    def receive(data,frames,timing,status):
        if cancelled.is_set() or released.is_set():return
        if status or len(data)!=frames*2 or frames>rate:failed.set();return
        try:chunks.put_nowait(bytes(data))
        except queue.Full:failed.set()

    try:
        with sd.RawInputStream(samplerate=rate,channels=1,dtype='int16',device=device,
                               callback=receive):
            on_recording()
            idle_until=time.monotonic()+30
            recording_until=None
            pending=bytearray()
            while not cancelled.is_set() and not released.is_set():
                if failed.is_set():raise VoiceError('Microphone audio was interrupted. Restart voice activation in settings.')
                now=time.monotonic()
                if not segment.started and now>=idle_until:return b''
                if recording_until is not None and now>=recording_until:
                    # An interrupted device must not upload an incomplete phrase.
                    raise VoiceError('Microphone stopped supplying audio. Restart voice activation in settings.')
                try:chunk=chunks.get(timeout=.05)
                except queue.Empty:continue
                # Let the driver choose its buffer size, as push-to-talk does.
                # WebRTC still receives exact 20 ms frames, independent of callbacks.
                pending.extend(chunk)
                while len(pending)>=segment.size and not segment.finished:
                    frame=bytes(pending[:segment.size]);del pending[:segment.size]
                    segment.feed(frame)
                if segment.started and recording_until is None:
                    recording_until=now+config.max_recording_seconds+2
                if on_level is not None:on_level(microphone_level(chunk))
                if segment.finished:break
    except VoiceError:raise
    except Exception:
        raise VoiceError('Cannot record for VR voice activation. Check the microphone and Windows microphone permission.') from None
    finally:
        if on_level is not None:on_level(0.0)
    if cancelled.is_set() or released.is_set():return b''
    return segment.audio()
