#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct VoxelCoord {
    pub x: i32,
    pub y: i32,
    pub z: i32,
}

#[derive(Clone, Copy, Debug)]
pub struct VoxelGridSpec {
    pub origin_um: [f32; 3],
    pub spacing_um: [f32; 3],
    pub dims: [usize; 3],
}

#[derive(Clone, Copy, Debug)]
pub struct TrajectorySpec {
    pub entry_um: [f32; 3],
    pub direction_unit: [f32; 3],
    pub depth_um: f32,
    pub step_um: f32,
}

fn to_voxel(point_um: [f32; 3], grid: VoxelGridSpec) -> Option<VoxelCoord> {
    let x = ((point_um[0] - grid.origin_um[0]) / grid.spacing_um[0]).floor() as i32;
    let y = ((point_um[1] - grid.origin_um[1]) / grid.spacing_um[1]).floor() as i32;
    let z = ((point_um[2] - grid.origin_um[2]) / grid.spacing_um[2]).floor() as i32;
    if x < 0 || y < 0 || z < 0 {
        return None;
    }
    if x as usize >= grid.dims[0] || y as usize >= grid.dims[1] || z as usize >= grid.dims[2] {
        return None;
    }
    Some(VoxelCoord { x, y, z })
}

pub fn sample_trajectory_voxels(spec: TrajectorySpec, grid: VoxelGridSpec) -> Vec<VoxelCoord> {
    let step_um = spec.step_um.max(1.0);
    let n = (spec.depth_um.max(0.0) / step_um).ceil() as usize;
    let mut out = Vec::with_capacity(n + 1);
    let mut last: Option<VoxelCoord> = None;
    for i in 0..=n {
        let d = (i as f32 * step_um).min(spec.depth_um.max(0.0));
        let point = [
            spec.entry_um[0] + spec.direction_unit[0] * d,
            spec.entry_um[1] + spec.direction_unit[1] * d,
            spec.entry_um[2] + spec.direction_unit[2] * d,
        ];
        if let Some(voxel) = to_voxel(point, grid) {
            if last != Some(voxel) {
                out.push(voxel);
                last = Some(voxel);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn samples_unique_adjacent_voxels() {
        let grid = VoxelGridSpec {
            origin_um: [0.0, 0.0, 0.0],
            spacing_um: [10.0, 10.0, 10.0],
            dims: [10, 10, 10],
        };
        let spec = TrajectorySpec {
            entry_um: [1.0, 1.0, 1.0],
            direction_unit: [1.0, 0.0, 0.0],
            depth_um: 35.0,
            step_um: 5.0,
        };
        let voxels = sample_trajectory_voxels(spec, grid);
        assert_eq!(voxels.len(), 4);
        assert_eq!(voxels[0], VoxelCoord { x: 0, y: 0, z: 0 });
        assert_eq!(voxels[3], VoxelCoord { x: 3, y: 0, z: 0 });
    }
}
