"""Compare three audio files with the locally installed MeanVC2 speaker encoder.

This is a diagnostic, not an independent perceptual quality benchmark. No model
downloads are performed; both speaker checkpoints must already exist locally.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "MeanVC2"
CACHE_ROOT = ROOT / ".cache"


def configure_local_runtime() -> None:
    # Set these before importing torch or s3prl. The explicit WavLM config below
    # selects the official encoder's local-only construction path.
    os.environ["HF_HOME"] = str(CACHE_ROOT / "huggingface")
    os.environ["TORCH_HOME"] = str(CACHE_ROOT / "torch")
    os.environ["XDG_CACHE_HOME"] = str(CACHE_ROOT)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def audio_statistics(path: Path) -> dict:
    import numpy as np
    import soundfile as sf

    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    finite = bool(np.isfinite(samples).all())
    # Match the official speaker encoder's channel averaging convention.
    mono = samples.astype(np.float64).mean(axis=1)
    nonempty = bool(len(mono))
    rms = float(np.sqrt(np.mean(mono * mono))) if finite and nonempty else None
    return {
        "path": str(path),
        "sample_rate_hz": int(sample_rate),
        "channels": int(samples.shape[1]),
        "frames": int(samples.shape[0]),
        "duration_seconds": float(samples.shape[0] / sample_rate),
        "mono_rms": rms,
        "peak_absolute": float(np.max(np.abs(samples))) if finite and nonempty else None,
        "all_samples_finite": finite,
        "nonempty": nonempty,
        "non_silent": bool(rms is not None and rms > 0),
    }


def load_official_speaker_module():
    module_path = MODEL_ROOT / "runtime" / "src" / "speaker.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"Official speaker module is missing: {module_path}")
    spec = importlib.util.spec_from_file_location("meanvc2_scoring_speaker", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load speaker module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(paths: dict[str, Path], report: dict) -> None:
    import torch

    torch.set_num_threads(1)
    torch.manual_seed(42)
    report["torch_version"] = torch.__version__

    for label, path in paths.items():
        report["audio"][label] = audio_statistics(path)

    invalid = [
        label
        for label, stats in report["audio"].items()
        if not stats["all_samples_finite"] or not stats["nonempty"] or not stats["non_silent"]
    ]
    if invalid:
        raise ValueError(f"Audio is empty, silent, or non-finite: {', '.join(invalid)}")

    checkpoint = MODEL_ROOT / "preprocess" / "ckpts" / "wavlm_large_finetune.pth"
    config = MODEL_ROOT / "preprocess" / "ckpts" / "wavlm_large_cfg.pt"
    for path in (checkpoint, config):
        if not path.is_file():
            raise FileNotFoundError(f"Local checkpoint is missing; downloads are disabled: {path}")
    report["speaker_encoder"] = {
        "implementation": str(MODEL_ROOT / "runtime" / "src" / "speaker.py"),
        "checkpoint": str(checkpoint),
        "config": str(config),
        "device": "cpu",
    }

    speaker = load_official_speaker_module()
    model = speaker.init_speaker_model(
        str(checkpoint), device="cpu", wavlm_config=str(config)
    )
    embeddings = {}
    with torch.inference_mode():
        for label, path in paths.items():
            embedding = speaker.extract_embedding(model, str(path), device="cpu")
            embedding = embedding.detach().cpu().double().reshape(-1)
            finite = bool(torch.isfinite(embedding).all().item())
            norm = float(torch.linalg.vector_norm(embedding).item()) if finite else None
            report["embeddings"][label] = {
                "dimensions": int(embedding.numel()),
                "all_values_finite": finite,
                "l2_norm": norm,
            }
            if not finite or norm is None or norm == 0:
                raise ValueError(f"Invalid speaker embedding for {label}")
            embeddings[label] = embedding / norm

    def cosine(left: str, right: str) -> float:
        return float(torch.dot(embeddings[left], embeddings[right]).clamp(-1, 1).item())

    similarities = {
        "reference_source": cosine("reference", "source"),
        "reference_converted": cosine("reference", "converted"),
        "source_converted": cosine("source", "converted"),
    }
    report["cosine_similarity"] = similarities
    report["reference_similarity_change"] = (
        similarities["reference_converted"] - similarities["reference_source"]
    )
    report["status"] = "completed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--converted", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True, help="Output JSON report")
    args = parser.parse_args()
    paths = {
        label: getattr(args, label).expanduser().resolve()
        for label in ("reference", "source", "converted")
    }
    destination = args.report.expanduser().resolve()
    if destination in paths.values():
        parser.error("--report must not overwrite an input audio file")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "audio": {},
        "embeddings": {},
        "cosine_similarity": None,
        "limitations": [
            "此评估使用与 MeanVC2 音色编码相同的 WavLM + ECAPA-TDNN 模型，"
            "余弦相似度不是独立的转换质量证明。",
            "分数仅用于同一模型内比较，不代表像原说话人的百分比，未设通过阈值。",
            "短参考音频、内容、噪声和录音条件可能影响分数；自然度、可懂度、"
            "语义保真及实时延迟仍需另行试听或测试。",
        ],
    }
    configure_local_runtime()
    try:
        evaluate(paths, report)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Report: {destination}")
    if report["cosine_similarity"] is not None:
        print(json.dumps(report["cosine_similarity"], indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
