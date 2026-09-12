"""Check actual SendInput packets without injecting keys into the desktop."""
import ctypes
import unittest
from unittest.mock import patch

import ptt


class PTTTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"hotkeys": {"ptt": "c"}, "audio": {"auto_ptt": True}}
        self.ctrl = ptt.PTTController(self.cfg)
        self.events = []
        self.send_result = 1

        def send(count, pointer, size):
            event = ctypes.cast(pointer, ctypes.POINTER(ptt._INPUT)).contents
            self.events.append((event.type, event.value.ki.wVk, event.value.ki.wScan,
                                event.value.ki.dwFlags, count, size))
            return self.send_result

        for patcher in (patch.object(ptt._user32, "SendInput", side_effect=send),
                        patch.object(ptt, "_record_event")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_c_down_and_up_use_physical_scan_code(self):
        self.assertTrue(self.ctrl.hold())
        self.assertTrue(self.ctrl.held)
        self.assertTrue(self.ctrl.release())
        self.assertFalse(self.ctrl.held)
        size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(self.events, [(1, 0, 0x2E, 0x08, 1, size),
                                       (1, 0, 0x2E, 0x0A, 1, size)])

    def test_failed_insertion_does_not_mark_key_held(self):
        self.send_result = 0
        self.assertFalse(self.ctrl.hold())
        self.assertFalse(self.ctrl.held)
        self.assertIn("Windows", self.ctrl.error)

    def test_release_uses_original_key_after_config_change(self):
        self.ctrl.hold()
        self.cfg["hotkeys"]["ptt"] = "v"
        self.ctrl.release()
        self.assertEqual([event[2] for event in self.events], [0x2E, 0x2E])

    def test_release_failure_can_be_retried(self):
        self.ctrl.hold()
        self.send_result = 0
        self.assertFalse(self.ctrl.release())
        self.assertTrue(self.ctrl.held)
        self.send_result = 1
        self.assertTrue(self.ctrl.release())
        self.assertFalse(self.ctrl.held)
        self.assertEqual([event[3] for event in self.events], [0x08, 0x0A, 0x0A])

    def test_hold_is_idempotent_until_released(self):
        self.ctrl.hold()
        self.ctrl.hold()
        self.assertEqual(len(self.events), 1)

    def test_disabled_auto_ptt_does_not_send(self):
        self.cfg["audio"]["auto_ptt"] = False
        self.assertFalse(self.ctrl.hold())
        self.assertTrue(self.ctrl.release())
        self.assertFalse(self.events)

    def test_invalid_key_reports_error_without_sending(self):
        self.cfg["hotkeys"]["ptt"] = "not_a_key"
        self.assertFalse(self.ctrl.hold())
        self.assertTrue(self.ctrl.error)
        self.assertFalse(self.events)

    def test_extended_key_preserves_extended_flag(self):
        self.cfg["hotkeys"]["ptt"] = "right"
        self.assertTrue(self.ctrl.hold())
        self.assertTrue(self.ctrl.release())
        self.assertEqual([event[2:4] for event in self.events], [(0x4D, 0x09), (0x4D, 0x0B)])


if __name__ == "__main__":
    unittest.main()
