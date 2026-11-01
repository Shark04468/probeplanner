use axum::{
    extract::{Path, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use planner_engine::{
    api::{
        engine_version, CancelTaskResponse, HealthResponse, OptimizeRequest, OptimizeResponse,
        SubmitTaskResponse, TaskStatusResponse,
    },
    optimizer::{PlannerCore, PlannerEngineConfig},
    task::{TaskRecord, TaskStore},
};
use serde_json::json;
use std::{
    any::Any,
    collections::BTreeMap,
    net::SocketAddr,
    panic::{catch_unwind, AssertUnwindSafe},
    sync::Arc,
    time::Duration,
};
use uuid::Uuid;

#[derive(Clone)]
struct EngineState {
    core: Arc<PlannerCore>,
    tasks: TaskStore,
}

fn panic_payload_message(payload: Box<dyn Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<&str>() {
        (*message).to_string()
    } else if let Some(message) = payload.downcast_ref::<String>() {
        message.clone()
    } else {
        "unknown panic payload".to_string()
    }
}

fn panic_response(message: String) -> OptimizeResponse {
    let mut compute_profile = BTreeMap::new();
    compute_profile.insert("panic".to_string(), json!(true));
    OptimizeResponse {
        ok: false,
        code: Some("engine_panic".to_string()),
        error: Some(message),
        engine_version: engine_version().to_string(),
        engine_status: "engine_panic".to_string(),
        algorithm: "unknown".to_string(),
        candidate_count: 0,
        candidate_universe_count: 0,
        selected: Vec::new(),
        uncovered: Vec::new(),
        required_coverage_units: 0,
        covered_coverage_units: 0,
        time_ms: 0,
        compute_profile,
        candidate_stage_breakdown: BTreeMap::new(),
    }
}

async fn health(State(state): State<Arc<EngineState>>) -> Json<HealthResponse> {
    let (atlas_loaded, sdf_loaded, region_index_loaded, risk_sdf_readiness, load_error) =
        state.core.health_flags();
    let ok = atlas_loaded && sdf_loaded && region_index_loaded && risk_sdf_readiness.ready;
    Json(HealthResponse {
        ok,
        engine_version: engine_version().to_string(),
        atlas_loaded,
        sdf_loaded,
        risk_sdf_ready: risk_sdf_readiness.ready,
        risk_sdf_loaded_classes: risk_sdf_readiness.loaded_classes,
        risk_sdf_missing_classes: risk_sdf_readiness.missing_classes,
        region_index_loaded,
        task_count: state.tasks.len(),
        load_error,
    })
}

async fn optimize(
    State(state): State<Arc<EngineState>>,
    Json(request): Json<OptimizeRequest>,
) -> Json<planner_engine::api::OptimizeResponse> {
    Json(
        match catch_unwind(AssertUnwindSafe(|| state.core.optimize(request))) {
            Ok(response) => response,
            Err(payload) => panic_response(format!(
                "Native engine optimize panic: {}",
                panic_payload_message(payload),
            )),
        },
    )
}

async fn submit_task(
    State(state): State<Arc<EngineState>>,
    Json(request): Json<OptimizeRequest>,
) -> Json<SubmitTaskResponse> {
    let task_id = request
        .task_id
        .clone()
        .unwrap_or_else(|| format!("engine_{}", Uuid::new_v4().simple()));
    state.tasks.insert(TaskRecord::queued(task_id.clone()));
    let tasks = state.tasks.clone();
    let core = state.core.clone();
    let task_id_for_worker = task_id.clone();
    tokio::spawn(async move {
        let preflight_stages = [
            ("payload_normalized", 0.10, "Request payload normalized"),
            ("atlas_ready", 0.20, "Resident atlas/index checked"),
            (
                "candidate_generating",
                0.35,
                "Generating candidates in native engine",
            ),
        ];
        for (stage, progress, message) in preflight_stages {
            if tasks.is_cancelled(&task_id_for_worker) {
                return;
            }
            tasks.update_progress(&task_id_for_worker, stage, progress, message);
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        if tasks.is_cancelled(&task_id_for_worker) {
            return;
        }
        let result = match catch_unwind(AssertUnwindSafe(|| {
            core.optimize_with_cancel(request, || tasks.is_cancelled(&task_id_for_worker))
        })) {
            Ok(result) => result,
            Err(payload) => {
                tasks.fail(
                    &task_id_for_worker,
                    format!(
                        "Native engine worker panic: {}",
                        panic_payload_message(payload),
                    ),
                );
                return;
            }
        };
        if tasks.is_cancelled(&task_id_for_worker) || result.code.as_deref() == Some("cancelled") {
            return;
        }
        let post_stages = [
            ("candidate_generated", 0.46, "Native candidates generated"),
            ("candidate_scoring", 0.55, "Scoring native candidates"),
            ("candidate_scored", 0.64, "Native candidates scored"),
            ("pareto_ranked", 0.70, "Native ranking completed"),
            ("solver_completed", 0.78, "Bitset solver completed"),
            ("payload_finalized", 0.92, "Finalizing native payload"),
        ];
        for (stage, progress, message) in post_stages {
            if tasks.is_cancelled(&task_id_for_worker) {
                return;
            }
            tasks.update_progress(&task_id_for_worker, stage, progress, message);
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        tasks.finish(&task_id_for_worker, result);
    });
    Json(SubmitTaskResponse {
        ok: true,
        task_id: task_id.clone(),
        status: "queued".to_string(),
        status_url: format!("/v1/tasks/{task_id}"),
    })
}

async fn task_status(
    State(state): State<Arc<EngineState>>,
    Path(task_id): Path<String>,
) -> Result<Json<TaskStatusResponse>, StatusCode> {
    state
        .tasks
        .get(&task_id)
        .map(|record| Json(record.response()))
        .ok_or(StatusCode::NOT_FOUND)
}

async fn cancel_task(
    State(state): State<Arc<EngineState>>,
    Path(task_id): Path<String>,
) -> Json<CancelTaskResponse> {
    let cancelled = state.tasks.cancel(&task_id);
    Json(CancelTaskResponse {
        ok: true,
        task_id,
        cancelled,
    })
}

#[tokio::main]
async fn main() {
    if let Err(err) = run().await {
        eprintln!("planner-engine fatal: {err}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), String> {
    let host = std::env::var("PROBEPLANNER_ENGINE_HOST")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "127.0.0.1".to_string());
    let port = std::env::var("PROBEPLANNER_ENGINE_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(7878);
    let state = Arc::new(EngineState {
        core: Arc::new(PlannerCore::new(PlannerEngineConfig::from_env())),
        tasks: TaskStore::default(),
    });
    let app = Router::new()
        .route("/healthz", get(health))
        .route("/v1/optimize", post(optimize))
        .route("/v1/tasks", post(submit_task))
        .route("/v1/tasks/:task_id", get(task_status))
        .route("/v1/tasks/:task_id/cancel", post(cancel_task))
        .with_state(state);
    let addr_text = format!("{host}:{port}");
    let addr: SocketAddr = addr_text
        .parse()
        .map_err(|err| format!("invalid bind address {addr_text}: {err}"))?;
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .map_err(|err| format!("failed to bind planner engine at {addr}: {err}"))?;
    println!("planner-engine listening on http://{addr}");
    axum::serve(listener, app)
        .await
        .map_err(|err| format!("planner engine server failed: {err}"))?;
    Ok(())
}
