"""Deterministic path-only projections; never overwrite original evidence."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import zipfile

from verify_current_evidence import require, strict_json, verify


USER_HOME = re.compile(r'(?i)(?<![a-z])[a-z]:[\\/]+Users[\\/]+[^\\/\s"\'<>]+')
DRIVE = re.compile(r'(?i)(?<![a-z])([a-z]):[\\/]+')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def project_text(value):
    homes = len(USER_HOME.findall(value))
    value = USER_HOME.sub('<USER_HOME>/', value)
    drives = len(DRIVE.findall(value))
    value = DRIVE.sub(lambda m: '<LOCAL_' + m.group(1).upper() + '>/', value)
    return value, homes + drives


def project_json(value):
    """Preserve numeric/boolean/null leaves and list order; reject key collisions."""
    if isinstance(value, str):
        return project_text(value)
    if isinstance(value, list):
        rows = [project_json(v) for v in value]
        return [r[0] for r in rows], sum(r[1] for r in rows)
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            new_key, key_count = project_text(key)
            require(new_key not in result, 'Path projection would collide JSON keys')
            result[new_key], changes = project_json(item)
            count += key_count + changes
        return result, count
    require(value is None or type(value) in (bool, int, float), 'Unsupported JSON value')
    return value, 0


def protected_member(name):
    return (name.endswith('.exact_model.json.gz') or Path(name).name in {
        'request.json', 'RESULTS.json', 'exact_solution.json', 'model_binding.json',
        'graph_audit.json', 'token_audit.json', 'planner_audit.json',
        'candidate_audits.json.gz', 'all_pair_audits.json.gz', 'exact_selected_audit.json',
    })


def transform_member(name, data):
    packed = name.endswith('.gz')
    raw = gzip.decompress(data) if packed else data
    is_json = name.endswith(('.json', '.json.gz'))
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return data, 0
    if is_json:
        original = strict_json(text)
        projected, count = project_json(original)
        if not count:
            return data, 0
        transformed = (json.dumps(projected, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        require(strict_json(transformed) == projected, 'Projection serialization mismatch')
    else:
        transformed_text, count = project_text(text)
        if not count:
            return data, 0
        transformed = transformed_text.encode('utf-8')
    require(not protected_member(name), 'Protected scientific record contains a path: ' + name)
    require(not DRIVE.search(transformed.decode('utf-8')), 'Path remains after projection')
    return (gzip.compress(transformed, mtime=0) if packed else transformed), count


def build(private_dir, output):
    checked = verify(private_dir)
    require(checked['evidenceIntegrityPassed'], 'Original evidence verification failed')
    require(not output.exists(), 'Output must not exist')
    output.mkdir(parents=True)
    original_manifest = strict_json((private_dir / 'CURRENT_EVIDENCE_MANIFEST.json').read_bytes())
    source = private_dir / original_manifest['archive']
    target = output / 'PUBLIC_VALIDATION_EVIDENCE.zip'
    files = {}
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as public:
        for name in sorted(original.namelist()):
            before = original.read(name)
            after, changes = transform_member(name, before)
            info = zipfile.ZipInfo(name, (2026, 11, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            public.writestr(info, after)
            files[name] = dict(sha256=digest(after), bytes=len(after), originalSha256=digest(before),
                               originalBytes=len(before), pathSubstitutions=changes,
                               protectedScientificRecord=protected_member(name))
    record = dict(schemaVersion='probeplanner.public_evidence_projection.v1', archive=target.name,
        archiveSha256=digest(target.read_bytes()), originalArchiveSha256=checked['archiveSha256'],
        originalManifestSha256=checked['manifestSha256'], files=files,
        projectionScriptSha256=digest(Path(__file__).read_bytes()),
        isOriginalFrozenManifest=False, originalEvidenceUnchanged=True,
        numericalAndRequestDataChanged=False, releaseAcceptance=False,
        scope='Explicit path-only public projection. Original hashes remain historical identities; projected bytes have separate hashes. Not an original frozen manifest or a portable campaign runner.')
    with (output / 'PUBLIC_EVIDENCE_MANIFEST.json').open('x', encoding='utf-8') as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write('\n')
    require(digest(source.read_bytes()) == checked['archiveSha256'], 'Original archive changed')
    return record
