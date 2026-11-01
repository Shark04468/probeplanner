#!/usr/bin/env python3
"""Execute the v0.3.0 local release gates into a new, source-bound report directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile

from prepare_public_profile import serialize, write_bytes
from public_profile_bundle import inspect_profile
from replay_public_campaign import (PUBLIC_PROFILE_SHA256, OLD_ARCHIVE_SHA256, OLD_MANIFEST_SHA256,
                                    compare_outcome, select_cases)
from verify_current_evidence import require, strict_json
from verify_public_evidence import verify as verify_evidence
from verify_source_v030 import ROOT, list_files, sha, verify as verify_source
from verify_package_v030 import verify as verify_package

SMOKE_CASES = ['retrospective/search_unilateral_ca1', 'confirmation/independent_request_01',
               'confirmation/reused_negative_control']
ALLOWED_SKIP = ('test_verify_source_v030', 'test_symlink_not_followed')
OLD_TAGS = {'v0.2.0-paper': '0d5748e05c05e54d1330d05009747bcb4fa915a5',
            'v0.2.1-paper': 'f259a72ecbfdb7dd898c17c4fb3755198f8dfb64'}


def junit_counts(path, expected=None, packaging=False):
    tree = ET.parse(path)
    cases = tree.findall('.//testcase')
    failures = [c for c in cases if c.find('failure') is not None or c.find('error') is not None]
    skipped = [(c.get('classname'), c.get('name')) for c in cases if c.find('skipped') is not None]
    allowed = packaging and len(skipped) == 1 and skipped[0] == ALLOWED_SKIP and sys.platform == 'win32'
    suite_errors = any(int(s.get('errors', 0)) or int(s.get('failures', 0)) for s in tree.iter('testsuite'))
    passed = bool(cases) and not failures and not suite_errors and (not skipped or allowed)
    if expected is not None:
        passed = passed and len(cases) == expected
    if packaging:
        passed = passed and len(cases) >= 170
    return dict(total=len(cases), passed=len(cases)-len(failures)-len(skipped),
                failuresOrErrors=len(failures), skipped=len(skipped), skippedTests=skipped,
                countGatePassed=bool(passed))


def inventory(root):
    report = verify_source(root)
    require(report['sourceConsistencyPassed'], 'Evaluated scientific source identity changed')
    return {name: sha(path) for name, path in list_files(root).items()}


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + root.as_posix(), '-C', str(root), *args],
                                   text=True, encoding='utf-8').strip()


def verify_git(root, require_clean, tag):
    require((root / '.git').exists(), 'Release gate requires the source Git checkout')
    old = {name: git(root, 'rev-parse', name + '^{commit}') for name in OLD_TAGS}
    require(old == OLD_TAGS, 'Historical tag moved')
    head = git(root, 'rev-parse', 'HEAD')
    clean = not bool(git(root, 'status', '--porcelain'))
    if require_clean:
        require(clean, 'Working tree is not clean')
    if tag:
        require(tag == 'v0.3.0-paper', 'Wrong release tag')
        require(git(root, 'cat-file', '-t', tag) == 'tag', 'Release must use an annotated tag')
        require(git(root, 'rev-parse', tag + '^{commit}') == head, 'Tag does not identify the checked source')
    return dict(commit=head, branch=git(root, 'branch', '--show-current'), workingTreeClean=clean,
                preservedOldTags=old, annotatedTag=tag)


def verify_environment(root):
    from packaging.requirements import Requirement
    require(sys.version_info[:2] == (3, 10), 'Paper release requires Python 3.10')
    lock = root / 'apps/backend-compat/requirements-lock-py310.txt'
    rows = []
    for line in lock.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        version = importlib.metadata.version(requirement.name)
        rows.append(dict(name=requirement.name, version=version, required=str(requirement.specifier),
                         match=requirement.specifier.contains(version)))
    require(rows and all(row['match'] for row in rows), 'Locked Python environment mismatch')
    return dict(python=sys.version, lockSha256=sha(lock), applicableRequirements=rows)


def verify_replay(root, evidence, profile, replay):
    summary = strict_json((replay / 'SUMMARY.json').read_bytes())
    contract = strict_json((replay / 'RUN_CONTRACT.json').read_bytes())
    require(summary['reproductionPassed'] is True and summary['integrityPostflightPassed'] is True,
            'Portable replay did not pass')
    require(summary['runContractSha256'] == sha(replay / 'RUN_CONTRACT.json'), 'Replay contract binding mismatch')
    require(contract['caseIds'] == SMOKE_CASES and summary['completed'] == 3, 'Required portable smoke coverage missing')
    require(contract['driverSha256'] == sha(root / 'scripts/release/replay_public_campaign.py'), 'Replay driver changed')
    require(contract['adapterSha256'] == sha(root / 'scripts/release/replay_bindings.py'), 'Replay adapter changed')
    require(contract['publicProfileSha256'] == sha(profile / 'paper_profile.json') == PUBLIC_PROFILE_SHA256,
            'Replay uses another profile')
    require(contract['newUntouchedEvidence'] is False and summary['newUntouchedEvidence'] is False,
            'Repeated requests are not untouched evidence')
    evidence_report = verify_evidence(evidence)
    require(contract['publicEvidenceManifestSha256'] == evidence_report['manifestSha256'], 'Replay evidence identity mismatch')
    checks = []
    with zipfile.ZipFile(evidence / 'PUBLIC_VALIDATION_EVIDENCE.zip') as archive:
        for cohort, item in select_cases(archive, ['retrospective', 'confirmation'], SMOKE_CASES):
            folder = replay / cohort / item['case_id']
            result = strict_json((folder / 'result.json').read_bytes())
            rows = strict_json((folder / 'comparison/ROWS.json').read_bytes()) if item['positive'] else []
            check = compare_outcome(archive, cohort, item, folder, result, rows)
            require(check['passed'], 'Portable replay outcome changed: ' + item['case_id'])
            checks.append(dict(case=cohort + '/' + item['case_id'], **check))
    return dict(passed=True, cases=checks, runContractSha256=sha(replay / 'RUN_CONTRACT.json'),
                summarySha256=sha(replay / 'SUMMARY.json'), newUntouchedEvidence=False)


def execute(output, name, command, cwd, env, expected=None, packaging=False):
    folder = output / name
    folder.mkdir()
    write_bytes(folder / 'INVOCATION.json', serialize(dict(command=command, cwd=str(cwd),
                expectedCount=expected, packagingSkipPolicy=packaging)))
    print('START ' + name, flush=True)
    started = time.monotonic()
    with (folder / 'run.log').open('xb') as log:
        process = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    counts = junit_counts(folder / 'junit.xml', expected, packaging) if expected is not None or packaging else None
    passed = process.returncode == 0 and (counts is None or counts['countGatePassed'])
    record = dict(passed=passed, exitCode=process.returncode, counts=counts,
                  seconds=time.monotonic()-started, logSha256=sha(folder / 'run.log'),
                  junitSha256=sha(folder / 'junit.xml') if counts is not None else None)
    write_bytes(folder / 'RESULT.json', serialize(record))
    print(json.dumps(dict(stage=name, **record)), flush=True)
    require(passed, 'Release check failed: ' + name + '; preserve logs and do not publish')
    return record


def run(args):
    root, output = args.root.resolve(), args.output.resolve()
    source = inventory(root)
    package = verify_package(root)
    before_git = verify_git(root, args.require_clean, args.tag)
    environment = verify_environment(root)
    old_root = root / 'release-evidence/v0.2.1-paper'
    old_zip = old_root / 'ProbePlanner-v0.2.1-paper-immutable-evidence.zip'
    require(sha(old_zip) == OLD_ARCHIVE_SHA256 and sha(old_root / 'IMMUTABLE_EVIDENCE_MANIFEST.json') == OLD_MANIFEST_SHA256,
            'Historical failure/confirmation archive changed')
    profile_before = inspect_profile(args.profile, PUBLIC_PROFILE_SHA256)
    replay = verify_replay(root, args.evidence_dir, args.profile, args.replay_dir)
    require(not output.exists(), 'Output exists; preserve prior gates')
    output.mkdir(parents=True)
    write_bytes(output / 'ENVIRONMENT.json', serialize(environment))
    write_bytes(output / 'PORTABLE_REPLAY_CHECK.json', serialize(replay))
    write_bytes(output / 'SOURCE_PACKAGE_CHECK.json', serialize(package))
    write_bytes(output / 'RUN_CONTRACT.json', serialize(dict(
        schemaVersion='probeplanner.local_release_checks.v1', createdUtc=datetime.now(timezone.utc).isoformat(),
        sourceFiles=source, git=before_git, driverSha256=sha(Path(__file__)),
        publicProfileSha256=PUBLIC_PROFILE_SHA256, originalSubsetCount=112, backendCount=329,
        frontendCount=152, surfaceBuilderCount=6, packagingMinimumCount=170,
        scope='Current-source software and evidence gates. Publication, public-endpoint verification and DOI remain separate.')))
    env = {k: v for k, v in os.environ.items() if not k.startswith('PROBEPLANNER_')}
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', CI='true')
    stages = {}
    stages['pip_check'] = execute(output, 'pip_check', [sys.executable, '-m', 'pip', 'check'], root, env)
    suites = [('original_112', ['apps/backend-compat/tests/test_optimizer_server.py', 'apps/backend-compat/tests/test_validation_scripts.py'], 112, sys.executable),
              ('backend_329', ['apps/backend-compat/tests'], 329, sys.executable),
              ('surface_builder_6', ['apps/backend-compat/scripts/tests'], 6, str(args.surface_python)),
              ('packaging', ['scripts/release/tests'], None, sys.executable)]
    for name, paths, count, python in suites:
        folder = output / name
        stage_env = dict(env, PROBEPLANNER_STATE_DIR=str(folder / 'state'),
                         PROBEPLANNER_SDF_CACHE_DIR=str(folder / 'sdf'), PROBEPLANNER_REGION_INDEX_CACHE_DIR=str(folder / 'region'))
        command = [python, '-X', 'utf8', '-m', 'pytest', *paths, '-q', '-ra', '-p', 'no:cacheprovider',
                   '--junitxml=' + str(folder / 'junit.xml'), '--basetemp=' + str(folder / 'temp')]
        stages[name] = execute(output, name, command, root, stage_env, count, name == 'packaging')
    npm = str(args.npm) if args.npm else shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    require(bool(npm), 'npm is unavailable')
    js_env = dict(env, PATH=str(Path(npm).parent) + os.pathsep + env.get('PATH', ''))
    stages['frontend_152'] = execute(output, 'frontend_152', [npm, 'run', 'test:frontend', '--',
        '--reporter=default', '--reporter=junit', '--outputFile=' + str(output / 'frontend_152/junit.xml')], root / 'apps/ui', js_env, 152)
    stages['ui_lint'] = execute(output, 'ui_lint', [npm, 'run', 'lint'], root / 'apps/ui', js_env)
    stages['ui_build'] = execute(output, 'ui_build', [npm, 'run', 'build', '--', '--outDir', str(output / 'ui_build/dist')], root / 'apps/ui', js_env)
    stages['historical_exact'] = execute(output, 'historical_exact', [sys.executable, '-X', 'utf8',
        str(root / 'scripts/release/verify_exact_oracle_models.py'), '--evidence-zip', str(old_zip),
        '--output', str(output / 'historical_exact/verification.json')], root, env)
    stages['current_exact'] = execute(output, 'current_exact', [sys.executable, '-X', 'utf8',
        str(root / 'scripts/release/verify_public_evidence.py'), '--evidence-dir', str(args.evidence_dir.resolve()),
        '--resolve-models', '--output', str(output / 'current_exact/verification.json')], root, env)
    require(inventory(root) == source, 'Source changed during release checks')
    require(verify_package(root) == package, 'Source package changed during release checks')
    require(inspect_profile(args.profile, PUBLIC_PROFILE_SHA256) == profile_before, 'Profile changed during release checks')
    require(verify_git(root, args.require_clean, args.tag) == before_git, 'Git state changed during release checks')
    require(verify_replay(root, args.evidence_dir, args.profile, args.replay_dir) == replay, 'Replay evidence changed')
    return dict(localReleaseValidationPassed=True, releaseAcceptance=False, git=before_git, stages=stages,
                sourceAndInputsUnchanged=True, runContractSha256=sha(output / 'RUN_CONTRACT.json'),
                countsOverlap=True, publicEndpointVerified=False, newVersionDoi=None,
                scope='All specified local software/evidence gates passed. This is not a completed public release.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--replay-dir', type=Path, required=True)
    parser.add_argument('--surface-python', type=Path, required=True)
    parser.add_argument('--npm', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--tag', choices=['v0.3.0-paper'])
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        parser.error('Output exists; preserve previous gates')
    for value in (args.root, args.evidence_dir, args.profile, args.replay_dir):
        path = value.resolve()
        if path == out or path in out.parents or out in path.parents:
            parser.error('Output must be separate from every input')
    try:
        result = run(args)
    except Exception as error:
        result = dict(localReleaseValidationPassed=False, releaseAcceptance=False,
                      error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    write_bytes(out / 'SUMMARY.json', serialize(result))
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['localReleaseValidationPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
