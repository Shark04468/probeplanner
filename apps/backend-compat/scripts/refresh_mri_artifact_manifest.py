#!/usr/bin/env python
"""Refresh an MRI+CCF artifact_manifest.json after stats/Jacobian updates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


LARGE_HASH_LIMIT = 128 * 1024 * 1024


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_shape(text: str) -> Tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected z,y,x shape, got {text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _load_old_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _old_hash(old: dict, key: str, path: Path) -> str:
    try:
        meta = old.get("artifacts", {}).get(key, {})
        if Path(str(meta.get("path", ""))).resolve() == path.resolve():
            return str(meta.get("sha256", "") or "")
    except Exception:
        return ""
    return ""


def _array_shape(path: Path, fallback: Optional[Tuple[int, int, int]]) -> Optional[Tuple[int, ...]]:
    if not path.exists():
        return fallback
    if path.suffix.lower() == ".npy":
        arr = np.load(path, mmap_mode="r", allow_pickle=False)
        return tuple(int(x) for x in arr.shape)
    # Avoid decompressing huge .npz files only to obtain shape.
    return fallback


def _meta(path: Path, *, kind: str, required: bool, shape, old: dict, key: str, hash_large: bool) -> dict:
    exists = path.exists()
    digest = ""
    if exists:
        if hash_large or path.stat().st_size <= LARGE_HASH_LIMIT:
            digest = _sha256_file(path)
        else:
            digest = _old_hash(old, key, path)
            if not digest:
                digest = _sha256_file(path)
    return {
        "kind": kind,
        "required": bool(required),
        "path": str(path),
        "exists": bool(exists),
        "shape": list(shape) if shape is not None else None,
        "sha256": digest,
        "version": "v1",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh MRI+CCF artifact manifest.")
    ap.add_argument("--profile-dir", required=True)
    ap.add_argument("--profile-id", default="")
    ap.add_argument("--expected-shape", default="528,320,456")
    ap.add_argument("--hash-large", action="store_true", help="Recompute hashes for large volumes.")
    args = ap.parse_args()

    profile_dir = Path(args.profile_dir).expanduser().resolve()
    if not profile_dir.exists():
        raise FileNotFoundError(f"Profile directory not found: {profile_dir}")
    expected_shape = _parse_shape(args.expected_shape)
    manifest_path = profile_dir / "artifact_manifest.json"
    old = _load_old_manifest(manifest_path)
    profile_id = str(args.profile_id or old.get("profileId") or profile_dir.name)

    paths = {
        "annotation": profile_dir / "annotation.npy",
        "region_map": profile_dir / "region_map.json",
        "region_map_v2": profile_dir / "region_map_v2.json",
        "stats": profile_dir / "stats.json",
        "registration_summary": profile_dir / "registration" / "registration_summary.json",
        "warped_mri": profile_dir / "registration" / "ambmc_in_ccf.nii.gz",
        "skull_mask": profile_dir / "skull_mask.npy",
        "outer_tissue_mask": profile_dir / "outer_tissue_mask.npy",
        "layered_sdf": profile_dir / "layered_sdf.npz",
        "jacobian": profile_dir / "jacobian.npy",
    }
    shape = _array_shape(paths["annotation"], expected_shape)
    artifacts = {
        "annotation": _meta(paths["annotation"], kind="volume", required=True, shape=shape, old=old, key="annotation", hash_large=args.hash_large),
        "region_map": _meta(paths["region_map"], kind="json", required=True, shape=None, old=old, key="region_map", hash_large=True),
        "region_map_v2": _meta(paths["region_map_v2"], kind="json", required=False, shape=None, old=old, key="region_map_v2", hash_large=True),
        "stats": _meta(paths["stats"], kind="json", required=True, shape=None, old=old, key="stats", hash_large=True),
        "registration_summary": _meta(paths["registration_summary"], kind="json", required=True, shape=None, old=old, key="registration_summary", hash_large=True),
        "warped_mri": _meta(paths["warped_mri"], kind="image", required=False, shape=expected_shape, old=old, key="warped_mri", hash_large=args.hash_large),
        "skull_mask": _meta(paths["skull_mask"], kind="mask", required=True, shape=_array_shape(paths["skull_mask"], expected_shape), old=old, key="skull_mask", hash_large=args.hash_large),
        "outer_tissue_mask": _meta(paths["outer_tissue_mask"], kind="mask", required=True, shape=_array_shape(paths["outer_tissue_mask"], expected_shape), old=old, key="outer_tissue_mask", hash_large=args.hash_large),
        "layered_sdf": _meta(paths["layered_sdf"], kind="sdf", required=True, shape=expected_shape, old=old, key="layered_sdf", hash_large=args.hash_large),
        "jacobian": _meta(paths["jacobian"], kind="volume", required=False, shape=_array_shape(paths["jacobian"], expected_shape), old=old, key="jacobian", hash_large=args.hash_large),
    }
    artifacts["layered_sdf"]["version"] = "v2"
    artifacts["layered_sdf"]["channels"] = [
        "signed_sdf_mm",
        "skull_sdf_mm",
        "outer_sdf_mm",
        "outer_tissue_sdf_mm",
        "ventricle_sdf_mm",
        "vessel_sdf_mm",
        "sulcus_sdf_mm",
        "cortex_outside_sdf_mm",
        "brain_surface_sdf_mm",
        "drilling_surface_sdf_mm",
    ]
    manifest = {
        "schemaVersion": "probeplanner.mri_ccf.artifact_manifest.v1",
        "generatedAt": _utc_now_iso(),
        "profileId": profile_id,
        "atlasShape": [int(x) for x in expected_shape],
        "voxelSizeUm": 25,
        "artifacts": artifacts,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    missing = [k for k, v in artifacts.items() if v["required"] and not v["exists"]]
    out = {"ok": not missing, "manifestPath": str(manifest_path), "profileId": profile_id, "missing": missing}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if missing:
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
