# -*- coding: utf-8 -*-
"""全局快捷键监听与按键文本解析。"""
import ctypes

from pynput import keyboard

_NAMED_KEYS = {
    "space": keyboard.Key.space,
    "enter": keyboard.Key.enter,
    "esc": keyboard.Key.esc,
    "tab": keyboard.Key.tab,
    "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "insert": keyboard.Key.insert,
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "page_up": keyboard.Key.page_up,
    "page_down": keyboard.Key.page_down,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "shift": keyboard.Key.shift_l,
    "ctrl": keyboard.Key.ctrl_l,
    "alt": keyboard.Key.alt_l,
    "caps_lock": keyboard.Key.caps_lock,
    "num_lock": keyboard.Key.num_lock,
    "scroll_lock": keyboard.Key.scroll_lock,
    "print_screen": keyboard.Key.print_screen,
    "pause": keyboard.Key.pause,
    "menu": keyboard.Key.menu,
}
for _i in range(1, 25):
    _NAMED_KEYS["f%d" % _i] = getattr(keyboard.Key, "f%d" % _i)


def _vk_for_char(char):
    """用 Windows VkKeyScanW 取字符对应虚拟键码（与真实按键一致，兼容布局差异）。"""
    try:
        return ctypes.windll.user32.VkKeyScanW(char) & 0xFF
    except Exception:
        return None


def parse_key(text):
    """把配置文本转成 pynput 按键对象。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in _NAMED_KEYS:
        return _NAMED_KEYS[t]
    if len(t) == 1:
        try:
            kc = keyboard.KeyCode.from_char(t)
        except Exception:
            kc = None
        vk = _vk_for_char(t)
        if vk and (kc is None or kc.vk is None):
            try:
                kc = keyboard.KeyCode(char=t, vk=vk)
            except Exception:
                pass
        return kc
    return None


def key_matches(key, text):
    """判断监听到的按键是否等于配置文本。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    if isinstance(key, keyboard.KeyCode):
        if key.char and key.char.lower() == t:
            return True
        if len(t) == 1:
            vk = _vk_for_char(t)
            if vk and key.vk and vk == key.vk:
                return True
        return False
    return str(key).lower() == "key." + t


def key_to_text(key):
    """把按键对象转成可保存的文本。"""
    if isinstance(key, keyboard.KeyCode):
        if key.char and len(key.char) == 1:
            return key.char
        return "vk%d" % (key.vk or 0)
    return str(key).split(".")[-1]


class HotkeyManager:
    """全局按键监听。回调运行在监听线程中，GUI 侧需用 after() 转回主线程。"""

    def __init__(self, cfg, on_wheel=None, on_stop=None, on_any=None):
        self.cfg = cfg
        self.on_wheel = on_wheel
        self.on_stop = on_stop
        self.on_any = on_any
        self._listener = None

    def start(self):
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _on_press(self, key):
        if self.on_any is not None:
            self.on_any(key)
        hk = self.cfg.get("hotkeys", {})
        if self.on_wheel and key_matches(key, hk.get("wheel", "")):
            self.on_wheel()
        if self.on_stop and key_matches(key, hk.get("stop", "")):
            self.on_stop()