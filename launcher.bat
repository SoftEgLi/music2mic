@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 管理员检测：管理员 -> 0；非管理员 -> 1（用 Windows 标识判断，不依赖 Server 服务）
powershell -NoProfile -Command "exit [int](! ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
if %errorlevel%==1 (
    rem 非管理员：以管理员身份直接启动 pythonw（只弹一次 UAC，不弹第二个 CMD 窗口）
    if exist ".venv\Scripts\pythonw.exe" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~dp0.venv\Scripts\pythonw.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath 'pythonw' -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -Verb RunAs"
    )
    exit /b
)

rem 已是管理员：直接启动
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
) else (
    start "" pythonw main.py
)