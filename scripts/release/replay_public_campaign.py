#!/usr/bin/env python3
"""Regenerate archived requests using frozen scientific modules and a public profile."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import traceback
import zipfile

from prepare_public_profile import serialize, write_bytes
from public_profile_bundle import inspect_profile
from verify_current_evidence import require, strict_json
from verify_public_evidence import verify as verify_evidence
from verify_source_v030 import ROOT as SOURCE, sha, safe_member, verify as verify_source

PUBLIC_PROFILE_SHA256 = '3580f40eb42b475319ea62796ef8c7b8789b1d1110a9ca4638f8e2168b09c677'
OLD_ARCHIVE_SHA256 = '947ec62f04a866ce024d704bd5284b96c263990e2ef26c95e470ebdf47c81e62'
OLD_MANIFEST_SHA256 = '7bb485919783dfb608ae4c1337a1abad4f2b417de2780d783bd1281e978a8783'
MODULES = ('runtime.py', 'case_engine.py', 'geometry_audit.py', 'coordinate_audit.py',
           'comparison.py', 'solver_worker.py')
COHORT_MANIFESTS = {'retrospective': 'COHORT_MANIFEST.json', 'confirmation': 'MANIFEST.json'}
HISTORICAL_ROOT = 'paper_final_validation/'
ORACLE_ROOT = HISTORICAL_ROOT + '03_exact_probe_count_oracle/'
COMPARISON_FIELDS = ('case_id', 'cohort', 'method', 'seed', 'status', 'probes', 'full',
                    'strict', 'strict_in_pool', 'members_of_pool', 'missing',
                    'pair_violations', 'numerical_pass', 'individual_pass', 'token_match')
CANDIDATE_FIELDS = ('id', 'ap_mm', 'ml_mm', 'depthUm', 'ap_mm_corrected', 'ml_mm_corrected',
    'depthUm_corrected', 'theta_deg', 'phi_deg', 'entry_xyz', 'tip_xyz', 'dir', 'coveredRegions',
    'coveredRegionSides', 'overlapVoxels', 'radiusUm', 'shanks', 'shankCount', 'shankPitchUm',
    'brainBoundsDiagnostics', 'trajectoryProfile', 'endpointTargeting')


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def project(items, fields):
    return [{key: item.get(key) for key in fields} for item in items]


def select_cases(archive, cohorts, identifiers):
    require(cohorts and len(cohorts) == len(set(cohorts)), 'Duplicate or empty cohort selection')
    require(not identifiers or len(identifiers) == len(set(identifiers)), 'Duplicate requested case')
    selected = []
    for cohort in cohorts:
        require(cohort in COHORT_MANIFESTS, 'Unknown cohort')
        manifest = strict_json(archive.read(cohort + '/protocol/' + COHORT_MANIFESTS[cohort]))
        cases = manifest['cases']
        require(len({c['case_id'] for c in cases}) == len(cases), 'Duplicate archived case ID')
        for case in cases:
            name = case['case_id']
            require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', name)), 'Unsafe case ID')
            require(type(case['positive']) is bool, 'Invalid case role')
            require(stable_hash(case['request']) == case['request_sha256'], 'Request digest mismatch')
            if not identifiers or cohort + '/' + name in identifiers:
                selected.append((cohort, case))
    require(bool(selected), 'Empty case selection')
    if identifiers:
        require({c + '/' + i['case_id'] for c, i in selected} == set(identifiers), 'Unknown requested case')
    return selected


def stage_historical(source, output, cases):
    directory = source / 'release-evidence/v0.2.1-paper'
    manifest_path = directory / 'IMMUTABLE_EVIDENCE_MANIFEST.json'
    require(sha(manifest_path) == OLD_MANIFEST_SHA256, 'Historical manifest changed')
    manifest = strict_json(manifest_path.read_bytes())
    archive_path = directory / manifest['archive']
    require(sha(archive_path) == OLD_ARCHIVE_SHA256 == manifest['archiveSha256'], 'Historical archive changed')
    expected = {row['path']: row for row in manifest['files']}
    names = {ORACLE_ROOT + 'run_exact_oracle.py', ORACLE_ROOT + 'CONFIG.json'}
    for _, item in cases:
        if item.get('old_exact') is not None:
            folder = 'targeted_mirror_source_exact_models' if item['exact_cohort'] == 'historical48' else 'v021_v5_exact_models'
            names.add(HISTORICAL_ROOT + '07_v021_revision/' + folder + '/' + item['case_id'] + '.exact_model.json.gz')
    hashes = {}
    with zipfile.ZipFile(archive_path) as archive:
        for name in sorted(names):
            require(safe_member(name) and name in expected, 'Unbound historical dependency')
            raw = archive.read(name)
            digest = hashlib.sha256(raw).hexdigest()
            require(digest == expected[name]['sha256'] and len(raw) == expected[name]['size'], 'Historical dependency mismatch')
            write_bytes(output / name, raw)
            hashes[name] = digest
    return hashes


def stage_modules(source, archive, output):
    hashes = {}
    for name in MODULES:
        raw = archive.read('retrospective/protocol/' + name)
        write_bytes(output / name, raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    raw = (source / 'scripts/release/replay_bindings.py').read_bytes()
    write_bytes(output / 'common.py', raw)
    hashes['common.py'] = hashlib.sha256(raw).hexdigest()
    return hashes


def compare_outcome(archive, cohort, item, folder, result, comparisons):
    prefix = cohort + '/results/' + item['case_id'] + '/'
    expected = strict_json(archive.read(prefix + 'result.json'))
    if not item['positive']:
        passed = (result.get('status') == expected['status'] == 'expected_fail_closed'
                  and result.get('expected_negative_pass') is True
                  and result.get('error_code') == expected['error_code'] == item['old_rejection_code'])
        return dict(passed=passed, negativeRejectionMatches=passed)
    filename = item['case_id'] + '.exact_model.json.gz'
    actual_model = strict_json(gzip.decompress((folder / filename).read_bytes()))
    old_model = strict_json(gzip.decompress(archive.read(prefix + filename)))
    # The original exporter has no timing/path metadata in this mathematical model.
    model_equal = actual_model == old_model
    current_pipeline = strict_json(gzip.decompress((folder / 'pipeline.json.gz').read_bytes()))
    previous_pipeline = strict_json(gzip.decompress(archive.read(prefix + 'pipeline.json.gz')))
    previous_comparisons = strict_json(archive.read(prefix + 'comparison/ROWS.json'))
    exact = result.get('exact') or {}
    checks = dict(
        full=result.get('full') is True, strict=result.get('strict_pass') is True,
        noCoordinateFailures=result.get('coordinate_failures') == 0,
        candidateCountMatches=result.get('candidate_count') == expected['candidate_count'],
        strictCountMatches=result.get('strict_candidate_count') == expected['strict_candidate_count'],
        selectedCountMatches=result.get('selected_count') == expected['selected_count'],
        candidateGeometryAndTargetsMatch=project(current_pipeline['candidatePreview'], CANDIDATE_FIELDS)
            == project(previous_pipeline['candidatePreview'], CANDIDATE_FIELDS),
        selectedGeometryAndTargetsMatch=project(current_pipeline['selected'], CANDIDATE_FIELDS)
            == project(previous_pipeline['selected'], CANDIDATE_FIELDS),
        mathematicalModelMatches=model_equal,
        exactCertificateValid=exact.get('certificate_valid') is True,
        plannerExactMatch=exact.get('planner_exact_match') is True,
        exactCountMatches=exact.get('exactMinimumCount') == expected['exact']['exactMinimumCount'],
        noSolverTimeout=exact.get('timeout') is False,
        pairAuditPassed=result.get('graph_audit', {}).get('pass') is True,
        comparisonsMatch=project(comparisons, COMPARISON_FIELDS) == project(previous_comparisons, COMPARISON_FIELDS),
        comparisonCountCorrect=len(comparisons) == 28,
        noComparisonErrors=all(r.get('status') == 'evaluated' for r in comparisons),
    )
    return dict(passed=all(checks.values()), checks=checks,
                mathematicalModelSha256=stable_hash(actual_model),
                retainedMathematicalModelSha256=stable_hash(old_model),
                scope='Exact JSON model identity; gzip container timestamps need not match. Timing/path fields are excluded from pipeline and comparator checks.')


def run(source, evidence, profile, output, cohorts, identifiers):
    source, evidence, profile, output = [p.resolve() for p in (source, evidence, profile, output)]
    require(not output.exists(), 'Output exists; retain previous runs')
    for input_path in (source, evidence, profile):
        require(output != input_path and input_path not in output.parents and output not in input_path.parents,
                'Output must be separate from every input')
    source_report = verify_source(source)
    require(source_report['sourceConsistencyPassed'], 'Source identity gate failed')
    evidence_report = verify_evidence(evidence)
    profile_inventory = inspect_profile(profile, PUBLIC_PROFILE_SHA256)
    output.mkdir(parents=True)
    write_bytes(output / 'SOURCE_PREFLIGHT.json', serialize(source_report))
    write_bytes(output / 'EVIDENCE_PREFLIGHT.json', serialize(evidence_report))
    with zipfile.ZipFile(evidence / 'PUBLIC_VALIDATION_EVIDENCE.zip') as archive:
        cases = select_cases(archive, cohorts, identifiers)
        modules = output / 'execution_modules'
        module_hashes = stage_modules(source, archive, modules)
        historical = output / 'historical_dependencies'
        dependency_hashes = stage_historical(source, historical, cases)
        config = dict(modules=str(modules), output=str(output), source=str(source),
                      profile=str(profile), historicalEvidence=str(historical))
        write_bytes(output / 'LOCAL_PATH_BINDINGS.json', serialize(config))
        contract = dict(schemaVersion='probeplanner.public_campaign_replay.v1',
            createdUtc=datetime.now(timezone.utc).isoformat(), role='public_packaging_reproduction_of_recorded_cases',
            newUntouchedEvidence=False, releaseAcceptance=False,
            caseIds=[c + '/' + i['case_id'] for c, i in cases], fullRequestedCohorts=not identifiers,
            cohortManifests={c: hashlib.sha256(archive.read(c + '/protocol/' + COHORT_MANIFESTS[c])).hexdigest() for c in cohorts},
            sourceInventorySha256=source_report['evaluatedInventorySha256'], optimizerSha256=source_report['optimizerSha256'],
            publicEvidenceManifestSha256=evidence_report['manifestSha256'], publicProfileSha256=PUBLIC_PROFILE_SHA256,
            driverSha256=sha(Path(__file__)), adapterSha256=module_hashes['common.py'],
            executionModules=module_hashes, historicalDependencies=dependency_hashes,
            workers=1, solverSeconds=120, solverWallSeconds=150, solverRelativeGap=0,
            packages={n: importlib.metadata.version(n) for n in ('numpy', 'scipy', 'Flask', 'allensdk', 'nibabel')},
            policy='One actual pipeline call per archived request; no retry, replacement, or parameter change; original method modules are byte-preserved.',
            comparedCandidateFields=CANDIDATE_FIELDS, comparedComparisonFields=COMPARISON_FIELDS,
            scope='Software reproduction only. Exact minimum is restricted to each regenerated strictly filtered finite candidate pool.')
        write_bytes(output / 'RUN_CONTRACT.json', serialize(contract))
        os.environ['PAPER_REPLAY_CONFIG'] = str(output / 'LOCAL_PATH_BINDINGS.json')
        sys.path.insert(0, str(modules))
        require(not any(name in sys.modules for name in ('common', 'runtime', 'case_engine', 'comparison')),
                'Run in a fresh Python process; campaign module names are already imported')
        from runtime import load_backend, load_oracle
        from case_engine import run_case
        from comparison import compare_case
        rows = []
        with (output / 'startup.log').open('x', encoding='utf-8', buffering=1) as startup:
            with redirect_stdout(startup), redirect_stderr(startup):
                server, oracle = load_backend(output), load_oracle()
            with server.app.app_context():
                for index, (cohort, item) in enumerate(cases, 1):
                    name = cohort + '/' + item['case_id']
                    print(f'START {index}/{len(cases)} {name}', flush=True)
                    parent = output / cohort
                    parent.mkdir(exist_ok=True)
                    folder = parent / item['case_id']
                    try:
                        with (parent / (item['case_id'] + '.log')).open('x', encoding='utf-8') as log:
                            with redirect_stdout(log), redirect_stderr(log):
                                result = run_case(server, oracle, item, folder)
                                comparisons = compare_case(server, item, folder) if item['positive'] else []
                        matched = compare_outcome(archive, cohort, item, folder, result, comparisons)
                        row = dict(case=name, positive=item['positive'], **matched)
                    except Exception as error:
                        row = dict(case=name, positive=item['positive'], passed=False,
                                   error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
                        folder.mkdir(exist_ok=True)
                    write_bytes(folder / 'REPRODUCTION_CHECK.json', serialize(row))
                    rows.append(row)
                    print(f'DONE {index}/{len(cases)} {name} reproduced={row["passed"]}', flush=True)
        require(verify_source(source) == source_report, 'Source changed during replay')
        require(inspect_profile(profile, PUBLIC_PROFILE_SHA256) == profile_inventory, 'Profile changed during replay')
        require(verify_evidence(evidence) == evidence_report, 'Evidence changed during replay')
        for name, digest in module_hashes.items():
            require(sha(modules / name) == digest, 'Execution module changed')
        for name, digest in dependency_hashes.items():
            require(sha(historical / name) == digest, 'Historical dependency changed')
        require(sha(Path(__file__)) == contract['driverSha256'], 'Driver changed')
        require(strict_json((output / 'RUN_CONTRACT.json').read_bytes()) == strict_json(serialize(contract)), 'Run contract changed')
    return dict(reproductionPassed=all(r['passed'] for r in rows) and len(rows) == len(cases),
        completed=len(rows), positive=sum(r['positive'] for r in rows), negative=sum(not r['positive'] for r in rows),
        fullRequestedCohorts=not identifiers, cases=rows, integrityPostflightPassed=True,
        newUntouchedEvidence=False, releaseAcceptance=False,
        runContractSha256=sha(output / 'RUN_CONTRACT.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cohort', choices=tuple(COHORT_MANIFESTS), action='append')
    parser.add_argument('--case', action='append', help='cohort/case_id; omit to replay every requested cohort case')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve previous replay')
    for path in (args.source, args.evidence_dir, args.profile):
        root, out = path.resolve(), args.output.resolve()
        if root == out or root in out.parents or out in root.parents:
            parser.error('Output must be separate from every input')
    try:
        result = run(args.source, args.evidence_dir, args.profile, args.output,
                     args.cohort or list(COHORT_MANIFESTS), args.case)
    except Exception as error:
        result = dict(reproductionPassed=False, releaseAcceptance=False,
                      error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    write_bytes(args.output / 'SUMMARY.json', serialize(result))
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['reproductionPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
