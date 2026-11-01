#!/usr/bin/env python
"""
Validate MRI+CCF hybrid artifacts before backend startup.
"""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np


def _parse_triplet(text: str, cast=int) -> Tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated values, got: {text!r}")
    return (cast(parts[0]), cast(parts[1]), cast(parts[2]))


def _load_volume(path: Path, preferred_keys: Optional[Sequence[str]] = None) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return np.asarray(np.load(path, allow_pickle=False))
    if suffix == ".npz":
        pack = np.load(path, allow_pickle=False)
        keys = [str(k) for k in (preferred_keys or ())]
        for key in keys:
            if key in pack.files:
                return np.asarray(pack[key])
        if not pack.files:
            raise RuntimeError(f"NPZ has no arrays: {path}")
        return np.asarray(pack[pack.files[0]])
    raise RuntimeError(f"Unsupported file extension for volume: {path}")


def _must_exist(path_text: str, label: str) -> Path:
    p = Path(path_text).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"{label} not found: {p}")
    return p


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            blk = fh.read(chunk_size)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _resolve_manifest_path(base_dir: Path, raw_path: str) -> Path:
    p = Path(str(raw_path or "").strip())
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def _load_manifest(manifest_path: Path) -> dict:
    with manifest_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise RuntimeError("artifact manifest must be a JSON object")
    return payload


