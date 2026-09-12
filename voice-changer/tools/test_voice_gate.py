"""Waveform-level regression checks; no microphone or CUDA is needed."""
from __future__ import annotations

import threading
import unittest

import numpy as np

from voice_gate import VoiceGate


class VoiceGateTests(unittest.TestCase):
    def setUp(self):
        self.now = [10.0]
        self.gate = VoiceGate(8, 80, clock=lambda: self.now[0])

    def callback(self, value, frames=8):
        output = np.full((frames, 1), np.nan, dtype=np.float32)
        self.gate.callback(np.full((frames, 1), value, dtype=np.float32), output, frames)
        return output[:, 0]

    def test_partial_output_and_queued_input_are_discarded_at_preset_boundary(self):
        self.gate.publish(0, np.full(12, 0.25, dtype=np.float32))
        np.testing.assert_array_equal(self.callback(0.1), np.full(8, 0.25))
        self.gate.publish(0, np.full(8, 0.5, dtype=np.float32))
        # Four old output samples remain in the partial buffer, another block
        # is pending, and input captured before the preset is still queued.
        self.gate.mute()
        self.assertFalse(self.gate.wait_for_silence(0))
        np.testing.assert_array_equal(self.callback(0.9), np.zeros(8))
        self.assertTrue(self.gate.wait_for_silence(0))
        self.assertFalse(self.gate.publish(0, np.ones(8, dtype=np.float32)))
        muted_generation, reset = self.gate.next_work(0, timeout=0)
        self.assertIsNone(reset)
        self.assertIsNone(self.gate.next_work(muted_generation, timeout=0))

        self.gate.resume()
        resumed_generation, reset = self.gate.next_work(muted_generation, timeout=0)
        self.assertIsNone(reset)
        # This block was captured by the sound card before resume. It must
        # not be converted, even though PortAudio delivers its callback now.
        np.testing.assert_array_equal(self.callback(0.9), np.zeros(8))
        self.assertIsNone(self.gate.next_work(resumed_generation, timeout=0))
        self.now[0] += self.gate.resume_capture_delay_seconds + 0.001
        np.testing.assert_array_equal(self.callback(0.4), np.zeros(8))
        generation, fresh_input = self.gate.next_work(resumed_generation, timeout=0)
        np.testing.assert_allclose(fresh_input, np.full(8, 0.4), rtol=1e-6)
        self.gate.publish(generation, fresh_input)
        np.testing.assert_allclose(self.callback(0.4), np.full(8, 0.4), rtol=1e-6)

    def test_inflight_conversion_cannot_replay_or_contaminate_fresh_model_cache(self):
        inference_entered = threading.Event()
        finish_old_inference = threading.Event()
        fresh_published = threading.Event()
        quit_worker = threading.Event()
        accepted = []
        failures = []

        def worker():
            generation = 0
            model_cache = 0.0
            first = True
            try:
                while not quit_worker.is_set():
                    work = self.gate.next_work(generation)
                    if work is None:
                        continue
                    work_generation, block = work
                    if block is None:
                        model_cache = 0.0
                        generation = work_generation
                        continue
                    if first:
                        first = False
                        inference_entered.set()
                        if not finish_old_inference.wait(2):
                            raise TimeoutError("Test did not release the simulated GPU job")
                    # A cache-dependent converter makes a missed model reset
                    # observable in the waveform, not only in a state flag.
                    converted = block + model_cache
                    model_cache = float(block[-1])
                    accepted.append(self.gate.publish(work_generation, converted))
                    if accepted[-1]:
                        fresh_published.set()
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.callback(0.25)
            self.assertTrue(inference_entered.wait(2))
            self.gate.mute()
            for _ in range(5):
                np.testing.assert_array_equal(self.callback(0.9), np.zeros(8))
            self.gate.resume()
            self.now[0] += self.gate.resume_capture_delay_seconds + 0.001
            self.callback(0.4)
            finish_old_inference.set()
            self.assertTrue(fresh_published.wait(2))
            self.assertEqual(accepted[:2], [False, True])
            np.testing.assert_allclose(self.callback(0), np.full(8, 0.4), rtol=1e-6)
            self.assertEqual(failures, [])
        finally:
            quit_worker.set()
            self.gate.stop()
            finish_old_inference.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_starts_muted_and_stop_never_emits_queued_voice(self):
        self.gate = VoiceGate(8, 80, start_muted=True)
        self.assertFalse(self.gate.publish(0, np.ones(8, dtype=np.float32)))
        np.testing.assert_array_equal(self.callback(0.9), np.zeros(8))
        self.gate.resume()
        generation, _ = self.gate.next_work(0, timeout=0)
        self.gate.publish(generation, np.ones(8, dtype=np.float32))
        self.gate.stop()
        np.testing.assert_array_equal(self.callback(0.9), np.zeros(8))
        self.assertIsNone(self.gate.next_work(generation, timeout=0))

    def test_device_buffer_margins_follow_actual_stream_latency(self):
        self.gate.configure_latency(0.06, 0.08)
        self.assertAlmostEqual(self.gate.drain_seconds, 0.2)
        self.assertAlmostEqual(self.gate.resume_capture_delay_seconds, 0.18)


if __name__ == "__main__":
    unittest.main()
