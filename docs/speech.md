# Creature speech

**Creature speech** turns new creature replies into spoken audio in desktop and
VR modes. It starts disabled and is independent of the **Voice** tab, which
controls microphone input. Enabling speech does not enable the microphone or
change push-to-talk preferences. The voice is synthesized, not a human recording.

Open **Settings → Creature speech**, apply a preset, and check
**Enable creature speech**. Choose a voice, speed, volume and output device, then
use **Test voice** to preview the unsaved choices. **Save & apply** activates
them in the running mod. Blank output device uses the Windows default; use
**Refresh Windows voices / devices** to select headphones or a headset explicitly.

## Providers

| Preset | What runs | Configuration |
| --- | --- | --- |
| Windows voices | Installed Windows speech engine, offline | Refresh voices and choose one, or leave blank for the default. No API key or model download. |
| Piper server | Separately installed local model/server, offline after download | Start the server below; choose an optional voice and speaker ID. |
| OpenAI | Remote speech API | API key, model and voice. Preset: `gpt-4o-mini-tts`, `coral`. |
| ElevenLabs | Remote speech API | API key, model and voice ID from your account. Preset model: `eleven_multilingual_v2`. |
| Custom / Local OpenAI-compatible | A compatible remote or local speech server | Its base URL, model, voice and optional local authentication. |

All preset values are editable. A chat or transcription endpoint alone is not
enough: compatible servers must implement `POST /audio/speech` and return signed
16-bit little-endian mono PCM at 24 kHz for `response_format: "pcm"`. API base URLs
include any `/v1` prefix; the mod appends the speech route. Remote URLs require
HTTPS; unauthenticated HTTP is allowed on loopback only.

Windows voices use [System.Speech](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer).
Only voices installed for that engine are listed; Windows natural/Narrator voices
are not necessarily available to it. Speed maps approximately to Windows' rate
control. API speed ranges are provider-specific: ElevenLabs accepts 0.7–1.2.
**Voice direction** is sent only to OpenAI-compatible APIs and requires a model
that supports instructions; leave it blank for models such as `tts-1`.
See the [OpenAI speech API](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)
and [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)
for provider options and account availability.

### Piper offline models

Piper is optional software installed separately; this mod does not bundle its
engine or voices. Create a separate Python environment in a writable folder:

```powershell
py -m venv piper-env
.\piper-env\Scripts\python.exe -m pip install "piper-tts[http]"
.\piper-env\Scripts\python.exe -m piper.download_voices en_US-lessac-medium
.\piper-env\Scripts\python.exe -m piper.http_server -m en_US-lessac-medium
```

Keep the server running. Apply **Piper server (offline models)** in the mod;
the default URL is `http://localhost:5000`. Leave voice blank to use the server's
default, or enter a loaded voice name. Speaker ID is optional for multi-speaker
models. Downloading the engine/model needs internet; subsequent local synthesis
does not. Follow [Piper's HTTP server guide](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md)
and the engine/model licenses. The integration expects 16-bit PCM WAV responses
from `/synthesize`; it does not start the server or download a voice itself.

## Microphone feedback protection

**Microphone feedback protection** is an optional checkbox, on by default.
It pauses microphone input while the creature's speech is being generated and
played, with a short pause afterward. Holding push-to-talk interrupts playback;
continue holding to record once playback has stopped. An existing push-to-talk
recording takes priority over a queued creature line.

With headphones or a headset, turn protection off to keep the microphone
available while the creature speaks. This does not change voice activation,
push-to-talk or automatic sending. Protection is not acoustic echo cancellation:
it cannot filter the game's dialogue or other nearby speakers.

## Playback, limits and errors

Only new creature reply text is spoken. Status notices, transcriptions and errors
stay silent. Replies retained from before startup, reconnecting or a creature
change are not replayed. Playback stops on focus loss, menus or loading. Brief
game-data gaps do not interrupt a line; a gap lasting three seconds stops playback.
New replies wait for fresh data before starting. Keep the game/mirror focused in VR. One reply plays at a time; only
the latest waiting reply is kept, and it expires after ten seconds.

Subtitles and desktop text still work with speech disabled or unavailable. Audio
is separate from the game renderer: it has no positional sound, lip sync or
synchronization with subtitle pages.

API synthesis sends the creature's reply text and voice settings to its provider
and may cost money, including **Test voice**, automatic replies and `--dummy`
chat. It does not send microphone recordings, observations or chat history to
the speech provider. Keys use the same private `.local/.env` store as chat/voice;
use different variable names for different keys. Blank key fields preserve saved
keys, and process environment values take precedence.

Set optional synthesis request/character caps and the timeout in **Limits**.
Server attempts count even if they fail; Windows voices are uncapped. Usage
resets on restart or **Save & apply**. Each preview has its own limits. These
limits are separate from chat and transcription budgets.

Audio stays in memory. Requests are bounded and not automatically retried.
A synthesis/provider failure stops further creature speech until **Save & apply**;
chat continues. A playback-device failure allows later replies to retry after a
five-second cooldown, without replaying the failed line. Identical output errors
are announced once until playback succeeds. Desktop shows a creature-speech error, and VR queues a red
`[Voice error: Creature speech: ...]` subtitle using the existing notice renderer.
Errors contain fixed descriptions, not raw provider responses or keys. Correct
the voice, credentials, local server or output device and apply settings to retry.
