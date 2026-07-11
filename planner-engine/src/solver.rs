use crate::BitSet;
use std::collections::HashMap;

const TRAJECTORY_CONFLICT_MIN_OVERLAP: u32 = 64;
const COVERAGE_REPAIR_BEAM_WIDTH: usize = 64;
const COVERAGE_REPAIR_PER_MASK_LIMIT: usize = 8;
const COVERAGE_OBJECTIVE_BIG_M: f32 = 1_000_000.0;

#[derive(Clone, Copy, Debug)]
pub struct ConflictPolicy {
    pub min_entry_spacing_um: f32,
    pub min_clearance_um: f32,
    pub use_voxel_overlap: bool,
}

impl Default for ConflictPolicy {
    fn default() -> Self {
        Self {
            min_entry_spacing_um: 3500.0,
            min_clearance_um: 1000.0,
            use_voxel_overlap: true,
        }
    }
}

#[derive(Clone, Debug)]
pub struct CandidateKernel {
    pub id: u32,
    pub coverage: BitSet,
    pub trajectory_voxels: BitSet,
    pub objective_score: f32,
    pub entry_um: [f32; 3],
    pub tip_um: [f32; 3],
    pub radius_um: f32,
    pub collision_radius_um: f32,
    pub shank_offsets_um: Vec<f32>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SolveCancelled;

#[derive(Clone, Debug, Default)]
pub struct CandidateStore {
    pub ids: Vec<u32>,
    pub ap_mm: Vec<f32>,
    pub ml_mm: Vec<f32>,
    pub theta_deg: Vec<f32>,
    pub phi_deg: Vec<f32>,
    pub depth_um: Vec<f32>,
    pub coverage: Vec<BitSet>,
    pub trajectory_voxels: Vec<BitSet>,
    pub objective_scores: Vec<f32>,
    pub entry_um: Vec<[f32; 3]>,
    pub tip_um: Vec<[f32; 3]>,
    pub radius_um: Vec<f32>,
    pub collision_radius_um: Vec<f32>,
    pub shank_offsets_um: Vec<Vec<f32>>,
}

impl CandidateStore {
    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    pub fn push(
        &mut self,
        candidate: CandidateKernel,
        ap_mm: f32,
        ml_mm: f32,
        theta_deg: f32,
        phi_deg: f32,
        depth_um: f32,
    ) {
        self.ids.push(candidate.id);
        self.ap_mm.push(ap_mm);
        self.ml_mm.push(ml_mm);
        self.theta_deg.push(theta_deg);
        self.phi_deg.push(phi_deg);
        self.depth_um.push(depth_um);
        self.coverage.push(candidate.coverage);
        self.trajectory_voxels.push(candidate.trajectory_voxels);
        self.objective_scores.push(candidate.objective_score);
        self.entry_um.push(candidate.entry_um);
        self.tip_um.push(candidate.tip_um);
        self.radius_um.push(candidate.radius_um);
        self.collision_radius_um.push(candidate.collision_radius_um);
        self.shank_offsets_um.push(candidate.shank_offsets_um);
    }

