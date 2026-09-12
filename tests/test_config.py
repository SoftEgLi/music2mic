"""Portable presets must work in a fresh clone and preserve personal settings."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user_config = self.root / "config.json"
        self.example = Path(config.__file__).parent / "config.example.json"
        self.example_data = json.loads(self.example.read_text(encoding="utf-8"))
        (self.root / "config.example.json").write_text(
            self.example.read_text(encoding="utf-8"), encoding="utf-8")
        config_path = patch.object(config, "CONFIG_PATH", self.user_config)
        config_path.start()
        self.addCleanup(config_path.stop)

    def test_fresh_clone_loads_portable_presets_relative_to_project_directory(self):
        cfg = config.load_config()
        self.assertEqual(len(cfg["slots"]), 10)
        for slot, example in zip(cfg["slots"], self.example_data["slots"]):
            self.assertEqual(slot["path"], str((self.root / example["path"]).resolve()))
            self.assertEqual(slot["name"], example["name"])
        self.assertEqual(cfg["voice"]["reference"],
                         str((self.root / self.example_data["voice"]["reference"]).resolve()))
        self.assertFalse(cfg["voice"]["enabled"])
        self.assertEqual(cfg["hotkeys"], {"wheel": "f8", "stop": ";", "ptt": "c"})
        self.assertFalse(self.user_config.exists())

    def test_existing_user_settings_override_example_and_preserve_absolute_paths(self):
        absolute_slot = "D:/personal/clip.mp3"
        absolute_reference = "C:\\voices\\target.wav"
        self.user_config.write_text(json.dumps({
            "slots": [{"path": absolute_slot, "name": "personal"}],
            "hotkeys": {"wheel": "f10", "ptt": "v"},
            "audio": {"volume": 0.25},
            "voice": {"enabled": True, "reference": absolute_reference},
        }), encoding="utf-8")
        cfg = config.load_config()
        self.assertEqual(cfg["slots"][0], {"path": absolute_slot, "name": "personal"})
        self.assertEqual(cfg["slots"][1], {"path": "", "name": ""})
        self.assertEqual(cfg["hotkeys"]["wheel"], "f10")
        self.assertEqual(cfg["audio"]["volume"], 0.25)
        self.assertEqual(cfg["voice"]["reference"], absolute_reference)
        self.assertTrue(cfg["voice"]["enabled"])

    def test_explicit_missing_file_keeps_selftest_defaults_without_example_fallback(self):
        cfg = config.load_config(self.user_config)
        self.assertTrue(all(slot == {"path": "", "name": ""} for slot in cfg["slots"]))
        self.assertEqual(cfg["hotkeys"], {"wheel": "f8", "stop": ";", "ptt": "v"})

    def test_explicit_relative_paths_resolve_against_that_config_file_directory(self):
        directory = self.root / "custom"
        directory.mkdir()
        file = directory / "alternate.json"
        file.write_text(json.dumps({
            "slots": [{"path": "../samples/clip.wav"}],
            "voice": {"reference": "references/target.mp3"},
        }), encoding="utf-8")
        cfg = config.load_config(file)
        self.assertEqual(cfg["slots"][0]["path"], str((self.root / "samples/clip.wav").resolve()))
        self.assertEqual(cfg["voice"]["reference"], str((directory / "references/target.mp3").resolve()))

    def test_bundled_example_references_existing_audio_files(self):
        paths = [slot["path"] for slot in self.example_data["slots"]]
        paths.append(self.example_data["voice"]["reference"])
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(Path(path).is_absolute())
                self.assertTrue((self.example.parent / path).is_file())


if __name__ == "__main__":
    unittest.main()
