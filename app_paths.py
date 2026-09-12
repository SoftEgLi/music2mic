"""Locate writable application data beside the launcher, including frozen builds."""

from pathlib import Path
import sys


def app_root():
    """Return the source directory or the installed EXE directory.

    A one-file bundle extracts Python modules to a temporary directory. Presets,
    configuration, logs, and the separate CUDA runtime live beside the EXE.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
