#!/usr/bin/env python
"""
Validate Qiu2018-mode input artifacts for probe-planner backend.

Checks:
- annotation volume exists and is .npy/.npz
- shape matches expected atlas grid (default: 528,320,456)
- non-zero labels exist
- optional region map/stats/jacobian files parse correctly
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, Tuple

import numpy as np


def parse_shape(text: str) -> Tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected shape like '528,320,456', got: {text!r}")
    return tuple(int(x) for x in parts)  # type: ignore[return-value]


def load_array(path: Path, mmap: bool = True) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        if mmap:
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
        else:
            arr = np.load(path, allow_pickle=False)
        return np.asarray(arr, dtype=np.uint32)
    if suffix == ".npz":
        pack = np.load(path, allow_pickle=False)
        key = "annotation" if "annotation" in pack.files else (pack.files[0] if pack.files else None)
        if not key:
            raise RuntimeError(f"NPZ has no arrays: {path}")
        return np.asarray(pack[key], dtype=np.uint32)
    raise RuntimeError(f"Unsupported annotation format: {path.suffix}. Use .npy or .npz.")


def iter_label_stats(annotation: np.ndarray, chunk_depth: int = 64) -> Tuple[int, int]:
    seen = set()
    nonzero_voxels = 0
    depth = int(annotation.shape[0])
    for z0 in range(0, depth, max(8, int(chunk_depth))):
        z1 = min(depth, z0 + max(8, int(chunk_depth)))
        block = np.asarray(annotation[z0:z1], dtype=np.uint32)
        if block.size == 0:
            continue
        nonzero_voxels += int(np.count_nonzero(block))
        ids = np.unique(block)
        for sid in ids.tolist():
            sid_i = int(sid)
            if sid_i > 0:
                seen.add(sid_i)
    return len(seen), nonzero_voxels


def load_region_map(path: Path) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    out: Dict[str, int] = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            key = str(k).strip()
            if not key:
                continue
            try:
                out[key] = int(v)
            except Exception:
                continue
    elif isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            key = str(row.get("acronym") or row.get("key") or "").strip()
            if not key:
                continue
            try:
                out[key] = int(row.get("id"))
            except Exception:
                continue
    return out


def load_optional_json(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return obj if isinstance(obj, dict) else {}


def load_jacobian(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return np.asarray(np.load(path, mmap_mode="r", allow_pickle=False), dtype=np.float32)
    if suffix == ".npz":
        pack = np.load(path, allow_pickle=False)
        key = "jacobian" if "jacobian" in pack.files else (pack.files[0] if pack.files else None)
        if not key:
            raise RuntimeError(f"Jacobian NPZ has no arrays: {path}")
        return np.asarray(pack[key], dtype=np.float32)
    raise RuntimeError(f"Unsupported jacobian format: {path.suffix}. Use .npy or .npz.")


def compute_jacobian_anomaly_ratio(jac: np.ndarray) -> float | None:
    if jac.size == 0:
        return None
    finite = np.isfinite(jac)
    if not np.any(finite):
        return None
    values = jac[finite]
    bad = np.logical_or(values <= 0.0, np.logical_or(values < 0.2, values > 5.0))
    return float(np.mean(bad))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def write_result(result: Dict[str, object], summary_out: str = "") -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if str(summary_out or "").strip():
        out_path = Path(summary_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Qiu2018 backend input files.")
    ap.add_argument("--annotation", required=True, help="Path to annotation .npy/.npz")
    ap.add_argument("--region-map", default="", help="Optional region map JSON (acronym->id)")
    ap.add_argument("--stats", default="", help="Optional stats JSON for dynamic uncertainty calibration")
    ap.add_argument("--jacobian", default="", help="Optional jacobian .npy/.npz")
    ap.add_argument("--expected-shape", default="528,320,456", help="Expected annotation shape")
    ap.add_argument("--no-memmap", action="store_true", help="Disable mmap for annotation loading")
    ap.add_argument("--summary-out", default="", help="Optional JSON summary output path")
    args = ap.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    summary: Dict[str, object] = {}

    expected_shape = parse_shape(args.expected_shape)
    ann_path = Path(args.annotation).expanduser().resolve()
    summary["annotationPath"] = str(ann_path)

    if not ann_path.exists():
        errors.append(f"Annotation path does not exist: {ann_path}")
    else:
        try:
            annotation = load_array(ann_path, mmap=(not args.no_memmap))
            summary["annotationShape"] = list(annotation.shape)
            summary["annotationDtype"] = str(annotation.dtype)
            summary["annotationMin"] = int(np.min(annotation)) if annotation.size else 0
            summary["annotationMax"] = int(np.max(annotation)) if annotation.size else 0
            summary["annotationSha256"] = sha256_file(ann_path)
            if tuple(annotation.shape) != tuple(expected_shape):
                errors.append(
                    f"Annotation shape mismatch: got {tuple(annotation.shape)}, expected {tuple(expected_shape)}"
                )
            label_count, nonzero_voxels = iter_label_stats(annotation, chunk_depth=64)
            summary["nonzeroLabelCount"] = int(label_count)
            summary["nonzeroVoxelCount"] = int(nonzero_voxels)
            if label_count <= 0:
                errors.append("Annotation has no non-zero labels")
        except Exception as exc:
            errors.append(f"Failed to load annotation: {type(exc).__name__}: {exc}")
            annotation = None

    region_map_path = Path(args.region_map).expanduser().resolve() if args.region_map else None
    if region_map_path:
        if not region_map_path.exists():
            errors.append(f"Region map path does not exist: {region_map_path}")
        else:
            try:
                region_map = load_region_map(region_map_path)
                summary["regionMapPath"] = str(region_map_path)
                summary["regionMapEntries"] = int(len(region_map))
                summary["regionMapSha256"] = sha256_file(region_map_path)
                if len(region_map) == 0:
                    warnings.append("Region map is present but no valid acronym->id entries were parsed")
            except Exception as exc:
                errors.append(f"Failed to parse region map JSON: {type(exc).__name__}: {exc}")
    else:
        warnings.append("No region map provided; IDs will be exposed as RID_<id> aliases")

    stats_path = Path(args.stats).expanduser().resolve() if args.stats else None
    if stats_path:
        if not stats_path.exists():
            errors.append(f"Stats path does not exist: {stats_path}")
        else:
            try:
                stats = load_optional_json(stats_path)
                summary["statsPath"] = str(stats_path)
                summary["statsTopLevelKeys"] = sorted(stats.keys())[:30]
                summary["statsSha256"] = sha256_file(stats_path)
            except Exception as exc:
                errors.append(f"Failed to parse stats JSON: {type(exc).__name__}: {exc}")
    else:
        warnings.append("No stats JSON provided; uncertainty profile will stay on defaults")

    jac_path = Path(args.jacobian).expanduser().resolve() if args.jacobian else None
    if jac_path:
        if not jac_path.exists():
            errors.append(f"Jacobian path does not exist: {jac_path}")
        else:
            try:
                jac = load_jacobian(jac_path)
                summary["jacobianPath"] = str(jac_path)
                summary["jacobianShape"] = list(jac.shape)
                ratio = compute_jacobian_anomaly_ratio(jac)
                summary["jacobianAnomalyRatio"] = None if ratio is None else round(float(ratio), 6)
                summary["jacobianSha256"] = sha256_file(jac_path)
            except Exception as exc:
                errors.append(f"Failed to parse jacobian: {type(exc).__name__}: {exc}")
    else:
        warnings.append("No jacobian file provided; quality gate jacobian metric will be unavailable")

    result = {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }
    write_result(result, args.summary_out)
    return 0 if len(errors) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
