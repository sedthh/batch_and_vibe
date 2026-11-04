# Agent reference: settings and troubleshooting

Use this with [SKILL.md](../SKILL.md) when configuring or fixing a player's setup.
Work in the user's checkout and preserve their choices. Use direct file edits for
requested configuration changes; the UI is useful for entering keys, selecting
devices and trying voices. Do not make the user manually reproduce edits you can
complete and validate yourself.

## Find the active configuration

| Location | Purpose |
| --- | --- |
| `settings.json`, or the path passed with `--config` | Private chat, voice, speech, prompt and gameplay preferences. |
| `.local/.env` beside that settings file | Saved provider keys; actual process environment values take precedence. |
| `.local/launcher.json` beside that settings file | Selected installation under `game_directory`. |
| `.local/launcher-display.json` beside that settings file | Saved `window_size`. |
| `src/bwv/settings.default.json` | Distributable defaults, not the player's active configuration. |

Inspect only relevant fields. Do not print entire settings, environment variables,
credential files or private prompts into tool output. To check a key, call
`credential_environment(name, env_path)` from `bwv.credentials` and report only
whether the configured key is present. Read keys through this helper rather than
sourcing `.env` as a shell script.

Load with `LLMConfig.load(path)`: it merges defaults, validates fields and handles
supported migrations. Missing nested values use their dataclass defaults. If
validation fails, inspect the relevant section and schema; do not replace the
file with defaults or delete unfamiliar fields to make it load.

## Edit settings directly

Use `.venv\Scripts\python.exe` from the checkout. `LLMConfig.save` writes atomically;
dataclass replacement validates before writing and preserves unrelated values.
Keep a private backup when changing an existing configuration. For example,
**when the user requests Windows speech with headset feedback protection off**:

```python
from dataclasses import replace
from pathlib import Path
import shutil
import time
from bwv.llm import LLMConfig

path = Path("settings.json")  # Use the actual --config path if different.
current = LLMConfig.load(path)
updated = replace(current, speech=replace(
    current.speech, enabled=True, provider="windows", feedback_protection=False))
backup = path.parent / ".local" / "settings-backups"
backup.mkdir(parents=True, exist_ok=True)
shutil.copy2(path, backup / f"{time.time_ns()}.json")
updated.save(path)
assert LLMConfig.load(path) == updated
assert updated.voice == current.voice
print("Settings validated and saved.")
```

Do not run that example merely because it is present; apply the user's requested
values. To validate without changing anything:

```powershell
.venv\Scripts\python.exe -c "from bwv.llm import LLMConfig; LLMConfig.load('settings.json'); print('Settings valid')"
```

An already-open settings form holds old values. Close it without saving before
editing the file; afterward reopen **Settings → Save & apply**, or restart the mod.
There is no file watcher. Save & apply reloads services and resets usage counters
without closing the game. A full launcher restart requires administrator access;
avoid introducing an elevation prompt while the game is running.

### Fields that matter

