"""The public projection must not silently become replacement experimental evidence."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_projection_lineage import check_json, check_text
from verify_public_evidence import verify, verify_ledger


def test_allowed_path_change_preserves_numeric_data():
    before = {'path': 'C:/Users/alice/model.nii', 'count': 3, 'x': [-0.0, 2.5, True, None]}
    after = dict(before, path='<USER_HOME>//model.nii')
    assert check_json(before, after) == 1


@pytest.mark.parametrize('before,after', [
    (1, 2), (1, True), (1, 1.0), (-0.0, 0.0), (False, True), (None, False),
    ([1, 2], [2, 1]), ([1], [1, 2]), ({'a': 1}, {'b': 1}),
    ({'a': 1, 'b': 2}, {'b': 2, 'a': 1}),
    ('target_CA1', 'target_CA3'), ('D:/data', '<LOCAL_E>/data'),
    ('C:/Users/alice/data', '<USER_HOME>//different'),
])
def test_non_path_changes_rejected(before, after):
    with pytest.raises(ValueError):
        check_json(before, after)


def test_text_retains_all_non_path_characters():
    assert check_text('file=D:\\data\\model; n=4', 'file=<LOCAL_D>/data\\model; n=4') == 1
    with pytest.raises(ValueError):
        check_text('file=D:/data; n=4', 'file=<LOCAL_D>/data; n=5')


def test_public_manifest_replacement_rejected(tmp_path):
    (tmp_path / 'PUBLIC_EVIDENCE_MANIFEST.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Pinned public manifest changed'):
        verify(tmp_path)


def ledger():
    from verify_public_evidence import ORIGINAL_ARCHIVE_SHA256, ORIGINAL_MANIFEST_SHA256
    return dict(schemaVersion='probeplanner.public_evidence_projection.v1',
                originalManifestSha256=ORIGINAL_MANIFEST_SHA256, originalArchiveSha256=ORIGINAL_ARCHIVE_SHA256,
                isOriginalFrozenManifest=False, originalEvidenceUnchanged=True,
                numericalAndRequestDataChanged=False, releaseAcceptance=False, archive='public.zip',
                files={'a/request.json': dict(sha256='a' * 64, originalSha256='a' * 64, bytes=2,
                       originalBytes=2, pathSubstitutions=0, protectedScientificRecord=True)})


@pytest.mark.parametrize('field,value', [('isOriginalFrozenManifest', True),
    ('originalEvidenceUnchanged', False), ('numericalAndRequestDataChanged', True),
    ('releaseAcceptance', True), ('originalArchiveSha256', '0' * 64),
    ('originalManifestSha256', '0' * 64), ('archive', '../public.zip')])
def test_misleading_public_scope_rejected(field, value):
    value_ = ledger()
    value_[field] = value
    with pytest.raises(ValueError, match='scope|identity|filename'):
        verify_ledger(value_)


@pytest.mark.parametrize('changes,match', [
    ({'sha256': 'invalid'}, 'digest'),
    ({'pathSubstitutions': True}, 'Invalid count'),
    ({'originalBytes': 3}, 'size mismatch'),
    ({'protectedScientificRecord': False}, 'classification'),
    ({'sha256': 'b' * 64}, 'Change ledger'),
    ({'sha256': 'b' * 64, 'pathSubstitutions': 1}, 'Protected record changed'),
])
def test_inconsistent_lineage_rejected(changes, match):
    value = deepcopy(ledger())
    value['files']['a/request.json'].update(changes)
    with pytest.raises(ValueError, match=match):
        verify_ledger(value)
