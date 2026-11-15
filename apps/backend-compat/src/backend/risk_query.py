"""Bound exact surface-query parallelism without changing neighbor semantics."""
import os
from pathlib import Path
import threading


MAX_QUERY_WORKERS = 8


def available_cpus():
    limits = [max(1, os.cpu_count() or 1)]
    if hasattr(os, "sched_getaffinity"):
        try:
            limits.append(max(1, len(os.sched_getaffinity(0))))
        except OSError:
            pass
    # Container quotas can be lower than both host CPU count and affinity.
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            limits.append(max(1, int(quota) // int(period)))
    except FileNotFoundError:
        try:
            root = Path("/sys/fs/cgroup/cpu")
            quota = int((root / "cpu.cfs_quota_us").read_text())
            period = int((root / "cpu.cfs_period_us").read_text())
            if quota > 0:
                limits.append(max(1, quota // period))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, ZeroDivisionError):
            limits.append(1)
    except (OSError, ValueError, ZeroDivisionError):
        limits.append(1)
    return min(limits)


class ExactRiskQueryPolicy:
    def __init__(self, requested=1, *, runtime_budget=1):
        if isinstance(requested, str) and requested.strip().isdigit():
            requested = int(requested.strip())
        if type(requested) is not int or not 1 <= requested <= MAX_QUERY_WORKERS:
            raise ValueError("Risk query workers must be an integer between 1 and 8")
        self.requested = requested
        self.cpu_limit = available_cpus()
        self.runtime_budget = max(1, int(runtime_budget))
        self.workers = min(requested, self.cpu_limit, self.runtime_budget)
        self._parallel_slot = threading.BoundedSemaphore(1)

    def configuration(self):
        return {
            "requestedWorkers": self.requested,
            "effectiveWorkers": self.workers,
            "cpuLimit": self.cpu_limit,
            "runtimeThreadLimit": self.runtime_budget,
            "maximumWorkers": MAX_QUERY_WORKERS,
            "parallelQuerySlots": 1 if self.workers > 1 else None,
            "exact": True, "k": 1, "p": 2, "eps": 0,
        }

    def query(self, tree, points):
        if self.workers == 1:
            return tree.query(points, k=1)
        # One process-wide policy bounds nested and concurrent risk queries.
        with self._parallel_slot:
            return tree.query(points, k=1, eps=0, p=2, workers=self.workers)
