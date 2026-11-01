from copy import deepcopy
import json

from test_optimizer_server import server, _fake_candidate, _base_opt_payload, _full_constraints


def surface_status(server, monkeypatch, tmp_path, source):
    obj = tmp_path/'brain.obj'
    obj.write_text('v 0 0 0\n', encoding='ascii')
    manifest = {'layers': {'brain_surface': {'displayName': 'MRI brain surface', 'source': source,
                 'lods': {'0': {'path': str(obj)}}}}}
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    before = path.read_bytes()
    monkeypatch.setattr(server, '_load_mri_surface_manifest', lambda _: (manifest, path))
    result = server._build_mri_surface_mesh_status({})['layers']['brain_surface']
    assert path.read_bytes() == before
    return result


def test_annotation_surface_is_not_labelled_mri(server, monkeypatch, tmp_path):
    row = surface_status(server, monkeypatch, tmp_path, 'annotation')
    assert row['displayName'] == 'Atlas-derived brain surface'
    assert row['manifestDisplayName'] == 'MRI brain surface'
    assert row['provenanceType'] == 'atlas_annotation'
    assert row['independentRegistrationEvidence'] is False


def test_unknown_surface_source_is_not_inferred_from_legacy_name(server, monkeypatch, tmp_path):
    row = surface_status(server, monkeypatch, tmp_path, None)
    assert row['provenanceType'] == 'unverified'
    assert 'MRI' not in row['displayName']


def test_declared_mri_mask_is_not_independent_registration_validation(server, monkeypatch, tmp_path):
    row = surface_status(server, monkeypatch, tmp_path, 'mri_brain_mask')
    assert row['provenanceType'] == 'declared_mri_mask'
    assert not row['independentRegistrationEvidence']


def test_final_side_coverage_tracks_returned_probes(server):
    selected = [{'coveredRegions': ['A'], 'coveredRegionSides': {'A': ['left']}}]
    required, covered, missing, sides = server._final_coverage_state(selected, ['A'], 'bilateral_symmetric')
    assert required == {'A:left', 'A:right'} and covered == {'A:left'}
    assert missing == ['A'] and sides == [{'key': 'A', 'side': 'right'}]


def test_final_fallback_coverage_can_be_complete(server):
    required, covered, missing, sides = server._final_coverage_state(
        [{'coveredRegions': ['A']}, {'coveredRegions': ['B']}], ['A', 'B'], 'single_hemisphere')
    assert required == covered == {'A', 'B'} and missing == sides == []


def test_rounded_control_match_does_not_certify_external_geometry(server):
    candidate = _fake_candidate()
    outside = deepcopy(candidate)
    outside['entry_xyz'][0] += 100
    outside['tip_xyz'][0] += 100
    assert server._candidate_signature(outside) == server._candidate_signature(candidate)
    audit = {}
    result = server._bounded_exact_token_cover_selection([candidate], ['VISp'], 'single_hemisphere',
                                                          0, 0, [outside], audit=audit)
    assert result == [outside]
    assert audit['status'] == 'not_run_external_incumbent'


def test_cover_identity_binds_tokens_and_hardware(server):
    candidate = _fake_candidate()
    changed = deepcopy(candidate)
    changed['coveredRegions'] = ['CA1']
    assert server._candidate_cover_identity(candidate, ['VISp', 'CA1'], 'single_hemisphere') != server._candidate_cover_identity(changed, ['VISp', 'CA1'], 'single_hemisphere')
    changed = deepcopy(candidate)
    changed['radiusUm'] = 100
    assert server._candidate_cover_identity(candidate, ['VISp'], 'single_hemisphere') != server._candidate_cover_identity(changed, ['VISp'], 'single_hemisphere')


def test_pipeline_recomputes_coverage_after_repair(server, monkeypatch):
    candidate = _fake_candidate()
    candidate['coveredRegions'] = ['VISp', 'CA1']
    candidate['overlapVoxels'] = {'VISp': 20, 'CA1': 10}
    monkeypatch.setattr(server, 'generate_candidates_voxel', lambda *a, **k: [deepcopy(candidate)])
    monkeypatch.setattr(server, 'greedy_optimize', lambda t, c, *a: {'selected': [c[0]], 'uncovered': []})
    def repair(selected, **kwargs):
        changed = deepcopy(selected)
        changed[0]['coveredRegions'] = ['VISp']
        return changed, {'applied': True, 'repaired': 1}
    monkeypatch.setattr(server, '_auto_repair_selection', repair)
    payload = _base_opt_payload(_full_constraints())
    payload['targetKeys'] = ['VISp', 'CA1']
    payload['params'].update(candidateExpansionEnabled=False, strictFallbackDecompose=False,
                             robustOptimization=False, paretoEnabled=False)
    with server.app.app_context():
        result = server._run_optimize_pipeline(payload)
    assert result['uncovered'] == ['CA1']
    assert result['coveredCoverageUnits'] == 1 and result['requiredCoverageUnits'] == 2
    assert not result['tokenCoverSearch']['appliesToReturnedSelection']
