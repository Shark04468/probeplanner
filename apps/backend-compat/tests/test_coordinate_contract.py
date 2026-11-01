import math
from copy import deepcopy

import numpy as np
import pytest

from test_optimizer_server import server


def probe_at(server, ap=-4., theta=30., phi=0., tilt=0., depth=5000.):
    entry, tip, direction, dv = server.make_trajectory_xyz(ap, 0., theta, phi, depth, tilt)
    return dict(ap_mm=ap, ml_mm=0., theta_deg=theta, phi_deg=phi, skullTiltDeg=tilt,
                depthUm=depth, entryDvCorr_mm=dv, dir=direction, entry_xyz=entry, tip_xyz=tip)


def inverse_tip(server, probe):
    return [(server.BREGMA_UM[0]-probe['tip_xyz'][0])/1000.,
            (probe['tip_xyz'][1]-server.BREGMA_UM[1])/1000.]


@pytest.mark.parametrize('theta,phi,tilt', [(0, 0, 0), (30, 0, 0), (30, 180, 0),
                                          (30, 90, 0), (0, 0, 10), (0, 0, -10),
                                          (30, 0, 10), (30, 180, -10)])
def test_original_red_tip_contract(server, theta, phi, tilt):
    probe = probe_at(server, theta=theta, phi=phi, tilt=tilt)
    assert server.trajectory_tip_mm(probe) == pytest.approx(inverse_tip(server, probe), abs=1e-10)


def test_original_red_posterior_bound(server):
    probe = probe_at(server)
    expected = inverse_tip(server, probe)
    assert expected[0] == pytest.approx(-6.5)
    assert (server.trajectory_tip_mm(probe)[0] >= -6.) == (expected[0] >= -6.)


@pytest.mark.parametrize('ap,tilt', [(2., 10.), (-2., 10.), (2., -10.), (-2., -10.)])
@pytest.mark.parametrize('phi', [0., 90., 180., 270.])
def test_parameter_entry_contact_and_physical_geometry_agree(server, ap, tilt, phi):
    probe = probe_at(server, ap=ap, theta=20., phi=phi, tilt=tilt)
    entry = server._probe_entry_um(ap, 0., tilt, probe['entryDvCorr_mm'])
    np.testing.assert_allclose(entry, probe['entry_xyz'], atol=1e-10)
    assert server.trajectory_tip_mm(probe) == pytest.approx(inverse_tip(server, probe), abs=1e-10)


@pytest.mark.parametrize('delta,allowed', [(-.001, False), (0., True), (.001, True)])
def test_individual_posterior_bound_and_margin(server, monkeypatch, delta, allowed):
    ap = -6. + 2.5 + delta
    probe = probe_at(server, ap=ap)
    monkeypatch.setattr(server, '_candidate_brain_mask_path_summary', lambda *a, **k: {'available': False})
    ok, reason, _ = server._candidate_passes_single_enforced_constraints(
        probe, {}, {}, {'brainBounds': 'enforce'})
    assert ok is allowed
    assert reason == (None if allowed else 'brainBounds')
    assert server._estimate_bounds_margin_mm(probe) == pytest.approx(delta, abs=1e-10)


@pytest.mark.parametrize('dv', [-.25, 0., .5])
def test_explicit_entry_dv_is_sampled_and_cache_separated(server, monkeypatch, dv):
    monkeypatch.setattr(server, 'BREGMA_UM', np.array([500., 500., 500.]))
    monkeypatch.setattr(server, 'ATLAS_SHAPE', (80, 80, 80))
    monkeypatch.setattr(server, 'trajectory_voxel_cache', {})
    entry, tip, _, returned_dv = server.make_trajectory_xyz(0, 0, 0, 0, 500, 10, entry_dv_corr_mm=dv)
    indices, depths = server._sample_trajectory_path_indices(0, 0, 0, 0, 500, 10, entry_dv_corr_mm=dv)
    assert returned_dv == dv
    assert indices[0] == tuple(np.rint(np.array(entry)/25).astype(int))
    assert indices[-1] == tuple(np.rint(np.array(tip)/25).astype(int))
    assert depths[-1] == 500
    a = server.sample_trajectory_voxels_cached(0, 0, 0, 0, 500, entry_dv_corr_mm=dv)
    b = server.sample_trajectory_voxels_cached(0, 0, 0, 0, 500, entry_dv_corr_mm=dv+.025)
    assert a != b and len(server.trajectory_voxel_cache) == 2


def test_raw_contact_missing_dv_uses_tilt_not_zero(server):
    probe = server._normalize_contact_probe_payload({'ap_mm': -2., 'skullTiltDeg': 10., 'depthUm': 1000.}, 0)
    assert probe['entryDvCorr_mm'] == pytest.approx(2*math.tan(math.radians(10)))


def test_corrected_contact_values_are_not_corrected_again(server):
    probe = server._normalize_contact_probe_payload(dict(ap_mm=1, ml_mm=2, entryDvCorr_mm=3,
        depthUm=1000, ap_mm_corrected=-2, ml_mm_corrected=-1, entryDvCorr_mm_corrected=.2, depthUm_corrected=2000), 0)
    assert [probe[k] for k in ['ap_mm', 'ml_mm', 'entryDvCorr_mm', 'depthUm']] == [-2, -1, .2, 2000]


