use crate::{BitSet, VoxelCoord};
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub struct RegionMaskIndex {
    pub dims: [usize; 3],
    pub region_masks: HashMap<String, BitSet>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrajectoryProfile {
    pub covered_regions: Vec<String>,
    pub coverage_bits: BitSet,
    pub sampled_voxels: usize,
}

fn flat_index(dims: [usize; 3], voxel: VoxelCoord) -> Option<usize> {
    if voxel.x < 0 || voxel.y < 0 || voxel.z < 0 {
        return None;
    }
    let x = voxel.x as usize;
    let y = voxel.y as usize;
    let z = voxel.z as usize;
    if x >= dims[0] || y >= dims[1] || z >= dims[2] {
        return None;
    }
    Some((z * dims[1] + y) * dims[0] + x)
}

pub fn compute_trajectory_profile(
    voxels: &[VoxelCoord],
    index: &RegionMaskIndex,
) -> TrajectoryProfile {
    let mut coverage_bits = BitSet::new(index.region_masks.len());
    let mut covered_regions = Vec::new();
    for (region_idx, (key, mask)) in index.region_masks.iter().enumerate() {
        let mut hit = false;
        for voxel in voxels {
            if let Some(flat) = flat_index(index.dims, *voxel) {
                if mask.get(flat) {
                    hit = true;
                    break;
                }
            }
        }
        if hit {
            coverage_bits.set(region_idx);
            covered_regions.push(key.clone());
        }
    }
    covered_regions.sort();
    TrajectoryProfile {
        covered_regions,
        coverage_bits,
        sampled_voxels: voxels.len(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_region_overlap() {
        let mut mask = BitSet::new(8);
        mask.set(3);
        let index = RegionMaskIndex {
            dims: [2, 2, 2],
            region_masks: HashMap::from([("CA1".to_string(), mask)]),
        };
        let profile = compute_trajectory_profile(&[VoxelCoord { x: 1, y: 1, z: 0 }], &index);
        assert_eq!(profile.covered_regions, vec!["CA1"]);
    }
}
