"""Stage a separately identified viewer into generated static output only."""
import json
from pathlib import Path
import shutil

from qualify_macos import SOURCE_COMMIT, sha, require, write

CANDIDATE_COMMIT = '5ca4a3b8874af4c03527da91aaf995adc8be4861'
CONTRACT_SHA = '6bced75e659337c0893db944eb8fdd06a803c2dbba9ea4d2a1e95b89e40027d1'
PLAYER_FILES = {'Build/WebGL-Build.loader.js', 'Build/WebGL-Build.data.unityweb',
                'Build/WebGL-Build.framework.js.unityweb', 'Build/WebGL-Build.wasm.unityweb'}
FILES = PLAYER_FILES | {'BUILD_PROVENANCE.json'}


def inventory(root):
    result = {}
    for p in root.rglob('*'):
        require(not p.is_symlink(), 'Symlink in static/payload tree')
        if p.is_file(): result[p.relative_to(root).as_posix()] = sha(p)
    return result


def validate_payload(payload, expected_digest=CONTRACT_SHA):
    payload = Path(payload)
    require(not payload.is_symlink(), 'Symlink payload root')
    require(sha(payload / 'CONTRACT.json') == expected_digest, 'Candidate contract hash differs')
    c = json.loads((payload / 'CONTRACT.json').read_text())
    require(c['baseFrozenCommit'] == SOURCE_COMMIT and c['candidateCommit'] == CANDIDATE_COMMIT,
            'Candidate/base identity differs')
    require(c['uiAccepted'] is False and c['anatomyAccepted'] is False, 'Candidate improperly accepted')
    require(set(c['files']) == FILES, 'Unexpected overlay path set')
    require(set(inventory(payload)) == FILES | {'CONTRACT.json'}, 'Unexpected payload member')
    for name, row in c['files'].items():
        p = payload / name
        require(p.stat().st_size == row['bytes'] and sha(p) == row['sha256'], 'Candidate bytes differ: '+name)
    p = json.loads((payload / 'BUILD_PROVENANCE.json').read_text())
    require(p['sourceCommit'] == CANDIDATE_COMMIT and p['baseFrozenCommit'] == SOURCE_COMMIT, 'Build identity differs')
    require(p['wrapperSha256'] == c['wrapperSha256'], 'Wrapper bindings differ')
    require(p['sourceHashes']['Assets/Scripts/BrainView/RegionManager.cs'] ==
            'd573515523a2600db6db8e42eb97cbac1aeacc1c7fe2469c2ef84779cbee0184', 'Guard source differs')
    require(p['outputHashes'] == {n:c['files'][n]['sha256'] for n in PLAYER_FILES}, 'Build output bindings differ')
    return c


def install_candidate(static, payload, report, expected_digest=CONTRACT_SHA):
    static, payload, report = Path(static), Path(payload), Path(report)
    require(static.is_dir() and not static.is_symlink() and not report.exists(), 'Invalid output destination')
    c = validate_payload(payload, expected_digest)
    before = inventory(static)
    require(before.get('unity/index.html') == c['wrapperSha256'], 'Frozen bridge wrapper differs')
    require(all('unity/'+n in before for n in FILES), 'Expected frozen viewer assets missing')
    for name in sorted(FILES): shutil.copyfile(payload / name, static / 'unity' / name)
    after = inventory(static)
    require(before.keys() == after.keys(), 'Static inventory changed')
    changed = {n for n in before if before[n] != after[n]}
    require(changed <= {'unity/'+n for n in FILES}, 'Unrelated static content changed')
    for name, row in c['files'].items(): require(after['unity/'+name] == row['sha256'], 'Installed hash differs')
    result = dict(status='STAGED_DERIVED_VIEWER_NOT_FROZEN_VIEWER_OR_UI_ACCEPTANCE',
        baseFrozenCommit=SOURCE_COMMIT, candidateCommit=CANDIDATE_COMMIT,
        contractSha256=expected_digest, before=before, after=after, changed=sorted(changed),
        uiAccepted=False, anatomyAccepted=False)
    write(report, result)
    return result
