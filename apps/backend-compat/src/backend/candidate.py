"""Candidate-layer helpers for optimizer pipeline."""

from __future__ import annotations

from typing import Callable, Dict, Iterable


def checkpoint_cancel(cancelled_cb: Callable[[], bool], stage: str) -> None:
    if cancelled_cb and cancelled_cb():
        raise RuntimeError(f"Task cancelled at stage: {stage}")


def summarize_candidate_pool(candidates: Iterable[Dict[str, object]]) -> Dict[str, int]:
    pool = list(candidates or [])
    return {
        "candidateCount": len(pool),
        "withParetoRank": sum(1 for c in pool if "paretoRank" in c),
        "withRobustScore": sum(1 for c in pool if c.get("robustScore") is not None),
    }