def _write_summary(summary: dict, summary_out: str = "") -> None:
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if str(summary_out or "").strip():
        out_path = Path(summary_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate MRI+CCF hybrid artifacts.")
    ap.add_argument("--manifest", default="", help="artifact_manifest.json (preferred contract input)")
    ap.add_argument("--annotation", default="", help="annotation.npy or annotation.npz")
    ap.add_argument("--expected-shape", default="528,320,456", help="Expected z,y,x shape")
    ap.add_argument("--region-map", default="", help="Optional region_map.json")
    ap.add_argument("--stats", default="", help="Optional stats.json")
    ap.add_argument("--registration-summary", default="", help="Optional registration_summary.json")
    ap.add_argument("--jacobian", default="", help="Optional jacobian.npy/.npz")
    ap.add_argument("--skull-mask", default="", help="Optional skull_mask.npy/.npz")
    ap.add_argument("--outer-tissue-mask", default="", help="Optional outer_tissue_mask.npy/.npz")
    ap.add_argument("--layered-sdf", default="", help="Optional layered_sdf.npy/.npz")
    ap.add_argument("--summary-out", default="", help="Optional JSON summary output path")
    args = ap.parse_args()

    expected_shape = _parse_triplet(args.expected_shape, cast=int)
    summary = {
        "ok": True,
        "expectedShape": list(expected_shape),
        "checks": [],
        "warnings": [],
        "missing": [],
        "hashMismatches": [],
        "manifestErrors": [],
    }
    manifest_expected_hashes = {}
    manifest_actual_hashes = {}

    def hash_artifact(artifact_key: str, path: Path) -> str:
        cache_key = f"{artifact_key}|{str(path.resolve()).lower()}"
        cached = manifest_actual_hashes.get(cache_key)
        if cached:
            return cached
        digest = _sha256_file(path)
        manifest_actual_hashes[cache_key] = digest
        return digest

    def compare_manifest_hash(artifact_key: str, actual_hash: str) -> None:
        expected = str(manifest_expected_hashes.get(artifact_key) or "").strip().lower()
        if not expected:
            return
        if str(actual_hash or "").strip().lower() != expected:
            summary["ok"] = False
            summary["hashMismatches"].append(
                {"key": artifact_key, "expected": expected, "actual": actual_hash}
            )

    if args.manifest:
        manifest_path = _must_exist(args.manifest, "manifest")
        manifest = _load_manifest(manifest_path)
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        if not artifacts:
            raise RuntimeError("manifest.artifacts is missing or empty")
        manifest_expected_hashes = {
            str(k): str(v.get("sha256") or "").strip()
            for k, v in artifacts.items()
            if isinstance(v, dict) and str(v.get("sha256") or "").strip()
        }
        if manifest.get("valid") is False:
            summary["ok"] = False
            summary["manifestErrors"].append("manifest_valid_false")
        for key, rec in artifacts.items():
            if not isinstance(rec, dict) or not bool(rec.get("required", False)):
                continue
            p_text = str(rec.get("path") or "").strip()
            if not p_text:
                summary["missing"].append({"key": str(key), "reason": "path_empty"})
                continue
            p = _resolve_manifest_path(manifest_path.parent, p_text)
            if not p.exists():
                summary["missing"].append({"key": str(key), "reason": "file_missing", "path": str(p)})
                continue
            expected_hash = str(rec.get("sha256") or "").strip().lower()
            if not expected_hash:
                summary["ok"] = False
                summary["manifestErrors"].append(f"required_hash_missing:{key}")
                continue
            compare_manifest_hash(str(key), hash_artifact(str(key), p))
        required_keys = (
            "annotation",
            "region_map",
            "stats",
            "registration_summary",
            "skull_mask",
            "outer_tissue_mask",
            "layered_sdf",
        )
        for key in required_keys:
            rec = artifacts.get(key)
            if not isinstance(rec, dict):
                summary["missing"].append({"key": key, "reason": "manifest_record_missing"})
                continue
            p_text = str(rec.get("path") or "").strip()
            if not p_text:
                summary["missing"].append({"key": key, "reason": "path_empty"})
                continue
            p = _resolve_manifest_path(manifest_path.parent, p_text)
            if not p.exists():
                summary["missing"].append({"key": key, "reason": "file_missing", "path": str(p)})

        jac_rec = artifacts.get("jacobian") if isinstance(artifacts.get("jacobian"), dict) else None
        if isinstance(jac_rec, dict):
            jac_required = bool(jac_rec.get("required", False))
            jac_text = str(jac_rec.get("path") or "").strip()
            if jac_text:
                jac_path = _resolve_manifest_path(manifest_path.parent, jac_text)
                if jac_path.exists():
                    args.jacobian = str(jac_path)
                elif jac_required:
                    summary["missing"].append({"key": "jacobian", "reason": "file_missing", "path": str(jac_path)})
                else:
                    args.jacobian = ""
                    summary["warnings"].append(f"optional jacobian missing: {jac_path}")
            elif jac_required:
                summary["missing"].append({"key": "jacobian", "reason": "path_empty"})

        if summary["missing"]:
            summary["ok"] = False
            _write_summary(summary, args.summary_out)
            raise SystemExit(2)

        # Wire manifest paths into args for unified validation flow below.
        args.annotation = str(_resolve_manifest_path(manifest_path.parent, artifacts["annotation"]["path"]))
        args.region_map = str(_resolve_manifest_path(manifest_path.parent, artifacts["region_map"]["path"]))
        args.stats = str(_resolve_manifest_path(manifest_path.parent, artifacts["stats"]["path"]))
        args.registration_summary = str(
            _resolve_manifest_path(manifest_path.parent, artifacts["registration_summary"]["path"])
        )
        if not str(args.jacobian or "").strip():
            jac_rec_reload = artifacts.get("jacobian") if isinstance(artifacts.get("jacobian"), dict) else None
            if isinstance(jac_rec_reload, dict):
                jac_required_reload = bool(jac_rec_reload.get("required", False))
                jac_path_raw_reload = str(jac_rec_reload.get("path") or "").strip()
                if jac_path_raw_reload:
                    jac_path_reload = _resolve_manifest_path(manifest_path.parent, jac_path_raw_reload)
                    if jac_path_reload.exists():
                        args.jacobian = str(jac_path_reload)
                    elif jac_required_reload:
                        args.jacobian = str(jac_path_reload)
                    else:
                        args.jacobian = ""
                else:
                    args.jacobian = ""
        args.skull_mask = str(_resolve_manifest_path(manifest_path.parent, artifacts["skull_mask"]["path"]))
        args.outer_tissue_mask = str(_resolve_manifest_path(manifest_path.parent, artifacts["outer_tissue_mask"]["path"]))
        args.layered_sdf = str(_resolve_manifest_path(manifest_path.parent, artifacts["layered_sdf"]["path"]))
        summary["checks"].append(
            {
                "name": "artifact_manifest",
                "path": str(manifest_path),
                "schemaVersion": str(manifest.get("schemaVersion") or ""),
                "atlasShape": list(manifest.get("atlasShape") or []),
            }
        )

    if not str(args.annotation or "").strip():
        raise RuntimeError("annotation is required when --manifest is not provided")
    annotation_path = _must_exist(args.annotation, "annotation")
    annotation = _load_volume(annotation_path, preferred_keys=("annotation",))
    if annotation.ndim != 3:
        raise RuntimeError(f"annotation must be 3D, got shape {tuple(annotation.shape)}")
    if tuple(annotation.shape) != tuple(expected_shape):
        raise RuntimeError(
            f"annotation shape mismatch: got {tuple(annotation.shape)} expected {tuple(expected_shape)}"
        )
    annotation_hash = hash_artifact("annotation", annotation_path)
    compare_manifest_hash("annotation", annotation_hash)
    summary["checks"].append(
        {
            "name": "annotation",
            "path": str(annotation_path),
            "shape": list(annotation.shape),
            "dtype": str(annotation.dtype),
            "nonzeroVoxelCount": int(np.count_nonzero(annotation)),
            "labelCount": int(len(np.unique(annotation)) - (1 if np.any(annotation == 0) else 0)),
            "sha256": annotation_hash,
        }
    )

    if args.region_map:
        region_map_path = _must_exist(args.region_map, "region_map")
        with region_map_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, (dict, list)):
            raise RuntimeError("region_map must be a dict or list")
        if isinstance(payload, dict):
            count = len(payload)
        else:
            count = len([x for x in payload if isinstance(x, dict)])
        region_map_hash = hash_artifact("region_map", region_map_path)
        compare_manifest_hash("region_map", region_map_hash)
        summary["checks"].append(
            {
                "name": "region_map",
                "path": str(region_map_path),
                "entries": int(count),
                "sha256": region_map_hash,
            }
        )

    if args.stats:
        stats_path = _must_exist(args.stats, "stats")
        with stats_path.open("r", encoding="utf-8") as fh:
            stats = json.load(fh)
        if not isinstance(stats, dict):
            raise RuntimeError("stats.json must be an object")
        metrics = stats.get("metrics") if isinstance(stats.get("metrics"), dict) else {}
        stats_hash = hash_artifact("stats", stats_path)
        compare_manifest_hash("stats", stats_hash)
        summary["checks"].append(
            {
                "name": "stats",
                "path": str(stats_path),
                "keys": sorted(list(stats.keys()))[:20],
                "metrics": {
                    "dice": metrics.get("dice", stats.get("dice")),
                    "hd95Mm": metrics.get("hd95Mm", stats.get("hd95Mm")),
                    "treMm": metrics.get("treMm", stats.get("treMm")),
                    "jacobianAnomalyRatio": metrics.get("jacobianAnomalyRatio", stats.get("jacobianAnomalyRatio")),
                },
                "sha256": stats_hash,
            }
        )

    if args.registration_summary:
        reg_path = _must_exist(args.registration_summary, "registration_summary")
        with reg_path.open("r", encoding="utf-8") as fh:
            registration_summary = json.load(fh)
        if not isinstance(registration_summary, dict):
            raise RuntimeError("registration_summary.json must be an object")
        reg_hash = hash_artifact("registration_summary", reg_path)
        compare_manifest_hash("registration_summary", reg_hash)
        summary["checks"].append(
            {
                "name": "registration_summary",
                "path": str(reg_path),
                "keys": sorted(list(registration_summary.keys()))[:30],
                "sha256": reg_hash,
            }
        )

    if args.jacobian:
        jac_path = _must_exist(args.jacobian, "jacobian")
        jac = _load_volume(jac_path, preferred_keys=("jacobian",))
        if jac.ndim != 3:
            raise RuntimeError(f"jacobian must be 3D, got {tuple(jac.shape)}")
        if tuple(jac.shape) != tuple(expected_shape):
            raise RuntimeError(
                f"jacobian shape mismatch: got {tuple(jac.shape)} expected {tuple(expected_shape)}"
            )
        finite = np.isfinite(jac)
        if not np.any(finite):
            raise RuntimeError("jacobian contains no finite values")
        vals = jac[finite]
        bad = np.logical_or(vals <= 0.0, np.logical_or(vals < 0.2, vals > 5.0))
        jac_hash = hash_artifact("jacobian", jac_path)
        compare_manifest_hash("jacobian", jac_hash)
        summary["checks"].append(
            {
                "name": "jacobian",
                "path": str(jac_path),
                "shape": list(jac.shape),
                "jacobianAnomalyRatio": float(np.mean(bad)),
                "sha256": jac_hash,
            }
        )

    def _check_optional_mask(path_text: str, label: str, keys: Sequence[str]):
        if not path_text:
            return
        p = _must_exist(path_text, label)
        arr = _load_volume(p, preferred_keys=keys)
        if arr.ndim != 3:
            raise RuntimeError(f"{label} must be 3D, got {tuple(arr.shape)}")
        if tuple(arr.shape) != tuple(expected_shape):
            raise RuntimeError(
                f"{label} shape mismatch: got {tuple(arr.shape)} expected {tuple(expected_shape)}"
            )
        mask_hash = hash_artifact(label, p)
        compare_manifest_hash(label, mask_hash)
        summary["checks"].append(
            {
                "name": label,
                "path": str(p),
                "shape": list(arr.shape),
                "nonzeroVoxelCount": int(np.count_nonzero(arr)),
                "sha256": mask_hash,
            }
        )

    _check_optional_mask(args.skull_mask, "skull_mask", ("mask", "skull_mask", "skull"))
    _check_optional_mask(
        args.outer_tissue_mask,
        "outer_tissue_mask",
        ("mask", "outer_tissue_mask", "outer_tissue", "cortex_outside"),
    )
    if args.layered_sdf:
        p = _must_exist(args.layered_sdf, "layered_sdf")
        arr = _load_volume(p, preferred_keys=("signed_sdf_mm", "layered_sdf", "sdf"))
        if arr.ndim != 3:
            raise RuntimeError(f"layered_sdf must be 3D, got {tuple(arr.shape)}")
        if tuple(arr.shape) != tuple(expected_shape):
            raise RuntimeError(
                f"layered_sdf shape mismatch: got {tuple(arr.shape)} expected {tuple(expected_shape)}"
            )
        layer_keys = []
        if p.suffix.lower() == ".npz":
            pack = np.load(p, allow_pickle=False)
            layer_keys = sorted(pack.files)
            required_channels = {
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
            }
            missing_channels = sorted([k for k in required_channels if k not in pack.files])
            if missing_channels:
                summary["warnings"].append(
                    f"layered_sdf missing channels: {', '.join(missing_channels)}"
                )
        sdf_hash = hash_artifact("layered_sdf", p)
        compare_manifest_hash("layered_sdf", sdf_hash)
        summary["checks"].append(
            {
                "name": "layered_sdf",
                "path": str(p),
                "shape": list(arr.shape),
                "keys": layer_keys,
                "sha256": sdf_hash,
            }
        )

    _write_summary(summary, args.summary_out)
    return 0 if summary.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
