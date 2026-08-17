import json
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_trajectory_stability as trajectory_stability
import benchmark_auto_probe_count_planning as probe_count_benchmark
import benchmark_auto_planning_stability as planning_stability
import diagnose_target_candidate_feasibility as target_diagnostic


RELEASE_BUILDER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "release" / "build_supplementary_data.py"
release_builder_spec = importlib.util.spec_from_file_location("build_supplementary_data", RELEASE_BUILDER_PATH)
release_builder = importlib.util.module_from_spec(release_builder_spec)
assert release_builder_spec.loader is not None
release_builder_spec.loader.exec_module(release_builder)


def _probe(x_mm: float) -> dict:
    x_um = x_mm * 1000.0
    return {
        "entry_xyz": [x_um, 0.0, 0.0],
        "tip_xyz": [x_um, 1000.0, 0.0],
    }


def test_rectangular_hungarian_can_skip_unmatched_baseline_probe():
    baseline = [_probe(100.0), _probe(1.0), _probe(2.0)]
    current = [_probe(1.0), _probe(2.0)]

    metrics = trajectory_stability.matched_spatial_metrics(baseline, current)

    assert metrics["matchingMethod"] == "rectangular_hungarian"
    assert metrics["matchedProbeCount"] == 2
    assert metrics["unmatchedBaselineProbeCount"] == 1
    assert metrics["unmatchedCurrentProbeCount"] == 0
    assert metrics["entryDisplacementMm"] == 0.0
    assert metrics["tipDisplacementMm"] == 0.0
    assert metrics["angularChangeDeg"] == 0.0


def test_rectangular_hungarian_can_skip_unmatched_current_probe():
    baseline = [_probe(1.0), _probe(2.0)]
    current = [_probe(100.0), _probe(2.0), _probe(1.0)]

    metrics = trajectory_stability.matched_spatial_metrics(baseline, current)

    assert metrics["matchedProbeCount"] == 2
    assert metrics["unmatchedBaselineProbeCount"] == 0
    assert metrics["unmatchedCurrentProbeCount"] == 1
    assert metrics["entryDisplacementMm"] == 0.0


def test_probe_count_checkpoint_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "checkpoint.json"
    run_config = {"workers": 1, "caseTimeoutSec": 30.0}
    cases = [{"caseId": "case_01", "status": "ok"}]

    probe_count_benchmark.write_checkpoint(path, run_config=run_config, results=cases)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["runConfig"] == run_config
    assert payload["cases"] == cases
    assert not path.with_suffix(".json.tmp").exists()


def test_release_json_sanitizer_does_not_add_newlines_to_identifiers():
    sanitized = release_builder.sanitize_json(
        {"caseId": "case_01_single_vpm", "nested": ["VISal", "plain text"]}
    )

    assert sanitized["caseId"] == "case_01_single_vpm"
    assert sanitized["nested"] == ["VISal", "plain text"]


def test_pipeline_exception_diagnostics_preserve_candidate_funnel():
    class PipelineError(RuntimeError):
        extra = {
            "computeProfile": {"candidateGeneration": {"finalCount": 0, "specTotal": 2394}},
            "candidateStageBreakdown": {"summary": {"safePoolCount": 0}},
        }

    extra, generation, breakdown = probe_count_benchmark.exception_pipeline_diagnostics(
        PipelineError("no candidates")
    )

    assert extra["computeProfile"]["candidateGeneration"]["specTotal"] == 2394
    assert generation == {"finalCount": 0, "specTotal": 2394}
    assert breakdown["summary"]["safePoolCount"] == 0


def test_bilateral_uncovered_tokens_preserve_missing_side():
    targets, sides = probe_count_benchmark.summarize_uncovered(
        "bilateral_symmetric", ["RE:right", "VISrl:left"]
    )

    assert targets == ["RE", "VISrl"]
    assert sides == [
        {"key": "RE", "side": "right"},
        {"key": "VISrl", "side": "left"},
    ]


