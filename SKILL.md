---
name: black-and-white-llm-mod
description: Set up Black & White and this LLM Creature Mod on Windows, configure a local or remote model, run and troubleshoot the mod, or contribute changes to this repository. Use for this mod, not general Black & White gameplay advice or openblack development.
---

# Black & White LLM Creature Mod

Help the user get their own game and the mod working, or make a focused contribution.
The mod adds LLM decisions and chat alongside the creature's original AI; it does
not replace the whole AI or provide the game itself.

## Game context

Black & White is a god game set on a series of islands. The player controls a
divine hand that moves objects, casts miracles and helps or frightens villagers.
Villagers' belief expands the god's influence over towns. Good, neutral and evil
play are all part of the game, and the player's alignment can differ from the
creature's.

The creature is a growing animal companion with its own needs and learned habits.
It eats, sleeps, plays, explores and can learn miracles by watching the player.
Stroking and slapping teach it through praise and punishment; the learning, love
and hate leashes also guide its behavior. A partly learned miracle can fail when
cast. Native behavior keeps running between the mod's commands, so observations
can change while the LLM is thinking.

The player speaks to the creature through the mod's chat as the in-game god.
The creature is not always available, and story scripts sometimes take control.
Do not assume every mechanic described here is exposed through the mod's tools.

## Find the right instructions

Work from the repository containing this file and `Start-Mod.cmd`. If this skill
was copied elsewhere, locate the user's checkout first. All paths below are
relative to that checkout. Do not assume a username, drive, game folder, process
ID, debugger URL, provider or model from another session.

- For installation and model setup, read [README.md](README.md).
- For current mod limitations and planned work, read the README's
  [roadmap and known limitations](README.md#roadmap-and-known-limitations).
  Treat it as the maintained overview; do not assume planned features already work.
- For launch flags, provider limitations and uninstalling, read [usage](docs/usage.md).
- For code changes, read [contributing](docs/contributing.md) and the relevant source.

## Set up the game and mod

1. Identify the user's intended installation and whether it already works. Reuse
   it instead of reinstalling, copying it or moving its saves. For a fresh game
   installation, use the user's own lawful media and the patch links in the README:
   1.10, then 1.20, then unofficial 1.42, skipping 1.30. Recommend a writable game
   folder outside Program Files. The mod does not install or license the base game.
2. Check Windows and Python 3.11+ with tkinter. Before setup, replacing a DLL or
   starting anything that requests elevation, have the user save and close the
   game if it is running. Administrator prompts during gameplay can freeze its
   display. An access-denied process query is not proof the game is closed.
3. Run `Setup-Mod.cmd` from the checkout. It creates `.venv`, installs dependencies,
   builds the native bridge, and opens the installation flow. Let the user handle
   Windows consent prompts when the agent cannot. A successful build alone is not
   a successful installation; check the install result. Use `--game` if folder
   selection needs correcting. Preserve the original DLL backup and install manifest.
4. Configure the user's chosen provider through the launcher's settings window or
   `Start-Mod.cmd --configure`. Presets fill connection options; the user supplies their model
   and key. Prefer entering secrets directly in that form, not in the conversation.
   `settings.json` holds settings; `.local/.env` holds keys. `api_key_env` is the
   variable's name, not its secret value. Preserve existing settings and keys.
5. Start `Start-Mod.cmd` before the game, then launch that installation's
   `runblack.exe` as administrator. The launcher finds the process automatically;
   users do not need a PID. Check attachment and Ctrl+Enter chat after loading a
   creature save. Windowed overlay behavior is tested; fullscreen is not verified.

The normal launcher uses configured model calls and enables creature actions and
automatic thinking. Do not silently switch providers or use a key as permission
for unrelated paid tests. For a requested offline smoke test, use `--dummy`; it
replaces model calls but **does not disable actions**. Add `--no-actions` when the
test only needs observation and chat. Report which mode you started.

## Run, inspect and troubleshoot

- Use one launcher. It owns the observer, chat and overlay and reconnects when the
  game restarts. Stop it with **Stop mod**, the window's close button or Ctrl+C.
  Closing the game alone leaves the launcher waiting. Prefer graceful shutdown.
- Add `--debug`, optionally `--port 8080`, to expose both localhost debugger pages.
  Read the current URLs from startup output; never reuse a previous run's token.
  Without `--debug`, no HTTP server should start.
- For an unsupported build, run
  `.venv\Scripts\python.exe -m bwv.cli inspect "<game-folder>" --shareable`.
  It reports build facts without the installation path. Do not bypass compatibility
  checks or assume every unofficial 1.42 executable has the same engine layout.
- Show errors clearly. Let recoverable connection failures retry, but investigate
  repeated failures instead of restarting endlessly. Check attachment, current
  context, offered tools, provider response and action feedback to locate the fault.
- Respect menu, cutscene, temple-interior and no-creature states. Do not remove
  these controls merely to make a test run. Ordinary native activity is not, by
  itself, a reason to wait indefinitely for the creature to do nothing.
- Before a visible game-action test, tell the user what to watch. Use one bounded
  attempt, then inspect the result. Do not repeatedly cast, attack or retrigger an
  action without evidence that the previous attempt failed. Tool selection, native
  plan entry, plan exit and a visible effect are distinct results.
- If saves appear missing, establish which installation/profile is in use and
  whether Windows permits writes. Do not overwrite saves, restore guessed backups
  or redirect the game to a copied installation as a troubleshooting shortcut.

## Contribute

Use existing entry points instead of writing another launcher or ad hoc controller.
The main areas are:

| Area | Files |
| --- | --- |
| Setup, lifecycle and settings UI | `src/bwv/launcher.py`, `launcher_ui.py`, `supervisor.py` |
| Observations and compact context | `src/bwv/qa.py`, `llm_context.py`, sensor modules |
| Providers, conversation and memory | `src/bwv/llm.py`, `conversation.py`, `memory_store.py` |
| Model tools and actions | `src/bwv/direct_tools.py`, `pending_actions.py`, `native/` |
| Chat display | `src/bwv/overlay.py`, `overlay_windows.py` |

Read current tool definitions before adding capabilities; do not invent callable
engine functions or treat old QA notes as current behavior. Update tool descriptions
and prompts when changing the model-facing contract. Keep public defaults generic.
Follow the contribution guide's Python and native checks as relevant. Use synthetic
fixtures for offline tests, and distinguish them from live game verification.
Close the game before installing a changed bridge. Do not commit or push unless
the user requested it.

## Keep private data private

Do not publish `.local/`, `settings.json`, `.env` files, credentials, profile or
machine details, memory/chat history, debug URL tokens, captures, saves or game
binaries. Do not dump environment variables or private settings while diagnosing
configuration. Review staged filenames and content before any requested commit;
Git ignore rules alone are not a privacy audit. Use invented names and data in tests.

Share only authored code, suitable interface facts and synthetic fixtures. Do not
copy game or GPL implementation into MIT source; preserve required notices for
any permitted third-party material. Private research archives are not required to
use this skill. Leave the user with the actual setup/test result, how to start and
stop the mod, and any unresolved limitation, without exposing secrets.
