"""Frozen subprocess launches isolate DLL/Python paths and restore the GUI."""

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

import voice_control
from voice_control import VoiceController


class FrozenSubprocessTests(unittest.TestCase):
    def frozen(self):
        for setting in (patch.object(voice_control.sys, "frozen", True, create=True),
                        patch.object(voice_control.sys, "platform", "win32"),
                        patch.object(voice_control.sys, "_MEIPASS", r"C:\Temp\_MEI123", create=True)):
            setting.start()
            self.addCleanup(setting.stop)

    def test_frozen_child_environment_removes_bundle_paths_without_changing_parent(self):
        self.frozen()
        environment = {
            "PATH": r'C:\Temp\_MEI123;"c:\temp\_mei123\pygame";C:\Temp\_MEI123-other;D:\CUDA\bin;C:\Windows\System32',
            "PYTHONHOME": r"C:\Temp\_MEI123",
            "PYTHONPATH": r"C:\development\custom-imports",
            "UNRELATED_SETTING": "preserved",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
                "voice_control._get_windows_dll_directory", return_value=r"C:\GUI-DLLs"), patch(
                "voice_control._set_windows_dll_directory") as setter, patch(
                "voice_control.subprocess.Popen") as popen:
            VoiceController._popen([r"D:\Music2Mic\voice-changer\runtime\python.exe"], "D:\\Music2Mic")
            self.assertEqual(dict(os.environ), environment)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["PATH"], r"C:\Temp\_MEI123-other;D:\CUDA\bin;C:\Windows\System32")
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["UNRELATED_SETTING"], "preserved")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual([call.args[0] for call in setter.call_args_list], [None, r"C:\GUI-DLLs"])

    def test_failed_process_creation_still_restores_original_dll_directory(self):
        self.frozen()
        with patch("voice_control._get_windows_dll_directory", return_value=r"C:\Actual-DLLs"), patch(
                "voice_control._set_windows_dll_directory") as setter, patch(
                "voice_control.subprocess.Popen", side_effect=OSError("cannot launch")):
            with self.assertRaisesRegex(OSError, "cannot launch"):
                VoiceController._popen(["missing-python.exe"], ".")
        self.assertEqual([call.args[0] for call in setter.call_args_list], [None, r"C:\Actual-DLLs"])

    def test_concurrent_launches_both_inherit_clean_directory_and_restore_parent(self):
        self.frozen()
        directory = [r"C:\GUI-DLLs"]
        barrier = threading.Barrier(2)
        inherited = []

        def create(*args, **kwargs):
            inherited.append(directory[0])
            time.sleep(0.03)
            return Mock()

        def launch():
            barrier.wait(timeout=2)
            return VoiceController._popen(["python.exe"], ".")

        with patch("voice_control._get_windows_dll_directory", side_effect=lambda: directory[0]), patch(
                "voice_control._set_windows_dll_directory", side_effect=lambda value: directory.__setitem__(0, value)), patch(
                "voice_control.subprocess.Popen", side_effect=create), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(launch) for _ in range(2)]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(inherited, [None, None])
        self.assertEqual(directory[0], r"C:\GUI-DLLs")

    def test_restore_failure_cleans_up_child_instead_of_losing_its_handle(self):
        self.frozen()
        process = Mock()
        with patch("voice_control._get_windows_dll_directory", return_value=r"C:\GUI-DLLs"), patch(
                "voice_control._set_windows_dll_directory", side_effect=[None, OSError("restore failed")]), patch(
                "voice_control.subprocess.Popen", return_value=process), patch.object(
                VoiceController, "_terminate_and_wait") as terminate:
            with self.assertRaisesRegex(OSError, "restore failed"):
                VoiceController._popen(["python.exe"], ".")
        terminate.assert_called_once_with(process)

    def test_development_launch_preserves_python_paths_and_avoids_windows_api(self):
        environment = {"PATH": r"C:\Dev\bin", "PYTHONHOME": r"C:\Dev", "PYTHONPATH": "custom"}
        with patch.object(voice_control.sys, "frozen", False, create=True), patch.dict(
                os.environ, environment, clear=True), patch("voice_control._get_windows_dll_directory") as getter, patch(
                "voice_control.subprocess.Popen") as popen:
            VoiceController._popen(["python.exe"], ".")
        getter.assert_not_called()
        env = popen.call_args.kwargs["env"]
        for name, value in environment.items():
            self.assertEqual(env[name], value)


if __name__ == "__main__":
    unittest.main()
