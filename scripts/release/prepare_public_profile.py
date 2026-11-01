#!/usr/bin/env python3
"""Package a path-redacted deployment copy; never change evaluated scientific bytes."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import stat

from public_evidence_projection import project_json
from verify_current_evidence import digest, require, strict_json
from verify_projection_lineage import check_json
from verify_source_v030 import safe_member, sha

EVALUATED_PROFILE_SHA256 = '1b12f6c88d4880f88a0a5d76add35221bf75f172f796df67747f1344ef824839'
NOTICE_NAMES = ('AMBMC_COPYING.txt', 'DATA_NOTICES.md', 'RESOURCE_IDENTITIES.json')
ROOT = Path(__file__).resolve().parents[2]


def checked_path(root, relative):
    require(safe_member(relative), 'Unsafe profile member')
    path = root / relative
    require(root in path.resolve().parents, 'Profile member escapes root')
    for part in (path, *path.parents):
        if part == root:
            break
        attributes = getattr(part.lstat(), 'st_file_attributes', 0)
        require(not part.is_symlink() and not attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0),
                'Profile links are not accepted')
    require(path.is_file(), 'Missing profile member')
    return path


def public_name(relative):
    return 'provenance/path_projected/' + relative[len('provenance/'):] if relative.startswith('provenance/') else relative


def serialize(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def project_metadata(data):
    before = strict_json(data)
    after, substitutions = project_json(before)
    require(check_json(before, after) == substitutions, 'Independent metadata check failed')
    return (serialize(after) if substitutions else data), substitutions


def build_profile(source, output, notices, *, expected_profile_sha256=EVALUATED_PROFILE_SHA256):
    source, output, notices = source.resolve(), output.resolve(), notices.resolve()
    require(not output.exists() and output != source and source not in output.parents, 'Output must be new and outside source')
    contract_path = checked_path(source, 'paper_profile.json')
    require(sha(contract_path) == expected_profile_sha256, 'Evaluated profile contract changed')
    original = strict_json(contract_path.read_bytes())
    require(original['schemaVersion'] == 'probeplanner.paper_profile.v1', 'Unsupported profile schema')
    bindings = original['files']
    require('artifact_manifest.json' in bindings, 'Artifact manifest is not bound')
    require(len({name.casefold() for name in bindings}) == len(bindings), 'Case-colliding profile members')
    source_paths = {}
    for relative, expected in bindings.items():
        require(relative not in {'paper_profile.json', 'PUBLIC_DERIVATION.json'}, 'Reserved profile member')
        path = checked_path(source, relative)
        require(sha(path) == expected, 'Evaluated profile file changed: ' + relative)
        source_paths[relative] = path
    artifact = strict_json(source_paths['artifact_manifest.json'].read_bytes())
    for entry in artifact['artifacts'].values():
        relative = entry['path']
        require(relative in bindings and entry['sha256'] == bindings[relative], 'Original artifact binding mismatch')
    for name in NOTICE_NAMES:
        checked_path(notices, name)
        require(name not in bindings, 'Notice collides with an evaluated file')

    output.mkdir(parents=True)
    rows = {}
    for relative, path in source_paths.items():
        if relative == 'artifact_manifest.json':
            continue
        destination = public_name(relative)
        target = output / destination
        substitutions = 0
        if relative.endswith('.json'):
            data, substitutions = project_metadata(path.read_bytes())
            write_bytes(target, data)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        actual = sha(target)
        require(relative.endswith('.json') or actual == bindings[relative], 'Scientific bytes changed')
        rows[relative] = dict(publicPath=destination, originalSha256=bindings[relative], publicSha256=actual,
                              bytes=target.stat().st_size, pathSubstitutions=substitutions,
                              operation='path_projection' if substitutions else 'byte_copy')

    original_artifact_bytes = source_paths['artifact_manifest.json'].read_bytes()
    projected_artifact, substitutions = project_metadata(original_artifact_bytes)
    deployed_artifact = strict_json(projected_artifact)
    rebound = []
    for key, entry in deployed_artifact['artifacts'].items():
        old_relative = artifact['artifacts'][key]['path']
        require(old_relative != 'artifact_manifest.json', 'Self-referential artifact manifest')
        row = rows[old_relative]
        if entry['path'] != row['publicPath'] or entry['sha256'] != row['publicSha256']:
            rebound.append(dict(artifact=key, originalPath=old_relative, publicPath=row['publicPath'],
                                originalSha256=entry['sha256'], publicSha256=row['publicSha256']))
        entry['path'], entry['sha256'] = row['publicPath'], row['publicSha256']
    deployed_artifact['publicMetadataDerivation'] = dict(originalProfileContractSha256=expected_profile_sha256,
        originalArtifactManifestSha256=bindings['artifact_manifest.json'], scientificBytesUnchanged=True,
        scope='Path metadata and active deployment bindings only. Historical metadata copies are explicitly path-projected; original private bytes remain retained separately.')
    artifact_data = serialize(deployed_artifact)
    write_bytes(output / 'artifact_manifest.json', artifact_data)
    rows['artifact_manifest.json'] = dict(publicPath='artifact_manifest.json', originalSha256=bindings['artifact_manifest.json'],
        publicSha256=digest(artifact_data), bytes=len(artifact_data), pathSubstitutions=substitutions,
        operation='path_projection_and_deployment_rebinding', reboundArtifacts=rebound)

    evaluated_contract = output / 'provenance/path_projected/evaluated_paper_profile.json'
    contract_data, contract_substitutions = project_metadata(contract_path.read_bytes())
    write_bytes(evaluated_contract, contract_data)
    notice_bindings = {}
    for name in NOTICE_NAMES:
        path = output / name
        shutil.copyfile(notices / name, path)
        notice_bindings[name] = sha(path)
    lineage = dict(schemaVersion='probeplanner.public_profile_derivation.v1',
        originalProfileContractSha256=expected_profile_sha256,
        publicHistoricalContractPath=evaluated_contract.relative_to(output).as_posix(),
        publicHistoricalContractSha256=sha(evaluated_contract), historicalContractPathSubstitutions=contract_substitutions,
        builderSha256=sha(Path(__file__)), files=rows, noticeFiles=notice_bindings,
        scientificFileBytesChanged=False, registrationEvidenceUpgraded=False,
        originalPrivateMetadataModified=False, releaseAcceptance=False)
    write_bytes(output / 'PUBLIC_DERIVATION.json', serialize(lineage))
    contract = deepcopy(original)
    contract['files'] = {path.relative_to(output).as_posix(): sha(path)
                         for path in sorted(output.rglob('*')) if path.is_file()}
    contract['publicDerivation'] = dict(sourceProfileContractSha256=expected_profile_sha256,
        record='PUBLIC_DERIVATION.json', scientificBytesUnchanged=True,
        scope='Public deployment derivative, not an original frozen experiment manifest or registration replay.')
    write_bytes(output / 'paper_profile.json', serialize(contract))
    for relative, path in source_paths.items():
        require(sha(path) == bindings[relative], 'Original input changed during copy')
    require(sha(contract_path) == expected_profile_sha256, 'Original contract changed during copy')
    return dict(publicProfilePrepared=True, releaseAcceptance=False,
        sourceProfileContractSha256=expected_profile_sha256, publicProfileContractSha256=sha(output / 'paper_profile.json'),
        publicDerivationSha256=sha(output / 'PUBLIC_DERIVATION.json'), fileCount=len(contract['files']),
        originalBoundFiles=len(rows), scientificFilesByteIdentical=sum(not name.endswith('.json') for name in rows),
        pathProjectedFiles=sum(row['pathSubstitutions'] > 0 for row in rows.values()), reboundArtifacts=rebound,
        scope='Prepared copy only; startup, clean download, UI and full release gates are separate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', type=Path, required=True)
    parser.add_argument('--output-profile', type=Path, required=True)
    parser.add_argument('--notices-dir', type=Path, default=ROOT / 'docs/release/data')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Report exists; preserve prior attempts')
    try:
        report = build_profile(args.source_profile, args.output_profile, args.notices_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = dict(publicProfilePrepared=False, releaseAcceptance=False, error=f'{type(error).__name__}: {error}')
    write_bytes(args.report, serialize(report))
    print(json.dumps(report, indent=2))
    return 0 if report['publicProfilePrepared'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
