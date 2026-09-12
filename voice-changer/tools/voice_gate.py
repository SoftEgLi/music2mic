"""Thread-safe audio boundary between the microphone worker and the sound card.

The generation changes on every transition. A worker may finish an old GPU job,
but its result cannot cross the boundary, and it must reset its model cache
before processing audio in the next generation.
"""
from __future__ import annotations

from collections import deque
import threading
import time

import numpy as np


class VoiceGate:
    def __init__(self, model_chunk_frames, sample_rate=48000, *, start_muted=False,
                 clock=time.perf_counter):
        self._condition = threading.Condition()
        self._clock = clock
        self._silence = threading.Event()
        self._inputs = deque()
        self._outputs = deque()
        self._out_buffer = np.empty(0, dtype=np.float32)
        self._output_started = False
        self._generation = 0
        self._muted = start_muted
        self._stopped = False
        self._capture_not_before = 0.0
        self._input_latency = 0.0
        self._output_latency = 0.0
        self._block_seconds = model_chunk_frames / sample_rate
        self.counters = {"input_drops": 0, "output_drops": 0, "output_underflows": 0,
                         "startup_silence_callbacks": 0, "callbacks": 0,
                         "muted_callbacks": 0, "discarded_stale_results": 0,
                         "discarded_input_callbacks": 0}

    @property
    def muted(self):
        with self._condition:
            return self._muted

    @property
    def drain_seconds(self):
        # One additional callback plus 20 ms covers device scheduling jitter.
        return self._output_latency + self._block_seconds + 0.02

    @property
    def resume_capture_delay_seconds(self):
        return self._input_latency + self._block_seconds + 0.02

    def configure_latency(self, input_latency, output_latency):
        with self._condition:
            self._input_latency = max(0.0, float(input_latency))
            self._output_latency = max(0.0, float(output_latency))

    def _transition(self, muted):
        with self._condition:
            self._generation += 1
            self._muted = muted
            self._inputs.clear()
            self._outputs.clear()
            self._out_buffer = np.empty(0, dtype=np.float32)
            self._output_started = False
            self._silence.clear()
            # Input blocks already captured by the device during the preset
            # must also be discarded, even if their callback arrives on resume.
            self._capture_not_before = self._clock() + self.resume_capture_delay_seconds
            self._condition.notify_all()

    def mute(self):
        self._transition(True)

    def resume(self):
        self._transition(False)

    def stop(self):
        with self._condition:
            self._stopped = True
            self._muted = True
            self._inputs.clear()
            self._outputs.clear()
            self._out_buffer = np.empty(0, dtype=np.float32)
            self._condition.notify_all()

    def wait_for_silence(self, timeout):
        """Wait until a device callback has actually written silence."""
        return self._silence.wait(timeout)

    def next_work(self, cache_generation, timeout=0.1):
        """Return (generation, audio), (generation, None) to reset, or None.

        Only the inference thread calls this method or resets the model cache.
        The next call must use the generation whose cache it last initialized.
        """
        with self._condition:
            if self._stopped:
                return None
            if cache_generation != self._generation:
                return self._generation, None
            if not self._inputs:
                self._condition.wait(timeout)
            if self._stopped:
                return None
            if cache_generation != self._generation:
                return self._generation, None
            if self._inputs and not self._muted:
                return self._generation, self._inputs.popleft()
            return None

    def publish(self, generation, audio):
        """Accept converted samples only if their input still belongs here."""
        with self._condition:
            if self._stopped or self._muted or generation != self._generation:
                self.counters["discarded_stale_results"] += 1
                return False
            if len(self._outputs) >= 4:
                self._outputs.popleft()
                self.counters["output_drops"] += 1
            self._outputs.append(audio)
            return True

    def callback(self, indata, outdata, frames):
        """Called by PortAudio; no CUDA work or blocking device I/O here."""
        with self._condition:
            self.counters["callbacks"] += 1
            outdata.fill(0)
            if self._muted or self._stopped:
                self.counters["muted_callbacks"] += 1
                self._silence.set()
                return
            if self._clock() >= self._capture_not_before:
                if len(self._inputs) < 4:
                    self._inputs.append(indata[:, 0].copy())
                    self._condition.notify()
                else:
                    self.counters["input_drops"] += 1
            else:
                self.counters["discarded_input_callbacks"] += 1
            while self._outputs:
                self._out_buffer = np.concatenate([self._out_buffer, self._outputs.popleft()])
            if not self._output_started and len(self._out_buffer) < frames:
                self.counters["startup_silence_callbacks"] += 1
                return
            self._output_started = True
            length = min(len(self._out_buffer), frames)
            outdata[:length, 0] = np.clip(self._out_buffer[:length], -1, 1)
            self._out_buffer = self._out_buffer[length:]
            if length < frames:
                self.counters["output_underflows"] += 1
