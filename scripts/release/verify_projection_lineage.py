#!/usr/bin/env python3
"""Independently compare retained originals with a path-only public projection."""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
import re
import zipfile

from verify_current_evidence import require, strict_json, verify as verify_original
from verify_public_evidence import verify as verify_public
from verify_source_v030 import sha

# Kept separate from the builder: this audit never calls its transform function.
HOME = re.compile(r'''(?i)(?<![a-z])[a-z]:[\\/]+Users[\\/]+[^\\/\s"'<>]+''')
DRIVE = re.compile(r'(?i)(?<![a-z])([a-z]):[\\/]+')
CREDENTIAL = re.compile(r'''(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[=:]\s*["']?[A-Za-z0-9_+/=-]{16,}''')


def check_text(before, after):
    intermediate, homes = HOME.subn('<USER_HOME>/', before)
    expected, drives = DRIVE.subn(lambda match: '<LOCAL_' + match[1].upper() + '>/', intermediate)
    require(after == expected, 'Non-path text change')
    return homes + drives


def check_json(before, after):
    require(type(before) is type(after), 'JSON type changed')
    if isinstance(before, dict):
        require(len(before) == len(after), 'JSON object size changed')
        total = 0
        for (old_key, old_value), (new_key, new_value) in zip(before.items(), after.items()):
            total += check_text(old_key, new_key) + check_json(old_value, new_value)
        return total
    if isinstance(before, list):
        require(len(before) == len(after), 'JSON list size changed')
        return sum(check_json(a, b) for a, b in zip(before, after))
    if isinstance(before, str):
        return check_text(before, after)
    require(before == after, 'Non-string JSON value changed')
    if type(before) is float:
        require(math.copysign(1, before) == math.copysign(1, after), 'JSON number sign changed')
    return 0


def audit(original_dir, public_dir):
    original_check = verify_original(original_dir)
    public_check = verify_public(public_dir)
    original = strict_json((original_dir / 'CURRENT_EVIDENCE_MANIFEST.json').read_bytes())
    public = strict_json((public_dir / 'PUBLIC_EVIDENCE_MANIFEST.json').read_bytes())
    require(set(original['files']) == set(public['files']), 'Projection member set changed')
    changed = identical = substitutions = 0
    privacy_hits = []
    with zipfile.ZipFile(original_dir / original['archive']) as old_zip, \
            zipfile.ZipFile(public_dir / public['archive']) as new_zip:
        for name, row in public['files'].items():
            old_row = original['files'][name]
            require(row['originalSha256'] == old_row['sha256'] and row['originalBytes'] == old_row['bytes'],
                    'Original ledger identity changed')
            before, after = old_zip.read(name), new_zip.read(name)
            raw_before = gzip.decompress(before) if name.endswith('.gz') else before
            raw_after = gzip.decompress(after) if name.endswith('.gz') else after
            count = 0
            if before == after:
                identical += 1
            else:
                require(not row['protectedScientificRecord'], 'Protected original was altered')
                changed += 1
                if name.endswith(('.json', '.json.gz')):
                    count = check_json(strict_json(raw_before), strict_json(raw_after))
                else:
                    count = check_text(raw_before.decode('utf-8'), raw_after.decode('utf-8'))
                require(count > 0, 'Unnecessary byte change')
            require(count == row['pathSubstitutions'], 'Path-change count mismatch')
            substitutions += count
            try:
                text = raw_after.decode('utf-8')
            except UnicodeDecodeError:
                continue
            for label, pattern in [('absoluteWindowsPath', DRIVE), ('credentialAssignmentPattern', CREDENTIAL)]:
                hits = len(pattern.findall(text))
                if hits:
                    privacy_hits.append(dict(member=name, pattern=label, count=hits))
    return dict(lineagePassed=True, originalBytesUnchanged=True, changedFileCount=changed,
                byteIdenticalFileCount=identical, pathSubstitutions=substitutions,
                changedJsonDataInvariant='Only approved path string substitutions; keys, types, scalar values and list order checked',
                privacyPatternHits=privacy_hits, privacyPatternScanPassed=not privacy_hits,
                privacyScanScope='Decompressed UTF-8 member text; Windows absolute paths and long credential assignment patterns only. Not a comprehensive secrets or licensing review.',
                originalArchiveSha256=original_check['archiveSha256'], publicArchiveSha256=public_check['archiveSha256'],
                originalManifestSha256=original_check['manifestSha256'], publicManifestSha256=public_check['manifestSha256'],
                releaseAcceptance=False, publicDistributionApproved=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-dir', type=Path, required=True)
    parser.add_argument('--public-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve previous audit')
    try:
        result = audit(args.original_dir, args.public_dir)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        result = dict(lineagePassed=False, releaseAcceptance=False, error=f'{type(error).__name__}: {error}')
    result['verifierSha256'] = sha(Path(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps(result, indent=2))
    return 0 if result['lineagePassed'] and result['privacyPatternScanPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
