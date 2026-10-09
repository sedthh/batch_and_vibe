"""Transactional installation of our DLL; never distributes the original VM."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .compat import ORIGINAL_VM, VM_PATH, inspect_install, sha256, unsupported_reason
from .pe import PE


def _safe_root(root: Path) -> Path:
    root = root.resolve(strict=True)
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Game files and installation backups must stay outside Git repositories")
    if (root / VM_PATH).is_symlink() or (root / "Plug Ins").is_symlink():
        raise ValueError("Symlinked game paths are not supported")
    return root


def _exclusive_copy(source: Path, destination: Path) -> None:
    with source.open("rb") as src, destination.open("xb") as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())


def install(root: Path, bridge: Path, *, experimental: bool = False) -> dict:
    root = _safe_root(root)
    if not experimental:
        raise ValueError("Observer is experimental; explicit --experimental acknowledgement required")
    report = inspect_install(root)
    if report["installed"]:
        raise ValueError("Already installed; uninstall before changing the bridge")
    if report["status"] != "experimental" or not report["abi_candidate"]:
        raise ValueError(unsupported_reason(report)+" Nothing changed.")
    bridge_pe = PE.read(bridge)
    if bridge_pe.machine != 0x14C or set(bridge_pe.exports()) != set(report["vm_exports"]):
        raise ValueError("Bridge does not match the game's x86 export interface")
    original = root / ORIGINAL_VM
    marker = root / "bwv-install.json"
    staged = root / "Plug Ins" / "bwv-staged.tmp"
    if any(p.exists() for p in (original, marker, staged)):
        raise ValueError("Installation files already exist; inspect the interrupted installation")
    ledger = {"schema_version": 1, "build_id": report["build"]["id"],
              "exe_sha256": report["exe_sha256"], "original_vm_sha256": report["vm_sha256"],
              "bridge_sha256": sha256(bridge), "mode": "observer", "state": "prepared"}
    _exclusive_copy(root / VM_PATH, original)
    _exclusive_copy(bridge, staged)
    with marker.open("x", encoding="utf-8") as out:
        json.dump(ledger, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    # The manifest and original exist durably before this single atomic replace.
    os.replace(staged, root / VM_PATH)
    ledger["state"] = "installed"
    # A prepared manifest is also recoverable if the process ends here.
    return ledger


def uninstall(root: Path) -> None:
    root = _safe_root(root)
    marker = root / "bwv-install.json"
    ledger = json.loads(marker.read_text("utf-8"))
    if ledger.get("schema_version") != 1:
        raise ValueError("Unsupported installation manifest")
    original = root / ORIGINAL_VM
    current = root / VM_PATH
    if sha256(original) != ledger["original_vm_sha256"]:
        raise ValueError("Original VM backup changed; refusing to overwrite anything")
    if sha256(current) not in {ledger["bridge_sha256"], ledger["original_vm_sha256"], ledger.get("previous_bridge_sha256")}:
        raise ValueError("Installed VM changed; refusing to overwrite another mod")
    staged = root / "Plug Ins" / "bwv-restore.tmp"
    _exclusive_copy(original, staged)
    os.replace(staged, current)
    if sha256(current) != ledger["original_vm_sha256"]:
        raise ValueError("Restore verification failed; backup retained")
    original.unlink()
    marker.unlink()


def update(root: Path, bridge: Path) -> dict:
    """Replace only our verified bridge, keeping the original VM backup throughout.

    A prepared manifest names both bridge hashes so interruption between the two
    atomic replacements can be recovered by another update or by uninstall.
    Caller must ensure the game is stopped before invoking this operation.
    """
    root = _safe_root(root)
    marker = root / 'bwv-install.json'
    if not marker.exists():
        return install(root, bridge, experimental=True)
    report = inspect_install(root)
    ledger = json.loads(marker.read_text('utf-8'))
    if ledger.get('schema_version') != 1:
        raise ValueError('Unsupported installation manifest; nothing changed')
    if report['status'] != 'experimental':
        raise ValueError(unsupported_reason(report)+' Nothing changed.')
    if sha256(root / ORIGINAL_VM) != ledger['original_vm_sha256']:
        raise ValueError('Original backup changed; nothing changed')
    current = sha256(root / VM_PATH)
    if current not in {ledger['bridge_sha256'], ledger.get('previous_bridge_sha256')}:
        raise ValueError('Installed DLL belongs to another modification; nothing changed')
    pe = PE.read(bridge)
    if pe.machine != 0x14c or set(pe.exports()) != set(report['vm_exports']):
        raise ValueError('Bridge does not match the game interface')
    incoming = sha256(bridge)
    if current == incoming:
        return {'installed': True, 'changed': False}
    staged = root / 'Plug Ins' / 'bwv-staged.tmp'
    marker_stage = root / 'bwv-install.tmp'
    if staged.exists() or marker_stage.exists():
        raise ValueError('An interrupted install has staging files; restore or inspect them before continuing')
    _exclusive_copy(bridge, staged)
    ledger.update(bridge_sha256=incoming, previous_bridge_sha256=current, state='prepared')
    with marker_stage.open('x', encoding='utf-8') as out:
        json.dump(ledger, out, indent=2)
        out.flush(); os.fsync(out.fileno())
    os.replace(marker_stage, marker)
    os.replace(staged, root / VM_PATH)
    if sha256(root / VM_PATH) != incoming:
        raise ValueError('Bridge verification failed; original backup retained')
    return {'installed': True, 'changed': True}
