import numpy as np
import pytest

from test_optimizer_server import server


@pytest.fixture
def contact_atlas(server, monkeypatch):
    annotation = np.full((32, 32, 32), 42, dtype=np.uint32)
    monkeypatch.setattr(server, 'atlas_annotation', annotation)
    monkeypatch.setattr(server, 'ATLAS_SHAPE', annotation.shape)
    monkeypatch.setattr(server, 'VOXEL_SIZE_UM', 25)
    monkeypatch.setattr(server, 'BREGMA_UM', np.array([400., 100., 400.]))
    monkeypatch.setattr(server, 'structure_map', {42: {'acronym': 'TEST', 'name': 'Test structure'}})
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'TEST': {'id': 42}})
    monkeypatch.setattr(server, 'contact_profile_cache', {})
    monkeypatch.setattr(server, '_compute_probe_risk_diagnostics', lambda *args, **kwargs: None)
    return server


def test_swept_samples_keep_the_minority_hemisphere(contact_atlas):
    s = contact_atlas
    contacts = s._pick_structure_contacts_from_samples(s.atlas_annotation, np.array([400., 100., 450.]), [
        {'offset': [0, 0, offset], 'shankIndex': i}
        for i, offset in enumerate([-100, 0, 100])
    ])
    assert contacts[0]['hemisphere'] == 'right'
    assert contacts[0]['hemispheres'] == ['left', 'right']
    assert contacts[0]['hemisphereCounts'] == {'left': 1, 'right': 2}


def test_multishank_interval_and_aggregate_keep_all_sample_sides(contact_atlas):
    s = contact_atlas
    probe = {'ml_mm': 0.05, 'depthUm': 100, 'radiusUm': 0, 'shankCount': 3, 'shankPitchUm': 100}
    profile = s._compute_probe_contact_profile(probe)
    interval = profile['contactIntervals'][0]
    assert interval['hemisphere'] == 'right'
    assert interval['hemispheres'] == ['left', 'right']
    assert interval['hemisphereCounts'] == {'left': 5, 'right': 10}
    assert profile['contactProfile'][0]['hemispheres'] == ['left', 'right']
    interval['hemispheres'].clear()
    assert s._compute_probe_contact_profile(probe)['contactIntervals'][0]['hemispheres'] == ['left', 'right']


def test_cross_midline_oblique_interval_keeps_both_sides(contact_atlas):
    profile = contact_atlas._compute_probe_contact_profile({
        'ml_mm': -0.1, 'theta_deg': 45, 'phi_deg': 90, 'depthUm': 300,
        'radiusUm': 0, 'shankCount': 1,
    })
    assert len(profile['contactIntervals']) == 1
    assert profile['contactIntervals'][0]['hemispheres'] == ['left', 'right']
    assert sum(profile['contactIntervals'][0]['hemisphereCounts'].values()) == 13


@pytest.mark.parametrize('ml, sides', [(-0.1, ['left']), (0.1, ['right']), (0, ['midline'])])
def test_unilateral_and_midline_contact_are_not_expanded(contact_atlas, ml, sides):
    profile = contact_atlas._compute_probe_contact_profile({
        'ml_mm': ml, 'depthUm': 100, 'radiusUm': 0, 'shankCount': 1,
    })
    assert profile['contactIntervals'][0]['hemispheres'] == sides
    assert profile['contactProfile'][0]['hemispheres'] == sides


def test_filtered_short_interval_does_not_supply_contact_sides(contact_atlas):
    profile = contact_atlas._compute_probe_contact_profile(
        {'depthUm': 100, 'radiusUm': 0}, min_contact_length_um=126)
    assert profile['contactIntervals'] == []
    assert profile['contactProfile'] == []
    assert profile['contactRegionSet'] == []


def test_contact_api_retains_sample_sides(contact_atlas):
    response = contact_atlas.app.test_client().post('/api/probe_contact_profile', json={
        'probes': [{'ml_mm': 0.05, 'depthUm': 100, 'radiusUm': 0, 'shankCount': 3, 'shankPitchUm': 100}],
        'minContactLengthUm': 0,
    })
    assert response.status_code == 200
    row = response.get_json()['probes'][0]
    assert row['contactIntervals'][0]['hemispheres'] == ['left', 'right']
    assert row['contactProfile'][0]['hemispheres'] == ['left', 'right']
