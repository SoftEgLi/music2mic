"""A packaged launcher must use its install directory and portable CUDA Python."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_paths
import main
from voice_control import VoiceController


class AppPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.install = self.directory / "Music2Mic portable"
        self.install.mkdir()
        self.executable = self.install / "Music2Mic.exe"
        self.source_root = Path(app_paths.__file__).resolve().parent

    def frozen(self):
        """Make imports observe an EXE beside assets, away from _MEIPASS."""
        for setting in (patch.object(app_paths.sys, "frozen", True, create=True),
                        patch.object(app_paths.sys, "executable", str(self.executable)),
                        patch.object(app_paths.sys, "_MEIPASS", str(self.directory / "_MEI123"), create=True)):
            setting.start()
            self.addCleanup(setting.stop)

    def load_module(self, filename):
        spec = importlib.util.spec_from_file_location("path_test_" + filename, self.source_root / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_source_root_is_independent_of_python_executable(self):
        with patch.object(app_paths.sys, "frozen", False, create=True), patch.object(
                app_paths.sys, "executable", str(self.executable)):
            self.assertEqual(app_paths.app_root(), self.source_root)

    def test_frozen_root_uses_executable_directory_instead_of_extraction(self):
        self.frozen()
        self.assertEqual(app_paths.app_root(), self.install)

    def test_frozen_configuration_and_reference_live_beside_executable(self):
        self.frozen()
        configuration = self.load_module("config.py")
        self.assertEqual(configuration.CONFIG_PATH, self.install / "config.json")
        self.assertEqual(configuration.DEFAULT_CONFIG["voice"]["reference"],
                         str(self.install / "musics" / "好厉害啊哥哥.mp3"))
        (self.install / "config.example.json").write_text(json.dumps({
            "slots": [{"path": "musics/preset.mp3", "name": "Preset"}],
            "voice": {"reference": "musics/reference.mp3"},
        }), encoding="utf-8")
        cfg = configuration.load_config()
        self.assertEqual(cfg["slots"][0]["path"], str(self.install / "musics" / "preset.mp3"))
        self.assertEqual(cfg["voice"]["reference"], str(self.install / "musics" / "reference.mp3"))
        configuration.save_config(cfg)
        self.assertTrue((self.install / "config.json").is_file())

    def test_frozen_ptt_log_is_written_beside_executable(self):
        self.frozen()
        ptt_module = self.load_module("ptt.py")
        self.assertEqual(ptt_module.LOG_PATH, self.install / "ptt.log")

    def test_frozen_controller_prefers_bundled_python_to_legacy_venv(self):
        self.frozen()
        portable = self.install / "voice-changer" / "runtime" / "python.exe"
        portable.parent.mkdir(parents=True)
        portable.touch()
        legacy = self.install / "voice-changer" / ".venv" / "Scripts" / "python.exe"
        legacy.parent.mkdir(parents=True)
        legacy.touch()
        controller = VoiceController()
        self.assertEqual(controller.root, self.install)
        self.assertEqual(controller.python, portable)
        self.assertEqual(controller.runner, self.install / "voice-changer" / "tools" / "run_voice_changer.py")

    def test_explicit_controller_root_and_python_override_frozen_location(self):
        self.frozen()
        explicit_root = self.directory / "custom-root"
        controller = VoiceController(explicit_root)
        self.assertEqual(controller.root, explicit_root)
        self.assertEqual(controller.python, explicit_root / "voice-changer" / ".venv" / "Scripts" / "python.exe")
        executable = self.directory / "custom-python.exe"
        controller = VoiceController(explicit_root, python_executable=executable)
        self.assertEqual(controller.python, executable)

    def test_frozen_bootstrap_sets_working_directory_after_multiprocessing_guard(self):
        self.frozen()
        actions = []
        with patch("multiprocessing.freeze_support", side_effect=lambda: actions.append("freeze")), patch(
                "main.os.chdir", side_effect=lambda path: actions.append(path)):
            main.prepare_runtime()
        self.assertEqual(actions, ["freeze", self.install])

    def test_source_bootstrap_preserves_callers_working_directory(self):
        with patch.object(app_paths.sys, "frozen", False, create=True), patch("main.os.chdir") as chdir:
            main.prepare_runtime()
        chdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
