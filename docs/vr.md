# Experimental BW1-VR compatibility

This mode works alongside the official [BW1-VR](https://github.com/BW-VR/BW1-VR)
release. It changes only the LLM Creature Mod. No BW1-VR fork, controller patch,
OpenXR injection or new controller binding is required.

Install BW1-VR alongside this mod in your **1.42** Black & White installation.
Both mods use the same game folder; a separate game copy is not required.
Verify that the BW1-VR launcher can start the game.
The compatibility target tested here is BW1-VR `v1.2.0-20261020-fd7f198`.

## Install and start

Use **Run B&W VR**, the third launch button in our start window. It applies VR
support to our mod and opens the official launcher. Choose **Play in VR** there.
All launch buttons use the selected game installation.
Before launching, they also set the game's registry `GameDir` to that folder.
The game caches this save root separately from its working directory; an old
path after moving or copying an installation can prevent creature data from
loading. The selected path remains after a successful launch, including game
exit, and is restored if starting the process fails. Save files are not moved
or replaced.

VR controls are disabled when the selected folder lacks `runblack.exe`,
`Black & White VR Mod.exe`, `ddraw.dll`, or `openxr_loader.dll`. **Settings > VR**
names missing files. After installing BW1-VR, use **Check again**
in settings; the start window also rechecks automatically. This detects the
installation files, not a connected headset or a working OpenXR runtime.
Use the **Voice** tab to enable and configure voice input. Enabling VR mode
alone does not enable the microphone.
**Push to talk** is on by default and remains editable in VR. Keep it on to use
the configured keyboard shortcut, or turn it off for voice activation.
The VR tab shows a reminder when voice is disabled or a speech model is missing.
Starting VR does not change voice input or transcript sending preferences.

From this repository, with every game instance closed:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[launcher]"
.venv\Scripts\python.exe tools\build_native.py --action-probe --repeat-actions --object-movement --action-catalog --held-actions
.\Start-Mod.cmd --install --game "C:\Games\BlackAndWhite"
.\Start-Mod.cmd --vr --game "C:\Games\BlackAndWhite"
```

Replace the example path with your game installation. `--vr` and `--no-vr` override the saved
mode at startup; the launch buttons select the mode afterward.
The normal windowed and borderless buttons also work with BW1-VR installed.
They temporarily select `vr=0` and the bundled dxwrapper desktop backend,
then launch the same game executable. Previous values are restored when the
game exits, or before the next launch if our mod was closed first. Other BW1-VR
options are preserved. A recovery record stays beside the game until restoration.
Load a save with your creature. Keep the game mirror window focused.
When our mod is in VR mode, native subtitles also work with **Play without VR**.
In native fullscreen the desktop chat overlay stays hidden; voice and native
subtitles remain active. Normal desktop mode still requires windowed or borderless
fullscreen for its overlay.

Configure a microphone and transcription model in **Voice** and enable voice.
With **Push to talk** off, speak normally, then pause for about **1.2 seconds**
to transcribe. Enable **Send transcript immediately** for hands-free sending;
otherwise the transcript waits for review in the desktop chat.
Use immediate sending in native fullscreen, where the desktop review controls
are not visible.
Voice activation requires no keyboard key or controller button.
Voice stays off if disabled or its model is blank.
An empty transcription resumes listening after one second. Microphone and provider failures still
require fixing the problem and using **Save & apply**.

Speech detection runs locally using
[webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels).
Automatic detection also requires sustained microphone energy above -55 dBFS
to reject very quiet background noise. If quiet speech does not trigger it,
increase the microphone input level or use push to talk, which has no such gate.
While listening, idle silence is kept only as a short in-memory pre-roll and
never submitted for transcription. Complete phrases use your configured local
or API transcription provider. API voice can incur charges even with `--dummy`.
Use headphones: detection cannot distinguish your voice from nearby speakers,
another person, or the game's spoken dialogue. It is not wake-word detection.

Recording stops when game focus or the current creature context is lost. A
microphone/transcription error stops activation until **Save & apply** restarts
voice. Disable voice and apply settings, or **Stop mod**, to stop listening.
There is no headset tracking or microphone-mute detection in this integration.

## Replies and limitations

Replies go through the game's own help-text renderer, which BW1-VR renders in
its VR interface. Each page starts with the configured creature name (or
`Creature` when unnamed), for example `Bongo: Hello.` The bridge colours the name
warm gold and restores the game's default colour for the reply. The desktop
chat remains available too. Long replies are
split into short pages, each displayed for up to ten seconds. A newer reply
replaces pending pages. Scene changes and stale creature identities discard
pending text. Incoming text is restricted to plain BMP characters; unsupported
emoji and engine commands are removed. Only the bridge's fixed name formatter
adds the game's verified colour commands.

Completed transcriptions appear once as `[Heard: ...]`; voice errors also get
one short notice. These wait until the current subtitle finishes and remain for
four seconds. Long transcriptions are shortened only for display; the full text
is still sent to chat. Repeated identical notices are suppressed for 30 seconds,
and notices delayed more than 30 seconds are dropped. There are no live listening,
transcribing, or sent-status subtitles, and no periodic subtitle refresh: the
game clears and redraws text when it changes, which causes visible blinking.
Fix microphone/provider errors and use **Save & apply** to restart voice input.
An updated bridge is required for notices; older bridges can still show replies.

Story dialogue and existing native help take priority. Captions may wait or be
skipped while that interface is occupied. This experiment does not add a VR
keyboard, speech synthesis, controller bindings, or a separate microphone panel.
Voice input is the intended headset interaction. Notices are delayed or
hidden while the game's dialogue owns the subtitles; they are not a mute indicator.

## Headset-free checks and headset review

Local validation: 1,201 Python tests passed, along with the native synthetic
suite. A bounded saved-game stereo run advanced with both mods loaded; two
captions were accepted and expired, text appeared in both captured eyes, and
the existing thinking-pause begin/end commands remained accepted. Voice
activation passed synthetic audio and digital silence tests. A subsequent live
desktop-fullscreen run recognized English speech, sent it as a model question,
and delivered the answer through native subtitles with the desktop overlay hidden.
The broader upstream
new-game verification did not complete all scenarios; this is a smoke test,
not a full BW1-VR regression result.

The upstream desktop stereo harness can exercise the official VR renderer with
fake head/controller poses. It can establish game/bridge coexistence, advancing
simulation, native packet acceptance, subtitle drawing in captured eye images,
and expiry. It cannot establish headset comfort, subtitle depth/readability,
OpenXR focus behavior, microphone quality, or controller usability.

For a first test, use `--vr --dummy --no-actions --no-ticks` and **disable voice**
to avoid microphone capture and provider calls. Desktop Ctrl+Enter can test
replies even without a headset. Then test voice deliberately with a local speech
model or your configured API limits. `--dummy` replaces only the chat model.

For a headset tester's PR review:

- Record the game patch, BW1-VR version, headset, runtime and controller profile.
- Confirm ordinary BW1-VR play works before starting our mod.
- Check one short reply, a multi-page reply, and a later reply after the first
  expires. Verify readability in both eyes and the subtitle/card position.
- Enable voice, speak a short request, and pause. Check that it sends once,
  without touching a keyboard, and that ordinary silence sends nothing.
- Check focus loss, menus, a cutscene, creature/save changes, and stopping the
  mod. Confirm that recording cancels and old text is not delivered afterward.
- After chat-only checks, enable actions and test one harmless command. Report
  the visible effect separately from an accepted command.

Do not describe headset compatibility as verified until someone has completed
those checks on real hardware. Do not publish saves, captures of private chat,
settings, credentials, or game binaries with the PR.
