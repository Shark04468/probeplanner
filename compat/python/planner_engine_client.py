"""Small Flask-side client for the future native planner-engine service.

This module is intentionally independent from the current probe-planner codebase.
It preserves the current camelCase `/api/optimize` payload shape and can be
copied into the Flask backend once the native service is ready to proxy real
compute.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import time

import requests


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class PlannerEngineClient:
    base_url: str = "http://127.0.0.1:7878"
    timeout_s: float = 900.0
    poll_interval_s: float = 0.25

    def health(self) -> JsonDict:
        response = requests.get(f"{self.base_url.rstrip('/')}/healthz", timeout=5)
        response.raise_for_status()
        return response.json()

    def optimize_sync(self, payload: JsonDict) -> JsonDict:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/v1/optimize",
            json=payload,
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return self._normalize_engine_response(response.json(), payload)

    def submit(self, payload: JsonDict) -> str:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/v1/tasks",
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        return str(body["taskId"])

    def status(self, task_id: str) -> JsonDict:
        response = requests.get(
            f"{self.base_url.rstrip('/')}/v1/tasks/{task_id}",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def cancel(self, task_id: str) -> JsonDict:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/v1/tasks/{task_id}/cancel",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def optimize_task(self, payload: JsonDict) -> JsonDict:
        task_id = self.submit(payload)
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            status = self.status(task_id)
            state = str(status.get("status", "")).lower()
            if state == "completed":
                result = status.get("result") or {}
                if not isinstance(result, dict):
                    raise RuntimeError("planner-engine returned non-object result")
                return self._normalize_engine_response(result, payload)
            if state in {"failed", "cancelled"}:
                raise RuntimeError(str(status.get("error") or status.get("message") or state))
            time.sleep(self.poll_interval_s)
        self.cancel(task_id)
        raise TimeoutError(f"planner-engine task timed out after {self.timeout_s}s")

    @staticmethod
    def _normalize_engine_response(engine_body: JsonDict, original_payload: Optional[JsonDict] = None) -> JsonDict:
        """Map native engine fields onto the current Flask response envelope."""

        body = dict(engine_body)
        body.setdefault("ok", True)
        body.setdefault("selected", [])
        body.setdefault("uncovered", list((original_payload or {}).get("targetKeys") or []))
        body.setdefault("candidateCount", body.pop("candidate_count", 0) if "candidate_count" in body else 0)
        body.setdefault(
            "candidateUniverseCount",
            body.pop("candidate_universe_count", 0) if "candidate_universe_count" in body else 0,
        )
        body.setdefault("timeMs", body.pop("time_ms", 0) if "time_ms" in body else 0)
        body.setdefault("computeProfile", body.pop("compute_profile", {}) if "compute_profile" in body else {})
        body.setdefault("engineVersion", body.pop("engine_version", None) if "engine_version" in body else None)
        return body
