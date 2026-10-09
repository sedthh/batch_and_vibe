@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python -m venv .venv
) else (
    py -3 -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e ".[launcher]"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" tools\build_native.py --action-probe --repeat-actions --object-movement --action-catalog --held-actions
if errorlevel 1 goto failed
echo Build complete. Close the game before approving the installation prompt.
echo After installation, use Start-Mod.cmd to configure your model and API key if needed.
echo Start the mod before launching the game. Stop mod or closing its window exits cleanly.
".venv\Scripts\python.exe" -m bwv.launcher --install
if errorlevel 1 goto failed
exit /b 0
:failed
echo ERROR: Setup did not finish. Read the error above, correct it, then run Setup-Mod.cmd again.
echo Install Python 3.11 or newer from python.org if Python could not be found.
pause
exit /b 1
