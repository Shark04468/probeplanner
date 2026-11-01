#!/usr/bin/env python3
"""Build and verify split, hash-bound public profile assets; no scientific re-encoding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import zipfile

from prepare_public_profile import EVALUATED_PROFILE_SHA256, NOTICE_NAMES, checked_path, serialize, write_bytes
from verify_current_evidence import require, strict_json
from verify_source_v030 import safe_member, sha

ROLES = ('arrays', 'sdf', 'surfaces', 'metadata')
HASH = re.compile(r'[0-9a-f]{64}\Z')
BYTE_IDENTICAL_JSON = frozenset({'region_map.json', 'region_map_v2.json', 'structures.json',
                                'mri_surfaces/surface_manifest.json'})
MAX_ASSET_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3


def inspect_profile(root, expected_sha256):
    root = root.resolve()
    contract_path = checked_path(root, 'paper_profile.json')
    require(sha(contract_path) == expected_sha256, 'Public profile contract hash mismatch')
    contract = strict_json(contract_path.read_bytes())
    require(contract['schemaVersion'] == 'probeplanner.paper_profile.v1', 'Profile schema mismatch')
    files = contract['files']
    expected_names = set(files) | {'paper_profile.json'}
    actual_names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    require(actual_names == expected_names, 'Profile file set mismatch')
    require(len({name.casefold() for name in expected_names}) == len(expected_names), 'Case-colliding profile files')
    inventory = {}
    for name in sorted(expected_names):
        path = checked_path(root, name)
        expected = expected_sha256 if name == 'paper_profile.json' else files[name]
        require(sha(path) == expected, 'Public profile file hash mismatch: ' + name)
        inventory[name] = dict(sha256=expected, bytes=path.stat().st_size)
    require(set(NOTICE_NAMES) <= set(files), 'Missing data notices')
    lineage = strict_json((root / 'PUBLIC_DERIVATION.json').read_bytes())
    require(lineage['schemaVersion'] == 'probeplanner.public_profile_derivation.v1', 'Lineage schema mismatch')
    require(lineage['originalProfileContractSha256'] == EVALUATED_PROFILE_SHA256, 'Wrong evaluated profile identity')
    require(lineage['scientificFileBytesChanged'] is False and lineage['registrationEvidenceUpgraded'] is False,
            'Public copy cannot change scientific evidence')
    historical_path = checked_path(root, lineage['publicHistoricalContractPath'])
    require(sha(historical_path) == lineage['publicHistoricalContractSha256'] == EVALUATED_PROFILE_SHA256,
            'Original evaluated profile contract must be byte-identical')
    original = strict_json(historical_path.read_bytes())
    require(set(lineage['files']) == set(original['files']), 'Original/public lineage member set mismatch')
    for name, row in lineage['files'].items():
        require(row['originalSha256'] == original['files'][name], 'Original profile identity mismatch')
        require(row['publicPath'] in inventory, 'Unbound derived file')
        require(row['publicSha256'] == inventory[row['publicPath']]['sha256'], 'Derived profile identity mismatch')
        require(row['bytes'] == inventory[row['publicPath']]['bytes'], 'Derived byte count mismatch')
        if not name.endswith('.json') or name in BYTE_IDENTICAL_JSON:
            require(row['originalSha256'] == row['publicSha256'], 'Scientific file changed: ' + name)
    artifact = strict_json((root / 'artifact_manifest.json').read_bytes())
    for entry in artifact['artifacts'].values():
        require(entry['path'] in inventory and entry['sha256'] == inventory[entry['path']]['sha256'],
                'Active artifact binding mismatch')
    return inventory


def role(name):
    if name == 'layered_sdf.npz':
        return 'sdf'
    if name.endswith('.obj'):
        return 'surfaces'
    if name.endswith(('.npy', '.nii.gz')):
        return 'arrays'
    return 'metadata'


def build(root, output, expected_sha256):
    root, output = root.resolve(), output.resolve()
    require(not output.exists() and output != root and root not in output.parents, 'Bundle output must be new and separate')
    inventory = inspect_profile(root, expected_sha256)
    output.mkdir(parents=True)
    assets = {}
    for category in ROLES:
        members = [name for name in inventory if role(name) == category]
        require(bool(members), 'Empty asset role: ' + category)
        filename = 'ProbePlanner-v0.3.0-paper-profile-' + category + '.zip'
        target = output / filename
        compression = zipfile.ZIP_STORED if category == 'sdf' else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(target, 'x', compression=compression, compresslevel=6) as archive:
            for name in members:
                archive.write(root / name, arcname=name)
        require(target.stat().st_size < MAX_ASSET_BYTES, 'Asset exceeds size bound')
        assets[category] = dict(filename=filename, sha256=sha(target), bytes=target.stat().st_size,
                                members=members, compression='stored' if category == 'sdf' else 'deflate')
        print(json.dumps(dict(asset=category, bytes=assets[category]['bytes'], sha256=assets[category]['sha256'])), flush=True)
    require(inspect_profile(root, expected_sha256) == inventory, 'Profile changed during packaging')
    manifest = dict(schemaVersion='probeplanner.public_profile_bundle.v1', intendedReleaseTag='v0.3.0-paper',
        originalProfileContractSha256=EVALUATED_PROFILE_SHA256, publicProfileContractSha256=expected_sha256,
        scientificFileBytesChanged=False, assets=assets, files=inventory,
        builderSha256=sha(Path(__file__)), releaseAcceptance=False,
        scope='Transport assets for the explicitly derived population-template profile; not new registration or experimental evidence.')
    write_bytes(output / 'DATA_BUNDLE_MANIFEST.json', serialize(manifest))
    checksums = [f"{item['sha256']}  {item['filename']}" for item in assets.values()]
    checksums.append(f"{sha(output / 'DATA_BUNDLE_MANIFEST.json')}  DATA_BUNDLE_MANIFEST.json")
    write_bytes(output / 'SHA256SUMS.txt', ('\n'.join(checksums) + '\n').encode('utf-8'))
    return manifest


def validate_bundle_manifest(manifest):
    require(manifest['schemaVersion'] == 'probeplanner.public_profile_bundle.v1', 'Bundle schema mismatch')
    require(manifest['intendedReleaseTag'] == 'v0.3.0-paper', 'Wrong bundle release')
    require(manifest['originalProfileContractSha256'] == EVALUATED_PROFILE_SHA256, 'Wrong original profile')
    require(manifest['scientificFileBytesChanged'] is False, 'Scientific data changed')
    require(set(manifest['assets']) == set(ROLES), 'Asset roles mismatch')
    files = manifest['files']
    require('paper_profile.json' in files and files['paper_profile.json']['sha256'] == manifest['publicProfileContractSha256'],
            'Unbound public profile contract')
    require(len({name.casefold() for name in files}) == len(files), 'Case-colliding members')
    total = 0
    for name, item in files.items():
        require(safe_member(name), 'Unsafe member path')
        require(type(item['bytes']) is int and 0 <= item['bytes'] < MAX_ASSET_BYTES, 'Invalid member size')
        require(isinstance(item['sha256'], str) and HASH.fullmatch(item['sha256']), 'Invalid member hash')
        total += item['bytes']
    require(total < MAX_TOTAL_BYTES, 'Bundle decoded size exceeds bound')
    seen = set()
    for category, asset in manifest['assets'].items():
        require(asset['filename'] == 'ProbePlanner-v0.3.0-paper-profile-' + category + '.zip', 'Unexpected asset filename')
        require(type(asset['bytes']) is int and 0 < asset['bytes'] < MAX_ASSET_BYTES, 'Invalid asset size')
        require(isinstance(asset['sha256'], str) and HASH.fullmatch(asset['sha256']), 'Invalid asset hash')
        require(asset['members'] and len(asset['members']) == len(set(asset['members'])), 'Empty or duplicate asset members')
        require(not seen.intersection(asset['members']), 'Member listed in multiple assets')
        require(set(asset['members']) <= set(files), 'Unlisted member')
        seen.update(asset['members'])
    require(seen == set(files), 'Incomplete bundle member coverage')


def extract_verified(downloads, output, manifest):
    validate_bundle_manifest(manifest)
    downloads, output = downloads.resolve(), output.resolve()
    require(not output.exists() and downloads != output and downloads not in output.parents,
            'Extraction output must be new and separate')
    for asset in manifest['assets'].values():
        path = checked_path(downloads, asset['filename'])
        require(path.stat().st_size == asset['bytes'] and sha(path) == asset['sha256'], 'Downloaded asset mismatch')
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) and set(names) == set(asset['members']), 'ZIP members mismatch')
            for info in archive.infolist():
                require(safe_member(info.filename) and not info.is_dir(), 'Unsafe ZIP member')
                require(not stat.S_ISLNK(info.external_attr >> 16), 'ZIP symlink rejected')
                require(info.file_size == manifest['files'][info.filename]['bytes'], 'ZIP member size mismatch')
    output.mkdir(parents=True)
    for asset in manifest['assets'].values():
        with zipfile.ZipFile(downloads / asset['filename']) as archive:
            for name in asset['members']:
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                expected = manifest['files'][name]
                count = 0
                with archive.open(name) as source, target.open('xb') as dest:
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        count += len(block)
                        require(count <= expected['bytes'], 'ZIP member exceeds bound')
                        dest.write(block)
                require(count == expected['bytes'] and sha(target) == expected['sha256'], 'Extracted member mismatch')
    require(inspect_profile(output, manifest['publicProfileContractSha256']) == manifest['files'],
            'Extracted profile identity mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--expected-profile-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.profile, args.output, args.expected_profile_sha256)
    print(json.dumps(dict(bundlePrepared=True, releaseAcceptance=False,
                         manifestSha256=sha(args.output / 'DATA_BUNDLE_MANIFEST.json'),
                         fileCount=len(manifest['files'])), indent=2))


if __name__ == '__main__':
    main()
