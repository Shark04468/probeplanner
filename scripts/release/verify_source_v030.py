#!/usr/bin/env python3
"""Check v0.3.0 packaging against evaluated source; not the full release gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = 'release-evidence/v0.3.0-paper/EVALUATED_SOURCE.json'
INVENTORY_SHA256 = '7a2e773b6fb4bcb18b20135a799f874cc89250a778841cd15f63a7bfc130831e'
EXPECTED_OPTIMIZER = '5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9'
ABSENT_LAUNCHER = 'apps/backend-compat/scripts/start_backend_compat.ps1'
# No scientifically exercised implementation or existing test is mutable here.
PACKAGING_ONLY = frozenset({
    '.github/workflows/paper-release.yml', '.gitattributes', '.gitignore', '.zenodo.json',
    'CITATION.cff', 'README.md', 'apps/backend-compat/README.md',
    'docs/PAPER_RUNTIME_AND_MRI_EVIDENCE.md', 'docs/project-map.md',
    'package.json', 'package-lock.json',
    'apps/backend-compat/scripts/run_allensdk_atlas_audit.ps1',
})
NEW_PREFIXES = ('scripts/release/', 'docs/release/', 'release-evidence/v0.3.0-paper/')
IGNORED_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.pytest_cache', '.cache', '.venv',
    '.playwright-cli', 'target', 'dist', 'reports', 'logs', '.probeplanner',
})
IGNORED_ROOTS = ('apps/ui/build/', 'apps/unity/Library/', 'apps/unity/Temp/',
                 'apps/unity/Logs/', 'apps/unity/Obj/', 'apps/unity/UserSettings/')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_member(name: str) -> bool:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and path.as_posix() == name
            and not any(p in {'.', '..', '.git'} or p.endswith((' ', '.')) for p in path.parts))


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def list_files(root: Path) -> dict[str, Path]:
    result = {}
    def visit(folder):
        for path in sorted(folder.iterdir()):
            relative = path.relative_to(root).as_posix()
            if path.name in IGNORED_DIRS or any((relative + '/').startswith(p) for p in IGNORED_ROOTS):
                continue
            attributes = getattr(path.lstat(), 'st_file_attributes', 0)
            if path.is_symlink() or attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                raise ValueError(f'Link is not a publishable source file: {relative}')
            if path.is_dir():
                visit(path)
            elif path.is_file():
                result[relative] = path
    visit(root)
    return result


def compare_source(root: Path, evaluated: dict, actual: dict[str, Path]) -> dict:
    errors, changes, additions = [], {}, {}
    names = list(evaluated)
    if len({n.casefold() for n in names}) != len(names):
        errors.append('Case-colliding evaluated paths')
    if len({n.casefold() for n in actual}) != len(actual):
        errors.append('Case-colliding current paths')
    for name, expected in evaluated.items():
        if not safe_member(name) or not isinstance(expected, str) or not SHA256.fullmatch(expected):
            errors.append(f'Invalid evaluated entry: {name}')
            continue
        path = root / name
        if name not in actual or not path.is_file():
            errors.append(f'Evaluated file missing: {name}')
            continue
        current = sha(path)
        if current != expected:
            changes[name] = {'evaluatedSha256': expected, 'packagedSha256': current}
            if name not in PACKAGING_ONLY:
                errors.append(f'Evaluated implementation changed: {name}')
    for name, path in actual.items():
        if not safe_member(name):
            errors.append(f'Invalid current path: {name}')
        if name not in evaluated:
            additions[name] = sha(path)
            if not name.startswith(NEW_PREFIXES):
                errors.append(f'Unapproved new source path: {name}')
    if (root / ABSENT_LAUNCHER).exists():
        errors.append('The deleted legacy launcher must not be restored')
    return {'errors': errors, 'changes': changes, 'additions': additions,
            'evaluatedFiles': len(evaluated), 'currentFiles': len(actual)}


def verify(root: Path) -> dict:
    root = root.resolve()
    if sha(root / INVENTORY) != INVENTORY_SHA256:
        raise ValueError('Evaluated inventory SHA-256 mismatch')
    snapshot = read_json(root / INVENTORY)
    if (snapshot['schemaVersion'] != 'probeplanner.evaluated_source.v1'
            or len(snapshot['files']) != 1403
            or snapshot['optimizerSha256'] != EXPECTED_OPTIMIZER
            or snapshot['absentTracked'] != [ABSENT_LAUNCHER]):
        raise ValueError('Evaluated inventory identity mismatch')
    report = compare_source(root, snapshot['files'], list_files(root))
    report.update(schemaVersion='probeplanner.source_packaging_check.v1',
                  evaluatedInventorySha256=INVENTORY_SHA256,
                  optimizerSha256=sha(root / 'apps/backend-compat/src/optimizer_server.py'),
                  sourceConsistencyPassed=not report['errors'], releaseAcceptance=False,
                  scope='Source identity only. Packaging changes require review, tests and final hash binding; evidence, public data and publishing gates are separate.')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; preserve previous checks')
    try:
        report = verify(args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {'sourceConsistencyPassed': False, 'releaseAcceptance': False,
                  'errors': [f'{type(exc).__name__}: {exc}']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('sourceConsistencyPassed', 'releaseAcceptance', 'errors')}, indent=2))
    return 0 if report['sourceConsistencyPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
