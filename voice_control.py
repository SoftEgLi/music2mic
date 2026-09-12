"""Nonblocking control of the isolated CUDA voice changer process.

All public methods, including ``poll()``, belong on the GUI thread. Reader and
writer threads only move messages through queues; callbacks run on that GUI
thread. The child starts muted and must acknowledge a drained mute before a
preset may start. No audio or machine-learning packages are imported here.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


EVENT_PREFIX = "M2M_EVENT "


def preferred_devices(devices):
    """Choose a physical microphone and CABLE output on the same audio API."""
    inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
    outputs = [d for d in devices if d.get("max_output_channels", 0) > 0]
    physical = [d for d in inputs if not any(word in d["name"].lower()
                for word in ("cable", "stereo mix", "立体声混音"))]

    def api_rank(device):
        name = device.get("hostapi_name", "").lower()
        return 0 if "wasapi" in name else 1 if "directsound" in name else 2 if name == "mme" else 3

    def mic_rank(device):
        name = device["name"].lower()
        return (0 if "usb2.0" in name else 1 if "麦克风" in name or "microphone" in name else 2,
                api_rank(device))

    cables = sorted([d for d in outputs if "cable input" in d["name"].lower()], key=api_rank)
    for output in cables:
        candidates = sorted([d for d in physical if d["hostapi"] == output["hostapi"]], key=mic_rank)
        if candidates:
            return candidates[0]["index"], output["index"]
    return (sorted(physical, key=mic_rank)[0]["index"] if physical else None,
            cables[0]["index"] if cables else None)


class VoiceController:
    """Manage voice startup, mute acknowledgements, and bounded child cleanup."""

    def __init__(self, root_dir=None, *, python_executable=None,
                 command_timeout=4.0, stop_timeout=3.0, exit_drain_seconds=0.3):
        self.root = Path(root_dir) if root_dir else Path(__file__).resolve().parent
        self.voice_root = self.root / "voice-changer"
        self.python = Path(python_executable) if python_executable else (
            self.voice_root / ".venv" / "Scripts" / "python.exe")
        self.runner = self.voice_root / "tools" / "run_voice_changer.py"
        self.probe = self.voice_root / "tools" / "probe_audio.py"
        self.command_timeout = command_timeout
        self.stop_timeout = stop_timeout
        self.exit_drain_seconds = exit_drain_seconds
        self._hardware_drain_seconds = exit_drain_seconds
        self.state = "stopped"
        self.message = "变声未启动"
        self.devices = []
        self.refreshing = False
        self._process = None
        self._probe_process = None
        self._probe_lock = threading.Lock()
        self._events = queue.Queue()
        self._notifications = []
        self._writes = None
        self._generation = 0
        self._next_id = 0
        self._last_resume_id = -1
        self._commands = {}
        self._mute_callbacks = []
        self._device_callback = None
        self._ready = False
        self._safe_muted = True
        self._desired_muted = True
        self._stop_deadline = None
        self._exit_deadline = None
        self._exit_code = None
        self._terminating = False
        self._closed = False

    @property
    def is_running(self):
        # Include shutdown/drain time so a new child cannot overlap this one.
        return self._process is not None

    @property
    def running(self):
        return self.is_running

    @property
    def can_start(self):
        return not self._closed and not self.is_running and not self.refreshing

    def _set_state(self, state, message):
        if (state, message) != (self.state, self.message):
            self.state, self.message = state, message
            self._notifications.append(dict(event="state", state=state, message=message))

    @staticmethod
    def _popen(command, cwd):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        return subprocess.Popen(command, cwd=str(cwd), env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8",
                                errors="replace", bufsize=1,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def discover_devices(self, callback=None):
        """Read device metadata off-thread; callback receives (devices, error)."""
        if not self.can_start:
            return False
        self.refreshing = True
        self._device_callback = callback

        def discover():
            process = None
            try:
                process = self._popen([str(self.python), "-u", str(self.probe), "list-devices"],
                                      self.voice_root)
                with self._probe_lock:
                    self._probe_process = process
                    closed = self._closed
                if closed:
                    self._terminate_and_wait(process)
                    return
                try:
                    output, _ = process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    self._terminate_and_wait(process)
                    raise RuntimeError("读取音频设备超时，请重新刷新设备")
                if process.returncode:
                    raise RuntimeError(output.strip() or "无法读取音频设备")
                devices = json.loads(output)
                if not isinstance(devices, list):
                    raise ValueError("音频设备列表格式错误")
                self._events.put((None, dict(event="devices", devices=devices)))
            except Exception as exc:
                self._events.put((None, dict(event="devices-error", message=str(exc))))
            finally:
                with self._probe_lock:
                    if self._probe_process is process:
                        self._probe_process = None

        threading.Thread(target=discover, daemon=True, name="voice-device-probe").start()
        return True

    def start(self, reference, input_device, output_device, initially_muted=False):
        if not self.can_start:
            return False
        try:
            reference = Path(reference).expanduser().resolve()
            if not reference.is_file():
                raise ValueError("参考音频不存在，请重新选择")
            if not input_device or not output_device:
                raise ValueError("请选择输入麦克风和变声输出设备")
            if input_device["hostapi"] != output_device["hostapi"]:
                raise ValueError("输入和输出需要选择相同的音频接口，例如 Windows WASAPI")
            if input_device.get("max_input_channels", 0) < 1:
                raise ValueError("所选设备不能作为输入麦克风")
            if output_device.get("max_output_channels", 0) < 1:
                raise ValueError("所选设备不能作为变声输出")
            if "cable" in input_device["name"].lower() and "cable" in output_device["name"].lower():
                raise ValueError("输入请选择实体麦克风，避免 CABLE 输入输出形成回路")
            if not self.python.is_file() or not self.runner.is_file():
                raise ValueError("未找到 GPU 变声运行环境或运行程序")
            session = self.voice_root / "reports" / (
                "launcher-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            session.mkdir(parents=True, exist_ok=True)
            command = [str(self.python), "-u", str(self.runner), "live",
                       "--reference", str(reference),
                       "--input-device", str(input_device["index"]),
                       "--output-device", str(output_device["index"]),
                       "--input-device-name", input_device["name"],
                       "--output-device-name", output_device["name"],
                       "--report", str(session / "live-report.json"),
                       "--control-stdin", "--start-muted"]
            if input_device.get("hostapi_name"):
                command.extend(["--hostapi-name", input_device["hostapi_name"]])
            self._process = self._popen(command, self.voice_root)
        except Exception as exc:
            self._set_state("error", str(exc))
            self._notifications.append(dict(event="error", message=str(exc)))
            return False
        self._generation += 1
        self._ready = False
        self._safe_muted = True
        self._desired_muted = bool(initially_muted)
        self._commands.clear()
        self._last_resume_id = -1
        self._hardware_drain_seconds = self.exit_drain_seconds
        self._stop_deadline = self._exit_deadline = self._exit_code = None
        self._terminating = False
        self._writes = queue.Queue()
        self._set_state("starting", "正在加载 GPU 变声模型…")
        threading.Thread(target=self._read_process, args=(self._process, self._generation),
                         daemon=True, name="voice-reader").start()
        threading.Thread(target=self._write_process,
                         args=(self._process, self._writes, self._generation),
                         daemon=True, name="voice-writer").start()
        return True

    def _read_process(self, process, generation):
        try:
            for line in process.stdout:
                event = None
                if line.startswith(EVENT_PREFIX):
                    try:
                        parsed = json.loads(line[len(EVENT_PREFIX):])
                        if isinstance(parsed, dict):
                            event = parsed
                    except ValueError:
                        pass
                self._events.put((generation, event or dict(event="log", message=line.rstrip())))
        finally:
            process.stdout.close()
            self._events.put((generation, dict(event="process-exit", returncode=process.wait())))

    def _write_process(self, process, writes, generation):
        try:
            while True:
                command = writes.get()
                if command is None:
                    return
                process.stdin.write(json.dumps(command) + "\n")
                process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._events.put((generation, dict(event="control-error", message=str(exc))))
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def _send(self, command):
        self._next_id += 1
        command_id = self._next_id
        self._commands[command_id] = (command, time.monotonic())
        if command == "resume":
            # Treat queued resume as audible even before its ACK arrives.
            self._last_resume_id = command_id
            self._safe_muted = False
        self._writes.put(dict(id=command_id, command=command))
        return command_id

    def _reconcile(self):
        """Send at most one audio transition, using the latest requested mode.

        Muting includes a hardware drain and can take hundreds of milliseconds.
        Repeated play/stop actions therefore update intent while an ACK is in
        flight, instead of queuing obsolete transitions behind that drain.
        """
        if (not self.is_running or not self._ready or self.state == "stopping"
                or self._terminating or self._exit_deadline is not None):
            return
        if any(command in ("mute", "resume") for command, _ in self._commands.values()):
            return
        if self._desired_muted and not self._safe_muted:
            self._send("mute")
        elif not self._desired_muted and self._safe_muted:
            self._send("resume")

    def request_mute(self, callback):
        """Run callback only after mute+drain ACK, or after the child has exited."""
        if self._closed:
            return
        self._desired_muted = True
        if not self.is_running or (self._safe_muted and self._exit_deadline is None):
            callback()
            return
        self._mute_callbacks.append(callback)
        if self.state != "stopping" and not self._terminating and self._exit_deadline is None:
            self._reconcile()
            self._set_state(self.state, "正在暂停变声，等待预设音频播放…")

    def cancel_pending_mute(self):
        self._mute_callbacks.clear()

    def resume(self):
        """Forget pending presets and resume fresh microphone conversion."""
        self.cancel_pending_mute()
        self._desired_muted = False
        self._reconcile()

    def stop(self):
        """Request shutdown without blocking Tk; poll escalates a stuck child."""
        self._desired_muted = True
        if not self.is_running or self.state == "stopping":
            return
        self._set_state("stopping", "正在停止变声并释放显存…")
        if self._exit_deadline is None:
            self._send("stop")
            self._stop_deadline = time.monotonic() + self.stop_timeout

    def _deliver_mute_callbacks(self):
        callbacks, self._mute_callbacks = self._mute_callbacks, []
        for callback in callbacks:
            callback()

    def _handle_event(self, event):
        kind = event.get("event")
        if kind in ("devices", "devices-error"):
            self.refreshing = False
            error = event.get("message") if kind == "devices-error" else None
            if not error:
                self.devices = event["devices"]
            callback, self._device_callback = self._device_callback, None
            if callback:
                callback(self.devices if not error else [], error)
        elif kind == "ready":
            self._ready = True
            self._hardware_drain_seconds = max(self.exit_drain_seconds,
                                               float(event.get("drain_seconds", 0)))
            if self.state != "stopping":
                if self._desired_muted:
                    self._set_state("muted", "预设音频播放期间，变声已暂停")
                else:
                    self.resume()
        elif kind in ("muted", "resumed", "stopped"):
            command_id = event.get("id")
            expected = {"muted": "mute", "resumed": "resume", "stopped": "stop"}[kind]
            command = self._commands.get(command_id)
            if command is None or command[0] != expected:
                return
            del self._commands[command_id]
            if kind == "muted" and command_id > self._last_resume_id:
                self._safe_muted = True
                if self._desired_muted:
                    if self.state != "stopping":
                        self._set_state("muted", "预设音频播放期间，变声已暂停")
                    self._deliver_mute_callbacks()
            elif kind == "resumed":
                if not self._desired_muted and self.state != "stopping":
                    self._set_state("running", "正在变声；播放预设音频时会自动暂停")
            self._reconcile()
        elif kind == "process-exit":
            self._exit_code = event["returncode"]
            self._exit_deadline = time.monotonic() + self._hardware_drain_seconds
            self._commands.clear()
            self._stop_deadline = None
            self._writes.put(None)
        elif kind in ("error", "control-error"):
            self._set_state("error", event.get("message", "变声进程发生错误"))
            self._begin_termination()
        self._notifications.append(event)

    def poll(self):
        """Apply queued events and callbacks on the caller's GUI thread."""
        if self._closed:
            return []
        while True:
            try:
                generation, event = self._events.get_nowait()
            except queue.Empty:
                break
            if generation is None or generation == self._generation:
                self._handle_event(event)
        now = time.monotonic()
        if self._exit_deadline is not None and now >= self._exit_deadline:
            code = self._exit_code
            was_error = self.state == "error"
            stopped = self.state == "stopping" or code == 0
            self._process = None
            self._ready = False
            self._safe_muted = True
            self._exit_deadline = None
            if not was_error:
                self._set_state("stopped" if stopped else "error",
                                "变声已停止，显存已释放" if stopped else f"变声进程已退出（{code}）")
            self._notifications.append(dict(event="finished", returncode=code))
            self._deliver_mute_callbacks()
        if self.is_running and self._exit_deadline is None and not self._terminating:
            if self._stop_deadline is not None and now >= self._stop_deadline:
                self._begin_termination()
            elif self.state != "stopping" and any(now - sent > self.command_timeout
                    for command, sent in self._commands.values() if command in ("mute", "resume")):
                self._set_state("error", "变声控制响应超时，正在停止进程以安全切换音频")
                self._notifications.append(dict(event="error", message=self.message))
                self._begin_termination()
        notifications, self._notifications = self._notifications, []
        return notifications

    def _begin_termination(self):
        if self._process is None or self._terminating or self._exit_deadline is not None:
            return
        self._terminating = True
        self._writes.put(None)
        threading.Thread(target=self._terminate_and_wait, args=(self._process,),
                         daemon=True, name="voice-terminate").start()

    @staticmethod
    def _terminate_and_wait(process):
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.5)

    def close(self):
        """Terminate owned processes during final window teardown."""
        self._closed = True
        self.cancel_pending_mute()
        if self._writes is not None:
            self._writes.put(None)
        with self._probe_lock:
            probe = self._probe_process
        for process in (self._process, probe):
            if process is not None:
                self._terminate_and_wait(process)
        self._process = None

    shutdown = close
