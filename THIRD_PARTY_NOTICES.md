# Third-party notices and asset provenance

This repository includes third-party source code, model files, and supplied audio.
Their original notices and terms remain applicable. This document records their
sources; it does not assign a new license to third-party material.

## MeanVC2 source

- Upstream: [ASLP-lab/MeanVC2](https://github.com/ASLP-lab/MeanVC2)
- Included source revision: `01968bcb5e053d87dae8e1a70e5056868e36b2ae`
- Location: `voice-changer/MeanVC2/`
- Attribution: the MeanVC2 authors and ASLP Lab, Northwestern Polytechnical
  University; the upstream README includes the full author list and citation.

The README at this revision declares that MeanVC2 is released under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). The upstream
snapshot did not contain a separate LICENSE file. Its README, attribution,
citation, and disclaimer are retained in the included source directory.
A copy of the official Apache License 2.0 text has been added as
`voice-changer/MeanVC2/LICENSE` for this distribution.

## Model files

Model download locations, sizes, and SHA-256 hashes are recorded in
[`voice-changer/model-manifest.json`](voice-changer/model-manifest.json).
The paths below are relative to `voice-changer/MeanVC2/`.

| Included file | Recorded source |
| --- | --- |
| `ckpts/pretrained_models/meanvc2_40ms_40ms.safetensors` | [ASLP-lab/MeanVC2 on Hugging Face](https://huggingface.co/ASLP-lab/MeanVC2), revision `39cdd19522fe896c227da691314d9a0e3b995486` |
| `ckpts/vocos/vocos.pt` | The same ASLP-lab/MeanVC2 model repository and revision |
| `preprocess/ckpts/fastu2pp_80ms.pt` | The same ASLP-lab/MeanVC2 model repository and revision |
| `preprocess/ckpts/wavlm_large.pt` | [s3prl/converted_ckpts](https://huggingface.co/s3prl/converted_ckpts/blob/main/wavlm_large.pt), a converted checkpoint of [Microsoft WavLM](https://github.com/microsoft/unilm/tree/master/wavlm) |
| `preprocess/ckpts/wavlm_large_finetune.pth` | The [speaker-model download linked by MeanVC2](https://drive.google.com/file/d/1-aE1NfzpRCLxA4GUxX9ITI3F9LlbtEGP/view), used for the WavLM and ECAPA-TDNN speaker encoder |
| `preprocess/ckpts/wavlm_large_cfg.pt` | The `cfg` configuration extracted locally from the included `wavlm_large.pt`; this is not a separately trained model |

The provenance of a model download does not establish a separate license for
every upstream component. Consult the original repositories, model cards, and
their terms for the applicable model conditions. This repository does not claim
that all listed checkpoints have the same license or relicense these files.

The runtime also uses Python, PyTorch, torchaudio, s3prl, and other packages.
Development environments are excluded from the source repository. Windows
release installers include a portable Python runtime and these dependencies;
their bundled license files and package metadata are retained under
`voice-changer/runtime/`, and all packages retain their respective licenses.
GUI dependency notices, including pygame, pynput, Python, Tcl/Tk, libffi, and
PyInstaller's distribution terms, are included under `licenses/gui/` in the
Windows installation. This directory also retains build-package notices.

## Supplied audio

The recordings in `musics/` were supplied with this project. The reference and
source WAV files in `voice-changer/samples/` are local test assets. Inclusion here
does not assert authorship, public-domain status, or a new license for these
recordings, performances, voices, or underlying works. No source-code license
in this repository is intended to relicense the audio.

## External audio driver

[VB-AUDIO Virtual Cable](https://vb-audio.com/Cable/) is a separate product by
VB-Audio Software. Windows release installers include the original BASIC
VB-CABLE driver package as an optional interactive installation, in accordance
with its [distribution conditions](https://vb-audio.com/Services/licensing.htm).
VB-CABLE is donationware; participation and donations are welcome through its
official site. The A+B and C+D products are not included. Driver binaries are
not checked into this source repository, and retain their original terms.

Windows releases also include Microsoft's original signed Visual C++ x64
Redistributable installer as a prerequisite. Its license terms remain
Microsoft's own. The NVIDIA graphics driver is not included.
