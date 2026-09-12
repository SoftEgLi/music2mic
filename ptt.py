# -*- coding: utf-8 -*-
"""PTT（按住说话）模拟：播放音乐时自动按住游戏说话键。"""
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pynput import keyboard

from hotkeys import parse_key

LOG_PATH = Path(__file__).resolve().with_name("ptt.log")


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    # Windows 的 INPUT 是联合体；只声明键盘字段会令 64 位结构体大小错误。
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUT_UNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
_user32.MapVirtualKeyW.restype = wintypes.UINT
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD


def _record_event(vk, scan, flags, sent, error):
    """仅记录本程序发送的 PTT 事件，用于区分发送失败和游戏未响应。"""
    logger = logging.getLogger("music2mic.ptt")
    try:
        if not logger.handlers:
            handler = RotatingFileHandler(LOG_PATH, maxBytes=262144, backupCount=1,
                                          encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        pid = wintypes.DWORD()
        hwnd = _user32.GetForegroundWindow()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        logger.info("vk=%s scan=0x%02X flags=0x%02X sent=%s error=%s foreground_pid=%s hwnd=%s",
                    vk, scan, flags, sent, error, pid.value, hwnd)
    except OSError:
        pass  # 日志文件不可写时仍允许正常收发按键。


class PTTController:
    def __init__(self, cfg):
        self.cfg = cfg
        self.held = False
        self.error = ""
        self._held_key = None

    def key(self):
        return parse_key(self.cfg.get("hotkeys", {}).get("ptt", ""))

    def _key_event(self):
        key = self.key()
        if isinstance(key, keyboard.Key):
            key = key.value
        vk = getattr(key, "vk", None)
        if not vk or not 1 <= vk <= 254:
            raise ValueError("说话键无效，请重新设置一个键盘按键")
        scan = _user32.MapVirtualKeyW(vk, 4)  # MAPVK_VK_TO_VSC_EX
        if not scan:
            raise ValueError("无法识别说话键的扫描码，请重新设置说话键")
        if scan >> 8 == 0xE1:
            raise ValueError("此按键不支持持续按住，请改用 C、V 等按键")
        flags = 0x0008  # KEYEVENTF_SCANCODE：按键动作，不是字符输入。
        # 部分键盘布局不返回 E0 前缀，保留 pynput 命名键的扩展标记。
        if scan >> 8 == 0xE0 or (getattr(key, "_flags", 0) or 0) & 0x0001:
            flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY
        if vk == 0x2C:  # VK_SNAPSHOT：MapVirtualKey 可能返回 SysRq 的 0x54。
            scan = 0x37
            flags |= 0x0001
        return vk, scan & 0xFF, flags

    def _send(self, key, release=False):
        vk, scan, flags = key
        if release:
            flags |= 0x0002  # KEYEVENTF_KEYUP
        event = _INPUT(type=1, value=_INPUT_UNION(ki=_KEYBDINPUT(wScan=scan, dwFlags=flags)))
        ctypes.set_last_error(0)
        sent = _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
        error = ctypes.get_last_error() if sent != 1 else 0
        _record_event(vk, scan, flags, sent, error)
        if sent != 1:
            detail = ctypes.FormatError(error).strip() if error else "请确认程序权限和目标窗口状态"
            raise OSError("Windows 未接受说话键事件：" + detail)

    def hold(self):
        self.error = ""
        if not self.cfg.get("audio", {}).get("auto_ptt", True):
            return False
        if self.held:
            return True
        try:
            key = self._key_event()
            self._send(key)
            self._held_key = key
            self.held = True
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def release(self):
        if not self.held:
            return True
        try:
            # 配置在播放期间可能改变，必须松开当初实际按下的那个键。
            self._send(self._held_key, release=True)
            self.held = False
            self._held_key = None
            self.error = ""
            return True
        except Exception as exc:
            self.error = str(exc)
            return False  # 保留按住状态，允许停止操作再次尝试松开。
