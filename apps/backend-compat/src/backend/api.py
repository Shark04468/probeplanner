"""API-layer helpers: task cancellation and structured logs."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Dict

TASK_STATE_QUEUED = "queued"
TASK_STATE_RUNNING = "running"
TASK_STATE_PARTIAL = "partial"
TASK_STATE_FAILED = "failed"
TASK_STATE_COMPLETED = "completed"

TASK_STATE_ORDER = (
    TASK_STATE_QUEUED,
    TASK_STATE_RUNNING,
    TASK_STATE_PARTIAL,
    TASK_STATE_FAILED,
    TASK_STATE_COMPLETED,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_task_status(raw: object, default: str = TASK_STATE_QUEUED) -> str:
    value = str(raw or "").strip().lower()
    if value in TASK_STATE_ORDER:
        return value
    if value in ("pending", "created", "received", "init", "initializing", "queued"):
        return TASK_STATE_QUEUED
    if value in ("working", "building", "processing", "loading", "running"):
        return TASK_STATE_RUNNING
    if value in ("degraded", "warn", "warning", "partial", "limited"):
        return TASK_STATE_PARTIAL
    if value in ("cancelled", "canceled", "error", "failed"):
        return TASK_STATE_FAILED
    if value in ("done", "ready", "ok", "success", "completed"):
        return TASK_STATE_COMPLETED
    return normalize_task_status(default, TASK_STATE_QUEUED) if value else TASK_STATE_QUEUED


def structured_log(logger, event: str, **fields) -> None:
    payload = {"ts": utc_now_iso(), "event": event, **fields}
    logger.info("[struct] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


class TaskRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = set()
        self._state: Dict[str, dict] = {}
        self._max_tasks = 256

    def _prune_locked(self) -> None:
        if len(self._state) <= self._max_tasks:
            return
        stale = sorted(
            self._state.items(),
            key=lambda kv: str(kv[1].get("updatedAt") or kv[1].get("startedAt") or ""),
        )
        remove_n = max(1, len(stale) - self._max_tasks)
        for key, _ in stale[:remove_n]:
            self._state.pop(key, None)
            self._cancelled.discard(key)

    def begin(self, task_id: str, stage: str = "queued", progress: float = 0.0, message: str = "") -> None:
        if not task_id:
            return
        task_id = str(task_id)
        now = utc_now_iso()
        with self._lock:
            self._cancelled.discard(task_id)
            self._state[task_id] = {
                "taskId": task_id,
                "status": TASK_STATE_QUEUED,
                "stage": str(stage or "queued"),
                "progress": float(max(0.0, min(1.0, progress))),
                "message": str(message or ""),
                "details": [],
                "result": None,
                "startedAt": now,
                "updatedAt": now,
                "finishedAt": None,
                "cancelled": False,
            }
            self._prune_locked()

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        details: list | tuple | None = None,
        result: dict | None = None,
    ) -> None:
        if not task_id:
            return
        task_id = str(task_id)
        now = utc_now_iso()
        with self._lock:
            if task_id not in self._state:
                self._state[task_id] = {
                    "taskId": task_id,
                    "status": TASK_STATE_QUEUED,
                    "stage": "queued",
                    "progress": 0.0,
                    "message": "",
                    "details": [],
                    "result": None,
                    "startedAt": now,
                    "updatedAt": now,
                    "finishedAt": None,
                    "cancelled": task_id in self._cancelled,
                }
            row = self._state[task_id]
            if status is not None:
                row["status"] = normalize_task_status(status, row.get("status", TASK_STATE_QUEUED))
            if stage is not None:
                row["stage"] = str(stage)
            if progress is not None:
                row["progress"] = float(max(0.0, min(1.0, progress)))
            if message is not None:
                row["message"] = str(message)
            if details is not None:
                if isinstance(details, (list, tuple)):
                    row["details"] = [str(x) for x in details if str(x).strip()]
                else:
                    row["details"] = [str(details)]
            if result is not None:
                row["result"] = result if isinstance(result, dict) else {"value": result}
            row["updatedAt"] = now
            row["cancelled"] = task_id in self._cancelled
            self._prune_locked()

    def finish(
        self,
        task_id: str,
        *,
        status: str = TASK_STATE_COMPLETED,
        stage: str = "done",
        progress: float = 1.0,
        message: str = "",
        details: list | tuple | None = None,
        result: dict | None = None,
    ) -> None:
        if not task_id:
            return
        now = utc_now_iso()
        self.update(
            task_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            details=details,
            result=result,
        )
        with self._lock:
            row = self._state.get(str(task_id))
            if row is not None:
                row["finishedAt"] = now
                row["updatedAt"] = now

    def cancel(self, task_id: str) -> None:
        if not task_id:
            return
        task_id = str(task_id)
        with self._lock:
            self._cancelled.add(task_id)
            row = self._state.get(task_id)
            if row is not None:
                row["cancelled"] = True
                row["status"] = TASK_STATE_FAILED
                row["stage"] = row.get("stage") or "cancelled"
                row["updatedAt"] = utc_now_iso()
                row["finishedAt"] = row.get("finishedAt") or utc_now_iso()

    def is_cancelled(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            return str(task_id) in self._cancelled

    def get(self, task_id: str) -> dict:
        if not task_id:
            return {
                "taskId": "",
                "status": TASK_STATE_QUEUED,
                "stage": "unknown",
                "progress": 0.0,
                "message": "",
                "details": [],
                "result": None,
                "startedAt": None,
                "updatedAt": utc_now_iso(),
                "finishedAt": None,
                "cancelled": False,
            }
        with self._lock:
            task_id = str(task_id)
            row = self._state.get(task_id)
            if row is None:
                return {
                    "taskId": task_id,
                    "status": TASK_STATE_QUEUED,
                    "stage": "unknown",
                    "progress": 0.0,
                    "message": "",
                    "details": [],
                    "result": None,
                    "startedAt": None,
                    "updatedAt": utc_now_iso(),
                    "finishedAt": None,
                    "cancelled": task_id in self._cancelled,
                }
            out = dict(row)
            out["status"] = normalize_task_status(out.get("status"), TASK_STATE_QUEUED)
            if not isinstance(out.get("details"), list):
                out["details"] = []
            if not isinstance(out.get("result"), dict):
                out["result"] = None
            return out

    def clear(self, task_id: str) -> None:
        if not task_id:
            return
        with self._lock:
            task_id = str(task_id)
            self._cancelled.discard(task_id)
            self._state.pop(task_id, None)
