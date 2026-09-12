"""Bounded local verification through VB-CABLE; never opens a speaker output.

Run with voice-changer/.venv/Scripts/python.exe. The main .venv supplies pygame
in an isolated child, matching the application's two-environment architecture.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import sounddevice as sd
import soundfile as sf

from voice_gate import VoiceGate


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "voice-changer" / "results"
SAMPLE_RATE = 48000
BLOCK = 7680


def device(kind, prefix):
    hostapis = sd.query_hostapis()
    found = [(index, entry) for index, entry in enumerate(sd.query_devices())
             if entry[f"max_{kind}_channels"] > 0
             and hostapis[entry["hostapi"]]["name"] == "Windows WASAPI"
             and entry["name"].startswith(prefix)]
    if len(found) != 1:
        raise RuntimeError(f"Expected one WASAPI {kind} {prefix!r}: {found}")
    return found[0]


def tone(frequency, count, offset=0):
    return (0.12 * np.sin(2 * np.pi * frequency *
                         (np.arange(count) + offset) / SAMPLE_RATE)).astype(np.float32)


def tone_level(audio, frequency):
    # Integrate a narrow spectral band, allowing device resampling clock drift.
    if len(audio) < SAMPLE_RATE // 10:
        raise RuntimeError("Insufficient captured samples in an analysis window")
    spectrum = np.abs(np.fft.rfft(audio)) ** 2
    frequencies = np.fft.rfftfreq(len(audio), 1 / SAMPLE_RATE)
    power = spectrum[np.abs(frequencies - frequency) <= 8].sum()
    return float(np.sqrt(4 * power / len(audio) ** 2))


def verify_cable():
    output_index, output_info = device("output", "CABLE Input (")
    capture_index, capture_info = device("input", "CABLE Output (")
    RESULTS.mkdir(parents=True, exist_ok=True)
    preset_path = RESULTS / "verification-preset-tone.wav"
    with wave.open(str(preset_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes((tone(1320, SAMPLE_RATE * 2) * 32767).astype("<i2").tobytes())

    pygame_code = """import json,sys,time
