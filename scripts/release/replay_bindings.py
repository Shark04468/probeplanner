"""Explicit path adapter for byte-preserved campaign modules, not a new method."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


CONFIG_PATH = Path(os.environ['PAPER_REPLAY_CONFIG']).resolve(strict=True)
CONFIG = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
ROOT = Path(CONFIG['modules'])
OUTPUT = Path(CONFIG['output'])
SOURCE = Path(CONFIG['source'])
PROFILE = Path(CONFIG['profile'])
EVIDENCE = Path(CONFIG['historicalEvidence'])
ORACLE = EVIDENCE / 'paper_final_validation/03_exact_probe_count_oracle/run_exact_oracle.py'
OPTIMIZER = 'apps/backend-compat/src/optimizer_server.py'
# Historical comparison identifiers; the new replay role is recorded separately.
COMMIT = 'f259a72ecbfdb7dd898c17c4fb3755198f8dfb64'
TAG = 'v0.2.1-paper'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    temp.replace(path)


def git(*args, repo=None):
    repo = Path(repo or SOURCE)
    return subprocess.check_output(['git', '-c', f'safe.directory={repo.as_posix()}', '-C', str(repo), *args])
