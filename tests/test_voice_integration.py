"""Exercise the launcher playback/voice handoff without Tk, keys, or audio."""

from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gui import Music2MicApp


class FakeVoice:
    def __init__(self, events):
        self.events = events
        self.callbacks = []
        self.muted = False
        self.resume_count = 0

    def cancel_pending_mute(self):
        self.events.append("voice.cancel")
        self.callbacks.clear()

    def request_mute(self, callback):
        self.events.append("voice.request_mute")
        self.callbacks.append(callback)

    def acknowledge_mute(self):
        self.events.append("voice.muted")
        self.muted = True
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def resume(self):
        self.events.append("voice.resume")
        self.callbacks.clear()
        self.muted = False
        self.resume_count += 1


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class VoiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.player = SimpleNamespace(playing_path=None, playing_index=None,
                                      busy=False, play_ok=True)

        def stop():
            self.events.append("player.stop")
            self.player.playing_path = None
            self.player.playing_index = None
            self.player.busy = False

        def play(index, path):
            self.events.append(f"player.play:{index}")
            if not self.player.play_ok:
                return False, "文件无法播放"
            self.player.playing_index = index
            self.player.playing_path = path
            self.player.busy = True
            return True, "正在播放：" + path

        self.player.stop = Mock(side_effect=stop)
        self.player.play = Mock(side_effect=play)
        self.player.is_playing = Mock(side_effect=lambda: self.player.busy)
        self.release_ok = True
        self.hold_ok = True

        def release():
            self.events.append("ptt.release")
            return self.release_ok

        def hold():
            self.events.append("ptt.hold")
            return self.hold_ok

        self.voice = FakeVoice(self.events)
        self.app = SimpleNamespace(
            cfg={"slots": [{"path": "one.wav"}, {"path": "two.wav"}],
                 "audio": {"auto_ptt": True, "loop": False}},
            player=self.player,
            voice=self.voice,
            ptt=SimpleNamespace(release=Mock(side_effect=release), hold=Mock(side_effect=hold),
                                error="PTT 请求失败"),
            wheel=Mock(),
            after=Mock(), _set_status=Mock(), _refresh_buttons=Mock(), _update_wheel=Mock(),
            _play_serial=0, _preset_pending=False, _closing=False,
        )
        for name in ("_play", "_begin_play", "_stop_all", "_poll", "_toggle_wheel",
                     "_on_wheel_select", "_on_hotkey_wheel"):
            setattr(self.app, name, MethodType(getattr(Music2MicApp, name), self.app))

    def test_preset_and_ptt_wait_until_voice_output_is_confirmed_muted(self):
        self.app._on_wheel_select(0)
        self.assertTrue(self.app._preset_pending)
        self.player.play.assert_not_called()
        self.player.stop.assert_not_called()
        self.app.ptt.hold.assert_not_called()
        self.app.ptt.release.assert_not_called()
        self.voice.acknowledge_mute()
        self.assertFalse(self.app._preset_pending)
        self.assertTrue(self.voice.muted)
        self.assertEqual(self.events, ["voice.cancel", "voice.request_mute", "voice.muted",
                                      "player.stop", "ptt.release", "player.play:0", "ptt.hold"])

    def test_f8_opens_wheel_without_muting_voice(self):
        self.app.wheel.visible.return_value = False
        self.app.wheel.show.return_value = True
        self.app._on_hotkey_wheel()
        delay, action = self.app.after.call_args.args
        self.assertEqual(delay, 0)
        action()
        self.app.wheel.show.assert_called_once()
        self.assertFalse(self.events)
        self.assertFalse(self.voice.muted)

    def test_latest_selection_wins_even_if_stale_callback_arrives(self):
        self.app._play(0)
        old_callback = self.voice.callbacks[0]
        self.app._play(1)
        old_callback()
        self.player.play.assert_not_called()
        self.voice.acknowledge_mute()
        self.player.play.assert_called_once_with(1, "two.wav")
        self.assertTrue(self.voice.muted)

    def test_stop_cancels_pending_play_and_resumes_voice(self):
        self.app._play(0)
        old_callback = self.voice.callbacks[0]
        self.app._stop_all()
        old_callback()
        self.assertFalse(self.app._preset_pending)
        self.assertFalse(self.voice.callbacks)
        self.assertEqual(self.voice.resume_count, 1)
        self.player.play.assert_not_called()
        self.app.ptt.hold.assert_not_called()

    def test_natural_completion_resumes_only_after_two_idle_polls(self):
        self.app._play(0)
        self.voice.acknowledge_mute()
        self.player.busy = False
        self.app._poll()
        self.assertEqual(self.voice.resume_count, 0)
        self.assertTrue(self.voice.muted)
        self.app._poll()
        self.assertEqual(self.voice.resume_count, 1)
        self.assertIsNone(self.player.playing_path)
        self.assertEqual(self.app.ptt.release.call_count, 2)
        self.app._set_status.assert_called_with("播放结束")

    def test_active_loop_keeps_voice_muted_until_explicit_stop(self):
        self.app.cfg["audio"]["loop"] = True
        self.app._play(0)
        self.voice.acknowledge_mute()
        for _ in range(6):
            self.app._poll()
        self.assertTrue(self.voice.muted)
        self.assertEqual(self.voice.resume_count, 0)
        self.app._stop_all()
        self.assertFalse(self.voice.muted)
        self.assertEqual(self.voice.resume_count, 1)

    def test_transient_idle_poll_does_not_interrupt_playback(self):
        self.app._play(0)
        self.voice.acknowledge_mute()
        self.player.busy = False
        self.app._poll()
        self.player.busy = True
        self.app._poll()
        self.player.busy = False
        self.app._poll()
        self.assertEqual(self.voice.resume_count, 0)
        self.assertEqual(self.player.playing_index, 0)

    def test_old_preset_ending_does_not_resume_while_new_preset_waits(self):
        self.app._play(0)
        self.voice.acknowledge_mute()
        self.app._play(1)
        self.player.busy = False
        self.app._poll()
        self.app._poll()
        self.assertEqual(self.voice.resume_count, 0)
        self.voice.acknowledge_mute()
        self.assertEqual(self.player.playing_index, 1)

    def test_ptt_release_failure_cancels_preset_and_resumes_voice(self):
        self.release_ok = False
        self.app._play(0)
        self.voice.acknowledge_mute()
        self.player.play.assert_not_called()
        self.app.ptt.hold.assert_not_called()
        self.assertFalse(self.app._preset_pending)
        self.assertEqual(self.voice.resume_count, 1)
        self.assertIn("松开失败", self.app._set_status.call_args.args[0])

    def test_failed_clip_playback_restores_voice(self):
        self.player.play_ok = False
        self.app._play(0)
        with patch("gui.messagebox.showwarning") as warning:
            self.voice.acknowledge_mute()
        warning.assert_called_once_with("播放失败", "文件无法播放")
        self.app.ptt.hold.assert_not_called()
        self.assertIsNone(self.player.playing_path)
        self.assertEqual(self.voice.resume_count, 1)

    def test_failed_ptt_hold_keeps_voice_muted_while_clip_still_plays(self):
        self.hold_ok = False
        self.app._play(0)
        self.voice.acknowledge_mute()
        self.assertTrue(self.voice.muted)
        self.assertEqual(self.player.playing_path, "one.wav")
        self.assertEqual(self.voice.resume_count, 0)
        self.assertIn("自动说话失败", self.app._set_status.call_args.args[0])


