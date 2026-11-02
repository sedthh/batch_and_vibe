# Using Black & White LLM Creature Mod

For VR support, see [experimental BW1-VR compatibility](vr.md).
Use **Run B&W VR**, the third button in our start window. Missing installation
files disable that button; **Model, API key & settings > VR** explains what is
missing. `--vr` also enables native subtitles. Voice settings remain your choice.

Requires Windows, Python 3.11+ with tkinter, and your own Black & White installation.
The mod supports the verified 1.42 engine layout; some unofficial patches are not
compatible. No game files are included.

## Set up and play

1. Close the game and run `Setup-Mod.cmd`. It installs dependencies, builds the
   mod and prompts for the game folder and administrator access.
2. Run `Start-Mod.cmd`. If model setup is needed, the settings form opens. You can
   also open it with **Model, API key & settings** in the mod window, or run
   `Start-Mod.cmd --configure` to open only the form without starting the mod.
   If the game-side mod bridge is missing, Start-Mod installs the compiled copy
   automatically while the game is closed. Installation shows a success popup.
   If the compiled copy is missing too, run Setup-Mod first.
   Apply an OpenAI, Claude, Gemini, or custom/local
   preset, then enter your model ID and key. You can edit the URL and protocol.
   Applying a preset clears the model, reasoning selection and unsaved key;
   nothing is saved until you choose Save.
3. Accept the mod's administrator prompt before launching the game. If you used
   `--configure`, run `Start-Mod.cmd` now. Launch the game as administrator, then
   Continue or load a save.
4. Press **Ctrl+Enter** to chat with your creature. Up/Down recalls recent messages.

Commands can include fishing or drinking from natural water. If an action needs empty
hands, the mod first asks the creature to put its held object down. If a target
vanishes before execution, it can try another of the same kind up to twice.
Uncertain outcomes are not repeated automatically, to avoid duplicate actions.
Nearby context distinguishes recognized building types and fences. Inspection and
search can use kinds such as `house`, `storage`, `nursery`, `cemetery`, `temple` or
`fence`. These are known objects, not a complete map of everything on screen.
Model turns include elapsed seconds since the previous sent observation, separate
from the island's time of day. Creature and god-hand positions use
horizontal X/Z coordinates. Nearby buildings and other creatures can also show
coordinates in inspection; portable objects keep their IDs. Use object actions
for objects and `move_to(target={"x":X,"z":Z})` for a ground location. Coordinates belong
to the current island; they are not a permanent destination across saved games.
The same `move_to` tool accepts an object ID, a movement target ID, or the special
destinations `god_hand` and `player_temple`. It accepts `pace: auto`, `walk` or `run`. Approaching the god hand
defaults to running and follows its live position; ask it to walk if you prefer.
Other movement uses the engine's pace unless the model selects one. The choice
applies to that movement, not to later creature behaviour.
The model can make up to six tool calls per turn by default, but select only one
physical action. `find_objects` pages through objects, and `inspect_object` can
read several IDs together. `find_targets` looks for objects suitable for an action.
`do_action(action="eat")` handles feeding from stores, fields, trees and food piles.
`use_held_object` handles putting down, eating, examining, bringing home, throwing
or casting a held bubble. Examining returns available details and selects the
native held-item animation in one call. Inspecting the creature's held item by ID
does the same when examination is available and no other action is selected.
Without an emoji, examination randomly chooses examine, shake or smell, never
stroke. 🔍 chooses examine, 👂 shake/listen, 👃 smell and 🫳 stroke. These cues
work only while the creature holds something. A cue with an inspection chooses
that inspection's gesture; it does not add a second action. A cue alone plays
the gesture without returning inspection details.
Inspecting other objects or the creature's general state remains read-only.
Search synonyms keep the object's canonical name and ID in the results.
The creature receives a rough time-of-day phase from the engine's sky clock and
available local rain/snow readings. Native intentions retain their
recognized targets between turns, but a planned kick is not proof of a hit.

The mod attaches automatically and reconnects after game restarts. Creature actions
and automatic thinking are enabled by default; paid providers may charge for calls.
Use **Stop mod** or close the mod window to stop it without closing the game.

