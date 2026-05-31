"""Risk-distance helpers shared by optimizer backend layers."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import numpy as np


RISK_CLASSES = (
    "vessel",
    "ventricle",
    "sulcus",
    "skull",
    "cortex_outside",
    "brain_surface",
    "drilling_surface",
)


def classify_risk_source(source_key: str) -> Optional[str]:
    source = str(source_key or "").strip().lower()
    if not source:
        return None
    if source.startswith("atlas_keywords:"):
        source = source.split(":", 1)[1]
    if "cortex_outside" in source or "outer_tissue" in source or "extracortical" in source:
        return "cortex_outside"
    if "drilling_surface" in source or "skull_entry" in source or "entry_layer" in source:
        return "drilling_surface"
    if "brain_surface" in source or "pial" in source or "cortical_surface" in source:
        return "brain_surface"
    if "skull" in source or "calvaria" in source or "bone" in source:
        return "skull"
    for cls in RISK_CLASSES:
        if cls in source:
            return cls
    return None


def resolve_risk_threshold_mm(thresholds: Dict[str, float], risk_class: Optional[str]) -> float:
    if risk_class and risk_class in thresholds:
        return float(thresholds[risk_class])
    return float(thresholds.get("default", 0.35))


def build_top_danger_segments(
    distance_series_mm: Iterable[float],
    class_series: Iterable[Optional[str]],
    thresholds_mm: Dict[str, float],
    step_um: float,
    top_k: int = 3,
) -> List[Dict[str, object]]:
    dist_arr = np.asarray(list(distance_series_mm), dtype=np.float64)
    cls_arr = list(class_series)
    if dist_arr.size == 0 or len(cls_arr) != int(dist_arr.size):
        return []

    segments = []
    start = None
    current_scores = []

    def _flush(end_idx: int):
        nonlocal start, current_scores
        if start is None:
            return
        segment_slice = slice(start, end_idx + 1)
        seg_dist = dist_arr[segment_slice]
        seg_classes = cls_arr[segment_slice]
        min_idx_local = int(np.argmin(seg_dist))
        min_dist = float(seg_dist[min_idx_local])
        cls_at_min = seg_classes[min_idx_local]
        thr = resolve_risk_threshold_mm(thresholds_mm, cls_at_min)
        segments.append(
            {
                "startDepthMm": round(start * float(step_um) / 1000.0, 6),
                "endDepthMm": round(end_idx * float(step_um) / 1000.0, 6),
                "sampleCount": int(end_idx - start + 1),
                "minDistanceMm": round(min_dist, 6),
                "thresholdMm": round(thr, 6),
                "hitStructureClass": cls_at_min,
                "severityScore": round(float(max(current_scores) if current_scores else 0.0), 6),
            }
        )
        start = None
        current_scores = []

    for i in range(dist_arr.size):
        cls = cls_arr[i]
        thr = resolve_risk_threshold_mm(thresholds_mm, cls)
        excess = float(thr - dist_arr[i])
        if excess > 0:
            if start is None:
                start = i
            current_scores.append(excess)
        elif start is not None:
            _flush(i - 1)

    if start is not None:
        _flush(int(dist_arr.size) - 1)

    if not segments:
        return []
    segments.sort(key=lambda s: (s["minDistanceMm"], -s["severityScore"], -s["sampleCount"]))
    return segments[: max(1, int(top_k))]
