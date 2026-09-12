"""Explicit release QA: real packaged Tk/CUDA/audio, temporary settings, no PTT.

Invoke the EXE with ``--release-smoke-report <absolute-report-path>``. This mode
opens a microphone/CABLE stream and automatically exercises preset handoffs.
It does not run during ordinary startup.
"""

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

from app_paths import app_root


def run_smoke(report_path):
    report_path = Path(report_path).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    root = app_root()
    started = time.monotonic()
    report = {
        "passed": False, "phase": "initializing", "events": [], "checks": [], "failures": [],
        "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
        "application_root": str(root), "report_path": str(report_path),
        "scope": "Real Tk GUI, portable CUDA subprocess, physical microphone/CABLE routing and pygame presets; no PTT or CS2 game test.",
    }
    app = None
    child = None
    backend_report = None
    completed = False
    live_since = None
    phase_started = started
    config_module = None
    ptt_module = None
    original_config = original_log = None
    user_config = root / "config.json"
    original_user_bytes = user_config.read_bytes() if user_config.is_file() else None

    def save_report():
        report["seconds"] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append(name)

    def advance(phase):
        nonlocal phase_started
        report["phase"] = phase
        phase_started = time.monotonic()
        save_report()

    def fail(message):
        report["failures"].append(message)
        save_report()
        if app is not None:
            app.quit()

    save_report()
    try:
        import config as config_module
        import ptt as ptt_module
        import pygame
        from gui import Music2MicApp

        original_config, original_log = config_module.CONFIG_PATH, ptt_module.LOG_PATH
        example = root / "config.example.json"
        check("bundled example config exists", example.is_file())
        cfg = config_module.load_config(example)
        cfg["audio"].update(device="CABLE Input", auto_ptt=False, loop=False)
        cfg["voice"]["enabled"] = False
        cfg["voice"]["reference"] = str(root / "musics" / "好厉害啊哥哥.mp3")
        preset = root / "musics" / "可惜兄弟.mp3"
        cfg["slots"][0]["path"] = str(preset)
        check("bundled reference and preset exist", preset.is_file() and Path(cfg["voice"]["reference"]).is_file())

        with tempfile.TemporaryDirectory(prefix="music2mic_release_qa_") as temporary:
            config_module.CONFIG_PATH = Path(temporary) / "config.json"
            ptt_module.LOG_PATH = Path(temporary) / "ptt.log"
            try:
                app = Music2MicApp(cfg)
                app.title("Music2Mic 发布验证（自动关闭、不发送说话键）")
                app.tabs.select(1)
                app.protocol("WM_DELETE_WINDOW", lambda: fail("Release QA window closed before completion"))
                app.report_callback_exception = lambda kind, value, tb: fail(
                    "".join(traceback.format_exception(kind, value, tb)))
                check("portable backend Python selected",
                      app.voice.python == root / "voice-changer" / "runtime" / "python.exe")
                report["backend_python"] = str(app.voice.python)
                check("presets use CABLE instead of default output", "cable input" in app.player.device_in_use.lower())
                preset_seconds = pygame.mixer.Sound(str(preset)).get_length()
                report["preset_seconds"] = preset_seconds
                check("bundled MP3 decoder works", preset_seconds > 0)
                original_poll = app.voice.poll
                original_play = app.player.play

                def poll():
                    nonlocal live_since
                    events = original_poll()
                    for event in events:
                        if event.get("event") not in ("log", "devices"):
                            report["events"].append({"seconds": round(time.monotonic() - started, 3), **event})
                    if app.voice.state == "running":
                        if live_since is None:
                            live_since = time.monotonic()
                    else:
                        live_since = None
                    return events

                def play(index, path):
                    check("preset begins only after drained mute acknowledgement", app.voice.state == "muted")
                    return original_play(index, path)

                def forbidden_ptt(*args, **kwargs):
                    raise AssertionError("Release QA must not inject any PTT keys")

                app.voice.poll = poll
                app.player.play = play
                app.ptt.hold = forbidden_ptt
                app.ptt._send = forbidden_ptt

                def warmed_live():
                    return live_since is not None and time.monotonic() - live_since >= 2.2

                def tick():
                    nonlocal child, backend_report, completed
                    try:
                        now = time.monotonic()
                        if now - started > 240:
                            raise TimeoutError(f"Release QA timed out in {report['phase']}: {app.voice.message}")
                        if app.voice.state == "error":
                            raise RuntimeError(app.voice.message)
                        phase = report["phase"]
                        if phase == "discover" and app.voice.devices and app.voice.can_start:
                            app.voice_enabled_var.set(True)
                            app._on_voice_enabled()
                            check("GUI enables CUDA voice", app.voice_enabled_var.get() and app.voice.is_running)
                            child = app.voice._process
                            arguments = child.args
                            backend_report = Path(arguments[arguments.index("--report") + 1])
                            report["backend_report_path"] = str(backend_report)
                            advance("first_live")
                        elif phase == "first_live" and warmed_live():
                            check("voice runs for at least two seconds before first preset", True)
                            app._play(0)
                            advance("first_preset")
                        elif phase == "first_preset" and app.player.playing_path:
                            check("first preset is playing with muted voice", app.voice.state == "muted")
                            advance("natural_end")
                        elif phase == "natural_end" and app.player.playing_path is None and warmed_live():
                            check("natural end resumes voice for at least two seconds", not app._preset_pending)
                            app.cfg["audio"]["loop"] = True
                            app._play(0)
                            advance("loop_start")
                        elif phase == "loop_start" and app.player.playing_path:
                            advance("loop_playing")
                        elif phase == "loop_playing" and now - phase_started >= max(3.0, preset_seconds + 0.75):
                            check("loop remains muted beyond one complete clip",
                                  app.player.is_playing() and app.voice.state == "muted")
                            app._stop_all()
                            advance("manual_resume")
                        elif phase == "manual_resume" and warmed_live():
                            check("manual stop resumes voice for at least two seconds", app.player.playing_path is None)
                            for _ in range(8):
                                app._play(0)
                                app._stop_all()
                            app._play(0)
                            advance("rapid_final")
                        elif phase == "rapid_final" and app.player.playing_path:
                            check("rapid cancellation leaves latest preset playing with muted voice", app.voice.state == "muted")
                            app._stop_all()
                            advance("final_resume")
                        elif phase == "final_resume" and warmed_live():
                            check("voice survives rapid handoffs and resumes for at least two seconds", app.voice.is_running)
                            app.voice_enabled_var.set(False)
                            app._on_voice_enabled()
                            advance("shutdown")
                        elif phase == "shutdown" and app.voice.can_start:
                            check("unchecking voice closes CUDA child", app.voice.state == "stopped" and child.poll() == 0)
                            check("backend produced its final report", backend_report.is_file())
                            backend_data = json.loads(backend_report.read_text(encoding="utf-8"))
                            report["backend_report"] = backend_data
                            check("GPU performed live inference", backend_data.get("chunks", 0) > 0)
                            check("audio callbacks and muted callbacks ran",
                                  backend_data.get("counters", {}).get("callbacks", 0) > 0
                                  and backend_data.get("counters", {}).get("muted_callbacks", 0) > 0)
                            completed = True
                            advance("complete")
                            app.quit()
                            return
                        app.after(50, tick)
                    except Exception:
                        fail(traceback.format_exc())

                advance("discover")
                app.after(100, tick)
                app.mainloop()
            finally:
                if app is not None:
                    try:
                        app._on_close()
                    except Exception:
                        # Teardown must still own and terminate the backend if
                        # a partially closed Tk window cannot finish its hook.
                        app.voice.close()
                        app.player.shutdown()
                        raise
    except Exception:
        report["failures"].append(traceback.format_exc())
    finally:
        if config_module is not None and original_config is not None:
            config_module.CONFIG_PATH = original_config
        if ptt_module is not None and original_log is not None:
            ptt_module.LOG_PATH = original_log
        if not completed and not report["failures"]:
            report["failures"].append("Release QA exited before completing all phases")
        try:
            check("CUDA child does not survive GUI cleanup", child is None or child.poll() is not None)
            check("personal config was not modified",
                  (user_config.read_bytes() if user_config.is_file() else None) == original_user_bytes)
        except Exception:
            report["failures"].append(traceback.format_exc())
        report["passed"] = completed and not report["failures"]
        save_report()
    return 0 if report["passed"] else 1
