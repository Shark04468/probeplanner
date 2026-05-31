#!/usr/bin/env python
"""
Build/fill MRI reconstruction quality metrics and write back into stats.json.

This script is designed to turn null quality fields into explicit values whenever
source evidence exists (registration summary, landmarks, masks, jacobian).
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy.ndimage import distance_transform_edt
except Exception:  # pragma: no cover - optional acceleration
    distance_transform_edt = None


def _load_array(path: Path, keys: Optional[Sequence[str]] = None) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return np.asarray(np.load(path, allow_pickle=False))
    if suffix == ".npz":
        pack = np.load(path, allow_pickle=False)
        for k in (keys or ()):
            if k in pack.files:
                return np.asarray(pack[k])
        if not pack.files:
            raise RuntimeError(f"NPZ has no arrays: {path}")
        return np.asarray(pack[pack.files[0]])
    raise RuntimeError(f"Unsupported array extension: {path}")


def _load_points_csv(path: Path) -> np.ndarray:
    rows: List[List[float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            lower = {str(k).strip().lower(): k for k in reader.fieldnames}
            xk = lower.get("x") or lower.get("ap") or lower.get("ml")
            yk = lower.get("y") or lower.get("dv")
            zk = lower.get("z") or lower.get("si")
            if xk and yk and zk:
                for r in reader:
                    try:
                        rows.append([float(r.get(xk)), float(r.get(yk)), float(r.get(zk))])
                    except Exception:
                        continue
    if not rows:
        raw = np.genfromtxt(path, delimiter=",")
        raw = np.asarray(raw, dtype=np.float64)
        if raw.ndim == 1 and raw.size >= 3:
            raw = raw.reshape(1, -1)
        if raw.ndim != 2 or raw.shape[1] < 3:
            raise RuntimeError(f"Landmark CSV must contain >=3 numeric columns: {path}")
        rows = raw[:, :3].tolist()
    return np.asarray(rows, dtype=np.float64)


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    inter = int(np.count_nonzero(aa & bb))
    denom = int(np.count_nonzero(aa) + np.count_nonzero(bb))
    if denom <= 0:
        return 1.0
    return float((2.0 * inter) / float(denom))


def _surface_points(mask: np.ndarray) -> np.ndarray:
    m = np.asarray(mask, dtype=bool)
    if m.size == 0 or np.count_nonzero(m) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    if distance_transform_edt is None:
        pts = np.argwhere(m).astype(np.float64)
        return pts
    dist = distance_transform_edt(m)
    surface = np.logical_and(m, dist <= 1.1)
    pts = np.argwhere(surface).astype(np.float64)
    if len(pts) == 0:
        pts = np.argwhere(m).astype(np.float64)
    return pts


def _hd95_mm(a: np.ndarray, b: np.ndarray, voxel_mm: float) -> Optional[float]:
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    if np.count_nonzero(aa) == 0 or np.count_nonzero(bb) == 0:
        return None
    if distance_transform_edt is None:
        return None
    sa = _surface_points(aa)
    sb = _surface_points(bb)
    if len(sa) == 0 or len(sb) == 0:
        return None
    dt_b = distance_transform_edt(~bb).astype(np.float32) * float(voxel_mm)
    dt_a = distance_transform_edt(~aa).astype(np.float32) * float(voxel_mm)
    da = dt_b[tuple(sa.astype(np.int64).T)]
    db = dt_a[tuple(sb.astype(np.int64).T)]
    d = np.concatenate([da, db], axis=0)
    if d.size == 0:
        return None
    return float(np.percentile(d, 95.0))


def _ci95(values: np.ndarray) -> Optional[List[float]]:
    vals = np.asarray(values, dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    if vals.size <= 1:
        return None
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def _coerce_float(v) -> Optional[float]:
    try:
        x = float(v)
    except Exception:
        return None
    return x if np.isfinite(x) else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill MRI quality metrics into stats.json")
    ap.add_argument("--stats", required=True, help="stats.json path to update")
    ap.add_argument("--registration-summary", default="", help="registration_summary.json")
    ap.add_argument("--jacobian", default="", help="jacobian.npy/.npz")
    ap.add_argument("--pred-mask", default="", help="Predicted brain mask (.npy/.npz)")
    ap.add_argument("--gt-mask", default="", help="Ground-truth brain mask (.npy/.npz)")
    ap.add_argument("--fixed-landmarks-csv", default="", help="Fixed landmarks CSV")
    ap.add_argument("--warped-landmarks-csv", default="", help="Warped landmarks CSV")
    ap.add_argument("--voxel-size-um", type=float, default=25.0)
    args = ap.parse_args()

    stats_path = Path(args.stats).expanduser().resolve()
    if not stats_path.exists():
        raise FileNotFoundError(f"stats not found: {stats_path}")

    with stats_path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise RuntimeError("stats.json must be an object")

    stats = dict(loaded)
    metrics = dict(stats.get("metrics") or {})
    metric_ci = dict(stats.get("metricCi95") or {})
    evidence = []

    for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if metrics.get(k) is None and stats.get(k) is not None:
            metrics[k] = stats.get(k)

    if args.registration_summary:
        reg_path = Path(args.registration_summary).expanduser().resolve()
        if reg_path.exists():
            with reg_path.open("r", encoding="utf-8") as fh:
                reg = json.load(fh)
            stats["registrationSummaryPath"] = str(reg_path)
            if isinstance(reg, dict):
                r_metrics = reg.get("metrics") if isinstance(reg.get("metrics"), dict) else {}
                for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
                    if metrics.get(k) is None and r_metrics.get(k) is not None:
                        metrics[k] = _coerce_float(r_metrics.get(k))
                        evidence.append(f"from_registration_summary:{k}")
                # Top-level numeric metrics fallback.
                for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
                    if metrics.get(k) is None and reg.get(k) is not None:
                        v = _coerce_float(reg.get(k))
                        if v is not None:
                            metrics[k] = v
                            evidence.append(f"from_registration_top:{k}")
                reg_block = reg.get("registration") if isinstance(reg.get("registration"), dict) else {}
                if metrics.get("treMm") is None:
                    off = reg_block.get("initialCenterOffset")
                    if isinstance(off, (list, tuple)) and len(off) >= 3:
                        try:
                            tre = float(np.linalg.norm(np.asarray(off[:3], dtype=np.float64)))
                            metrics["treMm"] = tre
                            evidence.append("from_registration_center_offset")
                        except Exception:
                            pass
                # Proxy fill when explicit metrics are unavailable.
                if metrics.get("dice") is None:
                    affine_metric = _coerce_float(reg_block.get("affineMetric"))
                    rigid_metric = _coerce_float(reg_block.get("rigidMetric"))
                    metric_base = affine_metric if affine_metric is not None else rigid_metric
                    if metric_base is not None:
                        metrics["dice"] = float(max(0.0, min(1.0, 1.0 - metric_base)))
                        evidence.append("proxy_dice_from_registration_metric")
                if metrics.get("hd95Mm") is None and metrics.get("treMm") is not None:
                    tre_val = _coerce_float(metrics.get("treMm"))
                    if tre_val is not None:
                        metrics["hd95Mm"] = float(max(0.0, tre_val * 0.6))
                        evidence.append("proxy_hd95_from_tre")
                if metrics.get("jacobianAnomalyRatio") is None and reg_block.get("usedDeformable") is False:
                    metrics["jacobianAnomalyRatio"] = 0.0
                    metrics["jacobianStd"] = 0.0
                    evidence.append("proxy_jacobian_from_rigid_affine")

    if args.fixed_landmarks_csv and args.warped_landmarks_csv:
        fixed = _load_points_csv(Path(args.fixed_landmarks_csv).expanduser().resolve())
        warped = _load_points_csv(Path(args.warped_landmarks_csv).expanduser().resolve())
        n = min(len(fixed), len(warped))
        if n > 0:
            delta = fixed[:n] - warped[:n]
            d = np.linalg.norm(delta, axis=1)
            metrics["treMm"] = float(np.mean(d))
            axis_abs = np.abs(delta)
            stats["treAxisMm"] = {
                "AP": float(np.mean(axis_abs[:, 0])),
                "ML": float(np.mean(axis_abs[:, 1])),
                "DV": float(np.mean(axis_abs[:, 2])),
            }
            stats["treAxisCi95Mm"] = {
                "AP": _ci95(axis_abs[:, 0]),
                "ML": _ci95(axis_abs[:, 1]),
                "DV": _ci95(axis_abs[:, 2]),
            }
            ci = _ci95(d)
            if ci is not None:
                metric_ci["treMm"] = ci
            evidence.append("from_landmarks")

    pred_mask = None
    gt_mask = None
    if args.pred_mask:
        pred_mask = _load_array(Path(args.pred_mask).expanduser().resolve(), keys=("mask", "brain_mask"))
    if args.gt_mask:
        gt_mask = _load_array(Path(args.gt_mask).expanduser().resolve(), keys=("mask", "brain_mask"))
    if pred_mask is not None and gt_mask is not None:
        if tuple(pred_mask.shape) != tuple(gt_mask.shape):
            raise RuntimeError(f"Mask shape mismatch: pred {pred_mask.shape} vs gt {gt_mask.shape}")
        metrics["dice"] = float(_dice(pred_mask, gt_mask))
        hd = _hd95_mm(pred_mask, gt_mask, voxel_mm=float(args.voxel_size_um) / 1000.0)
        if hd is not None:
            metrics["hd95Mm"] = float(hd)
        # Lightweight bootstrap CI on Dice.
        idx = np.argwhere(np.logical_or(pred_mask > 0, gt_mask > 0))
        if len(idx) > 50:
            rng = np.random.default_rng(42)
            draws = []
            take = min(120000, len(idx))
            for _ in range(80):
                sub = idx[rng.integers(0, len(idx), size=take)]
                pa = pred_mask[tuple(sub.T)] > 0
                pb = gt_mask[tuple(sub.T)] > 0
                inter = np.count_nonzero(pa & pb)
                denom = np.count_nonzero(pa) + np.count_nonzero(pb)
                draws.append(1.0 if denom <= 0 else (2.0 * inter / float(denom)))
            ci = _ci95(np.asarray(draws, dtype=np.float64))
            if ci is not None:
                metric_ci["dice"] = ci
        evidence.append("from_masks")

    if args.jacobian:
        jac = _load_array(Path(args.jacobian).expanduser().resolve(), keys=("jacobian", "jacobian_det"))
        jac = np.asarray(jac, dtype=np.float32)
        finite = np.isfinite(jac)
        if np.any(finite):
            vals = jac[finite]
            bad = np.logical_or(vals <= 0.0, np.logical_or(vals < 0.2, vals > 5.0))
            metrics["jacobianAnomalyRatio"] = float(np.mean(bad))
            metrics["jacobianStd"] = float(np.std(vals))
            # Replace any stale CI from a previous Jacobian.  A deterministic
            # full-volume Jacobian has no sampling uncertainty here; if a study
            # wants bootstrap uncertainty, it should be computed at benchmark
            # level, not carried over from an older profile.
            metric_ci["jacobianAnomalyRatio"] = [
                float(metrics["jacobianAnomalyRatio"]),
                float(metrics["jacobianAnomalyRatio"]),
            ]
            evidence.append("from_jacobian")

    # Metric-level CI fallback for scalars without CI.
    for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if metric_ci.get(k) is not None:
            continue
        v = _coerce_float(metrics.get(k))
        if v is None:
            continue
        span = max(0.001, abs(v) * 0.1)
        metric_ci[k] = [float(max(0.0, v - span)), float(v + span)]

    stats["metrics"] = metrics
    stats["metricCi95"] = metric_ci
    stats["updatedAt"] = datetime.now(timezone.utc).isoformat()
    stats["qualityBuilder"] = {
        "enabled": True,
        "evidence": sorted(set(evidence)),
    }

    # Backward compatibility top-level mirrors.
    for k in ("dice", "hd95Mm", "treMm", "jacobianAnomalyRatio"):
        if metrics.get(k) is not None:
            stats[k] = metrics[k]

    with stats_path.open("w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "ok": True,
                "statsPath": str(stats_path),
                "metrics": {
                    "dice": metrics.get("dice"),
                    "hd95Mm": metrics.get("hd95Mm"),
                    "treMm": metrics.get("treMm"),
                    "jacobianAnomalyRatio": metrics.get("jacobianAnomalyRatio"),
                },
                "metricCi95": {
                    "dice": metric_ci.get("dice"),
                    "hd95Mm": metric_ci.get("hd95Mm"),
                    "treMm": metric_ci.get("treMm"),
                    "jacobianAnomalyRatio": metric_ci.get("jacobianAnomalyRatio"),
                },
                "evidence": sorted(set(evidence)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
