# -*- coding: utf-8 -*-
"""音频播放：把音乐输出到指定设备（如 VB-CABLE 输入端），供游戏麦克风使用。"""
import ctypes
import os
import winreg
from ctypes import wintypes

import pygame


class _WAVEOUTCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", ctypes.c_wchar * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


_RENDER_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
_PKEY_FRIENDLY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_PKEY_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


def _sdl_device_names():
    """SDL 音频子系统就绪时枚举输出设备（完整名）。"""
    from pygame._sdl2 import audio as sdl2_audio
    return list(sdl2_audio.get_audio_device_names(False))


def _registry_full_name(name):
    """按注册表把配置里的设备名（友好名）拼成 SDL 完整名 "友好名 (驱动描述)"。

    不依赖 SDL 音频子系统，即使默认播放设备被其它程序独占（如游戏）也能得到
    可用的完整设备名。
    """
    low = name.lower().strip()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RENDER_KEY) as k:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, guid + r"\Properties") as pk:
                        fn, _ = winreg.QueryValueEx(pk, _PKEY_FRIENDLY)
                        desc, _ = winreg.QueryValueEx(pk, _PKEY_DESC)
                except OSError:
                    continue
                if not fn:
                    continue
                fl = fn.lower().strip()
                if fl == low or low in fl or fl in low:
                    return "%s (%s)" % (fn, desc)
    except Exception:
        pass
    return None


def list_devices():
    """返回系统输出设备名列表（SDL 完整名）。

    注意：不要破坏调用方正在使用的 mixer——若 mixer 已在运行则直接枚举，
    否则临时初始化再恢复原状。winmm 仅作回退（名字会被截断，仅供参考）。
    """
    was_init = pygame.mixer.get_init() is not None
    try:
        if not was_init:
            pygame.mixer.init()
        try:
            names = _sdl_device_names()
            if names:
                return names
        finally:
            if not was_init:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        n = ctypes.windll.winmm.waveOutGetNumDevs()
        devs = []
        for i in range(n):
            caps = _WAVEOUTCAPS()
            if ctypes.windll.winmm.waveOutGetDevCapsW(
                    i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                devs.append(caps.szPname.strip())
        return devs
    except Exception:
        return []


class AudioPlayer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.device = cfg["audio"].get("device", "")
        self.volume = cfg["audio"].get("volume", 0.8)
        self.playing_path = None
        self.playing_index = -1
        self.device_in_use = ""
        self.message = ""
        self.init_mixer()

    @staticmethod
    def _resolve_device(name):
        """按子串匹配出 SDL 枚举到的精确设备名（需在 SDL 音频已初始化时调用）。"""
        try:
            names = _sdl_device_names()
            low = name.lower()
            for n in names:
                nl = n.lower()
                if nl == low or low in nl or nl in low:
                    return n
        except Exception:
            pass
        return name

    def init_mixer(self):
        self.device_in_use = ""
        # 关掉可能残留的旧 mixer
        try:
            pygame.mixer.quit()
        except Exception:
            pass

        # 1) 尝试拉起 SDL 音频子系统（默认设备可能被游戏独占，失败也不影响按设备名初始化）
        sdl_up = False
        try:
            pygame.mixer.init()
            sdl_up = True
        except Exception:
            pass

        # 2) 收集候选设备名：SDL 精确名 -> 注册表完整名 -> 配置原文 -> 基础名 -> 默认
        candidates = []
        if self.device:
            if sdl_up:
                try:
                    r = self._resolve_device(self.device)
                    if r and r not in candidates:
                        candidates.append(r)
                except Exception:
                    pass
            try:
                r = _registry_full_name(self.device)
                if r and r not in candidates:
                    candidates.append(r)
            except Exception:
                pass
            if self.device not in candidates:
                candidates.append(self.device)
            base = self.device.split(" (")[0].strip()
            if base and base not in candidates:
                candidates.append(base)

        # 3) 关掉临时的默认 mixer，再用候选设备正式初始化
        if sdl_up:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
        candidates.append("")
        seen = set()
        last = None
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            try:
                if name:
                    pygame.mixer.init(frequency=44100, size=-16,
                                      channels=2, buffer=2048, devicename=name)
                else:
                    pygame.mixer.init()
                self.device_in_use = name
                self.message = "音频已就绪（%s）" % (name if name else "系统默认输出设备")
                return True
            except Exception as e:
                last = e
        self.message = ("音频设备初始化失败：%s"
                        "（请确认已安装 VB-CABLE 或声卡驱动，且设备未被其它程序独占）" % last)
        return False

    def play(self, index, path):
        if not path or not os.path.isfile(path):
            return False, "文件不存在，请先在插槽中选择音乐文件"
        if pygame.mixer.get_init() is None:
            self.init_mixer()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(max(0.0, min(1.0, self.volume)))
            loops = -1 if self.cfg["audio"].get("loop", False) else 0
            pygame.mixer.music.play(loops=loops)
            self.playing_index = index
            self.playing_path = path
            return True, "播放中：" + os.path.basename(path)
        except pygame.error as e:
            return False, "无法播放该文件：%s（请使用 mp3/wav/ogg/flac）" % e
        except Exception as e:
            return False, "播放失败：%s" % e

    def stop(self):
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self.playing_index = -1
        self.playing_path = None

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        try:
            pygame.mixer.music.set_volume(self.volume)
        except Exception:
            pass

    def is_playing(self):
        try:
            return pygame.mixer.music.get_busy()
        except Exception:
            return False

    def shutdown(self):
        self.stop()
        try:
            pygame.mixer.quit()
        except Exception:
            pass
