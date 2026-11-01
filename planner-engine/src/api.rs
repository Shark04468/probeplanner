use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

pub fn default_constraint_modes() -> BTreeMap<String, Value> {
    BTreeMap::from([
        ("maxAngle".to_string(), Value::String("enforce".to_string())),
        (
            "minEntrySpacing".to_string(),
            Value::String("enforce".to_string()),
        ),
        (
            "minClearance".to_string(),
            Value::String("enforce".to_string()),
        ),
        (
            "lambdaBoundary".to_string(),
            Value::String("enforce".to_string()),
        ),
        (
            "brainBounds".to_string(),
            Value::String("enforce".to_string()),
        ),
        (
            "continuousRiskDistance".to_string(),
            Value::String("enforce".to_string()),
        ),
        ("hemisphere".to_string(), Value::String("warn".to_string())),
    ])
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
#[serde(deny_unknown_fields)]
pub struct OptimizeRequest {
    pub task_id: Option<String>,
    pub target_keys: Vec<String>,
    pub params: BTreeMap<String, Value>,
    pub probe_params: BTreeMap<String, Value>,
    #[serde(default = "default_constraint_modes")]
    pub constraint_modes: BTreeMap<String, Value>,
    #[serde(default)]
    pub correction_factors: Option<BTreeMap<String, Value>>,
    #[serde(default)]
    pub correction_uncertainty: Option<BTreeMap<String, Value>>,
    #[serde(default)]
    pub correction_applied: Option<bool>,
    #[serde(default)]
    pub planner_calibration: Option<BTreeMap<String, Value>>,
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
    pub risk_sdf_ready: bool,
    pub risk_sdf_loaded_classes: Vec<String>,
    pub risk_sdf_missing_classes: Vec<String>,
    pub region_index_loaded: bool,
    pub task_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub load_error: Option<String>,
}

pub fn engine_version() -> &'static str {
    "planner-engine-rust-0.1.0"
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn optimize_request_rejects_unknown_fields() {
        let err = serde_json::from_value::<OptimizeRequest>(json!({
            "targetKeys": ["CA1"],
            "params": {},
            "probeParams": {},
            "constraintModes": {},
            "unsupportedField": true
        }))
        .expect_err("unknown fields must fail closed");
        assert!(err.to_string().contains("unsupportedField"));
    }

    #[test]
    fn optimize_request_defaults_optional_constraint_modes() {
        let request = serde_json::from_value::<OptimizeRequest>(json!({
            "targetKeys": ["CA1"],
            "params": {},
            "probeParams": {}
        }))
        .expect("constraintModes should default for direct engine clients");
        assert_eq!(
            request
                .constraint_modes
                .get("continuousRiskDistance")
                .and_then(Value::as_str),
            Some("enforce")
        );
    }

    #[test]
    fn optimize_request_accepts_known_flask_contract_fields() {
        let request = serde_json::from_value::<OptimizeRequest>(json!({
            "taskId": "task-1",
            "targetKeys": ["CA1"],
            "params": {},
            "probeParams": {},
            "constraintModes": {},
            "correctionFactors": {"apScale": 1.0},
            "correctionApplied": true,
            "correctionUncertainty": {"apMm": 0.1},
            "plannerCalibration": {"profile": "test"},
            "clinicalContext": {"studyArm": "A"}
        }))
        .expect("known compat fields should deserialize");
        assert_eq!(request.task_id.as_deref(), Some("task-1"));
        assert!(request.correction_applied.unwrap_or(false));
    }
}
