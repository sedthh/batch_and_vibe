# Using Black & White LLM Creature Mod

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

Commands can include fishing at a known fishing area. If an action needs empty
hands, the mod first asks the creature to put its held object down. If a target
vanishes before execution, it can try another of the same kind up to twice.
Uncertain outcomes are not repeated automatically, to avoid duplicate actions.
Nearby context distinguishes recognized building types and fences. Inspection and
search can use kinds such as `house`, `storage`, `nursery`, `cemetery`, `temple` or
`fence`. These are known objects, not a complete map of everything on screen.
Each model turn includes a compact UTC observation time and tick or message receipt
time, separate from the island's time of day. Creature and god-hand positions use
horizontal X/Z coordinates. Nearby buildings and other creatures can also show
coordinates in inspection; portable objects keep their IDs. Use object actions
for objects and `move_to(destination={"x":X,"z":Z})` for a ground location. Coordinates belong
to the current island; they are not a permanent destination across saved games.
The same `move_to` tool accepts an object ID, a movement target ID, or the special
destinations `god_hand` and `player_temple`. It accepts `pace: auto`, `walk` or `run`. Approaching the god hand
defaults to running and follows its live position; ask it to walk if you prefer.
Other movement uses the engine's pace unless the model selects one. The choice
applies to that movement, not to later creature behaviour.
The model can make up to six tool calls per turn by default, but select only one
physical action. Inspection has pages of ten objects; broader object searches
have pages of twenty. `find_targets` looks for objects suitable for an action.
`do_action(action="eat")` handles feeding from stores, fields, trees and food piles.
`use_held_object` handles putting down, eating, throwing or casting a held bubble.
Search synonyms keep the object's canonical name and ID in the results.
The creature receives a rough time-of-day phase from the engine's sky clock and
available local rain/snow readings. Native intentions retain their
recognized targets between turns, but a planned kick is not proof of a hit.

The mod attaches automatically and reconnects after game restarts. Creature actions
and automatic thinking are enabled by default; paid providers may charge for calls.
Use **Stop mod** or close the mod window to stop it without closing the game.

The launcher has **Run B&W windowed** with a size dropdown, and **Run B&W borderless
fullscreen** using the monitor containing the launcher. Borderless is a windowed
game with its frame removed, not native exclusive fullscreen; it still needs live
game testing on your setup. Launching from the elevated mod inherits administrator
access. Close the game before changing modes. These buttons save only the game's
windowed/fullscreen flag and display width/height; other settings and saves are untouched.
New model calls, including tool follow-ups, pause in unsupported fullscreen mode,
when the game window is minimized/unavailable, or if the overlay process exits.
Already-sent requests cannot be un-sent. Windowed mode resumes with fresh observations.

## Settings and debugging

The mod window's **Model, API key & settings** button opens configuration. Settings
go in ignored `settings.json`; keys go in ignored `.local/.env`. A blank key field
keeps the existing key. The **General prompt** tab edits game background, personality
and speaking style as one text, stored in `prompts.general` in `settings.json`.
Tool, engine, observation and memory rules are fixed in code and added separately;
the LLM debugger still shows the complete assembled prompt. Older prompt sections
are migrated on load with a notice; saving writes the new format.
Advanced settings and the general prompt have defaults in
[`settings.default.json`](../src/bwv/settings.default.json). Restart the mod after
changes, closing the game first to avoid administrator prompts during gameplay.
Recent conversation uses up to 12 exchanges and 24,000 characters. Adjust
`history_pairs` and `history_chars` in `settings.json`; larger limits use more
input tokens. Saved memories persist separately from this rolling history.

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
| `Start-Mod.cmd --dummy` | Use a dummy model without paid API calls. |
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

Fishing uses the nearest spot chosen by the game. To fish in a different area,
ask the creature to move there first. Poo uses a target for positioning; it does
not guarantee hitting or fertilizing it. Casting and throwing aim at a target,
but starting or ending an animation does not prove the effect landed.

World summaries cover known objects within 120 engine units; target inspection
reaches 360, and distant search can look farther through the creature's known
objects. Linked village buildings and villagers can include town, tribe, control
and belief information. A belief reference shows progress toward conversion;
the reported owner determines who actually controls the town. Unknown information
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
