# Contributing

Start with [usage](usage.md). Keep changes focused and describe the problem,
resulting behavior and checks in your pull request.

## Development

From PowerShell in the repository, create an environment and install dependencies:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[launcher]"
.venv\Scripts\python.exe -m unittest discover -s tests
```

For native changes, build and run the synthetic checks:

```powershell
.venv\Scripts\python.exe tools\build_native.py --action-probe --repeat-actions --object-movement --action-catalog --held-actions
.venv\Scripts\python.exe tools\test_native.py --zig .venv\Lib\site-packages\ziglang\zig.exe --bridge build\native-held-actions\ScriptLibraryR.dll
```

Close the game before installing a changed DLL with `Start-Mod.cmd --install`.
Use `--dummy` for game tests without paid LLM calls; API voice can still incur
transcription charges if you use it. Record what you actually
observed; an accepted command or ended plan does not prove its effect occurred.

## What to share

- Submit authored source and synthetic tests. Keep game files, saves, memory
  captures, credentials, personal settings and local research notes out of commits.
- For bugs, include reproduction steps, expected/actual behavior and a reviewed
  error excerpt. Use the shareable compatibility report for unsupported builds.
- Preserve required third-party notices and document any introduced dependencies
  or copied/adapted material. Reading a project for ideas does not grant permission
  to copy its implementation under this repository's license.

The [MIT license](../LICENSE) covers the original mod code, not Black & White.
Contributions must not redistribute game assets or bypass copy protection.
