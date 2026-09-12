# -*- coding: utf-8 -*-
"""主图形界面：编辑插槽、显示名称、快捷键、输出设备与音量。"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import SLOT_COUNT, save_config
from hotkeys import HotkeyManager, key_to_text
from player import AudioPlayer, list_devices
from ptt import PTTController
from wheel import WheelMenu
from voice_control import VoiceController

FONT = "Microsoft YaHei"
BG = "#f1f3f5"


class Music2MicApp(tk.Tk):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.title("Music2Mic — 预设音频与实时变声")
        self.geometry("840x880")
        self.minsize(840, 880)
        self.configure(bg=BG)

        self.player = AudioPlayer(cfg)
        self.ptt = PTTController(cfg)
        self.voice = VoiceController()
        self._play_serial = 0
        self._preset_pending = False
        self._closing = False
        self._voice_device_choices = {}
        self._voice_notice = ""
        self._restart_voice_after_stop = False
        self._voice_auto_start = bool(cfg.get("voice", {}).get("enabled", False))
        self.wheel = WheelMenu(self, on_select=self._on_wheel_select,
                               on_close=self._on_wheel_close,
                               on_error=self._set_status)
        self.hotkeys = HotkeyManager(cfg, on_wheel=self._on_hotkey_wheel,
                                     on_stop=self._on_hotkey_stop,
                                     on_any=self._on_any_key)
        self.hotkeys.start()
        self.after(1200, self._check_hotkey)

        self._capture_target = None
        self.slot_vars = []
        self.slot_name_vars = []
        self.slot_btns = []
        self.hotkey_vars = {}
        self.capture_btns = {}
        self.status_var = tk.StringVar(value="正在启动…")
        self.device_var = tk.StringVar()
        self.volume_var = tk.DoubleVar()
        self.auto_ptt_var = tk.BooleanVar()
        self.loop_var = tk.BooleanVar()
        self.voice_enabled_var = tk.BooleanVar(value=self._voice_auto_start)
        self.voice_reference_var = tk.StringVar(value=cfg.get("voice", {}).get("reference", ""))
        self.voice_input_var = tk.StringVar()
        self.voice_status_var = tk.StringVar(value="变声未开启")
        self.voice_output_var = tk.StringVar()

        self._build_ui()
        self._load_to_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(400, self._poll)
        self.after(300, lambda: self._set_status(self.player.message))
        self.after(100, self._poll_voice)
        self.after(500, self._refresh_voice_devices)
        save_config(cfg)

    # ---------- 界面 ----------
    def _build_ui(self):
        hint = ("用法：安装 VB-Cable 后，把“输出设备”设为 Cable Input，"
                "再把 Windows 默认麦克风设为 Cable Output。游戏内按转盘键弹出菜单，"
                "选择后自动按住说话键（PTT）播放音乐，按停止键结束。")
        tk.Label(self, text=hint, bg="#ffe8cc", fg="#9c4221", anchor="w",
                 justify="left", wraplength=760, padx=8, pady=6,
                 font=(FONT, 9)).pack(fill="x", padx=12, pady=(12, 6))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=6)
        preset_tab = tk.Frame(self.tabs, bg=BG)
        voice_tab = tk.Frame(self.tabs, bg=BG)
        self.tabs.add(preset_tab, text="预设音频与快捷键")
        self.tabs.add(voice_tab, text="实时变声")
        self._build_slots(preset_tab)
        self._build_settings(preset_tab)
        self._build_voice_settings(voice_tab)
        tk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken",
                 bg="#e9ecef", padx=8, font=(FONT, 9)).pack(side="bottom", fill="x")

    def _build_slots(self, parent):
        box = tk.LabelFrame(parent, text=(" ① 音乐插槽（转盘 %d 格，名称可选填，如“鼓励”“安慰”） " % SLOT_COUNT),
                            bg=BG, font=(FONT, 10, "bold"))
        box.pack(fill="x", padx=12, pady=4)
        for i in range(SLOT_COUNT):
            row = tk.Frame(box, bg=BG)
            row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text="%d" % (i + 1), width=2, bg=BG,
                     font=(FONT, 10, "bold")).pack(side="left")
            nvar = tk.StringVar()
            self.slot_name_vars.append(nvar)
            tk.Entry(row, textvariable=nvar, width=8, font=(FONT, 9),
                     justify="center").pack(side="left", padx=(0, 4))
            nvar.trace_add("write", lambda *a, i=i: self._on_slot_name(i))
            var = tk.StringVar()
            self.slot_vars.append(var)
            tk.Entry(row, textvariable=var, font=("Consolas", 9)).pack(
                side="left", fill="x", expand=True, padx=4)
            tk.Button(row, text="浏览…", width=7,
                      command=lambda i=i: self._browse(i)).pack(side="left")
            btn = tk.Button(row, text="▶", width=3,
                            command=lambda i=i: self._toggle_test(i))
            btn.pack(side="left", padx=(4, 0))
            self.slot_btns.append(btn)

    def _build_settings(self, parent):
        box = tk.LabelFrame(parent, text=" ② 快捷键与音频 ", bg=BG,
                            font=(FONT, 10, "bold"))
        box.pack(fill="x", padx=12, pady=4)

        hk = tk.Frame(box, bg=BG)
        hk.pack(fill="x", padx=8, pady=(6, 2))
        for key, label in (("wheel", "打开/关闭转盘"),
                           ("stop", "停止播放"),
                           ("ptt", "游戏说话键 (PTT)")):
            row = tk.Frame(hk, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, width=18, anchor="w", bg=BG,
                     font=(FONT, 9)).pack(side="left")
            var = tk.StringVar()
            self.hotkey_vars[key] = var
            tk.Entry(row, textvariable=var, state="readonly", width=8,
                     justify="center", font=("Consolas", 11, "bold")).pack(
                side="left", padx=4)
            btn = tk.Button(row, text="修改", width=6,
                            command=lambda k=key: self._start_capture(k))
            btn.pack(side="left")
            self.capture_btns[key] = btn

        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(row, text="输出设备", width=18, anchor="w", bg=BG,
                 font=(FONT, 9)).pack(side="left")
        self.device_combo = ttk.Combobox(row, textvariable=self.device_var,
                                         state="readonly", width=46)
        self.device_combo.pack(side="left", padx=4)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)
        tk.Label(row, text="（选 VB-Cable 的输入端）", bg=BG, fg="#868e96",
                 font=(FONT, 8)).pack(side="left")

        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(row, text="音量", width=18, anchor="w", bg=BG,
                 font=(FONT, 9)).pack(side="left")
        self.volume_scale = tk.Scale(row, from_=0, to=100, orient="horizontal",
                                     variable=self.volume_var, showvalue=True,
                                     bg=BG, highlightthickness=0,
                                     command=self._on_volume)
        self.volume_scale.pack(side="left", fill="x", expand=True, padx=4)

        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=8, pady=(2, 8))
        tk.Label(row, text="自动按住说话", width=18, anchor="w", bg=BG,
                 font=(FONT, 9)).pack(side="left")
        tk.Checkbutton(row, variable=self.auto_ptt_var, bg=BG,
                       text="播放音乐时自动按住游戏说话键",
                       command=self._on_auto_ptt,
                       font=(FONT, 9)).pack(side="left")
        row2 = tk.Frame(box, bg=BG)
        row2.pack(fill="x", padx=8, pady=(2, 8))
        tk.Label(row2, text="循环播放", width=18, anchor="w", bg=BG,
                 font=(FONT, 9)).pack(side="left")
        tk.Checkbutton(row2, variable=self.loop_var, bg=BG,
                       text="单曲循环，持续播放并持续按住说话键",
                       command=self._on_loop,
                       font=(FONT, 9)).pack(side="left")

    def _load_to_ui(self):
        for i in range(SLOT_COUNT):
            self.slot_vars[i].set(self.cfg["slots"][i].get("path", ""))
            self.slot_name_vars[i].set(self.cfg["slots"][i].get("name", ""))
        for key in ("wheel", "stop", "ptt"):
            self.hotkey_vars[key].set(self.cfg["hotkeys"].get(key, ""))
        devices = list_devices()
        self._devices = devices
        self.device_combo.configure(values=["（系统默认输出设备）"] + devices)
        dev = self.cfg["audio"].get("device", "")
        if dev:
            for d in devices:
                dl, dd = d.lower(), dev.lower()
                if dl == dd or dd in dl or dl in dd:
                    dev = d
                    break
        self.device_var.set(dev if dev in devices else "（系统默认输出设备）")
        self.volume_var.set(int(self.cfg["audio"].get("volume", 0.8) * 100))
        self.auto_ptt_var.set(self.cfg["audio"].get("auto_ptt", True))
        self.loop_var.set(self.cfg["audio"].get("loop", False))
        self.voice_output_var.set("与预设共用输出：" + (self.cfg["audio"].get("device") or "请先选择 CABLE Input"))

    def _build_voice_settings(self, parent):
        box = tk.LabelFrame(parent, text=" GPU 实时变声 ", bg=BG, font=(FONT, 11, "bold"))
        box.pack(fill="both", expand=True, padx=8, pady=10)
        tk.Label(box, text="播放预设音频时自动静音变声；结束或停止预设后自动恢复。\n"
                          "只打开 F8 转盘不会静音。循环播放时，变声保持静音直到预设停止。",
                 bg=BG, justify="left", anchor="w", font=(FONT, 10), wraplength=720).pack(
                     fill="x", padx=12, pady=12)
        tk.Checkbutton(box, text="启用实时变声（下次启动保留此选择）", variable=self.voice_enabled_var,
                       command=self._on_voice_enabled, bg=BG, font=(FONT, 10)).pack(anchor="w", padx=12)
        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=12, pady=(16, 6))
        tk.Label(row, text="目标音色参考", bg=BG, width=13, anchor="w", font=(FONT, 10)).pack(side="left")
        self.voice_reference_entry = tk.Entry(row, textvariable=self.voice_reference_var, font=(FONT, 9))
        self.voice_reference_entry.pack(side="left", fill="x", expand=True)
        self.voice_browse_button = tk.Button(row, text="选择音频", command=self._browse_voice_reference)
        self.voice_browse_button.pack(side="left", padx=(8, 0))
        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=12, pady=6)
        tk.Label(row, text="输入麦克风", bg=BG, width=13, anchor="w", font=(FONT, 10)).pack(side="left")
        self.voice_input_combo = ttk.Combobox(row, textvariable=self.voice_input_var, state="readonly")
        self.voice_input_combo.pack(side="left", fill="x", expand=True)
        self.voice_refresh_button = tk.Button(row, text="刷新设备", command=self._refresh_voice_devices)
        self.voice_refresh_button.pack(side="left", padx=(8, 0))
        tk.Label(box, textvariable=self.voice_output_var, bg=BG, anchor="w", font=(FONT, 9)).pack(
            fill="x", padx=12, pady=8)
        tk.Label(box, text="游戏麦克风选择 CABLE Output。需要自己试听时请用耳机，避免外放回授。\n"
                          "变声时仍使用游戏自己的说话键；预设音频沿用自动按住说话键的设置。",
                 bg=BG, justify="left", anchor="w", wraplength=720, fg="#555", font=(FONT, 9)).pack(
                     fill="x", padx=12, pady=6)
        tk.Label(box, textvariable=self.voice_status_var, anchor="w", bg=BG, fg="#245b97",
                 font=(FONT, 10, "bold")).pack(fill="x", padx=12, pady=12)
        self.voice_log = tk.Text(box, height=13, state="disabled", wrap="word", bg="#fff", font=(FONT, 9))
        self.voice_log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _browse_voice_reference(self):
        path = filedialog.askopenfilename(title="选择目标音色参考", filetypes=[("音频文件", "*.wav *.mp3 *.flac *.ogg")])
        if path:
            self.voice_reference_var.set(path)
            self.cfg["voice"]["reference"] = path
            save_config(self.cfg)

    def _refresh_voice_devices(self):
        if self.voice.can_start and self.voice.discover_devices():
            self._voice_notice = "正在读取麦克风设备…"

    def _set_voice_devices(self, devices):
        self._voice_notice = ""
        previous = self.cfg["voice"].get("input_name", "")
        physical = [d for d in devices if d["max_input_channels"] and
                    not any(word in d["name"].lower() for word in ("cable", "stereo mix", "立体声混音"))]
        physical.sort(key=lambda d: (d["name"] != previous if previous else "usb" not in d["name"].lower(),
                                     d["hostapi_name"] != self.cfg["voice"].get("hostapi", "Windows WASAPI")))
        self._voice_device_choices = {
            f"{d['name']}  [{d['hostapi_name']}]": d for d in physical}
        self.voice_input_combo.configure(values=list(self._voice_device_choices))
        current = self.voice_input_var.get()
        if current not in self._voice_device_choices:
            self.voice_input_var.set(next(iter(self._voice_device_choices), ""))
        if self._voice_auto_start:
            self._voice_auto_start = False
            self._start_voice()

    def _on_voice_enabled(self):
        enabled = self.voice_enabled_var.get()
        self._voice_notice = ""
        self.cfg["voice"]["enabled"] = enabled
        save_config(self.cfg)
        if enabled:
            if self.voice.refreshing:
                self._voice_auto_start = True
            elif not self.voice.can_start:
                self._restart_voice_after_stop = self.voice.state in ("stopping", "error")
            else:
                self._start_voice()
        else:
            self._voice_auto_start = False
            self._restart_voice_after_stop = False
            self.voice.stop()
        self._sync_voice_status()

    def _start_voice(self):
        if not self.voice.can_start:
            return
        self._voice_notice = ""
        incoming = self._voice_device_choices.get(self.voice_input_var.get())
        desired_output = self.player.device_in_use.lower()
        outputs = [d for d in self.voice.devices if incoming and d["max_output_channels"]
                   and d["hostapi"] == incoming["hostapi"] and desired_output]
        matches = [d for d in outputs if d["name"].lower() == desired_output]
        if not matches:
            matches = [d for d in outputs if desired_output in d["name"].lower()
                       or d["name"].lower() in desired_output]
        outgoing = matches[0] if len(matches) == 1 else None
        if incoming is None or outgoing is None:
            self.voice_enabled_var.set(False)
            self.cfg["voice"]["enabled"] = False
            save_config(self.cfg)
            self._voice_notice = "请刷新麦克风，并在预设页选择可用的 CABLE Input 等具体输出设备。"
            self._sync_voice_status()
            return
        reference = self.voice_reference_var.get().strip()
        self.cfg["voice"].update(reference=reference, input_name=incoming["name"], hostapi=incoming["hostapi_name"])
        self.cfg["voice"]["enabled"] = True
        save_config(self.cfg)
        muted = self._preset_pending or self.player.playing_path is not None
        if not self.voice.start(reference, incoming, outgoing, initially_muted=muted):
            self.voice_enabled_var.set(False)
            self.cfg["voice"]["enabled"] = False
            save_config(self.cfg)
        self._sync_voice_status()

    def _sync_voice_status(self):
        state = self.voice.state
        if self._voice_notice:
            text = self._voice_notice
        elif self._preset_pending and state in ("running", "muted"):
            text = "正在清空变声缓冲，准备播放预设…"
        elif state == "muted":
            text = "预设播放中，变声已静音" if self.player.playing_path is not None else "正在恢复麦克风变声…"
        elif state == "running":
            text = "正在变声"
        else:
            text = self.voice.message
        self.voice_status_var.set(text)
        running = self.voice.is_running
        self.voice_reference_entry.configure(state="disabled" if running else "normal")
        self.voice_browse_button.configure(state="disabled" if running else "normal")
        self.voice_input_combo.configure(state="disabled" if running else "readonly")
        self.voice_refresh_button.configure(state="disabled" if running or self.voice.refreshing else "normal")

    def _poll_voice(self):
        if self._closing:
            return
        for event in self.voice.poll():
            if event.get("event") == "devices":
                self._set_voice_devices(event["devices"])
            if event.get("event") == "devices-error":
                self._voice_notice = event.get("message", "无法读取麦克风设备")
                self._voice_auto_start = False
            if event.get("event") in ("finished", "devices-error") and not self._restart_voice_after_stop:
                self.voice_enabled_var.set(False)
                self.cfg["voice"]["enabled"] = False
                save_config(self.cfg)
            if event.get("event") in ("log", "error", "devices-error"):
                message = event.get("message", "")
                self.voice_log.configure(state="normal")
                self.voice_log.insert("end", message + ("" if message.endswith("\n") else "\n"))
                if int(self.voice_log.index("end-1c").split(".")[0]) > 250:
                    self.voice_log.delete("1.0", "80.0")
                self.voice_log.see("end")
                self.voice_log.configure(state="disabled")
        if self._restart_voice_after_stop and self.voice.can_start:
            self._restart_voice_after_stop = False
            if self.voice_enabled_var.get():
                self._voice_auto_start = True
                self._refresh_voice_devices()
        self._sync_voice_status()
        self.after(50, self._poll_voice)

    # ---------- 插槽操作 ----------
    def _on_slot_name(self, i):
        self.cfg["slots"][i]["name"] = self.slot_name_vars[i].get().strip()
        save_config(self.cfg)
        self._update_wheel()

    def _browse(self, i):
        p = filedialog.askopenfilename(
            title="选择音乐（插槽 %d）" % (i + 1),
            filetypes=[("音频文件", "*.mp3 *.wav *.ogg *.flac"),
                       ("所有文件", "*.*")])
        if p:
            self.slot_vars[i].set(p)
            self.cfg["slots"][i]["path"] = p
            save_config(self.cfg)
            self._set_status("已设置插槽 %d：%s" % (i + 1, os.path.basename(p)))

    def _toggle_test(self, i):
        if self.player.playing_index == i:
            self._stop_all()
        else:
            self._play(i)

    def _play(self, i):
        path = self.cfg["slots"][i].get("path", "").strip()
        self._play_serial = getattr(self, "_play_serial", 0) + 1
        serial = self._play_serial
        self._preset_pending = True
        self._set_status("正在静音变声，准备播放预设…")
        voice = getattr(self, "voice", None)
        if voice is None:
            Music2MicApp._begin_play(self, i, path, serial)
        else:
            voice.cancel_pending_mute()
            voice.request_mute(lambda: self._begin_play(i, path, serial))

    def _begin_play(self, i, path, serial):
        if serial != self._play_serial or getattr(self, "_closing", False):
            return
        self._preset_pending = False
        self._idle_count = 0
        self.player.stop()
        # 游戏失焦后可能清空按键状态；每次选歌都重新发送一对松开/按下。
        if not self.ptt.release():
            self._set_status("说话键松开失败：" + self.ptt.error)
            if getattr(self, "voice", None):
                self.voice.resume()
            self._refresh_buttons()
            self._update_wheel()
            return
        ok, msg = self.player.play(i, path)
        if ok:
            auto = self.cfg["audio"].get("auto_ptt", True)
            if auto:
                if self.ptt.hold():
                    msg += "（说话键按下请求已发送）"
                else:
                    msg += "（自动说话失败：%s）" % self.ptt.error
            self._set_status(msg)
        else:
            self.player.stop()
            if getattr(self, "voice", None):
                self.voice.resume()
            messagebox.showwarning("播放失败", msg)
            self._set_status(msg)
        self._refresh_buttons()
        self._update_wheel()

    def _stop_all(self):
        self._play_serial = getattr(self, "_play_serial", 0) + 1
        self._preset_pending = False
        self.player.stop()
        released = self.ptt.release()
        if getattr(self, "voice", None):
            self.voice.resume()
        self._set_status("已停止播放" if released else "已停止播放；说话键松开失败：" + self.ptt.error)
        self._refresh_buttons()
        self._update_wheel()

    # ---------- 转盘 ----------
    def _on_wheel_select(self, i):
        if i == -1:
            self._stop_all()
        else:
            self._play(i)

    def _on_wheel_close(self):
        pass

    def _on_hotkey_wheel(self):
        self.after(0, self._toggle_wheel)

    def _on_hotkey_stop(self):
        def do():
            self.wheel.hide()
            self._stop_all()
        self.after(0, do)

    def _toggle_wheel(self):
        if self.wheel.visible():
            self.wheel.hide()
        else:
            self._update_wheel(force=True)
            if self.wheel.show():
                self._set_status("转盘已打开（按 1-9、0 选择，Esc 关闭）")

    def _update_wheel(self, force=False):
        if force or self.wheel.visible():
            names = []
            for i in range(SLOT_COUNT):
                p = self.cfg["slots"][i].get("path", "").strip()
                nm = self.cfg["slots"][i].get("name", "").strip()
                if not nm:
                    nm = os.path.basename(p) if p else ("插槽 %d" % (i + 1))
                names.append({"name": nm})
            self.wheel.set_slots(names, self.player.playing_index)

    def _refresh_buttons(self):
        for i, btn in enumerate(self.slot_btns):
            btn.configure(text="⏹" if self.player.playing_index == i else "▶")

    # ---------- 快捷键修改 ----------
    def _start_capture(self, target):
        if self._capture_target is not None:
            self.capture_btns[self._capture_target].configure(text="修改", bg=BG)
        self._capture_target = target
        self.capture_btns[target].configure(text="请按新按键…", bg="#ffe066")

    def _on_any_key(self, key):
        if self._capture_target is None:
            return
        t = key_to_text(key)
        if t.startswith("vk") or t in ("shift", "ctrl", "alt", "cmd"):
            return
        target = self._capture_target
        self._capture_target = None

        def apply():
            self.cfg["hotkeys"][target] = t
            self.hotkey_vars[target].set(t)
            self.capture_btns[target].configure(text="修改", bg=BG)
            save_config(self.cfg)
            label = {"wheel": "打开/关闭转盘", "stop": "停止播放",
                     "ptt": "游戏说话键"}.get(target, target)
            self._set_status("快捷键【%s】已设为 %s" % (label, t))
        self.after(0, apply)

    # ---------- 设备 / 音量 / PTT ----------
    def _on_device_change(self, event=None):
        # 两种音频共用输出；切换设备时停止当前预设并重启变声端。
        self._stop_all()
        restart_voice = self.voice_enabled_var.get()
        self._restart_voice_after_stop = restart_voice
        if self.voice.is_running:
            self.voice.stop()
        sel = self.device_var.get()
        dev = "" if sel == "（系统默认输出设备）" else sel
        self.cfg["audio"]["device"] = dev
        self.player.device = dev
        self.player.init_mixer()
        save_config(self.cfg)
        self._set_status(self.player.message)
        self.voice_output_var.set("与预设共用输出：" + (dev or "请先选择具体输出设备"))

    def _on_volume(self, val):
        v = float(val) / 100.0
        self.cfg["audio"]["volume"] = v
        self.player.set_volume(v)
        save_config(self.cfg)

    def _on_auto_ptt(self):
        self.cfg["audio"]["auto_ptt"] = bool(self.auto_ptt_var.get())
        if not self.cfg["audio"]["auto_ptt"]:
            self.ptt.release()
        save_config(self.cfg)

    def _on_loop(self):
        self.cfg["audio"]["loop"] = bool(self.loop_var.get())
        save_config(self.cfg)
        # 正在播放时立即按新设置重开当前曲目，让循环即时生效
        if self.player.playing_path is not None:
            self._play(self.player.playing_index)

    # ---------- 状态 / 轮询 / 退出 ----------
    def _check_hotkey(self):
        l = self.hotkeys._listener
        if l is None or not getattr(l, "running", False):
            self._set_status("警告：热键监听未启动，请右键 launcher.bat → 以管理员身份运行")
    def _set_status(self, text):
        self.status_var.set(text)

    def _poll(self):
        if self.player.playing_path is not None and not self.player.is_playing():
            self._idle_count = getattr(self, "_idle_count", 0) + 1
            if self._idle_count >= 2:  # 连续两次未播放才判定结束，避免瞬时波动中断
                self._idle_count = 0
                self.player.stop()
                self.ptt.release()
                if not getattr(self, "_preset_pending", False):
                    self.voice.resume()
                self._set_status("播放结束")
                self._refresh_buttons()
                self._update_wheel()
        else:
            self._idle_count = 0
        self.after(400, self._poll)

    def _on_close(self):
        self._closing = True
        self._play_serial += 1
        self.hotkeys.stop()
        self.ptt.release()
        self.player.shutdown()
        self.voice.close()
        self.destroy()
