"""Example adapter for replacing the body of Flask `/api/optimize` later.

Do not import this file from the existing app yet. It documents the exact bridge
shape for phase 2, after phase 1 native kernels have passed the perf gate.
"""

from __future__ import annotations

from typing import Any, Dict

from planner_engine_client import PlannerEngineClient


def optimize_via_engine(request_body: Dict[str, Any]) -> Dict[str, Any]:
    client = PlannerEngineClient()
    health = client.health()
    if not health.get("ok"):
        raise RuntimeError("planner-engine is not healthy")
    return client.optimize_task(request_body)
