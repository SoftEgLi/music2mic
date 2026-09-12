"""Build an application-local CPython/CUDA runtime without virtualenv paths.

The official CPython embeddable archive supplies Python itself. The already
validated voice environment supplies its pinned binary dependencies. Hardlinks
save staging space on NTFS; archive tools store their file contents normally.
No source-environment file is edited, and no recursive deletion is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.11.5"
ARCHIVE_NAME = f"python-{VERSION}-embed-amd64.zip"
ARCHIVE_URL = f"https://www.python.org/ftp/python/{VERSION}/{ARCHIVE_NAME}"
# Published on the official release page. SHA-256 is also recorded in manifest.
ARCHIVE_MD5 = "c5e83dc45630df2236720a18170bf941"
ARCHIVE_SHA256 = "d82391a2e51c3684987c61f6b7cedbff3ce9fbe2e39cd948d32b0da866544b17"
SKIP_DIRS = {"__pycache__", ".git", ".cache"}
SKIP_ROOT = {"docs", "test", "_virtualenv.py", "_virtualenv.pth"}


def file_hash(path, algorithm="sha256"):
    result = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def stage_file(source, destination, hardlinks):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.samefile(destination):
            return "hardlink"
        # Replacing a staging hardlink by unlinking it never modifies source.
        destination.unlink()
    if hardlinks:
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError:
            pass
    shutil.copy2(source, destination)
    return "copy"


def clean_environment(runtime):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV"))}
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join([str(runtime), str(windows / "System32"), str(windows)])
    env["PYTHONUTF8"] = "1"
    return env


def validate_runtime(runtime):
    code = """import json,sys,torch,torchaudio,numpy,scipy,sounddevice,soundfile,numba,librosa
import safetensors,omegaconf,s3prl,x_transformers
assert torch.cuda.is_available(), 'CUDA driver is unavailable'
x=torch.arange(8,device='cuda',dtype=torch.float32)
assert float((x*x).sum().item()) == 140.0
print(json.dumps({'python':sys.version,'executable':sys.executable,'prefix':sys.prefix,
                  'sys_path':sys.path,'torch':torch.__version__,'cuda':torch.version.cuda,
                  'gpu':torch.cuda.get_device_name(0),'numpy':numpy.__version__,
                  'scipy':scipy.__version__,'sounddevice':sounddevice.__version__,
                  'portable_imports_and_cuda_tensor_passed':True},ensure_ascii=True))
