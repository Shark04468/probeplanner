#!/usr/bin/env python3
"""Inspect the pinned public evidence projection, optionally re-solving its models."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import zipfile

from public_evidence_projection import protected_member
from verify_current_evidence import (
    MANIFEST_SHA256 as ORIGINAL_MANIFEST_SHA256,
    digest, require, strict_json, verify_cohorts, verify_members,
)
from verify_source_v030 import safe_member, sha

PUBLIC_MANIFEST_SHA256 = 'ff539dcc0781061acd4a8d15f0e2b3d1fc8d66d1eb2c603db2d5f142b3e614a2'
ORIGINAL_ARCHIVE_SHA256 = 'c230d0c67ae064de5f45a46e40a133989126f3c9ca5d0c230442e911a0159ab3'
HASH = re.compile(r'[0-9a-f]{64}')


def verify_ledger(manifest):
    require(manifest['schemaVersion'] == 'probeplanner.public_evidence_projection.v1', 'Public schema mismatch')
    require(manifest['originalManifestSha256'] == ORIGINAL_MANIFEST_SHA256, 'Original manifest identity mismatch')
    require(manifest['originalArchiveSha256'] == ORIGINAL_ARCHIVE_SHA256, 'Original archive identity mismatch')
    for field, expected in [('isOriginalFrozenManifest', False), ('originalEvidenceUnchanged', True),
                            ('numericalAndRequestDataChanged', False), ('releaseAcceptance', False)]:
        require(manifest[field] is expected, 'Invalid projection scope: ' + field)
    require(safe_member(manifest['archive']) and '/' not in manifest['archive'], 'Invalid archive filename')
    changed = protected = 0
    for name, row in manifest['files'].items():
        require(safe_member(name), 'Unsafe public member')
        for field in ('sha256', 'originalSha256'):
            require(isinstance(row[field], str) and HASH.fullmatch(row[field]), 'Invalid digest')
        for field in ('bytes', 'originalBytes', 'pathSubstitutions'):
            require(type(row[field]) is int and row[field] >= 0, 'Invalid count: ' + field)
        require(row['protectedScientificRecord'] is protected_member(name), 'Protected-member classification changed')
        identical = row['sha256'] == row['originalSha256']
        require(identical == (row['pathSubstitutions'] == 0), 'Change ledger mismatch')
        if identical:
            require(row['bytes'] == row['originalBytes'], 'Unchanged member size mismatch')
        if row['protectedScientificRecord']:
            require(identical, 'Protected record changed')
            protected += 1
        changed += int(not identical)
    require(len(manifest['files']) == 2444 and changed == 428 and protected == 1022,
            'Public member counts changed')
    return {'files': len(manifest['files']), 'pathProjectedFiles': changed, 'byteProtectedFiles': protected}


def verify(directory, *, resolve_models=False, time_limit_sec=120.0):
    manifest_path = directory / 'PUBLIC_EVIDENCE_MANIFEST.json'
    require(sha(manifest_path) == PUBLIC_MANIFEST_SHA256, 'Pinned public manifest changed')
    manifest = strict_json(manifest_path.read_bytes())
    counts = verify_ledger(manifest)
    archive_path = directory / manifest['archive']
    require(sha(archive_path) == manifest['archiveSha256'], 'Public archive hash mismatch')
    rows = []
    with zipfile.ZipFile(archive_path) as archive:
        verify_members(archive, manifest['files'])
        original_identities = {name: row['originalSha256'] for name, row in manifest['files'].items()}
        cohorts = verify_cohorts(archive, original_identities=original_identities)
        if resolve_models:
            from verify_exact_oracle_models import solve_model
            require(time_limit_sec > 0, 'Time limit must be positive')
            for cohort in cohorts:
                prefix = cohort + '/results/'
                results = strict_json(archive.read(prefix + 'RESULTS.json'))
                for case in results['cases']:
                    if not case['positive']:
                        continue
                    name = prefix + case['case_id'] + '/' + case['case_id'] + '.exact_model.json.gz'
                    raw = archive.read(name)
                    status, count, elapsed, message = solve_model(strict_json(gzip.decompress(raw)), time_limit_sec)
                    expected = case['exact']['exactMinimumCount']
                    rows.append(dict(cohort=cohort, caseId=case['case_id'], modelSha256=digest(raw),
                                     solverStatus=status, exactMinimum=count, recordedMinimum=expected,
                                     plannerCount=case['selected_count'], runtimeSec=elapsed, solverMessage=message,
                                     passed=status == 'proven_optimal' and count == expected == case['selected_count']))
    failed = [r for r in rows if not r['passed']]
    solver_passed = not failed and len(rows) == 101 if resolve_models else None
    return dict(publicEvidenceIntegrityPassed=True, counts=counts, cohorts=cohorts,
                manifestSha256=PUBLIC_MANIFEST_SHA256, archiveSha256=manifest['archiveSha256'],
                originalManifestSha256=ORIGINAL_MANIFEST_SHA256, originalArchiveSha256=ORIGINAL_ARCHIVE_SHA256,
                solverRequested=resolve_models, solverPassed=solver_passed, solverCases=rows,
                solverTimeouts=sum(r['solverStatus'] == 'timeout' for r in rows),
                releaseAcceptance=False, publicDistributionApproved=False,
                scope='Actual public bytes verified against a pinned derived manifest. Original identities are provenance links, not hashes of projected bytes. Exact results concern archived finite candidate pools only.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resolve-models', action='store_true')
    parser.add_argument('--time-limit-sec', type=float, default=120.0)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve previous verification')
    try:
        result = verify(args.evidence_dir, resolve_models=args.resolve_models, time_limit_sec=args.time_limit_sec)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        result = dict(publicEvidenceIntegrityPassed=False, releaseAcceptance=False,
                      error=f'{type(error).__name__}: {error}')
    result['verifierSha256'] = sha(Path(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'solverCases'}, indent=2))
    return 0 if result['publicEvidenceIntegrityPassed'] and result.get('solverPassed') is not False else 1


if __name__ == '__main__':
    raise SystemExit(main())
