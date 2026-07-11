use planner_engine::{
    build_conflict_matrix, exact_branch_and_bound, sample_trajectory_voxels, BitSet,
    CandidateKernel, ConflictPolicy, SdfGrid, TrajectorySpec, VoxelGridSpec,
};

#[derive(Clone, Copy)]
struct Lcg(u64);

impl Lcg {
    fn new(seed: u64) -> Self {
        Self(seed)
    }

    fn next_u32(&mut self) -> u32 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }

    fn next_f32(&mut self) -> f32 {
        self.next_u32() as f32 / u32::MAX as f32
    }
}

fn bitset(len: usize, indices: &[usize]) -> BitSet {
    let mut out = BitSet::new(len);
    for index in indices {
        out.set(*index);
    }
    out
}

fn kernel(id: u32, coverage: BitSet, score: f32, entry_x_um: f32) -> CandidateKernel {
    CandidateKernel {
        id,
        coverage,
        trajectory_voxels: BitSet::new(256),
        objective_score: score,
        entry_um: [entry_x_um, 0.0, 0.0],
        tip_um: [entry_x_um, 5000.0, 0.0],
        radius_um: 35.0,
        collision_radius_um: 35.0,
        shank_offsets_um: vec![0.0],
    }
}

fn selection_value(candidates: &[CandidateKernel], selected_ids: &[u32]) -> (u32, f32, usize) {
    let mut covered = BitSet::new(candidates[0].coverage.len_bits());
    let mut score = 0.0;
    for id in selected_ids {
        let candidate = candidates.iter().find(|item| item.id == *id).unwrap();
        covered.union_with(&candidate.coverage);
        score += candidate.objective_score;
    }
    (covered.count_ones(), score, selected_ids.len())
}

fn better(a: (u32, f32, usize), b: (u32, f32, usize)) -> bool {
    a.0 > b.0 || (a.0 == b.0 && (a.1 > b.1 || ((a.1 - b.1).abs() <= f32::EPSILON && a.2 < b.2)))
}

fn brute_force_best(candidates: &[CandidateKernel], max_count: usize) -> Vec<u32> {
    let n = candidates.len();
    let mut best = Vec::new();
    let mut best_value = (0, f32::NEG_INFINITY, usize::MAX);
    for mask in 0usize..(1usize << n) {
        if mask.count_ones() as usize > max_count {
            continue;
        }
        let selected = (0..n)
            .filter(|idx| (mask & (1usize << idx)) != 0)
            .map(|idx| candidates[idx].id)
            .collect::<Vec<_>>();
        let value = selection_value(candidates, &selected);
        if better(value, best_value) {
            best = selected;
            best_value = value;
        }
    }
    best
}

#[test]
fn synthetic_axis_aligned_voxel_sampling_matches_closed_form() {
    let grid = VoxelGridSpec {
        origin_um: [0.0, 0.0, 0.0],
        spacing_um: [10.0, 10.0, 10.0],
        dims: [128, 128, 128],
    };
    for axis in 0..3 {
        for k in 1..100 {
            let mut direction = [0.0, 0.0, 0.0];
            direction[axis] = 1.0;
            let spec = TrajectorySpec {
                entry_um: [1.0, 1.0, 1.0],
                direction_unit: direction,
                depth_um: 10.0 * k as f32 - 1.2,
                step_um: 1.0,
            };
            let voxels = sample_trajectory_voxels(spec, grid);
            assert_eq!(voxels.len(), k);
            for (idx, voxel) in voxels.iter().enumerate() {
                let expected = idx as i32;
                match axis {
                    0 => assert_eq!((voxel.x, voxel.y, voxel.z), (expected, 0, 0)),
                    1 => assert_eq!((voxel.x, voxel.y, voxel.z), (0, expected, 0)),
                    _ => assert_eq!((voxel.x, voxel.y, voxel.z), (0, 0, expected)),
                }
            }
        }
    }
}