"""
    completed = subprocess.run([str(runtime / "python.exe"), "-X", "utf8", "-B", "-c", code],
                               cwd=runtime, env=clean_environment(runtime),
                               text=True, encoding="utf-8", errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(completed.stdout, flush=True)
    if completed.returncode:
        raise RuntimeError(f"Portable runtime validation exited {completed.returncode}")
    result = json.loads(completed.stdout.splitlines()[-1])
    for entry in result["sys_path"]:
        if (entry and not Path(entry).resolve().is_relative_to(runtime)
                and Path(entry).resolve() != runtime.parent / "tools"):
            raise RuntimeError(f"Runtime imports outside its portable directory: {entry}")
    return result


def validate_model(runtime, voice_root):
    """Run real conversion with portable Python, retaining weights in place."""
    runner = voice_root / "tools" / "run_voice_changer.py"
    reference = ROOT / "musics" / "好厉害啊哥哥.mp3"
    reports = ROOT / "work" / "voice-runtime-validation"
    reports.mkdir(parents=True, exist_ok=True)
    # _pth deliberately ignores arbitrary script directories; bootstrap only
    # the explicitly selected backend source while retaining isolated packages.
    bootstrap = ("import pathlib,runpy,sys; p=sys.argv[1]; "
                 "sys.path.insert(0,str(pathlib.Path(p).parent)); "
                 "sys.argv=sys.argv[1:]; runpy.run_path(p,run_name='__main__')")
    command = [str(runtime / "python.exe"), "-X", "utf8", "-B", "-c", bootstrap, str(runner),
               "benchmark", "--reference", str(reference), "--source", str(reference),
               "--seconds", "2", "--report", str(reports / "benchmark.json"),
               "--output", str(reports / "converted.wav")]
    completed = subprocess.run(command, cwd=runtime, env=clean_environment(runtime),
                               text=True, encoding="utf-8", errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (reports / "benchmark.log").write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        print(completed.stdout, flush=True)
        raise RuntimeError(f"Portable CUDA model validation exited {completed.returncode}")
    report = json.loads((reports / "benchmark.json").read_text(encoding="utf-8"))
    if not report["output_finite"] or report["output_rms"] <= 0:
        raise RuntimeError("Portable model did not emit valid audio")
    return {"passed": True, "gpu": report["gpu"], "torch": report["torch"],
            "mean_processing_ms": report["mean_processing_ms"],
            "output_seconds": report["output_seconds"], "output_rms": report["output_rms"],
            "output_finite": report["output_finite"],
            "steady_cuda_memory": report["steady_cuda_memory"]}


def build(args):
    source = args.site_packages.resolve()
    runtime = args.destination.resolve()
    dist = (ROOT / "dist").resolve()
    if runtime == dist or not runtime.is_relative_to(dist) or runtime.is_relative_to(source):
        raise ValueError(f"Staging destination must be a subdirectory of {dist}: {runtime}")
    if not (source / "torch").is_dir():
        raise ValueError(f"Missing installed CUDA packages: {source}")
    runtime.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "work" / "voice-runtime-downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE_NAME
    if not archive.exists():
        print(f"Downloading {ARCHIVE_URL}", flush=True)
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "Music2Mic-release-builder"})
        partial = cache / (ARCHIVE_NAME + ".download")
        with urllib.request.urlopen(request, timeout=60) as response:
            with partial.open("wb") as output:
                shutil.copyfileobj(response, output)
        if file_hash(partial) != ARCHIVE_SHA256:
            raise RuntimeError(f"Downloaded CPython archive checksum mismatch: {partial}")
        partial.replace(archive)
    if file_hash(archive, "md5") != ARCHIVE_MD5 or file_hash(archive) != ARCHIVE_SHA256:
        raise RuntimeError(f"Official CPython archive checksum mismatch: {archive}")
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (runtime / member.filename).resolve()
            if not target.is_relative_to(runtime):
                raise ValueError(f"Unsafe archive member: {member.filename}")
        bundle.extractall(runtime)
    # Relative entries make extraction to a different drive/folder portable.
    # import site enables package metadata while _pth retains isolated mode.
    (runtime / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\n../tools\nimport site\n", encoding="ascii")
    staged = {"hardlink": 0, "copy": 0, "bytes": 0, "skipped": 0,
              "excluded_development_files": 0, "excluded_development_bytes": 0}
    target_packages = runtime / "Lib" / "site-packages"
    for directory, directories, files in os.walk(source):
        relative = Path(directory).relative_to(source)
        directories[:] = [name for name in directories if name not in SKIP_DIRS
                          and (relative != Path(".") or name not in SKIP_ROOT)]
        for filename in files:
            target = target_packages / relative / filename
            development_file = filename.endswith(".lib") or relative.parts[:2] == ("torch", "include")
            if (development_file or filename.endswith((".pyc", ".pyo", ".whl"))
                    or (relative == Path(".") and filename in SKIP_ROOT)):
                staged["skipped"] += 1
                # Static/import libraries support extension development, not
                # DLL loading or this application's prebuilt model inference.
                # Clean only this exact staging file when rebuilding an older
                # runtime that included them; never modify the source hardlink.
                if development_file:
                    staged["excluded_development_files"] += 1
                    staged["excluded_development_bytes"] += (Path(directory) / filename).stat().st_size
                if development_file and target.exists():
                    if not target.resolve().is_relative_to(runtime):
                        raise ValueError(f"Refusing to remove outside runtime: {target}")
                    target.unlink()
                continue
            original = Path(directory) / filename
            method = stage_file(original, target, not args.copy)
            staged[method] += 1
            staged["bytes"] += original.stat().st_size
    header_root = target_packages / "torch" / "include"
    if header_root.exists():
        for directory, _, _ in os.walk(header_root, topdown=False, followlinks=False):
            empty = Path(directory).resolve()
            if not empty.is_relative_to(runtime):
                raise ValueError(f"Refusing to remove outside runtime: {empty}")
            # rmdir only succeeds for an empty directory, never recursively.
            empty.rmdir()
    print(f"Staged packages: {json.dumps(staged)}", flush=True)
    result = {"python_version": VERSION, "python_archive_url": ARCHIVE_URL,
              "python_archive_sha256": file_hash(archive),
              "package_files": staged, "layout": "isolated CPython embeddable + relative Lib/site-packages",
              "source_environment_modified": False}
    if not args.skip_validation:
        result["validation"] = validate_runtime(runtime)
    if args.validate_model:
        result["real_model_validation"] = validate_model(runtime, args.voice_root.resolve())
    (runtime / "runtime-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path,
                        default=ROOT / "voice-changer/.venv/Lib/site-packages")
    parser.add_argument("--destination", type=Path,
                        default=ROOT / "dist/Music2Mic/voice-changer/runtime")
    parser.add_argument("--copy", action="store_true", help="Copy instead of trying same-volume hardlinks")
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--validate-model", action="store_true",
                        help="Also convert the reference through the actual CUDA model")
    parser.add_argument("--voice-root", type=Path, default=ROOT / "voice-changer",
                        help="Backend/model directory for --validate-model")
    build(parser.parse_args())
