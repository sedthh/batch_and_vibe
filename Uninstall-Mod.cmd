@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: The mod's Python environment is missing. Restore this repository's environment before uninstalling.
    echo Your game files have not been changed.
    pause
    exit /b 1
)
echo Close Black ^& White and stop the mod before continuing.
echo This restores the original game DLL. Saves, settings and creature memory are kept.
".venv\Scripts\python.exe" -m bwv.launcher --uninstall %*
if errorlevel 1 (
    echo ERROR: Uninstall did not finish. Read the error above before trying again.
    pause
    exit /b 1
)
exit /b 0