def synthetic_atlas(server, monkeypatch):
    monkeypatch.setattr(server, 'BREGMA_UM', np.array([1000., 500., 1000.]))
    monkeypatch.setattr(server, 'ATLAS_SHAPE', (100, 100, 100))
    monkeypatch.setattr(server, 'atlas_annotation', np.ones((100, 100, 100), dtype=np.uint32))
    monkeypatch.setattr(server, 'descendant_ids', {1: frozenset([1])})
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'T': {'id': 1, 'ap': 0., 'ml': 0., 'dv': .5}})
    monkeypatch.setattr(server, 'structure_map', {1: {'id': 1, 'acronym': 'T', 'voxel_count': 1000000}})


def test_local_candidate_correction_is_single_applied_to_geometry_and_audit(server, monkeypatch):
    synthetic_atlas(server, monkeypatch)
    corr = dict(ap=.8, ml=1.2, dv=1.5, apShift=.1, mlShift=.1, dvShift=.2)
    candidate = server._build_candidate_from_geometry(base_probe={'skullTiltDeg': 10.},
        ap_mm=.2, ml_mm=.1, theta_deg=0., phi_deg=0., depth_um=500., target_ids={'T': 1},
        params={}, constraint_modes={'brainBounds': 'enforce'}, correction_factors=corr, probe_radius_um=0.)
    assert candidate is not None
    raw_dv = -.2*math.tan(math.radians(10))
    expected_dv = raw_dv*1.5+.2
    assert candidate['entryDvCorr_mm'] == pytest.approx(raw_dv, abs=.000051)
    assert candidate['entryDvCorr_mm_corrected'] == pytest.approx(expected_dv, abs=.000051)
    assert candidate['entry_xyz'][1] == pytest.approx(500+1000*expected_dv, abs=.051)
    assert server.trajectory_tip_mm(candidate) == pytest.approx(inverse_tip(server, candidate), abs=.00011)
    sampled = server._candidate_brain_mask_path_summary(candidate)
    assert sampled['passed']
    normalized = server._normalize_contact_probe_payload(candidate, 0)
    entry = server._probe_entry_um(normalized['ap_mm'], normalized['ml_mm'],
                                 normalized['skullTiltDeg'], normalized['entryDvCorr_mm'])
    np.testing.assert_allclose(entry, candidate['entry_xyz'], atol=.11)


def test_coverage_api_honors_explicit_entry_dv(server, monkeypatch):
    synthetic_atlas(server, monkeypatch)
    server.atlas_annotation.fill(0)
    server.atlas_annotation[:, 45:65, :] = 1
    result = server.app.test_client().post('/api/coverage', json={'targetKeys': ['T'],
        'trajectories': [dict(ap_mm=0, ml_mm=0, entryDvCorr_mm=.5, depthUm=500)]})
    assert result.status_code == 200
    assert result.get_json()['trajectories'][0]['coveredRegions'] == ['T']


def resolved_probe():
    return dict(ap_mm=-2., ml_mm=1., entryDvCorr_mm=.2, depthUm=1000.,
        ap_mm_corrected=-1.5, ml_mm_corrected=1.25, entryDvCorr_mm_corrected=.4, depthUm_corrected=1500.,
        theta_deg=20., phi_deg=180., coveredRegions=['VISp'])


def test_export_preserves_resolved_coordinates_and_depth(server):
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [resolved_probe()], 'correctionFactors': {'ap': 2., 'dv': 2., 'dvShift': 1.}})
    assert result.status_code == 200
    p = result.get_json()['pinpointData']['probes'][0]
    assert [p[k] for k in ['ap', 'ml', 'dv', 'depth']] == [1.5, 1.25, .4, 1.5]


def test_raw_export_scales_depth_once(server):
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [dict(ap_mm=-2., ml_mm=1., entryDvCorr_mm=.2, depthUm=1000.)],
        'correctionFactors': {'dv': 1.5, 'dvShift': .1}})
    p = result.get_json()['pinpointData']['probes'][0]
    assert [p['dv'], p['depth']] == pytest.approx([.4, 1.5])


@pytest.mark.parametrize('change', ['resolved_dv', 'resolved_depth', 'correction'])
def test_linked_export_rejects_changed_resolved_geometry(server, change):
    probe = resolved_probe()
    response = {'ok': True, 'selected': [probe], 'correctionFactors': server.DEFAULT_CORRECTION_FACTORS}
    server._attach_payload_hashes({}, response)
    server._persist_replay_record('coordinate-plan', {}, response)
    body = dict(trajectories=[deepcopy(probe)], planRequestId='coordinate-plan', planResponseHash=response['responseHash'])
    if change == 'resolved_dv':
        body['trajectories'][0]['entryDvCorr_mm_corrected'] += .1
    elif change == 'resolved_depth':
        body['trajectories'][0]['depthUm_corrected'] += 100
    else:
        body['correctionFactors'] = {'dvShift': .1}
    assert server.app.test_client().post('/api/export_pinpoint', json=body).status_code == 400