class VoiceSettingsTests(unittest.TestCase):
    """Check device/lifecycle routing through the actual settings handlers."""

    def setUp(self):
        save = patch("gui.save_config")
        self.save = save.start()
        self.addCleanup(save.stop)
        self.microphone = dict(index=2, name="USB Microphone", hostapi=1,
                               hostapi_name="Windows WASAPI", max_input_channels=1,
                               max_output_channels=0)
        self.cable = dict(index=4, name="CABLE Input (VB-Audio Virtual Cable)", hostapi=1,
                          hostapi_name="Windows WASAPI", max_input_channels=0,
                          max_output_channels=2)
        self.headphones = {**self.cable, "index": 5, "name": "USB Headphones"}
        self.voice = SimpleNamespace(can_start=True, is_running=False, refreshing=False,
                                     state="stopped", message="变声未启动",
                                     devices=[self.microphone, self.cable, self.headphones])

        def start(*args, **kwargs):
            self.voice.can_start = False
            self.voice.is_running = True
            self.voice.state = "starting"
            return True

        def stop():
            if self.voice.is_running:
                self.voice.can_start = False
                self.voice.state = "stopping"

        def discover():
            self.voice.can_start = False
            self.voice.refreshing = True
            return True

        self.voice.start = Mock(side_effect=start)
        self.voice.stop = Mock(side_effect=stop)
        self.voice.discover_devices = Mock(side_effect=discover)
        self.voice.poll = Mock(return_value=[])
        self.player = SimpleNamespace(device_in_use=self.cable["name"], playing_path=None,
                                      init_mixer=Mock(), message="输出已切换")
        microphone_label = "USB Microphone  [Windows WASAPI]"
        self.app = SimpleNamespace(
            cfg={"audio": {"device": self.cable["name"]}, "voice": {"enabled": False}},
            voice=self.voice, player=self.player,
            voice_enabled_var=FakeVar(False), voice_reference_var=FakeVar("target.mp3"),
            voice_input_var=FakeVar(microphone_label), device_var=FakeVar(self.cable["name"]),
            voice_status_var=FakeVar(), voice_output_var=FakeVar(),
            voice_reference_entry=Mock(), voice_browse_button=Mock(), voice_input_combo=Mock(),
            voice_refresh_button=Mock(), voice_log=Mock(),
            _voice_device_choices={microphone_label: self.microphone},
            _voice_auto_start=False, _restart_voice_after_stop=False, _voice_notice="",
            _preset_pending=False, _closing=False,
            _stop_all=Mock(), _set_status=Mock(), after=Mock(),
        )
        self.app.voice_log.index.return_value = "1.0"
        for name in ("_on_voice_enabled", "_start_voice", "_sync_voice_status", "_poll_voice",
                     "_set_voice_devices", "_refresh_voice_devices", "_on_device_change"):
            setattr(self.app, name, MethodType(getattr(Music2MicApp, name), self.app))

    def test_enabling_voice_during_preset_starts_the_child_muted(self):
        self.player.playing_path = "preset.wav"
        self.app.voice_enabled_var.set(True)
        self.app._on_voice_enabled()
        self.voice.start.assert_called_once_with("target.mp3", self.microphone, self.cable,
                                                  initially_muted=True)

    def test_enabling_voice_during_pending_preset_starts_the_child_muted(self):
        self.app._preset_pending = True
        self.app.voice_enabled_var.set(True)
        self.app._on_voice_enabled()
        self.assertTrue(self.voice.start.call_args.kwargs["initially_muted"])

    def test_output_change_waits_for_exit_and_device_refresh_before_restart(self):
        self.voice.can_start = False
        self.voice.is_running = True
        self.voice.state = "running"
        self.app.voice_enabled_var.set(True)
        self.app.device_var.set(self.headphones["name"])
        self.player.init_mixer.side_effect = lambda: setattr(
            self.player, "device_in_use", self.headphones["name"])
        self.app._on_device_change()
        self.app._stop_all.assert_called_once()
        self.voice.stop.assert_called_once()
        self.app._poll_voice()
        self.voice.start.assert_not_called()
        self.voice.discover_devices.assert_not_called()
        self.assertTrue(self.app._restart_voice_after_stop)

        self.voice.is_running = False
        self.voice.can_start = True
        self.voice.state = "stopped"
        self.voice.poll.return_value = [dict(event="finished", returncode=0)]
        self.app._poll_voice()
        self.assertTrue(self.app.voice_enabled_var.get())
        self.voice.discover_devices.assert_called_once()
        self.voice.start.assert_not_called()
        self.assertTrue(self.app._voice_auto_start)

        self.voice.refreshing = False
        self.voice.can_start = True
        self.voice.poll.return_value = [dict(event="devices", devices=self.voice.devices)]
        self.app._poll_voice()
        self.voice.start.assert_called_once_with("target.mp3", self.microphone, self.headphones,
                                                  initially_muted=False)
        self.assertFalse(self.app._restart_voice_after_stop)
        self.assertFalse(self.app._voice_auto_start)

    def test_disabling_voice_cancels_queued_restart_and_device_autostart(self):
        self.voice.refreshing = True
        self.voice.can_start = False
        self.app._voice_auto_start = True
        self.app._restart_voice_after_stop = True
        self.app.voice_enabled_var.set(False)
        self.app._on_voice_enabled()
        self.assertFalse(self.app._voice_auto_start)
        self.assertFalse(self.app._restart_voice_after_stop)
        self.assertFalse(self.app.cfg["voice"]["enabled"])
        self.voice.refreshing = False
        self.voice.can_start = True
        self.voice.poll.return_value = [dict(event="devices", devices=self.voice.devices)]
        self.app._poll_voice()
        self.voice.start.assert_not_called()
        self.voice.discover_devices.assert_not_called()

    def test_default_output_fallback_prevents_voice_start_and_keeps_error_visible(self):
        # A stale saved CABLE name must not override the mixer's actual fallback.
        self.player.device_in_use = ""
        self.app.voice_enabled_var.set(True)
        self.app._on_voice_enabled()
        self.voice.start.assert_not_called()
        self.assertFalse(self.app.voice_enabled_var.get())
        self.assertFalse(self.app.cfg["voice"]["enabled"])
        self.assertIn("可用", self.app.voice_status_var.get())
        message = self.app.voice_status_var.get()
        self.app._poll_voice()
        self.assertEqual(self.app.voice_status_var.get(), message)

    def test_start_uses_actual_mixer_device_instead_of_stale_saved_device(self):
        self.player.device_in_use = self.headphones["name"]
        self.app.voice_enabled_var.set(True)
        self.app._on_voice_enabled()
        self.voice.start.assert_called_once_with("target.mp3", self.microphone, self.headphones,
                                                  initially_muted=False)

    def test_similar_output_names_choose_exact_device_even_when_listed_last(self):
        first = {**self.headphones, "name": "Speakers"}
        selected = {**self.headphones, "index": 6, "name": "Speakers 2"}
        self.voice.devices = [first, selected]
        self.player.device_in_use = selected["name"]
        self.app._start_voice()
        self.assertEqual(self.voice.start.call_args.args[2], selected)

    def test_ambiguous_short_output_name_cannot_route_voice_to_another_device(self):
        self.voice.devices = [{**self.headphones, "name": "Speakers A"},
                              {**self.headphones, "index": 6, "name": "Speakers B"}]
        self.player.device_in_use = "Speakers"
        self.app._start_voice()
        self.voice.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
