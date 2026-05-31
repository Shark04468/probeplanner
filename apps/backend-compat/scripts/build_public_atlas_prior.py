#!/usr/bin/env python3
"""Build a public-atlas skull landmark prior with validation uncertainty.

The script is intentionally data-source agnostic. Upstream jobs should register
public MRI+CT/skull-derived atlas samples into the subject/native MRI space, then
write one row per transformed landmark:

  sourceId, atlasName, landmark, xMm, yMm, zMm

Optional leave-one-out validation can be supplied either as a scalar `treMm`
column or as paired prediction/truth coordinates:

  predXMm, predYMm, predZMm, trueXMm, trueYMm, trueZMm

The output JSON can be used as a planner calibration profile because it writes
`uncertaintyCalibration.riskMarginTreMm`; the backend adds that TRE to risk
thresholds before planning.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np


LANDMARK_ALIASES = {
    "bregma": "bregma",
    "br": "bregma",
    "lambda": "lambda",
    "lamb": "lambda",
    "left_support": "left_support",
    "leftsupport": "left_support",
    "right_support": "right_support",
    "rightsupport": "right_support",
}
TRE_LANDMARKS = {"bregma", "lambda"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        out = float(text)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def _pick(row: Dict[str, str], *names: str) -> Optional[str]:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        hit = lowered.get(str(name).strip().lower())
        if hit is not None:
            return hit
    return None


def _normalize_landmark(value: str) -> Optional[str]:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return LANDMARK_ALIASES.get(key, key if key in set(LANDMARK_ALIASES.values()) else None)


def _read_rows(path: Path) -> List[Dict[str, object]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=2):
            landmark = _normalize_landmark(_pick(row, "landmark", "label", "name") or "")
            if not landmark:
                continue
            xyz = [
                _to_float(_pick(row, "xMm", "x_mm", "x")),
                _to_float(_pick(row, "yMm", "y_mm", "y")),
                _to_float(_pick(row, "zMm", "z_mm", "z")),
            ]
            if any(v is None for v in xyz):
                continue
            tre = _to_float(_pick(row, "treMm", "tre_mm", "landmarkTreMm"))
            pred = [
                _to_float(_pick(row, "predXMm", "pred_x_mm")),
                _to_float(_pick(row, "predYMm", "pred_y_mm")),
                _to_float(_pick(row, "predZMm", "pred_z_mm")),
            ]
            truth = [
                _to_float(_pick(row, "trueXMm", "true_x_mm")),
                _to_float(_pick(row, "trueYMm", "true_y_mm")),
                _to_float(_pick(row, "trueZMm", "true_z_mm")),
            ]
            if tre is None and all(v is not None for v in pred + truth):
                tre = float(np.linalg.norm(np.asarray(pred, dtype=float) - np.asarray(truth, dtype=float)))
            rows.append(
                {
                    "row": idx,
                    "sourceId": str(_pick(row, "sourceId", "sampleId", "atlasId", "atlasName") or f"row_{idx}"),
                    "atlasName": str(_pick(row, "atlasName", "atlas", "source") or ""),
                    "landmark": landmark,
                    "xyzMm": [float(v) for v in xyz],
                    "treMm": tre,
                }
            )
    return rows


def _covariance(points: np.ndarray) -> List[List[float]]:
    if points.shape[0] <= 1:
        return np.zeros((3, 3), dtype=float).tolist()
    return np.cov(points.T, ddof=1).astype(float).tolist()


def _summarize_landmarks(rows: Iterable[Dict[str, object]]) -> Dict[str, object]:
    by_landmark: Dict[str, List[List[float]]] = {}
    by_source: Dict[str, List[str]] = {}
    for row in rows:
        lm = str(row["landmark"])
        by_landmark.setdefault(lm, []).append(list(row["xyzMm"]))
        by_source.setdefault(lm, []).append(str(row.get("sourceId") or ""))

    out = {}
    for landmark, pts in sorted(by_landmark.items()):
        arr = np.asarray(pts, dtype=float)
        out[landmark] = {
            "n": int(arr.shape[0]),
            "meanNativeMm": [round(float(v), 6) for v in np.mean(arr, axis=0).tolist()],
            "covarianceMm2": [[round(float(v), 9) for v in row] for row in _covariance(arr)],
            "stdAxesMm": [round(float(v), 6) for v in np.std(arr, axis=0, ddof=1).tolist()]
            if arr.shape[0] > 1
            else [0.0, 0.0, 0.0],
            "sources": sorted(set(by_source.get(landmark, []))),
        }
    return out


def _quantile(values: np.ndarray, q: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.quantile(values, q))


def _summarize_validation(rows: Iterable[Dict[str, object]]) -> Dict[str, object]:
    vals_by_landmark: Dict[str, List[float]] = {}
    for row in rows:
        tre = row.get("treMm")
        if tre is None:
            continue
        landmark = str(row.get("landmark") or "")
        vals_by_landmark.setdefault(landmark, []).append(abs(float(tre)))

    all_vals = [
        value
        for landmark, vals in vals_by_landmark.items()
        if landmark in TRE_LANDMARKS
        for value in vals
    ]
    if not all_vals:
        all_vals = [value for vals in vals_by_landmark.values() for value in vals]
    arr = np.asarray(all_vals, dtype=float)
    if arr.size == 0:
        return {"available": False, "sampleCount": 0}

    by_landmark = {}
    for landmark, vals in sorted(vals_by_landmark.items()):
        v = np.asarray(vals, dtype=float)
        by_landmark[landmark] = {
            "sampleCount": int(v.size),
            "meanTreMm": round(float(np.mean(v)), 6),
            "rmseTreMm": round(float(np.sqrt(np.mean(v ** 2))), 6),
            "p95TreMm": round(_quantile(v, 0.95), 6),
            "maxTreMm": round(float(np.max(v)), 6),
        }
    return {
        "available": True,
        "source": "leave_one_out_public_paired_mri_ct_landmarks",
        "sampleCount": int(arr.size),
        "meanTreMm": round(float(np.mean(arr)), 6),
        "rmseTreMm": round(float(np.sqrt(np.mean(arr ** 2))), 6),
        "p50TreMm": round(_quantile(arr, 0.50), 6),
        "p95TreMm": round(_quantile(arr, 0.95), 6),
        "maxTreMm": round(float(np.max(arr)), 6),
        "byLandmark": by_landmark,
    }


def build_prior(csv_path: Path) -> Dict[str, object]:
    rows = _read_rows(csv_path)
    landmark_summary = _summarize_landmarks(rows)
    validation = _summarize_validation(rows)
    risk_margin = float(validation.get("p95TreMm", 0.0) or 0.0) if validation.get("available") else 0.0
    landmark_tre = float(validation.get("rmseTreMm", 0.0) or 0.0) if validation.get("available") else 0.0
    required = {"bregma", "lambda"}
    present = set(landmark_summary.keys())
    readiness = {
        "status": "public_atlas_prior_ready" if required <= present and validation.get("available") else "blocked",
        "highPrecisionReady": False,
        "reason": (
            "Population prior is automatic, but must remain uncertainty-inflated; "
            "subject-specific skull landmarks are still absent."
        ),
        "missing": sorted(required - present),
    }
    return {
        "schemaVersion": "probeplanner.public_atlas_prior.v1",
        "createdAt": _utc_now(),
        "sourceCsv": str(csv_path),
        "priorMode": "public_mri_ct_skull_atlas_ensemble",
        "landmarks": landmark_summary,
        "validation": validation,
        "uncertaintyCalibration": {
            "landmarkTreMm": round(landmark_tre, 6),
            "riskMarginTreMm": round(float(max(0.0, min(5.0, risk_margin))), 6),
        },
        "readiness": readiness,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build public atlas landmark prior + TRE calibration JSON")
    parser.add_argument("--landmarks-csv", type=Path, required=True, help="Registered public atlas landmark CSV")
    parser.add_argument("--out", type=Path, required=True, help="Output JSON calibration/prior file")
    args = parser.parse_args()

    if not args.landmarks_csv.exists():
        print(f"CSV not found: {args.landmarks_csv}")
        return 1
    result = build_prior(args.landmarks_csv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out), "readiness": result["readiness"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
