"""Portable replay must retain request, model and comparator semantics."""
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import replay_public_campaign as replay


def archive_of(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, value in members.items():
            archive.writestr(name, value if isinstance(value, bytes) else json.dumps(value))
    return zipfile.ZipFile(buffer)


def request_case(name='one'):
    request = dict(targetKeys=['CA1'], params=dict(computeWorkers=1))
    return dict(case_id=name, positive=True, request=request, request_sha256=replay.stable_hash(request))


def test_case_selection_preserves_request_and_manifest_order():
    cases = [request_case('a'), request_case('b')]
    with archive_of({'confirmation/protocol/MANIFEST.json': dict(cases=cases)}) as archive:
        actual = replay.select_cases(archive, ['confirmation'], ['confirmation/b', 'confirmation/a'])
    assert actual == [('confirmation', case) for case in cases]


@pytest.mark.parametrize('kind', ['empty', 'duplicate', 'unsafe', 'digest', 'role', 'unknown', 'duplicate_selection', 'duplicate_cohort'])
def test_rejects_invalid_selection(kind):
    cases = [request_case()]
    cohorts, selected = ['confirmation'], None
    if kind == 'empty':
        cases = []
    if kind == 'duplicate':
        cases *= 2
    if kind == 'unsafe':
        cases[0]['case_id'] = '../escape'
    if kind == 'digest':
        cases[0]['request']['targetKeys'] = ['DG']
    if kind == 'role':
        cases[0]['positive'] = 1
    if kind == 'unknown':
        selected = ['confirmation/missing']
    if kind == 'duplicate_selection':
        selected = ['confirmation/one', 'confirmation/one']
    if kind == 'duplicate_cohort':
        cohorts *= 2
    with archive_of({'confirmation/protocol/MANIFEST.json': dict(cases=cases)}) as archive:
        with pytest.raises(ValueError):
            replay.select_cases(archive, cohorts, selected)


@pytest.fixture
def outcome(tmp_path):
    item = dict(case_id='one', positive=True)
    folder = tmp_path / 'one'
    folder.mkdir()
    model = dict(requiredTokens=['CA1'], candidateCount=1, pipelineCandidateCount=1,
                 candidates=[dict(entryCcfUm=[1, 2, 3], tipCcfUm=[3, 4, 5], coverageTokens=['CA1'])], conflicts=[])
    pipeline = dict(candidatePreview=[dict(id=1, entry_xyz=[1, 2, 3], tip_xyz=[3, 4, 5], coveredRegions=['CA1'])],
                    selected=[dict(id=1, entry_xyz=[1, 2, 3], tip_xyz=[3, 4, 5], coveredRegions=['CA1'])])
    result = dict(full=True, strict_pass=True, coordinate_failures=0, candidate_count=1,
                  strict_candidate_count=1, selected_count=1, graph_audit=dict(pass_=True),
                  exact=dict(certificate_valid=True, planner_exact_match=True, exactMinimumCount=1, timeout=False))
    result['graph_audit'] = {'pass': True}
    comparisons = [dict(method='random_feasible', seed=i, status='evaluated', full=True, probes=1) for i in range(28)]
    prefix = 'confirmation/results/one/'
    frozen = {prefix + 'result.json': deepcopy(result),
              prefix + 'one.exact_model.json.gz': gzip.compress(json.dumps(model).encode(), mtime=1),
              prefix + 'pipeline.json.gz': gzip.compress(json.dumps(pipeline).encode()),
              prefix + 'comparison/ROWS.json': deepcopy(comparisons)}
    (folder / 'one.exact_model.json.gz').write_bytes(gzip.compress(json.dumps(model).encode(), mtime=2))
    (folder / 'pipeline.json.gz').write_bytes(gzip.compress(json.dumps(pipeline).encode()))
    return item, folder, result, comparisons, frozen


def compare(outcome):
    item, folder, result, comparisons, frozen = outcome
    with archive_of(frozen) as archive:
        return replay.compare_outcome(archive, 'confirmation', item, folder, result, comparisons)


def test_mathematical_identity_not_gzip_timestamp(outcome):
    assert compare(outcome)['passed'] is True


@pytest.mark.parametrize('field', ['coordinate_failures', 'candidate_count', 'strict_candidate_count', 'selected_count', 'strict_pass', 'full'])
def test_changed_outcome_fails(outcome, field):
    result = outcome[2]
    result[field] = not result[field] if isinstance(result[field], bool) else result[field] + 1
    assert compare(outcome)['passed'] is False


@pytest.mark.parametrize('field,value', [('certificate_valid', False), ('planner_exact_match', False), ('timeout', True), ('exactMinimumCount', 2)])
def test_changed_certificate_fails(outcome, field, value):
    outcome[2]['exact'][field] = value
    assert compare(outcome)['passed'] is False


def test_changed_model_fails(outcome):
    path = outcome[1] / 'one.exact_model.json.gz'
    model = json.loads(gzip.decompress(path.read_bytes()))
    model['conflicts'] = [[0, 0]]
    path.write_bytes(gzip.compress(json.dumps(model).encode()))
    assert compare(outcome)['passed'] is False


def test_changed_selected_geometry_fails(outcome):
    path = outcome[1] / 'pipeline.json.gz'
    pipeline = json.loads(gzip.decompress(path.read_bytes()))
    pipeline['selected'][0]['tip_xyz'][0] += 1
    path.write_bytes(gzip.compress(json.dumps(pipeline).encode()))
    assert compare(outcome)['passed'] is False


def test_changed_baseline_result_fails(outcome):
    outcome[3][0]['probes'] = 2
    assert compare(outcome)['passed'] is False


def test_fewer_comparator_rows_fail(outcome):
    outcome[3].pop()
    assert compare(outcome)['passed'] is False


def test_runtime_variation_does_not_change_numerical_identity(outcome):
    outcome[3][0]['selector_seconds'] = 123.45
    assert compare(outcome)['passed'] is True


@pytest.mark.parametrize('code,passed', [('candidate_generation_empty', True), ('other_error', False)])
def test_negative_requires_same_expected_rejection(tmp_path, code, passed):
    item = dict(case_id='negative', positive=False, old_rejection_code='candidate_generation_empty')
    previous = dict(status='expected_fail_closed', error_code='candidate_generation_empty')
    current = dict(status='expected_fail_closed', expected_negative_pass=True, error_code=code)
    with archive_of({'confirmation/results/negative/result.json': previous}) as archive:
        actual = replay.compare_outcome(archive, 'confirmation', item, tmp_path, current, [])
    assert actual['passed'] is passed


def test_staged_scientific_modules_are_byte_identical(tmp_path):
    source = tmp_path / 'source'
    adapter = source / 'scripts/release/replay_bindings.py'
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(b'# path adapter only\n')
    contents = {'retrospective/protocol/' + name: ('# frozen ' + name + '\n').encode() for name in replay.MODULES}
    out = tmp_path / 'modules'
    with archive_of(contents) as archive:
        hashes = replay.stage_modules(source, archive, out)
    for name in replay.MODULES:
        assert (out / name).read_bytes() == contents['retrospective/protocol/' + name]
        assert replay.sha(out / name) == hashes[name]
    assert (out / 'common.py').read_bytes() == adapter.read_bytes()


def test_existing_run_cannot_be_overwritten(tmp_path):
    existing = tmp_path / 'previous'
    existing.mkdir()
    (existing / 'SUMMARY.json').write_text('prior failure')
    with pytest.raises(ValueError, match='Output exists'):
        replay.run(tmp_path / 'source', tmp_path / 'evidence', tmp_path / 'profile', existing, [], None)
    assert (existing / 'SUMMARY.json').read_text() == 'prior failure'


def test_output_cannot_modify_input_tree(tmp_path):
    with pytest.raises(ValueError, match='separate'):
        replay.run(tmp_path, tmp_path / 'evidence', tmp_path / 'profile', tmp_path / 'new', [], None)
    assert not (tmp_path / 'new').exists()
