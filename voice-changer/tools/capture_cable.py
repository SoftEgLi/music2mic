"""Capture only the virtual microphone during a user-authorized local test."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import sounddevice as sd
import soundfile as sf

parser = argparse.ArgumentParser()
parser.add_argument("--device", type=int, default=23)
parser.add_argument("--seconds", type=float, default=15)
parser.add_argument("--output", required=True)
args = parser.parse_args()
device = sd.query_devices(args.device, "input")
if "cable output" not in device["name"].lower():
    raise SystemExit("This test records only CABLE Output")
audio = sd.rec(round(args.seconds * 48000), samplerate=48000, channels=1,
               dtype="float32", device=args.device)
print("CAPTURE_READY", flush=True)
sd.wait()
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
sf.write(path, audio, 48000, subtype="PCM_16")
summary = {"path": str(path.resolve()), "seconds": args.seconds,
           "rms": float(np.sqrt(np.mean(audio ** 2))), "peak": float(np.max(np.abs(audio))),
           "non_silent_fraction": float(np.mean(np.abs(audio) > .001))}
path.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2), flush=True)