While the model answers a question or thinks on a tick, the mod pauses new
autonomous decisions. An action already underway can still finish. The pause
ends with the model turn, including errors and timeouts; player commands and
scene control take precedence. Other objects can still move or disappear.

The launcher has **Run B&W windowed** with a size dropdown, and **Run B&W borderless
fullscreen** using the monitor containing the launcher. Borderless is a windowed
game with its frame removed, not native exclusive fullscreen. Launching from the elevated mod inherits administrator
access. Close the game before changing modes. All launch buttons set the game's
registry `GameDir` to the selected installation, so the cached save root points
to that folder. A failed launch restores the previous value. The desktop buttons
also save the windowed/fullscreen flag and display width/height. When BW1-VR is installed, they
also temporarily select its desktop backend and restore its previous launch settings
afterward; see [VR compatibility](vr.md). Saves are untouched.
New model calls, including tool follow-ups, pause in unsupported desktop fullscreen mode,
when the game window is minimized/unavailable, or if the overlay process exits.
Already-sent requests cannot be un-sent. Windowed mode resumes with fresh observations.

## Voice input

Open **Model, API key & settings → Voice**, enable voice, and choose a preset.
**Push to talk** is checked by default; uncheck it to use voice activation.
VR mode leaves this control and your transcript sending preference unchanged.

Available presets:

- **Local Whisper:** speech stays on your computer. From the repository folder,
  install the optional model runtime once:
  `.venv\Scripts\python.exe -m pip install -e ".[voice-local]"`.
  Start with the `base` model; `tiny` is faster and `small` may be more accurate.
  The first recording downloads the chosen model to `.local/speech-models`, so it
  takes longer. You can also enter an existing faster-whisper model folder.
  Processing uses two CPU threads and loads one model at a time.
- **OpenAI transcription:** enter a speech model and API key. The preset uses
  `gpt-transcribe`. The speech provider can differ from your chat provider.
