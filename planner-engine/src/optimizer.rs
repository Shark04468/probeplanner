use crate::{
    api::{OptimizeRequest, OptimizeResponse},
    atlas::{load_f32_npy_from_npz, MmapVolume, RegionLookup},
    exact_branch_and_bound, greedy_select, sample_trajectory_voxels, BitSet, CandidateKernel,
    ConflictPolicy, SdfGrid, TrajectorySpec, VoxelCoord, VoxelGridSpec,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    env,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Instant,
};

#[derive(Clone, Debug, Default)]
pub struct PlannerEngineConfig {
    pub atlas_path: Option<String>,
    pub sdf_path: Option<String>,
    pub sdf_npy_path: Option<String>,
    pub sdf_channel: Option<String>,
    pub layered_sdf_npy_paths: BTreeMap<String, String>,
    pub region_index_path: Option<String>,
}

#[derive(Clone, Debug)]
pub struct PlannerCore {
    atlas: Option<Arc<NativeAtlas>>,
    target_cache: Arc<Mutex<HashMap<String, TargetModel>>>,
}

#[derive(Clone, Debug)]
struct TargetModel {
    key: String,
    center_mm: [f32; 3],
    descendant_ids: Vec<u32>,
    atlas_backed: bool,
}

#[derive(Clone, Debug)]
struct CandidateDraft {
    id: u32,
    ap_mm: f32,
    ml_mm: f32,
    theta_deg: f32,
    phi_deg: f32,
    depth_um: f32,
    coverage: BitSet,
    trajectory_voxels: BitSet,
    covered_regions: Vec<String>,
    overlap_voxels: BTreeMap<String, usize>,
    entry_um: [f32; 3],
    tip_um: [f32; 3],
    direction: [f32; 3],
    probe_radius_um: f32,
    collision_radius_um: f32,
    shank_count: usize,
    shank_spacing_um: f32,
    shank_offsets_um: Vec<f32>,
    objective_score: f32,
    min_target_distance_mm: f32,
    min_sdf_distance_mm: Option<f32>,
    risk_distances_mm: BTreeMap<String, f32>,
    drcc_metrics: Option<DrccMetrics>,
    brain_bounds_summary: Option<BrainBoundsSummary>,
    sample_count: usize,
    aggregate_seed: bool,
}

#[derive(Clone, Debug)]
struct DrccMetrics {
    available: bool,
    worst_case_violation_prob: f32,
    risk_integral_shortfall_mm: f32,
    risk_tail_shortfall_mm: f32,
    risk_budget_epsilon: f32,
    feasible: bool,
}

#[derive(Clone, Debug)]
struct GeneratedCandidates {
    candidates: Vec<CandidateDraft>,
    target_count: usize,
    target_keys: Vec<String>,
    fallback_target_keys: Vec<String>,
    generation_ms: u64,
    scoring_ms: u64,
    rejected_count: usize,
    rejection_reasons: BTreeMap<String, usize>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SolverKind {
    Greedy,
    Exact,
    DrccExactPool,
    DrccHybrid,
    DrccHeuristic,
    DrccCvarRelax,
}

#[derive(Clone, Debug)]
struct SolverPlan {
    kind: SolverKind,
    pool_size: usize,
    effective_algorithm: &'static str,
    requested_drcc_solver: Option<String>,
    effective_drcc_solver: Option<&'static str>,
    native_drcc_approximation: bool,
}

#[derive(Clone, Debug)]
struct UnsupportedSolver {
    requested_solver: String,
    message: String,
}

impl SolverPlan {
    fn from_request(
        request: &OptimizeRequest,
        algorithm_requested: &str,
        candidate_count: usize,
    ) -> Result<Self, UnsupportedSolver> {
        match algorithm_requested {
            "drcc" => {
                let requested_solver =
                    string_param(&request.params, "drccSolver", "exact_pool").to_ascii_lowercase();
                let configured = match requested_solver.as_str() {
                    "misocp" => usize_param(
                        &request.params,
                        "drccMisocpMaxCandidates",
                        usize_param(&request.params, "drccExactMaxCandidates", 42),
                    ),
                    _ => usize_param(&request.params, "drccExactMaxCandidates", 42),
                };
                let pool_size = configured.clamp(4, 64).min(candidate_count.max(1));
                let mut plan = match requested_solver.as_str() {
                    "exact" | "exact_pool" => Self {
                        kind: SolverKind::DrccExactPool,
                        pool_size,
                        effective_algorithm: "drcc_exact_pool_bitset",
                        requested_drcc_solver: Some(requested_solver),
                        effective_drcc_solver: Some("exact_pool"),
                        native_drcc_approximation: false,
                    },
                    "hybrid" => Self {
                        kind: SolverKind::DrccHybrid,
                        pool_size,
                        effective_algorithm: "drcc_hybrid_exact_pool_bitset",
                        requested_drcc_solver: Some(requested_solver),
                        effective_drcc_solver: Some("hybrid"),
                        native_drcc_approximation: false,
                    },
                    "heuristic" => Self {
                        kind: SolverKind::DrccHeuristic,
                        pool_size: candidate_count,
                        effective_algorithm: "drcc_heuristic_bitset",
                        requested_drcc_solver: Some(requested_solver),
                        effective_drcc_solver: Some("heuristic"),
                        native_drcc_approximation: false,
                    },
                    "cvar" | "cvar_relax" => Self {
                        kind: SolverKind::DrccCvarRelax,
                        pool_size,
                        effective_algorithm: "drcc_cvar_relax_bitset",
                        requested_drcc_solver: Some(requested_solver),
                        effective_drcc_solver: Some("cvar_relax"),
                        native_drcc_approximation: false,
                    },
                    "misocp" => {
                        return Err(UnsupportedSolver {
                            requested_solver,
                            message: "Native engine does not implement the legacy Python MISOCP/CVX solver; request exact_pool, hybrid, heuristic, or cvar_relax. MISOCP is available only through the legacy Python backend for historical comparison.".to_string(),
                        });
                    }
                    _ => {
                        return Err(UnsupportedSolver {
                            requested_solver,
                            message: "Unsupported native DRCC solver. Supported native values: exact_pool, hybrid, heuristic, cvar_relax. MISOCP is legacy Python only and fails closed in the native engine.".to_string(),
                        });
                    }
                };
                if plan.pool_size == 0 {
                    plan.pool_size = 1;
                }
                Ok(plan)
            }
            "exact" => {
                let configured = usize_param(
                    &request.params,
                    "nativeExactMaxCandidates",
                    usize_param(&request.params, "exactMaxCandidates", 48),
                );
                Ok(Self {
                    kind: SolverKind::Exact,
                    pool_size: configured.clamp(4, 64).min(candidate_count.max(1)),
                    effective_algorithm: if candidate_count > configured {
                        "exact_reduced_pool_bitset"
                    } else {
                        "exact_bitset"
                    },
                    requested_drcc_solver: None,
                    effective_drcc_solver: None,
                    native_drcc_approximation: false,
                })
            }
            _ => Ok(Self {
                kind: SolverKind::Greedy,
                pool_size: candidate_count,
                effective_algorithm: "greedy_bitset",
                requested_drcc_solver: None,
                effective_drcc_solver: None,
                native_drcc_approximation: false,
            }),
        }
    }
}

const TRAJECTORY_BITSET_BINS: usize = 16_384;
const SDF_SCORING_STRIDE: usize = 4;
const RISK_SDF_TRILINEAR_STEP_UM: f32 = 150.0;
const ENTRY_CORRIDOR_RISK_SAMPLE_STRIDE: usize = 1;
const VOXEL_SIZE_UM: f32 = 25.0;
const MULTI_SHANK_PROFILE_STEP_UM: f32 = 75.0;
const BREGMA_UM: [f32; 3] = [5400.0, 440.0, 5700.0];
const ATLAS_DIMS: [usize; 3] = [528, 320, 456];
const LAMBDA_AP_MM: f32 = -4.0;
const ENTRY_AP_MIN_MM: f32 = -4.5;
const ENTRY_AP_MAX_MM: f32 = 4.5;
const TIP_POST_LIMIT_MM: f32 = -6.0;
const MAX_DV_MM: f32 = 8.5;
const BRAIN_BOUNDS_OUTSIDE_GAP_UM: f32 = 300.0;
const MIN_RISK_DISTANCE_MM_DEFAULT: f32 = 0.35;
const RISK_SDF_CHANNELS: &[(&str, &str)] = &[
    ("skull_sdf_mm", "skull"),
    ("outer_tissue_sdf_mm", "cortex_outside"),
    ("cortex_outside_sdf_mm", "cortex_outside"),
    ("ventricle_sdf_mm", "ventricle"),
    ("vessel_sdf_mm", "vessel"),
    ("sulcus_sdf_mm", "sulcus"),
    ("brain_surface_sdf_mm", "brain_surface"),
    ("drilling_surface_sdf_mm", "drilling_surface"),
];
const ENTRY_CORRIDOR_RISK_CLASSES: &[&str] = &[
    "cortex_outside",
    "brain_surface",
    "drilling_surface",
    "skull",
];
const SKULL_ENTRY_CORRIDOR_ALLOWANCE_UM: f32 = 900.0;

#[derive(Debug)]
struct NativeAtlas {
    annotation: MmapVolume,
    regions: RegionLookup,
    label_stats: HashMap<u32, LabelMoment>,
    sdf_npy: Option<MmapVolume>,
    sdf: Option<SdfGrid>,
    risk_sdfs: HashMap<String, SdfVolume>,
}

#[derive(Debug)]
struct AtlasTrajectoryProfile {
    coverage: BitSet,
    covered_regions: Vec<String>,
    overlap_voxels: BTreeMap<String, usize>,
    min_sdf_distance_mm: Option<f32>,
    risk_distances_mm: BTreeMap<String, f32>,
    brain_bounds_summary: Option<BrainBoundsSummary>,
}

#[derive(Debug)]
enum SdfVolume {
    Mmap(MmapVolume),
    Grid(SdfGrid),
}

#[derive(Clone, Debug)]
struct BrainBoundsSummary {
    available: bool,
    passed: bool,
    reason: String,
    tip_inside: bool,
    first_inside_index: Option<usize>,
    last_inside_index: Option<usize>,
    inside_count: usize,
    sample_count: usize,
    inside_ratio: f32,
    inside_ratio_after_entry: f32,
    max_outside_gap_um: f32,
    max_outside_gap_vox: usize,
}

#[derive(Clone, Debug)]
struct ConstraintPolicy {
    max_angle_mode: String,
    lambda_boundary_mode: String,
    brain_bounds_mode: String,
    continuous_risk_mode: String,
    min_entry_spacing_mode: String,
    min_clearance_mode: String,
    max_angle_deg: f32,
    min_entry_spacing_um: f32,
    min_clearance_um: f32,
    probe_radius_um: f32,
    collision_radius_um: f32,
    shank_count: usize,
    shank_spacing_um: f32,
    shank_offsets_um: Vec<f32>,
    allow_fallback_target_coverage: bool,
    min_risk_distance_mm: f32,
    risk_thresholds_mm: BTreeMap<String, f32>,
    algorithm_requested: String,
    drcc_enabled: bool,
    drcc_strict: bool,
    drcc_risk_epsilon: f32,
    drcc_wasserstein_radius_mm: f32,
    drcc_cvar_alpha: f32,
    risk_distance_sigma_mm: f32,
    sdf_risk_integral_weight: f32,
    sdf_risk_cvar_weight: f32,
}

#[derive(Debug, Default)]
struct TargetLabelLookup {
    by_label: HashMap<u32, Vec<usize>>,
}

impl TargetLabelLookup {
    fn new(targets: &[TargetModel]) -> Self {
        let mut by_label: HashMap<u32, Vec<usize>> = HashMap::new();
        for (idx, target) in targets.iter().enumerate() {
            for label in &target.descendant_ids {
                by_label.entry(*label).or_default().push(idx);
            }
        }
        Self { by_label }
    }
}

impl SdfVolume {
    fn sample_point_um(&self, point_um: [f32; 3]) -> crate::SdfSample {
        match self {
            SdfVolume::Mmap(volume) => volume.trilinear_sample_um(point_um).map_or(
                crate::SdfSample {
                    distance_mm: f32::INFINITY,
                    inside_grid: false,
                },
                |distance_mm| crate::SdfSample {
                    distance_mm,
                    inside_grid: distance_mm.is_finite(),
                },
            ),
            SdfVolume::Grid(grid) => grid.trilinear_sample([
                point_um[0] / 1000.0,
                point_um[1] / 1000.0,
                point_um[2] / 1000.0,
            ]),
        }
    }
}

impl BrainBoundsSummary {
    fn unavailable() -> Self {
        Self {
            available: false,
            passed: true,
            reason: "unavailable".to_string(),
            tip_inside: true,
            first_inside_index: None,
            last_inside_index: None,
            inside_count: 0,
            sample_count: 0,
            inside_ratio: 0.0,
            inside_ratio_after_entry: 0.0,
            max_outside_gap_um: 0.0,
            max_outside_gap_vox: 0,
        }
    }

