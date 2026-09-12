@echo off
setlocal
cd /d "%~dp0voice-changer"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The voice-changer Python environment is missing.
    pause
    exit /b 1
)
if not exist "gui.py" (
    echo The voice-changer GUI is missing.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%CD%\gui.py"
endlocal
