"""Exercise the controller with real subprocess pipes, without audio or CUDA."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from voice_control import VoiceController, preferred_devices


FAKE_RUNNER = r'''
import json
from pathlib import Path
import sys
import time

def emit(event, **kwargs):
    print("M2M_EVENT " + json.dumps(dict(event=event, **kwargs)), flush=True)

args = sys.argv[1:]
mode = Path(args[args.index("--reference") + 1]).stem
emit("launched", start_muted="--start-muted" in args)
time.sleep(0.08)
emit("ready", muted="--start-muted" in args)
for line in sys.stdin:
    data = json.loads(line)
    emit("received", **data)
    command = data["command"]
    if command == "mute":
        if mode == "die":
            sys.exit(7)
        if mode == "ignore":
            continue
        time.sleep(0.12)
        emit("muted", id=data["id"], drain_seconds=0.12)
    elif command == "resume":
        time.sleep(0.02)
        emit("resumed", id=data["id"])
    elif command == "stop":
        if mode == "ignore-stop":
            time.sleep(30)
        emit("stopped", id=data["id"])
        break
'''

INPUT = dict(index=1, name="USB2.0 Microphone", hostapi=2,
             hostapi_name="Windows WASAPI", max_input_channels=1, max_output_channels=0)
OUTPUT = dict(index=3, name="CABLE Input (VB-Audio Virtual Cable)", hostapi=2,
              hostapi_name="Windows WASAPI", max_input_channels=0, max_output_channels=2)


class VoiceControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tools = self.root / "voice-changer" / "tools"
        tools.mkdir(parents=True)
        (tools / "run_voice_changer.py").write_text(FAKE_RUNNER, encoding="utf-8")
        (tools / "probe_audio.py").write_text(
            "import json\nprint(" + repr(json.dumps([INPUT, OUTPUT])) + ")\n", encoding="utf-8")
        self.controller = VoiceController(self.root, python_executable=sys.executable,
                                          command_timeout=0.4, stop_timeout=0.2,
                                          exit_drain_seconds=0.08)
        self.addCleanup(self.controller.close)
        self.events = []

    def poll_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.events.extend(self.controller.poll())
            if predicate():
                return
            time.sleep(0.01)
        self.fail(f"Timed out: state={self.controller.state}, events={self.events}")

    def start(self, mode="normal", initially_muted=False):
        reference = self.root / (mode + ".wav")
        reference.write_bytes(b"fake reference")
        self.assertTrue(self.controller.start(reference, INPUT, OUTPUT, initially_muted))

    def test_launch_muted_allows_preset_while_model_loads_and_does_not_auto_resume(self):
        self.start()
        callbacks = []
        self.controller.request_mute(lambda: callbacks.append("safe"))
        self.assertEqual(callbacks, ["safe"])
        self.poll_until(lambda: self.controller.state == "muted")
        self.assertTrue(next(e for e in self.events if e["event"] == "launched")["start_muted"])
        self.assertFalse([e for e in self.events if e["event"] == "received"])
        self.controller.resume()
        self.poll_until(lambda: self.controller.state == "running")

    def test_mute_waits_for_ack_and_callbacks_run_on_poll_thread(self):
        self.start()
        self.poll_until(lambda: self.controller.state == "running")
        called = []
        self.controller.request_mute(lambda: called.append(threading.get_ident()))
        self.assertFalse(called)
        self.poll_until(lambda: any(e.get("command") == "mute" for e in self.events))
        self.assertFalse(called)
        self.poll_until(lambda: bool(called))
        self.assertEqual(called, [threading.get_ident()])
        self.assertEqual(self.controller.state, "muted")
        self.controller.resume()
        self.poll_until(lambda: self.controller.state == "running")

    def test_cancelled_mute_cannot_start_old_preset_or_override_new_resume(self):
        self.start()
        self.poll_until(lambda: self.controller.state == "running")
        called = []
        self.controller.request_mute(lambda: called.append("old preset"))
        self.controller.resume()
        self.poll_until(lambda: len([e for e in self.events if e["event"] == "resumed"]) == 2)
        self.assertFalse(called)
        self.assertEqual(self.controller.state, "running")

    def test_new_mute_after_cancel_shares_inflight_ack_without_obsolete_resume(self):
        self.start()
        self.poll_until(lambda: self.controller.state == "running")
        called = []
        self.controller.request_mute(lambda: called.append("cancelled"))
        self.controller.resume()
        self.controller.request_mute(lambda: called.append("latest"))
        self.assertFalse(called)
        self.poll_until(lambda: bool(called))
        self.assertEqual(called, ["latest"])
        self.assertEqual([e["command"] for e in self.events if e["event"] == "received"],
                         ["resume", "mute"])

    def test_mute_during_inflight_resume_waits_for_following_mute_ack(self):
        self.start(initially_muted=True)
        self.poll_until(lambda: self.controller.state == "muted")
        called = []
        self.controller.resume()
        self.controller.request_mute(lambda: called.append("safe"))
        self.assertFalse(called)
        self.poll_until(lambda: bool(called))
        self.assertEqual([e["command"] for e in self.events if e["event"] == "received"],
                         ["resume", "mute"])
        self.assertEqual(self.controller.state, "muted")

    def test_rapid_play_stop_requests_coalesce_during_slow_hardware_drain(self):
        self.controller.runner.write_text(FAKE_RUNNER.replace("time.sleep(0.12)",
                                                            "time.sleep(0.70)"), encoding="utf-8")
        self.controller.command_timeout = 4.0
        self.start()
        self.poll_until(lambda: self.controller.state == "running")
        called = []
        for index in range(7):
            self.controller.request_mute(lambda i=index: called.append(i))
            self.controller.resume()
        self.controller.request_mute(lambda: called.append("latest"))
        self.poll_until(lambda: bool(called), timeout=3)
        self.assertEqual(called, ["latest"])
        self.assertTrue(self.controller.running)
        self.assertEqual(self.controller.state, "muted")
        self.assertEqual([e["command"] for e in self.events if e["event"] == "received"],
                         ["resume", "mute"])

    def test_child_death_releases_waiting_preset_only_after_exit_and_drain(self):
        self.start("die")
        self.poll_until(lambda: self.controller.state == "running")
        called = []
        self.controller.request_mute(lambda: called.append(time.monotonic()))
        self.poll_until(lambda: any(e["event"] == "process-exit" for e in self.events))
        exited_at = time.monotonic()
        self.assertFalse(called)
        self.assertFalse(self.controller.can_start)
        self.poll_until(lambda: bool(called))
        self.assertGreaterEqual(called[0] - exited_at, 0.06)
        self.assertFalse(self.controller.running)
        self.assertEqual(self.controller.state, "error")

    def test_missing_ack_terminates_child_before_allowing_preset(self):
        self.start("ignore")
        self.poll_until(lambda: self.controller.state == "running")
        process = self.controller._process
        called = []
        self.controller.request_mute(lambda: called.append(process.poll()))
        self.poll_until(lambda: bool(called))
        self.assertIsNotNone(called[0])
        self.assertTrue(any(e["event"] == "error" and "超时" in e["message"]
                            for e in self.events))

    def test_stop_is_nonblocking_and_escalates_stuck_child(self):
        self.start("ignore-stop")
        self.poll_until(lambda: self.controller.state == "running")
        process = self.controller._process
        started = time.monotonic()
        self.controller.stop()
        self.assertLess(time.monotonic() - started, 0.1)
        self.poll_until(lambda: not self.controller.running)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.controller.state, "stopped")

    def test_close_terminates_running_child(self):
        self.start()
        process = self.controller._process
        self.controller.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.controller.can_start)

    def test_discovery_callback_runs_on_gui_thread(self):
        called = []
        self.assertTrue(self.controller.discover_devices(
            lambda devices, error: called.append((devices, error, threading.get_ident()))))
        self.poll_until(lambda: bool(called))
        self.assertEqual(called, [([INPUT, OUTPUT], None, threading.get_ident())])
        self.assertFalse(self.controller.refreshing)
        self.assertEqual(preferred_devices(self.controller.devices), (1, 3))

    def test_device_api_mismatch_fails_before_launch(self):
        reference = self.root / "normal.wav"
        reference.touch()
        self.assertFalse(self.controller.start(reference, INPUT, {**OUTPUT, "hostapi": 1}))
        self.assertFalse(self.controller.running)
        self.assertEqual(self.controller.state, "error")


if __name__ == "__main__":
    unittest.main()