    fn from_inside_series(inside: &[bool], step_um: f32) -> Self {
        let sample_count = inside.len();
        if sample_count == 0 {
            return Self::unavailable();
        }
        let inside_count = inside.iter().filter(|flag| **flag).count();
        if inside_count == 0 {
            return Self {
                available: true,
                passed: false,
                reason: "no_brain_intersection".to_string(),
                tip_inside: false,
                first_inside_index: None,
                last_inside_index: None,
                inside_count,
                sample_count,
                inside_ratio: 0.0,
                inside_ratio_after_entry: 0.0,
                max_outside_gap_um: 0.0,
                max_outside_gap_vox: 0,
            };
        }

        let first_inside = inside.iter().position(|flag| *flag).unwrap_or(0);
        let last_inside = inside
            .iter()
            .rposition(|flag| *flag)
            .unwrap_or(first_inside);
        let tip_inside = *inside.last().unwrap_or(&false);
        let span = &inside[first_inside..=last_inside];
        let mut max_gap = 0usize;
        let mut current_gap = 0usize;
        for flag in span {
            if *flag {
                max_gap = max_gap.max(current_gap);
                current_gap = 0;
            } else {
                current_gap += 1;
            }
        }
        max_gap = max_gap.max(current_gap);

        let safe_step_um = step_um.max(1e-6);
        let gap_threshold_vox =
            ((BRAIN_BOUNDS_OUTSIDE_GAP_UM / safe_step_um).ceil() as usize).max(1);
        let exits_brain_midway = max_gap >= gap_threshold_vox;
        let passed = tip_inside && !exits_brain_midway;
        let reason = if !tip_inside {
            "tip_outside_brain"
        } else if exits_brain_midway {
            "trajectory_exits_brain_midway"
        } else {
            "ok"
        };
        let after_entry = &inside[first_inside..];
        let after_inside = after_entry.iter().filter(|flag| **flag).count();

        Self {
            available: true,
            passed,
            reason: reason.to_string(),
            tip_inside,
            first_inside_index: Some(first_inside),
            last_inside_index: Some(last_inside),
            inside_count,
            sample_count,
            inside_ratio: inside_count as f32 / sample_count.max(1) as f32,
            inside_ratio_after_entry: after_inside as f32 / after_entry.len().max(1) as f32,
            max_outside_gap_um: max_gap as f32 * safe_step_um,
            max_outside_gap_vox: max_gap,
        }
    }

    fn to_json(&self) -> Value {
        json!({
            "available": self.available,
            "passed": self.passed,
            "reason": self.reason,
            "tipInside": self.tip_inside,
            "firstInsideIndex": self.first_inside_index,
            "lastInsideIndex": self.last_inside_index,
            "insideCount": self.inside_count,
            "sampleCount": self.sample_count,
            "insideRatio": round3(self.inside_ratio),
            "insideRatioAfterEntry": round3(self.inside_ratio_after_entry),
            "outsideGapUmThreshold": BRAIN_BOUNDS_OUTSIDE_GAP_UM,
            "maxOutsideGapVox": self.max_outside_gap_vox,
            "maxOutsideGapUm": round3(self.max_outside_gap_um),
        })
    }
}

impl ConstraintPolicy {
    fn from_request(request: &OptimizeRequest) -> Self {
        let algorithm_requested =
            string_param(&request.params, "algorithm", "greedy").to_ascii_lowercase();
        let mut risk_thresholds_mm = balanced_risk_thresholds();
        let default_threshold = f32_param(
            &request.params,
            "minRiskDistanceMm",
            MIN_RISK_DISTANCE_MM_DEFAULT,
        );
        risk_thresholds_mm.insert("default".to_string(), default_threshold);
        if let Some(map) = request
            .params
            .get("riskThresholdsMm")
            .and_then(Value::as_object)
        {
            for (key, value) in map {
                if let Some(v) = value.as_f64() {
                    risk_thresholds_mm.insert(key.to_string(), v as f32);
                }
            }
        }
        enforce_boundary_risk_threshold_floors(&mut risk_thresholds_mm);
        let shank_count = probe_shank_count(&request.probe_params);
        let shank_spacing_um = probe_shank_spacing_um(&request.probe_params, shank_count);
        let shank_offsets_um = shank_offsets_um(shank_count, shank_spacing_um);
        let probe_radius = probe_radius_um(&request.probe_params);
        let half_span_um = shank_offsets_um
            .iter()
            .map(|offset| offset.abs())
            .fold(0.0f32, f32::max);
        Self {
            max_angle_mode: constraint_mode(&request.constraint_modes, "maxAngle", "enforce"),
            lambda_boundary_mode: constraint_mode(
                &request.constraint_modes,
                "lambdaBoundary",
                "enforce",
            ),
            brain_bounds_mode: constraint_mode(&request.constraint_modes, "brainBounds", "enforce"),
            continuous_risk_mode: constraint_mode(
                &request.constraint_modes,
                "continuousRiskDistance",
                "warn",
            ),
            min_entry_spacing_mode: constraint_mode(
                &request.constraint_modes,
                "minEntrySpacing",
                "enforce",
            ),
            min_clearance_mode: constraint_mode(
                &request.constraint_modes,
                "minClearance",
                "enforce",
            ),
            max_angle_deg: f32_param(&request.params, "maxAngle", 15.0),
            min_entry_spacing_um: f32_param(&request.probe_params, "entrySpacingUm", 3500.0),
            min_clearance_um: f32_param(&request.params, "minClearance", 1000.0),
            probe_radius_um: probe_radius,
            collision_radius_um: probe_radius + half_span_um,
            shank_count,
            shank_spacing_um,
            shank_offsets_um,
            allow_fallback_target_coverage: bool_param(
                &request.params,
                "allowFallbackTargetCoverage",
                false,
            ),
            min_risk_distance_mm: default_threshold,
            risk_thresholds_mm,
            algorithm_requested: algorithm_requested.clone(),
            drcc_enabled: algorithm_requested == "drcc"
                || bool_param(&request.params, "drccEnabled", false),
            drcc_strict: bool_param(&request.params, "drccStrict", false),
            drcc_risk_epsilon: f32_param(&request.params, "drccRiskEpsilon", 0.08)
                .clamp(0.001, 0.5),
            drcc_wasserstein_radius_mm: f32_param(&request.params, "drccWassersteinRadius", 0.03)
                .max(0.0),
            drcc_cvar_alpha: f32_param(&request.params, "drccCvarAlpha", 0.9).clamp(0.5, 0.999),
            risk_distance_sigma_mm: f32_param(&request.params, "riskDistanceSigmaMm", 0.08)
                .max(0.005),
            sdf_risk_integral_weight: f32_param(&request.params, "sdfRiskIntegralWeight", 1.0)
                .max(0.0),
            sdf_risk_cvar_weight: f32_param(&request.params, "sdfRiskCvarWeight", 0.7).max(0.0),
        }
    }

    fn enforce_max_angle(&self) -> bool {
        self.max_angle_mode == "enforce"
    }

    fn enforce_lambda_boundary(&self) -> bool {
        self.lambda_boundary_mode == "enforce"
    }

    fn enforce_brain_bounds(&self) -> bool {
        self.brain_bounds_mode == "enforce"
    }

    fn enforce_continuous_risk(&self) -> bool {
        self.continuous_risk_mode == "enforce"
    }

    fn drcc_active(&self) -> bool {
        self.drcc_enabled || self.algorithm_requested == "drcc"
    }

    fn risk_threshold_for(&self, class_name: &str) -> f32 {
        self.risk_thresholds_mm
            .get(class_name)
            .copied()
            .unwrap_or(self.min_risk_distance_mm)
    }

