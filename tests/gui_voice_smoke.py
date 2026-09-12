"""Manual Windows/CUDA smoke: real Tk app and presets, without sending PTT.

Uses temporary configuration; opens only the configured CABLE output and a
physical microphone. Run from the project root with .venv/Scripts/python.exe.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")


def smoke():
    import config
    import ptt
    from gui import Music2MicApp

    report = {"events": [], "checks": [], "failures": []}
    started = time.monotonic()
    phase = "discover"
    deadline = started + 120
    phase_started = started
    with tempfile.TemporaryDirectory(prefix="m2m_gui_voice_") as folder:
        cfg = config.load_config()
        config.CONFIG_PATH = Path(folder) / "config.json"
        ptt.LOG_PATH = Path(folder) / "ptt.log"
        cfg["audio"].update(device="CABLE Input", auto_ptt=False, loop=False)
        cfg["voice"]["enabled"] = False
        cfg["slots"][0]["path"] = str(ROOT / "musics" / "可惜兄弟.mp3")
        app = Music2MicApp(cfg)
        app.title("Music2Mic 集成验证（自动关闭、不发送说话键）")
        app.tabs.select(1)
        original_poll = app.voice.poll
        original_play = app.player.play
        original_hold = app.ptt.hold

        def poll():
            events = original_poll()
            for event in events:
                if event.get("event") not in ("log", "devices"):
                    report["events"].append({"seconds": round(time.monotonic() - started, 3), **event})
            return events

        def play(index, path):
            check("preset starts only after mute acknowledgement", app.voice.state == "muted")
            return original_play(index, path)

        def forbidden_hold():
            raise AssertionError("This smoke test must never send PTT")

        app.voice.poll = poll
        app.player.play = play
        app.ptt.hold = forbidden_hold
        app.report_callback_exception = lambda kind, value, tb: fail("".join(traceback.format_exception(kind, value, tb)))

        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            report["checks"].append(name)

        def fail(message):
            report["failures"].append(message)
            app.quit()

        def advance(next_phase):
            nonlocal phase, phase_started
            phase = next_phase
            phase_started = time.monotonic()

        def tick():
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Timed out in {phase}; voice={app.voice.state}: {app.voice.message}")
                if app.voice.state == "error":
                    raise RuntimeError(app.voice.message)
                if phase == "discover" and app.voice.devices and app.voice.can_start:
                    app.voice_enabled_var.set(True)
                    app._on_voice_enabled()
                    check("common CABLE output selected", "cable input" in app.player.device_in_use.lower())
                    check("voice enabled", app.voice_enabled_var.get())
                    advance("loading")
                elif phase == "loading" and app.voice.state == "running":
                    check("CUDA voice ready through GUI controller", True)
                    app._play(0)
                    advance("first_preset")
                elif phase == "first_preset" and app.player.playing_path:
                    check("preset and muted GUI status active", app.voice.state == "muted")
                    advance("natural_end")
                elif phase == "natural_end" and app.player.playing_path is None and app.voice.state == "running":
                    check("natural preset end resumes voice", not app._preset_pending)
                    app.cfg["audio"]["loop"] = True
                    app._play(0)
                    advance("loop_start")
                elif phase == "loop_start" and app.player.playing_path:
                    advance("loop_playing")
                elif phase == "loop_playing" and time.monotonic() - phase_started > 3:
                    check("loop longer than clip stays muted", app.player.is_playing() and app.voice.state == "muted")
                    app._stop_all()
                    advance("manual_resume")
                elif phase == "manual_resume" and app.voice.state == "running":
                    check("manual stop resumes voice", app.player.playing_path is None)
                    for _ in range(8):
                        app._play(0)
                        app._stop_all()
                    app._play(0)
                    advance("rapid_final")
                elif phase == "rapid_final" and app.player.playing_path:
                    check("rapid cancellation leaves final preset playing with muted voice", app.voice.state == "muted")
                    app._stop_all()
                    advance("final_resume")
                elif phase == "final_resume" and app.voice.state == "running":
                    check("voice survives rapid cancellation", app.voice.is_running)
                    app.voice_enabled_var.set(False)
                    app._on_voice_enabled()
                    advance("shutdown")
                elif phase == "shutdown" and app.voice.can_start:
                    check("unchecking closes CUDA child", app.voice.state == "stopped")
                    app.quit()
                    return
                app.after(50, tick)
            except Exception:
                fail(traceback.format_exc())

        app.after(100, tick)
        try:
            app.mainloop()
        finally:
            app.ptt.hold = original_hold
            app._on_close()
    report["passed"] = not report["failures"]
    report["seconds"] = time.monotonic() - started
    report["scope"] = "Real Tk app, CUDA subprocess, microphone/CABLE routing and pygame presets; no PTT sent; not a CS2 game test."
    path = ROOT / "voice-changer" / "results" / "integration-gui-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(smoke())