- **Groq Whisper:** choose **fast** (`whisper-large-v3-turbo`) or **accuracy**
  (`whisper-large-v3`), then enter your Groq key.
  See [Groq's speech models](https://console.groq.com/docs/speech-to-text).
- **Together AI:** uses `openai/whisper-large-v3`; enter your Together key.
  See [Together's transcription guide](https://docs.together.ai/docs/inference/transcription/overview).
- **Custom compatible API:** enter your provider's speech model, base URL and key.
- **Local compatible server:** enter its model and base URL, and leave the key
  variable blank if it needs no key. Other remote providers work if they support
  the OpenAI-compatible `/audio/transcriptions` endpoint and JSON text responses.
  Remote URLs must use HTTPS. An LLM-compatible API does not necessarily support audio.

Presets fill in the connection details without changing your language, microphone,
shortcut or voice limits. The model field stays editable. Applying a preset clears
any key you are currently typing; it does not delete saved keys or change chat settings.

Run `Setup-Mod.cmd` again if microphone support is missing. Choose your microphone
with **Refresh**, or leave it blank for the Windows default. Allow microphone access
for desktop apps in Windows settings. **Save & apply** reloads voice settings too.

In the game, hold **F11** (the default), speak, and release. Voice settings let you
choose **F1-F24**, with optional **Ctrl**. Controller-to-keyboard remapping can
target these shortcuts; see [VR input setup](vr.md). The input field shows **[Listening]**
with a volume bar that grows as you speak louder, then **[Transcribing]**.
The creature's reply stays visible above it. The words it heard then replace the
status in the normal chat input.
It sends them just like a typed message: the field stays visible and disabled while
waiting, then clears and becomes editable when the reply starts appearing.
If sending fails, the text stays in the field so you can edit or retry it.
You can change the shortcut and turn off automatic sending to review the words
before pressing Enter. **Escape** cancels recording or transcription;
leaving the game window or entering a menu also cancels an unfinished recording
or transcription. Finish or clear a typed draft before recording. The default
recording limit is 60 seconds; reaching it stops recording and transcribes the clip.

With push-to-talk enabled, a key press opens the microphone. With it off, voice
activation listens while chat is available and the game is focused.
Audio stays in memory and is discarded after
transcription; the mod does not save recordings. API mode sends that recording to
your chosen speech provider and may cost money, **even with dummy chat enabled**.

For voice troubleshooting, create an empty `.local/voice-debug.enabled` file,
then use **Save & apply**. The local `.local/voice-diagnostics.json` log keeps only
the latest 20 attempts: transcript, detected language, timing and audio levels.
It contains spoken text but no audio or API keys. Remove the marker and apply
settings again to stop logging. These files are ignored by Git.
Cancelling discards the result but cannot retract an API request already sent.
Local mode runs offline after its model download. The resulting words use normal
creature chat and its existing history, usage limits and action rules. Speech API
usage is separate from the LLM message cap and token counter. Creature replies are
still text; this version does not speak them aloud.

The **Voice** tab has optional API limits for transcription requests and total
audio minutes per run. Leave either blank for unlimited usage. A recording that
would exceed the audio budget is not uploaded; you can record a shorter message
or change the limit. Failed requests and requests cancelled after sending count
toward both limits because the provider may already have processed the audio.
Reaching a cap leaves typed chat available. These limits do not cap local Whisper.
Restarting the mod or using **Save & apply** starts a fresh voice budget.

## Winamp music

Use your existing Winamp installation and the original
[Black & White MP3 add-on](https://www.bwgame.net/downloads/mp3-addon.137/).
The add-on's instructions say to start Winamp before loading the game. Keep that
setup responsible for playback and the creature's native music reactions; this
mod neither installs nor disables the add-on and does not add an MP3-folder player.

1. Open **Model, API key & settings → Music**, enable **Connect to Winamp**, and
   **Save & apply**. The setting is off by default (`winamp_music: false`).
2. Play a track in Winamp. Use a single Winamp instance.
3. Speak, sing or type through ordinary chat. No start/stop phrases or lyric mode
   are required. The integration adds playback data without classifying your words,
   withholding action tools, changing command handling or pausing automatic thinking.

The connection reads playback state, displayed track label, playback position
and track duration. Available readings accompany normal scheduled
model turns and chat; track changes do not trigger extra calls. Each chat message
also keeps its sampled playback context at submission.
The agent sees a compact music block: state, title, and position/duration in seconds.
Internal source labels, track-session IDs and sample-age
metadata stay in diagnostics. An identical submission-time block is omitted.

`listening_history` adds one compact label based on song starts observed by the mod:
`new` (zero or one observed start), `rare` (two or three), `occasional` (four to nine),
`regular` (ten to twenty-four), or `favorite` (twenty-five or more).
Counts never enter model context. Joining a song
midway still shows `new` if it has no prior recorded starts, without incrementing
the counter. A start must be sampled within the first five seconds. Pausing,
ordinary seeking, repeated polls and reconnecting to the same active song do not
increase it. A sampled end-to-start wrap can count a repeat; a seek at that exact
boundary can be indistinguishable. Missed starts while disconnected are not guessed.

`on_repeat: true` appears when the current song accounts for at least three of the
last ten observed song starts, including the current start when observed. The
plays need not be consecutive: repeating a small playlist also qualifies. This
describes recent rotation, separately from lifetime `listening_history`, and is
omitted when the condition no longer holds. Polls and pauses do not fill this
window. The recent window is saved alongside the counts and survives restarts;
older history files retain their counts and begin with an empty recent window.

History survives restarts in `music-history.json` beside the runtime's conversation
store. It holds bounded counts under hashed song identifiers, not song names,
file paths or lyrics. The filename identifies the song when available; otherwise
the displayed title/artist is used, so indistinguishable labels may share history.
The labels describe observed repetition rather than total listening outside the
mod. Dummy and live runtime stores remain separate.

For a local MP3, the connection also reads embedded tags using TinyTag and caches
up to eight files, refreshing a file when its size or modification time changes.
Available title, artist, genre and tagged BPM are included; album art, comments,
file paths and other tags are excluded. Missing tags leave the window-title
fallback working. These tags describe the file; BPM is not a live beat measurement.
The reader uses only bounded, read-only access to Winamp's filename string and MP3
tag regions. Unsupported, denied or changing filename readings omit file metadata.

The optional **Look up missing lyrics online (LRCLIB)** checkbox in **Music**
uses [LRCLIB](https://lrclib.net) without an API key. It is off by default and
requires the Winamp connection. Embedded lyrics take precedence; otherwise it
sends only the tagged artist, cleaned track title and duration, never the audio,
file path or player's words. Lookup runs in the background. Exact artist/title
matches can supply timed lyrics when durations agree within two seconds; a
different duration permits plain text only. Missing artist tags skip lookup.
Unavailable service results leave ordinary music context working.

Results are cached in `music-lyrics` beside the runtime's conversation store:
successful lyrics for 30 days, missing results for one day and failures for five
minutes, with at most 128 files. Full lyrics stay in this local cache and are
not included in chat context. Service retry delays are respected.

Timestamped lyrics (embedded SYLT, LRC-formatted tags or LRCLIB results) can supply
at most two nearby cues and 320 UTF-8 bytes of lyric text across the entire turn.
All exposed timestamps use seconds. Recording-time lyrics take priority over
submission-time or current playback. When lyrics lack timestamps, a local
literal-word regex search can retrieve one passage matching at least three
consecutive words from the player's submitted message, ignoring case/punctuation.
That excerpt uses `match: words`, without a fabricated lyric timestamp. It shares
the same 320-byte budget; full lyric sheets are never sent. Matching only retrieves
context: it does not classify singing, change the message or gate action tools.
When no specific match is available, repeated one- or two-line phrases can supply
one short `match: refrain` hint for the creature to sing. This is a recurring
phrase, not a claim about the current verse or its timing. Unique lyrics supply
no generic refrain. Recorded voice searches its sampled song(s), not a replacement
track at submission. No audio is transcribed from the song. Old observations lose their lyric excerpts
when assembling later model requests, while the player's words, replies, song
identity and timing remain. The current excerpt may recur in tool rounds or
consecutive turns, but full lyric sheets never enter model context or saved chat turns.

Voice messages additionally carry the approximate captured audio window. Playback
history supplies the track and position sampled near the beginning and end, plus
observed track/state changes during the recording. Idle microphone waiting and
transcription delays are excluded. This works with push-to-talk, voice activation,
automatic sending and unchanged transcripts sent after review, including VR.
Replacing/editing the transcript drops the recording association; ordinary chat
still works. Playback history stays in memory for up to ten minutes and clears on
a game-connection gap. Missing samples are reported as unavailable, never replaced
with the track playing at submission. Timing includes sample age and is approximate,
not beat-accurate synchronization or proof that your words match a song's lyrics.

On the supported game build, optional read-only sensors also expose native music
feature flags, the current native music-mood entry and recent feedback in raw
engine units. Verified controller state can identify an active dance animation.
The separate game line-input analyser supplies activity, beat-detection status and
detected tempo when its native strength/freshness checks pass. Its 120 BPM fallback
is never presented as a measured tempo. Line-input observations are labelled with
their own source; they are not automatically attributed to the Winamp track. The
reader does not enable line-input capture or change the add-on's configuration.
The agent's `native_music` block contains only available `mood_raw`, `feedback_raw`
and `line_input` beat/tempo readings; an empty block is omitted. Verified dancing
belongs to the normal `activity.dancing` state, independently of music settings.
Voice playback context is also compact, with seconds throughout and observation
gaps marked only when present.
These layouts and code fingerprints were checked against the supported executable;
live music/beat behaviour still needs checking with the game and add-on running.

Track observations use Winamp's classic window/IPC interface. Compatibility with
a particular version or skin needs checking in that setup. An unavailable, hung,
inaccessible or ambiguous player leaves ordinary chat working. Missing, overly
long and file/stream-location titles are omitted. Playback data expires after a
sampling gap. Music context stays in the background; there is no track or
connection indicator in the chat overlay. The optional debugger includes it.

No Winamp audio-analysis plug-in or generated singing voice is currently connected.
A fallback window title is not a verified artist/title split, and
Winamp playback does not establish whether the game add-on is installed or reacting.
The existing conversation, memory and action tools remain available as usual.

Enabling this connection includes playback, recording-time context and native music readings in your
configured chat model's context and may retain them in ordinary chat history or
memories. It reads local MP3 tags, but does not upload song files or playback audio.
Microphone recordings still follow your separate voice-provider settings.

## Settings and debugging

The mod window's **Model, API key & settings** button opens configuration. Settings
go in ignored `settings.json`; keys go in ignored `.local/.env`. A blank key field
keeps the existing key. The **General prompt** tab edits game background, personality
and speaking style as one text, stored in `prompts.general` in `settings.json`.
Tool, engine, observation and memory rules are fixed in code and added separately;
the LLM debugger still shows the complete assembled prompt. Older prompt sections
are migrated on load with a notice; saving writes the new format.
Advanced settings and the general prompt have defaults in
[`settings.default.json`](../src/bwv/settings.default.json). **Save & apply** reloads
the mod services inside the existing launcher; the game can stay open. Manual file
edits are loaded when you reopen settings and save, or restart the mod.
Recent conversation uses up to 12 exchanges and 24,000 characters. Adjust
`history_pairs` and `history_chars` in `settings.json`; larger limits use more
input tokens. Saved memories persist separately from this rolling history.
Reinstalling with `Setup-Mod.cmd` or `Start-Mod.cmd --install` clears the saved
chat history so old tool usage does not confuse the new build. Saved creature
memories are kept. An ordinary mod restart keeps both.

Fixed instructions go before changing memory and observations to help provider
prompt caches. GPT-5.4-mini uses automatic prefix caching, with no extra cache-write
fee; trimming history can still reuse the unchanged prefix. Supported GPT-5.6/6
Responses models use an explicit breakpoint before the changing data. Gemini uses
its automatic cache; Claude's compatibility API does not support caching. No
separate cache objects or prewarming requests are created. The LLM debugger shows
reported cache reads/writes within the input-token totals; missing statistics are
marked unreported. Hits depend on the provider, prefix length and unchanged tool
schemas. See [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)
and [Gemini caching](https://ai.google.dev/gemini-api/docs/generate-content/caching).

Run these from a terminal in the repository:

| Command | Purpose |
| --- | --- |
| `Start-Mod.cmd --debug` | Enable and print localhost observer and LLM debugger URLs. |
| `Start-Mod.cmd --debug --port 8080` | Use a chosen port for both debugger pages. |
| `Start-Mod.cmd --dummy` | Use dummy chat without paid LLM calls. API voice still has its own charges. |
| `Start-Mod.cmd --no-ticks` | Send direct questions only. |
| `Start-Mod.cmd --no-actions` | Disable creature commands for diagnosis. |
| `Start-Mod.cmd --uninstall` | Restore the original DLL; close the game first. |

To remove the mod from the game, close the game and mod, then run
`Uninstall-Mod.cmd` and accept administrator access. A popup confirms completion.
Your saves, settings and creature memory stay intact. Starting the mod again can
reinstall its bridge automatically.

Debugging is off by default. Check **Enable debugging** in the mod window to start
it while playing, optionally choose a localhost port, and use **Open LLM debugger**
or **Open observer**. Uncheck it to stop the web server and clear snapshot history.
These changes preserve chat drafts, pending replies and memory; no game restart or
administrator prompt is needed. `--debug` starts with the checkbox enabled, and
`--port` sets its initial port. The checkbox is for the current mod session.

No HTTP server starts until debugging is enabled. Errors appear in the mod window;
recoverable connection errors retry automatically. Debug pages can contain private
chat and game information, so review anything before sharing it.
Normal play keeps only the latest world snapshot, plus bounded error and event
buffers. With `--debug`, the launcher also retains the last 120 changed snapshots
in memory; these disappear when the mod closes. Creature memory, recent
conversation and token totals still persist locally in either mode.

Control and short-lived events are checked about every quarter-second. Detailed
surroundings and weather are reused for up to a second, with a refresh before each
model turn and when a tool needs newer details. The chat overlay reads a small
display payload, not the full debugger snapshot.

For an unsupported build, run `.venv\Scripts\python.exe -m bwv.cli inspect "<game-folder>" --shareable`.
This reports build compatibility without installation paths or personal settings.
Back up saves before experimenting. Native creature behavior continues between
LLM commands; the mod is still experimental.

The creature can attempt vomiting through `do_action("vomit", "self")`.
For held objects, `use_held_object("discard", null)` tosses the item away without
aiming. Use `put_down` for gentle placement or `throw_at` for a chosen destination.
Vomiting and untargeted discard are wired but still need in-game confirmation.

`use_held_object(action="bring_home", target=null)` carries the currently held
item to the creature's native home and releases it there. Food, toys and other
items share this one call; movement and release are handled by the game.
Bring-home delivery has been tested through both dummy commands and real chat.

`do_action(action="fish", target="god_hand")` catches fish and delivers them to
the hand's ground position when the command starts. It does not follow a moving
hand. Use `self` to catch and eat, `player_temple` to bring fish home, or a storage
or worship-site object ID to supply it. Hand delivery has been tested; the other
delivery destinations still need live checks. Fishing uses the nearest spot
chosen by the game. Drinking uses
`do_action(action="drink", target="self", miracle=null)`; the game finds natural
water and handles approaching it. Move near the desired river, pond or shore
first to drink in that area. Water miracles cannot be drunk.
To fish in a different area, ask the creature to move there first.
Poo uses a target for positioning; it does
not guarantee hitting or fertilizing it. Casting and throwing aim at a target,
but starting or ending an animation does not prove the effect landed.

World summaries cover known objects within 120 engine units; target inspection
reaches 360, and distant search can look farther through the creature's known
objects. Linked village buildings and villagers can include town, tribe, control
and belief information. Examine a town or its storage building to see its full
storage-flag needs; regular updates include the three largest positive demands.
Only categories with actual storage flags are exposed. Internal desires without
flags are omitted. Readings are unsmoothed demand, not exact visible flag heights.
Meeting village needs can help impress the villagers. A belief reference shows
progress toward conversion; the reported owner determines who controls the town. Unknown information
is not treated as neutral ownership. These readings still need testing on later islands.
Belief entries currently cover your player and the town's owner, not a full god roster.

The system prompt separately lists active gods, their game display names and
registered creatures. It refreshes when those identities or alliances change;
moving hand positions and alignment stay in the current observations.
`inspect_object` also accepts a `god_id`. Scripted creatures remain detectable;
an internal owner link does not establish their allegiance in the story.
When the bridge can verify your temple in the creature's known objects, its
stable `player_temple` target ID is included in the system prompt. The creature
can use it to go home without a nearby search. Ownership is checked again when
acting; the ID resolves to the current temple after loading or changing islands.

Claude uses Anthropic's [Chat Completions compatibility API](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk),
not its native Messages API. It supports tools but ignores structured-output and
reasoning-effort settings; replies still undergo the mod's validation. Native
Claude features and keys requiring a workspace header are not supported yet.
Gemini uses Google's [compatible endpoint](https://ai.google.dev/gemini-api/docs/openai)
with a Google AI Studio API key (`GEMINI_API_KEY`). Use your chosen Gemini model ID;
reasoning options depend on the model. Claude and Gemini presets have not been live-tested.

The model uses `find_objects` for nearby or known objects, and `find_targets` for
action-compatible targets. Object descriptions can be brief or detailed.
`inspect_object` accepts a list of `object_ids` and optional detail sections, so it
can compare town needs or creature traits in one call. All physical tools use
`target`; `move_to` also accepts `{x,z}`. Tick context and `inspect_self` share
`self.body`, `self.held_object`, `self.motivations`, `self.learning` and `activity`.

Existing Teleport gates are separate from island vortexes. When a gate is near
the creature or god hand, its connected gates are also available to inspect.
`do_action(action="use_teleport", target=exit_gate_id)` selects the destination
gate and approaches another entrance in the same network. It uses existing gates,
not a new miracle; gates can expire or be unreachable. Ordinary `move_to` only
approaches a gate.
