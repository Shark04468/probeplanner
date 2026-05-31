use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OptimizeRequest {
    pub task_id: Option<String>,
    pub target_keys: Vec<String>,
    pub params: BTreeMap<String, Value>,
    pub probe_params: BTreeMap<String, Value>,
    pub constraint_modes: BTreeMap<String, Value>,
    pub clinical_context: Option<BTreeMap<String, Value>>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OptimizeResponse {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub engine_version: String,
    pub engine_status: String,
    pub algorithm: String,
    pub candidate_count: usize,
    pub candidate_universe_count: usize,
    pub selected: Vec<Value>,
    pub uncovered: Vec<String>,
    pub required_coverage_units: usize,
    pub covered_coverage_units: usize,
    pub time_ms: u64,
    pub compute_profile: BTreeMap<String, Value>,
    pub candidate_stage_breakdown: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitTaskResponse {
    pub ok: bool,
    pub task_id: String,
    pub status: String,
    pub status_url: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStatusResponse {
    pub ok: bool,
    pub task_id: String,
    pub status: String,
    pub stage: String,
    pub progress: f32,
    pub message: String,
    pub cancelled: bool,
    pub result: Option<OptimizeResponse>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelTaskResponse {
    pub ok: bool,
    pub task_id: String,
    pub cancelled: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HealthResponse {
    pub ok: bool,
    pub engine_version: String,
    pub atlas_loaded: bool,
    pub sdf_loaded: bool,
    pub region_index_loaded: bool,
    pub task_count: usize,
}

pub fn engine_version() -> &'static str {
    "planner-engine-rust-0.1.0"
}
