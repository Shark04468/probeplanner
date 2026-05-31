#!/usr/bin/env python
"""Validate real layered SDF channels have samples on both sides of safety thresholds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np


DEFAULT_THRESHOLDS_MM = {
    "skull_sdf_mm": 0.45,
    "brain_surface_sdf_mm": 0.45,
    "ventricle_sdf_mm": 0.35,
    "vessel_sdf_mm": 0.60,
    "sulcus_sdf_mm": 0.40,
}


def _resolve_profile_root(path_text: str, profile_name: str) -> Path:
    root = Path(path_text).expanduser().resolve()
    if (root / "layered_sdf.npz").exists():
        return root
    if profile_name:
        candidate = root / profile_name
        if (candidate / "layered_sdf.npz").exists():
            return candidate
    return root


def _channel_result(npz, channel: str, threshold: float, epsilon: float, stride: int) -> dict:
    if channel not in npz.files:
        return {
            "channel": channel,
            "thresholdMm": threshold,
            "exists": False,
            "passed": False,
            "reason": "missing_channel",
        }
    arr = np.asarray(npz[channel])
    sampled = arr[::stride, ::stride, ::stride]
    finite = sampled[np.isfinite(sampled)]
    if finite.size == 0:
        return {
            "channel": channel,
            "thresholdMm": threshold,
            "exists": True,
            "passed": False,
            "reason": "no_finite_samples",
        }
    lower = threshold - epsilon
    upper = threshold + epsilon
    below_band = finite[(finite < threshold) & (finite >= lower)]
    above_band = finite[(finite >= threshold) & (finite <= upper)]
    return {
        "channel": channel,
        "thresholdMm": threshold,
        "epsilonMm": epsilon,
        "exists": True,
        "passed": bool(below_band.size > 0 and above_band.size > 0),
        "sampleStride": stride,
        "finiteSampleCount": int(finite.size),
        "belowBandCount": int(below_band.size),
        "aboveBandCount": int(above_band.size),
        "sampleMinMm": float(np.min(finite)),
        "sampleMaxMm": float(np.max(finite)),
        "reason": "" if below_band.size > 0 and above_band.size > 0 else "threshold_band_not_bracketed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", default=os.environ.get("PROBEPLANNER_ARTIFACT_ROOT", ""))
    parser.add_argument("--profile-name", default=os.environ.get("PROBEPLANNER_MRI_CCF_PROFILE_NAME", ""))
    parser.add_argument("--layered-sdf", default="")
    parser.add_argument("--epsilon-mm", type=float, default=0.025)
    parser.add_argument("--stride", type=int, default=4)
    args = parser.parse_args()

    if args.layered_sdf:
        layered_sdf = Path(args.layered_sdf).expanduser().resolve()
        profile_root = layered_sdf.parent
    else:
        if not args.artifact_root:
            raise SystemExit("Set PROBEPLANNER_ARTIFACT_ROOT or pass --layered-sdf")
        profile_root = _resolve_profile_root(args.artifact_root, args.profile_name)
        layered_sdf = profile_root / "layered_sdf.npz"
    if not layered_sdf.exists():
        raise FileNotFoundError(f"layered_sdf.npz not found: {layered_sdf}")

    stride = max(1, int(args.stride))
    epsilon = max(1e-6, float(args.epsilon_mm))
    with np.load(layered_sdf, allow_pickle=False) as npz:
        channels = [
            _channel_result(npz, channel, threshold, epsilon, stride)
            for channel, threshold in DEFAULT_THRESHOLDS_MM.items()
        ]
    payload = {
        "ok": all(item["passed"] for item in channels),
        "profileRoot": str(profile_root),
        "layeredSdf": str(layered_sdf),
        "channels": channels,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
