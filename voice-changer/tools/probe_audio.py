"""Enumerate audio endpoints or test only the VB-CABLE render/capture path.

Run with voice-changer/.venv/Scripts/python.exe. The cable command plays a known
WAV at reduced gain and records only CABLE Output. It never changes Windows
defaults, records a physical microphone, sends PTT, or opens a game.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import threading
import time


def devices():
    import sounddevice as sd

    apis = sd.query_hostapis()
    return [
        {**dict(d), "index": i, "hostapi_name": apis[d["hostapi"]]["name"]}
        for i, d in enumerate(sd.query_devices())
    ]


def cable(args):
    import numpy as np
    from scipy import signal
    import sounddevice as sd
    import soundfile as sf

    endpoints = devices()
    if not 0 <= args.input_device < len(endpoints) or not 0 <= args.output_device < len(endpoints):
        raise ValueError("Device index is out of range; run list-devices first")
    incoming = endpoints[args.input_device]
    outgoing = endpoints[args.output_device]
    if "cable output" not in incoming["name"].lower():
        raise ValueError("Recording is restricted to a CABLE Output endpoint")
    if "cable input" not in outgoing["name"].lower():
        raise ValueError("Playback is restricted to a CABLE Input endpoint")
    if incoming["hostapi"] != outgoing["hostapi"]:
        raise ValueError("Choose CABLE Input/Output entries from the same host API")
    if incoming["max_input_channels"] < 1 or outgoing["max_output_channels"] < 1:
        raise ValueError("Wrong direction for the selected CABLE endpoints")
    if args.gain <= 0 or args.gain > 0.5:
        raise ValueError("Use a positive playback gain no greater than 0.5")
    if args.tail_seconds < 0.2 or args.tail_seconds > 5:
        raise ValueError("Tail must be between 0.2 and 5 seconds")
    if Path(args.source).resolve() == Path(args.record).resolve():
        raise ValueError("Recording must not overwrite the source sample")

    source, original_rate = sf.read(args.source, dtype="float32", always_2d=True)
    source = source.mean(axis=1)
    if not len(source) or not np.isfinite(source).all():
        raise ValueError("Source must contain finite audio samples")
    rate = args.samplerate
    if original_rate != rate:
        gcd = math.gcd(original_rate, rate)
        source = signal.resample_poly(source, rate // gcd, original_rate // gcd)
    source_peak = float(np.max(np.abs(source)))
    if source_peak < 1e-5:
        raise ValueError("Source sample is silent")
    source = source * min(args.gain, 0.5 / source_peak)
    prefix = int(rate * 0.5)
    suffix = int(rate * args.tail_seconds)
    payload = np.concatenate((np.zeros(prefix), source, np.zeros(suffix))).astype("float32")
    input_channels = min(2, incoming["max_input_channels"])
    output_channels = min(2, outgoing["max_output_channels"])
    recorded = np.zeros((len(payload), input_channels), dtype="float32")
    done = threading.Event()
    callback_statuses = []
    timestamp_offsets_ms = []
    position = 0

    def callback(indata, outdata, frames, time_info, status):
        nonlocal position
        outdata.fill(0)
        count = min(frames, len(payload) - position)
        if count > 0:
            recorded[position:position + count] = indata[:count]
            outdata[:count] = payload[position:position + count, None]
            position += count
        if status:
            callback_statuses.append(dict(position_samples=position, status=str(status)))
        adc = float(time_info.inputBufferAdcTime)
        dac = float(time_info.outputBufferDacTime)
        if adc and dac:
            timestamp_offsets_ms.append((dac - adc) * 1000)
        if position >= len(payload):
            raise sd.CallbackStop

    sd.check_input_settings(device=args.input_device, channels=input_channels,
                            samplerate=rate, dtype="float32")
    sd.check_output_settings(device=args.output_device, channels=output_channels,
                             samplerate=rate, dtype="float32")
    started = time.perf_counter()
    with sd.Stream(device=(args.input_device, args.output_device),
                   samplerate=rate, channels=(input_channels, output_channels),
                   dtype="float32", blocksize=args.blocksize, latency=args.latency,
                   callback=callback, finished_callback=done.set) as stream:
        api_latency = stream.latency
        if not done.wait(len(payload) / rate + 10):
            raise TimeoutError("CABLE stream did not finish in the expected time")
    elapsed = time.perf_counter() - started
    recorded = recorded[:position]
    capture = recorded.mean(axis=1)
    correlations = signal.correlate(capture, source, mode="full", method="fft")
    lags = signal.correlation_lags(len(capture), len(source), mode="full")
    eligible = (lags >= prefix) & (lags <= min(prefix + suffix, len(capture) - len(source)))
    peak_index = np.flatnonzero(eligible)[np.argmax(np.abs(correlations[eligible]))]
    lag = int(lags[peak_index])
    aligned = capture[lag:lag + len(source)]
    source_zero = source - source.mean()
    aligned_zero = aligned - aligned.mean()
    denominator = float(np.linalg.norm(source_zero) * np.linalg.norm(aligned_zero))
    correlation = float(np.dot(source_zero, aligned_zero) / denominator) if denominator else 0.0
    gain = float(np.dot(aligned, source) / np.dot(source, source))
    residual = aligned - gain * source
    rms = float(np.sqrt(np.mean(capture.astype("float64") ** 2)))
    pretest_rms = float(np.sqrt(np.mean(capture[:prefix // 2].astype("float64") ** 2)))
    route_offset_ms = (lag - prefix) / rate * 1000
    timestamp_offset = float(np.median(timestamp_offsets_ms)) if timestamp_offsets_ms else None
    device_estimate = route_offset_ms - timestamp_offset if timestamp_offset is not None else None
    passed = rms > 1e-5 and abs(correlation) > 0.85 and not callback_statuses
    report = {
        "scope": "known WAV -> CABLE Input -> CABLE Output recording; excludes voice model and game",
        "source": str(Path(args.source).resolve()),
        "recording": str(Path(args.record).resolve()),
        "input_device": incoming,
        "output_device": outgoing,
        "samplerate_hz": rate,
        "requested_blocksize_frames": args.blocksize,
        "requested_latency": args.latency,
        "source_duration_seconds": len(source) / rate,
        "elapsed_seconds": elapsed,
        "audio_api_latency_ms": {"input": api_latency[0] * 1000, "output": api_latency[1] * 1000},
        "audio_api_timestamp_dac_minus_adc_ms_median": timestamp_offset,
        "application_cable_roundtrip_ms": route_offset_ms,
        "device_latency_estimate_ms": device_estimate,
        "latency_interpretation": (
            "application_cable_roundtrip_ms is correlation lag on the duplex callback sample timeline. "
            "device_latency_estimate_ms subtracts PortAudio DAC-minus-ADC timestamps and is approximate; "
            "it can be unavailable or invalid on drivers with unreliable timestamps. "
            "Neither field measures voice-model delay or CS2/network voice delay."
        ),
        "playback_gain_requested": args.gain,
        "source_peak_after_gain": float(np.max(np.abs(source))),
        "recording_peak": float(np.max(np.abs(capture))),
        "recording_rms": rms,
        "recording_rms_dbfs": 20 * math.log10(max(rms, 1e-12)),
        "pretest_recording_rms": pretest_rms,
        "normalized_correlation": correlation,
        "estimated_cable_gain": gain,
        "aligned_residual_rms": float(np.sqrt(np.mean(residual.astype("float64") ** 2))),
        "callback_statuses": callback_statuses,
        "passed": passed,
    }
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.record, recorded, rate, subtype="PCM_16")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-devices", help="Enumerate PortAudio devices without starting audio")
    route = commands.add_parser("cable", help="Play a WAV into VB-CABLE and record only CABLE Output")
    route.add_argument("--source", required=True)
    route.add_argument("--input-device", type=int, required=True)
    route.add_argument("--output-device", type=int, required=True)
    route.add_argument("--record", required=True)
    route.add_argument("--report", required=True)
    route.add_argument("--samplerate", type=int, default=48000)
    route.add_argument("--blocksize", type=int, default=0)
    route.add_argument("--latency", choices=["low", "high"], default="low")
    route.add_argument("--gain", type=float, default=0.25)
    route.add_argument("--tail-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "list-devices":
        print(json.dumps(devices(), ensure_ascii=False, indent=2))
        return 0
    return cable(args)


if __name__ == "__main__":
    sys.exit(main())
