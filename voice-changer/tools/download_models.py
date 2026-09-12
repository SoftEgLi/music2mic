"""Download only public upstream inference weights into this installation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "MeanVC2"


def download(url, relative_path, expected_size=None, expected_sha=None):
    dest = REPO / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        print(f"EXISTS {relative_path}: {dest.stat().st_size}", flush=True)
    else:
        partial = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(3):
            try:
                print(f"DOWNLOAD {relative_path}", flush=True)
                with requests.get(url, stream=True, timeout=(30, 120)) as response:
                    response.raise_for_status()
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise RuntimeError("Download returned an HTML page instead of model weights")
                    total = int(response.headers.get("Content-Length", 0))
                    received = 0
                    last = time.monotonic()
                    with partial.open("wb") as out:
                        for chunk in response.iter_content(2 * 1024 * 1024):
                            out.write(chunk)
                            received += len(chunk)
                            if time.monotonic() - last > 15:
                                print(f"PROGRESS {dest.name}: {received / 1048576:.0f} / {total / 1048576:.0f} MiB", flush=True)
                                last = time.monotonic()
                if expected_size and received != expected_size:
                    raise RuntimeError(f"Size mismatch: {received} != {expected_size}")
                partial.replace(dest)
                break
            except Exception as exc:
                print(f"RETRY {dest.name}: {type(exc).__name__}: {str(exc)[:220]}", flush=True)
                if attempt == 2:
                    raise
                time.sleep(3)
    digest = hashlib.sha256()
    with dest.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    actual_sha = digest.hexdigest()
    if expected_sha and actual_sha != expected_sha:
        raise RuntimeError(f"SHA256 mismatch: {dest.name}")
    print(f"VERIFIED {dest.name}: {dest.stat().st_size / 1048576:.1f} MiB", flush=True)
    return {"file": relative_path, "url": url, "bytes": dest.stat().st_size, "sha256": actual_sha}


def main():
    response = requests.get("https://huggingface.co/api/models/ASLP-lab/MeanVC2?blobs=true", timeout=30)
    response.raise_for_status()
    metadata = response.json()
    entries = {item["rfilename"]: item for item in metadata["siblings"]}
    jobs = []
    for name, path in [
        ("fastu2pp_80ms.pt", "preprocess/ckpts/fastu2pp_80ms.pt"),
        ("meanvc2_40ms_40ms.safetensors", "ckpts/pretrained_models/meanvc2_40ms_40ms.safetensors"),
        ("vocos.pt", "ckpts/vocos/vocos.pt"),
    ]:
        info = entries[name]
        jobs.append((f"https://huggingface.co/ASLP-lab/MeanVC2/resolve/{metadata['sha']}/{name}?download=true", path, info.get("size"), info.get("lfs", {}).get("sha256")))
    jobs += [
        # Upstream s3prl's official wavlm_large hubconf points to this native checkpoint.
        ("https://huggingface.co/s3prl/converted_ckpts/resolve/main/wavlm_large.pt?download=true", "preprocess/ckpts/wavlm_large.pt", None, None),
        ("https://drive.usercontent.google.com/download?id=1-aE1NfzpRCLxA4GUxX9ITI3F9LlbtEGP&export=download&confirm=t", "preprocess/ckpts/wavlm_large_finetune.pth", None, None),
    ]
    records = []
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(download, *job): job[1] for job in jobs}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as exc:
                errors.append({"file": futures[future], "error": str(exc)[:500]})
    manifest = {"huggingface_revision": metadata["sha"], "files": records, "errors": errors}
    (ROOT / "model-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if errors:
        raise SystemExit(json.dumps(errors, indent=2))


if __name__ == "__main__":
    main()
