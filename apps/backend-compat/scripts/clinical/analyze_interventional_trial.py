#!/usr/bin/env python3
"""
Analyze manual vs manual+system interventional trial (non-inferiority style).

Required CSV columns:
  - arm: manual | manual_plus_system
  - safetyPass: 0/1
Optional:
  - planningTimeSec
  - coverageRate
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List


def _safe_float(v):
    try:
        x = float(v)
    except Exception:
        return None
    if not math.isfinite(x):
        return None
    return x


def _safe_int(v):
    try:
        return int(float(v))
    except Exception:
        return None


def _wald_ci_diff(p1: float, n1: int, p0: float, n0: int):
    if n1 <= 0 or n0 <= 0:
        return (float("nan"), float("nan"))
    diff = p1 - p0
    se = math.sqrt((p1 * (1.0 - p1) / n1) + (p0 * (1.0 - p0) / n0))
    z = 1.959963984540054
    return (diff - z * se, diff + z * se)


def _mean(values: List[float]):
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _read_rows(path: Path) -> List[Dict[str, object]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze interventional trial non-inferiority")
    parser.add_argument("--csv", type=Path, required=True, help="Trial CSV")
    parser.add_argument("--out-dir", type=Path, default=Path("benchmark_out") / "interventional")
    parser.add_argument(
        "--noninferiority-margin",
        type=float,
        default=-0.05,
        help="Safety pass-rate margin on (manual_plus_system - manual)",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"CSV not found: {args.csv}")
        return 1

    rows = _read_rows(args.csv)
    if not rows:
        print("No rows in CSV.")
        return 2

    by_arm = {"manual": [], "manual_plus_system": []}
    for row in rows:
        arm = str(row.get("arm", "")).strip().lower()
        if arm not in by_arm:
            continue
        by_arm[arm].append(row)

    n_manual = len(by_arm["manual"])
    n_sys = len(by_arm["manual_plus_system"])
    if n_manual == 0 or n_sys == 0:
        print("Both arms must be present: manual and manual_plus_system.")
        return 3

    safety_manual = [int(_safe_int(r.get("safetyPass")) or 0) for r in by_arm["manual"]]
    safety_sys = [int(_safe_int(r.get("safetyPass")) or 0) for r in by_arm["manual_plus_system"]]
    p_manual = sum(safety_manual) / n_manual
    p_sys = sum(safety_sys) / n_sys
    diff = p_sys - p_manual
    ci_low, ci_high = _wald_ci_diff(p_sys, n_sys, p_manual, n_manual)
    noninferior = ci_low > args.noninferiority_margin

    t_manual = [_safe_float(r.get("planningTimeSec")) for r in by_arm["manual"]]
    t_sys = [_safe_float(r.get("planningTimeSec")) for r in by_arm["manual_plus_system"]]
    c_manual = [_safe_float(r.get("coverageRate")) for r in by_arm["manual"]]
    c_sys = [_safe_float(r.get("coverageRate")) for r in by_arm["manual_plus_system"]]

    out = {
        "sampleSize": {"manual": n_manual, "manual_plus_system": n_sys},
        "safetyPassRate": {
            "manual": round(p_manual, 6),
            "manual_plus_system": round(p_sys, 6),
            "difference": round(diff, 6),
            "differenceCi95": [round(ci_low, 6), round(ci_high, 6)],
            "nonInferiorityMargin": float(args.noninferiority_margin),
            "nonInferior": bool(noninferior),
        },
        "planningTimeSecMean": {
            "manual": round(_mean([x for x in t_manual if x is not None]) or float("nan"), 6),
            "manual_plus_system": round(_mean([x for x in t_sys if x is not None]) or float("nan"), 6),
        },
        "coverageRateMean": {
            "manual": round(_mean([x for x in c_manual if x is not None]) or float("nan"), 6),
            "manual_plus_system": round(_mean([x for x in c_sys if x is not None]) or float("nan"), 6),
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "interventional_summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        "# Interventional Trial Summary",
        "",
        f"- Manual n={n_manual}, Manual+System n={n_sys}",
        f"- Safety pass (manual): {p_manual:.3f}",
        f"- Safety pass (manual+system): {p_sys:.3f}",
        f"- Difference (system-manual): {diff:.3f} "
        f"[{ci_low:.3f}, {ci_high:.3f}]",
        f"- Non-inferiority margin: {args.noninferiority_margin:.3f}",
        f"- Non-inferior: **{'Yes' if noninferior else 'No'}**",
        "",
        f"- Mean planning time (manual): {statistics.fmean([x for x in t_manual if x is not None]):.2f}s"
        if any(x is not None for x in t_manual)
        else "- Mean planning time (manual): N/A",
        f"- Mean planning time (manual+system): {statistics.fmean([x for x in t_sys if x is not None]):.2f}s"
        if any(x is not None for x in t_sys)
        else "- Mean planning time (manual+system): N/A",
    ]
    (args.out_dir / "interventional_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Interventional analysis completed: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
