r"""Windows desktop smoke test: wheel selection must send PTT to the prior window.

Run: .venv\Scripts\python.exe tests\gui_ptt_smoke.py
Creates two temporary windows and a silent WAV; never edits the real config.
"""
import ctypes
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import wave
from ctypes import wintypes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND


def activate_receiver(hwnd):
    # Windows may reject SetForegroundWindow until this process receives input.
    # Click only if the point belongs to our own temporary receiver window.
    from pynput.mouse import Button, Controller
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    point = wintypes.POINT(rect.left + 100, rect.top + 55)
    if user32.GetAncestor(user32.WindowFromPoint(point), 2) != hwnd:
        return
    mouse = Controller()
    previous = mouse.position
    try:
        mouse.position = (point.x, point.y)
        mouse.click(Button.left)
    finally:
        mouse.position = previous


def receiver(folder):
    root = tk.Tk()
    root.title("Music2Mic PTT 接收测试（自动关闭）")
    root.geometry("400x100+80+80")
    tk.Label(root, text="正在验证转盘关闭后收到 C 键按下和松开").pack(pady=20)
    events = []

    def record(event):
        if event.keycode != 0x43:
            return
        events.append("down" if event.type == tk.EventType.KeyPress else "up")
        (folder / "events.json").write_text(json.dumps(events), encoding="utf-8")

    root.bind("<KeyPress>", record)
    root.bind("<KeyRelease>", record)

    def ready():
        root.focus_force()
        hwnd = user32.GetAncestor(root.winfo_id(), 2)
        (folder / "ready.txt").write_text(str(hwnd), encoding="ascii")

    root.after(100, ready)
    root.after(15000, root.destroy)
    root.mainloop()


def smoke():
    import config
    import ptt
    from gui import Music2MicApp

    previous = user32.GetForegroundWindow()
    app = None
    child = None
    with tempfile.TemporaryDirectory(prefix="m2m_ptt_smoke_") as folder_name:
        folder = Path(folder_name)
        ptt.LOG_PATH = folder / "ptt.log"
        cfg = config.load_config()
        config.CONFIG_PATH = folder / "config.json"
        cfg["hotkeys"]["ptt"] = "c"
        cfg["audio"].update(auto_ptt=True, loop=True)
        wav = folder / "silence.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x00" * 44100)
        cfg["slots"][0]["path"] = str(wav)
        failures = []
        observations = []
        target = None
        focus_at_stop = None
        try:
            app = Music2MicApp(cfg)
            app.title("Music2Mic 转盘/PTT 测试（自动关闭）")
            app.geometry("840x880+500+80")
            app.report_callback_exception = lambda typ, value, tb: failures.append(str(value))
            child = subprocess.Popen(
                [sys.executable, __file__, "--receiver", folder_name],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            original_hold = app.ptt.hold

            def checked_hold():
                foreground = user32.GetForegroundWindow()
                observations.append({"ptt_foreground": foreground, "target": target})
                if foreground != target:
                    failures.append("PTT was sent before focus returned to the target window")
                    return False
                return original_hold()

            app.ptt.hold = checked_hold

            def finish():
                path = folder / "events.json"
                events = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
                observations.append({"received": events})
                if events != ["down", "up"]:
                    if events == ["down"] and focus_at_stop != target:
                        observations.append({"note": "Desktop focus changed; verify key release in Windows instead"})
                    else:
                        failures.append("Target did not receive exactly one C down/up pair")
                if user32.GetAsyncKeyState(0x43) & 0x8000:
                    failures.append("Windows still reports C held after stopping")
                if app.ptt.held:
                    failures.append("PTT still marked held after stopping")
                app.quit()

            def stop():
                nonlocal focus_at_stop
                focus_at_stop = user32.GetForegroundWindow()
                observations.append({"stop_foreground": focus_at_stop, "target": target})
                if not (user32.GetAsyncKeyState(0x43) & 0x8000):
                    failures.append("C was not held during playback")
                app._stop_all()
                app.after(200, finish)

            def select():
                app.wheel._on_key(SimpleNamespace(char="1", keysym="1"))
                app.after(700, stop)

            def prepare(attempt=0):
                nonlocal target
                ready = folder / "ready.txt"
                if not ready.exists():
                    if attempt >= 40:
                        failures.append("Receiver window did not start")
                        app.quit()
                    else:
                        app.after(100, lambda: prepare(attempt + 1))
                    return
                target = int(ready.read_text(encoding="ascii"))
                if user32.GetForegroundWindow() != target:
                    result = user32.SetForegroundWindow(target)
                    if attempt == 3:
                        activate_receiver(target)
                    if attempt >= 40:
                        observations.append({"foreground": user32.GetForegroundWindow(),
                                             "target": target, "set_foreground": result,
                                             "initial_foreground": previous})
                        failures.append("Could not focus receiver window before the test")
                        app.quit()
                    else:
                        app.after(100, lambda: prepare(attempt + 1))
                    return
                user32.SetWindowPos(target, -2, 0, 0, 0, 0, 0x0013)
                app._toggle_wheel()
                app.after(200, select)

            app.after(200, prepare)
            app.after(8000, lambda: (failures.append("Smoke test timed out"), app.quit()))
            app.mainloop()
        finally:
            if app is not None:
                app._on_close()
            if child is not None:
                child.terminate()
                child.wait(timeout=5)
            if previous:
                user32.SetForegroundWindow(previous)
            logging.shutdown()
        for observation in observations:
            print(observation)
        for failure in failures:
            print("FAIL:", failure)
        if failures:
            if ptt.LOG_PATH.exists():
                print(ptt.LOG_PATH.read_text(encoding="utf-8"))
            return 1
        print("GUI_PTT_SMOKE PASS")
        return 0


if __name__ == "__main__":
    if "--receiver" in sys.argv:
        receiver(Path(sys.argv[2]))
    else:
        sys.exit(smoke())
