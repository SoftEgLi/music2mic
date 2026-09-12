"""Regression tests for the real wheel selection and focus restoration path."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import wheel


class WheelFocusTests(unittest.TestCase):
    def setUp(self):
        self.native_patch = patch.object(wheel, "_user32")
        self.native = self.native_patch.start()
        self.addCleanup(self.native_patch.stop)
        self.native.GetForegroundWindow.return_value = 200
        self.native.GetAncestor.return_value = 200
        self.native.IsWindow.return_value = True
        self.menu = wheel.WheelMenu.__new__(wheel.WheelMenu)
        self.menu._return_window = 100
        self.menu._pending_selection = None
        self.menu.on_select = Mock()
        self.menu.on_close = Mock()
        self.menu.on_error = Mock()
        self.menu.winfo_id = Mock(return_value=201)
        self.menu.withdraw = Mock()
        self.pending = {}
        self.next_id = 0

        def schedule(callback):
            self.next_id += 1
            self.pending[self.next_id] = callback
            return self.next_id

        self.menu.after_idle = schedule
        self.menu.after = lambda delay, callback: schedule(callback)
        self.menu.after_cancel = lambda timer: self.pending.pop(timer, None)

    def tick(self):
        timer = next(iter(self.pending))
        self.pending.pop(timer)()

    def select_first(self):
        self.menu._on_key(SimpleNamespace(char="1", keysym="1"))

    def prepare_show(self):
        self.native.GetForegroundWindow.return_value = 100
        self.native.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: 10 if hwnd == 100 else 20
        self.menu.visible = Mock(return_value=False)
        self.menu.winfo_screenwidth = Mock(return_value=1920)
        self.menu.winfo_screenheight = Mock(return_value=1080)
        for name in ("geometry", "deiconify", "lift", "attributes", "focus_force",
                     "_redraw", "update_idletasks"):
            setattr(self.menu, name, Mock())

    def test_show_makes_wheel_the_actual_foreground_window(self):
        self.prepare_show()

        def activate(hwnd):
            self.native.GetForegroundWindow.return_value = hwnd
            return True

        self.native.SetForegroundWindow.side_effect = activate
        self.menu.show()
        self.assertEqual(self.native.GetForegroundWindow(), 200)
        self.assertEqual(self.menu._return_window, 100)

    def test_show_retries_with_shared_input_then_detaches(self):
        self.prepare_show()
        attached = False

        def attach(first, second, enabled):
            nonlocal attached
            attached = enabled
            return True

        def activate(hwnd):
            if attached:
                self.native.GetForegroundWindow.return_value = hwnd
            return attached

        self.native.AttachThreadInput.side_effect = attach
        self.native.SetForegroundWindow.side_effect = activate
        self.menu.show()
        self.assertEqual(self.native.GetForegroundWindow(), 200)
        self.native.AttachThreadInput.assert_any_call(20, 10, True)
        self.native.AttachThreadInput.assert_any_call(20, 10, False)
        self.assertFalse(attached)

    def test_show_failure_hides_unusable_overlay_and_reports_error(self):
        self.prepare_show()
        self.native.SetForegroundWindow.return_value = False
        self.native.AttachThreadInput.return_value = False
        self.menu.show()
        self.menu.withdraw.assert_called_once()
        self.menu.on_error.assert_called_once()

    def test_keyboard_selection_waits_until_game_has_focus(self):
        self.select_first()
        self.native.SetForegroundWindow.assert_called_once_with(100)
        self.menu.on_select.assert_not_called()
        self.tick()
        self.menu.on_select.assert_not_called()
        self.native.GetForegroundWindow.return_value = 100
        self.tick()
        self.menu.on_select.assert_called_once_with(0)
        self.assertFalse(self.pending)

    def test_restore_is_requested_before_withdrawing_wheel(self):
        order = []
        self.native.SetForegroundWindow.side_effect = lambda hwnd: order.append("restore")
        self.menu.withdraw.side_effect = lambda: order.append("withdraw")
        self.select_first()
        self.assertEqual(order, ["restore", "withdraw"])

    def test_mouse_selection_uses_the_same_focus_check(self):
        self.menu._hit = Mock(return_value=3)
        self.menu._on_click(SimpleNamespace(x=10, y=20))
        self.tick()
        self.menu.on_select.assert_not_called()
        self.native.GetForegroundWindow.return_value = 100
        self.tick()
        self.menu.on_select.assert_called_once_with(3)

    def test_focus_timeout_reports_error_without_starting_playback(self):
        self.select_first()
        for _ in range(26):
            self.tick()
        self.menu.on_select.assert_not_called()
        self.menu.on_error.assert_called_once()
        self.assertFalse(self.pending)

    def test_closed_game_window_does_not_receive_playback(self):
        self.native.IsWindow.return_value = False
        self.select_first()
        self.tick()
        self.native.SetForegroundWindow.assert_not_called()
        self.menu.on_select.assert_not_called()
        self.menu.on_error.assert_called_once()

    def test_stop_cancels_pending_selection(self):
        self.select_first()
        self.tick()
        self.menu.hide()  # The stop hotkey calls hide before stopping playback.
        self.assertFalse(self.pending)
        self.menu.on_select.assert_not_called()

    def test_escape_restores_focus_without_selecting(self):
        self.menu._on_key(SimpleNamespace(char="\x1b", keysym="Escape"))
        self.native.SetForegroundWindow.assert_called_once_with(100)
        self.menu.on_select.assert_not_called()
        self.menu.on_close.assert_called_once()

    def test_center_stop_is_immediate_even_without_target(self):
        self.menu._return_window = None
        self.menu._hit = Mock(return_value=-1)
        self.menu._on_click(SimpleNamespace(x=10, y=20))
        self.menu.on_select.assert_called_once_with(-1)
        self.assertFalse(self.pending)

    def test_function_key_with_empty_character_is_ignored(self):
        self.menu._on_key(SimpleNamespace(char="", keysym="F8"))
        self.menu.on_select.assert_not_called()
        self.menu.withdraw.assert_not_called()

    def test_hiding_inactive_wheel_does_not_steal_another_apps_focus(self):
        self.native.GetForegroundWindow.return_value = 300
        self.menu.hide()
        self.native.SetForegroundWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
