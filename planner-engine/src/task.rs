use crate::api::{OptimizeResponse, TaskStatusResponse};
use std::{
    collections::HashMap,
    sync::{Arc, RwLock},
    time::{Duration, Instant},
};

const DEFAULT_MAX_TASK_RECORDS: usize = 256;
const DEFAULT_TASK_TTL_SECS: u64 = 3600;

#[derive(Clone, Debug)]
pub struct TaskRecord {
    pub task_id: String,
    pub status: String,
    pub stage: String,
    pub progress: f32,
    pub message: String,
    pub cancelled: bool,
    pub result: Option<OptimizeResponse>,
    pub error: Option<String>,
    updated_at: Instant,
}

impl TaskRecord {
    pub fn queued(task_id: String) -> Self {
        Self {
            task_id,
            status: "queued".to_string(),
            stage: "queued".to_string(),
            progress: 0.0,
            message: "Queued".to_string(),
            cancelled: false,
            result: None,
            error: None,
            updated_at: Instant::now(),
        }
    }

    pub fn response(&self) -> TaskStatusResponse {
        TaskStatusResponse {
            ok: true,
            task_id: self.task_id.clone(),
            status: self.status.clone(),
            stage: self.stage.clone(),
            progress: self.progress,
            message: self.message.clone(),
            cancelled: self.cancelled,
            result: self.result.clone(),
            error: self.error.clone(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct TaskStore {
    inner: Arc<RwLock<HashMap<String, TaskRecord>>>,
    max_records: usize,
    ttl: Duration,
}

impl Default for TaskStore {
    fn default() -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            max_records: DEFAULT_MAX_TASK_RECORDS,
            ttl: Duration::from_secs(DEFAULT_TASK_TTL_SECS),
        }
    }
}

impl TaskStore {
    pub fn insert(&self, record: TaskRecord) {
        if let Ok(mut guard) = self.inner.write() {
            Self::prune_locked(&mut guard, self.max_records, self.ttl);
            guard.insert(record.task_id.clone(), record);
            Self::prune_locked(&mut guard, self.max_records, self.ttl);
        }
    }

    pub fn update_progress(&self, task_id: &str, stage: &str, progress: f32, message: &str) {
        if let Ok(mut guard) = self.inner.write() {
            if let Some(record) = guard.get_mut(task_id) {
                if record.cancelled {
                    return;
                }
                record.status = "running".to_string();
                record.stage = stage.to_string();
                record.progress = progress.clamp(0.0, 1.0);
                record.message = message.to_string();
                record.updated_at = Instant::now();
            }
        }
    }

    pub fn finish(&self, task_id: &str, result: OptimizeResponse) {
        if let Ok(mut guard) = self.inner.write() {
            if let Some(record) = guard.get_mut(task_id) {
                if record.cancelled {
                    return;
                }
                record.status = "completed".to_string();
                record.stage = "completed".to_string();
                record.progress = 1.0;
                record.message = "Optimization completed".to_string();
                record.result = Some(result);
                record.updated_at = Instant::now();
            }
            Self::prune_locked(&mut guard, self.max_records, self.ttl);
        }
    }

    pub fn fail(&self, task_id: &str, error: String) {
        if let Ok(mut guard) = self.inner.write() {
            if let Some(record) = guard.get_mut(task_id) {
                record.status = "failed".to_string();
                record.stage = "failed".to_string();
                record.progress = 1.0;
                record.message = error.clone();
                record.error = Some(error);
                record.updated_at = Instant::now();
            }
            Self::prune_locked(&mut guard, self.max_records, self.ttl);
        }
    }

    pub fn cancel(&self, task_id: &str) -> bool {
        if let Ok(mut guard) = self.inner.write() {
            if let Some(record) = guard.get_mut(task_id) {
                record.cancelled = true;
                record.status = "cancelled".to_string();
                record.stage = "cancelled".to_string();
                record.progress = 1.0;
                record.message = "Task cancelled".to_string();
                record.updated_at = Instant::now();
                return true;
            }
        }
        false
    }

    pub fn is_cancelled(&self, task_id: &str) -> bool {
        self.get(task_id).map(|r| r.cancelled).unwrap_or(false)
    }

    pub fn get(&self, task_id: &str) -> Option<TaskRecord> {
        self.inner.read().ok()?.get(task_id).cloned()
    }

    pub fn len(&self) -> usize {
        self.inner.read().map(|guard| guard.len()).unwrap_or(0)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    fn prune_locked(guard: &mut HashMap<String, TaskRecord>, max_records: usize, ttl: Duration) {
        let now = Instant::now();
        guard.retain(|_, record| {
            !matches!(record.status.as_str(), "completed" | "failed" | "cancelled")
                || now.duration_since(record.updated_at) <= ttl
        });
        if guard.len() <= max_records {
            return;
        }
        let mut records = guard
            .iter()
            .map(|(task_id, record)| {
                (
                    task_id.clone(),
                    record.updated_at,
                    matches!(record.status.as_str(), "completed" | "failed" | "cancelled"),
                )
            })
            .collect::<Vec<_>>();
        records.sort_by_key(|(_, updated_at, terminal)| (!*terminal, *updated_at));
        for (task_id, _, _) in records.into_iter().take(guard.len() - max_records) {
            guard.remove(&task_id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::sync::{Arc, RwLock};

    fn empty_response() -> OptimizeResponse {
        OptimizeResponse {
            ok: true,
            code: None,
            error: None,
            engine_version: "test".to_string(),
            engine_status: "ok".to_string(),
            algorithm: "greedy".to_string(),
            candidate_count: 0,
            candidate_universe_count: 0,
            selected: Vec::new(),
            uncovered: Vec::new(),
            required_coverage_units: 0,
            covered_coverage_units: 0,
            time_ms: 0,
            compute_profile: BTreeMap::new(),
            candidate_stage_breakdown: BTreeMap::new(),
        }
    }

    #[test]
    fn task_store_prunes_completed_records_to_cap() {
        let store = TaskStore {
            inner: Arc::new(RwLock::new(HashMap::new())),
            max_records: 2,
            ttl: Duration::from_secs(3600),
        };
        for idx in 0..3 {
            let id = format!("task_{idx}");
            store.insert(TaskRecord::queued(id.clone()));
            store.finish(&id, empty_response());
        }
        assert_eq!(store.len(), 2);
        assert!(store.get("task_0").is_none());
        assert!(store.get("task_1").is_some());
        assert!(store.get("task_2").is_some());
    }
}
