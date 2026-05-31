use axum::{
    extract::{Path, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use planner_engine::{
    api::{
        engine_version, CancelTaskResponse, HealthResponse, OptimizeRequest, SubmitTaskResponse,
        TaskStatusResponse,
    },
    optimizer::{PlannerCore, PlannerEngineConfig},
    task::{TaskRecord, TaskStore},
};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use uuid::Uuid;

#[derive(Clone)]
struct EngineState {
    core: Arc<PlannerCore>,
    tasks: TaskStore,
}

async fn health(State(state): State<Arc<EngineState>>) -> Json<HealthResponse> {
    let (atlas_loaded, sdf_loaded, region_index_loaded) = state.core.health_flags();
    Json(HealthResponse {
        ok: true,
        engine_version: engine_version().to_string(),
        atlas_loaded,
        sdf_loaded,
        region_index_loaded,
        task_count: state.tasks.len(),
    })
}

async fn optimize(
    State(_state): State<Arc<EngineState>>,
    Json(request): Json<OptimizeRequest>,
) -> Json<planner_engine::api::OptimizeResponse> {
    Json(_state.core.optimize(request))
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
        let result = core.optimize(request);
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
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .expect("valid bind address");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("bind planner engine");
    println!("planner-engine listening on http://{addr}");
    axum::serve(listener, app)
        .await
        .expect("planner engine server");
}
