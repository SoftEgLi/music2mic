# -*- coding: utf-8 -*-
"""Music2Mic 启动入口。

用法:
    python main.py            启动图形界面
    python main.py --selftest 运行自检（不启动界面）
"""
import os
import sys
import tempfile

from app_paths import app_root

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def selftest():
    ok = True

    def t(name, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS  " if cond else "FAIL  ") + name + (("  " + extra) if extra else ""))

    # 0) 模块导入
    try:
        from config import SLOT_COUNT, load_config, save_config
        from hotkeys import key_matches, key_to_text, parse_key
        from player import AudioPlayer, list_devices
        from pynput import keyboard
        import gui  # noqa: F401
        import wheel  # noqa: F401
        import ptt  # noqa: F401
        t("全部模块可导入", True)
    except Exception as e:
        t("全部模块可导入", False, repr(e))
        print("=" * 40)
        print("SELFTEST 结果: FAIL")
        sys.exit(1)

    # 1) 配置读写
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "config.json")
        cfg = load_config(p)
        t("配置默认 %d 插槽" % SLOT_COUNT, len(cfg["slots"]) == SLOT_COUNT, "slots=%d" % len(cfg["slots"]))
        t("配置默认快捷键",
          cfg["hotkeys"] == {"wheel": "f8", "stop": ";", "ptt": "v"},
          str(cfg["hotkeys"]))
        cfg["slots"][0]["path"] = "C:/music/test.mp3"
        cfg["hotkeys"]["wheel"] = "f9"
        save_config(cfg, p)
        cfg2 = load_config(p)
        t("配置保存/读取",
          cfg2["slots"][0]["path"] == "C:/music/test.mp3"
          and cfg2["hotkeys"]["wheel"] == "f9")

    # 2) 快捷键解析与匹配
    t("解析 ;", parse_key(";") is not None)
    t("匹配 ; (字符)", key_matches(keyboard.KeyCode(char=";"), ";"))
    t("匹配 ; (VK)", key_matches(keyboard.KeyCode(vk=186), ";"))
    t("匹配 F8", key_matches(keyboard.Key.f8, "f8"))
    t("匹配 V", key_matches(keyboard.KeyCode(char="v"), "v"))
    t("按键转文本",
      key_to_text(keyboard.Key.f8) == "f8"
      and key_to_text(keyboard.KeyCode(char="v")) == "v")

    # 3) 输出设备枚举
    devs = list_devices()
    print("      输出设备列表: %s" % devs)
    t("设备枚举返回列表", isinstance(devs, list))

    # 4) 音频初始化（默认输出设备；无声卡环境仅告警）
    try:
        pl = AudioPlayer({"audio": {"device": "", "volume": 0.8}})
        print("      音频初始化信息: %s" % pl.message)
        t("默认输出设备初始化", pl.message.startswith("音频已就绪"))
        pl.shutdown()
    except Exception as e:
        t("默认输出设备初始化", False, repr(e))

    print("=" * 40)
    print("SELFTEST 结果: %s" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


def prepare_runtime():
    """Keep a frozen launch's working files beside its EXE, not its extraction."""
    if getattr(sys, "frozen", False):
        from multiprocessing import freeze_support
        freeze_support()
        os.chdir(app_root())


def relaunch_as_admin(arguments):
    """Match launcher.bat's normal elevation while leaving QA noninteractive."""
    if (not getattr(sys, "frozen", False) or sys.platform != "win32"
            or any(flag in arguments for flag in ("--no-admin", "--selftest", "--release-smoke-report"))):
        return False
    import ctypes
    from ctypes import wintypes
    import subprocess

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    is_admin = shell32.IsUserAnAdmin
    is_admin.argtypes = []
    is_admin.restype = wintypes.BOOL
    if is_admin():
        return False
    execute = shell32.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                       wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = ctypes.c_ssize_t
    # The elevated one-file EXE must unpack its own runtime because this parent
    # exits immediately and its temporary bundle will be removed.
    previous_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        result = execute(None, "runas", sys.executable, subprocess.list2cmdline(arguments),
                         str(app_root()), 1)
    finally:
        if previous_reset is None:
            os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        else:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = previous_reset
    if result <= 32:
        raise OSError(result, "未能以管理员身份启动。请允许权限提示，或使用 --no-admin 以普通权限打开。")
    return True


def _show_startup_error(message):
    import ctypes
    from ctypes import wintypes

    show = ctypes.WinDLL("user32", use_last_error=True).MessageBoxW
    show.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    show.restype = ctypes.c_int
    show(None, message, "Music2Mic 启动失败", 0x10)


def main():
    from config import load_config
    from gui import Music2MicApp

    cfg = load_config()
    app = Music2MicApp(cfg)
    app.mainloop()


def entrypoint(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    prepare_runtime()
    if "--release-smoke-report" in arguments:
        index = arguments.index("--release-smoke-report")
        if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
            _show_startup_error("--release-smoke-report 后需要指定报告文件路径。")
            return 2
        from release_smoke import run_smoke
        return run_smoke(arguments[index + 1])
    if "--selftest" in arguments:
        selftest()
        return 0
    try:
        if relaunch_as_admin(arguments):
            return 0
    except OSError as exc:
        _show_startup_error(str(exc))
        return 1
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
