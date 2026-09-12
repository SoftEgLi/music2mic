"""Local CUDA runner, measured streaming benchmark, and explicit audio routing."""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "MeanVC2"
for key, folder in [("HF_HOME", "huggingface"), ("TORCH_HOME", "torch"), ("NUMBA_CACHE_DIR", "numba")]:
    os.environ.setdefault(key, str(ROOT / ".cache" / folder))
sys.path.insert(0, str(UPSTREAM / "runtime"))

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import torch
from safetensors.torch import load_file
import run_rt
from voice_gate import VoiceGate


def memory():
    return {key: round(value / 1048576, 2) for key, value in {
        "allocated_mib": torch.cuda.memory_allocated(),
        "reserved_mib": torch.cuda.memory_reserved(),
        "peak_allocated_mib": torch.cuda.max_memory_allocated(),
        "peak_reserved_mib": torch.cuda.max_memory_reserved(),
    }.items()}


def write_report(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_audio(path, sr=16000):
    data, source_sr = sf.read(str(path), dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if source_sr != sr:
        from math import gcd
        divisor = gcd(source_sr, sr)
        data = resample_poly(data, sr // divisor, source_sr // divisor)
    return np.ascontiguousarray(data, dtype=np.float32)


def checked_loader(config_path, ckpt_path, device="cuda"):
    config = json.loads(Path(config_path).read_text())
    model = run_rt.DiT(**config["model"])
    weights = load_file(ckpt_path)
    result = model.load_state_dict(weights, strict=False)
    bad_missing = [key for key in result.missing_keys if not key.startswith("cache_embed.")]
    if bad_missing or result.unexpected_keys:
        raise RuntimeError(f"Checkpoint mismatch: missing={bad_missing}, unexpected={result.unexpected_keys}")
    print(f"[Checkpoint] Loaded {len(weights)} tensors; unused cache-only missing: {result.missing_keys}", flush=True)
    return model.to(device).float().eval()


def initialize(reference):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this environment")
    print(f"[GPU] {torch.cuda.get_device_name(0)} / torch {torch.__version__}", flush=True)
    cfg_file = UPSTREAM / "preprocess/ckpts/wavlm_large_cfg.pt"
    if not cfg_file.exists():
        checkpoint = torch.load(UPSTREAM / "preprocess/ckpts/wavlm_large.pt", map_location="cpu", weights_only=False)
        torch.save(checkpoint["cfg"], cfg_file)
        del checkpoint
        gc.collect()
    reference = Path(reference).resolve()
    prepared = ROOT / "samples" / "active_reference.wav"
    prepared.parent.mkdir(parents=True, exist_ok=True)
    reference_audio = read_audio(reference)
    sf.write(prepared, reference_audio, 16000, subtype="PCM_16")
    run_rt._load_vc_model = checked_loader
    torch.manual_seed(42)
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    vc = run_rt.VCRunner(str(prepared), device="cuda", model="40ms")
    torch.cuda.synchronize()
    loaded = memory()
    del vc.spk_model
    gc.collect()
    torch.cuda.empty_cache()
    freed = memory()
    info = {
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
        "reference": str(reference), "reference_seconds": len(reference_audio) / 16000,
        "initialization_seconds": time.perf_counter() - started,
        "with_speaker_model": loaded, "after_releasing_speaker_model": freed,
        "model": "40ms_40ms", "audio_chunk_ms": vc.CHUNK / 16, "ode_steps": 2,
    }
    print(f"[Memory] loaded={loaded}; after releasing speaker encoder={freed}", flush=True)
    for _ in range(5):
        vc.process_chunk(np.zeros(vc.CHUNK, dtype=np.float32))
    torch.cuda.synchronize()
    vc._init_cache()
    torch.cuda.reset_peak_memory_stats()
    print("[Ready] CUDA model warmed up", flush=True)
    return vc, info


def statistics(times, block_ms):
    arr = np.asarray(times)
    return {
        "chunks": len(times), "mean_processing_ms": float(arr.mean()) if len(arr) else None,
        "p95_processing_ms": float(np.percentile(arr, 95)) if len(arr) else None,
        "max_processing_ms": float(arr.max()) if len(arr) else None,
        "chunks_over_deadline": int((arr > block_ms).sum()),
        "real_time_factor": float(arr.mean() / block_ms) if len(arr) else None,
    }


def benchmark(args):
    vc, report = initialize(args.reference)
    source = read_audio(args.source)
    source_wav = ROOT / "samples" / "active_source.wav"
    sf.write(source_wav, source, 16000)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    vc.process_file(str(source_wav), str(output))
    converted, sample_rate = sf.read(output, dtype="float32")
    report.update({"source": str(Path(args.source).resolve()), "source_seconds": len(source) / 16000,
                   "output": str(output.resolve()), "output_seconds": len(converted) / sample_rate,
                   "output_rms": float(np.sqrt(np.mean(converted ** 2))),
                   "output_peak": float(np.max(np.abs(converted))), "output_finite": bool(np.isfinite(converted).all())})
    vc._init_cache()
    segment = np.concatenate([source, np.zeros(3200, dtype=np.float32)])
    n_chunks = max(1, round(args.seconds * 16000 / vc.CHUNK))
    times = []
    emitted = 0
    t0 = time.perf_counter()
    for index in range(n_chunks):
        positions = np.arange(index * vc.CHUNK, (index + 1) * vc.CHUNK) % len(segment)
        start = time.perf_counter()
        with torch.inference_mode():
            result = vc.process_chunk(segment[positions])
        torch.cuda.synchronize()
        times.append((time.perf_counter() - start) * 1000)
        if result is not None:
            emitted += len(result)
        if args.paced:
            delay = t0 + (index + 1) * vc.CHUNK / 16000 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        if index % 50 == 49:
            print(f"[Benchmark] {index + 1}/{n_chunks}, last={times[-1]:.1f}ms, memory={memory()['allocated_mib']:.0f}MiB", flush=True)
    report.update(statistics(times, vc.CHUNK / 16))
    report.update({"benchmark_wall_seconds": time.perf_counter() - t0, "paced": args.paced,
                   "stream_input_seconds": n_chunks * vc.CHUNK / 16000, "stream_output_seconds": emitted / 16000,
                   "steady_cuda_memory": memory(), "processing_times_ms": times,
                   "measurement_scope": "Local model processing; excludes sound card and game/network delay"})
    write_report(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != "processing_times_ms"}, ensure_ascii=False, indent=2), flush=True)


def resolve_device(sd, index, name, hostapi_name, kind):
    """Resolve persisted names again: hot-plugging can change numeric indices."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    channel_key = f"max_{kind}_channels"

    def matches(device):
        return (device[channel_key] > 0
                and (not name or device["name"] == name)
                and (not hostapi_name or hostapis[device["hostapi"]]["name"] == hostapi_name))

    if name or hostapi_name:
        candidates = [position for position, device in enumerate(devices) if matches(device)]
        if index in candidates:
            return index, devices[index]
        if len(candidates) != 1:
            raise RuntimeError(f"Cannot uniquely resolve {kind} device {name!r} / {hostapi_name!r}; "
                               f"matching indices: {candidates}. Refresh devices in the launcher.")
        return candidates[0], devices[candidates[0]]
    if index is None:
        raise RuntimeError(f"An explicit {kind} device index or name is required")
    return index, sd.query_devices(index, kind)


def live(args):
    import sounddevice as sd
    input_device, input_info = resolve_device(sd, args.input_device, args.input_device_name,
                                              args.hostapi_name, "input")
    output_device, output_info = resolve_device(sd, args.output_device, args.output_device_name,
                                                args.hostapi_name, "output")
    if input_info["hostapi"] != output_info["hostapi"]:
        raise RuntimeError("Input and output must use the same audio Host API")
    device_sr = 48000
    sd.check_input_settings(device=input_device, channels=1, dtype="float32", samplerate=device_sr)
    sd.check_output_settings(device=output_device, channels=1, dtype="float32", samplerate=device_sr)
    commands = queue.Queue()
    protocol_lock = threading.Lock()

    def event(kind, command_id=None, **details):
        if args.control_stdin:
            payload = {"event": kind, **details}
            if command_id is not None:
                payload["id"] = command_id
            with protocol_lock:
                print("M2M_EVENT " + json.dumps(payload, ensure_ascii=True), flush=True)

    def read_commands():
        for line in sys.stdin:
            command_id = None
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Expected a JSON object")
                command_id = command.get("id")
                if command.get("command") not in {"mute", "resume", "stop"}:
                    raise ValueError("Unknown command; expected mute, resume, or stop")
                commands.put(command)
            except (ValueError, TypeError) as exc:
                event("error", command_id, message=str(exc))
        # If the launcher disappears, never leave a hidden microphone running.
        commands.put({"command": "stop"})

    if args.control_stdin:
        threading.Thread(target=read_commands, daemon=True, name="voice-control").start()
    vc, report = initialize(args.reference)
    if args.stop_file and Path(args.stop_file).exists():
        report.update({"cancelled_before_audio": True, "faults": []})
        write_report(args.report, report)
        print("[Stopped] Cancelled before opening microphone", flush=True)
        event("stopped")
        return
    stop = threading.Event()
    times = []
    faults = []
    blocksize = vc.CHUNK * 3
    gate = VoiceGate(blocksize, device_sr, start_muted=args.start_muted)
    counters = gate.counters
    stop_command_id = None

    def worker():
        cache_generation = 0
        try:
            while not stop.is_set():
                work = gate.next_work(cache_generation)
                if work is None:
                    continue
                generation, block = work
                if block is None:
                    vc._init_cache()
                    cache_generation = generation
                    continue
                started = time.perf_counter()
                downsampled = resample_poly(block, 1, 3).astype(np.float32)
                with torch.inference_mode():
                    result = vc.process_chunk(downsampled)
                torch.cuda.synchronize()
                times.append((time.perf_counter() - started) * 1000)
                if result is not None:
                    upsampled = resample_poly(result, 3, 1).astype(np.float32)
                    gate.publish(generation, upsampled)
        except Exception as exc:
            faults.append(f"worker: {type(exc).__name__}: {exc}")
            gate.stop()
            stop.set()

    def callback(indata, outdata, frames, time_info, status):
        try:
            if status:
                faults.append(str(status))
            gate.callback(indata, outdata, frames)
        except Exception as exc:
            outdata.fill(0)
            faults.append(f"callback: {type(exc).__name__}: {exc}")
            gate.stop()
            stop.set()
            raise sd.CallbackAbort from exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    started = time.perf_counter()
    last_log = started
    try:
        with sd.Stream(device=(input_device, output_device), channels=1, samplerate=device_sr,
                       blocksize=blocksize, latency="low", dtype="float32", callback=callback) as stream:
            gate.configure_latency(*stream.latency)
            report.update({"input_device": dict(input_info), "output_device": dict(output_info),
                           "device_sample_rate": device_sr, "audio_api_latency_seconds": list(stream.latency)})
            print(f"[Live] Running: {input_info['name']} -> {output_info['name']}. CS2 microphone: CABLE Output", flush=True)
            event("ready", muted=gate.muted, input_device=input_device, output_device=output_device,
                  input_device_name=input_info["name"], output_device_name=output_info["name"],
                  drain_seconds=gate.drain_seconds,
                  resume_capture_delay_seconds=gate.resume_capture_delay_seconds,
                  audio_api_latency_seconds=list(stream.latency))
            while not stop.is_set():
                if not stream.active:
                    faults.append("Audio stream unexpectedly stopped")
                    break
                if args.stop_file and Path(args.stop_file).exists():
                    break
                if args.seconds and time.perf_counter() - started >= args.seconds:
                    break
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    command = None
                if command:
                    command_id = command.get("id")
                    if command["command"] == "stop":
                        stop_command_id = command_id
                        break
                    if command["command"] == "mute":
                        gate.mute()
                        # A mute acknowledgement means samples already submitted
                        # to the sound card have also had time to drain. The
                        # parent must wait for this event before starting a clip.
                        if not gate.wait_for_silence(timeout=2.0):
                            raise RuntimeError("No silent audio callback observed after mute")
                        if stop.wait(gate.drain_seconds):
                            break
                        if not stream.active:
                            raise RuntimeError("Audio stream stopped while draining voice output")
                        event("muted", command_id, drain_seconds=gate.drain_seconds)
                    elif command["command"] == "resume":
                        gate.resume()
                        event("resumed", command_id,
                              capture_delay_seconds=gate.resume_capture_delay_seconds)
                if time.perf_counter() - last_log > 5:
                    stats = statistics(times[-30:], vc.CHUNK / 16)
                    print(f"[Live] mean={stats['mean_processing_ms']}ms, gpu={memory()['allocated_mib']:.0f}MiB, underflows={counters['output_underflows']}", flush=True)
                    last_log = time.perf_counter()
                time.sleep(0.05)
    finally:
        gate.stop()
        stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            faults.append("worker: timeout while stopping CUDA inference")
        report.update(statistics(times, vc.CHUNK / 16))
        report.update({"wall_seconds": time.perf_counter() - started, "counters": counters,
                       "faults": faults, "steady_cuda_memory": memory(),
                       "measurement_scope": "Live physical microphone to explicit output; no game/network latency measurement"})
        write_report(args.report, report)
        print(f"[Stopped] Report: {args.report}", flush=True)
    if any(item.startswith(("worker:", "callback:")) or item == "Audio stream unexpectedly stopped" for item in faults):
        raise RuntimeError(faults[-1])
    event("stopped", stop_command_id)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ["benchmark", "live"]:
        sub = subparsers.add_parser(name)
        sub.add_argument("--reference", required=True)
        sub.add_argument("--report", default=str(ROOT / "results" / f"{name}.json"))
        sub.add_argument("--seconds", type=float, default=0 if name == "live" else 30)
        if name == "benchmark":
            sub.add_argument("--source", required=True)
            sub.add_argument("--output", default=str(ROOT / "results" / "converted.wav"))
            sub.add_argument("--paced", action="store_true")
        else:
            sub.add_argument("--input-device", type=int)
            sub.add_argument("--output-device", type=int)
            sub.add_argument("--input-device-name")
            sub.add_argument("--output-device-name")
            sub.add_argument("--hostapi-name")
            sub.add_argument("--stop-file")
            sub.add_argument("--control-stdin", action="store_true")
            sub.add_argument("--start-muted", action="store_true")
    args = parser.parse_args()
    try:
        {"benchmark": benchmark, "live": live}[args.command](args)
    except Exception as exc:
        if getattr(args, "control_stdin", False):
            print("M2M_EVENT " + json.dumps({"event": "error", "message": f"{type(exc).__name__}: {exc}"},
                                           ensure_ascii=True), flush=True)
        raise


if __name__ == "__main__":
    main()
