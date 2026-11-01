#![recursion_limit = "256"]

pub mod api;
pub mod atlas;
pub mod bitset;
pub mod geometry;
pub mod optimizer;
pub mod profile;
pub mod risk;
pub mod sdf;
pub mod solver;
pub mod task;

pub use bitset::BitSet;
pub use geometry::{sample_trajectory_voxels, TrajectorySpec, VoxelCoord, VoxelGridSpec};
pub use optimizer::{PlannerCore, PlannerEngineConfig};
pub use profile::{compute_trajectory_profile, RegionMaskIndex, TrajectoryProfile};
pub use risk::{risk_distance_diagnostics, RiskDistanceDiagnostics};
pub use sdf::{SdfGrid, SdfSample};
pub use solver::{
    build_conflict_matrix, build_conflict_matrix_with_cancel, exact_branch_and_bound,
    exact_branch_and_bound_with_cancel, greedy_select, greedy_select_with_cancel, CandidateKernel,
    CandidateStore, ConflictPolicy, SolveCancelled,
};

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error("invalid grid dimensions")]
    InvalidGrid,
    #[error("input length mismatch")]
    LengthMismatch,
    #[error("index out of bounds")]
    OutOfBounds,
}
