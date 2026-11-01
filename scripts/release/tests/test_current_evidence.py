"""Tampered evidence must fail even if a ZIP itself remains readable."""
from copy import deepcopy
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('verify_current_evidence', ROOT / 'verify_current_evidence.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def archive(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name, content in entries:
            z.writestr(name, content)
    data.seek(0)
    return zipfile.ZipFile(data)


def binding(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def test_unchanged_member_accepted():
    with archive([('case/result.json', b'{}')]) as z:
        gate.verify_members(z, {'case/result.json': binding(b'{}')})


def test_changed_member_rejected():
    with archive([('case/result.json', b'[]')]) as z:
        with pytest.raises(ValueError, match='hash mismatch'):
            gate.verify_members(z, {'case/result.json': binding(b'{}')})


def test_member_length_mismatch_rejected():
    with archive([('case/result.json', b'{} ')]) as z:
        with pytest.raises(ValueError, match='byte count mismatch'):
            gate.verify_members(z, {'case/result.json': binding(b'{}')})


def test_unlisted_member_rejected():
    with archive([('extra.json', b'{}')]) as z:
        with pytest.raises(ValueError, match='member set mismatch'):
            gate.verify_members(z, {})


def test_duplicate_zip_entry_rejected():
    with pytest.warns(UserWarning, match='Duplicate name'):
        z = archive([('same', b'1'), ('same', b'1')])
    with z, pytest.raises(ValueError, match='Duplicate ZIP member'):
        gate.verify_members(z, {'same': binding(b'1')})


def test_case_colliding_zip_entries_rejected():
    with archive([('a', b'1'), ('A', b'1')]) as z:
        with pytest.raises(ValueError, match='Case-colliding'):
            gate.verify_members(z, {'a': binding(b'1'), 'A': binding(b'1')})


@pytest.mark.parametrize('name', ['../escape.json', 'C:/local.json', '/absolute', 'a\\b', 'a//b'])
def test_path_traversal_rejected(name):
    with archive([(name, b'{}')]) as z:
        with pytest.raises(ValueError, match='Unsafe ZIP path'):
            gate.verify_members(z, {name: binding(b'{}')})


def test_zip_symlink_rejected():
    info = zipfile.ZipInfo('link')
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with archive([(info, b'target')]) as z:
        with pytest.raises(ValueError, match='ZIP symlink'):
            gate.verify_members(z, {'link': binding(b'target')})


@pytest.mark.parametrize('data', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_json_rejected(data):
    with pytest.raises(ValueError):
        gate.strict_json(data)


def test_replaced_manifest_rejected(tmp_path):
    (tmp_path / 'CURRENT_EVIDENCE_MANIFEST.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Pinned evidence manifest changed'):
        gate.verify(tmp_path)


MODEL = {'candidateCount': 3, 'requiredTokens': ['A', 'B'], 'conflicts': [[0, 1]],
         'candidates': [{'coverageTokens': ['A']}, {'coverageTokens': ['B']}, {'coverageTokens': ['A', 'B']}]}
SOLUTION = {'selectedCandidateIndices': [2], 'exactMinimumCount': 1, 'solverStatus': 'proven_optimal',
            'optimalityGap': 0, 'timeout': False, 'certificate_valid': True}


def test_valid_certificate_accepted():
    gate.verify_selection(MODEL, SOLUTION)


@pytest.mark.parametrize('update', [
    {'selectedCandidateIndices': [0]},
    {'selectedCandidateIndices': [0, 1], 'exactMinimumCount': 2},
    {'selectedCandidateIndices': [2, 2], 'exactMinimumCount': 2},
    {'selectedCandidateIndices': [True]}, {'selectedCandidateIndices': [-1]},
    {'selectedCandidateIndices': [3]}, {'selectedCandidateIndices': []},
    {'exactMinimumCount': 2}, {'solverStatus': 'timeout'}, {'optimalityGap': 0.1},
    {'timeout': True}, {'certificate_valid': False},
])
def test_invalid_certificate_rejected(update):
    with pytest.raises(ValueError):
        gate.verify_selection(MODEL, dict(SOLUTION, **update))


@pytest.mark.parametrize('update', [
    {'candidateCount': 4}, {'requiredTokens': []}, {'requiredTokens': ['A', 'A']},
    {'conflicts': [[0, 1], [0, 1]]}, {'conflicts': [[0, 9]]}, {'conflicts': [[1, 0]]},
    {'conflicts': [[False, 1]]}, {'conflicts': [[0]]},
])
def test_invalid_model_rejected(update):
    with pytest.raises(ValueError):
        gate.verify_selection(dict(deepcopy(MODEL), **update), SOLUTION)
