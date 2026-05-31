#!/usr/bin/env python3
"""
Build DECIDE-AI style silent-study logs from replay records.

Outputs:
  - silent_study_cases.csv
  - failure_cards.csv
  - silent_study_summary.md
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List


def _load_records(replay_dir: Path) -> List[Dict[str, object]]:
    records = []
    for path in sorted(replay_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[warn] skip {path.name}: {exc}")
    return records


def _load_manual_override(path: Path | None) -> Dict[str, Dict[str, str]]:
    if not path or not path.exists():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            case_id = str(row.get("caseId", "")).strip()
            if case_id:
                out[case_id] = row
    return out


def _as_bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


def _case_metrics(record: Dict[str, object], override: Dict[str, Dict[str, str]]) -> Dict[str, object]:
    req = record.get("request", {}) or {}
    resp = record.get("response", {}) or {}
    ctx = req.get("clinicalContext", {}) or {}
    case_id = str(ctx.get("caseId") or record.get("requestId") or "")

    selected = resp.get("selected", []) or []
    failure_cards = resp.get("failureCards", []) or []
    min_dist = []
    for probe in selected:
        val = probe.get("minSafetyDistanceMm", probe.get("minSafetyDistanceMeanMm"))
        f = _safe_float(val)
        if f is not None:
            min_dist.append(f)
    min_safety = min(min_dist) if min_dist else None

    override_row = override.get(case_id, {})
    manual_override = _as_bool(override_row.get("manualOverride", "0"))
    return {
        "requestId": record.get("requestId", ""),
        "caseId": case_id,
        "studyArm": ctx.get("studyArm", "unspecified"),
        "hospitalId": ctx.get("hospitalId", "unknown"),
        "deviceModel": ctx.get("deviceModel", "unknown"),
        "targetCount": len(req.get("targetKeys", []) or []),
        "selectedCount": len(selected),
        "failureCardCount": len(failure_cards),
        "minSafetyDistanceMm": round(min_safety, 6) if min_safety is not None else "",
        "manualOverride": int(manual_override),
        "overrideReason": override_row.get("overrideReason", ""),
        "correctionPath": override_row.get("correctionPath", ""),
    }


def _failure_rows(record: Dict[str, object]) -> List[Dict[str, object]]:
    req = record.get("request", {}) or {}
    resp = record.get("response", {}) or {}
    ctx = req.get("clinicalContext", {}) or {}
    case_id = str(ctx.get("caseId") or record.get("requestId") or "")
    rows = []
    for card in resp.get("failureCards", []) or []:
        if not isinstance(card, dict):
            continue
        seg = card.get("segmentDepthMm") or [None, None]
        rows.append(
            {
                "requestId": record.get("requestId", ""),
                "caseId": case_id,
                "studyArm": ctx.get("studyArm", "unspecified"),
                "constraint": card.get("constraint", ""),
                "hitStructureClass": card.get("hitStructureClass", ""),
                "hitSource": card.get("hitSource", ""),
                "segmentStartDepthMm": seg[0],
                "segmentEndDepthMm": seg[1],
                "minDistanceMm": card.get("minDistanceMm", ""),
                "thresholdMm": card.get("thresholdMm", ""),
            }
        )
    return rows


def _write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate silent-study logs from replay records")
    parser.add_argument("--replay-dir", type=Path, default=Path.home() / ".probeplanner" / "replay")
    parser.add_argument("--out-dir", type=Path, default=Path("benchmark_out") / "silent_study")
    parser.add_argument("--manual-override-csv", type=Path, default=None)
    args = parser.parse_args()

    records = _load_records(args.replay_dir)
    if not records:
        print(f"No replay records found in: {args.replay_dir}")
        return 1

    manual_override = _load_manual_override(args.manual_override_csv)
    case_rows = [_case_metrics(rec, manual_override) for rec in records]
    fail_rows = [row for rec in records for row in _failure_rows(rec)]

    _write_csv(args.out_dir / "silent_study_cases.csv", case_rows)
    _write_csv(args.out_dir / "failure_cards.csv", fail_rows)

    override_rate = 0.0
    if case_rows:
        override_rate = sum(int(r.get("manualOverride") or 0) for r in case_rows) / len(case_rows)
    fail_counter = Counter((r.get("constraint"), r.get("hitStructureClass")) for r in fail_rows)
    top_fail = fail_counter.most_common(5)

    lines = [
        "# Silent Study Summary",
        "",
        f"- Cases: **{len(case_rows)}**",
        f"- Failure cards: **{len(fail_rows)}**",
        f"- Manual override rate: **{override_rate * 100:.1f}%**",
        "",
        "## Top Failure Modes",
    ]
    if top_fail:
        for (constraint, hit_cls), cnt in top_fail:
            lines.append(f"- {constraint or 'unknown'} / {hit_cls or 'unknown'}: {cnt}")
    else:
        lines.append("- No failure cards logged.")
    lines += [
        "",
        "## Output Files",
        f"- `{args.out_dir / 'silent_study_cases.csv'}`",
        f"- `{args.out_dir / 'failure_cards.csv'}`",
    ]
    (args.out_dir / "silent_study_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Silent-study report generated: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
