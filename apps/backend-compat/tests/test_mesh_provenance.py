from test_optimizer_server import server


def test_base_fallback_lod_is_preserved_on_cache_hit_and_cors(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, '_mesh_cache', {})
    monkeypatch.setattr(server, 'MESH_BASE_DIR', tmp_path)
    monkeypatch.setattr(server, 'MESH_LOD_DIR', tmp_path)
    (tmp_path / '382.obj').write_text('v 1 2 3\n', encoding='utf-8')
    client = server.app.test_client()
    for cache_source in ['disk:fallback_base', 'memory']:
        response = client.get('/api/allen_mesh_lod/382?lod=2', headers={'Origin': 'http://127.0.0.1:5039'})
        assert response.status_code == 200
        assert response.headers['X-Lod'] == '0'
        assert response.headers['X-Lod-Requested'] == '2'
        assert response.headers['X-Lod-Fallback'] == '1'
        assert response.headers['X-Cache-Source'] == cache_source
        assert 'X-Lod' in response.headers['Access-Control-Expose-Headers']
        assert response.data == b'v 1 2 3\n'


def test_real_lod_retains_its_identity_in_memory(server, monkeypatch, tmp_path):
    monkeypatch.setattr(server, '_mesh_cache', {})
    monkeypatch.setattr(server, 'MESH_LOD_DIR', tmp_path)
    (tmp_path / '382.lod2.obj').write_text('v 4 5 6\n', encoding='utf-8')
    assert server._fetch_mesh_obj_resolved(382, 2) == ('v 4 5 6\n', 2, 'disk')
    assert server._fetch_mesh_obj_resolved(382, 2) == ('v 4 5 6\n', 2, 'memory')


def test_region_ontology_paths_survive_status_and_catalog_serialization(server, monkeypatch):
    monkeypatch.setattr(server, 'PLANNER_REGIONS', {'CA1': {'id': 382}})
    monkeypatch.setattr(server, 'acronym_record_map', {'ca1': {
        'id': 382, 'acronym': 'CA1', 'structure_id_path': '/997/8/382/',
    }})
    monkeypatch.setattr(server, 'structure_map', {382: {'voxel_count': 42, 'n_subregions': 3}})
    catalog = server._build_region_catalog()
    assert catalog['CA1']['structureIdPath'] == [997, 8, 382]
    for endpoint in ['/api/regions', '/api/regions_unity']:
        rows = server.app.test_client().get(endpoint).get_json()['regions']
        row = next(row for row in rows if row['key'] == 'CA1')
        assert row['structureIdPath'] == [997, 8, 382]
        assert row['voxelCount'] == 42
