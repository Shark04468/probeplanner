"""Affine query perturbations preserve the physical path sampling contract."""
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from test_optimizer_server import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_registration_perturbation_sensitivity as audit


def affine():
    return audit.perturbation_to_affine(audit.Perturbation(
        'fixture', 'combined', translation_mm=(.2, -.1, .15),
        rotation_deg=(2., -3., 4.), scale=1.02), np.array([0., 2., 0.]))


def expected_path(s, transform, args, kwargs):
    # Independent continuous points; inverse-transform before rounding to voxels.
    entry, _, direction, _ = s.make_trajectory_xyz(*args, kwargs.get('skull_tilt_deg', 0.),
        entry_dv_corr_mm=kwargs.get('entry_dv_corr_mm'))
    depth = args[4]
    depths = list(np.arange(0., depth, kwargs['step_um'])) + [depth]
    indices = []
    for d in depths:
        point = np.asarray(entry) + d*np.asarray(direction)
        planner = (point-s.BREGMA_UM)/1000.*np.array([-1., 1., 1.])
        source = transform.inverse(planner)
        voxel = (s.BREGMA_UM+source*1000.*np.array([-1., 1., 1.]))/s.VOXEL_SIZE_UM
        indices.append(tuple(np.rint(voxel).astype(int)))
    return indices, depths


@pytest.mark.parametrize('route', ['ordered', 'public', 'cached'])
@pytest.mark.parametrize('explicit_dv', [None, .275])
def test_affine_routes_use_same_continuous_points(server, route, explicit_dv):
    args = (-1.3, -.7, 17., 210., 112.5)
    kwargs = dict(skull_tilt_deg=9., step_um=25.)
    if explicit_dv is not None:
        kwargs['entry_dv_corr_mm'] = explicit_dv
    transform = affine()
    expected, depths = expected_path(server, transform, args, kwargs)
    with audit.BackendPerturbationContext(server, transform):
        if route == 'ordered':
            actual, actual_depths = server._sample_trajectory_path_indices(*args, **kwargs)
            assert actual == expected
            np.testing.assert_allclose(actual_depths, depths, atol=1e-9, rtol=0)
        else:
            fn = server.sample_trajectory_voxels if route == 'public' else server.sample_trajectory_voxels_cached
            expected = list(dict.fromkeys(v for v in expected if server.voxel_in_bounds(*v)))
            assert fn(*args, **kwargs) == expected


def test_ordered_path_keeps_duplicates_exact_tip_and_out_of_bounds(server):
    args = (-1., -.7, 0., 0., 8123.5)
    kwargs = dict(step_um=10., entry_dv_corr_mm=.275)
    transform = affine()
    expected, depths = expected_path(server, transform, args, kwargs)
    with audit.BackendPerturbationContext(server, transform):
        actual, actual_depths = server._sample_trajectory_path_indices(*args, **kwargs)
    assert actual == expected
    assert len(set(actual)) < len(actual)
    assert any(not server.voxel_in_bounds(*v) for v in actual)
    np.testing.assert_allclose(actual_depths, depths, atol=1e-9, rtol=0)


def test_context_restores_functions_regions_and_caches_on_exception(server):
    names = ['_sample_trajectory_path_indices', 'sample_trajectory_voxels',
             'sample_trajectory_voxels_cached', 'compute_structure_principal_axis']
    functions = {k: getattr(server, k) for k in names}
    regions, centroids = deepcopy(server.PLANNER_REGIONS), deepcopy(server.REGION_CENTROIDS_UM)
    baseline = server.sample_trajectory_voxels_cached(-1., -.7, 0., 0., 112.5)
    with pytest.raises(RuntimeError, match='fixture'):
        with audit.BackendPerturbationContext(server, affine()):
            assert server.sample_trajectory_voxels_cached(-1., -.7, 0., 0., 112.5) != baseline
            raise RuntimeError('fixture')
    assert {k: getattr(server, k) for k in names} == functions
    assert server.PLANNER_REGIONS == regions and server.REGION_CENTROIDS_UM == centroids
    assert server.sample_trajectory_voxels_cached(-1., -.7, 0., 0., 112.5) == baseline


def test_identity_context_is_numerically_identical(server):
    identity = audit.perturbation_to_affine(audit.Perturbation('identity', 'identity'), np.zeros(3))
    args, kwargs = (-1., -.7, 17., 210., 112.5), dict(step_um=25., entry_dv_corr_mm=.275)
    baseline = server._sample_trajectory_path_indices(*args, **kwargs)
    with audit.BackendPerturbationContext(server, identity):
        assert server._sample_trajectory_path_indices(*args, **kwargs) == baseline


def test_pca_axis_and_centroid_follow_affine(server, monkeypatch):
    original = dict(theta_deg=0., phi_deg=0., principal_axis=[0., 1., 0.],
        centroid_bregma=[-1., 3., -.7], elongation=2., dv_span_um=1000.)
    monkeypatch.setattr(server, 'compute_structure_principal_axis', lambda _: deepcopy(original))
    transform = affine()
    with audit.BackendPerturbationContext(server, transform):
        actual = server.compute_structure_principal_axis(1)
    np.testing.assert_allclose(actual['centroid_bregma'], transform.forward(np.array(original['centroid_bregma'])), atol=.00051)
    flip = np.diag([-1., 1., 1.])
    direction = flip @ transform.rotation @ flip @ np.array(original['principal_axis'])
    np.testing.assert_allclose(actual['principal_axis'], direction, atol=1e-6)
    theta, phi = np.radians([actual['theta_deg'], actual['phi_deg']])
    np.testing.assert_allclose([np.sin(theta)*np.cos(phi), np.cos(theta), np.sin(theta)*np.sin(phi)], direction, atol=.0002)


def test_report_does_not_claim_success_for_failed_cases(tmp_path):
    rows = [dict(name='baseline', kind='baseline', status='ok', selectedProbeCount=1,
                 coveredTargets=['x'], uncoveredTargets=[], selected=[]),
            dict(name='failed', kind='translation', status='fail_closed', selectedProbeCount=0,
                 coveredTargets=[], uncoveredTargets=['x'], selected=[])]
    payload = dict(summary=audit.add_comparisons(rows, ['x']), cases=rows)
    out = tmp_path / 'report.md'
    audit.write_markdown(out, payload)
    text = out.read_text(encoding='utf-8')
    assert 'no perturbation failed' not in text
    assert 'Failures: 1; coverage-changed cases: 1; cases with newly uncovered targets: 1.' in text
    assert 'not anatomical registration accuracy' in text
