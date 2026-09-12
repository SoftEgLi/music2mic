"""Packaged startup elevates ordinary launches and keeps release QA unattended."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main
import release_smoke


class MainStartupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("C:/Music2Mic portable")
        self.shell = SimpleNamespace(IsUserAnAdmin=Mock(return_value=False),
                                     ShellExecuteW=Mock(return_value=42))
        for setting in (patch.object(main.sys, "frozen", True, create=True),
                        patch.object(main.sys, "platform", "win32"),
                        patch.object(main.sys, "executable", str(self.root / "Music2Mic.exe")),
                        patch("main.app_root", return_value=self.root),
                        patch("ctypes.WinDLL", return_value=self.shell)):
            setting.start()
            self.addCleanup(setting.stop)

    def test_elevation_preserves_spaced_quoted_arguments_and_uses_fresh_bundle(self):
        arguments = ["--label", 'voice "one"', "C:\\folder with spaces\\"]
        inherited_reset = []
        self.shell.ShellExecuteW.side_effect = lambda *args: (
            inherited_reset.append(os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")) or 42)
        with patch.dict(os.environ, {"PYINSTALLER_RESET_ENVIRONMENT": "previous"}):
            self.assertTrue(main.relaunch_as_admin(arguments))
            self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "previous")
        self.shell.ShellExecuteW.assert_called_once_with(
            None, "runas", str(self.root / "Music2Mic.exe"), subprocess.list2cmdline(arguments),
            str(self.root), 1)
        self.assertEqual(inherited_reset, ["1"])

    def test_cancelled_uac_is_reported_without_starting_gui(self):
        self.shell.ShellExecuteW.return_value = 5
        with patch("main.prepare_runtime"), patch("main.main") as start_gui, patch(
                "main._show_startup_error") as error:
            self.assertEqual(main.entrypoint([]), 1)
        start_gui.assert_not_called()
        self.assertIn("--no-admin", error.call_args.args[0])

    def test_already_elevated_process_does_not_relaunch(self):
        self.shell.IsUserAnAdmin.return_value = True
        self.assertFalse(main.relaunch_as_admin([]))
        self.shell.ShellExecuteW.assert_not_called()

    def test_qa_and_no_admin_switches_never_request_elevation(self):
        for arguments in (["--no-admin"], ["--selftest"], ["--release-smoke-report", "report.json"]):
            with self.subTest(arguments=arguments):
                self.assertFalse(main.relaunch_as_admin(arguments))
        self.shell.IsUserAnAdmin.assert_not_called()
        self.shell.ShellExecuteW.assert_not_called()

    def test_source_startup_keeps_existing_launcher_elevation_behavior(self):
        with patch.object(main.sys, "frozen", False):
            self.assertFalse(main.relaunch_as_admin([]))
        self.shell.IsUserAnAdmin.assert_not_called()

    def test_smoke_entry_dispatches_report_path_without_gui_or_uac(self):
        report = "C:\\release reports\\结果.json"
        with patch("main.prepare_runtime"), patch("release_smoke.run_smoke", return_value=0) as smoke, patch(
                "main.main") as start_gui:
            self.assertEqual(main.entrypoint(["--release-smoke-report", report]), 0)
        smoke.assert_called_once_with(report)
        start_gui.assert_not_called()
        self.shell.ShellExecuteW.assert_not_called()


class ReleaseSmokeReportTests(unittest.TestCase):
    def test_startup_asset_failure_still_writes_report_without_opening_gui(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_file = root / "reports" / "smoke.json"
            with patch("release_smoke.app_root", return_value=root), patch("gui.Music2MicApp") as app:
                self.assertEqual(release_smoke.run_smoke(report_file), 1)
            app.assert_not_called()
            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertIn("bundled example config exists", report["failures"][0])
            self.assertEqual(report["report_path"], str(report_file))
            self.assertFalse((root / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
