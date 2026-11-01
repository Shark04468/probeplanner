"""Shared environment-driven defaults for legacy artifact scripts."""

from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    value = os.environ.get("PROBEPLANNER_WORKSPACE_ROOT")
    if value:
        return Path(value).expanduser()
    return Path(__file__).resolve().parents[3]


def backend_root() -> Path:
    value = os.environ.get("PROBEPLANNER_BACKEND_COMPAT_ROOT")
    if value:
        return Path(value).expanduser()
    return Path(__file__).resolve().parents[1]


def mri_root() -> Path:
    value = os.environ.get("PROBEPLANNER_MRI_CCF_ROOT")
    if value:
        return Path(value).expanduser()
    return workspace_root() / ".data" / "mri_ccf"


def mri_path(relative: str) -> Path:
    return mri_root() / relative


def ants_bin() -> Path:
    value = os.environ.get("PROBEPLANNER_ANTS_BIN")
    if value:
        return Path(value).expanduser()
    return workspace_root() / "tools" / "ants" / "ants-2.6.5-windows-2022-X64-VS2019" / "ants-2.6.5" / "bin"
