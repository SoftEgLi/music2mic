"""A failed PTT request must not be reported as a successful key hold."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from gui import Music2MicApp


class PTTStatusTests(unittest.TestCase):
    def make_app(self):
        app = SimpleNamespace(
            cfg={"slots": [{"path": "test.wav"}], "audio": {"auto_ptt": True}},
            player=Mock(playing_index=0),
            ptt=Mock(held=False, error="Windows 拒绝发送 C"),
            _set_status=Mock(), _refresh_buttons=Mock(), _update_wheel=Mock(),
        )
        app.player.play.return_value = (True, "播放中：test.wav")
        app.ptt.hold.return_value = False
        app.ptt.release.return_value = True
        return app

    def test_failed_hold_is_visible_in_playback_status(self):
        app = self.make_app()
        Music2MicApp._play(app, 0)
        status = app._set_status.call_args.args[0]
        self.assertNotIn("已自动按住", status)
        self.assertIn("Windows 拒绝发送 C", status)

    def test_replaying_same_slot_releases_then_presses_again(self):
        app = self.make_app()
        app.ptt.held = True
        app.ptt.hold.return_value = True
        Music2MicApp._play(app, 0)
        app.ptt.release.assert_called_once()
        self.assertLess(app.ptt.method_calls.index(("release", (), {})),
                        app.ptt.method_calls.index(("hold", (), {})))


if __name__ == "__main__":
    unittest.main()
