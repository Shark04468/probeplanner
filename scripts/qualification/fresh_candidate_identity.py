"""Fail-closed identity checks for the explicitly derived application candidate."""
import json
from pathlib import Path
import subprocess

from qualify_macos import require, sha

CONTRACT_PATH = Path(__file__).with_name('fresh_candidate_contract.json')


def contract():
    return json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))


def verify_source(source):
    c = contract()
    git = lambda *args: subprocess.check_output(['git', *args], cwd=source, text=True).strip()
    require(git('rev-parse', 'HEAD') == c['sourceCommit'], 'Candidate commit differs')
    require(not git('status', '--porcelain'), 'Candidate source dirty')
    names = set(git('ls-files').splitlines())
    require(names == set(c['files']), 'Tracked source inventory differs')
    for name, expected in c['files'].items():
        p = (source / name).resolve()
        require(source.resolve() in p.parents and not (source/name).is_symlink(), 'Source path escapes')
        require(sha(p) == expected, 'Source hash differs: '+name)
    return dict(sourceCommit=c['sourceCommit'], filesVerified=len(names), contractSha256=sha(CONTRACT_PATH))


def verify_runtime(runtime, c=None):
    c = c or contract()
    for key in ['optimizerSha256', 'profileContractSha256']:
        require(runtime[key] == c[key], 'Runtime differs: '+key)
    for key in ['manifestEnforced', 'pathConsistencyPassed', 'cacheInitiallyEmpty']:
        require(runtime[key] is True, 'Runtime gate failed: '+key)
    require(runtime['workers'] == 1, 'Candidate workers changed')
    require(runtime['riskQueryPolicySha256'] == c['files']['apps/backend-compat/src/backend/risk_query.py'],
            'Exact query implementation differs')
    policy = runtime['riskQueryPolicy']
    require(policy['requestedWorkers'] == 4 and policy['exact'] is True, 'Query policy differs')
    require(all(type(policy[k]) is int and policy[k] >= 1 for k in
                ['cpuLimit', 'runtimeThreadLimit', 'effectiveWorkers']), 'Invalid query limits')
    require(policy['effectiveWorkers'] == min(4, policy['cpuLimit'], policy['runtimeThreadLimit']),
            'Unbounded or unexpected query workers')
    require(policy['k'] == 1 and policy['p'] == 2 and policy['eps'] == 0, 'Approximate query rejected')
    return runtime


def validate_fresh_roots(preparation, fresh):
    preparation, fresh = preparation.resolve(), fresh.resolve()
    require(preparation != fresh and preparation not in fresh.parents and fresh not in preparation.parents,
            'Preparation and fresh runtimes must be separate')
    require(not fresh.exists(), 'Fresh runtime already exists')


def derived_mesh_contract(original):
    c = contract()
    require(original['sourceCommit'] == c['baseFrozenCommit'], 'Unexpected mesh donor')
    require(original['optimizerSha256'] == '5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9',
            'Unexpected mesh donor optimizer')
    require(original['profileContractSha256'] == c['profileContractSha256'], 'Mesh profile differs')
    derived = dict(original, sourceCommit=c['sourceCommit'], optimizerSha256=c['optimizerSha256'])
    derived['role'] = 'same_expected_mesh_bytes_new_backend_identity_requires_live_reverification'
    return derived
