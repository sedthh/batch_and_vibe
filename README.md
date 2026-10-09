# Black & White LLM Creature Mod

A fan mod created with GPT-6 Astra that adds generative AI to your creature in
Lionhead's **Black & White**. Talk to your creature, give it instructions, and let
an LLM think about its needs and surroundings. It builds on the original creature
AI, which keeps running and learning between LLM commands.

This is an experimental, unofficial project, not affiliated with or endorsed by
Lionhead Studios or the game's rights holders. **You need your own original game.**
This repository contains no game code, assets, saves or patches.

New to the game? Watch [Black & White: Playing God With a Giant Cow](https://www.youtube.com/watch?v=qelivOEUZfY).
For the story behind the game and why it's hard to find today, see Noclip's
[Why has Black & White Been Abandoned?](https://www.youtube.com/watch?v=GtNvEna6bxc).

![The monkey chatting with its god near the village](docs/assets/creature-chat.png)

![Chatting while the monkey meets another creature](docs/assets/creature-chat-with-ally.png)

## Getting started

Want an agent to help? Ask it: "Read [SKILL.md](SKILL.md) and help me set up
Black & White and this mod." The same guide covers troubleshooting and contributing.

### Prepare the game

For a Windows 11 setup in 2026:

1. Install your copy outside protected folders such as Program Files, for example
   `D:\Games\Black And White`, to reduce save-file permission problems.
2. Apply [patch 1.10](https://www.bwgame.net/downloads/v1-1-patch.129/), then
   [patch 1.20](https://www.bwgame.net/downloads/v1-2-patch.130/), then the
   [unofficial patch 1.42](https://www.bwgame.net/downloads/black-white-unofficial-patch-v1-42.1418/).
   Follow the patch author's instructions and **skip 1.30**. The patch also lets
   you skip the tutorials on a fresh installation.
3. Launch `runblack.exe` as administrator and confirm the game runs and saves
   correctly. **Exclusive fullscreen is not supported:** creature chat cannot
   reliably appear there, so new LLM calls pause. Use windowed mode or the mod's
   **Run B&W borderless fullscreen** button, which fills the screen while keeping
   creature chat working.
   Back up your saves before experimenting,
   then close the game before installing the mod.

### Set up the mod and model

You need **Python 3.11+ with tkinter** and either a local LLM server or an API key
for a supported provider. Use a model that's good at following instructions,
calling tools and returning JSON. Otherwise, it may talk about doing something
without actually asking the game to do it.

1. Download or clone this repository into a writable folder.
2. With the game closed, run `Setup-Mod.cmd`. It installs dependencies, builds
   the bridge, and asks for administrator access and your game folder.
3. Run `Start-Mod.cmd` **before launching the game**. On first use, it opens model
   settings; you can also use **Model, API key & settings** in the mod window.
   Pick an OpenAI, Claude, Gemini, or custom/local preset, enter your model ID and
   API key, and save. You can edit the preset values. If you change settings while
   the mod is running, stop and restart it. Accept the administrator prompt, then
   use **Run B&W windowed** with a size from the dropdown, or **Run B&W borderless
   fullscreen**, then Continue or load a save. Both inherit the mod's administrator
   access. You can still launch `runblack.exe` yourself. Administrator
   prompts during gameplay can freeze the display. Claude and Gemini use compatible
   APIs and haven't been live-tested here; see [provider notes](docs/usage.md).
4. Press **Ctrl+Enter** to chat. Up/Down recalls recent messages. Try "Wave hello"
   or "Cast a water miracle on a nearby tree" if your creature knows the water miracle.

The mod finds the running game automatically. Automatic thinking and creature
actions are enabled by default. Close the mod window or choose **Stop mod** to
stop it cleanly. If you only close the game, the mod waits for you to open it again.

You can control LLM usage in **Model, API key & settings → Limits**. Set a session
message cap, output-token limit per response, and limits on tool calls and rounds.
The mod automatically stops new model messages when your session cap is reached;
automatic thinking counts toward it too. The session cap is optional and unlimited
by default. Restarting the mod starts a new session budget.

### Manual configuration

The settings window creates `settings.json` in the mod's main folder. You can also
copy [the default settings](src/bwv/settings.default.json) there if the file does
not already exist, then edit it. For example, these connection fields select OpenAI:

```json
{
  "model": "YOUR_MODEL_ID",
  "api": "responses",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY"
}
```

Put the actual key in `.local/.env`, not in `settings.json`:

```dotenv
OPENAI_API_KEY=YOUR_API_KEY
```

For another provider, use its URL, protocol, model and key variable. Local servers
usually use `chat_completions`; set `api_key_env` to `null` if your local server
doesn't need a key. Settings and `.local/` are ignored by Git. Restart the mod after
changes, closing the game first. The settings window also lets you adjust how often
the creature thinks and set message/token limits. A user message or automatic tick
counts once, including its tool rounds. The message cap is optional: leave it blank
in **Limits**, or omit `max_messages` / set it to `null` in JSON, for no cap.

Remote providers receive the creature's observations, names, conversation and
memory; API calls may cost money. Use a local model if you want that data to stay
on your machine.

### Debugging

```bat
Start-Mod.cmd --debug --port 8080
Start-Mod.cmd --dummy --debug --port 8080
```

`--debug` prints the localhost URLs for the observer and LLM pages. They show full
observations, compact model context, memory, errors and token usage. You can also
switch **Enable debugging** on or off in the mod window while playing, choose a
port and open either page there. Debugging starts off unless you pass `--debug`;
no HTTP server runs while it is off. Switching it does not restart chat or the game.
`--dummy` uses a test model and makes no paid model calls;
it does not disable game actions. Add `--no-actions` for observation/chat-only tests.
Review debug output before sharing it: it can contain private chat and game data.

See [usage](docs/usage.md) for more options, troubleshooting and uninstallation.

## How it works

The installer adds a DLL bridge where the game loads its scripting library. It
keeps the original library and forwards its calls, so the game's scripts still run.
A local Python service reads game information, makes a short summary for the model,
and handles chat, memory and tools.

```mermaid
flowchart LR
    Game["Black & White"] <--> Bridge["Native bridge"]
    Bridge <-->|Game state and actions| Service["Python service<br/>World and creature summaries<br/>Chat history and memory<br/>Tools: inspect, find targets, act"]
    Service <-->|Context, replies and tool calls| Model["Local or remote LLM"]
    Chat["Ctrl+Enter chat"] <--> Service
```

The service collects readings from the world and combines them into simple,
consistent descriptions of events and objects. Summaries go into the creature's
prompt alongside recent chat and saved memories. Tools expose more detail and
ways to interact, so the creature can learn more about its surroundings, find a
target, remember something or choose an action. Python checks those requests and
sends commands through the bridge for the game to carry out. Replies appear in
the chat overlay. Special emoji cues trigger body gestures and are removed from
the visible text.

The time between automatic observations changes with nearby activity and player
distance, with a little randomness. Chat messages don't wait for the next tick.
The mod pauses activity during menus, temple visits and cutscenes where needed.
Starting an action doesn't guarantee it worked; the mod also tracks game feedback.

## Roadmap and known limitations

Want to help build or test it? See [contributing](docs/contributing.md).

- [ ] Separate AI memory and conversation by game profile and save. They currently
  survive restarts but are shared between saves/profiles for the same local user.
- [ ] Give the creature summaries of relevant story events. Cutscene interference
  is intentionally blocked, but the creature gets no dialogue or story summary afterward.
- [ ] Connect the LLM to the creature's built-in learning system. It can't directly
  change learned behavior yet, so you'll still need to praise and punish your creature.
- [ ] Make the creature aware of its tattoos, bruises and other appearance details.
- [ ] Finish and verify emotes. Some do not yet produce a visible animation, and
  availability can vary by creature species.
- [ ] Add voice input through speech-to-text and spoken creature replies.
- [ ] Test later islands, multiplayer, and more game builds.
  Multiplayer is untested; Creature Isle is unsupported. This bridge does
  not work with openblack.
- [ ] Expose hidden or unused game content, such as creatures getting high from
  mushrooms without an in-game status display.
- [ ] Add Roko's Basilisk as a selectable creature.
- [ ] Give an Xbox 360 Kinect some generative AI and finally bring Milo to life.

The [MIT license](LICENSE) covers the original mod code, not Black & White.
