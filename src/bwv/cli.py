from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .compat import inspect_install, shareable_report
from .pipe import snapshot
from .llm import DEFAULT_SETTINGS_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Black & White AI bridge development tools")
    sub = parser.add_subparsers(dest="command", required=True)
    settings = sub.add_parser("init-settings", help="Create private shared settings and editable core prompts; no API calls")
    settings.add_argument("--config", type=Path, default=DEFAULT_SETTINGS_PATH)
    check = sub.add_parser("inspect", help="Read installation fingerprints without changing anything")
    check.add_argument("game", type=Path)
    check.add_argument('--shareable',action='store_true',help='Only game-build compatibility facts; omit installation paths and local state')
    observe = sub.add_parser("observe", help="Read a snapshot from a running observer bridge")
    observe.add_argument("pid", type=int)
    status = sub.add_parser("status", help="Read AI permission status without making model calls")
    status.add_argument("pid", type=int)
    sub.add_parser("smoke", help="Run deterministic offline scenarios; never controls the game")
    qa = sub.add_parser("qa", help="Serve a local live context and prompt inspector; no model calls")
    qa.add_argument("pid", type=int)
    qa.add_argument("--port", type=int, default=0, help="Loopback port; default chooses a free port")
    qa.add_argument("--seconds", type=int, default=43200, help="Stop after this many seconds (maximum 12 hours)")
    qa.add_argument("--memory-file", type=Path, help="Persistent memory file; defaults to the local application data folder")
    demo = sub.add_parser("qa-demo", help="Synthetic offline chat QA with a deterministic dummy; no game access")
    demo.add_argument("--port", type=int, default=0)
    demo.add_argument("--seconds", type=int, default=43200)
    demo.add_argument("--state-dir", type=Path, help="Separate demo memory directory")
    live_dummy = sub.add_parser("qa-live-dummy", help="Chat with a deterministic dummy; engine actions disabled unless explicitly opted in")
    live_dummy.add_argument("source_url", help="Tokenized loopback URL of the running read-only inspector")
    live_dummy.add_argument("--port", type=int, default=0)
    live_dummy.add_argument("--seconds", type=int, default=43200)
    live_dummy.add_argument("--state-dir", type=Path, help="Separate persistent dummy memory directory")
    live_dummy.add_argument("--config", type=Path, help="Private overrides; otherwise use bundled defaults plus optional settings.json")
    live_dummy.add_argument("--experimental-action-pid", type=int, help="Opt in to one request-bound dummy action in this game process; requires the v2 action-probe build")
    chat = sub.add_parser("chat", help="Connect a configured LLM; engine actions require explicit opt-in")
    chat.add_argument("source_url", help="Tokenized loopback URL of the running read-only inspector")
    chat.add_argument("--config", type=Path, help="Private overrides; otherwise use bundled defaults plus optional settings.json; keys come from environment")
    chat.add_argument("--port", type=int, default=0)
    chat.add_argument("--seconds", type=int, default=43200)
    chat.add_argument("--state-dir", type=Path, help="Separate persistent LLM memory directory")
    chat.add_argument("--experimental-action-pid", type=int,
        help="Opt in to guarded gesture/hand-approach tools for direct questions in this game process; requires an action-probe bridge")
    for server_command in (qa, live_dummy, chat):
        server_command.add_argument("--debug", action="store_true",
            help="Enable the browser debugger page; the local API stays available without it")
    overlay = sub.add_parser("overlay", help="Show creature chat over the windowed game; no engine writes")
    overlay.add_argument("chat_url", help="Tokenized loopback URL of the live creature chat")
    overlay.add_argument("--seconds", type=int, default=43200)
    overlay.add_argument("--diagnostics-file", type=Path, help="Optional local overlay diagnostics, without chat text")
    overlay.add_argument("--stop-on-stdin", action="store_true", help="Launcher-owned overlay: close when its parent pipe closes")
    overlay.add_argument("--settings", type=Path, help="Private settings used for optional voice input")
    overlay.add_argument("--vr", action="store_true", help="Experimental game subtitles and voice input for BW1-VR")
    watch = sub.add_parser("watch", help="Read live observations through the mock planner; no game actions")
    watch.add_argument("pid", type=int)
    watch.add_argument("--seconds", type=float, default=5)
    watch.add_argument("--training", action="store_true", help="Include read-only live training context")
    watch.add_argument("--control", action="store_true", help="Include individually verified read-only control facts")
    watch.add_argument("--leash", action="store_true", help="Include read-only selected-creature leash context")
    watch.add_argument("--miracles", action="store_true", help="Include read-only engine-registered miracle learning")
    watch.add_argument("--holding", action="store_true", help="Include the creature's held object and active plan")
    watch.add_argument("--experience", action="store_true", help="Include experimental body readings, health changes and native plan context")
    training = sub.add_parser("training-watch", help="Watch read-only creature training context; no model calls")
    training.add_argument("pid", type=int)
    training.add_argument("--seconds", type=float, default=15)
    conditions = sub.add_parser("condition-probe", help="Read experimental creature motivation/fire sensors; no model calls or game actions")
    conditions.add_argument("pid", type=int)
    conditions.add_argument("--seconds", type=float, default=5)
    action_probe = sub.add_parser("action-test", help="Preview or run one explicitly selected QA action with an experimental bridge")
    action_probe.add_argument("pid", type=int)
    action_probe.add_argument("--execute", action="store_true", help="Explicitly submit the offered one-shot QA action to the game")
    action_probe.add_argument("--seconds", type=int, default=15)
    action_probe.add_argument("--action", choices=("look_confused", "sleep_here", "approach_hand", "wake_up", "approach_object", "wave", "sneeze", "shiver", "scratch", "show_hotness", "dance", "put_down", "eat_held"), default="look_confused",
                              help="Explicit QA action; sleep_here requests native sleep without editing fatigue")
    add = sub.add_parser("install-observer", help="Install experimental observer after backing up profiles")
    add.add_argument("game", type=Path)
    add.add_argument("bridge", type=Path)
    add.add_argument("--experimental", action="store_true")
    remove = sub.add_parser("uninstall", help="Restore the verified original scripting DLL")
    remove.add_argument("game", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init-settings":
            from .llm import init_settings
            result = init_settings(args.config)
        elif args.command == "action-test":
            from .action_probe import run
            result = run(args.pid,execute=args.execute,seconds=args.seconds,action=args.action)
        elif args.command == "chat":
            from .qa_live import run_llm
            result = run_llm(args.source_url,args.config,args.port,args.seconds,state_dir=args.state_dir,
                             action_pid=args.experimental_action_pid,debug=args.debug)
        elif args.command == "overlay":
            from .overlay import run
            result = run(args.chat_url,seconds=args.seconds,diagnostics_file=args.diagnostics_file,
                         stop_on_stdin=args.stop_on_stdin,settings_path=args.settings,vr=args.vr)
        elif args.command == "qa-live-dummy":
            from .qa_live import run
            result = run(args.source_url,args.port,args.seconds,state_dir=args.state_dir,config_path=args.config,
                         action_pid=args.experimental_action_pid,debug=args.debug)
        elif args.command == "qa-demo":
            from .qa_demo import run
            result = run(args.port, args.seconds, state_dir=args.state_dir)
        elif args.command == "qa":
            from .qa import run
            result = run(args.pid, args.port, args.seconds, memory_path=args.memory_file,debug=args.debug)
        elif args.command == "training-watch":
            from .training_watch import run
            result = run(args.pid, args.seconds)
        elif args.command == "condition-probe":
            from .conditions_live import run
            result = run(args.pid, args.seconds)
        elif args.command == "watch":
            import asyncio
            from .watch import run
            result = asyncio.run(run(args.pid, args.seconds, training=args.training,
                control=args.control, leash=args.leash, miracles=args.miracles, holding=args.holding,
                experience=args.experience))
        elif args.command == "smoke":
            import asyncio
            from .smoke import run
            result = asyncio.run(run())
        elif args.command == "inspect":
            result = inspect_install(args.game)
            if args.shareable:result=shareable_report(result)
        elif args.command == "observe":
            result = snapshot(args.pid)
        elif args.command == "status":
            from .control import ControlGate, from_observer
            observation = snapshot(args.pid)
            now = time.monotonic()
            gate = ControlGate().update(from_observer(observation, now), now)
            result = {"inference_allowed": gate.allowed, "gameplay_writes": False,
                      "reasons": gate.reasons, "session": observation.get("session"),
                      "ticks": observation.get("ticks"), "source_age_ms": observation.get("source_age_ms"),
                      "provider_calls": 0}
        elif args.command == "install-observer":
            from .install import install
            result = install(args.game, args.bridge, experimental=args.experimental)
            from .memory_store import clear_installed_history
            result.update(clear_installed_history())
            if not result['history_cleared']:
                raise RuntimeError('The bridge is installed, but some saved chat history could not be cleared. '
                                   'Those files were preserved. Close the mod and retry Start-Mod.cmd --install.')
        else:
            from .install import uninstall
            uninstall(args.game)
            result = {"restored": True}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"bwv: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
