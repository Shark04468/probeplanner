import importlib.util
from pathlib import Path
import sys
import gzip
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('public_projection', ROOT / 'public_evidence_projection.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.mark.parametrize('path', ['C:/Users/sample/file.json', r'C:\Users\sample\file.json'])
def test_home_is_removed(path):
    text, count = p.project_text(path)
    assert count == 1 and 'sample' not in text and 'Users' not in text
    assert not p.DRIVE.search(text)


def test_drives_normalized_and_numbers_retained():
    data = {'path': 'D:/data/a.nii', 'depth': 5.5, 'coordinates': [1.2, -3, 0], 'valid': False, 'missing': None}
    after, count = p.project_json(data)
    assert count == 1 and after['path'] == '<LOCAL_D>/data/a.nii'
    assert {k: v for k, v in after.items() if k != 'path'} == {k: v for k, v in data.items() if k != 'path'}


def test_key_collision_is_rejected():
    with pytest.raises(ValueError, match='collide'):
        p.project_json({'C:/Users/one/a': 1, 'C:/Users/two/a': 2})


def test_unchanged_data_is_byte_identical():
    data = b'{ "target": "CA1", "depth": 5.500 }'
    assert p.transform_member('request.json', data) == (data, 0)
    packed = gzip.compress(data)
    assert p.transform_member('x.exact_model.json.gz', packed) == (packed, 0)


@pytest.mark.parametrize('name', ['request.json', 'RESULTS.json', 'x.exact_model.json.gz'])
def test_protected_record_cannot_be_redacted(name):
    data = b'{"path":"D:/local"}'
    if name.endswith('.gz'):
        data = gzip.compress(data)
    with pytest.raises(ValueError, match='Protected scientific record'):
        p.transform_member(name, data)


def test_metadata_gzip_is_deterministic():
    data = gzip.compress(b'{"path":"D:/local", "depth": 2.5}')
    a, changes = p.transform_member('pipeline.json.gz', data)
    b, _ = p.transform_member('pipeline.json.gz', data)
    assert a == b and changes == 1
    assert json.loads(gzip.decompress(a)) == {'path': '<LOCAL_D>/local', 'depth': 2.5}


def test_projection_idempotent():
    data = b'{"path":"D:/local", "value":3}'
    once, _ = p.transform_member('metadata.json', data)
    twice, count = p.transform_member('metadata.json', once)
    assert twice == once and count == 0


def test_source_numeric_literals_not_modified():
    data = b"root = Path('D:/data')\nlimit = 100000\nangle = 3.5\n"
    after, count = p.transform_member('common.py', data)
    assert count == 1 and after == b"root = Path('<LOCAL_D>/data')\nlimit = 100000\nangle = 3.5\n"
