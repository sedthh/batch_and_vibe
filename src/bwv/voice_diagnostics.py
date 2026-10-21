"""Opt-in, bounded local voice diagnostics. Never store audio or credentials."""
from array import array
import io
import json
import math
from pathlib import Path
import tempfile
import wave


def audio_summary(audio):
    with wave.open(io.BytesIO(audio), 'rb') as stream:
        rate, channels, width = stream.getframerate(), stream.getnchannels(), stream.getsampwidth()
        frames = stream.getnframes()
        pcm = stream.readframes(frames)
    result = {'seconds': round(frames / rate, 3), 'sample_rate': rate,
              'channels': channels, 'sample_bytes': width}
    if width == 2 and pcm:
        samples = array('h', pcm)
        rms = math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples))
        peak = max(abs(x) for x in samples)
        result.update(rms_dbfs=round(20 * math.log10(rms / 32768), 1) if rms else None,
                      peak_dbfs=round(20 * math.log10(peak / 32768), 1) if peak else None,
                      clipped_percent=round(100 * sum(abs(x) >= 32767 for x in samples) / len(samples), 2))
    return result


class VoiceDiagnostics:
    MAX_RECORDS = 20
    MAX_BYTES = 512_000

    def __init__(self, settings_path):
        settings_path = Path(settings_path)
        self.path = settings_path.parent / '.local' / 'voice-diagnostics.json'
        # Read once per overlay start; no microphone or settings polling.
        self.enabled = settings_path.is_file() and self.path.with_name('voice-debug.enabled').is_file()

    def write(self, record):
        if not self.enabled:
            return
        temporary = None
        try:
            records = []
            try:
                with self.path.open('rb') as stream:
                    raw = stream.read(self.MAX_BYTES + 1)
                previous = json.loads(raw) if len(raw) <= self.MAX_BYTES else None
                if isinstance(previous, list):
                    records = previous[-(self.MAX_RECORDS - 1):]
            except (OSError, ValueError):
                pass
            records.append(record)
            # Escape Unicode to retain exact codepoints in any Windows editor.
            payload = json.dumps(records, ensure_ascii=True, indent=2, allow_nan=False)
            while len(payload) > self.MAX_BYTES and len(records) > 1:
                records.pop(0)
                payload = json.dumps(records, ensure_ascii=True, indent=2, allow_nan=False)
            if len(payload) > self.MAX_BYTES:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='voice-debug-', suffix='.tmp',
                    mode='w', encoding='ascii', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            temporary.replace(self.path)
        except (OSError, ValueError, TypeError):
            pass  # Diagnostic storage must never break voice input.
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass
