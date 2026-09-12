# Windows installer

The release has a one-file GUI, an external portable CUDA runtime, the complete
voice model files, and preset audio. Users install the resulting package and
do not need Python or pip. The following steps are for the release builder.

Run these commands from the repository root on Windows x64. First prepare the
player and CUDA source environments described in the [main README](../README.md),
and retrieve the real Git LFS assets. The runtime builder copies dependencies
from the validated `voice-changer/.venv/Lib/site-packages` environment.

```powershell
git lfs pull
git lfs fsck
uv pip install --python '.\.venv\Scripts\python.exe' -r '.\packaging\requirements-build.txt'
```

## 1. Build the portable CUDA runtime

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\build_voice_runtime.py' --validate-model
```

This downloads and verifies CPython's official embeddable archive, stages its
runtime and the validated CUDA packages under
`dist/Music2Mic/voice-changer/runtime/`, and checks isolated imports, a CUDA
calculation, and real model conversion. It retains package metadata and license
files while excluding compiled caches and unused development libraries. It
uses NTFS hardlinks when possible; `--copy` forces independent copies. Model
validation reports go under `work/voice-runtime-validation/`.

## 2. Stage the application and build the GUI

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\build_windows.py'
```

This stages tracked backend source, model files, audio, example configuration,
and notices into `dist/Music2Mic/`. It downloads the original prerequisite
installers, checks their Authenticode publishers, and builds `Music2Mic.exe`
using the pinned PyInstaller dependencies. It also writes `build-info.json`.
No development virtual environment is copied into the payload.

The staged directory is the complete application. Keep the EXE together with
its `voice-changer/`, `musics/`, and other supporting files. `--skip-gui` can
restage unchanged assets without rebuilding an already validated GUI.

## 3. Verify the frozen application and compile the installer

Close other Music2Mic instances and run the explicit release smoke mode. This
opens the packaged GUI and a real physical-microphone-to-CABLE audio stream,
exercises preset handoffs and loops, and closes itself. It keeps PTT disabled
and uses temporary configuration. It requires working VB-CABLE and an NVIDIA
driver; it does not test CS2 or voice-network latency.

```powershell
New-Item -ItemType Directory -Path '.\work\release-qa' -Force | Out-Null
$qaReportPath = [System.IO.Path]::GetFullPath('.\work\release-qa\frozen-report.json')
$qaProcess = Start-Process -FilePath '.\dist\Music2Mic\Music2Mic.exe' -ArgumentList @('--release-smoke-report', ('"{0}"' -f $qaReportPath)) -PassThru -Wait
if ($qaProcess.ExitCode -ne 0) { throw 'Frozen application smoke test failed.' }
$qaReport = Get-Content -LiteralPath $qaReportPath -Raw | ConvertFrom-Json
if (-not $qaReport.passed) { throw 'Frozen application report did not pass.' }
```

