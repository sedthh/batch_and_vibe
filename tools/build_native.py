"""Build an x86 VM observer with Zig, using export names from the user's DLL.

Only metadata is generated. The original DLL is neither linked nor packaged.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bwv.compat import inspect_install  # noqa: E402

# POP returns raw words in EAX, including script-float bits. Keep operand exports
# as direct PE forwarders; a C++ float return uses a different register.
WRAPPED = {"Version", "Initialise", "LookIn", "LoadBinary", "RestoreState", "UnInitialise", "Reboot"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zig", type=Path, help="Zig executable; otherwise use the installed ziglang Python package")
    parser.add_argument("--game", type=Path, help="Optionally verify the public export contract against a local installation")
    parser.add_argument("--action-probe", action="store_true", help="Build the separate manual one-shot native-agenda experiment")
    parser.add_argument("--repeat-actions", action="store_true", help="Opt-in action-probe QA with verified endings and a 10-second cooldown")
    parser.add_argument("--object-movement", action="store_true", help="Separate manual nearby-tree/rock approach experiment; not a model capability")
    parser.add_argument("--action-catalog", action="store_true", help="Expanded named self-actions and plan-independent standing/walking interruption")
    parser.add_argument("--held-actions", action="store_true", help="Request-bound pickup, put-down, targeted throw and food-pile eating commands")
    parser.add_argument("--qa-unlearned-megablast", action="store_true", help="Separate dummy-QA build: one zero-knowledge Mega Blast Extreme attempt per process")
    args = parser.parse_args()
    if args.repeat_actions and not args.action_probe:
        parser.error("--repeat-actions requires --action-probe")
    if args.object_movement and not (args.action_probe and args.repeat_actions):
        parser.error("--object-movement requires --action-probe and --repeat-actions")
    if args.action_catalog and not (args.action_probe and args.repeat_actions):
        parser.error("--action-catalog requires --action-probe and --repeat-actions")
    if args.held_actions and not (args.action_catalog and args.object_movement):
        parser.error("--held-actions requires --action-catalog and --object-movement")
    if args.qa_unlearned_megablast and not args.held_actions:
        parser.error("--qa-unlearned-megablast requires --held-actions")
    names = (ROOT / "native/exports.txt").read_text("ascii").splitlines()
    if args.game:
        report = inspect_install(args.game)
        if report["status"] != "experimental" or not report["abi_candidate"] or report["vm_exports"] != names:
            raise SystemExit("Unrecognized game build or interface. Inspect and review before installing.")
    build = ROOT / "build" / ("native-held-actions" if args.held_actions else "native-action-catalog" if args.action_catalog else "native-object-movement" if args.object_movement else "native-action-repeat" if args.repeat_actions else "native-action-probe" if args.action_probe else "native")
    if args.qa_unlearned_megablast:
        build = ROOT / "build" / "native-qa-unlearned-megablast"
    build.mkdir(parents=True, exist_ok=True)
    exports = ["LIBRARY ScriptLibraryR", "EXPORTS"]
    for name in names:
        # A PE can contain arbitrary names. Never insert unvalidated input into a
        # linker definition file; these characters cover this game's ABI names.
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_?@$" for c in name):
            raise SystemExit("Unexpected export name")
        # Zig 0.14.1's i386 GNU linker prefixes forwarder modules with '_'. The
        # installed original filename is deliberately _bwv_original_vm.dll.
        destination = f"bwv_{name}" if name in WRAPPED else f"bwv_original_vm.{name}"
        exports.append(f'  "{name}"="{destination}"')
    definition = build / "observer.def"
    definition.write_text("\n".join(exports) + "\n", encoding="ascii")
    env = dict(os.environ)
    env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "build" / "zig-cache")
    options = ["-DBWV_ACTION_PROBE"] if args.action_probe else []
    if args.repeat_actions: options.append("-DBWV_REPEAT_ACTIONS")
    if args.object_movement: options.append("-DBWV_OBJECT_MOVEMENT")
    if args.action_catalog: options.append("-DBWV_ACTION_CATALOG")
    if args.held_actions: options.append("-DBWV_HELD_ACTIONS")
    if args.qa_unlearned_megablast: options.append("-DBWV_QA_UNLEARNED_MEGABLAST")
    compiler = [str(args.zig.resolve())] if args.zig else [sys.executable, '-m', 'ziglang']
    subprocess.run([*compiler, "c++", "-target", "x86-windows-gnu", "-std=c++17", "-O2", *options,
        "-shared", "-static",
        str(ROOT / "native" / "observer.cpp"), str(definition), "-ladvapi32",
        "-o", str(build / "ScriptLibraryR.dll")], env=env, check=True)
    print(build / "ScriptLibraryR.dll")


if __name__ == "__main__":
    main()