#[test]
fn synthetic_linear_sdf_trilinear_interpolation_is_exact() {
    let dims = [8, 9, 7];
    let spacing = [0.5, 0.75, 1.25];
    let origin = [-1.0, 2.0, 0.5];
    let mut values = Vec::new();
    for x in 0..dims[0] {
        for y in 0..dims[1] {
            for z in 0..dims[2] {
                let px = origin[0] + spacing[0] * x as f32;
                let py = origin[1] + spacing[1] * y as f32;
                let pz = origin[2] + spacing[2] * z as f32;
                values.push(px + 2.0 * py + 3.0 * pz);
            }
        }
    }
    let grid = SdfGrid {
        dims,
        spacing_mm: spacing,
        origin_mm: origin,
        values,
    };
    let mut rng = Lcg::new(0x5eed);
    for _ in 0..500 {
        let fx = rng.next_f32() * (dims[0] as f32 - 2.001);
        let fy = rng.next_f32() * (dims[1] as f32 - 2.001);
        let fz = rng.next_f32() * (dims[2] as f32 - 2.001);
        let point = [
            origin[0] + spacing[0] * fx,
            origin[1] + spacing[1] * fy,
            origin[2] + spacing[2] * fz,
        ];
        let expected = point[0] + 2.0 * point[1] + 3.0 * point[2];
        let sample = grid.trilinear_sample(point);
        assert!(sample.inside_grid);
        assert!((sample.distance_mm - expected).abs() < 1e-4);
    }
}

#[test]
fn synthetic_entry_spacing_conflict_threshold_has_no_false_negative() {
    let mut close = kernel(1, bitset(2, &[0]), 1.0, 0.0);
    let mut near = kernel(2, bitset(2, &[1]), 1.0, 3569.0);
    let mut far = kernel(3, bitset(2, &[1]), 1.0, 3571.0);
    close.radius_um = 35.0;
    near.radius_um = 35.0;
    far.radius_um = 35.0;
    let policy = ConflictPolicy {
        min_entry_spacing_um: 3500.0,
        min_clearance_um: 0.0,
        use_voxel_overlap: false,
    };
    let conflicts = build_conflict_matrix(&[close.clone(), near], &policy);
    assert!(conflicts[0].get(1));
    let non_conflicts = build_conflict_matrix(&[close, far], &policy);
    assert!(!non_conflicts[0].get(1));
}

#[test]
fn synthetic_exact_solver_matches_bruteforce_on_small_phantoms() {
    let mut rng = Lcg::new(0xc0ffee);
    for case_idx in 0..500 {
        let candidate_count = 5 + (rng.next_u32() as usize % 5);
        let target_count = 3 + (rng.next_u32() as usize % 5);
        let max_count = 1 + (rng.next_u32() as usize % 3);
        let mut candidates = Vec::new();
        for idx in 0..candidate_count {
            let mut targets = Vec::new();
            for target_idx in 0..target_count {
                if (rng.next_u32() + target_idx as u32 + idx as u32).is_multiple_of(3) {
                    targets.push(target_idx);
                }
            }
            if targets.is_empty() {
                targets.push(idx % target_count);
            }
            let score = rng.next_f32() * 100.0 + case_idx as f32 * 0.001;
            candidates.push(kernel(
                idx as u32 + 1,
                bitset(target_count, &targets),
                score,
                idx as f32 * 10_000.0,
            ));
        }
        let policy = ConflictPolicy {
            min_entry_spacing_um: 3500.0,
            min_clearance_um: 0.0,
            use_voxel_overlap: false,
        };
        let exact = exact_branch_and_bound(&candidates, max_count, &policy);
        let brute = brute_force_best(&candidates, max_count);
        let exact_value = selection_value(&candidates, &exact);
        let brute_value = selection_value(&candidates, &brute);
        assert_eq!(exact_value.0, brute_value.0);
        assert!((exact_value.1 - brute_value.1).abs() < 1e-3);
        assert_eq!(exact_value.2, brute_value.2);
    }
}
