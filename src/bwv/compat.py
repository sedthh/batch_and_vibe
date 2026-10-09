"""Compatibility fingerprints are metadata, not entitlement verification."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from .pe import PE, PEError
from .image_signature import image_signature
from .supported_layouts import SUPPORTED_LAYOUTS

VM_PATH = Path("Plug Ins") / "ScriptLibraryR.dll"
ORIGINAL_VM = "_bwv_original_vm.dll"
REQUIRED_EXPORTS = {"Version", "Initialise", "LookIn", "LoadBinary", "RestoreState", "UnInitialise", "LoopGlobalVariables"}

# Shared game-build checksums, not a CD key, machine ID or personal configuration.
# Fixed native offsets must never be enabled merely because a patch label matches.
EXE_SHA256 = "3f58bc02a6c421b4e66ef08716646bf0e1fa55ab292786edb521d7596ed48aba"
VM_SHA256 = "047b1d7cd967fb653f214a67a23a688e9f57558c102b80e5706d44e9221b79a1"
BUILD_ID = "bw1-142"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_image(pe, file_hash, component):
    """Exact-file fast path, then an independently validated same-layout match."""
    exact={'exe':EXE_SHA256,'vm':VM_SHA256}[component]
    if file_hash==exact:
        return {'supported':True,'method':'exact_file','reason':'Known game build.'}
    try:
        actual=image_signature(pe)
    except (PEError, UnicodeError, AttributeError) as error:
        return {'supported':False,'method':'unsupported','reason':
                'Cannot validate the x86 PE layout ('+type(error).__name__+').'}
    expected=SUPPORTED_LAYOUTS[component]
    if actual['layout_sha256']==expected['layout_sha256']:
        return {'supported':True,'method':'matching_layout','reason':
                'Code, data and layout match; only excluded file metadata or resource contents differ.'}
    changed=[]
    if actual['headers_sha256']!=expected['headers_sha256']:changed.append('loader_headers')
    if len(actual['sections'])!=len(expected['sections']):changed.append('section_count')
    for i,section in enumerate(actual['sections']):
        if i>=len(expected['sections']) or section!=expected['sections'][i]:
            changed.append(section['name'] if section['name'].isprintable() else 'unnamed_section')
    return {'supported':False,'method':'unsupported','different_regions':changed,
            'reason':'Engine code, data or memory layout differs from the supported build. '
                     'This patch needs compatibility investigation; fixed addresses were not enabled.'}


def unsupported_reason(report):
    parts=[name+': '+item['reason']+(' Differing regions: '+', '.join(item['different_regions'])+'.'
           if item.get('different_regions') else '') for name,item in report.get('compatibility',{}).items()
           if not item.get('supported')]
    if not report.get('abi_candidate',True):parts.append('The x86 game/VM interface does not match.')
    return ' '.join(parts) or 'Unsupported game build or interface.'


def shareable_report(report):
    """Allowlisted build facts only; no installation paths or runtime state."""
    return {'schema_version':1,'build_id':(report.get('build') or {}).get('id'),
            **{key:report[key] for key in ('status','architecture','exe_sha256','vm_sha256')},
            'compatibility':{name:{key:item[key] for key in
                ('supported','method','reason','different_regions') if key in item}
                for name,item in report['compatibility'].items() if name in ('exe','vm')}}


def inspect_install(root: Path) -> dict:
    root = root.resolve(strict=True)
    exe_path = root / "runblack.exe"
    installed = (root / "bwv-install.json").is_file()
    vm_path = root / (ORIGINAL_VM if installed else VM_PATH)
    exe, vm = PE.read(exe_path), PE.read(vm_path)
    exe_hash, vm_hash = sha256(exe_path), sha256(vm_path)
    registry = json.loads(files("bwv").joinpath("builds.json").read_text("utf-8"))
    compatibility={'exe':check_image(exe,exe_hash,'exe'),'vm':check_image(vm,vm_hash,'vm')}
    match = (next((b for b in registry["builds"] if b["id"] == BUILD_ID), None)
             if all(item['supported'] for item in compatibility.values()) else None)
    exports = vm.exports()
    abi_candidate = exe.machine == vm.machine == 0x14C and not exe.managed and not vm.managed and REQUIRED_EXPORTS <= set(exports)
    return {
        "install": str(root), "installed": installed,
        "exe_sha256": exe_hash, "vm_sha256": vm_hash,
        "architecture": "x86" if exe.machine == 0x14C else hex(exe.machine),
        "managed": exe.managed, "vm_exports": exports,
        "build": match, "abi_candidate": abi_candidate, "compatibility":compatibility,
        "status": match["status"] if match and abi_candidate else "unsupported",
        "gameplay_writes": False,
        "ownership_verified": False,
    }
