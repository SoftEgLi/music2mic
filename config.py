# -*- coding: utf-8 -*-
"""配置读写：config.json 保存插槽、快捷键与音频设置。"""
import json
from pathlib import Path, PureWindowsPath

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

SLOT_COUNT = 10

DEFAULT_CONFIG = {
    "slots": [{"path": "", "name": ""} for _ in range(SLOT_COUNT)],
    "hotkeys": {
        "wheel": "f8",   # 打开/关闭转盘
        "stop": ";",     # 停止播放
        "ptt": "v",      # 游戏内的说话键
    },
    "audio": {
        "device": "",    # 输出设备名（留空=系统默认输出设备）
        "volume": 0.8,
        "auto_ptt": True,
        "loop": False,
    },
    "voice": {
        "enabled": False,
        "reference": str(Path(__file__).resolve().parent / "musics" / "好厉害啊哥哥.mp3"),
        "input_name": "",
        "hostapi": "Windows WASAPI",
    },
}


def _merge(default, data):
    out = dict(default)
    for k, v in (data or {}).items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif isinstance(out[k], list) and isinstance(v, list) and v:
            out[k] = v
        else:
            out[k] = v
    return out


def _resolve_audio_path(value, directory):
    """Resolve portable preset paths without rewriting existing absolute paths."""
    if not value or not isinstance(value, str):
        return value
    audio_path = Path(value)
    if audio_path.is_absolute() or PureWindowsPath(value).is_absolute():
        return value
    return str((directory / audio_path).resolve())


def load_config(path=None):
    p = Path(path) if path is not None else CONFIG_PATH
    # A fresh clone uses the checked-in presets. Explicit paths retain their
    # default-only behavior when missing (including the self-test's temp file).
    if path is None and not p.exists():
        example = p.with_name("config.example.json")
        if example.is_file():
            p = example
    cfg = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cfg = _merge(cfg, data)
        except Exception:
            pass
    slots = cfg["slots"]
    if len(slots) < SLOT_COUNT:
        slots += [{"path": "", "name": ""} for _ in range(SLOT_COUNT - len(slots))]
    # 统一归一化：每个插槽都保证存在 path/name 键（兼容旧版本无 name 的配置）
    cfg["slots"] = [
        {"path": _resolve_audio_path(s.get("path", ""), p.parent), "name": s.get("name", "")}
        if isinstance(s, dict) else {"path": "", "name": ""}
        for s in slots[:SLOT_COUNT]
    ]
    cfg["voice"]["reference"] = _resolve_audio_path(cfg["voice"]["reference"], p.parent)
    return cfg


def save_config(cfg, path=None):
    p = Path(path) if path else CONFIG_PATH
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
