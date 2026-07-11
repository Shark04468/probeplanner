"""Audit selected real-atlas trajectories against the CCF annotation brain mask."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


VOXEL_SIZE_UM = 25.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _longest_true_run(flags: np.ndarray) -> tuple[int, int | None, int | None]:
    best = current = 0
    best_start = current_start = None
    for index, flag in enumerate(flags.tolist()):
        if flag:
            if current == 0:
                current_start = index
            current += 1
            if current > best:
                best = current
                best_start = current_start
        else:
            current = 0
            current_start = None
    if best_start is None:
        return 0, None, None
    return best, int(best_start), int(best_start + best - 1)


def _run_intervals(flags: np.ndarray) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(flags.tolist()):
        if flag and start is None:
            start = index
        if start is not None and ((not flag) or index == len(flags) - 1):
            end = index if flag else index - 1
            intervals.append((int(start), int(end)))
            start = None
    return intervals


def _sample_segment(entry_um: np.ndarray, tip_um: np.ndarray, step_um: float) -> tuple[np.ndarray, np.ndarray]:
    length_um = float(np.linalg.norm(tip_um - entry_um))
    if length_um <= 0:
        return np.asarray([entry_um], dtype=np.float64), np.asarray([0.0], dtype=np.float64)
    depths_um = np.r_[np.arange(0.0, length_um, step_um), length_um]
    points_um = entry_um[None, :] + (tip_um - entry_um)[None, :] * (depths_um[:, None] / length_um)
    return points_um, depths_um


def _target_center_vox(annotation: np.ndarray, ids: set[int]) -> np.ndarray | None:
    if not ids:
        return None
    coords = np.argwhere(np.isin(annotation, list(ids)))
    if coords.size == 0:
        return None
    return np.mean(coords, axis=0, dtype=np.float64)


def _structure_map(path: Path | None) -> dict[int, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(item["id"]): item for item in raw if isinstance(item, dict) and item.get("id") is not None}


def _probe_audit(
    *,
    case: dict[str, Any],
    probe: dict[str, Any],
    annotation: np.ndarray,
    structures: dict[int, dict[str, Any]],
    target_centers: dict[str, np.ndarray | None],
    step_um: float,
) -> dict[str, Any]:
    entry_um = np.asarray(probe["entryCcfUm"], dtype=np.float64)
    tip_um = np.asarray(probe["tipCcfUm"], dtype=np.float64)
    points_um, depths_um = _sample_segment(entry_um, tip_um, step_um)
    voxel_points = np.rint(points_um / VOXEL_SIZE_UM).astype(np.int64)
    in_bounds = np.all((voxel_points >= 0) & (voxel_points < np.asarray(annotation.shape)), axis=1)
    labels = np.zeros(len(points_um), dtype=np.int64)
    labels[in_bounds] = annotation[tuple(voxel_points[in_bounds].T)]
    inside = np.logical_and(in_bounds, labels > 0)
    first_inside = int(np.argmax(inside)) if np.any(inside) else None
    last_inside = int(len(inside) - 1 - np.argmax(inside[::-1])) if np.any(inside) else None
    inside_after_entry = inside[first_inside:] if first_inside is not None else np.asarray([], dtype=bool)
    outside_after_entry = np.logical_not(inside_after_entry)
    max_gap_samples, gap_start, gap_end = _longest_true_run(outside_after_entry)

    figure = case.get("figure") if isinstance(case.get("figure"), dict) else {}
    target_ids = {
        str(key): {int(value) for value in values}
        for key, values in (figure.get("targetIds") or {}).items()
        if isinstance(values, list)
    }
    target_metrics: dict[str, Any] = {}
    union_hits = np.zeros(len(labels), dtype=bool)
    for key in case.get("requestedTargets") or []:
        ids = target_ids.get(str(key), set())
        hits = np.isin(labels, list(ids)) if ids else np.zeros(len(labels), dtype=bool)
        union_hits |= hits
        hit_indices = np.flatnonzero(hits)
        sampled_length_um = float(np.sum(np.diff(depths_um)[hits[:-1]])) if len(depths_um) > 1 else 0.0
        target_metrics[str(key)] = {
            "sampleCount": int(np.count_nonzero(hits)),
            "firstEntryDepthMm": round(float(depths_um[hit_indices[0]] / 1000.0), 6) if hit_indices.size else None,
            "lastExitDepthMm": round(float(depths_um[hit_indices[-1]] / 1000.0), 6) if hit_indices.size else None,
            "intersectionLengthMm": round(sampled_length_um / 1000.0, 6),
            "intervalsMm": [
                [round(float(depths_um[start] / 1000.0), 6), round(float(depths_um[end] / 1000.0), 6)]
                for start, end in _run_intervals(hits)
            ],
        }

    union_indices = np.flatnonzero(union_hits)
    last_target_depth_um = float(depths_um[union_indices[-1]]) if union_indices.size else None
    overshoot_um = float(depths_um[-1] - last_target_depth_um) if last_target_depth_um is not None else None
    min_center_distance_mm = None
    centers = [center for center in target_centers.values() if center is not None]
    if centers:
        center_stack = np.vstack(centers)
        distances = np.linalg.norm(
            points_um[:, None, :] / 1000.0 - center_stack[None, :, :] * VOXEL_SIZE_UM / 1000.0,
            axis=2,
        )
        min_center_distance_mm = float(np.min(distances))

    tip_label_id = int(labels[-1])
    tip_structure = structures.get(tip_label_id, {})
    tip_target_keys = [key for key, ids in target_ids.items() if tip_label_id in ids]
    gap_segment_mm = None
    if first_inside is not None and gap_start is not None and gap_end is not None:
        gap_segment_mm = [
            round(float(depths_um[first_inside + gap_start] / 1000.0), 6),
            round(float(depths_um[first_inside + gap_end] / 1000.0), 6),
        ]
    return {
        "caseId": str(case.get("caseId")),
        "probeIndex": int(probe.get("probeIndex", 0)),
        "physicalLengthMm": round(float(depths_um[-1] / 1000.0), 6),
        "brainMask": {
            "firstInsideIndex": first_inside,
            "lastInsideIndex": last_inside,
            "firstInsideDepthMm": round(float(depths_um[first_inside] / 1000.0), 6) if first_inside is not None else None,
            "lastInsideDepthMm": round(float(depths_um[last_inside] / 1000.0), 6) if last_inside is not None else None,
            "insideRatioAfterEntry": round(float(np.mean(inside_after_entry)), 6) if inside_after_entry.size else 0.0,
            "tipInside": bool(inside[-1]),
            "outsideSampleCount": int(np.count_nonzero(outside_after_entry)),
            "maxOutsideGapMm": round(float(max_gap_samples * step_um / 1000.0), 6),
            "maxOutsideGapSegmentMm": gap_segment_mm,
            "brainBoundaryPassed": bool(inside[-1] and np.count_nonzero(outside_after_entry) == 0),
        },
        "targetMetrics": target_metrics,
        "targetEndpoint": {
            "tipRegionId": tip_label_id,
            "tipRegionAcronym": tip_structure.get("acronym"),
            "tipRegionName": tip_structure.get("name"),
            "tipInRequestedTarget": bool(tip_target_keys),
            "tipTargetKeys": sorted(tip_target_keys),
            "lastTargetIntersectionDepthMm": round(last_target_depth_um / 1000.0, 6) if last_target_depth_um is not None else None,
            "overshootAfterLastTargetMm": round(overshoot_um / 1000.0, 6) if overshoot_um is not None else None,
            "minDistanceToTargetCenterMm": round(min_center_distance_mm, 6) if min_center_distance_mm is not None else None,
        },
        "strictPass": bool(
            inside[-1]
            and np.count_nonzero(outside_after_entry) == 0
            and bool(tip_target_keys)
            and (overshoot_um is not None and abs(overshoot_um) <= 1e-6)
        ),
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = [
        "| case | probe | tip inside brain | post-entry inside ratio | max outside gap mm | tip region | tip in target | overshoot mm | strict pass |",
        "|---|---:|---|---:|---:|---|---|---:|---|",
    ]
    for item in payload["probes"]:
        brain = item["brainMask"]
        target = item["targetEndpoint"]
        rows.append(
            "| {case} | {probe} | {tip_inside} | {ratio:.6f} | {gap:.6f} | {region} | {in_target} | {overshoot} | {passed} |".format(
                case=item["caseId"],
                probe=item["probeIndex"],
                tip_inside=brain["tipInside"],
                ratio=float(brain["insideRatioAfterEntry"]),
                gap=float(brain["maxOutsideGapMm"]),
                region=target.get("tipRegionAcronym") or target.get("tipRegionId"),
                in_target=target["tipInRequestedTarget"],
                overshoot=target.get("overshootAfterLastTargetMm"),
                passed=item["strictPass"],
            )
        )
    lines = [
        "# Real-Atlas Trajectory Containment and Endpoint Audit",
        "",
        "Each selected trajectory is sampled at 25 um, including its physical tip. The brain mask is the CCF annotation support (`annotation > 0`).",
        "",
        *rows,
        "",
        "## Acceptance Contract",
        "",
        "`tipInside=true`, `insideRatioAfterEntry=1.0`, `outsideSampleCount=0`, `maxOutsideGapMm=0`, `tipInRequestedTarget=true`, and `overshootAfterLastTargetMm=0` within sampling precision.",
        "",
        "## Summary",
        "",
        f"- Selected probes audited: {payload['summary']['selectedProbeCount']}",
        f"- Strict passes: {payload['summary']['strictPassCount']}",
        f"- Strict failures: {payload['summary']['strictFailureCount']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-study-json", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--structure-tree", type=Path, default=Path.home() / ".allensdk_cache" / "structures.json")
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument("--step-um", type=float, default=25.0)
    args = parser.parse_args()

    case_study = json.loads(args.case_study_json.read_text(encoding="utf-8"))
    annotation = np.load(args.annotation, mmap_mode="r")
    structures = _structure_map(args.structure_tree)
    center_cache: dict[tuple[int, ...], np.ndarray | None] = {}
    probes = []
    for case in case_study.get("cases") or []:
        figure = case.get("figure") if isinstance(case.get("figure"), dict) else {}
        centers = {}
        for key, values in (figure.get("targetIds") or {}).items():
            ids = tuple(sorted(int(value) for value in values))
            if ids not in center_cache:
                center_cache[ids] = _target_center_vox(annotation, set(ids))
            centers[str(key)] = center_cache[ids]
        for probe in case.get("selectedProbes") or []:
            probes.append(
                _probe_audit(
                    case=case,
                    probe=probe,
                    annotation=annotation,
                    structures=structures,
                    target_centers=centers,
                    step_um=float(args.step_um),
                )
            )

    strict_pass_count = sum(1 for probe in probes if probe["strictPass"])
    payload = {
        "schemaVersion": 1,
        "caseStudy": str(args.case_study_json.resolve()),
        "annotation": {
            "path": str(args.annotation.resolve()),
            "shape": [int(value) for value in annotation.shape],
            "sha256": _sha256(args.annotation),
            "voxelSpacingUm": VOXEL_SIZE_UM,
        },
        "samplingStepUm": float(args.step_um),
        "acceptanceContract": {
            "tipInside": True,
            "insideRatioAfterEntry": 1.0,
            "outsideSampleCount": 0,
            "maxOutsideGapMm": 0.0,
            "tipInRequestedTarget": True,
            "overshootAfterLastTargetMm": 0.0,
        },
        "summary": {
            "selectedProbeCount": len(probes),
            "strictPassCount": strict_pass_count,
            "strictFailureCount": len(probes) - strict_pass_count,
        },
        "probes": probes,
    }
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = args.out_prefix.with_suffix(".json")
    csv_path = args.out_prefix.with_suffix(".csv")
    md_path = args.out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "caseId", "probeIndex", "physicalLengthMm", "tipInside", "insideRatioAfterEntry",
                "outsideSampleCount", "maxOutsideGapMm", "tipRegionId", "tipRegionAcronym",
                "tipInRequestedTarget", "lastTargetIntersectionDepthMm", "overshootAfterLastTargetMm",
                "minDistanceToTargetCenterMm", "strictPass",
            ],
        )
        writer.writeheader()
        for probe in probes:
            brain = probe["brainMask"]
            target = probe["targetEndpoint"]
            writer.writerow(
                {
                    "caseId": probe["caseId"],
                    "probeIndex": probe["probeIndex"],
                    "physicalLengthMm": probe["physicalLengthMm"],
                    "tipInside": brain["tipInside"],
                    "insideRatioAfterEntry": brain["insideRatioAfterEntry"],
                    "outsideSampleCount": brain["outsideSampleCount"],
                    "maxOutsideGapMm": brain["maxOutsideGapMm"],
                    "tipRegionId": target["tipRegionId"],
                    "tipRegionAcronym": target["tipRegionAcronym"],
                    "tipInRequestedTarget": target["tipInRequestedTarget"],
                    "lastTargetIntersectionDepthMm": target["lastTargetIntersectionDepthMm"],
                    "overshootAfterLastTargetMm": target["overshootAfterLastTargetMm"],
                    "minDistanceToTargetCenterMm": target["minDistanceToTargetCenterMm"],
                    "strictPass": probe["strictPass"],
                }
            )
    _write_markdown(md_path, payload)
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path), "summary": payload["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
