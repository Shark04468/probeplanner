from copy import deepcopy

import pytest

from test_optimizer_server import server, _fake_candidate


def test_unity_catalog_reports_absent_regions_without_removing_ontology(server, monkeypatch):
    monkeypatch.setattr(server, '_build_region_catalog', lambda scope: {
        'present': {'structureId': 1, 'presentInVolume': True, 'voxel_count': 3},
        'absent': {'structureId': 2, 'presentInVolume': False, 'unavailableReason': 'not_present_in_current_annotation'},
    })
    rows = server.app.test_client().get('/api/regions_unity').get_json()['regions']
    assert len(rows) == 2
    assert rows[0]['volumeAvailabilityKnown'] and rows[0]['presentInVolume']
    assert not rows[1]['presentInVolume']
    assert rows[1]['unavailableReason'] == 'not_present_in_current_annotation'


def test_runtime_identity_is_bound_before_response_hashing(server):
    identity = {'optimizerSha256': 'a' * 64, 'profileContractSha256': 'b' * 64}
    server.app.config['PAPER_RUNTIME_IDENTITY'] = identity
    response = {'ok': True}
    server._attach_payload_hashes({}, response)
    assert response['runtimeIdentity'] == identity
    first = response['responseHash']
    response['runtimeIdentity']['optimizerSha256'] = 'c' * 64
    assert server._hash_payload(server._payload_without_response_hash(response)) != first
    assert server.app.config['PAPER_RUNTIME_IDENTITY'] == identity


def saved_plan(server):
    candidate = _fake_candidate()
    response = {'ok': True, 'selected': [candidate], 'runtimeIdentity': {'optimizerSha256': 'old-source'}}
    server._attach_payload_hashes({}, response)
    server._persist_replay_record('saved-plan', {}, response)
    return candidate, response


def test_export_binds_saved_plan_not_current_exporter_identity(server):
    candidate, saved = saved_plan(server)
    server.app.config['PAPER_RUNTIME_IDENTITY'] = {'optimizerSha256': 'new-exporter'}
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [candidate], 'planRequestId': 'saved-plan', 'planResponseHash': saved['responseHash'],
    })
    assert result.status_code == 200
    data = result.get_json()['pinpointData']
    assert data['_coordinateSystem'] == 'pinpoint_ap_posterior_positive_mm'
    assert data['probes'][0]['ap'] == -candidate['ap_mm']
    assert data['_sourcePlan']['runtimeIdentity']['optimizerSha256'] == 'old-source'
    assert data['_exportRuntime']['optimizerSha256'] == 'new-exporter'


@pytest.mark.parametrize('change', ['geometry', 'hash', 'request_path'])
def test_export_rejects_unbound_plan_claim(server, change):
    candidate, saved = saved_plan(server)
    body = {'trajectories': [deepcopy(candidate)], 'planRequestId': 'saved-plan', 'planResponseHash': saved['responseHash']}
    if change == 'geometry':
        body['trajectories'][0]['ap_mm'] += 1
    elif change == 'hash':
        body['planResponseHash'] = 'wrong'
    else:
        body['planRequestId'] = '../saved-plan'
    assert server.app.test_client().post('/api/export_pinpoint', json=body).status_code == 400


def test_unlinked_export_never_invents_validation(server):
    result = server.app.test_client().post('/api/export_pinpoint', json={'trajectories': [_fake_candidate()]})
    assert result.status_code == 200
    assert result.get_json()['pinpointData']['_sourcePlan']['status'] == 'unverified_geometry'


def test_export_preserves_per_probe_hardware_and_spin(server):
    candidate = {**_fake_candidate(), 'probeType': 'custom two shank', 'shankCount': 2,
                 'shankPitchUm': 312.5, 'radiusUm': 22, 'shankWidthUm': 44,
                 'shankThicknessUm': 18, 'spin_deg': 37.25}
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [candidate], 'probeType': 'NP2.0-4sh',
    })
    assert result.status_code == 200
    probe = result.get_json()['pinpointData']['probes'][0]
    for key in ['probeType', 'shankCount', 'shankPitchUm', 'radiusUm', 'shankWidthUm', 'shankThicknessUm']:
        assert probe[key] == candidate[key]
    assert probe['spin'] == candidate['spin_deg']


@pytest.mark.parametrize('field,value', [('spin_deg', 40), ('shankCount', 4),
    ('shankPitchUm', 250), ('radiusUm', 45), ('probeType', 'different model')])
def test_linked_export_rejects_changed_hardware(server, field, value):
    candidate = {**_fake_candidate(), 'probeType': 'custom', 'shankCount': 2,
                 'shankPitchUm': 300, 'radiusUm': 20, 'spin_deg': 15}
    saved = {'ok': True, 'selected': [candidate]}
    server._attach_payload_hashes({}, saved)
    server._persist_replay_record('hardware-plan', {}, saved)
    changed = {**candidate, field: value}
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [changed], 'planRequestId': 'hardware-plan', 'planResponseHash': saved['responseHash'],
    })
    assert result.status_code == 400


@pytest.mark.parametrize('fields', [{'shankCount': 1.5}, {'shankPitchUm': -1},
    {'radiusUm': 'NaN'}, {'shankCount': 2, 'shanks': 4}])
def test_export_rejects_invalid_hardware(server, fields):
    result = server.app.test_client().post('/api/export_pinpoint', json={
        'trajectories': [{**_fake_candidate(), **fields}],
    })
    assert result.status_code == 400