    fn conflict_policy(&self) -> ConflictPolicy {
        ConflictPolicy {
            min_entry_spacing_um: if self.min_entry_spacing_mode == "off" {
                0.0
            } else {
                self.min_entry_spacing_um
            },
            min_clearance_um: if self.min_clearance_mode == "off" {
                0.0
            } else {
                self.min_clearance_um
            },
            use_voxel_overlap: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct LabelMoment {
    total_count: usize,
    total_sum: [f64; 3],
    positive_count: usize,
    positive_sum: [f64; 3],
    negative_count: usize,
    negative_sum: [f64; 3],
}

impl LabelMoment {
    fn add(&mut self, point_mm: [f32; 3]) {
        self.total_count += 1;
        add_point(&mut self.total_sum, point_mm);
        if point_mm[2] >= 0.0 {
            self.positive_count += 1;
            add_point(&mut self.positive_sum, point_mm);
        } else {
            self.negative_count += 1;
            add_point(&mut self.negative_sum, point_mm);
        }
    }
}

impl PlannerEngineConfig {
    pub fn from_env() -> Self {
        let artifact_root = env::var("PROBEPLANNER_ARTIFACT_ROOT")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_default();
        let artifact_root = resolve_artifact_profile_root(Path::new(&artifact_root));

        fn env_or_existing(name: &str, default_path: impl AsRef<Path>) -> Option<String> {
            env::var(name)
                .ok()
                .filter(|value| !value.trim().is_empty())
                .or_else(|| {
                    let default_path = default_path.as_ref();
                    default_path
                        .exists()
                        .then(|| default_path.to_string_lossy().to_string())
                })
        }

        fn env_or_existing_any(name: &str, default_paths: &[PathBuf]) -> Option<String> {
            env::var(name)
                .ok()
                .filter(|value| !value.trim().is_empty())
                .or_else(|| {
                    default_paths
                        .iter()
                        .find(|path| path.exists())
                        .map(|path| path.to_string_lossy().to_string())
                })
        }

        let cache_root = env::current_dir()
            .ok()
            .map(|cwd| cwd.join(".cache").join("native-artifacts"));
        let mut layered_sdf_npy_paths = BTreeMap::new();
        for (channel, _class_name) in RISK_SDF_CHANNELS {
            let env_name = format!("PROBEPLANNER_{}_NPY", channel.to_ascii_uppercase());
            let env_path = env::var(&env_name)
                .ok()
                .filter(|value| !value.trim().is_empty());
            let cache_path = cache_root
                .as_ref()
                .map(|root| root.join(format!("{channel}.npy")));
            let artifact_path = artifact_root.join(format!("{channel}.npy"));
            let resolved = env_path
                .or_else(|| {
                    cache_path
                        .as_ref()
                        .filter(|path| path.exists())
                        .map(|path| path.to_string_lossy().to_string())
                })
                .or_else(|| {
                    artifact_path
                        .exists()
                        .then(|| artifact_path.to_string_lossy().to_string())
                });
            if let Some(path) = resolved {
                layered_sdf_npy_paths.insert((*channel).to_string(), path);
            }
        }

        Self {
            atlas_path: env_or_existing(
                "PROBEPLANNER_ATLAS_NPY",
                artifact_root.join("annotation.npy"),
            ),
            sdf_path: env_or_existing(
                "PROBEPLANNER_SDF_NPZ",
                artifact_root.join("layered_sdf.npz"),
            ),
            sdf_npy_path: env_or_existing(
                "PROBEPLANNER_SDF_NPY",
                artifact_root.join("signed_sdf_mm.npy"),
            ),
            sdf_channel: env::var("PROBEPLANNER_SDF_CHANNEL")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .or_else(|| Some("signed_sdf_mm".to_string())),
            layered_sdf_npy_paths,
            region_index_path: env_or_existing_any(
                "PROBEPLANNER_REGION_MAP_JSON",
                &[
                    artifact_root.join("region_map_v2.json"),
                    artifact_root.join("region_map.json"),
                ],
            ),
        }
    }
}

fn resolve_artifact_profile_root(base: &Path) -> PathBuf {
    for env_name in [
        "PROBEPLANNER_MRI_CCF_ACTIVE_PROFILE_PATH",
        "PROBEPLANNER_MRI_CCF_DEFAULT_PROFILE_DIR",
    ] {
        if let Ok(value) = env::var(env_name) {
            let candidate = PathBuf::from(value.trim());
            if has_native_atlas_artifacts(&candidate) {
                return candidate;
            }
        }
    }

    if has_native_atlas_artifacts(base) {
        return base.to_path_buf();
    }

    if let Ok(profile_name) = env::var("PROBEPLANNER_MRI_CCF_PROFILE_NAME") {
        let candidate = base.join(profile_name.trim());
        if has_native_atlas_artifacts(&candidate) {
            return candidate;
        }
    }

    base.to_path_buf()
}

fn has_native_atlas_artifacts(path: &Path) -> bool {
    path.join("annotation.npy").exists()
        && (path.join("region_map_v2.json").exists() || path.join("region_map.json").exists())
}

impl NativeAtlas {
    fn load(config: &PlannerEngineConfig) -> Result<Self, crate::EngineError> {
        let annotation_path = config
            .atlas_path
            .as_deref()
            .ok_or(crate::EngineError::OutOfBounds)?;
        let region_path = config
            .region_index_path
            .as_deref()
            .ok_or(crate::EngineError::OutOfBounds)?;
        let annotation = MmapVolume::open_npy(
            annotation_path,
            [VOXEL_SIZE_UM, VOXEL_SIZE_UM, VOXEL_SIZE_UM],
            [0.0, 0.0, 0.0],
        )?;
        let label_stats = Self::build_label_stats(&annotation);
        let regions = RegionLookup::from_json(region_path)?;
        let sdf_npy = config.sdf_npy_path.as_deref().and_then(|path| {
            MmapVolume::open_npy(
                path,
                [VOXEL_SIZE_UM, VOXEL_SIZE_UM, VOXEL_SIZE_UM],
                [0.0, 0.0, 0.0],
            )
            .ok()
        });
        let sdf = if sdf_npy.is_none() {
            config
                .sdf_path
                .as_deref()
                .and_then(|path| {
                    let channel = config.sdf_channel.as_deref().unwrap_or("signed_sdf_mm");
                    load_f32_npy_from_npz(path, channel).ok()
                })
                .and_then(|(values, dims)| {
                    let grid = SdfGrid {
                        dims,
                        spacing_mm: [VOXEL_SIZE_UM / 1000.0; 3],
                        origin_mm: [0.0, 0.0, 0.0],
                        values,
                    };
                    grid.validate().ok().map(|_| grid)
                })
        } else {
            None
        };
        let risk_sdfs = Self::load_risk_sdfs(config);
        Ok(Self {
            annotation,
            regions,
            label_stats,
            sdf_npy,
            sdf,
            risk_sdfs,
        })
    }

    fn load_risk_sdfs(config: &PlannerEngineConfig) -> HashMap<String, SdfVolume> {
        let mut out = HashMap::new();
        let allow_npz_fallback = env_bool("PROBEPLANNER_LOAD_LAYERED_SDF_FROM_NPZ", true);
        for (channel, class_name) in RISK_SDF_CHANNELS {
            if let Some(path) = config.layered_sdf_npy_paths.get(*channel) {
                if let Ok(volume) = MmapVolume::open_npy(
                    path,
                    [VOXEL_SIZE_UM, VOXEL_SIZE_UM, VOXEL_SIZE_UM],
                    [0.0, 0.0, 0.0],
                ) {
                    out.insert((*class_name).to_string(), SdfVolume::Mmap(volume));
                    continue;
                }
            }
            if !allow_npz_fallback {
                continue;
            }
            let Some(path) = config.sdf_path.as_deref() else {
                continue;
            };
            let Ok((values, dims)) = load_f32_npy_from_npz(path, channel) else {
                continue;
            };
            let grid = SdfGrid {
                dims,
                spacing_mm: [VOXEL_SIZE_UM / 1000.0; 3],
                origin_mm: [0.0, 0.0, 0.0],
                values,
            };
            if grid.validate().is_ok() {
                out.insert((*class_name).to_string(), SdfVolume::Grid(grid));
            }
        }
        out
    }

    fn target_model(&self, key: &str, idx: usize) -> Option<TargetModel> {
        let descendant_ids = self.regions.descendant_ids_for(key);
        if descendant_ids.is_empty() {
            return None;
        }
        let mut sorted_ids = descendant_ids.clone();
        sorted_ids.sort_unstable();
        let preferred_side = preferred_ml_side(key, idx);
        let mut total_count = 0usize;
        let mut total_sum = [0f64; 3];
        let mut side_count = 0usize;
        let mut side_sum = [0f64; 3];

        for label in &sorted_ids {
            if let Some(moment) = self.label_stats.get(label) {
                total_count += moment.total_count;
                add_sum(&mut total_sum, moment.total_sum);
                let (count, sum) = if preferred_side >= 0.0 {
                    (moment.positive_count, moment.positive_sum)
                } else {
                    (moment.negative_count, moment.negative_sum)
                };
                side_count += count;
                add_sum(&mut side_sum, sum);
            }
        }

        let (count, sum) = if side_count > 0 {
            (side_count, side_sum)
        } else {
            (total_count, total_sum)
        };
        if count == 0 {
            return None;
        }
        let n = count as f64;
        Some(TargetModel {
            key: key.to_string(),
            center_mm: [
                (sum[0] / n) as f32,
                (sum[1] / n) as f32,
                (sum[2] / n) as f32,
            ],
            descendant_ids: sorted_ids,
            atlas_backed: true,
        })
    }

    fn build_label_stats(annotation: &MmapVolume) -> HashMap<u32, LabelMoment> {
        let spec = annotation.spec();
        let mut stats = HashMap::new();
        for x in 0..spec.dims[0] {
            for y in 0..spec.dims[1] {
                for z in 0..spec.dims[2] {
                    let Some(label) = annotation.sample_label(x, y, z) else {
                        continue;
                    };
                    if label == 0 {
                        continue;
                    }
                    let ccf_um = [
                        x as f32 * spec.spacing_um[0] + spec.origin_um[0],
                        y as f32 * spec.spacing_um[1] + spec.origin_um[1],
                        z as f32 * spec.spacing_um[2] + spec.origin_um[2],
                    ];
                    stats
                        .entry(label)
                        .or_insert_with(LabelMoment::default)
                        .add(ccf_um_to_bregma_mm(ccf_um));
                }
            }
        }
        stats
    }

    fn trajectory_profile(
        &self,
        voxels: &[VoxelCoord],
        brain_bounds_voxels: &[VoxelCoord],
        trajectories: &[TrajectorySpec],
        risk_inflation_mm: f32,
        step_um: f32,
        targets: &[TargetModel],
        target_lookup: &TargetLabelLookup,
    ) -> AtlasTrajectoryProfile {
        let mut coverage = BitSet::new(targets.len());
        let mut overlap_counts = vec![0usize; targets.len()];
        let mut min_sdf_distance_mm: Option<f32> = None;
        let mut risk_distances_mm: BTreeMap<String, f32> = BTreeMap::new();
        let mut brain_inside = Vec::with_capacity(voxels.len());

        for (sample_idx, voxel) in voxels.iter().enumerate() {
            if voxel.x < 0 || voxel.y < 0 || voxel.z < 0 {
                brain_inside.push(false);
                continue;
            }
            let x = voxel.x as usize;
            let y = voxel.y as usize;
            let z = voxel.z as usize;
            let label = self.annotation.sample_label(x, y, z).unwrap_or(0);
            if let Some(target_indices) = target_lookup.by_label.get(&label) {
                for target_idx in target_indices {
                    coverage.set(*target_idx);
                    overlap_counts[*target_idx] += 1;
                }
            }
            if sample_idx % SDF_SCORING_STRIDE == 0 {
                let sample = self.sdf_sample_voxel(x, y, z);
                if sample.inside_grid && sample.distance_mm.is_finite() {
                    min_sdf_distance_mm =
                        Some(min_sdf_distance_mm.map_or(sample.distance_mm, |current| {
                            current.min(sample.distance_mm)
                        }));
                }
            }
        }

        for voxel in brain_bounds_voxels {
            if voxel.x < 0 || voxel.y < 0 || voxel.z < 0 {
                brain_inside.push(false);
                continue;
            }
            let label = self
                .annotation
                .sample_label(voxel.x as usize, voxel.y as usize, voxel.z as usize)
                .unwrap_or(0);
            brain_inside.push(label > 0);
        }

        let risk_step_um = step_um.clamp(VOXEL_SIZE_UM, RISK_SDF_TRILINEAR_STEP_UM);
        for trajectory in trajectories {
            let n = (trajectory.depth_um.max(0.0) / risk_step_um).ceil() as usize;
            for i in 0..=n {
                let d = (i as f32 * risk_step_um).min(trajectory.depth_um.max(0.0));
                let point_um = [
                    trajectory.entry_um[0] + trajectory.direction_unit[0] * d,
                    trajectory.entry_um[1] + trajectory.direction_unit[1] * d,
                    trajectory.entry_um[2] + trajectory.direction_unit[2] * d,
                ];
                for (class_name, sdf) in &self.risk_sdfs {
                    if ENTRY_CORRIDOR_RISK_CLASSES.contains(&class_name.as_str()) {
                        if d <= SKULL_ENTRY_CORRIDOR_ALLOWANCE_UM {
                            continue;
                        }
                        if i % ENTRY_CORRIDOR_RISK_SAMPLE_STRIDE != 0 {
                            continue;
                        }
                    }
                    let sample = sdf.sample_point_um(point_um);
                    if sample.inside_grid && sample.distance_mm.is_finite() {
                        let distance_mm = sample.distance_mm - risk_inflation_mm.max(0.0);
                        risk_distances_mm
                            .entry(class_name.clone())
                            .and_modify(|current| *current = current.min(distance_mm))
                            .or_insert(distance_mm);
                    }
                }
            }
        }

        let covered_regions = targets
            .iter()
            .enumerate()
            .filter_map(|(idx, target)| coverage.get(idx).then(|| target.key.clone()))
            .collect();
        let overlap_voxels = overlap_counts
            .into_iter()
            .enumerate()
            .filter_map(|(idx, count)| {
                if count == 0 {
                    None
                } else {
                    Some((targets[idx].key.clone(), count))
                }
            })
            .collect();
        AtlasTrajectoryProfile {
            coverage,
            covered_regions,
            overlap_voxels,
            min_sdf_distance_mm,
            risk_distances_mm,
            brain_bounds_summary: Some(BrainBoundsSummary::from_inside_series(
                &brain_inside,
                step_um,
            )),
        }
    }

    fn sdf_sample_voxel(&self, x: usize, y: usize, z: usize) -> crate::SdfSample {
        if let Some(sdf) = &self.sdf_npy {
            return sdf.sample_f32(x, y, z).map_or(
                crate::SdfSample {
                    distance_mm: f32::INFINITY,
                    inside_grid: false,
                },
                |distance_mm| crate::SdfSample {
                    distance_mm,
                    inside_grid: distance_mm.is_finite(),
                },
            );
        }
        self.sdf.as_ref().map_or(
            crate::SdfSample {
                distance_mm: f32::INFINITY,
                inside_grid: false,
            },
            |sdf| sdf.sample_voxel(x, y, z),
        )
    }
}

impl PlannerCore {
    pub fn new(config: PlannerEngineConfig) -> Self {
        let atlas = NativeAtlas::load(&config).ok().map(Arc::new);
        Self {
            atlas,
            target_cache: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn health_flags(&self) -> (bool, bool, bool) {
        let atlas_loaded = self.atlas.is_some();
        let sdf_loaded = self
            .atlas
            .as_ref()
            .map(|atlas| {
                atlas.sdf_npy.is_some() || atlas.sdf.is_some() || !atlas.risk_sdfs.is_empty()
            })
            .unwrap_or(false);
        let region_index_loaded = self
            .atlas
            .as_ref()
            .map(|atlas| atlas.regions.len() > 0)
            .unwrap_or(false);
        (atlas_loaded, sdf_loaded, region_index_loaded)
    }

    pub fn optimize(&self, request: OptimizeRequest) -> OptimizeResponse {
        let started = Instant::now();
        let algorithm_requested =
            string_param(&request.params, "algorithm", "greedy").to_ascii_lowercase();
        let constraints = ConstraintPolicy::from_request(&request);
        let conflict_policy = constraints.conflict_policy();
        let generated = self.generate_candidates(&request);
        let strict_atlas_targets = bool_param(&request.params, "strictAtlasTargets", true);
        if strict_atlas_targets
            && !constraints.allow_fallback_target_coverage
            && !generated.fallback_target_keys.is_empty()
        {
            let mut compute_profile = BTreeMap::new();
            compute_profile.insert("nativeKernelStatus".to_string(), json!("blocked"));
            compute_profile.insert("targetCount".to_string(), json!(generated.target_count));
            compute_profile.insert(
                "fallbackTargetKeys".to_string(),
                json!(generated.fallback_target_keys),
            );
            let mut stage_breakdown = BTreeMap::new();
            stage_breakdown.insert("engineService".to_string(), json!(true));
            stage_breakdown.insert(
                "nativeEngineStatus".to_string(),
                json!("strict_target_lookup_failed"),
            );
            stage_breakdown.insert("candidateUniverseCount".to_string(), json!(0));
            stage_breakdown.insert("candidatePoolCount".to_string(), json!(0));
            stage_breakdown.insert("selectedCount".to_string(), json!(0));
            stage_breakdown.insert("uncoveredCount".to_string(), json!(generated.target_count));
            return OptimizeResponse {
                ok: false,
                code: Some("strict_atlas_target_lookup_failed".to_string()),
                error: Some(
                    "One or more requested target keys are not backed by the atlas index"
                        .to_string(),
                ),
                engine_version: crate::api::engine_version().to_string(),
                engine_status: "strict_target_lookup_failed".to_string(),
                algorithm: algorithm_requested,
                candidate_count: 0,
                candidate_universe_count: 0,
                selected: Vec::new(),
                uncovered: generated.target_keys,
                required_coverage_units: generated.target_count,
                covered_coverage_units: 0,
                time_ms: elapsed_ms(started),
                compute_profile,
                candidate_stage_breakdown: stage_breakdown,
            };
        }

        let solver_started = Instant::now();
        let kernels: Vec<CandidateKernel> = generated
            .candidates
            .iter()
            .map(|candidate| CandidateKernel {
                id: candidate.id,
                coverage: candidate.coverage.clone(),
                trajectory_voxels: candidate.trajectory_voxels.clone(),
                objective_score: candidate.objective_score,
                entry_um: candidate.entry_um,
                tip_um: candidate.tip_um,
                radius_um: candidate.probe_radius_um,
                collision_radius_um: candidate.collision_radius_um,
                shank_offsets_um: candidate.shank_offsets_um.clone(),
            })
            .collect();

        let max_probe_count = usize_param(
            &request.probe_params,
            "count",
            generated.target_count.max(1),
        )
        .clamp(1, 16);
        let solver_plan = match SolverPlan::from_request(
            &request,
            &algorithm_requested,
            kernels.len(),
        ) {
            Ok(plan) => plan,
            Err(unsupported) => {
                let mut compute_profile = BTreeMap::new();
                compute_profile.insert("nativeKernelStatus".to_string(), json!("blocked"));
                compute_profile.insert("targetCount".to_string(), json!(generated.target_count));
                compute_profile.insert(
                    "fallbackTargetKeys".to_string(),
                    json!(generated.fallback_target_keys.clone()),
                );
                compute_profile.insert("solverRuntimePolicy".to_string(), json!({
                    "requestedAlgorithm": algorithm_requested,
                    "requestedDrccSolver": unsupported.requested_solver,
                    "effectiveAlgorithm": "unsupported_native_drcc_solver",
                    "supportedNativeDrccSolvers": ["exact_pool", "hybrid", "heuristic", "cvar_relax"],
                    "unsupportedDrccSolver": true,
                    "nativeDrccApproximation": false,
                    "candidateCount": kernels.len(),
                    "maxProbeCount": max_probe_count,
                }));
                let mut stage_breakdown = BTreeMap::new();
                stage_breakdown.insert("engineService".to_string(), json!(true));
                stage_breakdown.insert(
                    "nativeEngineStatus".to_string(),
                    json!("unsupported_native_drcc_solver"),
                );
                stage_breakdown.insert(
                    "candidateUniverseCount".to_string(),
                    json!(generated.candidates.len()),
                );
                stage_breakdown.insert("candidatePoolCount".to_string(), json!(0));
                stage_breakdown.insert("selectedCount".to_string(), json!(0));
                stage_breakdown.insert("uncoveredCount".to_string(), json!(generated.target_count));
                return OptimizeResponse {
                    ok: false,
                    code: Some("unsupported_native_drcc_solver".to_string()),
                    error: Some(unsupported.message),
                    engine_version: crate::api::engine_version().to_string(),
                    engine_status: "unsupported_native_drcc_solver".to_string(),
                    algorithm: algorithm_requested,
                    candidate_count: 0,
                    candidate_universe_count: generated.candidates.len(),
                    selected: Vec::new(),
                    uncovered: generated.target_keys,
                    required_coverage_units: generated.target_count,
                    covered_coverage_units: 0,
                    time_ms: elapsed_ms(started),
                    compute_profile,
                    candidate_stage_breakdown: stage_breakdown,
                };
            }
        };
        let solver_kernels = if solver_plan.pool_size < kernels.len() {
            reduce_solver_pool(&kernels, solver_plan.pool_size, generated.target_count)
        } else {
            kernels.clone()
        };
        let selected_ids = match solver_plan.kind {
            SolverKind::Greedy | SolverKind::DrccHeuristic => {
                greedy_select(&kernels, max_probe_count, &conflict_policy)
            }
            SolverKind::Exact | SolverKind::DrccExactPool | SolverKind::DrccCvarRelax => {
                exact_branch_and_bound(&solver_kernels, max_probe_count, &conflict_policy)
            }
            SolverKind::DrccHybrid => {
                let exact_ids =
                    exact_branch_and_bound(&solver_kernels, max_probe_count, &conflict_policy);
                if exact_ids.is_empty() && !kernels.is_empty() {
                    greedy_select(&kernels, max_probe_count, &conflict_policy)
                } else {
                    exact_ids
                }
            }
        };
        let solver_ms = elapsed_ms(solver_started);

        let by_id: HashMap<u32, &CandidateDraft> =
            generated.candidates.iter().map(|c| (c.id, c)).collect();
        let selected_candidates: Vec<&CandidateDraft> = selected_ids
            .iter()
            .filter_map(|id| by_id.get(id).copied())
            .collect();
        let mut covered = BitSet::new(generated.target_count);
        for candidate in &selected_candidates {
            covered.union_with(&candidate.coverage);
        }
        let uncovered: Vec<String> = generated
            .target_keys
            .iter()
            .enumerate()
            .filter_map(|(idx, key)| {
                if covered.get(idx) {
                    None
                } else {
                    Some(key.clone())
                }
            })
            .collect();
        let selected_payload = selected_candidates
            .iter()
            .enumerate()
            .map(|(rank, candidate)| candidate_payload(candidate, rank + 1))
            .collect::<Vec<_>>();

        let total_ms = elapsed_ms(started);
        let mut compute_profile = BTreeMap::new();
        compute_profile.insert("nativeKernelStatus".to_string(), json!("active"));
        compute_profile.insert("targetCount".to_string(), json!(generated.target_count));
        compute_profile.insert("atlasResident".to_string(), json!(self.atlas.is_some()));
        compute_profile.insert(
            "sdfResident".to_string(),
            json!(self
                .atlas
                .as_ref()
                .map(|atlas| atlas.sdf_npy.is_some()
                    || atlas.sdf.is_some()
                    || !atlas.risk_sdfs.is_empty())
                .unwrap_or(false)),
        );
        compute_profile.insert(
            "sdfDataSource".to_string(),
            json!(self.atlas.as_ref().map_or("none", |atlas| {
                if atlas.sdf_npy.is_some() {
                    "mmap_npy"
                } else if atlas.sdf.is_some() {
                    "npz_loaded_vec"
                } else {
                    "none"
                }
            })),
        );
        compute_profile.insert(
            "regionIndexResident".to_string(),
            json!(self
                .atlas
                .as_ref()
                .map(|atlas| atlas.regions.len() > 0)
                .unwrap_or(false)),
        );
        compute_profile.insert(
            "layeredSdfClasses".to_string(),
            json!(self
                .atlas
                .as_ref()
                .map_or_else(Vec::<String>::new, |atlas| {
                    let mut classes = atlas.risk_sdfs.keys().cloned().collect::<Vec<_>>();
                    classes.sort();
                    classes
                })),
        );
        compute_profile.insert(
            "nativeDataSource".to_string(),
            json!(if self.atlas.is_some() {
                "mmap_atlas_region_index_sdf"
            } else {
                "deterministic_priors_fallback"
            }),
        );
        compute_profile.insert(
            "fallbackTargetKeys".to_string(),
            json!(generated.fallback_target_keys.clone()),
        );
        compute_profile.insert(
            "trajectoryBitsetBins".to_string(),
            json!(TRAJECTORY_BITSET_BINS),
        );
        compute_profile.insert(
            "nativeTimingsMs".to_string(),
            json!({
                "generationMs": generated.generation_ms,
                "scoringMs": generated.scoring_ms,
                "solverMs": solver_ms,
                "totalMs": total_ms,
            }),
        );
        compute_profile.insert(
            "solverRuntimePolicy".to_string(),
            json!({
                "requestedAlgorithm": algorithm_requested,
                "effectiveAlgorithm": solver_plan.effective_algorithm,
                "requestedDrccSolver": solver_plan.requested_drcc_solver,
                "effectiveDrccSolver": solver_plan.effective_drcc_solver,
                "nativeDrccApproximation": solver_plan.native_drcc_approximation,
                "supportedNativeDrccSolvers": ["exact_pool", "hybrid", "heuristic", "cvar_relax"],
                "candidateCount": kernels.len(),
                "solverPoolSize": solver_kernels.len(),
                "solverPoolLimit": solver_plan.pool_size,
                "maxProbeCount": max_probe_count,
                "minEntrySpacingUm": conflict_policy.min_entry_spacing_um,
                "minClearanceUm": conflict_policy.min_clearance_um,
                "shankCount": constraints.shank_count,
                "shankSpacingUm": constraints.shank_spacing_um,
                "collisionRadiusUm": constraints.collision_radius_um,
                "drccEnabled": constraints.drcc_active(),
                "drccStrict": constraints.drcc_strict,
                "drccRiskEpsilon": constraints.drcc_risk_epsilon,
                "drccWassersteinRadiusMm": constraints.drcc_wasserstein_radius_mm,
            }),
        );

        let mut stage_breakdown = BTreeMap::new();
        stage_breakdown.insert("engineService".to_string(), json!(true));
        stage_breakdown.insert(
            "nativeEngineStatus".to_string(),
            json!("native_kernel_active"),
        );
        stage_breakdown.insert(
            "candidateUniverseCount".to_string(),
            json!(generated.candidates.len()),
        );
        stage_breakdown.insert(
            "nativeRejectedCount".to_string(),
            json!(generated.rejected_count),
        );
        stage_breakdown.insert(
            "nativeRejectReasons".to_string(),
            json!(generated.rejection_reasons),
        );
        stage_breakdown.insert(
            "candidatePoolCount".to_string(),
            json!(generated.candidates.len()),
        );
        stage_breakdown.insert("selectedCount".to_string(), json!(selected_payload.len()));
        stage_breakdown.insert("uncoveredCount".to_string(), json!(uncovered.len()));
        stage_breakdown.insert(
            "timingsMs".to_string(),
            json!({
                "generationMs": generated.generation_ms,
                "scoringMs": generated.scoring_ms,
                "solverMs": solver_ms,
            }),
        );

        OptimizeResponse {
            ok: true,
            code: None,
            error: None,
            engine_version: crate::api::engine_version().to_string(),
            engine_status: "native_kernel_active".to_string(),
            algorithm: algorithm_requested,
            candidate_count: generated.candidates.len(),
            candidate_universe_count: generated.candidates.len(),
            selected: selected_payload,
            uncovered,
            required_coverage_units: generated.target_count,
            covered_coverage_units: covered.count_ones() as usize,
            time_ms: total_ms,
            compute_profile,
            candidate_stage_breakdown: stage_breakdown,
        }
    }

    fn generate_candidates(&self, request: &OptimizeRequest) -> GeneratedCandidates {
        let generation_started = Instant::now();
        let target_keys = request.target_keys.clone();
        let targets: Vec<TargetModel> = target_keys
            .iter()
            .enumerate()
            .map(|(idx, key)| self.target_model_for(key, idx))
            .collect();
        let fallback_target_keys = targets
            .iter()
            .filter(|target| !target.atlas_backed)
            .map(|target| target.key.clone())
            .collect::<Vec<_>>();
        let constraints = ConstraintPolicy::from_request(request);
        let candidate_seed_targets = targets
            .iter()
            .filter(|target| target.atlas_backed || constraints.allow_fallback_target_coverage)
            .cloned()
            .collect::<Vec<_>>();

        let target_count = targets.len().max(1);
        let coverage_radius_mm =
            (f32_param(&request.params, "coverageRadius", 600.0) / 1000.0).clamp(0.1, 3.5);
        let grid_spacing_mm =
            (f32_param(&request.params, "gridSpacing", 1000.0) / 1000.0).clamp(0.1, 4.0);
        let max_angle = f32_param(&request.params, "maxAngle", 10.0).clamp(0.0, 45.0);
        let angle_step = f32_param(&request.params, "angleStep", 5.0)
            .abs()
            .clamp(1.0, 20.0);
        let requested_depth_um = f32_param(
            &request.probe_params,
            "depthUm",
            f32_param(
                &request.params,
                "depthUm",
                f32_param(&request.probe_params, "depth", 10.0).max(0.5) * 1000.0,
            ),
        )
        .max(500.0);
        let step_um = f32_param(&request.params, "candidateCoarseStepUm", 100.0).clamp(10.0, 500.0);
        let limit = usize_param(&request.params, "nativeCandidateLimit", 8192).clamp(32, 50_000);

        let mut geometries = Vec::new();
        let fine_offset_mm = (coverage_radius_mm * 0.5).min(grid_spacing_mm).max(0.05);
        let coarse_offset_mm = (coverage_radius_mm * 0.9)
            .min(grid_spacing_mm)
            .max(fine_offset_mm);
        let offsets = [
            -coarse_offset_mm,
            -fine_offset_mm,
            0.0,
            fine_offset_mm,
            coarse_offset_mm,
        ];
        let angle_values = angle_values(max_angle, angle_step);
        let mut next_id = 1u32;

        if candidate_seed_targets.len() > 1 {
            let center = centroid(&candidate_seed_targets);
            geometries.push((next_id, center, 0.0, 0.0, requested_depth_um, true));
            next_id += 1;
        }

        for target in &candidate_seed_targets {
            for ap_offset in offsets {
                for ml_offset in offsets {
                    for theta in &angle_values {
                        let phis: &[f32] = if theta.abs() < f32::EPSILON {
                            &[0.0]
                        } else {
                            &[0.0, 90.0, 180.0, 270.0]
                        };
                        for phi in phis {
                            if geometries.len() >= limit {
                                break;
                            }
                            let (ap_mm, ml_mm, depth_um) = entry_for_target(
                                target.center_mm,
                                ap_offset,
                                ml_offset,
                                *theta,
                                *phi,
                                requested_depth_um,
                            );
                            geometries.push((
                                next_id,
                                [ap_mm, target.center_mm[1], ml_mm],
                                *theta,
                                *phi,
                                depth_um,
                                false,
                            ));
                            next_id += 1;
                        }
                    }
                }
            }
        }
        let generation_ms = elapsed_ms(generation_started);

        let scoring_started = Instant::now();
        let target_lookup = TargetLabelLookup::new(&targets);
        let mut candidates = Vec::new();
        let mut rejected_count = 0usize;
        let mut rejection_reasons = BTreeMap::new();
        for (id, geom, theta_deg, phi_deg, depth_um, aggregate_seed) in geometries {
            match score_candidate(
                id,
                geom[0],
                geom[2],
                theta_deg,
                phi_deg,
                depth_um,
                step_um,
                &targets,
                coverage_radius_mm,
                aggregate_seed,
                self.atlas.as_deref(),
                &target_lookup,
                &constraints,
            ) {
                Ok(candidate) => candidates.push(candidate),
                Err(reason) => {
                    rejected_count += 1;
                    *rejection_reasons.entry(reason).or_insert(0) += 1;
                }
            }
        }
        let scoring_ms = elapsed_ms(scoring_started);

        GeneratedCandidates {
            candidates,
            target_count,
            target_keys,
            fallback_target_keys,
            generation_ms,
            scoring_ms,
            rejected_count,
            rejection_reasons,
        }
    }

    fn target_model_for(&self, key: &str, idx: usize) -> TargetModel {
        let cache_key = format!("{key}#{idx}");
        if let Ok(guard) = self.target_cache.lock() {
            if let Some(cached) = guard.get(&cache_key) {
                return cached.clone();
            }
        }
        let model = self
            .atlas
            .as_ref()
            .and_then(|atlas| atlas.target_model(key, idx))
            .unwrap_or_else(|| fallback_target_model(key, idx));
        if let Ok(mut guard) = self.target_cache.lock() {
            guard.insert(cache_key, model.clone());
        }
        model
    }
}

fn score_candidate(
    id: u32,
    ap_mm: f32,
    ml_mm: f32,
    theta_deg: f32,
    phi_deg: f32,
    depth_um: f32,
    step_um: f32,
    targets: &[TargetModel],
    coverage_radius_mm: f32,
    aggregate_seed: bool,
    atlas: Option<&NativeAtlas>,
    target_lookup: &TargetLabelLookup,
    constraints: &ConstraintPolicy,
) -> Result<CandidateDraft, String> {
    let (entry_um, tip_um, direction) =
        trajectory_geometry(ap_mm, ml_mm, theta_deg, phi_deg, depth_um);
    if constraints.enforce_max_angle() && theta_deg.abs() > constraints.max_angle_deg {
        return Err("maxAngle".to_string());
    }
    if constraints.enforce_lambda_boundary() && ap_mm < LAMBDA_AP_MM {
        return Err("lambdaBoundary".to_string());
    }
    let entry_mm = ccf_um_to_bregma_mm(entry_um);
    let tip_mm = ccf_um_to_bregma_mm(tip_um);
    if constraints.enforce_brain_bounds() {
        if ap_mm < ENTRY_AP_MIN_MM || ap_mm > ENTRY_AP_MAX_MM {
            return Err("entry_ap_bounds".to_string());
        }
        if tip_mm[0] < TIP_POST_LIMIT_MM {
            return Err("tip_ap_bounds".to_string());
        }
        if tip_mm[1] > MAX_DV_MM {
            return Err("tip_dv_bounds".to_string());
        }
    }
    let grid = VoxelGridSpec {
        origin_um: [0.0, 0.0, 0.0],
        spacing_um: [VOXEL_SIZE_UM, VOXEL_SIZE_UM, VOXEL_SIZE_UM],
        dims: ATLAS_DIMS,
    };
    let profile_step_um = if constraints.shank_count > 1 {
        step_um.clamp(VOXEL_SIZE_UM, MULTI_SHANK_PROFILE_STEP_UM)
    } else {
        step_um.min(VOXEL_SIZE_UM)
    };
    let centerline_spec = TrajectorySpec {
        entry_um,
        direction_unit: direction,
        depth_um,
        step_um: profile_step_um,
    };
    let lateral_axis = probe_lateral_axis_um(direction);
    let shank_specs = constraints
        .shank_offsets_um
        .iter()
        .map(|offset_um| {
            let offset = scale(lateral_axis, *offset_um);
            TrajectorySpec {
                entry_um: add(entry_um, offset),
                direction_unit: direction,
                depth_um,
                step_um: profile_step_um,
            }
        })
        .collect::<Vec<_>>();
    let centerline_voxels = sample_trajectory_voxels(centerline_spec, grid);
    let mut voxel_seen = HashSet::new();
    let mut voxels = Vec::new();
    for spec in shank_specs.iter() {
        for voxel in sample_trajectory_voxels(*spec, grid) {
            if voxel_seen.insert(voxel) {
                voxels.push(voxel);
            }
        }
    }
    let mut trajectory_voxels = BitSet::new(TRAJECTORY_BITSET_BINS);
    for voxel in &voxels {
        trajectory_voxels.set(voxel_hash_bin(voxel.x, voxel.y, voxel.z));
    }

    let atlas_profile = atlas.map(|atlas| {
        atlas.trajectory_profile(
            &voxels,
            &centerline_voxels,
            &[centerline_spec],
            ((constraints.collision_radius_um - constraints.probe_radius_um).max(0.0)) / 1000.0,
            profile_step_um,
            targets,
            target_lookup,
        )
    });
    let mut coverage = atlas_profile
        .as_ref()
        .map(|profile| profile.coverage.clone())
        .unwrap_or_else(|| BitSet::new(targets.len()));
    let mut covered_regions = atlas_profile
        .as_ref()
        .map(|profile| profile.covered_regions.clone())
        .unwrap_or_default();
    let mut overlap_voxels = atlas_profile
        .as_ref()
        .map(|profile| profile.overlap_voxels.clone())
        .unwrap_or_default();
    let min_sdf_distance_mm = atlas_profile
        .as_ref()
        .and_then(|profile| profile.min_sdf_distance_mm);
    let risk_distances_mm = atlas_profile
        .as_ref()
        .map(|profile| profile.risk_distances_mm.clone())
        .unwrap_or_default();
    let brain_bounds_summary = atlas_profile
        .as_ref()
        .and_then(|profile| profile.brain_bounds_summary.clone());
    if constraints.enforce_brain_bounds() {
        if let Some(summary) = &brain_bounds_summary {
            if summary.available && !summary.passed {
                return Err(summary.reason.clone());
            }
        }
        if let Some(reason) = boundary_risk_rejection(&risk_distances_mm, constraints, false) {
            return Err(reason);
        }
    }
    if constraints.enforce_continuous_risk() {
        if risk_distances_mm.is_empty() && min_sdf_distance_mm.is_none() {
            return Err("risk_model_unavailable".to_string());
        }
        if let Some(reason) = boundary_risk_rejection(&risk_distances_mm, constraints, true) {
            return Err(reason);
        }
    }
    let mut proximity_score = 0.0f32;
    let mut min_dist = f32::INFINITY;

    for (idx, target) in targets.iter().enumerate() {
        let dist = point_segment_distance_mm(target.center_mm, entry_mm, tip_mm);
        min_dist = min_dist.min(dist);
        proximity_score += 1.0 / (0.05 + dist);

        let use_distance_fallback = atlas.is_none() || !target.atlas_backed;
        let covered = use_distance_fallback
            && constraints.allow_fallback_target_coverage
            && (aggregate_seed || dist <= coverage_radius_mm);
        if covered && !coverage.get(idx) {
            coverage.set(idx);
            covered_regions.push(target.key.clone());
            let overlap = ((coverage_radius_mm - dist).max(0.05) * 120.0).round() as usize;
            overlap_voxels.insert(target.key.clone(), overlap.max(1));
        }
    }

    if covered_regions.is_empty() {
        return Err("noCoverage".to_string());
    }

    let coverage_score = coverage.count_ones() as f32 * 10_000.0;
    let angle_score = (45.0 - theta_deg.abs()).max(0.0) * 10.0;
    let center_penalty = (ap_mm.abs() + ml_mm.abs()) * 2.0;
    let aggregate_bonus = if aggregate_seed { 2_500.0 } else { 0.0 };
    let sdf_score = min_sdf_distance_mm
        .map(|distance| distance.clamp(-1.0, 3.0) * 150.0)
        .unwrap_or(0.0);
    let drcc_metrics = constraints
        .drcc_active()
        .then(|| compute_drcc_metrics(&risk_distances_mm, constraints));
    let drcc_penalty = drcc_metrics
        .as_ref()
        .map(|metrics| {
            let infeasible_penalty = if constraints.drcc_strict && !metrics.feasible {
                25_000.0
            } else {
                0.0
            };
            let violation_penalty = (metrics.worst_case_violation_prob
                / constraints.drcc_risk_epsilon.max(1e-6))
            .max(0.0)
                * 1_500.0;
            let integral_penalty = metrics.risk_integral_shortfall_mm
                * constraints.sdf_risk_integral_weight
                * 10_000.0;
            let tail_penalty =
                metrics.risk_tail_shortfall_mm * constraints.sdf_risk_cvar_weight * 10_000.0;
            infeasible_penalty + violation_penalty + integral_penalty + tail_penalty
        })
        .unwrap_or(0.0);
    let objective_score =
        coverage_score + proximity_score * 100.0 + angle_score + aggregate_bonus + sdf_score
            - center_penalty
            - drcc_penalty;

    Ok(CandidateDraft {
        id,
        ap_mm,
        ml_mm,
        theta_deg,
        phi_deg,
        depth_um,
        coverage,
        trajectory_voxels,
        covered_regions,
        overlap_voxels,
        entry_um,
        tip_um,
        direction,
        probe_radius_um: constraints.probe_radius_um,
        collision_radius_um: constraints.collision_radius_um,
        shank_count: constraints.shank_count,
        shank_spacing_um: constraints.shank_spacing_um,
        shank_offsets_um: constraints.shank_offsets_um.clone(),
        objective_score,
        min_target_distance_mm: min_dist,
        min_sdf_distance_mm,
        risk_distances_mm,
        drcc_metrics,
        brain_bounds_summary,
        sample_count: voxels.len(),
        aggregate_seed,
    })
}

fn compute_drcc_metrics(
    distances: &BTreeMap<String, f32>,
    constraints: &ConstraintPolicy,
) -> DrccMetrics {
    if distances.is_empty() {
        return DrccMetrics {
            available: false,
            worst_case_violation_prob: 0.0,
            risk_integral_shortfall_mm: 0.0,
            risk_tail_shortfall_mm: 0.0,
            risk_budget_epsilon: constraints.drcc_risk_epsilon,
            feasible: true,
        };
    }
    let sigma = constraints.risk_distance_sigma_mm.max(0.005);
    let mut worst_prob = 0.0f32;
    let mut shortfalls = Vec::with_capacity(distances.len());
    for (class_name, distance_mm) in distances {
        let threshold = constraints.risk_threshold_for(class_name);
        let robust_threshold = threshold + constraints.drcc_wasserstein_radius_mm;
        let shortfall = (robust_threshold - *distance_mm).max(0.0);
        let z = ((robust_threshold - *distance_mm) / sigma).clamp(-50.0, 50.0);
        let probability = 1.0 / (1.0 + (-z).exp());
        worst_prob = worst_prob.max(probability);
        shortfalls.push(shortfall);
    }
    shortfalls.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    let integral = shortfalls.iter().sum::<f32>();
    let tail_count = ((1.0 - constraints.drcc_cvar_alpha).max(0.001)
        * shortfalls.len().max(1) as f32)
        .ceil() as usize;
    let tail_slice = &shortfalls[..tail_count.clamp(1, shortfalls.len())];
    let tail = tail_slice.iter().sum::<f32>() / tail_slice.len().max(1) as f32;
    DrccMetrics {
        available: true,
        worst_case_violation_prob: worst_prob,
        risk_integral_shortfall_mm: integral,
        risk_tail_shortfall_mm: tail,
        risk_budget_epsilon: constraints.drcc_risk_epsilon,
        feasible: worst_prob <= constraints.drcc_risk_epsilon && integral <= 1e-6,
    }
}

fn reduce_solver_pool(
    candidates: &[CandidateKernel],
    limit: usize,
    target_count: usize,
) -> Vec<CandidateKernel> {
    if candidates.len() <= limit {
        return candidates.to_vec();
    }
    let safe_limit = limit.clamp(1, candidates.len());
    let mut selected: HashSet<usize> = HashSet::new();
    let per_target_keep = if target_count == 0 {
        0
    } else {
        (safe_limit / target_count.max(1)).clamp(2, 8)
    };

    for target_idx in 0..target_count {
        let mut ranked = candidates
            .iter()
            .enumerate()
            .filter(|(_, candidate)| candidate.coverage.get(target_idx))
            .collect::<Vec<_>>();
        ranked.sort_by(|(_, a), (_, b)| {
            b.objective_score
                .partial_cmp(&a.objective_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for (idx, _) in ranked.into_iter().take(per_target_keep) {
            selected.insert(idx);
            if selected.len() >= safe_limit {
                break;
            }
        }
        if selected.len() >= safe_limit {
            break;
        }
    }

    let mut best_by_mask: HashMap<u64, (usize, f32)> = HashMap::new();
    for (idx, candidate) in candidates.iter().enumerate() {
        let mask = coverage_mask_u64(&candidate.coverage, target_count);
        let entry = best_by_mask
            .entry(mask)
            .or_insert((idx, candidate.objective_score));
        if candidate.objective_score > entry.1 {
            *entry = (idx, candidate.objective_score);
        }
    }
    let mut masks = best_by_mask.into_values().collect::<Vec<_>>();
    masks.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    for (idx, _) in masks {
        selected.insert(idx);
        if selected.len() >= safe_limit {
            break;
        }
    }

    if selected.len() < safe_limit {
        let mut global = candidates.iter().enumerate().collect::<Vec<_>>();
        global.sort_by(|(_, a), (_, b)| {
            b.objective_score
                .partial_cmp(&a.objective_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for (idx, _) in global {
            selected.insert(idx);
            if selected.len() >= safe_limit {
                break;
            }
        }
    }

    let mut selected = selected.into_iter().collect::<Vec<_>>();
    selected.sort_by(|a, b| {
        candidates[*b]
            .objective_score
            .partial_cmp(&candidates[*a].objective_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    selected
        .into_iter()
        .map(|idx| candidates[idx].clone())
        .collect()
}

fn coverage_mask_u64(coverage: &BitSet, target_count: usize) -> u64 {
    let mut mask = 0u64;
    for idx in 0..target_count.min(64) {
        if coverage.get(idx) {
            mask |= 1u64 << idx;
        }
    }
    mask
}

fn candidate_payload(candidate: &CandidateDraft, pareto_rank: usize) -> Value {
    let overlap_sum: usize = candidate.overlap_voxels.values().copied().sum();
    let coverage_score = (candidate.covered_regions.len() as f32).min(4.0) / 4.0;
    let safety_margin = (3.0 - candidate.min_target_distance_mm).clamp(0.0, 3.0);
    let sdf_margin = candidate
        .min_sdf_distance_mm
        .map(round3)
        .unwrap_or(f32::NAN);
    let explain_score = (candidate.objective_score / 10_000.0).max(0.0);
    json!({
        "id": format!("native-{}", candidate.id),
        "nativeCandidateId": candidate.id,
        "nativeKernel": true,
        "ap_mm": round3(candidate.ap_mm),
        "ml_mm": round3(candidate.ml_mm),
        "ap_mm_corrected": round3(candidate.ap_mm),
        "ml_mm_corrected": round3(candidate.ml_mm),
        "theta_deg": round3(candidate.theta_deg),
        "phi_deg": round3(candidate.phi_deg),
        "depthUm": round3(candidate.depth_um),
        "depthUm_corrected": round3(candidate.depth_um),
        "entryDvCorr_mm": 0.0,
        "entryDvCorr_mm_corrected": 0.0,
        "entry_xyz": round_array1(candidate.entry_um),
        "tip_xyz": round_array1(candidate.tip_um),
        "entry": round_array1(candidate.entry_um),
        "tip": round_array1(candidate.tip_um),
        "dir": round_array6(candidate.direction),
        "radiusUm": round3(candidate.probe_radius_um),
        "probeRadiusUm": round3(candidate.probe_radius_um),
        "collisionRadiusUm": round3(candidate.collision_radius_um),
        "shanks": candidate.shank_count,
        "shankCount": candidate.shank_count,
        "shankSpacingUm": round3(candidate.shank_spacing_um),
        "shankPitchUm": round3(candidate.shank_spacing_um),
        "shankOffsetsUm": candidate.shank_offsets_um.iter().map(|value| round3(*value)).collect::<Vec<_>>(),
        "coveredRegions": candidate.covered_regions,
        "overlapVoxels": candidate.overlap_voxels,
        "voxelMode": true,
        "explainScore": round3(explain_score),
        "robustScore": round3((0.9 - candidate.min_target_distance_mm * 0.05).clamp(0.0, 1.0)),
        "paretoRank": pareto_rank,
        "coveredGain": candidate.covered_regions.len(),
        "safetyMarginMm": round3(safety_margin),
        "minSafetyDistanceMm": round3(safety_margin),
        "minSdfDistanceMm": candidate.min_sdf_distance_mm.map(round3),
        "sampleCount": candidate.sample_count,
        "aggregateSeed": candidate.aggregate_seed,
        "objectiveScores": {
            "coverage": round3(coverage_score),
            "safety": round3(candidate.min_sdf_distance_mm.map(|value| ((value + 1.0) / 4.0).clamp(0.0, 1.0)).unwrap_or((safety_margin / 3.0).clamp(0.0, 1.0))),
            "angle": round3(((45.0 - candidate.theta_deg.abs()) / 45.0).clamp(0.0, 1.0)),
            "robust": round3((0.9 - candidate.min_target_distance_mm * 0.05).clamp(0.0, 1.0))
        },
        "objectiveWeighted": {
            "coverage": round3(coverage_score * 0.5),
            "safety": round3(candidate.min_sdf_distance_mm.map(|value| ((value + 1.0) / 4.0).clamp(0.0, 1.0)).unwrap_or((safety_margin / 3.0).clamp(0.0, 1.0)) * 0.3),
            "angle": round3(((45.0 - candidate.theta_deg.abs()) / 45.0).clamp(0.0, 1.0) * 0.2)
        },
        "riskPrefilter": {
            "bucket": "feasible",
            "worstMarginMm": candidate.min_sdf_distance_mm.map(round3).unwrap_or(round3(safety_margin)),
            "nativeSdfSampled": candidate.min_sdf_distance_mm.is_some(),
            "classMinDistanceMm": rounded_distance_map(&candidate.risk_distances_mm)
        },
        "riskDiagnostics": {
            "available": candidate.min_sdf_distance_mm.is_some(),
            "minDistanceMm": candidate.min_sdf_distance_mm.map(round3),
            "fallbackTargetDistanceMm": round3(candidate.min_target_distance_mm),
            "classMinDistanceMm": rounded_distance_map(&candidate.risk_distances_mm),
            "drcc": candidate.drcc_metrics.as_ref().map(drcc_metrics_json),
            "brainBounds": candidate.brain_bounds_summary.as_ref().map(BrainBoundsSummary::to_json)
        },
        "drccMetrics": candidate.drcc_metrics.as_ref().map(drcc_metrics_json),
        "drccFeasible": candidate.drcc_metrics.as_ref().map(|metrics| metrics.feasible),
        "drccWorstCaseViolationProb": candidate.drcc_metrics.as_ref().map(|metrics| round3(metrics.worst_case_violation_prob)),
        "brainBoundsDiagnostics": candidate.brain_bounds_summary.as_ref().map(BrainBoundsSummary::to_json),
        "trajectoryProfile": {
            "nativeBitsetBins": TRAJECTORY_BITSET_BINS,
            "sampleCount": candidate.sample_count,
            "overlapSum": overlap_sum,
            "minSdfDistanceMm": if sdf_margin.is_nan() { Value::Null } else { json!(sdf_margin) }
        },
        "explain": {
            "summary": format!("Native engine candidate covering {} region(s)", candidate.covered_regions.len())
        }
    })
}

fn drcc_metrics_json(metrics: &DrccMetrics) -> Value {
    json!({
        "available": metrics.available,
        "worstCaseViolationProb": round3(metrics.worst_case_violation_prob),
        "riskIntegralShortfallMm": round3(metrics.risk_integral_shortfall_mm),
        "riskTailShortfallMm": round3(metrics.risk_tail_shortfall_mm),
        "riskBudgetEpsilon": round3(metrics.risk_budget_epsilon),
        "feasible": metrics.feasible,
    })
}

fn trajectory_geometry(
    ap_mm: f32,
    ml_mm: f32,
    theta_deg: f32,
    phi_deg: f32,
    depth_um: f32,
) -> ([f32; 3], [f32; 3], [f32; 3]) {
    let theta = theta_deg.to_radians();
    let phi = phi_deg.to_radians();
    let direction = [
        theta.sin() * phi.cos(),
        theta.cos(),
        theta.sin() * phi.sin(),
    ];
    let entry_um = [
        BREGMA_UM[0] - ap_mm * 1000.0,
        BREGMA_UM[1],
        BREGMA_UM[2] + ml_mm * 1000.0,
    ];
    let tip_um = [
        entry_um[0] + direction[0] * depth_um,
        entry_um[1] + direction[1] * depth_um,
        entry_um[2] + direction[2] * depth_um,
    ];
    (entry_um, tip_um, direction)
}

fn entry_for_target(
    center_mm: [f32; 3],
    ap_offset: f32,
    ml_offset: f32,
    theta_deg: f32,
    phi_deg: f32,
    requested_depth_um: f32,
) -> (f32, f32, f32) {
    let theta = theta_deg.to_radians();
    let phi = phi_deg.to_radians();
    let dir_x = theta.sin() * phi.cos();
    let dir_y = theta.cos().max(0.2);
    let dir_z = theta.sin() * phi.sin();
    let depth_to_center_um = (center_mm[1] * 1000.0 / dir_y).max(500.0);
    let ap_mm = center_mm[0] + ap_offset + (dir_x * depth_to_center_um / 1000.0);
    let ml_mm = center_mm[2] + ml_offset - (dir_z * depth_to_center_um / 1000.0);
    let max_depth_by_dv_um = (MAX_DV_MM * 1000.0 / dir_y).max(500.0);
    let depth_um = requested_depth_um
        .min(max_depth_by_dv_um)
        .min((depth_to_center_um + 500.0).max(500.0))
        .max(500.0);
    (ap_mm, ml_mm, depth_um)
}

fn ccf_um_to_bregma_mm(point_um: [f32; 3]) -> [f32; 3] {
    [
        BREGMA_UM[0] / 1000.0 - point_um[0] / 1000.0,
        point_um[1] / 1000.0 - BREGMA_UM[1] / 1000.0,
        point_um[2] / 1000.0 - BREGMA_UM[2] / 1000.0,
    ]
}

fn add_point(sum: &mut [f64; 3], point: [f32; 3]) {
    sum[0] += point[0] as f64;
    sum[1] += point[1] as f64;
    sum[2] += point[2] as f64;
}

fn add_sum(sum: &mut [f64; 3], value: [f64; 3]) {
    sum[0] += value[0];
    sum[1] += value[1];
    sum[2] += value[2];
}

fn point_segment_distance_mm(point: [f32; 3], start: [f32; 3], end: [f32; 3]) -> f32 {
    let ab = [end[0] - start[0], end[1] - start[1], end[2] - start[2]];
    let ap = [
        point[0] - start[0],
        point[1] - start[1],
        point[2] - start[2],
    ];
    let denom = dot(ab, ab).max(1e-9);
    let t = (dot(ap, ab) / denom).clamp(0.0, 1.0);
    let closest = [
        start[0] + ab[0] * t,
        start[1] + ab[1] * t,
        start[2] + ab[2] * t,
    ];
    let dx = point[0] - closest[0];
    let dy = point[1] - closest[1];
    let dz = point[2] - closest[2];
    (dx * dx + dy * dy + dz * dz).sqrt()
}

fn dot(a: [f32; 3], b: [f32; 3]) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
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

fn probe_lateral_axis_um(direction: [f32; 3]) -> [f32; 3] {
    let dir = normalize(direction, [0.0, 1.0, 0.0]);
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
        normalize(
            [
                dir[1] * 0.0 - dir[2] * 1.0,
                dir[2] * 0.0 - dir[0] * 0.0,
                dir[0] * 1.0 - dir[1] * 0.0,
            ],
            [1.0, 0.0, 0.0],
        )
    }
}

fn target_center_mm(key: &str, idx: usize) -> [f32; 3] {
    let upper = key.to_ascii_uppercase();
    let base = if upper.contains("CA1") {
        [-2.2, 1.7, 1.8]
    } else if upper.contains("DG") {
        [-2.0, 2.0, 1.4]
    } else if upper.contains("VIS") {
        [-3.6, 1.0, 2.5]
    } else if upper.contains("MOP") || upper.contains("MOTOR") {
        [1.0, 1.1, 1.6]
    } else if upper.contains("TH") || upper.contains("VPM") || upper.contains("LG") {
        [-1.6, 3.3, 1.2]
    } else if upper.contains("STR") || upper.contains("CP") {
        [0.6, 2.4, 1.9]
    } else {
        let h = stable_hash(key);
        [
            -4.0 + ((h & 0xff) as f32 / 255.0) * 7.0,
            0.8 + (((h >> 8) & 0xff) as f32 / 255.0) * 4.2,
            0.7 + (((h >> 16) & 0xff) as f32 / 255.0) * 3.4,
        ]
    };
    let side = if idx % 2 == 0 { 1.0 } else { -1.0 };
    [base[0], base[1], base[2] * side]
}

fn fallback_target_model(key: &str, idx: usize) -> TargetModel {
    TargetModel {
        key: key.to_string(),
        center_mm: target_center_mm(key, idx),
        descendant_ids: Vec::new(),
        atlas_backed: false,
    }
}

fn preferred_ml_side(key: &str, idx: usize) -> f32 {
    let upper = key.to_ascii_uppercase();
    if upper.contains("LEFT") || upper.ends_with("_L") || upper.ends_with("-L") {
        -1.0
    } else if upper.contains("RIGHT") || upper.ends_with("_R") || upper.ends_with("-R") {
        1.0
    } else if idx % 2 == 0 {
        1.0
    } else {
        -1.0
    }
}

fn centroid(targets: &[TargetModel]) -> [f32; 3] {
    let mut out = [0.0, 0.0, 0.0];
    for target in targets {
        out[0] += target.center_mm[0];
        out[1] += target.center_mm[1];
        out[2] += target.center_mm[2];
    }
    let n = targets.len().max(1) as f32;
    [out[0] / n, out[1] / n, out[2] / n]
}

fn angle_values(max_angle: f32, step: f32) -> Vec<f32> {
    let mut values = vec![0.0];
    let mut current = step;
    while current <= max_angle + 1e-6 {
        values.push(current.min(max_angle));
        current += step;
    }
    if max_angle > 0.0 {
        values.push(max_angle);
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    values.dedup_by(|a, b| (*a - *b).abs() < 1e-4);
    values
}

fn voxel_hash_bin(x: i32, y: i32, z: i32) -> usize {
    let mut h = 0xcbf29ce484222325u64;
    for value in [x as u64, y as u64, z as u64] {
        h ^= value.wrapping_add(0x9e3779b97f4a7c15);
        h = h.wrapping_mul(0x100000001b3);
    }
    (h as usize) % TRAJECTORY_BITSET_BINS
}

fn stable_hash(text: &str) -> u64 {
    let mut h = 0xcbf29ce484222325u64;
    for b in text.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn boundary_risk_rejection(
    distances: &BTreeMap<String, f32>,
    constraints: &ConstraintPolicy,
    use_thresholds: bool,
) -> Option<String> {
    for (class_name, distance_mm) in distances {
        let threshold = if use_thresholds {
            constraints.risk_threshold_for(class_name)
        } else {
            0.0
        };
        if ENTRY_CORRIDOR_RISK_CLASSES.contains(&class_name.as_str()) && threshold <= 0.0 {
            continue;
        }
        if *distance_mm < threshold {
            return Some(format!("risk_{class_name}"));
        }
    }
    None
}

fn balanced_risk_thresholds() -> BTreeMap<String, f32> {
    BTreeMap::from([
        ("default".to_string(), MIN_RISK_DISTANCE_MM_DEFAULT),
        ("vessel".to_string(), 0.60),
        ("ventricle".to_string(), 0.35),
        ("sulcus".to_string(), 0.40),
        ("skull".to_string(), 0.45),
        ("cortex_outside".to_string(), 0.05),
        ("brain_surface".to_string(), 0.05),
        ("drilling_surface".to_string(), 0.05),
    ])
}

fn enforce_boundary_risk_threshold_floors(thresholds: &mut BTreeMap<String, f32>) {
    for (key, floor) in [
        ("cortex_outside", 0.05_f32),
        ("brain_surface", 0.05_f32),
        ("drilling_surface", 0.05_f32),
    ] {
        let current = thresholds.get(key).copied().unwrap_or(0.0);
        thresholds.insert(key.to_string(), current.max(floor));
    }
}

fn constraint_mode(map: &BTreeMap<String, Value>, key: &str, default: &str) -> String {
    let mode = map
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .trim()
        .to_ascii_lowercase();
    match mode.as_str() {
        "enforce" | "warn" | "off" => mode,
        _ => default.to_string(),
    }
}

fn env_bool(name: &str, default: bool) -> bool {
    env::var(name)
        .ok()
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(default)
}

fn f32_param(map: &BTreeMap<String, Value>, key: &str, default: f32) -> f32 {
    map.get(key)
        .and_then(Value::as_f64)
        .map(|v| v as f32)
        .unwrap_or(default)
}

fn f32_param_any(map: &BTreeMap<String, Value>, keys: &[&str], default: f32) -> f32 {
    for key in keys {
        let value = f32_param(map, key, f32::NAN);
        if value.is_finite() {
            return value;
        }
    }
    default
}

fn probe_radius_um(probe_params: &BTreeMap<String, Value>) -> f32 {
    let explicit = f32_param(probe_params, "probeRadiusUm", f32::NAN);
    if explicit.is_finite() {
        return explicit.max(0.0);
    }
    let radius = f32_param(probe_params, "radiusUm", f32::NAN);
    if radius.is_finite() {
        return radius.max(0.0);
    }
    (f32_param(probe_params, "shankWidthUm", 70.0) / 2.0).max(5.0)
}

fn probe_shank_count(probe_params: &BTreeMap<String, Value>) -> usize {
    let explicit = usize_param(probe_params, "shankCount", 0)
        .max(usize_param(probe_params, "shanks", 0))
        .max(usize_param(probe_params, "nShanks", 0));
    explicit.clamp(1, 16)
}

fn probe_shank_spacing_um(probe_params: &BTreeMap<String, Value>, shank_count: usize) -> f32 {
    f32_param_any(
        probe_params,
        &["shankSpacingUm", "shankPitchUm", "shankPitch"],
        if shank_count > 1 { 250.0 } else { 0.0 },
    )
    .max(0.0)
}

fn shank_offsets_um(shank_count: usize, spacing_um: f32) -> Vec<f32> {
    let count = shank_count.clamp(1, 16);
    let center = (count as f32 - 1.0) * 0.5;
    (0..count)
        .map(|idx| (idx as f32 - center) * spacing_um)
        .collect()
}

fn usize_param(map: &BTreeMap<String, Value>, key: &str, default: usize) -> usize {
    map.get(key)
        .and_then(Value::as_u64)
        .map(|v| v as usize)
        .unwrap_or(default)
}

fn string_param(map: &BTreeMap<String, Value>, key: &str, default: &str) -> String {
    map.get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

fn bool_param(map: &BTreeMap<String, Value>, key: &str, default: bool) -> bool {
    map.get(key).and_then(Value::as_bool).unwrap_or(default)
}

fn elapsed_ms(started: Instant) -> u64 {
    started.elapsed().as_micros().div_ceil(1000) as u64
}

fn round3(value: f32) -> f32 {
    (value * 1000.0).round() / 1000.0
}

fn round_array1(values: [f32; 3]) -> [f32; 3] {
    [
        (values[0] * 10.0).round() / 10.0,
        (values[1] * 10.0).round() / 10.0,
        (values[2] * 10.0).round() / 10.0,
    ]
}

fn round_array6(values: [f32; 3]) -> [f32; 3] {
    [
        (values[0] * 1_000_000.0).round() / 1_000_000.0,
        (values[1] * 1_000_000.0).round() / 1_000_000.0,
        (values[2] * 1_000_000.0).round() / 1_000_000.0,
    ]
}

fn rounded_distance_map(values: &BTreeMap<String, f32>) -> BTreeMap<String, f32> {
    values
        .iter()
        .map(|(key, value)| (key.clone(), round3(*value)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_optimizer_selects_covering_candidate() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([
                ("algorithm".to_string(), json!("greedy")),
                ("coverageRadius".to_string(), json!(600)),
                ("maxAngle".to_string(), json!(5)),
                ("depthUm".to_string(), json!(7000)),
                ("allowFallbackTargetCoverage".to_string(), json!(true)),
            ]),
            probe_params: BTreeMap::from([
                ("depthUm".to_string(), json!(7000)),
                ("count".to_string(), json!(1)),
            ]),
            constraint_modes: BTreeMap::new(),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(response.ok);
        assert!(response.candidate_count > 0);
        assert_eq!(response.uncovered.len(), 0);
        assert_eq!(response.selected.len(), 1);
        assert!(response.selected[0].get("entry_xyz").is_some());
        assert!(response.selected[0].get("tip_xyz").is_some());
        assert!(response.selected[0].get("dir").is_some());
    }

    #[test]
    fn strict_atlas_targets_default_blocks_unknown_targets() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["PFC".to_string()],
            params: BTreeMap::from([("algorithm".to_string(), json!("greedy"))]),
            probe_params: BTreeMap::new(),
            constraint_modes: BTreeMap::new(),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(!response.ok);
        assert_eq!(
            response.code.as_deref(),
            Some("strict_atlas_target_lookup_failed")
        );
    }

    #[test]
    fn native_drcc_misocp_fails_closed() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([
                ("algorithm".to_string(), json!("drcc")),
                ("drccSolver".to_string(), json!("misocp")),
                ("allowFallbackTargetCoverage".to_string(), json!(true)),
            ]),
            probe_params: BTreeMap::from([("count".to_string(), json!(1))]),
            constraint_modes: BTreeMap::new(),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(!response.ok);
        assert_eq!(
            response.code.as_deref(),
            Some("unsupported_native_drcc_solver")
        );
        let policy = response
            .compute_profile
            .get("solverRuntimePolicy")
            .and_then(Value::as_object)
            .expect("solver runtime policy");
        assert_eq!(
            policy.get("requestedDrccSolver").and_then(Value::as_str),
            Some("misocp")
        );
    }

    #[test]
    fn native_drcc_heuristic_reports_effective_solver() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([
                ("algorithm".to_string(), json!("drcc")),
                ("drccSolver".to_string(), json!("heuristic")),
                ("allowFallbackTargetCoverage".to_string(), json!(true)),
                ("coverageRadius".to_string(), json!(600)),
            ]),
            probe_params: BTreeMap::from([("count".to_string(), json!(1))]),
            constraint_modes: BTreeMap::new(),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(response.ok);
        let policy = response
            .compute_profile
            .get("solverRuntimePolicy")
            .and_then(Value::as_object)
            .expect("solver runtime policy");
        assert_eq!(
            policy.get("effectiveAlgorithm").and_then(Value::as_str),
            Some("drcc_heuristic_bitset")
        );
        assert_eq!(
            policy.get("effectiveDrccSolver").and_then(Value::as_str),
            Some("heuristic")
        );
    }

    #[test]
    fn brain_bounds_summary_rejects_tip_outside() {
        let summary =
            BrainBoundsSummary::from_inside_series(&[false, true, true, true, false, false], 100.0);
        assert!(summary.available);
        assert!(!summary.passed);
        assert_eq!(summary.reason, "tip_outside_brain");
    }

    #[test]
    fn brain_bounds_policy_does_not_apply_clearance_threshold_without_explicit_risk() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::new(),
            probe_params: BTreeMap::new(),
            constraint_modes: BTreeMap::from([("brainBounds".to_string(), json!("enforce"))]),
            clinical_context: None,
        };
        let policy = ConstraintPolicy::from_request(&request);
        let distances = BTreeMap::from([("vessel".to_string(), 0.10)]);
        assert_eq!(boundary_risk_rejection(&distances, &policy, false), None);
        assert_eq!(
            boundary_risk_rejection(&distances, &policy, true),
            Some("risk_vessel".to_string())
        );
    }

    #[test]
    fn boundary_risk_rejection_is_strict_at_near_threshold() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([(
                "riskThresholdsMm".to_string(),
                json!({
                    "skull": 0.45,
                    "brain_surface": 0.45,
                    "ventricle": 0.35,
                    "vessel": 0.60,
                    "sulcus": 0.40
                }),
            )]),
            probe_params: BTreeMap::new(),
            constraint_modes: BTreeMap::from([(
                "continuousRiskDistance".to_string(),
                json!("enforce"),
            )]),
            clinical_context: None,
        };
        let policy = ConstraintPolicy::from_request(&request);
        for (class_name, threshold) in [
            ("skull", 0.45_f32),
            ("brain_surface", 0.45_f32),
            ("ventricle", 0.35_f32),
            ("vessel", 0.60_f32),
            ("sulcus", 0.40_f32),
        ] {
            let below = BTreeMap::from([(class_name.to_string(), threshold - 0.001)]);
            let exactly = BTreeMap::from([(class_name.to_string(), threshold)]);
            let above = BTreeMap::from([(class_name.to_string(), threshold + 0.001)]);
            assert_eq!(
                boundary_risk_rejection(&below, &policy, true),
                Some(format!("risk_{class_name}"))
            );
            assert_eq!(boundary_risk_rejection(&exactly, &policy, true), None);
            assert_eq!(boundary_risk_rejection(&above, &policy, true), None);
        }
    }

    #[test]
    fn native_risk_thresholds_floor_boundary_surface_classes() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([(
                "riskThresholdsMm".to_string(),
                json!({
                    "cortex_outside": 0.0,
                    "brain_surface": 0.0,
                    "drilling_surface": 0.0,
                    "skull": 0.01
                }),
            )]),
            probe_params: BTreeMap::new(),
            constraint_modes: BTreeMap::from([(
                "continuousRiskDistance".to_string(),
                json!("enforce"),
            )]),
            clinical_context: None,
        };
        let policy = ConstraintPolicy::from_request(&request);
        assert_eq!(policy.risk_threshold_for("cortex_outside"), 0.05);
        assert_eq!(policy.risk_threshold_for("brain_surface"), 0.05);
        assert_eq!(policy.risk_threshold_for("drilling_surface"), 0.05);
        assert_eq!(policy.risk_threshold_for("skull"), 0.01);
    }

    #[test]
    fn native_continuous_risk_enforce_requires_risk_measurements() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([
                ("algorithm".to_string(), json!("greedy")),
                ("coverageRadius".to_string(), json!(600)),
                ("allowFallbackTargetCoverage".to_string(), json!(true)),
            ]),
            probe_params: BTreeMap::from([("count".to_string(), json!(1))]),
            constraint_modes: BTreeMap::from([(
                "continuousRiskDistance".to_string(),
                json!("enforce"),
            )]),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(response.ok);
        assert_eq!(response.candidate_count, 0);
        assert!(response
            .candidate_stage_breakdown
            .get("nativeRejectReasons")
            .and_then(Value::as_object)
            .and_then(|reasons| reasons.get("risk_model_unavailable"))
            .is_some());
    }

    #[test]
    fn native_optimizer_caps_depth_to_brain_bounds() {
        let request = OptimizeRequest {
            task_id: None,
            target_keys: vec!["CA1".to_string()],
            params: BTreeMap::from([
                ("algorithm".to_string(), json!("greedy")),
                ("coverageRadius".to_string(), json!(600)),
                ("maxAngle".to_string(), json!(5)),
                ("depthUm".to_string(), json!(20000)),
                ("allowFallbackTargetCoverage".to_string(), json!(true)),
            ]),
            probe_params: BTreeMap::from([
                ("depthUm".to_string(), json!(20000)),
                ("count".to_string(), json!(1)),
            ]),
            constraint_modes: BTreeMap::new(),
            clinical_context: None,
        };
        let response = PlannerCore::new(PlannerEngineConfig::default()).optimize(request);
        assert!(response.ok);
        assert_eq!(response.selected.len(), 1);
        let tip = response.selected[0]
            .get("tip_xyz")
            .and_then(Value::as_array)
            .expect("tip_xyz array");
        let tip_y_um = tip[1].as_f64().expect("tip y") as f32;
        let tip_dv_mm = tip_y_um / 1000.0 - BREGMA_UM[1] / 1000.0;
        assert!(tip_dv_mm <= MAX_DV_MM + 0.05);
    }
}
