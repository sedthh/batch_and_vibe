"""Compile and run a synthetic x86 bridge integration test on Windows."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bwv.pipe import snapshot
from bwv.pe import PE


def verify_operand_forwarders(path: Path) -> None:
    """POP returns a raw word in EAX, including float bits; keep direct forwarding."""
    pe = PE.read(path)
    start = pe.directory_rva(0)
    size = pe.u32(pe.directory + 4)
    table = pe.rva(start, 40)
    count = pe.u32(table + 24)
    names = pe.rva(pe.u32(table + 32), count * 4)
    ordinals = pe.rva(pe.u32(table + 36), count * 2)
    functions = pe.u32(table + 28)
    verified = set()
    for i in range(count):
        name = pe.string(pe.u32(names + i * 4))
        if name not in {"POP", "PUSH"}:
            continue
        target = pe.u32(pe.rva(functions + pe.u16(ordinals + i * 2) * 4, 4))
        assert start <= target < start + size, f"{name} must be a direct PE forwarder"
        assert pe.string(target) == f"_bwv_original_vm.{name}", name
        verified.add(name)
    assert verified == {"POP", "PUSH"}, verified


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zig", required=True, type=Path)
    parser.add_argument("--without-task-number", action="store_true", help="Verify optional export fallback")
    parser.add_argument("--bridge", type=Path, help="Test a separate experimental bridge build")
    args = parser.parse_args()
    bridge = args.bridge if args.bridge else ROOT / "build/native/ScriptLibraryR.dll"
    verify_operand_forwarders(bridge)
    lab = ROOT / "build" / "synthetic"
    lab.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "build" / "zig-cache"))
    compiler = [str(args.zig), "c++", "-target", "x86-windows-gnu", "-O2", "-UNDEBUG", "-static"]
    # Cross-language agreement uses invented PEs only, never local game copies.
    sys.path.insert(0,str(ROOT / 'tests'))
    from test_image_signature import cases
    from bwv.image_signature import image_signature
    from bwv.pe import PEError
    signature_test=lab/'pe_signature.exe'
    subprocess.run(compiler+['-std=c++17',str(ROOT/'tests/native/pe_signature.cpp'),
                            '-ladvapi32','-o',str(signature_test)],check=True,env=env)
    for name,data,_ in cases():
        fixture=lab/(name+'.fixture');fixture.write_bytes(data)
        try:expected=image_signature(PE(bytes(data)))['layout_sha256']
        except (PEError,UnicodeError):expected='invalid'
        actual=subprocess.check_output([str(signature_test),str(fixture)],text=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
        assert actual==expected,(name,actual,expected)
    for name in ("vr_caption", "action_probe_policy", "command_priority", "thinking_pause", "teleport_travel", "fishing_delivery", "mushroom_effect", "catalog_refresh", "reader_activity", "type_name_cache", "object_type_view", "object_hierarchy", "activity_guard", "movement_guard", "movement_pace", "object_movement_guard", "action_catalog", "held_action_guard", "held_miracle", "throw_targets", "target_pool", "miracle_actions", "world_actions", "individual_gesture", "pickup_completion", "pickup_eligibility", "snapshot_payload"):
        executable = lab / f"{name}.exe"
        subprocess.run(compiler + ["-std=c++17", str(ROOT / f"tests/native/{name}.cpp"),
                                  "-o", str(executable)], check=True, env=env)
        result=subprocess.run([str(executable)], capture_output=True,text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            print(result.stdout+result.stderr,file=sys.stderr)
            result.check_returncode()
    options = ["-DOMIT_TASK_NUMBER"] if args.without_task_number else []
    subprocess.run(compiler + options + ["-shared", str(ROOT / "tests/native/fake_vm.cpp"), "-o", str(lab / "_bwv_original_vm.dll")], check=True, env=env)
    subprocess.run(compiler + [str(ROOT / "tests/native/host.cpp"), "-o", str(lab / "host.exe")], check=True, env=env)
    shutil.copyfile(bridge, lab / "ScriptLibraryR.dll")
    with subprocess.Popen([str(lab / 'host.exe'), '--idle'], cwd=lab,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as idle:
        try:
            assert idle.stdout.readline().strip()==b'idle_ready'
            before=snapshot(idle.pid)
            assert before['ready'] is True and before['source_age_ms']<1500,before
            assert idle.stdout.readline().strip()==b'idle_suspended'
            after=snapshot(idle.pid)
            assert after['ticks']>before['ticks'] and after['source_age_ms']<1500,after
            out,err=idle.communicate(timeout=5)
            assert idle.returncode==0 and b'idle_resumed' in out,(idle.returncode,out,err)
            print(json.dumps({'reader_idle_suspend_resume':'passed','idle_callback_forwarding':'passed'}))
        except BaseException:
            idle.kill();idle.communicate();raise
    with subprocess.Popen([str(lab / "host.exe")], cwd=lab, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as host:
        try:
            time.sleep(0.8)
            first = snapshot(host.pid)
            assert first["vm_version"] == 7, first
            assert first["task_count"] == 3, first
            assert first["capabilities"] == [], first
            assert first["control"]["verified"] is False, first
            if 'action_probe' in first:
                assert first['action_probe']['supported'] is False, first
                assert first['action_probe']['engine_calls'] == 0, first
                assert first['action_probe']['activity']['verified'] is False, first
            assert first["native_table"]["reserved_prefix"] is True, first
            assert first["native_table"]["unavailable_functions"] == 1, first
            assert first["native_functions"] == ["SYNTHETIC_RESERVED", "SYNTHETIC_POSITION", "START_DIALOGUE"], first
            trace = first["native_trace"]
            assert trace["entered"] == trace["left"] > 0, trace
            assert 0 < len(trace["events"]) <= 32, trace
            assert trace["events"][-1]["name"] == "START_DIALOGUE", trace
            assert trace["events"][-1]["slot"] == 3, trace
            assert trace["control_verified"] is False, trace
            assert first["globals"] == [{"name": "synthetic_hunger", "type": 2, "value_bits": 1061158912}], first
            for _ in range(3):
                assert snapshot(host.pid)["ticks"] >= first["ticks"]
            assert host.stdout.readline().startswith(b"ready ")
            assert host.stdout.readline().strip() == b"trace_full"
            full_trace = snapshot(host.pid)["native_trace"]
            assert full_trace["total_events"] > 32 and len(full_trace["events"]) == 32, full_trace
            assert full_trace["events"][0]["sequence"] > 1, full_trace
            events = full_trace["events"]
            expected_tasks = {None} if args.without_task_number else {17, 29}
            assert {event["task_number"] for event in events} == expected_tasks, events
            for enter, leave in zip(events[::2], events[1::2]):
                assert enter["phase"] == "enter" and leave["phase"] == "leave", events
                assert enter["task_number"] == leave["task_number"], events
                expected_name = None if args.without_task_number else (
                    "synthetic_story" if enter["task_number"] == 17 else "synthetic_creature")
                assert enter["task_name"] == leave["task_name"] == expected_name, events
                assert enter["input_count"] == leave["input_count"] == 0, events
                assert enter["output_count"] == leave["output_count"] == 1, events
            assert host.stdout.readline().strip() == b"restored"
            invalidated = snapshot(host.pid)
            assert invalidated["ready"] is False and invalidated["lifecycle"] == "invalidated", invalidated
            time.sleep(1)
            after = snapshot(host.pid)
            assert after["session"] > first["session"], after
            stdout, stderr = host.communicate(timeout=10)
            assert host.returncode == 0, (host.returncode, stdout, stderr)
            print(json.dumps({"synthetic_abi": "passed", "forwarding": "passed", "operand_word_abi": "passed", "pipe": "passed", "session_invalidation": "passed", "callback_passthrough": "passed", "bounded_event_trace": "passed", "task_attribution": "unavailable_fallback_passed" if args.without_task_number else "passed"}))
        except BaseException:
            host.kill()
            stdout, stderr = host.communicate()
            print(stdout.decode(errors="replace"), stderr.decode(errors="replace"), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