After the smoke test passes, compile with the portable Inno Setup tool described
below:

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\build_windows.py' --skip-gui --installer
```

The default compiler is `work/tools/InnoSetup7/ISCC.exe`; pass `--iscc` to select
another installation. The installer output is `dist/installer/`. The script's
explicit file exclusions keep runtime reports, logs, `.cache`, `__pycache__`,
and generated `active_*.wav` samples out of the installer even if the staged
application was used for QA.

To compile an already staged payload directly, including a version override:

```powershell
& '.\work\tools\InnoSetup7\ISCC.exe' --define=MyAppVersion=0.1.0 '.\packaging\Music2Mic.iss'
```

`AppSourceDir` and `InstallerOutputDir` can also be overridden with `--define`
compiler definitions. Install the resulting package into a separate test
directory and repeat the frozen smoke test against its installed EXE before
publishing. Leave the optional VB-CABLE installation unchecked on a machine
where the driver is already installed.

```powershell
$installedQaRoot = [System.IO.Path]::GetFullPath('.\work\installed-qa')
Start-Process -FilePath '.\dist\installer\Music2Mic-0.1.0-Windows-x64-Setup.exe' -ArgumentList ('/DIR="{0}"' -f $installedQaRoot) -Wait
```

For the installed smoke test, use `$installedQaRoot\Music2Mic.exe` as the
executable and another absolute report filename under `work/release-qa/`.

## Release files

Publish the generated `Music2Mic-<version>-Windows-x64-Setup.exe` and **every**
matching `Setup-*.bin` file, plus a SHA-256 checksum list. Users download these
files into one folder and launch the EXE. Do not rename the `.bin` files.

GitHub requires each release asset to be smaller than 2 GiB; it does not impose
a total release size or bandwidth limit. The script uses Inno Setup disk
spanning with 1,900,000,000-byte slices to stay below the per-file limit.
See [GitHub release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases),
[DiskSpanning](https://jrsoftware.org/ishelp/topic_setup_diskspanning.htm), and
[DiskSliceSize](https://jrsoftware.org/ishelp/topic_setup_diskslicesize.htm).

Setup installs to `{localappdata}\Programs\Music2Mic` with
`PrivilegesRequired=lowest`. It preserves root `config.json` during upgrades
and uninstall, creates a Start Menu shortcut, and offers an optional desktop
shortcut. Application elevation for game hotkeys belongs to the app launcher;
the installer does not require elevation for copying the application.

## Microsoft runtime prerequisite

Place the verified, original Microsoft Visual C++ x64 Redistributable at
`dist/Music2Mic/drivers/VC_redist.x64.exe`. Obtain it from Microsoft's
[official supported downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
Keep the original package and its Microsoft license terms intact.

The script reads that package's version at compile time. Before optional
driver setup and application launch, it checks `Installed` and `Version` under
`Microsoft\VisualStudio\14.0\VC\Runtimes\x64` in both registry views. If either
view reports an equal or newer installed runtime, installation is skipped.
Otherwise it launches Microsoft's original installer with
`/install /quiet /norestart`, waits for it, and requests UAC elevation for that
prerequisite alone. It checks the registry again afterward and reports an
incomplete prerequisite installation instead of proceeding to launch the app.
See Microsoft's
[redistribution and detection documentation](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files).
NVIDIA graphics drivers remain an external prerequisite.

## Optional VB-CABLE package

If included, place the **complete, original basic** VB-CABLE driver package
under `dist/Music2Mic/drivers/VBCABLE/`, including
`VBCABLE_Setup_x64.exe` and its companion driver files. Verify the official
download and the executable's signature before adding it to the payload.

The installer displays `InstallerInfo.txt` with VB-Audio attribution,
donationware information, and official links. Its final page offers an
unchecked action to launch VB-Audio's original interactive setup with an
explicit UAC prompt. It supplies no silent-install parameters and does not
change Windows audio routing. Without the package, that page offers an
unchecked link to the official download page instead.

VB-Audio's [distribution terms](https://vb-audio.com/Services/licensing.htm)
permit bundling the basic VB-CABLE package when its origin and donationware
model remain visible and end users can donate or pay for their license. The
terms distinguish professional volume distribution and prohibit bundling
VB-CABLE A+B or C+D. Keep the original package notices and these user-facing
attributions with this distribution.

## Build compiler

The [official download page](https://jrsoftware.org/isdl.php) lists Inno Setup
7.1.0 x64 as of 2026-09-13. Verify the download using the
[official verification instructions](https://jrsoftware.org/isdl-verify.php).
The expected Authenticode publisher for that version is `Pyrsys B.V.`.

The Inno Setup installer supports `/PORTABLE=1`, which extracts a portable
compiler without adding uninstall registry entries. Install it into the
workspace tool directory with `/DIR`, `/VERYSILENT`, `/SUPPRESSMSGBOXES`, and
`/NORESTART`; see the [technical notes](https://jrsoftware.org/ishelp/topic_technotes.htm)
and [installer switches](https://jrsoftware.org/ishelp/topic_setupcmdline.htm).
This compiler is a build tool and is not part of the Music2Mic payload.
