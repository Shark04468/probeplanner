import concurrent.futures
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pytest
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend import risk_query


@pytest.fixture
def eight_cpus(monkeypatch):
    monkeypatch.setattr(risk_query, "available_cpus", lambda: 8)


@pytest.mark.parametrize("value", [0, -1, 9, True, 4.0, "all", "-1", "2.5", None])
def test_invalid_workers_fail_instead_of_using_all_cpus(value, eight_cpus):
    with pytest.raises(ValueError, match="integer between 1 and 8"):
        risk_query.ExactRiskQueryPolicy(value, runtime_budget=16)


def test_requested_workers_are_capped_and_reported(monkeypatch):
    monkeypatch.setattr(risk_query, "available_cpus", lambda: 3)
    policy = risk_query.ExactRiskQueryPolicy("8", runtime_budget=2)
    config = policy.configuration()
    assert config["requestedWorkers"] == 8 and config["effectiveWorkers"] == 2
    assert config["cpuLimit"] == 3 and config["runtimeThreadLimit"] == 2
    assert config["parallelQuerySlots"] == 1 and config["exact"]


def test_default_query_preserves_legacy_signature(eight_cpus):
    class LegacyTree:
        def query(self, points, k):
            assert k == 1
            return points, 7
    policy = risk_query.ExactRiskQueryPolicy(runtime_budget=8)
    assert policy.query(LegacyTree(), "points") == ("points", 7)
    assert policy.configuration()["parallelQuerySlots"] is None


@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_parallel_results_and_tie_indices_are_byte_exact(workers, eight_cpus):
    rng = np.random.default_rng(125)
    cloud = np.vstack([rng.normal(size=(2000, 3)), np.zeros((4, 3))])
    tree = cKDTree(cloud)
    points = np.vstack([rng.normal(size=(500, 3)), np.zeros((2, 3)), cloud[:3]])
    expected = tree.query(points, k=1)
    observed = risk_query.ExactRiskQueryPolicy(workers, runtime_budget=8).query(tree, points)
    for before, after in zip(expected, observed):
        assert before.dtype == after.dtype and before.shape == after.shape
        assert before.tobytes() == after.tobytes()


def test_parallel_calls_share_a_bounded_slot(eight_cpus):
    policy = risk_query.ExactRiskQueryPolicy(4, runtime_budget=8)
    guard = threading.Lock()
    current = peak = 0

    class Tree:
        def query(self, points, **kwargs):
            nonlocal current, peak
            assert kwargs == dict(k=1, eps=0, p=2, workers=4)
            with guard:
                current += 1
                peak = max(peak, current)
            time.sleep(.005)
            with guard:
                current -= 1
            return points, 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        assert list(executor.map(lambda i: policy.query(Tree(), i), range(12))) == [(i, 0) for i in range(12)]
    assert peak == 1


def test_query_error_is_not_hidden_and_releases_slot(eight_cpus):
    policy = risk_query.ExactRiskQueryPolicy(4, runtime_budget=8)
    calls = []

    class Tree:
        def query(self, points, **kwargs):
            calls.append(kwargs)
            if points == "bad":
                raise TypeError("unsupported query runtime")
            return points, 0

    with pytest.raises(TypeError, match="unsupported query runtime"):
        policy.query(Tree(), "bad")
    assert policy.query(Tree(), "good") == ("good", 0)
    assert len(calls) == 2


@pytest.mark.parametrize("quota,expected", [("250000 100000", 2), ("max 100000", 6),
                                            ("invalid", 1), ("100 0", 1)])
def test_cgroup_and_affinity_caps(monkeypatch, quota, expected):
    monkeypatch.setattr(risk_query.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(risk_query.os, "sched_getaffinity", lambda pid: set(range(6)), raising=False)
    monkeypatch.setattr(risk_query.Path, "read_text", lambda self: quota)
    assert risk_query.available_cpus() == expected


def test_missing_cgroup_uses_cpu_affinity(monkeypatch):
    monkeypatch.setattr(risk_query.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(risk_query.os, "sched_getaffinity", lambda pid: set(range(4)), raising=False)
    def missing(self):
        raise FileNotFoundError()
    monkeypatch.setattr(risk_query.Path, "read_text", missing)
    assert risk_query.available_cpus() == 4


def test_legacy_cgroup_cpu_quota(monkeypatch):
    monkeypatch.setattr(risk_query.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(risk_query.os, "sched_getaffinity", lambda pid: set(range(6)), raising=False)
    def legacy(self):
        if self.name == "cpu.max":
            raise FileNotFoundError()
        return "300000" if self.name == "cpu.cfs_quota_us" else "100000"
    monkeypatch.setattr(risk_query.Path, "read_text", legacy)
    assert risk_query.available_cpus() == 3
