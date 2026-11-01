#!/usr/bin/env python3
"""Download pinned profile assets to new directories and verify before loading."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen
import zipfile

from prepare_public_profile import serialize, write_bytes
from public_profile_bundle import extract_verified, validate_bundle_manifest
from verify_current_evidence import require, strict_json
from verify_source_v030 import sha


def check_url(url):
    value = urlsplit(url)
    require(value.scheme == 'https' or (value.scheme == 'http' and value.hostname in ('127.0.0.1', 'localhost', '::1')),
            'HTTPS required except for loopback testing')
    require(value.username is None and value.password is None, 'Credentials must not be embedded in URL')


def download(url, target, expected_sha256, *, size=None, maximum=None):
    check_url(url)
    limit = size if size is not None else maximum
    require(type(limit) is int and limit > 0, 'Download size bound required')
    count = 0
    with urlopen(url, timeout=120) as response, target.open('xb') as stream:
        check_url(response.url)
        for block in iter(lambda: response.read(4 * 1024 * 1024), b''):
            count += len(block)
            require(count <= limit, 'Download exceeds declared size')
            stream.write(block)
    require(size is None or count == size, 'Download size mismatch')
    require(sha(target) == expected_sha256, 'Download hash mismatch')
    return count


def fetch(base_url, expected_manifest_sha256, downloads, profile):
    downloads, profile = downloads.resolve(), profile.resolve()
    require(not downloads.exists() and not profile.exists(), 'Download and profile directories must be new')
    require(downloads != profile and downloads not in profile.parents and profile not in downloads.parents,
            'Download and profile directories must be separate')
    check_url(base_url)
    downloads.mkdir(parents=True)
    url = base_url.rstrip('/') + '/'
    manifest_path = downloads / 'DATA_BUNDLE_MANIFEST.json'
    download(urljoin(url, manifest_path.name), manifest_path, expected_manifest_sha256, maximum=1024 * 1024)
    manifest = strict_json(manifest_path.read_bytes())
    validate_bundle_manifest(manifest)
    rows = []
    for category, asset in manifest['assets'].items():
        count = download(urljoin(url, asset['filename']), downloads / asset['filename'], asset['sha256'], size=asset['bytes'])
        rows.append(dict(asset=category, filename=asset['filename'], bytes=count, sha256=asset['sha256']))
        print(json.dumps(dict(downloaded=category, bytes=count)), flush=True)
    extract_verified(downloads, profile, manifest)
    return dict(downloadAndExtractionPassed=True, bundleManifestSha256=expected_manifest_sha256,
                publicProfileContractSha256=manifest['publicProfileContractSha256'], filesVerified=len(manifest['files']),
                assets=rows, transport='loopback' if urlsplit(base_url).scheme == 'http' else 'https',
                runtimeStartupVerified=False, releaseAcceptance=False,
                scope='Verified transfer and exact file identities only; startup and published-endpoint checks remain separate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists() or args.profile.resolve() in args.report.resolve().parents:
        parser.error('Use a new report outside the profile')
    try:
        result = fetch(args.base_url, args.manifest_sha256, args.downloads, args.profile)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        result = dict(downloadAndExtractionPassed=False, releaseAcceptance=False, error=f'{type(error).__name__}: {error}')
    write_bytes(args.report, serialize(result))
    print(json.dumps(result, indent=2))
    return 0 if result['downloadAndExtractionPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
