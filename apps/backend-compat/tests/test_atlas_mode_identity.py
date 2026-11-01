from test_optimizer_server import server


def test_status_query_does_not_switch_active_atlas(server, monkeypatch):
    monkeypatch.setattr(server, 'ACTIVE_ATLAS_MODE', 'allen_sdk')
    calls = []
    monkeypatch.setattr(server, '_apply_active_atlas_mode', lambda mode: calls.append(mode))
    monkeypatch.setattr(server, '_atlas_loader_alive', lambda: False)
    monkeypatch.setattr(server, '_ensure_atlas_loader', lambda **kwargs: None)
    response = server.app.test_client().get('/api/status?atlasMode=qiu2018&includeRegions=0')
    assert response.status_code == 200
    assert calls == []
    assert response.json['activeAtlasMode'] == 'allen_sdk'
    assert response.json['atlasModeMatchesRequest'] is False


def test_same_mode_post_does_not_force_loader_reset(server, monkeypatch):
    monkeypatch.setattr(server, 'ACTIVE_ATLAS_MODE', 'allen_sdk')
    monkeypatch.setattr(server, '_atlas_loader_alive', lambda: False)
    calls = []
    monkeypatch.setattr(server, '_ensure_atlas_loader', lambda **kwargs: calls.append(kwargs))
    response = server.app.test_client().post('/api/atlas/mode', json={'mode': 'allen_sdk'})
    assert response.status_code == 200
    assert response.json['changed'] is False
    assert calls == []


def test_bound_paper_runtime_rejects_different_atlas_mode(server, monkeypatch):
    monkeypatch.setattr(server, 'ACTIVE_ATLAS_MODE', 'allen_sdk')
    monkeypatch.setitem(server.app.config, 'PAPER_RUNTIME_IDENTITY',
        {'atlasMode': 'allen_sdk', 'profileContractSha256': 'bound-profile'})
    monkeypatch.setattr(server, '_atlas_loader_alive', lambda: False)
    calls = []
    monkeypatch.setattr(server, '_apply_active_atlas_mode', lambda mode: calls.append(mode))
    monkeypatch.setattr(server, '_ensure_atlas_loader', lambda **kwargs: None)
    response = server.app.test_client().post('/api/atlas/mode', json={'mode': 'qiu2018'})
    assert response.status_code == 409
    assert response.json['code'] == 'paper_profile_mode_locked'
    assert calls == []
