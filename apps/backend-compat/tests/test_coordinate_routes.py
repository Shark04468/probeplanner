import numpy as np
import pytest

from test_optimizer_server import server
from test_coordinate_contract import probe_at, synthetic_atlas, inverse_tip


def test_objective_and_risk_cache_identity_include_entry_dv(server, monkeypatch):
    first = probe_at(server, ap=0, theta=0, depth=1000)
    second = {**first, 'entryDvCorr_mm': .5}
    assert server._candidate_objective_cache_key(first, ['T'], {}) != server._candidate_objective_cache_key(second, ['T'], {})
    seen = []
    monkeypatch.setattr(server, 'risk_diag_cache', {})
    monkeypatch.setattr(server, 'sample_trajectory_voxels_cached', lambda *a, **k: seen.append(k['entry_dv_corr_mm']) or [])
    monkeypatch.setattr(server, '_risk_distance_diagnostics_from_voxels', lambda *a, **k: {'available': True})
    server._compute_probe_risk_diagnostics(first)
    server._compute_probe_risk_diagnostics(second)
    server._compute_probe_risk_diagnostics(first)
    assert seen == [0, .5]


class ConstantPerturbation:
    def normal(self, mean, sigma, size=None):
        return np.array([0., 0., .2]) if size == 3 else 0.


def test_uncertainty_applies_dv_perturbation_to_sampled_path(server, monkeypatch):
    synthetic_atlas(server, monkeypatch)
    probe = probe_at(server, ap=.2, theta=0, tilt=10, depth=500)
    probe['coveredRegions'] = ['T']
    monkeypatch.setattr(server.np.random, 'default_rng', lambda *a: ConstantPerturbation())
    monkeypatch.setattr(server, '_risk_distance_from_voxels_mm', lambda *a: None)
    original = server.sample_trajectory_voxels_cached
    seen = []
    def sample(*args, **kwargs):
        voxels = original(*args, **kwargs)
        seen.append((kwargs['entry_dv_corr_mm'], voxels[0]))
        return voxels
    monkeypatch.setattr(server, 'sample_trajectory_voxels_cached', sample)
    result = server._compute_probe_uncertainty_metrics(probe, ['T'], {'robustSamples': 4, 'maxAngle': 30})
    expected_dv = probe['entryDvCorr_mm'] + .2
    assert len(seen) == 5 and result['coverageMean'] == 1
    assert seen[-1][0] == pytest.approx(probe['entryDvCorr_mm'])
    for dv, first in seen[:4]:
        assert dv == pytest.approx(expected_dv)
        assert first[1] == round((server.BREGMA_UM[1]+1000*expected_dv)/25)


def test_robust_objective_uses_same_corrected_tip_helper(server, monkeypatch):
    probe = probe_at(server, ap=-3.5, depth=5000)
    probe['coveredRegions'] = ['T']
    monkeypatch.setattr(server.np.random, 'default_rng', lambda *a: ConstantPerturbation())
    monkeypatch.setattr(server, 'robust_perturb_cache', {})
    original = server._tip_ap_dv_mm
    seen = []
    def record(ap, dv, direction, depth):
        result = original(ap, dv, direction, depth)
        seen.append((ap, dv, result))
        return result
    monkeypatch.setattr(server, '_tip_ap_dv_mm', record)
    server._compute_candidate_objectives(probe, ['T'], {'robustOptimization': True, 'robustSamples': 4, 'maxAngle': 45})
    perturbed = [row for row in seen if abs(row[1]-.2) < 1e-9]
    assert len(perturbed) == 4
    assert all(row[2][0] == pytest.approx(-6) for row in perturbed)


@pytest.mark.parametrize('tilt', [-10., 0., 10.])
def test_real_generator_retains_resolved_dv_in_preview(server, monkeypatch, tilt):
    synthetic_atlas(server, monkeypatch)
    monkeypatch.setattr(server, 'REGION_CENTROIDS_UM', {1: [1000., 1000., 1000.]})
    params = dict(gridSpacing=1000, angleStep=10, maxAngle=0, depthUm=700, skullTiltDeg=tilt,
                  robustOptimization=False, paretoEnabled=False, targetEndpointPolicy='trajectory_intersects',
                  candidateWorkers=1, computeWorkers=1)
    corr = dict(ap=1., ml=1., dv=1.2, apShift=0., mlShift=0., dvShift=.15)
    pool = server.generate_candidates_voxel(['T'], params, {'brainBounds': 'enforce'}, correction_factors=corr)
    assert pool
    for candidate in pool:
        assert candidate['entryDvCorr_mm_corrected'] == pytest.approx(candidate['entryDvCorr_mm']*1.2+.15, abs=.00002)
        assert server.trajectory_tip_mm(candidate) == pytest.approx(inverse_tip(server, candidate), abs=.00011)
        assert candidate['entry_xyz'][1] == pytest.approx(500+1000*candidate['entryDvCorr_mm_corrected'], abs=.06)


@pytest.mark.parametrize('corrected', [False, True])
def test_final_constraint_report_uses_physical_posterior_tip(server, monkeypatch, corrected):
    probe = probe_at(server)
    if corrected:
        probe.update(ap_mm=8, depthUm=1, ap_mm_corrected=-4, depthUm_corrected=5000)
    monkeypatch.setattr(server, '_candidate_brain_mask_path_summary', lambda *a, **k: {'available': False})
    modes = {k: 'off' for k in server.DEFAULT_CONSTRAINT_MODES}
    modes['brainBounds'] = 'warn'
    issues = server._collect_safety_issues([probe], {}, {}, modes)
    assert any(item['constraint'] == 'brainBounds' and '-6.500' in item['msgEn'] for item in issues)


@pytest.mark.parametrize('ap', [-2., 2.])
def test_targeted_recovery_uses_planner_region_anterior_positive_ap(server, monkeypatch, ap):
    monkeypatch.setattr(server, 'BREGMA_UM', np.array([5000., 500., 1250.]))
    annotation = np.full((400, 80, 100), 2, dtype=np.uint32)
    target_x = round((5000-ap*1000)/25)
    annotation[target_x-1:target_x+2, 20:65, 49:52] = 1
    monkeypatch.setattr(server, 'atlas_annotation', annotation)
    monkeypatch.setattr(server, 'ATLAS_SHAPE', annotation.shape)
    monkeypatch.setattr(server, 'descendant_ids', {1: frozenset([1])})
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'T': {'id': 1, 'ap': ap, 'ml': 0., 'dv': .5}})
    monkeypatch.setattr(server, 'structure_map', {1: {'voxel_count': 405}})
    monkeypatch.setattr(server, '_score_candidates', lambda *a, **k: {})
    result = server.coverage_boost_voxel([], ['T'], ['T'], {'maxAngle': 0, 'depthUm': 1000}, {},
        {'brainBounds': 'enforce'}, server.DEFAULT_CORRECTION_FACTORS)
    assert result['uncovered'] == [] and len(result['selected']) == 1
    assert result['selected'][0]['ap_mm'] == ap