import pygame
pygame.mixer.init(frequency=48000,size=-16,channels=1,buffer=512,devicename=sys.argv[1])
print('READY',flush=True)
sys.stdin.readline()
pygame.mixer.music.load(sys.argv[2])
pygame.mixer.music.play()
print(json.dumps({'started':time.perf_counter()}),flush=True)
while pygame.mixer.music.get_busy(): time.sleep(.01)
print(json.dumps({'finished':time.perf_counter()}),flush=True)
pygame.mixer.quit()
"""
    env = dict(os.environ, PYTHONUTF8="1", PYGAME_HIDE_SUPPORT_PROMPT="1")
    pygame_process = subprocess.Popen(
        [str(ROOT / ".venv/Scripts/python.exe"), "-u", "-c", pygame_code,
         output_info["name"], str(preset_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pygame_lines = queue.Queue()

    def read_pygame():
        for line in pygame_process.stdout:
            pygame_lines.put(line.strip())
        pygame_lines.put("EOF")

    threading.Thread(target=read_pygame, daemon=True).start()

    def pygame_line():
        return pygame_lines.get(timeout=10)
    gate = VoiceGate(BLOCK, SAMPLE_RATE)
    quit_worker = threading.Event()
    frequencies = {"current": 440}
    captured = []
    faults = []
    worker_failures = []
    frame_offset = 0

    def capture(indata, frames, timing, status):
        if status:
            faults.append(f"capture: {status}")
        start = time.perf_counter() + timing.inputBufferAdcTime - timing.currentTime
        captured.append((start, indata[:, 0].copy()))

    def output(outdata, frames, timing, status):
        nonlocal frame_offset
        if status:
            faults.append(f"output: {status}")
        samples = tone(frequencies["current"], frames, frame_offset)
        frame_offset += frames
        gate.callback(samples.reshape(-1, 1), outdata, frames)

    def worker():
        cache_generation = 0
        try:
            while not quit_worker.is_set():
                work = gate.next_work(cache_generation)
                if work is None:
                    continue
                generation, block = work
                if block is None:
                    cache_generation = generation
                else:
                    gate.publish(generation, block)
        except Exception as exc:
            worker_failures.append(str(exc))

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    report = {"scope": "Real VoiceGate and WASAPI VB-CABLE capture; synthetic converter tones; real pygame preset",
              "output_device": dict(output_info), "capture_device": dict(capture_info)}
    try:
        first_line = pygame_line()
        if first_line != "READY":
            raise RuntimeError(f"pygame did not initialize CABLE output: {first_line}")
        with sd.InputStream(device=capture_index, channels=1, samplerate=SAMPLE_RATE,
                            blocksize=480, dtype="float32", callback=capture) as capture_stream:
            with sd.OutputStream(device=output_index, channels=1, samplerate=SAMPLE_RATE,
                                 blocksize=BLOCK, latency="low", dtype="float32", callback=output) as output_stream:
                gate.configure_latency(0.0, output_stream.latency)
                report["output_latency_seconds"] = output_stream.latency
                report["capture_latency_seconds"] = capture_stream.latency
                started = time.perf_counter()
                time.sleep(2)
                # Leave additional old conversion queued at the transition.
                gate.publish(0, tone(440, BLOCK * 2))
                gate.mute()
                frequencies["current"] = 660  # speech during the preset
                if not gate.wait_for_silence(2):
                    raise RuntimeError("No silent callback at mute")
                time.sleep(gate.drain_seconds)
                muted_ack = time.perf_counter()
                pygame_process.stdin.write("play\n")
                pygame_process.stdin.flush()
                preset_started = json.loads(pygame_line())["started"]
                preset_finished = json.loads(pygame_line())["finished"]
                # Allow SDL's final small buffer to finish before resume.
                time.sleep(0.15)
                frequencies["current"] = 880
                gate.resume()
                resume_time = time.perf_counter()
                stale_accepted = gate.publish(0, tone(440, BLOCK * 2))
                time.sleep(gate.resume_capture_delay_seconds + output_stream.latency + 2.0)
                gate.mute()
                time.sleep(gate.drain_seconds + 0.25)
        report.update({"mute_ack_seconds_after_start": muted_ack - started,
                       "drain_seconds": gate.drain_seconds,
                       "stale_conversion_result_accepted": stale_accepted,
                       "worker_failures": worker_failures, "stream_statuses": faults,
                       "counters": gate.counters})
        windows = {"before": (started + 0.8, started + 1.7),
                   "preset_start": (preset_started, preset_started + 0.25),
                   "preset": (preset_started + 0.3, preset_finished - 0.15),
                   "resumed": (resume_time + gate.resume_capture_delay_seconds +
                               report["output_latency_seconds"] + 0.5,
                               resume_time + gate.resume_capture_delay_seconds +
                               report["output_latency_seconds"] + 1.5)}
        measurements = {}
        for label, (start, end) in windows.items():
            segments = []
            for block_start, samples in captured:
                first = max(0, int(np.ceil((start - block_start) * SAMPLE_RATE)))
                last = min(len(samples), int(np.floor((end - block_start) * SAMPLE_RATE)))
                if last > first:
                    segments.append(samples[first:last])
            audio = np.concatenate(segments) if segments else np.empty(0)
            measurements[label] = {"seconds": len(audio) / SAMPLE_RATE,
                                   "rms": float(np.sqrt(np.mean(audio ** 2))),
                                   **{f"tone_{freq}_amplitude": tone_level(audio, freq)
                                      for freq in [440, 660, 880, 1320]}}
        checks = {
            "conversion_audible_before_preset": measurements["before"]["tone_440_amplitude"] > 0.05,
            "preset_audible": measurements["preset"]["tone_1320_amplitude"] > 0.05,
            "no_old_voice_at_preset_start": measurements["preset_start"]["tone_440_amplitude"] < 0.003,
            "no_conversion_during_preset": max(measurements["preset"][f"tone_{freq}_amplitude"]
                                                for freq in [440, 660, 880]) < 0.003,
            "fresh_conversion_audible_after_resume": measurements["resumed"]["tone_880_amplitude"] > 0.05,
            "no_queued_speech_after_resume": max(measurements["resumed"][f"tone_{freq}_amplitude"]
                                                 for freq in [440, 660]) < 0.003,
            "inflight_old_result_rejected": not stale_accepted,
            "worker_ok": not worker_failures,
        }
        report.update({"measurements": measurements, "checks": checks, "passed": all(checks.values())})
        sf.write(RESULTS / "integration-cable-tones.wav", np.concatenate([block for _, block in captured]),
                 SAMPLE_RATE, subtype="PCM_16")
    finally:
        quit_worker.set()
        gate.stop()
        worker_thread.join(2)
        if pygame_process.poll() is None:
            pygame_process.terminate()
        pygame_process.wait(5)
    (RESULTS / "integration-cable-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    if not report["passed"]:
        raise RuntimeError("CABLE isolation verification failed")


def verify_cuda_ipc():
    input_index, input_info = device("input", "麦克风 (USB2.0 Device)")
    output_index, output_info = device("output", "CABLE Input (")
    result_path = RESULTS / "integration-cuda-live.json"
    command = [sys.executable, "-u", str(Path(__file__).with_name("run_voice_changer.py")), "live",
               "--reference", str(ROOT / "musics/好厉害啊哥哥.mp3"), "--report", str(result_path),
               "--input-device", str(input_index), "--output-device", str(output_index),
               "--input-device-name", input_info["name"], "--output-device-name", output_info["name"],
               "--hostapi-name", "Windows WASAPI", "--control-stdin", "--start-muted"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               env=dict(os.environ, PYTHONUTF8="1"),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    inbox = queue.Queue()
    logs = []
    events = []

    def reader():
        for line in process.stdout:
            logs.append(line.rstrip())
            if line.startswith("M2M_EVENT "):
                inbox.put((time.perf_counter(), json.loads(line[len("M2M_EVENT "):])))
        inbox.put((time.perf_counter(), {"event": "eof"}))

    threading.Thread(target=reader, daemon=True).start()

    def wait_event(kind, command_id=None, timeout=90):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            timestamp, event = inbox.get(timeout=max(0.1, deadline - time.perf_counter()))
            events.append({"received": timestamp, **event})
            if event["event"] in {"error", "eof"}:
                raise RuntimeError(f"Backend failed while awaiting {kind}: {event}")
            if event["event"] == kind and (command_id is None or event.get("id") == command_id):
                return event
        raise TimeoutError(f"No {kind} event")

    def send(command_id, name):
        process.stdin.write(json.dumps({"id": command_id, "command": name}) + "\n")
        process.stdin.flush()

    started = time.perf_counter()
    try:
        ready = wait_event("ready")
        if ready.get("muted") is not True:
            raise RuntimeError("Backend did not start muted")
        send(1, "resume")
        wait_event("resumed", 1, 10)
        time.sleep(2.5)
        mute_sent = time.perf_counter()
        send(2, "mute")
        muted = wait_event("muted", 2, 10)
        mute_elapsed = time.perf_counter() - mute_sent
        time.sleep(0.3)
        send(3, "resume")
        wait_event("resumed", 3, 10)
        time.sleep(2.5)
        send(4, "stop")
        wait_event("stopped", 4, 15)
        process.wait(15)
        report = {"scope": "Actual CUDA model and physical USB microphone to explicit VB-CABLE; no speech requested or recorded",
                  "events": events, "exit_code": process.returncode,
                  "wall_seconds": time.perf_counter() - started,
                  "mute_ack_seconds": mute_elapsed,
                  "declared_drain_seconds": muted["drain_seconds"],
                  "passed": process.returncode == 0 and mute_elapsed >= muted["drain_seconds"]}
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(10)
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "integration-cuda-ipc.log").write_text("\n".join(logs), encoding="utf-8")
    (RESULTS / "integration-cuda-ipc-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    if not report["passed"]:
        raise RuntimeError("CUDA IPC smoke failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["cable", "cuda"])
    args = parser.parse_args()
    {"cable": verify_cable, "cuda": verify_cuda_ipc}[args.mode]()
