@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: The mod environment is missing. Run Setup-Mod.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m bwv.launcher %*
exit /b %errorlevel%
