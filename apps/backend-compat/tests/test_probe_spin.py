import numpy as np
import pytest

from test_optimizer_server import server


def test_positive_ccf_spin_rotates_projected_ml_toward_ap(server):
    np.testing.assert_allclose(server._probe_lateral_axis_um([0, 1, 0], 90), [1, 0, 0], atol=1e-12)
    np.testing.assert_allclose(server._probe_lateral_axis_um([0, 1, 0], 360), [0, 0, 1], atol=1e-12)


def test_spun_contact_samples_and_collision_segments_agree(server):
    probe = {'entry_xyz': [1000, 1000, 1000], 'tip_xyz': [1000, 3000, 1000],
             'shankCount': 2, 'shankPitchUm': 300, 'radiusUm': 0, 'spin_deg': 90}
    samples, geometry = server._build_probe_swept_contact_samples_um(probe, [0, 1, 0], 0)
    segments = server._probe_shank_segments_um(probe)
    assert geometry['spinDeg'] == 90
    assert len(samples) == len(segments) == 2
    for index, (sample, segment) in enumerate(zip(samples, segments)):
        np.testing.assert_allclose(sample['offset'], [-150 if index == 0 else 150, 0, 0], atol=1e-12)
        np.testing.assert_allclose(segment[0] - probe['entry_xyz'], sample['offset'], atol=1e-12)
        np.testing.assert_allclose(segment[1] - probe['tip_xyz'], sample['offset'], atol=1e-12)


def test_collision_uses_rotated_shanks_not_unrotated_centerline(server):
    a = {'entry_xyz': [1000, 1000, 1000], 'tip_xyz': [1000, 3000, 1000],
         'shankCount': 2, 'shankPitchUm': 300, 'radiusUm': 10}
    b = {'entry_xyz': [1150, 1000, 1000], 'tip_xyz': [1150, 3000, 1000],
         'shankCount': 1, 'radiusUm': 10}
    assert not server.trajs_collide(a, b, 0, min_entry_um=0)
    assert server.trajs_collide({**a, 'spin_deg': 90}, b, 0, min_entry_um=0)


def test_contact_api_retains_spin(server, monkeypatch):
    seen = []
    monkeypatch.setattr(server, '_compute_probe_contact_profile', lambda probe, **kwargs: seen.append(probe) or {})
    result = server.app.test_client().post('/api/probe_contact_profile', json={
        'probes': [{'ap_mm': -2, 'ml_mm': -1, 'depthUm': 4000, 'spin_deg': 37.25, 'shankCount': 2}],
    })
    assert result.status_code == 200
    assert seen[0]['spin_deg'] == 37.25


@pytest.mark.parametrize('spin', ['NaN', float('inf'), True])
def test_contact_api_rejects_invalid_spin(server, spin):
    result = server.app.test_client().post('/api/probe_contact_profile', json={'probes': [{'depthUm': 4000, 'spin_deg': spin}]})
    assert result.status_code == 400
    assert 'spin' in str(result.get_json())


def test_contact_spin_alias_and_conflict(server):
    assert server._normalize_contact_probe_payload({'depthUm': 4000, 'spin': -90}, 0)['spin_deg'] == -90
    with pytest.raises(server.ContractError, match='must agree'):
        server._normalize_contact_probe_payload({'depthUm': 4000, 'spin': 90, 'spin_deg': 0}, 0)


@pytest.mark.parametrize('direction', [[0, 1, 0], [0, 0, 1], [0.3, 0.8, 0.2]])
def test_spin_preserves_pitch_and_zero_spin_basis(server, direction):
    raw_direction = np.asarray(direction, dtype=float)
    direction = raw_direction / np.linalg.norm(raw_direction)
    expected = np.array([0, 0, 1]) - direction * direction[2]
    if np.linalg.norm(expected) <= 1e-9:
        expected = np.cross(direction, [0, 1, 0])
    expected /= np.linalg.norm(expected)
    np.testing.assert_array_equal(server._probe_lateral_axis_um(raw_direction), expected)
    for spin in [37.25, -90, 450]:
        lateral = server._probe_lateral_axis_um(raw_direction, spin)
        assert np.linalg.norm(lateral) == pytest.approx(1)
        assert np.dot(lateral, direction) == pytest.approx(0, abs=1e-12)
        assert np.dot(lateral, expected) == pytest.approx(np.cos(np.deg2rad(spin)), abs=1e-12)


def test_contact_cache_separates_spin_and_resamples_anatomy(server, monkeypatch):
    annotation = np.zeros((32, 32, 32), dtype=np.uint32)
    annotation[20:23, :, :] = 42
    monkeypatch.setattr(server, 'atlas_annotation', annotation)
    monkeypatch.setattr(server, 'ATLAS_SHAPE', annotation.shape)
    monkeypatch.setattr(server, 'VOXEL_SIZE_UM', 25)
    monkeypatch.setattr(server, 'BREGMA_UM', np.array([400., 100., 400.]))
    monkeypatch.setattr(server, 'structure_map', {42: {'acronym': 'TEST', 'name': 'Synthetic contact region'}})
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'TEST': {'id': 42}})
    monkeypatch.setattr(server, 'contact_profile_cache', {})
    monkeypatch.setattr(server, '_compute_probe_risk_diagnostics', lambda *args, **kwargs: None)
    probe = {'ap_mm': 0, 'ml_mm': 0, 'depthUm': 300, 'radiusUm': 0,
             'shankCount': 2, 'shankPitchUm': 200, 'spin_deg': 0}
    unspun = server._compute_probe_contact_profile(probe)
    spun = server._compute_probe_contact_profile({**probe, 'spin_deg': 90})
    assert unspun['contactProfile'] == []
    assert [item['regionId'] for item in spun['contactProfile']] == [42]
    assert spun['physicalProbe']['spinDeg'] == 90
    assert len(server.contact_profile_cache) == 2
    assert server._compute_probe_contact_profile(probe) == unspun
    assert len(server.contact_profile_cache) == 2
