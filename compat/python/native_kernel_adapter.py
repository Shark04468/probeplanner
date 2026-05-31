"""Drop-in Python wrappers for phase-1 native hot-path replacement.

The functions intentionally return plain Python data structures so they can
replace small blocks inside `optimizer_server.py` without changing the public
Flask API.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

Voxel = Tuple[int, int, int]


def _native_module():
    import planner_engine_native  # type: ignore

    return planner_engine_native


def sample_trajectory_voxels_native_or_raise(
    *,
    entry_um: Sequence[float],
    direction_unit: Sequence[float],
    depth_um: float,
    step_um: float,
    origin_um: Sequence[float],
    spacing_um: Sequence[float],
    dims: Sequence[int],
) -> List[Voxel]:
    native = _native_module()
    return list(
        native.sample_trajectory_voxels_native(
            tuple(map(float, entry_um)),
            tuple(map(float, direction_unit)),
            float(depth_um),
            float(step_um),
            tuple(map(float, origin_um)),
            tuple(map(float, spacing_um)),
            tuple(map(int, dims)),
        )
    )


def sdf_trilinear_sample_native_or_raise(
    *,
    values: Iterable[float],
    dims: Sequence[int],
    spacing_mm: Sequence[float],
    origin_mm: Sequence[float],
    point_mm: Sequence[float],
) -> Tuple[float, bool]:
    native = _native_module()
    distance, inside = native.sdf_trilinear_sample_native(
        list(map(float, values)),
        tuple(map(int, dims)),
        tuple(map(float, spacing_mm)),
        tuple(map(float, origin_mm)),
        tuple(map(float, point_mm)),
    )
    return float(distance), bool(inside)


def conflict_pairs_native_or_raise(trajectory_masks: Sequence[Sequence[int]], len_bits: int) -> List[Tuple[int, int]]:
    native = _native_module()
    return [tuple(map(int, pair)) for pair in native.conflict_pairs_native(trajectory_masks, int(len_bits))]
