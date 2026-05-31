use crate::{sample_trajectory_voxels, SdfGrid, TrajectorySpec, VoxelGridSpec};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RiskDistanceDiagnostics {
    pub min_distance_mm: f32,
    pub sample_count: usize,
    pub outside_grid_count: usize,
    pub passed: bool,
}

pub fn risk_distance_diagnostics(
    trajectory: TrajectorySpec,
    voxel_grid: VoxelGridSpec,
    sdf: &SdfGrid,
    threshold_mm: f32,
) -> RiskDistanceDiagnostics {
    let voxels = sample_trajectory_voxels(trajectory, voxel_grid);
    let mut min_distance_mm = f32::INFINITY;
    let mut outside_grid_count = 0usize;
    for voxel in &voxels {
        let point_mm = [
            (voxel.x as f32 * voxel_grid.spacing_um[0] + voxel_grid.origin_um[0]) / 1000.0,
            (voxel.y as f32 * voxel_grid.spacing_um[1] + voxel_grid.origin_um[1]) / 1000.0,
            (voxel.z as f32 * voxel_grid.spacing_um[2] + voxel_grid.origin_um[2]) / 1000.0,
        ];
        let sample = sdf.trilinear_sample(point_mm);
        if sample.inside_grid {
            min_distance_mm = min_distance_mm.min(sample.distance_mm);
        } else {
            outside_grid_count += 1;
        }
    }
    RiskDistanceDiagnostics {
        min_distance_mm,
        sample_count: voxels.len(),
        outside_grid_count,
        passed: min_distance_mm >= threshold_mm,
    }
}
