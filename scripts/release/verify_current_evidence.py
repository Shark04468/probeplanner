#!/usr/bin/env python3
"""Verify current archived validation bytes and finite-pool certificates, not release readiness."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import stat
import zipfile

from verify_source_v030 import safe_member, sha

MANIFEST_SHA256 = '56388ac1f7dc8e27d65b5f6296aeb2a1d94e8400333bc0c235fb29681a289386'
OPTIMIZER = '5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9'
COHORTS = {
    'retrospective': (89, 9, 'COHORT_MANIFEST.json',
        '5b7b2b588829fdcc822b2cfc4b5dd9d6875eafad53510b3ad7a741c3c60c914f',
        'c3f0bc4028e193dcfbac99b79cbab8edfb31c5f254ea6a6fdea325e3e68499c4'),
    'confirmation': (12, 1, 'MANIFEST.json',
        'cf4170e19b93bc15e26dff772d6f18ce19bd30792e3e4146d4ed9edf57520d07',
        '94094bfdbf31ebc407d2b53a5e87822f6e39327c4ee959722ed9963b62a312f1'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Non-finite JSON value: ' + value)
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)


def verify_members(archive, files):
    for name in files:
        require(safe_member(name), 'Unsafe ZIP path: ' + str(name))
    infos = archive.infolist()
    names = [item.filename for item in infos]
    require(len(names) == len(set(names)), 'Duplicate ZIP member')
    require(len(names) == len({n.casefold() for n in names}), 'Case-colliding ZIP members')
    require(set(names) == set(files), 'ZIP member set mismatch')
    require(sum(i.file_size for i in infos) < 512 * 1024 * 1024, 'Evidence exceeds declared size limit')
    for item in infos:
        name = item.filename
        require(safe_member(name) and not item.is_dir(), 'Unsafe ZIP path: ' + name)
        require(not stat.S_ISLNK(item.external_attr >> 16), 'ZIP symlink: ' + name)
        require(item.file_size < 100 * 1024 * 1024, 'Oversized ZIP member: ' + name)
        row = files[name]
        require(item.file_size == row['bytes'], 'ZIP byte count mismatch: ' + name)
        require(digest(archive.read(name)) == row['sha256'], 'ZIP content hash mismatch: ' + name)


def verify_selection(model, solved):
    candidates, required, conflicts = model['candidates'], model['requiredTokens'], model['conflicts']
    require(type(model['candidateCount']) is int and len(candidates) == model['candidateCount'] > 0,
            'Model candidate count mismatch')
    require(required and len(required) == len(set(required)), 'Empty or duplicate required tokens')
    edges = set()
    for edge in conflicts:
        require(isinstance(edge, list) and len(edge) == 2, 'Malformed conflict edge')
        a, b = edge
        require(type(a) is int and type(b) is int and 0 <= a < b < len(candidates), 'Invalid conflict indices')
        require((a, b) not in edges, 'Duplicate conflict edge')
        edges.add((a, b))
    chosen = solved['selectedCandidateIndices']
    require(chosen and all(type(i) is int and 0 <= i < len(candidates) for i in chosen), 'Invalid selection indices')
    require(len(chosen) == len(set(chosen)) == solved['exactMinimumCount'], 'Selection count mismatch')
    covered = set().union(*(set(candidates[i]['coverageTokens']) for i in chosen))
    require(set(required) <= covered, 'Selected model solution misses tokens')
    require(not any(a in chosen and b in chosen for a, b in edges), 'Selected model solution has conflicts')
    require(solved['solverStatus'] == 'proven_optimal' and solved['optimalityGap'] == 0
            and solved['timeout'] is False and solved['certificate_valid'] is True,
            'Recorded exact certificate is incomplete')


def verify_cohorts(archive, *, original_identities=None):
    """Check content contracts after the caller verifies the archive's actual bytes.

    Public projections carry separately pinned original identities. Those are
    provenance links, never hashes of the projected bytes being inspected.
    """
    def identity(name):
        if original_identities is not None:
            return original_identities[name]
        return digest(archive.read(name))

    summaries = {}
    def load(name):
        return strict_json(archive.read(name))
    for cohort, (positive_count, negative_count, manifest_name, manifest_hash, contract_hash) in COHORTS.items():
        prefix = cohort + '/results/'
        protocol_path = cohort + '/protocol/' + manifest_name
        require(identity(protocol_path) == manifest_hash, 'Frozen request manifest identity changed')
        protocol = load(protocol_path)
        require(identity(prefix + 'RUN_CONTRACT.json') == contract_hash, 'Run contract identity changed')
        results = load(prefix + 'RESULTS.json')
        require(results['contractSha256'] == contract_hash, 'Results/contract mismatch')
        cases = results['cases']
        require(len(cases) == positive_count + negative_count, 'Cohort count mismatch')
        ids = [r['case_id'] for r in cases]
        require(len(set(ids)) == len(ids), 'Duplicate case ID')
        require(ids == [r['case_id'] for r in protocol['cases']], 'Request/result case order mismatch')
        require(not results['comparisonErrors'], 'Comparison execution errors')
        positive = negative = 0
        for request, case in zip(protocol['cases'], cases):
            name = prefix + case['case_id'] + '/'
            raw_request = load(name + 'request.json')
            request_hash = digest(json.dumps(raw_request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
            require(request_hash == request['request_sha256'] == case['request_sha256'], 'Request identity mismatch')
            require(case['source_optimizer_sha256'] == OPTIMIZER, 'Case optimizer identity mismatch')
            require(type(case['positive']) is bool, 'Invalid case role')
            if not case['positive']:
                require(case['status'] == 'expected_fail_closed' and case['expected_negative_pass'], 'Negative control changed')
                negative += 1
                continue
            require(case['status'] == 'full' and case['strict_pass'] and case['planner_in_model_pool'],
                    'Positive plan not strict/full/in-pool')
            binding = load(name + 'model_binding.json')
            require(binding['optimizer_sha256'] == OPTIMIZER and binding['request_sha256'] == request_hash,
                    'Model source/request binding mismatch')
            container = archive.read(name + case['case_id'] + '.exact_model.json.gz')
            require(digest(container) == binding['model_file_sha256'], 'Model file binding mismatch')
            data = gzip.decompress(container)
            require(digest(data) == binding['model_uncompressed_sha256'], 'Model payload binding mismatch')
            model = strict_json(data)
            solved = load(name + 'exact_solution.json')
            require(solved == case['exact'], 'Per-case exact solution mismatch')
            verify_selection(model, solved)
            require(solved['exactMinimumCount'] == case['selected_count'], 'Planner/exact count mismatch')
            require(identity(name + 'pipeline.json.gz') == binding['pipeline_sha256'], 'Original pipeline binding mismatch')
            require(load(name + 'planner_audit.json')['strict_pass'], 'Planner strict audit failed')
            require(load(name + 'exact_selected_audit.json')['strict_pass'], 'Exact selection strict audit failed')
            graph = load(name + 'graph_audit.json')
            require(graph['pass'] and graph['discrepancy_count'] == 0 and not graph['discrepancies'], 'Graph audit failed')
            require(graph['pair_count'] == len(model['candidates']) * (len(model['candidates']) - 1) // 2,
                    'Graph audit pair count mismatch')
            require(graph['conflict_count'] == len(model['conflicts']), 'Graph conflict count mismatch')
            require(load(name + 'token_audit.json')['pass'], 'Token audit failed')
            positive += 1
        require((positive, negative) == (positive_count, negative_count), 'Cohort roles changed')
        summaries[cohort] = {'strictFull': positive, 'recordedExactCountMatches': positive, 'expectedNegative': negative}
    return summaries


def verify(directory):
    manifest_path = directory / 'CURRENT_EVIDENCE_MANIFEST.json'
    require(sha(manifest_path) == MANIFEST_SHA256, 'Pinned evidence manifest changed')
    manifest = strict_json(manifest_path.read_bytes())
    require(manifest['schemaVersion'] == 'probeplanner.current_validation_bundle.v1', 'Manifest schema mismatch')
    require(safe_member(manifest['archive']) and '/' not in manifest['archive'], 'Invalid archive filename')
    archive_path = directory / manifest['archive']
    require(sha(archive_path) == manifest['archiveSha256'], 'Archive hash mismatch')
    with zipfile.ZipFile(archive_path) as archive:
        verify_members(archive, manifest['files'])
        summaries = verify_cohorts(archive)
    return dict(evidenceIntegrityPassed=True, memberCount=len(manifest['files']), cohorts=summaries,
                archiveSha256=manifest['archiveSha256'], manifestSha256=MANIFEST_SHA256,
                publicDistributionApproved=manifest['publicDistributionApproved'], releaseAcceptance=False,
                scope='Pinned archived evidence and recorded feasible certificates only; this command does not rerun the MILP solver, planner or anatomy.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve previous verification')
    try:
        report = verify(args.evidence_dir)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        report = dict(evidenceIntegrityPassed=False, releaseAcceptance=False, error=f'{type(error).__name__}: {error}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
    print(json.dumps(report, indent=2))
    return 0 if report['evidenceIntegrityPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
