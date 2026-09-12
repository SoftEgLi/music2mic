# -*- coding: utf-8 -*-
"""游戏中覆盖显示的 10 槽位转盘菜单（槽位数由 SLOT_COUNT 决定）。"""
import ctypes
import math
import tkinter as tk
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL

SLOT_COUNT = 10
_COLORS = ["#2f6f9f", "#2f9f6f", "#9f6f2f", "#9f2f6f", "#6f2f9f", "#2f9f9f",
           "#9f9f2f", "#6f9f2f", "#9f6f9f", "#2f9fd0"]
_COLOR_PLAYING = "#1c7ed6"
_COLOR_HOVER = "#ffd43b"


class WheelMenu(tk.Toplevel):
    SIZE = 540
    CX = CY = SIZE // 2
    R_OUTER = 205
    R_INNER = 110
    R_CENTER = 62

    @staticmethod
    def _step():
        return 360 // SLOT_COUNT

    def __init__(self, master, on_select, on_close, on_error=None):
        super().__init__(master)
        self.on_select = on_select
        self.on_close = on_close
        self.on_error = on_error
        self._return_window = None
        self._pending_selection = None
        self.slots = [{"name": ""} for _ in range(SLOT_COUNT)]
        self.playing_index = -1
        self.hover_index = -1

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-transparentcolor", "#010203")
        self.configure(bg="#010203")
        self.canvas = tk.Canvas(self, width=self.SIZE, height=self.SIZE,
                                bg="#010203", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.bind("<KeyPress>", self._on_key)
        self.bind("<Escape>", lambda e: self.hide())
        self.withdraw()

    # ---------- 外部接口 ----------
    def set_slots(self, slots, playing_index=-1):
        self.slots = slots
        self.playing_index = playing_index
        self._redraw()

    def show(self):
        self._cancel_selection()
        if not self.visible():
            self._return_window = _user32.GetForegroundWindow()
        w, h = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (w - self.SIZE) // 2, (h - self.SIZE) // 2
        self.geometry("+%d+%d" % (x, y))
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self._redraw()
        self.update_idletasks()
        if not self._activate_window():
            self.hide()
            if self.on_error:
                self.on_error("转盘未能获得游戏焦点，请确认程序权限后重新打开转盘。")
            return False
        # 周期性强制置顶：对抗部分窗口化/无边框全屏游戏抢回 Z 序
        self.after(150, self._keep_top)
        return True

    def _activate_window(self):
        """置顶不等于激活；必须让游戏真正失焦，解除其鼠标中心锁定。"""
        hwnd = _user32.GetAncestor(self.winfo_id(), 2)
        foreground = _user32.GetForegroundWindow()

        def activate():
            _user32.SetForegroundWindow(hwnd)
            self.focus_force()
            return _user32.GetForegroundWindow() == hwnd

        if activate():
            return True
        own_thread = _user32.GetWindowThreadProcessId(hwnd, None)
        foreground_thread = _user32.GetWindowThreadProcessId(foreground, None)
        attached = (own_thread and foreground_thread and own_thread != foreground_thread
                    and _user32.AttachThreadInput(own_thread, foreground_thread, True))
        try:
            if attached:
                activate()
        finally:
            if attached:
                _user32.AttachThreadInput(own_thread, foreground_thread, False)
        return _user32.GetForegroundWindow() == hwnd

    def _keep_top(self):
        if not self.visible():
            return
        try:
            self.lift()
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.after(150, self._keep_top)

    def hide(self):
        self._cancel_selection()
        target = self._return_window
        self._return_window = None
        # 在转盘仍为前台窗口时请求切回；先 withdraw 会让焦点落到主界面。
        if target and _user32.IsWindow(target):
            own_window = _user32.GetAncestor(self.winfo_id(), 2)
            if _user32.GetForegroundWindow() == own_window:
                _user32.SetForegroundWindow(target)
        self.withdraw()
        return target

    def _cancel_selection(self):
        if self._pending_selection is not None:
            self.after_cancel(self._pending_selection)
            self._pending_selection = None

    def _select(self, index):
        target = self.hide()
        if index == -1:
            if self.on_select:
                self.on_select(index)
            return

        def when_focused(attempt=0):
            self._pending_selection = None
            if target and _user32.GetForegroundWindow() == target:
                if self.on_select:
                    self.on_select(index)
            elif target and _user32.IsWindow(target) and attempt < 25:
                # 前台窗口切换可能异步完成；停止或再次打开转盘会取消此等待。
                self._pending_selection = self.after(20, lambda: when_focused(attempt + 1))
            elif self.on_error:
                self.on_error("未能切回原窗口，已取消选歌；请切回游戏后重新打开转盘选择。")

        self._pending_selection = self.after_idle(when_focused)

    def visible(self):
        try:
            return bool(self.winfo_viewable())
        except Exception:
            return False

    # ---------- 绘制 ----------
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        c.create_rectangle(0, 0, self.SIZE, self.SIZE, fill="#010203", outline="")
        c.create_text(self.CX, 28, text="选择要播放的音乐", fill="#ced4da",
                      font=("Microsoft YaHei", 12, "bold"))
        c.create_text(self.CX, 52, text="按 1-9、0 选择，Esc 关闭", fill="#868e96",
                      font=("Microsoft YaHei", 9))
        step = self._step()
        for i in range(SLOT_COUNT):
            a0 = -(90 + step // 2) + i * step
            if i == self.playing_index:
                fill = _COLOR_PLAYING
            elif i == self.hover_index:
                fill = _COLOR_HOVER
            else:
                fill = _COLORS[i % len(_COLORS)]
            c.create_arc(self.CX - self.R_OUTER, self.CY - self.R_OUTER,
                         self.CX + self.R_OUTER, self.CY + self.R_OUTER,
                         start=a0, extent=step, style="pieslice", fill=fill,
                         outline="#f1f3f5", width=2,
                         tags=("sec", "sec%d" % i))
            name = self.slots[i].get("name") or ("插槽 %d" % (i + 1))
            if i == self.playing_index:
                name = "▶ " + name
            x, y = self._label_pos(i)
            c.create_text(x, y, text=name, fill="#ffffff",
                          font=("Microsoft YaHei", 9, "bold"),
                          width=96, justify="center")
        c.create_oval(self.CX - self.R_CENTER, self.CY - self.R_CENTER,
                      self.CX + self.R_CENTER, self.CY + self.R_CENTER,
                      fill="#343a40", outline="#ff6b6b", width=3,
                      tags=("center",))
        c.create_text(self.CX, self.CY - 6, text="⏹", fill="#ff6b6b",
                      font=("Segoe UI Symbol", 26, "bold"))
        c.create_text(self.CX, self.CY + 26, text="停止", fill="#ffffff",
                      font=("Microsoft YaHei", 10, "bold"))

    def _label_pos(self, i):
        """扇区 i 的真实视觉中心角（顺时针，3 点钟=0°），与 Tk 扇区绘制一致。"""
        step = self._step()
        ang = math.radians((90 - step * i) % 360)
        r = (self.R_INNER + self.R_OUTER) / 2
        return self.CX + r * math.cos(ang), self.CY + r * math.sin(ang)

    # ---------- 交互 ----------
    def _hit(self, x, y):
        dx, dy = x - self.CX, y - self.CY
        r = math.hypot(dx, dy)
        if r <= self.R_CENTER:
            return -1  # 中央停止键
        if r > self.R_OUTER or r < self.R_INNER:
            return None
        # “画在哪里就命中哪里”：直接按 canvas 扇区 tag 判定，不做角度换算，避免偏差
        ids = self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        for iid in ids:
            for t in self.canvas.gettags(iid):
                if t.startswith("sec") and t != "sec":
                    return int(t[3:])
        return None

    def _on_motion(self, event):
        hit = self._hit(event.x, event.y)
        idx = hit if (hit is not None and hit >= 0) else -1
        if idx != self.hover_index:
            self.hover_index = idx
            self._redraw()

    def _on_click(self, event):
        hit = self._hit(event.x, event.y)
        if hit is None:
            return
        self._select(hit)

    def _on_key(self, event):
        ch = event.char
        if ch and ch in "1234567890":
            idx = 9 if ch == "0" else int(ch) - 1
            if 0 <= idx < SLOT_COUNT:
                self._select(idx)
        elif event.keysym == "Escape":
            self.hide()
            if self.on_close:
                self.on_close()