def test_isolated_case_timeout_returns_durable_failure(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 321
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    terminated = []
    monkeypatch.setattr(probe_count_benchmark.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        probe_count_benchmark,
        "terminate_process_tree",
        lambda process: terminated.append(process.pid),
    )

    result = probe_count_benchmark.run_isolated_case(
        probe_count_benchmark.CASES[0],
        profile_root=tmp_path,
        structures_json=tmp_path / "structures.json",
        workers=1,
        timeout_sec=0.01,
        worker_dir=tmp_path / "workers",
    )

    assert terminated == [321]
    assert result["status"] == "timeout"
    assert result["failureReason"] == "case_timeout"
    assert result["timedOut"] is True
    assert result["selectedProbeCount"] == 0


def test_memory_aware_worker_policy_clamps_to_available_budget():
    assert planning_stability.resolve_worker_count(
        4,
        policy="memory_aware",
        available_gb=19.0,
        reserve_gb=8.0,
        memory_per_worker_gb=12.0,
    ) == 1
    assert planning_stability.resolve_worker_count(
        4,
        policy="memory_aware",
        available_gb=60.0,
        reserve_gb=8.0,
        memory_per_worker_gb=12.0,
    ) == 4


def test_profile_run_lock_rejects_concurrent_stability_process(tmp_path):
    first, lock_path = planning_stability.acquire_profile_run_lock(tmp_path)
    try:
        assert lock_path.exists()
        try:
            planning_stability.acquire_profile_run_lock(tmp_path)
        except RuntimeError as exc:
            assert "Another stability run" in str(exc)
        else:
            raise AssertionError("Second profile lock unexpectedly succeeded")
    finally:
        first.close()


def test_target_diagnostic_localizes_zero_seed_failure_before_solver():
    diagnosis = target_diagnostic.classify_target_failure(
        target="VISal",
        target_region={"acronym": "VISal", "voxelCount": 48435, "descendantCount": 7},
        result={
            "selectedProbeCount": 1,
            "uncoveredTargets": ["VISal"],
            "candidateGeneration": {
                "targetDiversitySeedCounts": {"VISal": 0},
                "coarseRejectReasonsBySource": {
                    "target_grid_VISal": {"noCoverage": 120, "brainMaskRejected": 30}
                },
            },
        },
    )

    assert diagnosis["failureStage"] == "coarse_target_coverage"
    assert diagnosis["counts"]["coarseCoverage"] == 0
    assert diagnosis["targetLocalRejectReasons"]["noCoverage"] == 120
    assert "selector" in diagnosis["conclusion"]


def test_label_centroid_stats_can_select_canonical_left_hemisphere(monkeypatch):
    server = probe_count_benchmark.atlas_util.import_backend_server()
    monkeypatch.setattr(server, "BREGMA_UM", (0.0, 0.0, 50.0), raising=True)
    monkeypatch.setattr(server, "VOXEL_SIZE_UM", 25.0, raising=True)
    annotation = np.zeros((1, 1, 5), dtype=np.uint32)
    annotation[0, 0, 0] = 402
    annotation[0, 0, 4] = 402

    bilateral = server._compute_label_centroid_stats_chunked(annotation, [402])
    left = server._compute_label_centroid_stats_chunked(annotation, [402], hemisphere="left")
    right = server._compute_label_centroid_stats_chunked(annotation, [402], hemisphere="right")

    assert bilateral[402]["count"] == 2
    assert bilateral[402]["sum_k"] == 4.0
    assert left[402]["count"] == 1
    assert left[402]["sum_k"] == 0.0
    assert right[402]["count"] == 1
    assert right[402]["sum_k"] == 4.0


def test_structure_fallback_registers_missing_parent_region(tmp_path):
    structures_path = tmp_path / "structures.json"
    structures_path.write_text(
        json.dumps(
            [
                {
                    "id": 10,
                    "acronym": "AUDp",
                    "name": "Primary auditory area",
                    "structure_id_path": [1, 10],
                },
                {
                    "id": 11,
                    "acronym": "AUDp-child",
                    "name": "Primary auditory area child",
                    "structure_id_path": [1, 10, 11],
                },
            ]
        ),
        encoding="utf-8",
    )

    def centroid_stats(_annotation, *, label_ids, chunk_depth, hemisphere=None):
        assert set(label_ids) == {10, 11}
        assert chunk_depth == 4
        if hemisphere == "left":
            return {11: {"count": 2, "sum_i": 4.0, "sum_j": 6.0, "sum_k": 8.0}}
        return {11: {"count": 4, "sum_i": 8.0, "sum_j": 12.0, "sum_k": 24.0}}

    rebuilt = []
    server = SimpleNamespace(
        PLANNER_REGIONS={},
        REGION_INDEX_CACHE_CHUNK_DEPTH=4,
        VOXEL_SIZE_UM=25.0,
        atlas_annotation=np.zeros((1, 1, 1), dtype=np.uint32),
        descendant_ids={},
        structure_map={},
        acronym_record_map={},
        _compute_label_centroid_stats_chunked=centroid_stats,
        _voxel_centroid_to_bregma_mm=lambda centroid: tuple(float(value) for value in centroid),
        _rebuild_region_centroids=lambda: rebuilt.append(True),
    )

    report = probe_count_benchmark.atlas_util.apply_structure_tree_descendant_fallback(
        server, ["AUDp"], structures_path
    )

    assert report["applied"] is True
    assert report["updatedTargets"][0]["plannerCentroidHemisphere"] == "left"
    assert server.PLANNER_REGIONS["AUDp"] == {
        "id": 10,
        "ap": 2.0,
        "dv": 3.0,
        "ml": 4.0,
        "category": "atlas",
    }
    assert server.descendant_ids[10] == frozenset({10, 11})
    assert server.structure_map[10]["voxel_count"] == 4
    assert server.acronym_record_map["audp"]["id"] == 10
    assert rebuilt == [True]


def test_structure_fallback_prefers_exact_ontology_id_over_stale_seed(tmp_path):
    structures_path = tmp_path / "structures.json"
    structures_path.write_text(
        json.dumps(
            [
                {"id": 394, "acronym": "VISam", "name": "anteromedial visual", "structure_id_path": [394]},
                {"id": 409, "acronym": "VISl", "name": "lateral visual", "structure_id_path": [409]},
            ]
        ),
        encoding="utf-8",
    )

    def centroid_stats(_annotation, *, label_ids, chunk_depth, hemisphere=None):
        assert set(label_ids) == {409}
        assert chunk_depth == 4
        if hemisphere == "left":
            return {409: {"count": 2, "sum_i": 4.0, "sum_j": 6.0, "sum_k": 8.0}}
        return {409: {"count": 4, "sum_i": 8.0, "sum_j": 12.0, "sum_k": 16.0}}

    server = SimpleNamespace(
        PLANNER_REGIONS={"VISl": {"id": 394, "ap": 0.0, "dv": 0.0, "ml": 0.0, "category": "core"}},
        REGION_INDEX_CACHE_CHUNK_DEPTH=4,
        VOXEL_SIZE_UM=25.0,
        atlas_annotation=np.zeros((1, 1, 1), dtype=np.uint32),
        descendant_ids={},
        structure_map={},
        acronym_record_map={"visl": {"id": 394}},
        _compute_label_centroid_stats_chunked=centroid_stats,
        _voxel_centroid_to_bregma_mm=lambda centroid: tuple(float(value) for value in centroid),
        _rebuild_region_centroids=lambda: None,
    )

    report = probe_count_benchmark.atlas_util.apply_structure_tree_descendant_fallback(
        server, ["VISl"], structures_path
    )

    assert report["updatedTargets"][0]["regionId"] == 409
    assert server.PLANNER_REGIONS["VISl"]["id"] == 409
    assert server.acronym_record_map["visl"]["id"] == 409


def test_region_index_cache_rejects_bilateral_centroid_policy(tmp_path, monkeypatch):
    server = probe_count_benchmark.atlas_util.import_backend_server()
    monkeypatch.setattr(server, "REGION_INDEX_CACHE_DIR", tmp_path, raising=True)
    monkeypatch.setattr(server, "REGION_INDEX_CACHE_ENABLED", True, raising=True)
    cache_path = server._region_index_cache_path("mri_ccf_hybrid", "abc")
    cache_path.write_text(
        json.dumps(
            {
                "atlasMode": "mri_ccf_hybrid",
                "atlasHash": "abc",
                "plannerSeed": {"VISal": {"id": 402, "ml": 0.0}},
                "structureMap": {"402": {}},
                "descendantIds": {"402": [402]},
            }
        ),
        encoding="utf-8",
    )

    assert server._load_region_index_cache("mri_ccf_hybrid", "abc") is None

    server._save_region_index_cache(
        "mri_ccf_hybrid",
        "abc",
        {"VISal": {"id": 402, "ml": -3.7}},
        {402: {}},
        {402: [402]},
    )
    loaded = server._load_region_index_cache("mri_ccf_hybrid", "abc")
    assert loaded["plannerSeed"]["VISal"]["ml"] == -3.7
    assert planning_stability.resolve_worker_count(
        4,
        policy="fixed",
        available_gb=1.0,
        reserve_gb=8.0,
        memory_per_worker_gb=12.0,
    ) == 4


def test_depth_variants_are_centered_on_each_target_depth():
    server = probe_count_benchmark.atlas_util.import_backend_server()

    shallow = server._candidate_depth_variants(7600.0, 7600.0, 4700.0, True)
    deep = server._candidate_depth_variants(7600.0, 7600.0, 6500.0, True)

    assert 5200.0 in shallow
    assert 7000.0 in deep
    assert 5200.0 not in deep


def test_bilateral_target_local_centers_include_exact_mirror():
    server = probe_count_benchmark.atlas_util.import_backend_server()

    assert server._target_local_ml_centers(-4.25, False) == [-4.25]
    assert server._target_local_ml_centers(-4.25, True) == [-4.25, 4.25]
    assert server._target_local_ml_centers(0.0, True) == [0.0]


def test_bilateral_candidate_spec_mirrors_entry_and_lateral_direction():
    server = probe_count_benchmark.atlas_util.import_backend_server()

    mirrored = server._mirror_bilateral_candidate_spec(
        (-0.1, 2.191, 10.0, 30.0, 7125.0, "grid")
    )

    assert mirrored == (-0.1, -2.191, 10.0, 330.0, 7125.0, "grid")


def test_bounded_bilateral_token_cover_improves_anchored_solution(monkeypatch):
    server = probe_count_benchmark.atlas_util.import_backend_server()
    monkeypatch.setattr(server, "trajs_collide", lambda *_args: False)

    def candidate(candidate_id, sides):
        explicit_sides = {side for values in sides.values() for side in values}
        ml_mm = 1.0 if explicit_sides == {"right"} else -1.0
        return {
            "id": candidate_id,
            "coveredRegions": sorted(sides),
            "coveredRegionSides": sides,
            "paretoRank": 1,
            "explainScore": 1.0,
            "ml_mm": ml_mm,
            "entry_xyz": [float(candidate_id), 0.0, 0.0],
            "tip_xyz": [float(candidate_id), 1.0, 0.0],
        }

    candidates = [
        candidate(1, {"A": ["left"], "B": ["left"]}),
        candidate(2, {"A": ["right"]}),
        candidate(3, {"B": ["right"]}),
        candidate(4, {"A": ["left"]}),
        candidate(5, {"B": ["left"]}),
    ]
    incumbent = [candidates[1], candidates[2], candidates[3], candidates[4]]

    selected = server._bounded_exact_token_cover_selection(
        candidates,
        ["A", "B"],
        "bilateral_symmetric",
        min_clearance=0.0,
        min_entry=0.0,
        incumbent=incumbent,
    )

    assert len(selected) == 3
    covered = set().union(
        *(server._candidate_coverage_tokens(row, ["A", "B"], "bilateral_symmetric") for row in selected)
    )
    assert covered == {"A:left", "A:right", "B:left", "B:right"}


def test_explicit_candidate_side_is_not_broadened_by_entry_fallback():
    server = probe_count_benchmark.atlas_util.import_backend_server()
    candidate = {
        "ml_mm": -2.0,
        "coveredRegions": ["A"],
        "coveredRegionSides": {"A": ["right"]},
    }

    assert server._candidate_coverage_tokens(
        candidate, ["A"], "bilateral_symmetric"
    ) == {"A:right"}


def test_bounded_token_cover_can_replace_incomplete_augmentation(monkeypatch):
    server = probe_count_benchmark.atlas_util.import_backend_server()
    monkeypatch.setattr(server, "trajs_collide", lambda *_args: False)
    candidates = [
        {"id": 1, "coveredRegions": ["A"], "coveredRegionSides": {"A": ["left"]}},
        {"id": 2, "coveredRegions": ["A"], "coveredRegionSides": {"A": ["right"]}},
        {"id": 3, "coveredRegions": ["B"], "coveredRegionSides": {"B": ["left"]}},
        {"id": 4, "coveredRegions": ["B"], "coveredRegionSides": {"B": ["right"]}},
    ]

    selected, missing, covered, required = server._augment_selection_for_targeting_mode(
        candidates[:3], candidates, ["A", "B"], "bilateral_symmetric", 0.0, 0.0
    )

    assert len(selected) == 4
    assert missing == []
    assert covered == required


def test_bounded_token_cover_repairs_incomplete_unilateral_selection(monkeypatch):
    server = probe_count_benchmark.atlas_util.import_backend_server()
    monkeypatch.setattr(server, "trajs_collide", lambda *_args: False)
    candidates = [
        {"id": 1, "coveredRegions": ["A"]},
        {"id": 2, "coveredRegions": ["B"]},
        {"id": 3, "coveredRegions": ["C"]},
    ]

    selected, missing, covered, required = server._augment_selection_for_targeting_mode(
        candidates[:2], candidates, ["A", "B", "C"], "single_hemisphere", 0.0, 0.0
    )

    assert len(selected) == 3
    assert missing == []
    assert covered == required == {"A", "B", "C"}
