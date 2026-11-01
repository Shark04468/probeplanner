"""Proposal families and centroid maps share the physical AP/DV/ML convention."""
import math
import threading

import numpy as np
import pytest

from test_optimizer_server import server


def centroid_line_residuals(server, monkeypatch, ap, tilt, theta, phi):
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'fixture': {'id': 1}})
    monkeypatch.setattr(server, 'compute_structure_principal_axis', lambda _: dict(
        elongation=2., theta_deg=theta, phi_deg=phi, centroid_bregma=[ap, 3., 1.]))
    proposals = server.generate_atlas_guided_candidates(['fixture'], {'depthUm': 7000., 'skullTiltDeg': tilt}, theta)
    assert proposals
    target = server.BREGMA_UM + np.array([-ap, 3., 1.]) * 1000.
    residuals = []
    for c in proposals:
        entry, tip, direction, _ = server.make_trajectory_xyz(c['ap_mm'], c['ml_mm'], c['theta_deg'],
            c['phi_deg'], c['depthUm'], c['skullTiltDeg'])
        delta = target - entry
        assert 0 <= np.dot(delta, direction) <= np.linalg.norm(np.array(tip) - entry)
        residuals.append(float(np.linalg.norm(np.cross(delta, direction))))
    return residuals


@pytest.mark.parametrize('ap', [-2., 2.])
@pytest.mark.parametrize('tilt', [-10., 0., 10.])
def test_vertical_pca_proposals_pass_through_centroid(server, monkeypatch, ap, tilt):
    assert max(centroid_line_residuals(server, monkeypatch, ap, tilt, 0., 0.)) < 1e-6


@pytest.mark.parametrize('tilt', [-10., 10.])
@pytest.mark.parametrize('phi', [0., 180.])
def test_oblique_pca_proposals_pass_through_centroid(server, monkeypatch, tilt, phi):
    assert max(centroid_line_residuals(server, monkeypatch, -2., tilt, 10., phi)) < 1e-6


def test_zero_ap_control(server, monkeypatch):
    assert max(centroid_line_residuals(server, monkeypatch, 0., 0., 0., 0.)) < 1e-6


def test_pca_angles_are_ccf_xyz(server, monkeypatch):
    annotation = np.zeros((310, 150, 150), dtype=np.uint16)
    for k in range(25):
        annotation[245+k, 60+2*k, 100:103] = 1
    monkeypatch.setattr(server, 'atlas_annotation', annotation)
    monkeypatch.setattr(server, 'descendant_ids', {1: frozenset([1])})
    axis = server.compute_structure_principal_axis(1)
    assert axis is not None and axis['elongation'] > 1.5
    theta, phi = math.radians(axis['theta_deg']), math.radians(axis['phi_deg'])
    direction = np.array([math.sin(theta)*math.cos(phi), math.cos(theta), math.sin(theta)*math.sin(phi)])
    assert np.dot(direction, np.array([1., 2., 0.])/math.sqrt(5.)) > .999


@pytest.mark.parametrize('tilt', [-10., 0., 10.])
@pytest.mark.parametrize('reverse', [False, True])
def test_pair_line_preserves_target_order_and_skull_tilt(server, monkeypatch, tilt, reverse):
    centers = {1: [6500., 2440., 6700.], 2: [7000., 4440., 6700.]}
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'a': {'id': 1}, 'b': {'id': 2}})
    monkeypatch.setattr(server, 'REGION_CENTROIDS_UM', centers)
    proposals = server.generate_multi_target_candidates(['b', 'a'] if reverse else ['a', 'b'],
        {'depthUm': 7000., 'maxAngle': 30., 'skullTiltDeg': tilt})
    assert len(proposals) == 1
    c = proposals[0]
    entry, _, direction, _ = server.make_trajectory_xyz(c['ap_mm'], c['ml_mm'], c['theta_deg'],
        c['phi_deg'], c['depthUm'], c['skullTiltDeg'])
    for point in centers.values():
        delta = np.asarray(point) - entry
        # Public fields round entries to 1 um and angles to 0.01 degree.
        assert np.linalg.norm(np.cross(delta, direction)) < 2.
        assert 0 < np.dot(delta, direction) < c['depthUm']


@pytest.mark.parametrize('voxel_x', [100., 300.])
def test_atlas_centroid_coordinate_round_trip(server, monkeypatch, voxel_x):
    voxel = [voxel_x, 100., 140.]
    ap, dv, ml = server._voxel_centroid_to_bregma_mm(voxel)
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'fixture': dict(id=1, ap=ap, dv=dv, ml=ml)})
    server._rebuild_region_centroids()
    np.testing.assert_allclose(server.REGION_CENTROIDS_UM[1], np.array(voxel) * 25., atol=1e-6, rtol=0)


def test_dynamic_centroid_uses_anterior_positive_ap(server, monkeypatch):
    updates = dict(PLANNER_REGIONS={}, DYNAMIC_REGION_PROVISION_ENABLED=True, atlas_annotation=np.ones((1, 1, 1)),
        atlas_tree_ref=None, acronym_record_map={'fixture': {'id': 1, 'acronym': 'fixture', 'name': 'fixture'}},
        descendant_ids={}, structure_map={}, atlas_lock=threading.RLock(),
        _compute_descendant_centroid=lambda *args: dict(ap_mm=-2., dv_mm=3., ml_mm=-1., voxel_count=1))
    for name, value in updates.items():
        monkeypatch.setattr(server, name, value)
    assert server._register_region_from_atlas_key('fixture')
    np.testing.assert_allclose(server.REGION_CENTROIDS_UM[1], [7400., 3440., 4700.], atol=1e-6, rtol=0)


def test_bootstrap_ap_and_ccf_locations_agree(server):
    assert server.BASE_PLANNER_REGIONS['CA1']['ap'] < 0
    for region in server.PLANNER_REGIONS.values():
        assert server.REGION_CENTROIDS_UM[region['id']][0] == pytest.approx(5400. - region['ap'] * 1000.)
