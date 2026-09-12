# Windows installer

`Music2Mic.iss` packages the prepared `dist/Music2Mic/` tree. It does not create
that tree or install development dependencies. The payload must contain
`Music2Mic.exe`, `config.example.json`, the portable CUDA runtime, the
`voice-changer/` source and model paths, `musics/`, and third-party notices.
The executable may be a PyInstaller one-file GUI; its external CUDA runtime and
model files remain at their prepared paths.

Compile with Inno Setup 7 from the repository root:

```powershell
& '.\work\tools\InnoSetup7\ISCC.exe' --define=MyAppVersion=0.1.0 '.\packaging\Music2Mic.iss'
```

`AppSourceDir` and `InstallerOutputDir` can also be overridden with `--define` compiler
definitions. The default output is `dist/installer/`.

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
