use crate::api::{OptimizeResponse, TaskStatusResponse};
use std::{
    collections::HashMap,
    sync::{Arc, RwLock},
};

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

#[derive(Clone, Debug, Default)]
pub struct TaskStore {
    inner: Arc<RwLock<HashMap<String, TaskRecord>>>,
}

impl TaskStore {
    pub fn insert(&self, record: TaskRecord) {
        if let Ok(mut guard) = self.inner.write() {
            guard.insert(record.task_id.clone(), record);
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
            }
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
            }
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
}