| Goal | Fields / behavior |
| --- | --- |
| Chat provider | Top-level `model`, `api`, `base_url`, `api_key_env`, `output_format`, `token_parameter`, `reasoning_effort`. Use the supported preset in `launcher_ui.py`, then fill the user's model. |
| Thinking and cost | `cadence`, `event_cooldown_seconds`, `max_messages`, `max_output_tokens`, `max_tool_rounds`, `max_tool_calls`. `--no-ticks` disables automatic thinking for that run. |
| Conversation history | `history_pairs`, `history_chars`; separate from persistent creature memory. |
| Character prompt | `prompts.general`. A saved custom prompt overrides bundled prose; changing the default file does not update that override. Fixed tool/engine contracts remain in code. |
| Microphone input | `voice.enabled`, `provider` (`local` / `api`), `model`, `language`, `microphone`. An empty model disables capture even if the checkbox is on. |
| Input controls | `voice.push_to_talk=true`: hold `voice.hotkey`. `false`: automatic voice activation. `voice.auto_send=true` sends recognized words; VR needs this because it has no transcript-review UI. |
| Spoken replies | `speech.enabled`, `provider` (`windows` / `piper` / `openai` / `elevenlabs`), `model`, `voice`, `speed`, `volume`, `output_device`. Independent of microphone settings. |
| Feedback protection | `speech.feedback_protection`; optional microphone pause during synthesis/playback. Preserve the user's headset preference. |
| Speech/API limits | `voice.max_requests`, `voice.max_audio_minutes`; `speech.max_requests`, `speech.max_characters`, plus each section's timeout. Optional caps use `null` for unlimited, not zero. |
| VR | `vr_mode`, also overridden by `--vr` / `--no-vr` and launch buttons. No separate VR game-folder setting; BW1-VR can share the game installation. |
| Music | `winamp_music`, `music_lyrics_online`; see [music setup](usage.md#winamp-music). |

Use the validators in `llm.py`, `voice.py` and `speech.py` for current ranges.
Chat, transcription and synthesis have separate endpoints and budgets. Do not
assume an LLM model can transcribe or synthesize speech. Voice IDs and installed
devices belong in private settings, never public defaults.

## Launch and verify

Run **Start-Mod.cmd first**, accept administrator access, then use its **Run B&W
windowed**, **Run B&W borderless fullscreen**, or **Run B&W VR** button. This also
selects the correct game directory and gives the game/VR launcher administrator
access. Do not tell the player to start the game before the mod.

Reuse the selected installation unless the user asks to switch. Folder names are
not version evidence: use `bwv.cli inspect "<game-folder>" --shareable`. The bridge
supports the verified 1.42 build. Close the game before updating its DLL;
rebuilding alone does not install it. `Start-Mod.cmd --install` installs the build
and clears old chat history while keeping creature memories.

Use the three launcher indicators as configuration checks, not proof of a working
remote connection. LLM runtime errors also turn its indicator yellow. For local
Windows voice enumeration, `bwv.speech_audio.windows_voices()` lists what this mod
can use without playing audio. A preview or real reply verifies output separately.
Do not start a second launcher or reuse old helper scripts with recorded PIDs.

## Diagnose the symptom

| Symptom | First checks |
| --- | --- |
| No creature replies | Correct game/profile, playable scene and window focus; check `--no-ticks`, provider errors and exhausted limits. Inspect the LLM debugger's current request/error, not just the configured model name. |
| No microphone capture | `voice.enabled`, nonblank model, push-to-talk vs activation, correct input device and Windows desktop microphone permissions. PTT means hold a key; it does not mean automatic listening. |
| Capture works, words are wrong | Check the transcript, `voice.language`, microphone level and recognition provider. Do not change the creature prompt to fix transcription. |
| Transcript exists but no question sent | `voice.auto_send`, pending chat, current scene and identity. VR cannot review a desktop draft. |
| Creature text appears but no speech | `speech.enabled`, volume, selected Windows/provider voice and output device. Use Test voice. Check the desktop error or VR notice for synthesis versus playback failure. |
| Speech cuts off | Focus loss, loading/scene changes or prolonged stale game data stop playback. Brief data gaps should not. Output-device failures allow later replies after a cooldown; synthesis/provider failures need correction and Save & apply. |
| VR launch unavailable | `Settings → VR` lists missing BW1-VR files. Normal desktop launch remains available. |
| VR notices missing/wrong colour | Verify the installed bridge matches the built DLL. Reinstall with the game closed. Story dialogue delays notices; ready/off/failure messages are one-time state changes, not listening indicators. |
| Creature says it cannot do something | Compare offered actions, game observations and in-game learned abilities. A selected action is not proof it completed. |
| Wrong/fresh creature or missing saves | Verify the selected installation and profile before touching files. Launch via this mod to set the game directory. Do not assign a guessed creature file, restore unrelated backups or overwrite saves. |

Enable **Debugging** in the launcher and open its current observer/LLM links.
This can be done while playing; do not reuse expired URLs or tokens. A run
started with `--debug` also writes `.local/overlay-diagnostics.json`, useful for
focus, availability and voice state. These files may be stale after another run;
check timestamps. Normal launcher output is not automatically saved to a log.

For voice diagnosis, enable the opt-in `.local/voice-debug.enabled` marker beside
the active settings, then Save & apply. `.local/voice-diagnostics.json` keeps the
latest 20 attempts with transcript, language, timing and audio levels, not audio.
Remove the marker and apply again when finished. Inspect only what the reported
problem requires; transcripts and debugger data are private.

`--dummy` replaces chat only: API transcription and creature speech can still
cost money. For offline tests, disable those APIs or choose offline providers.
Use `--no-actions` when no game action is needed. Report what was actually tested
and any remaining live/headset check; passing synthetic tests alone does not prove
the player's microphone, account or headset works.
