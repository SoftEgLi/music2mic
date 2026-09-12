"""Small Windows launcher for the local MeanVC2 CUDA streaming runner."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "tools" / "run_voice_changer.py"
DEFAULT_REFERENCE = ROOT.parent / "musics" / "好厉害啊哥哥.mp3"


def preferred_devices(devices):
    """Prefer a USB microphone and CABLE render endpoint on the same audio API."""
    inputs = [d for d in devices if d["max_input_channels"] > 0]
    outputs = [d for d in devices if d["max_output_channels"] > 0]
    physical = [d for d in inputs if "cable" not in d["name"].lower()
                and not any(s in d["name"].lower() for s in ("stereo mix", "立体声混音"))]

    def api_rank(device):
        api = device["hostapi_name"].lower()
        return 0 if "wasapi" in api else 1 if "directsound" in api else 2 if api == "mme" else 3

    def mic_rank(device):
        name = device["name"].lower()
        return (0 if "usb2.0" in name else 1 if "麦克风" in name or "microphone" in name else 2,
                api_rank(device))

    cable_outputs = sorted([d for d in outputs if "cable input" in d["name"].lower()], key=api_rank)
    for output in cable_outputs:
        candidates = sorted([d for d in physical if d["hostapi"] == output["hostapi"]], key=mic_rank)
        if candidates:
            return candidates[0]["index"], output["index"]
    # Missing CABLE should leave output blank so the user deliberately chooses a route.
    input_id = sorted(physical, key=mic_rank)[0]["index"] if physical else None
    return input_id, cable_outputs[0]["index"] if cable_outputs else None


class VoiceChangerApp:
    def __init__(self, window):
        self.window = window
        self.events = queue.Queue()
        self.devices = []
        self.input_choices = {}
        self.output_choices = {}
        self.process = None
        self.stop_file = None
        self.report_file = None
        self.stopping = False
        self.closing = False
        self.refreshing = False
        self.reference = tk.StringVar(value=str(DEFAULT_REFERENCE))
        self.input_choice = tk.StringVar()
        self.output_choice = tk.StringVar()
        self.status = tk.StringVar(value="正在读取音频设备…")
        window.title("实时变声器 · MeanVC2 GPU")
        window.geometry("880x660")
        window.minsize(750, 520)
        window.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style()
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=5)

        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="GPU 实时变声", font=("Microsoft YaHei UI", 17, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 15))
        ttk.Label(frame, text="目标音色参考").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
        self.reference_entry = ttk.Entry(frame, textvariable=self.reference)
        self.reference_entry.grid(row=1, column=1, sticky="ew", pady=6)
        self.browse_button = ttk.Button(frame, text="选择音频", command=self.browse)
        self.browse_button.grid(row=1, column=2, padx=(8, 0), pady=6)
        ttk.Label(frame, text="输入麦克风").grid(row=2, column=0, sticky="w", pady=6)
        self.input_box = ttk.Combobox(frame, textvariable=self.input_choice, state="readonly")
        self.input_box.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(frame, text="变声输出").grid(row=3, column=0, sticky="w", pady=6)
        self.output_box = ttk.Combobox(frame, textvariable=self.output_choice, state="readonly")
        self.output_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(frame, text="游戏中将语音输入选为 CABLE Output，并使用游戏说话键。\n"
                              "建议戴耳机。首次开始时需要等待模型加载，停止后会释放显存。",
                  foreground="#505866", wraplength=760).grid(
                      row=4, column=0, columnspan=3, sticky="w", pady=(10, 12))

        controls = ttk.Frame(frame)
        controls.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="开始变声", command=self.start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.refresh_button = ttk.Button(controls, text="刷新设备", command=self.refresh_devices)
        self.refresh_button.pack(side="left")
        ttk.Label(frame, textvariable=self.status, foreground="#245b97").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.log = ScrolledText(frame, height=15, wrap="word", state="disabled",
                                font=("Consolas", 10), background="#f6f7f9", relief="flat")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.window.after(100, self.poll_events)
        self.window.after_idle(self.refresh_devices)

    def write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 2500:
            self.log.delete("1.0", f"{line_count - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def browse(self):
        selected = filedialog.askopenfilename(
            title="选择要模仿的音色参考", initialdir=str(DEFAULT_REFERENCE.parent),
            filetypes=[("音频文件", "*.wav *.mp3 *.flac *.ogg *.m4a"), ("所有文件", "*.*")])
        if selected:
            self.reference.set(selected)

    def refresh_devices(self):
        if self.process is not None or self.refreshing:
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("正在读取音频设备…")

        def discover():
            try:
                import sounddevice as sd
                # PortAudio caches its list; refresh while no streams are running.
                sd._terminate()
                sd._initialize()
                apis = sd.query_hostapis()
                found = [{**dict(d), "index": i, "hostapi_name": apis[d["hostapi"]]["name"]}
                         for i, d in enumerate(sd.query_devices())]
                self.events.put(("devices", found))
            except Exception as exc:
                self.events.put(("devices-error", str(exc)))

        threading.Thread(target=discover, daemon=True).start()

    def set_devices(self, devices):
        old_in = self.input_choices.get(self.input_choice.get())
        old_out = self.output_choices.get(self.output_choice.get())
        self.devices = devices
        self.input_choices = {}
        self.output_choices = {}
        for device in devices:
            label = f'{device["index"]}: {device["name"]}  [{device["hostapi_name"]}]'
            if device["max_input_channels"]:
                self.input_choices[label] = device
            if device["max_output_channels"]:
                self.output_choices[label] = device
        self.input_box["values"] = list(self.input_choices)
        self.output_box["values"] = list(self.output_choices)
        default_in, default_out = preferred_devices(devices)
        for mapping, old, default, variable in (
                (self.input_choices, old_in, default_in, self.input_choice),
                (self.output_choices, old_out, default_out, self.output_choice)):
            previous = next((label for label, d in mapping.items() if old
                             and d["name"] == old["name"] and d["hostapi_name"] == old["hostapi_name"]), None)
            preferred = next((label for label, d in mapping.items() if d["index"] == default), "")
            variable.set(previous or preferred)
        self.refreshing = False
        self.refresh_button.configure(state="normal")
        self.start_button.configure(state="normal")
        self.status.set("准备就绪" if default_out is not None else "未找到 CABLE Input，请检查虚拟声卡后刷新设备")

    def set_controls_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.refresh_button.configure(state="disabled" if running else "normal")
        self.browse_button.configure(state="disabled" if running else "normal")
        self.reference_entry.configure(state="disabled" if running else "normal")
        self.input_box.configure(state="disabled" if running else "readonly")
        self.output_box.configure(state="disabled" if running else "readonly")

    def start(self):
        if self.process is not None:
            return
        reference = Path(self.reference.get().strip()).expanduser()
        incoming = self.input_choices.get(self.input_choice.get())
        outgoing = self.output_choices.get(self.output_choice.get())
        if not reference.is_file():
            messagebox.showerror("参考音频不存在", "请选择一个存在的目标音色参考文件。")
            return
        if incoming is None or outgoing is None:
            messagebox.showerror("请选择音频设备", "需要选择输入麦克风和变声输出设备。")
            return
        if incoming["hostapi"] != outgoing["hostapi"]:
            messagebox.showerror("音频接口不匹配", "请选择方括号内接口名称相同的输入与输出，例如两者都是 Windows WASAPI。")
            return
        if "cable" in incoming["name"].lower() and "cable" in outgoing["name"].lower():
            messagebox.showerror("请选择实体麦克风", "输入与输出都选择 CABLE 会形成回路。请把输入改为你的实体麦克风。")
            return
        if not RUNNER.is_file():
            messagebox.showerror("缺少运行程序", f"未找到：{RUNNER}")
            return
        session = ROOT / "reports" / ("gui-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        try:
            session.mkdir(parents=True)
            self.stop_file = session / "stop.request"
            self.report_file = session / "live-report.json"
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            command = [str(executable), "-u", str(RUNNER), "live", "--reference", str(reference.resolve()),
                       "--input-device", str(incoming["index"]), "--output-device", str(outgoing["index"]),
                       "--stop-file", str(self.stop_file), "--report", str(self.report_file)]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            self.process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            encoding="utf-8", errors="replace", bufsize=1,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            self.process = None
            messagebox.showerror("启动失败", str(exc))
            return
        self.stopping = False
        self.set_controls_running(True)
        self.status.set("正在加载模型；等待日志显示音频流已启动…")
        self.write_log(f"\n开始：{reference.name}\n输入：{incoming['name']}\n输出：{outgoing['name']}\n")
        threading.Thread(target=self.read_process, args=(self.process,), daemon=True).start()

    def read_process(self, process):
        try:
            for line in process.stdout:
                self.events.put(("log", line))
        finally:
            process.stdout.close()
            code = process.wait()
            self.events.put(("finished", (process, code)))

    def stop(self):
        process = self.process
        if process is None or self.stopping:
            return
        self.stopping = True
        self.stop_button.configure(state="disabled")
        self.status.set("正在停止并释放音频设备与显存…")
        try:
            self.stop_file.touch()
        except OSError as exc:
            self.write_log(f"写入停止请求失败：{exc}\n")
            process.terminate()
        # A loading/erroring model may not reach its cooperative stop check.
        self.window.after(12000, lambda: self.terminate_if_needed(process))

    def terminate_if_needed(self, process):
        if self.process is process and process.poll() is None:
            self.write_log("程序尚未响应停止，正在结束进程以释放设备…\n")
            process.terminate()
            self.window.after(2000, lambda: self.kill_if_needed(process))

    def kill_if_needed(self, process):
        if self.process is process and process.poll() is None:
            process.kill()

    def close(self):
        self.closing = True
        if self.process is not None:
            self.stop()
        else:
            self.window.destroy()

    def poll_events(self):
        for _ in range(250):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.write_log(payload)
                if not self.stopping and any(s in payload.lower() for s in ("[stream] running", "[live] running", "stream started", "音频流已启动")):
                    self.status.set("正在变声 · CS2 语音输入请选择 CABLE Output")
            elif kind == "devices":
                self.set_devices(payload)
            elif kind == "devices-error":
                self.refreshing = False
                self.refresh_button.configure(state="normal")
                self.status.set("设备读取失败，请查看日志并刷新")
                self.write_log(f"读取音频设备失败：{payload}\n")
            elif kind == "finished":
                process, code = payload
                if self.process is process:
                    was_stopping = self.stopping
                    self.process = None
                    self.stopping = False
                    if self.closing:
                        self.window.destroy()
                        return
                    self.set_controls_running(False)
                    self.status.set("已停止，音频设备与显存已释放" if was_stopping or code == 0
                                    else f"运行失败（退出码 {code}），请查看日志")
                    self.write_log(f"进程结束，退出码：{code}\n")
                    if self.report_file and self.report_file.is_file():
                        self.write_log(f"运行报告：{self.report_file}\n")
        self.window.after(100, self.poll_events)


if __name__ == "__main__":
    root = tk.Tk()
    VoiceChangerApp(root)
    root.mainloop()
