use planner_engine::{
    build_conflict_matrix, sample_trajectory_voxels, BitSet, CandidateKernel, ConflictPolicy,
    SdfGrid, TrajectorySpec, VoxelGridSpec,
};
use pyo3::prelude::*;

fn grid(
    origin_um: (f32, f32, f32),
    spacing_um: (f32, f32, f32),
    dims: (usize, usize, usize),
) -> VoxelGridSpec {
    VoxelGridSpec {
        origin_um: [origin_um.0, origin_um.1, origin_um.2],
        spacing_um: [spacing_um.0, spacing_um.1, spacing_um.2],
        dims: [dims.0, dims.1, dims.2],
    }
}

#[pyfunction]
fn sample_trajectory_voxels_native(
    entry_um: (f32, f32, f32),
    direction_unit: (f32, f32, f32),
    depth_um: f32,
    step_um: f32,
    origin_um: (f32, f32, f32),
    spacing_um: (f32, f32, f32),
    dims: (usize, usize, usize),
) -> Vec<(i32, i32, i32)> {
    let spec = TrajectorySpec {
        entry_um: [entry_um.0, entry_um.1, entry_um.2],
        direction_unit: [direction_unit.0, direction_unit.1, direction_unit.2],
        depth_um,
        step_um,
    };
    sample_trajectory_voxels(spec, grid(origin_um, spacing_um, dims))
        .into_iter()
        .map(|v| (v.x, v.y, v.z))
        .collect()
}

#[pyfunction]
fn sdf_trilinear_sample_native(
    values: Vec<f32>,
    dims: (usize, usize, usize),
    spacing_mm: (f32, f32, f32),
    origin_mm: (f32, f32, f32),
    point_mm: (f32, f32, f32),
) -> (f32, bool) {
    let sdf = SdfGrid {
        dims: [dims.0, dims.1, dims.2],
        spacing_mm: [spacing_mm.0, spacing_mm.1, spacing_mm.2],
        origin_mm: [origin_mm.0, origin_mm.1, origin_mm.2],
        values,
    };
    let sample = sdf.trilinear_sample([point_mm.0, point_mm.1, point_mm.2]);
    (sample.distance_mm, sample.inside_grid)
}

#[pyfunction]
fn conflict_pairs_native(
    trajectory_masks: Vec<Vec<usize>>,
    len_bits: usize,
) -> Vec<(usize, usize)> {
    let candidates: Vec<CandidateKernel> = trajectory_masks
        .into_iter()
        .enumerate()
        .map(|(idx, mask_indices)| {
            let mut trajectory_voxels = BitSet::new(len_bits);
            for bit in mask_indices {
                trajectory_voxels.set(bit);
            }
            CandidateKernel {
                id: idx as u32,
                coverage: BitSet::new(0),
                trajectory_voxels,
                objective_score: 0.0,
                entry_um: [idx as f32 * 10_000.0, 0.0, 0.0],
                tip_um: [idx as f32 * 10_000.0, 0.0, 1.0],
                radius_um: 0.0,
                collision_radius_um: 0.0,
                shank_offsets_um: Vec::new(),
            }
        })
        .collect();
    let matrix = build_conflict_matrix(
        &candidates,
        &ConflictPolicy {
            min_entry_spacing_um: 0.0,
            min_clearance_um: 0.0,
            use_voxel_overlap: true,
        },
    );
    let mut out = Vec::new();
    for i in 0..matrix.len() {
        for j in (i + 1)..matrix.len() {
            if matrix[i].get(j) {
                out.push((i, j));
            }
        }
    }
    out
}

#[pymodule]
fn planner_engine_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sample_trajectory_voxels_native, m)?)?;
    m.add_function(wrap_pyfunction!(sdf_trilinear_sample_native, m)?)?;
    m.add_function(wrap_pyfunction!(conflict_pairs_native, m)?)?;
    Ok(())
}