    pub fn as_kernels(&self) -> Vec<CandidateKernel> {
        (0..self.len())
            .map(|idx| CandidateKernel {
                id: self.ids[idx],
                coverage: self.coverage[idx].clone(),
                trajectory_voxels: self.trajectory_voxels[idx].clone(),
                objective_score: self.objective_scores[idx],
                entry_um: self.entry_um[idx],
                tip_um: self.tip_um[idx],
                radius_um: self.radius_um[idx],
                collision_radius_um: self.collision_radius_um[idx],
                shank_offsets_um: self.shank_offsets_um[idx].clone(),
            })
            .collect()
    }
}

pub fn build_conflict_matrix(
    candidates: &[CandidateKernel],
    policy: &ConflictPolicy,
) -> Vec<BitSet> {
    build_conflict_matrix_with_cancel(candidates, policy, &|| false)
        .expect("non-cancellable conflict matrix build cannot be cancelled")
}

pub fn build_conflict_matrix_with_cancel<F>(
    candidates: &[CandidateKernel],
    policy: &ConflictPolicy,
    should_cancel: &F,
) -> Result<Vec<BitSet>, SolveCancelled>
where
    F: Fn() -> bool,
{
    let n = candidates.len();
    let mut rows = vec![BitSet::new(n); n];
    for i in 0..n {
        if should_cancel() {
            return Err(SolveCancelled);
        }
        for j in (i + 1)..n {
            if trajectories_conflict(&candidates[i], &candidates[j], policy) {
                rows[i].set(j);
                rows[j].set(i);
            }
        }
    }
    Ok(rows)
}

pub fn greedy_select(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
) -> Vec<u32> {
    greedy_select_with_cancel(candidates, max_count, policy, &|| false)
        .expect("non-cancellable greedy solve cannot be cancelled")
}

pub fn greedy_select_with_cancel<F>(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
    should_cancel: &F,
) -> Result<Vec<u32>, SolveCancelled>
where
    F: Fn() -> bool,
{
    if candidates.is_empty() || max_count == 0 {
        return Ok(Vec::new());
    }
    if should_cancel() {
        return Err(SolveCancelled);
    }
    let coverage_len = candidates[0].coverage.len_bits();
    let required_coverage = coverable_target_count(candidates, coverage_len);
    let mut profiles = vec![
        vec![1.0; coverage_len],
        rarity_weights(candidates, coverage_len),
    ];
    for target_idx in 0..coverage_len {
        let mut weights = vec![1.0; coverage_len];
        weights[target_idx] = 100.0;
        profiles.push(weights);
    }

    let mut best: Vec<usize> = Vec::new();
    for weights in &profiles {
        if should_cancel() {
            return Err(SolveCancelled);
        }
        let selected =
            greedy_weighted_pass(candidates, max_count, policy, weights, None, should_cancel)?;
        if selection_better(candidates, &selected, &best) {
            best = selected;
        }
        if selection_value(candidates, &best).0 >= required_coverage {
            break;
        }
    }
    if selection_value(candidates, &best).0 < required_coverage {
        for target_idx in 0..coverage_len {
            if should_cancel() {
                return Err(SolveCancelled);
            }
            if let Some(seed_idx) = best_seed_for_target(candidates, target_idx) {
                let selected = greedy_weighted_pass(
                    candidates,
                    max_count,
                    policy,
                    &profiles[0],
                    Some(seed_idx),
                    should_cancel,
                )?;
                if selection_better(candidates, &selected, &best) {
                    best = selected;
                }
                if selection_value(candidates, &best).0 >= required_coverage {
                    break;
                }
            }
        }
    }
    if selection_value(candidates, &best).0 < required_coverage && coverage_len <= 64 {
        let selected = beam_repair_select(
            candidates,
            max_count,
            policy,
            COVERAGE_REPAIR_BEAM_WIDTH,
            COVERAGE_REPAIR_PER_MASK_LIMIT,
            should_cancel,
        )?;
        if selection_better(candidates, &selected, &best) {
            best = selected;
        }
    }
    Ok(best.into_iter().map(|idx| candidates[idx].id).collect())
}

fn coverable_target_count(candidates: &[CandidateKernel], coverage_len: usize) -> u32 {
    let mut coverable = BitSet::new(coverage_len);
    for candidate in candidates {
        coverable.union_with(&candidate.coverage);
    }
    coverable.count_ones()
}

fn rarity_weights(candidates: &[CandidateKernel], coverage_len: usize) -> Vec<f32> {
    let mut counts = vec![0usize; coverage_len];
    for candidate in candidates {
        for (idx, count) in counts.iter_mut().enumerate() {
            if candidate.coverage.get(idx) {
                *count += 1;
            }
        }
    }
    let max_count = counts.iter().copied().max().unwrap_or(1).max(1) as f32;
    counts
        .into_iter()
        .map(|count| {
            if count == 0 {
                max_count
            } else {
                (max_count / count as f32).clamp(1.0, 100.0)
            }
        })
        .collect()
}

fn best_seed_for_target(candidates: &[CandidateKernel], target_idx: usize) -> Option<usize> {
    candidates
        .iter()
        .enumerate()
        .filter(|(_, candidate)| candidate.coverage.get(target_idx))
        .max_by(|(_, a), (_, b)| {
            a.objective_score
                .partial_cmp(&b.objective_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|(idx, _)| idx)
}

fn greedy_weighted_pass(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
    weights: &[f32],
    seed_idx: Option<usize>,
    should_cancel: &dyn Fn() -> bool,
) -> Result<Vec<usize>, SolveCancelled> {
    let mut selected = Vec::new();
    let mut selected_mask = vec![false; candidates.len()];
    let mut covered = BitSet::new(candidates[0].coverage.len_bits());
    if let Some(idx) = seed_idx {
        if idx < candidates.len()
            && weighted_gain(&covered, &candidates[idx].coverage, weights) > 0.0
        {
            covered.union_with(&candidates[idx].coverage);
            selected.push(idx);
            selected_mask[idx] = true;
        }
    }

    while selected.len() < max_count {
        if should_cancel() {
            return Err(SolveCancelled);
        }
        let mut best_idx = None;
        let mut best_value = f32::NEG_INFINITY;
        for idx in 0..candidates.len() {
            if idx % 256 == 0 && should_cancel() {
                return Err(SolveCancelled);
            }
            if selected_mask[idx] {
                continue;
            }
            if selected
                .iter()
                .any(|chosen| trajectories_conflict(&candidates[idx], &candidates[*chosen], policy))
            {
                continue;
            }
            let gain = weighted_gain(&covered, &candidates[idx].coverage, weights);
            if gain <= 0.0 {
                continue;
            }
            let value = gain * 1_000_000.0 + candidates[idx].objective_score;
            if value > best_value {
                best_value = value;
                best_idx = Some(idx);
            }
        }
        let Some(idx) = best_idx else {
            break;
        };
        covered.union_with(&candidates[idx].coverage);
        selected.push(idx);
        selected_mask[idx] = true;
    }
    Ok(selected)
}

fn weighted_gain(covered: &BitSet, candidate: &BitSet, weights: &[f32]) -> f32 {
    weights
        .iter()
        .enumerate()
        .filter_map(|(idx, weight)| (candidate.get(idx) && !covered.get(idx)).then_some(*weight))
        .sum()
}

fn selection_better(
    candidates: &[CandidateKernel],
    selected: &[usize],
    incumbent: &[usize],
) -> bool {
    let (coverage, score, count) = selection_value(candidates, selected);
    let (best_coverage, best_score, best_count) = selection_value(candidates, incumbent);
    coverage > best_coverage
        || (coverage == best_coverage
            && (score > best_score
                || ((score - best_score).abs() <= f32::EPSILON && count < best_count)))
}

fn selection_value(candidates: &[CandidateKernel], selected: &[usize]) -> (u32, f32, usize) {
    let coverage_len = candidates
        .first()
        .map(|candidate| candidate.coverage.len_bits())
        .unwrap_or(0);
    let mut covered = BitSet::new(coverage_len);
    let mut score = 0.0;
    for idx in selected {
        covered.union_with(&candidates[*idx].coverage);
        score += candidates[*idx].objective_score;
    }
    (covered.count_ones(), score, selected.len())
}

#[derive(Clone, Debug)]
struct BeamState {
    selected: Vec<usize>,
    covered_mask: u64,
    score: f32,
}

fn beam_repair_select(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
    beam_width: usize,
    per_mask_limit: usize,
    should_cancel: &dyn Fn() -> bool,
) -> Result<Vec<usize>, SolveCancelled> {
    let coverage_len = candidates[0].coverage.len_bits();
    let masks: Vec<u64> = candidates
        .iter()
        .map(|candidate| coverage_mask(&candidate.coverage, coverage_len))
        .collect();
    let mut order: Vec<usize> = (0..candidates.len()).collect();
    order.sort_by(|a, b| {
        masks[*b]
            .count_ones()
            .cmp(&masks[*a].count_ones())
            .then_with(|| {
                candidates[*b]
                    .objective_score
                    .partial_cmp(&candidates[*a].objective_score)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    });

    let mut states = vec![BeamState {
        selected: Vec::new(),
        covered_mask: 0,
        score: 0.0,
    }];
    let mut best = states[0].clone();
    for _ in 0..max_count {
        if should_cancel() {
            return Err(SolveCancelled);
        }
        let mut expanded = states.clone();
        for state in &states {
            for &idx in &order {
                if idx % 256 == 0 && should_cancel() {
                    return Err(SolveCancelled);
                }
                let add_mask = masks[idx] & !state.covered_mask;
                if add_mask == 0 || state.selected.contains(&idx) {
                    continue;
                }
                if state.selected.iter().any(|chosen| {
                    trajectories_conflict(&candidates[idx], &candidates[*chosen], policy)
                }) {
                    continue;
                }
                let mut selected = state.selected.clone();
                selected.push(idx);
                expanded.push(BeamState {
                    selected,
                    covered_mask: state.covered_mask | masks[idx],
                    score: state.score + candidates[idx].objective_score,
                });
            }
        }
        states = prune_beam(expanded, beam_width, per_mask_limit);
        for state in &states {
            if beam_state_better(state, &best) {
                best = state.clone();
            }
        }
        if best.covered_mask.count_ones() >= coverage_len as u32 {
            break;
        }
    }
    Ok(best.selected)
}

fn coverage_mask(coverage: &BitSet, coverage_len: usize) -> u64 {
    let mut mask = 0u64;
    for idx in 0..coverage_len.min(64) {
        if coverage.get(idx) {
            mask |= 1u64 << idx;
        }
    }
    mask
}

fn prune_beam(
    mut states: Vec<BeamState>,
    beam_width: usize,
    per_mask_limit: usize,
) -> Vec<BeamState> {
    states.sort_by(|a, b| compare_beam_state(b, a));
    let mut accepted_per_mask: HashMap<u64, usize> = HashMap::new();
    let mut pruned = Vec::with_capacity(beam_width.min(states.len()));
    for state in states {
        let count = accepted_per_mask.entry(state.covered_mask).or_insert(0);
        if *count >= per_mask_limit {
            continue;
        }
        *count += 1;
        pruned.push(state);
        if pruned.len() >= beam_width {
            break;
        }
    }
    pruned
}

fn beam_state_better(candidate: &BeamState, incumbent: &BeamState) -> bool {
    compare_beam_state(candidate, incumbent).is_gt()
}

fn compare_beam_state(a: &BeamState, b: &BeamState) -> std::cmp::Ordering {
    a.covered_mask
        .count_ones()
        .cmp(&b.covered_mask.count_ones())
        .then_with(|| {
            a.score
                .partial_cmp(&b.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .then_with(|| b.selected.len().cmp(&a.selected.len()))
}

fn trajectories_conflict(
    a: &CandidateKernel,
    b: &CandidateKernel,
    policy: &ConflictPolicy,
) -> bool {
    if policy.min_entry_spacing_um > 0.0 {
        let entry_surface_um =
            point_distance_um(a.entry_um, b.entry_um) - a.radius_um - b.radius_um;
        if entry_surface_um < policy.min_entry_spacing_um {
            return true;
        }
    }
    if policy.min_clearance_um > 0.0 {
        let surface_clearance_um = shank_segment_clearance_um(a, b);
        if surface_clearance_um < policy.min_clearance_um {
            return true;
        }
    }
    policy.use_voxel_overlap
        && a.trajectory_voxels.intersection_count(&b.trajectory_voxels)
            >= TRAJECTORY_CONFLICT_MIN_OVERLAP
}

fn point_distance_um(a: [f32; 3], b: [f32; 3]) -> f32 {
    let dx = a[0] - b[0];
    let dy = a[1] - b[1];
    let dz = a[2] - b[2];
    (dx * dx + dy * dy + dz * dz).sqrt()
}

fn dot(a: [f32; 3], b: [f32; 3]) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn sub(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn add(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

fn scale(a: [f32; 3], value: f32) -> [f32; 3] {
    [a[0] * value, a[1] * value, a[2] * value]
}

fn normalize(a: [f32; 3], fallback: [f32; 3]) -> [f32; 3] {
    let norm = dot(a, a).sqrt();
    if norm <= 1e-9 {
        fallback
    } else {
        [a[0] / norm, a[1] / norm, a[2] / norm]
    }
}

fn lateral_axis_um(entry_um: [f32; 3], tip_um: [f32; 3]) -> [f32; 3] {
    let dir = normalize(sub(tip_um, entry_um), [0.0, 1.0, 0.0]);
    let ml_axis = [0.0, 0.0, 1.0];
    let projected = [
        ml_axis[0] - dir[0] * dot(ml_axis, dir),
        ml_axis[1] - dir[1] * dot(ml_axis, dir),
        ml_axis[2] - dir[2] * dot(ml_axis, dir),
    ];
    let lateral = normalize(projected, [f32::NAN, f32::NAN, f32::NAN]);
    if lateral[0].is_finite() {
        lateral
    } else {
        normalize([-dir[2], 0.0, dir[0]], [1.0, 0.0, 0.0])
    }
}

fn shank_segment_clearance_um(a: &CandidateKernel, b: &CandidateKernel) -> f32 {
    let a_axis = lateral_axis_um(a.entry_um, a.tip_um);
    let b_axis = lateral_axis_um(b.entry_um, b.tip_um);
    let a_offsets: &[f32] = if a.shank_offsets_um.is_empty() {
        &[0.0]
    } else {
        &a.shank_offsets_um
    };
    let b_offsets: &[f32] = if b.shank_offsets_um.is_empty() {
        &[0.0]
    } else {
        &b.shank_offsets_um
    };
    let mut best = f32::INFINITY;
    for a_offset in a_offsets {
        let ao = scale(a_axis, *a_offset);
        let a_entry = add(a.entry_um, ao);
        let a_tip = add(a.tip_um, ao);
        for b_offset in b_offsets {
            let bo = scale(b_axis, *b_offset);
            let b_entry = add(b.entry_um, bo);
            let b_tip = add(b.tip_um, bo);
            let clearance = segment_segment_distance_um(a_entry, a_tip, b_entry, b_tip)
                - a.radius_um
                - b.radius_um;
            best = best.min(clearance);
        }
    }
    best
}

fn segment_segment_distance_um(p1: [f32; 3], q1: [f32; 3], p2: [f32; 3], q2: [f32; 3]) -> f32 {
    let d1 = sub(q1, p1);
    let d2 = sub(q2, p2);
    let r = sub(p1, p2);
    let a = dot(d1, d1);
    let e = dot(d2, d2);
    let f = dot(d2, r);
    let eps = 1e-6f32;
    let (mut s, mut t);
    if a <= eps && e <= eps {
        return point_distance_um(p1, p2);
    }
    if a <= eps {
        s = 0.0;
        t = (f / e).clamp(0.0, 1.0);
    } else {
        let c = dot(d1, r);
        if e <= eps {
            t = 0.0;
            s = (-c / a).clamp(0.0, 1.0);
        } else {
            let b = dot(d1, d2);
            let denom = a * e - b * b;
            if denom.abs() > eps {
                s = ((b * f - c * e) / denom).clamp(0.0, 1.0);
            } else {
                s = 0.0;
            }
            t = (b * s + f) / e;
            if t < 0.0 {
                t = 0.0;
                s = (-c / a).clamp(0.0, 1.0);
            } else if t > 1.0 {
                t = 1.0;
                s = ((b - c) / a).clamp(0.0, 1.0);
            }
        }
    }
    let c1 = [p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s];
    let c2 = [p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t];
    point_distance_um(c1, c2)
}

pub fn exact_branch_and_bound(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
) -> Vec<u32> {
    exact_branch_and_bound_with_cancel(candidates, max_count, policy, &|| false)
        .expect("non-cancellable exact solve cannot be cancelled")
}

pub fn exact_branch_and_bound_with_cancel<F>(
    candidates: &[CandidateKernel],
    max_count: usize,
    policy: &ConflictPolicy,
    should_cancel: &F,
) -> Result<Vec<u32>, SolveCancelled>
where
    F: Fn() -> bool,
{
    if should_cancel() {
        return Err(SolveCancelled);
    }
    let conflicts = build_conflict_matrix_with_cancel(candidates, policy, should_cancel)?;
    let mut order: Vec<usize> = (0..candidates.len()).collect();
    order.sort_by(|a, b| {
        candidates[*b]
            .objective_score
            .partial_cmp(&candidates[*a].objective_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let coverage_len = candidates
        .first()
        .map(|c| c.coverage.len_bits())
        .unwrap_or(0);
    let mut best_score = f32::NEG_INFINITY;
    let mut best: Vec<usize> = Vec::new();
    let mut selected: Vec<usize> = Vec::new();
    let mut covered = BitSet::new(coverage_len);

    struct DfsContext<'a> {
        order: &'a [usize],
        candidates: &'a [CandidateKernel],
        conflicts: &'a [BitSet],
        max_count: usize,
        best_score: &'a mut f32,
        best: &'a mut Vec<usize>,
        should_cancel: &'a dyn Fn() -> bool,
        coverage_len: usize,
    }

    fn dfs(
        pos: usize,
        selected: &mut Vec<usize>,
        covered: &mut BitSet,
        ctx: &mut DfsContext<'_>,
    ) -> Result<(), SolveCancelled> {
        if (ctx.should_cancel)() {
            return Err(SolveCancelled);
        }
        if selected.len() >= ctx.max_count || pos >= ctx.order.len() {
            let score: f32 = selected
                .iter()
                .map(|idx| ctx.candidates[*idx].objective_score)
                .sum();
            let coverage_bonus = covered.count_ones() as f32 * COVERAGE_OBJECTIVE_BIG_M;
            let total = coverage_bonus + score;
            if total > *ctx.best_score {
                *ctx.best_score = total;
                *ctx.best = selected.clone();
            }
            return Ok(());
        }
        let remaining_coverage = ctx
            .coverage_len
            .saturating_sub(covered.count_ones() as usize);
        let optimistic: f32 = selected
            .iter()
            .map(|idx| ctx.candidates[*idx].objective_score)
            .sum::<f32>()
            + ctx.order[pos..]
                .iter()
                .take(ctx.max_count.saturating_sub(selected.len()))
                .map(|idx| ctx.candidates[*idx].objective_score.max(0.0))
                .sum::<f32>()
            + covered.count_ones() as f32 * COVERAGE_OBJECTIVE_BIG_M
            + (remaining_coverage as f32 * COVERAGE_OBJECTIVE_BIG_M);
        if optimistic < *ctx.best_score {
            return Ok(());
        }
        let idx = ctx.order[pos];
        let can_take = !selected
            .iter()
            .any(|chosen| ctx.conflicts[idx].get(*chosen));
        if can_take {
            let before = covered.clone();
            covered.union_with(&ctx.candidates[idx].coverage);
            selected.push(idx);
            dfs(pos + 1, selected, covered, ctx)?;
            selected.pop();
            *covered = before;
        }
        dfs(pos + 1, selected, covered, ctx)?;
        Ok(())
    }

    let mut ctx = DfsContext {
        order: &order,
        candidates,
        conflicts: &conflicts,
        max_count,
        best_score: &mut best_score,
        best: &mut best,
        should_cancel,
        coverage_len,
    };
    dfs(0, &mut selected, &mut covered, &mut ctx)?;
    Ok(best.into_iter().map(|idx| candidates[idx].id).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn candidate(id: u32, score: f32, cover: usize, traj: usize) -> CandidateKernel {
        let mut coverage = BitSet::new(4);
        coverage.set(cover);
        let mut trajectory_voxels = BitSet::new(256);
        for offset in 0..TRAJECTORY_CONFLICT_MIN_OVERLAP as usize {
            trajectory_voxels.set(traj + offset);
        }
        CandidateKernel {
            id,
            coverage,
            trajectory_voxels,
            objective_score: score,
            entry_um: [id as f32 * 5000.0, 0.0, 0.0],
            tip_um: [id as f32 * 5000.0, 5000.0, 0.0],
            radius_um: 35.0,
            collision_radius_um: 35.0,
            shank_offsets_um: vec![0.0],
        }
    }

    #[test]
    fn conflict_matrix_reuses_bitsets() {
        let a = candidate(1, 1.0, 0, 2);
        let b = candidate(2, 0.9, 1, 2);
        let conflicts = build_conflict_matrix(&[a, b], &ConflictPolicy::default());
        assert!(conflicts[0].get(1));
        assert!(conflicts[1].get(0));
    }

    #[test]
    fn greedy_skips_conflicting_candidate() {
        let chosen = greedy_select(
            &[
                candidate(1, 1.0, 0, 2),
                candidate(2, 0.9, 1, 2),
                candidate(3, 0.8, 1, 3),
            ],
            2,
            &ConflictPolicy::default(),
        );
        assert_eq!(chosen, vec![1, 3]);
    }

    #[test]
    fn greedy_fallback_respects_entry_spacing() {
        let mut a = candidate(1, 1.0, 0, 20);
        let mut b = candidate(2, 0.9, 1, 120);
        a.entry_um = [0.0, 0.0, 0.0];
        a.tip_um = [0.0, 5000.0, 0.0];
        b.entry_um = [500.0, 0.0, 0.0];
        b.tip_um = [500.0, 5000.0, 0.0];
        let chosen = greedy_select(&[a, b], 2, &ConflictPolicy::default());
        assert_eq!(chosen, vec![1]);
    }

    #[test]
    fn conflict_matrix_uses_multi_shank_footprint_clearance() {
        let mut a = candidate(1, 1.0, 0, 20);
        let mut b = candidate(2, 0.9, 1, 120);
        a.entry_um = [0.0, 0.0, 0.0];
        a.tip_um = [0.0, 5000.0, 0.0];
        b.entry_um = [0.0, 0.0, 700.0];
        b.tip_um = [0.0, 5000.0, 700.0];
        a.shank_offsets_um = vec![-375.0, -125.0, 125.0, 375.0];
        b.shank_offsets_um = vec![-375.0, -125.0, 125.0, 375.0];

        let single_shank_policy = ConflictPolicy {
            min_entry_spacing_um: 0.0,
            min_clearance_um: 200.0,
            use_voxel_overlap: false,
        };
        let conflicts = build_conflict_matrix(&[a, b], &single_shank_policy);
        assert!(conflicts[0].get(1));
        assert!(conflicts[1].get(0));
    }

    #[test]
    fn greedy_prioritizes_recovering_rare_coverage() {
        let mut high_score_blocker = candidate(1, 1000.0, 0, 20);
        let mut rare_target = candidate(2, 1.0, 1, 120);
        let mut alternate = candidate(3, 100.0, 0, 220);
        high_score_blocker.entry_um = [0.0, 0.0, 0.0];
        high_score_blocker.tip_um = [0.0, 5000.0, 0.0];
        rare_target.entry_um = [1000.0, 0.0, 0.0];
        rare_target.tip_um = [1000.0, 5000.0, 0.0];
        alternate.entry_um = [6000.0, 0.0, 0.0];
        alternate.tip_um = [6000.0, 5000.0, 0.0];
        let chosen = greedy_select(
            &[high_score_blocker, rare_target, alternate],
            2,
            &ConflictPolicy::default(),
        );
        assert_eq!(chosen, vec![2, 3]);
    }

    #[test]
    fn cancellable_conflict_matrix_stops_early() {
        let calls = AtomicUsize::new(0);
        let candidates = vec![candidate(1, 1.0, 0, 20), candidate(2, 0.9, 1, 120)];
        let result =
            build_conflict_matrix_with_cancel(&candidates, &ConflictPolicy::default(), &|| {
                calls.fetch_add(1, Ordering::SeqCst) >= 1
            });
        assert_eq!(result, Err(SolveCancelled));
    }

    #[test]
    fn cancellable_greedy_stops_inside_pass() {
        let calls = AtomicUsize::new(0);
        let candidates = vec![
            candidate(1, 1.0, 0, 20),
            candidate(2, 0.9, 1, 120),
            candidate(3, 0.8, 2, 220),
        ];
        let result = greedy_select_with_cancel(&candidates, 2, &ConflictPolicy::default(), &|| {
            calls.fetch_add(1, Ordering::SeqCst) >= 2
        });
        assert_eq!(result, Err(SolveCancelled));
    }

    #[test]
    fn cancellable_exact_stops_inside_dfs() {
        let calls = AtomicUsize::new(0);
        let candidates = vec![
            candidate(1, 1.0, 0, 20),
            candidate(2, 0.9, 1, 120),
            candidate(3, 0.8, 2, 220),
        ];
        let result =
            exact_branch_and_bound_with_cancel(&candidates, 2, &ConflictPolicy::default(), &|| {
                calls.fetch_add(1, Ordering::SeqCst) >= 4
            });
        assert_eq!(result, Err(SolveCancelled));
    }
}
