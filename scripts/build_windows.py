"""Stage a self-contained Windows app and optionally build its split installer.

Run using the project .venv after installing packaging/requirements-build.txt.
The portable voice runtime is built separately by build_voice_runtime.py.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'dist' / 'Music2Mic'
DOWNLOADS = ROOT / 'work' / 'downloads'
VERSION = '0.1.0'


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def stage(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.samefile(destination):
            return
        destination.unlink()
    # Model weights are immutable build input. Keep extra disk usage small.
    if source.suffix in ('.pt', '.pth', '.safetensors', '.mp3', '.wav'):
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    shutil.copy2(source, destination)


def download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        print('Downloading', url, flush=True)
        urllib.request.urlretrieve(url, destination)


def verify_signature(path, publisher):
    # Pass the path through an environment variable, never interpolate shell code.
    env = dict(os.environ, M2M_SIGNATURE_FILE=str(path))
    # PowerShell 7's inherited module paths cannot be loaded by Windows PS 5.1.
    env = {key: value for key, value in env.items() if key.upper() != 'PSMODULEPATH'}
    command = '$s = Get-AuthenticodeSignature -LiteralPath $env:M2M_SIGNATURE_FILE; '
    command += '[pscustomobject]@{Status=$s.Status.ToString();Publisher=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress'
    powershell = Path(env.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    value = json.loads(subprocess.check_output([str(powershell), '-NoProfile', '-Command', command], env=env))
    if value['Status'] != 'Valid' or publisher.lower() not in value['Publisher'].lower():
        raise RuntimeError(f'Unexpected signature for {path.name}: {value}')
    return value


def prerequisites():
    archive = DOWNLOADS / 'VBCABLE_Driver_Pack45.zip'
    download('https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip', archive)
    cable = APP / 'drivers' / 'VBCABLE'
    cable.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            if not (cable / item.filename).resolve().is_relative_to(cable.resolve()):
                raise ValueError('Unsafe driver archive path')
        zipped.extractall(cable)
    redist = APP / 'drivers' / 'VC_redist.x64.exe'
    download('https://aka.ms/vs/17/release/vc_redist.x64.exe', redist)
    return {'VB-CABLE': verify_signature(cable / 'VBCABLE_Setup_x64.exe', 'BUREL VINCENT'),
            'Microsoft VC++': verify_signature(redist, 'Microsoft Corporation')}


def stage_payload():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    prefixes = ('musics/', 'voice-changer/MeanVC2/', 'voice-changer/tools/', 'voice-changer/samples/')
    extras = ['config.example.json', 'README.md', 'THIRD_PARTY_NOTICES.md',
              'voice-changer/model-manifest.json', 'voice-changer/requirements-runtime.txt',
              'packaging/InstallerInfo.txt']
    files = {name for name in tracked if name.startswith(prefixes)} | set(extras)
    for name in sorted(files):
        source = ROOT / name
        if not source.is_file():
            raise FileNotFoundError(source)
        stage(source, APP / name)
    for name in ('ckpts/vocos/vocos.pt', 'ckpts/pretrained_models/meanvc2_40ms_40ms.safetensors',
                 'preprocess/ckpts/fastu2pp_80ms.pt', 'preprocess/ckpts/wavlm_large_finetune.pth',
                 'preprocess/ckpts/wavlm_large.pt'):
        if (APP / 'voice-changer' / 'MeanVC2' / name).stat().st_size < 1_000_000:
            raise RuntimeError(f'Missing real model payload (run git lfs pull): {name}')
    if not (APP / 'voice-changer' / 'runtime' / 'python.exe').exists():
        raise RuntimeError('Build the portable voice runtime first')
    return len(files)


def stage_gui_licenses():
    licenses = APP / 'licenses' / 'gui'
    for distribution in importlib.metadata.distributions():
        for relative in distribution.files or ():
            if '.dist-info' not in relative.as_posix():
                continue
            if relative.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    stage(source, licenses / relative.as_posix())
    import pygame
    pygame_license = Path(pygame.__file__).parent / 'docs/generated/LGPL.txt'
    if not pygame_license.is_file():
        raise RuntimeError('The pygame LGPL license must accompany the release')
    stage(pygame_license, licenses / 'pygame/LGPL.txt')
    base = Path(sys.base_prefix)
    for name in ('LICENSE_PYTHON.txt', 'Library/lib/tk8.6/license.terms'):
        source = base / name
        if source.is_file():
            stage(source, licenses / 'python-and-tk' / name)
    # Conda's native libffi/Tcl packages keep license texts in their package
    # metadata, separately from the runtime DLL and data locations.
    for pattern in ('libffi-*/info/licenses/*', 'tk-*/info/licenses/*/license.terms'):
        for source in (base / 'pkgs').glob(pattern):
            if source.is_file():
                stage(source, licenses / 'native' / source.relative_to(base / 'pkgs'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-gui', action='store_true')
    parser.add_argument('--installer', action='store_true')
    parser.add_argument('--iscc', type=Path, default=ROOT / 'work/tools/InnoSetup7/ISCC.exe')
    args = parser.parse_args()
    APP.mkdir(parents=True, exist_ok=True)
    files = stage_payload()
    stage_gui_licenses()
    signatures = prerequisites()
    if not args.skip_gui:
        # A venv created from Conda may not expose these DLLs to PyInstaller's
        # dependency resolver, although _ctypes and Tk require them at runtime.
        native_dependencies = []
        for name in ('ffi.dll', 'tk86t.dll', 'tcl86t.dll'):
            dependency = Path(sys.base_prefix) / 'Library' / 'bin' / name
            if dependency.is_file():
                native_dependencies += ['--add-binary', str(dependency) + os.pathsep + '.']
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
             '--name', 'Music2Mic', '--distpath', APP, '--workpath', ROOT / 'work/build-gui',
             '--specpath', ROOT / 'work', '--hidden-import', 'pygame._sdl2.audio',
             '--hidden-import', 'pynput.keyboard._win32', '--hidden-import', 'pynput.mouse._win32',
             '--hidden-import', 'release_smoke', '--exclude-module', 'torch',
             '--exclude-module', 'numpy', *native_dependencies, ROOT / 'main.py'])
    manifest = {'version': VERSION, 'source_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'staged_source_files': files,
        'prerequisite_signatures': signatures,
        'exe_sha256': hashlib.sha256((APP / 'Music2Mic.exe').read_bytes()).hexdigest()}
    (APP / 'build-info.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.installer:
        run([args.iscc, '/Qp', f'/DMyAppVersion={VERSION}', ROOT / 'packaging/Music2Mic.iss'])
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
