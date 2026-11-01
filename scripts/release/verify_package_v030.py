"""Verify release metadata and the exact source-package member inventory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from prepare_public_profile import serialize, write_bytes
from verify_current_evidence import require, strict_json
from verify_source_v030 import ROOT, safe_member, list_files, sha, verify as verify_source

MANIFEST = 'release-evidence/v0.3.0-paper/PACKAGE_SOURCE_MANIFEST.json'
REPOSITORY = 'https://github.com/Shark04468/probeplanner'
RELEASE_URL = REPOSITORY + '/releases/tag/v0.3.0-paper'
AFFILIATION = 'University of Shanghai for Science and Technology'


def verify_metadata(root):
    packages = ('package.json', 'apps/ui/package.json', 'apps/backend-compat/package.json')
    for name in packages:
        require(strict_json((root / name).read_bytes())['version'] == '0.3.0', 'Package version mismatch: ' + name)
    for name in ('package-lock.json', 'apps/ui/package-lock.json'):
        data = strict_json((root / name).read_bytes())
        require(data['version'] == data['packages']['']['version'] == '0.3.0', 'Lock version mismatch: ' + name)
    citation = yaml.safe_load((root / 'CITATION.cff').read_text(encoding='utf-8'))
    require(citation['version'] == '0.3.0-paper' and citation['type'] == 'software', 'Citation version/type mismatch')
    require(citation['authors'] == [dict(**{'family-names': 'Sha', 'given-names': 'Yijun'}, affiliation=AFFILIATION)],
            'Citation author/affiliation mismatch')
    require(citation['repository-code'] == REPOSITORY and citation['url'] == RELEASE_URL, 'Citation URL mismatch')
    require(citation['license'] == 'MIT', 'Citation license mismatch')
    require('doi' not in citation, 'The frozen source must not anticipate the post-publication Version DOI')
    zenodo = strict_json((root / '.zenodo.json').read_bytes())
    require(zenodo['version'] == '0.3.0-paper' and zenodo['upload_type'] == 'software', 'Zenodo version/type mismatch')
    require(zenodo['title'] == citation['title'], 'Citation/Zenodo title mismatch')
    require(zenodo['creators'] == [dict(name='Sha, Yijun', affiliation=AFFILIATION)], 'Zenodo author/affiliation mismatch')
    require(zenodo['license'] == 'MIT' and 'doi' not in zenodo, 'Zenodo license or anticipated DOI mismatch')
    predecessors = [r['identifier'] for r in zenodo['related_identifiers'] if r['relation'] == 'isNewVersionOf']
    require(predecessors == ['10.5281/zenodo.21974187'], 'Wrong predecessor Version DOI')
    license_text = (root / 'LICENSE').read_text(encoding='utf-8')
    require(license_text.startswith('MIT License\n') and 'Copyright (c) 2026 Yijun Sha' in license_text,
            'Software license identity mismatch')
    return dict(passed=True, version='0.3.0-paper', author='Yijun Sha', affiliation=AFFILIATION,
                repositoryUrl=REPOSITORY, releaseUrl=RELEASE_URL,
                predecessorVersionDoi='10.5281/zenodo.21974187', conceptDoi='10.5281/zenodo.21307546',
                versionDoiPolicy='Resolved after publication in final citation/release reports; no prior DOI assigned to this source')


def source_inventory(root):
    return {name: dict(sha256=sha(path), bytes=path.stat().st_size)
            for name, path in list_files(root).items() if name != MANIFEST}


def build(root):
    require(not (root / MANIFEST).exists(), 'Package manifest already exists; preserve the previous candidate')
    source = verify_source(root)
    require(source['sourceConsistencyPassed'], 'Evaluated source identity mismatch')
    metadata = verify_metadata(root)
    manifest = dict(schemaVersion='probeplanner.source_package.v1', intendedReleaseTag='v0.3.0-paper',
        evaluatedInventorySha256=source['evaluatedInventorySha256'], optimizerSha256=source['optimizerSha256'],
        files=source_inventory(root), metadata=metadata,
        selfExcluded=MANIFEST, releaseAcceptance=False,
        scope='Exact candidate source-file bytes, excluding this manifest to avoid self-reference. The final Git commit and this manifest SHA-256 are bound externally in release provenance.')
    write_bytes(root / MANIFEST, serialize(manifest))
    return verify(root)


def verify(root):
    path = root / MANIFEST
    manifest = strict_json(path.read_bytes())
    require(manifest['schemaVersion'] == 'probeplanner.source_package.v1'
            and manifest['intendedReleaseTag'] == 'v0.3.0-paper', 'Package identity mismatch')
    require(manifest['selfExcluded'] == MANIFEST and manifest['releaseAcceptance'] is False, 'Package scope mismatch')
    require(all(safe_member(name) for name in manifest['files']), 'Unsafe source-package member')
    require(manifest['files'] == source_inventory(root), 'Source package file/hash/size mismatch')
    source = verify_source(root)
    require(source['sourceConsistencyPassed'], 'Evaluated source identity mismatch')
    require(manifest['evaluatedInventorySha256'] == source['evaluatedInventorySha256']
            and manifest['optimizerSha256'] == source['optimizerSha256'], 'Evaluated identity binding mismatch')
    require(manifest['metadata'] == verify_metadata(root), 'Package metadata binding mismatch')
    return dict(sourcePackagePassed=True, packageManifestSha256=sha(path),
                files=len(manifest['files']), optimizerSha256=manifest['optimizerSha256'], releaseAcceptance=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.root.resolve() in args.output.resolve().parents:
        parser.error('Use a new external report path')
    try:
        result = build(args.root.resolve()) if args.build else verify(args.root.resolve())
    except Exception as error:
        result = dict(sourcePackagePassed=False, releaseAcceptance=False, error=f'{type(error).__name__}: {error}')
    write_bytes(args.output, serialize(result))
    print(json.dumps(result, indent=2))
    return 0 if result['sourcePackagePassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
