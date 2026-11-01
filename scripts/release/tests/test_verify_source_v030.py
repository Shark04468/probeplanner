"""Negative checks for source drift and the portable audit wrapper."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'verify_source_v030.py'
spec = importlib.util.spec_from_file_location('verify_source_v030', SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture_file(root, name, data=b'original'):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_unchanged_evaluated_file(tmp_path):
    p = fixture_file(tmp_path, 'apps/optimizer.py')
    report = gate.compare_source(tmp_path, {'apps/optimizer.py': gate.sha(p)}, gate.list_files(tmp_path))
    assert not report['errors'] and not report['changes']


@pytest.mark.parametrize('name', [
    'apps/backend-compat/src/optimizer_server.py', 'apps/ui/src/ProbeplannerGUI.jsx',
    'apps/backend-compat/tests/test_optimizer_server.py',
    'scripts/release/verify_release.py',
])
def test_implementation_and_original_tests_cannot_change(tmp_path, name):
    p = fixture_file(tmp_path, name)
    before = gate.sha(p)
    p.write_bytes(b'edited')
    report = gate.compare_source(tmp_path, {name: before}, gate.list_files(tmp_path))
    assert any('implementation changed' in e for e in report['errors'])


def test_missing_evaluated_file_fails(tmp_path):
    report = gate.compare_source(tmp_path, {'apps/optimizer.py': 'a' * 64}, {})
    assert 'Evaluated file missing: apps/optimizer.py' in report['errors']


def test_packaging_change_is_reported_not_silent(tmp_path):
    p = fixture_file(tmp_path, 'README.md')
    before = gate.sha(p)
    p.write_bytes(b'release documentation')
    report = gate.compare_source(tmp_path, {'README.md': before}, gate.list_files(tmp_path))
    assert not report['errors']
    assert report['changes']['README.md'] == {'evaluatedSha256': before, 'packagedSha256': gate.sha(p)}


def test_new_scientific_module_rejected(tmp_path):
    fixture_file(tmp_path, 'apps/backend-compat/src/new_planner.py')
    assert gate.compare_source(tmp_path, {}, gate.list_files(tmp_path))['errors']


def test_new_release_script_is_in_inventory(tmp_path):
    p = fixture_file(tmp_path, 'scripts/release/new_gate.py')
    report = gate.compare_source(tmp_path, {}, gate.list_files(tmp_path))
    assert not report['errors']
    assert report['additions']['scripts/release/new_gate.py'] == gate.sha(p)


def test_deleted_launcher_cannot_reappear(tmp_path):
    fixture_file(tmp_path, gate.ABSENT_LAUNCHER)
    assert any('must not be restored' in e for e in gate.compare_source(tmp_path, {}, gate.list_files(tmp_path))['errors'])


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/local', 'a\\b',
                                 'a/../b', 'a//b', './a', '.git/config', 'a./b', 'a /b'])
def test_unsafe_path_rejected(name):
    assert not gate.safe_member(name)


def test_inventory_duplicate_json_key_rejected(tmp_path):
    path = fixture_file(tmp_path, 'bad.json', b'{"files": {}, "files": {"x": "y"}}')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        gate.read_json(path)


def test_inventory_tamper_rejected_before_processing(tmp_path):
    fixture_file(tmp_path, gate.INVENTORY, b'{}')
    with pytest.raises(ValueError, match='inventory SHA-256 mismatch'):
        gate.verify(tmp_path)


def test_case_colliding_inventory_rejected(tmp_path):
    report = gate.compare_source(tmp_path, {'a.py': 'a' * 64, 'A.py': 'b' * 64}, {})
    assert 'Case-colliding evaluated paths' in report['errors']


def test_generation_cache_not_treated_as_release_source(tmp_path):
    fixture_file(tmp_path, '__pycache__/module.pyc')
    fixture_file(tmp_path, 'node_modules/package/index.js')
    fixture_file(tmp_path, 'apps/ui/build/index.html')
    fixture_file(tmp_path, 'apps/ui/public/unity/Build/WebGL.loader.js')
    assert list(gate.list_files(tmp_path)) == ['apps/ui/public/unity/Build/WebGL.loader.js']


def test_symlink_not_followed(tmp_path):
    fixture_file(tmp_path, 'target.txt')
    link = tmp_path / 'link.txt'
    try:
        link.symlink_to(tmp_path / 'target.txt')
    except OSError:
        pytest.skip('Host does not permit unprivileged symlinks')
    with pytest.raises(ValueError, match='Link is not'):
        gate.list_files(tmp_path)


def test_windows_junction_not_followed(tmp_path):
    import os
    if os.name != 'nt':
        pytest.skip('Windows reparse-point test')
    shell = shutil.which('pwsh') or shutil.which('powershell')
    assert shell, 'Windows test environment requires PowerShell'
    target = tmp_path / 'target'
    target.mkdir()
    driver = tmp_path / 'make-junction.ps1'
    driver.write_text('''param([string]$Link, [string]$Target)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null
''', encoding='utf-8')
    run = subprocess.run([shell, '-NoProfile', '-File', str(driver),
                          '-Link', str(tmp_path / 'junction'), '-Target', str(target)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    with pytest.raises(ValueError, match='Link is not'):
        gate.list_files(tmp_path)


WRAPPER = gate.ROOT / 'apps/backend-compat/scripts/run_allensdk_atlas_audit.ps1'


def test_wrapper_has_no_machine_specific_fallback():
    text = WRAPPER.read_text(encoding='utf-8')
    assert 'anaconda' not in text.lower()
    assert 'PROBEPLANNER_ALLENSDK_PYTHON' in text
    assert '$Python = "python"' in text


@pytest.mark.parametrize('source', ['explicit', 'environment', 'path'])
def test_wrapper_selects_interpreter_and_preserves_exit_code(tmp_path, source):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if shell is None:
        pytest.skip('PowerShell not installed')
    import os
    wrapper = tmp_path / WRAPPER.name
    shutil.copyfile(WRAPPER, wrapper)
    fixture_file(tmp_path, 'audit_allensdk_atlas_resources.py', b'# Deliberate no-op placeholder; not executed.\n')
    driver = tmp_path / 'driver.ps1'
    # Intercept the invocation in process: no Python/data download/audit is run.
    driver.write_text('''param([string]$Wrapper, [string]$Mode)
$ErrorActionPreference = 'Stop'
function fake-explicit { $args | ConvertTo-Json -Compress; $global:LASTEXITCODE = 23 }
function fake-environment { $args | ConvertTo-Json -Compress; $global:LASTEXITCODE = 23 }
function python { $args | ConvertTo-Json -Compress; $global:LASTEXITCODE = 23 }
$env:PROBEPLANNER_ALLENSDK_PYTHON = if ($Mode -eq 'path') { '' } else { 'fake-environment' }
$params = @{ArtifactRoot='atlas root'; OutDir='output directory'; EvidenceDir='evidence directory'}
if ($Mode -eq 'explicit') { $params.Python = 'fake-explicit' }
& $Wrapper @params
exit $LASTEXITCODE
''', encoding='utf-8')
    run = subprocess.run([shell, '-NoProfile', '-File', str(driver), '-Wrapper', str(wrapper), '-Mode', source],
                         capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert run.returncode == 23, run.stdout + run.stderr
    expected = {'explicit': 'fake-explicit', 'environment': 'fake-environment', 'path': 'python'}[source]
    assert f'[allensdk-audit] python={expected}' in run.stdout
    args = json.loads(next(line for line in run.stdout.splitlines() if line.startswith('[') and not line.startswith('[allensdk-audit]')))
    assert args[1:] == ['--out', 'output directory', '--evidence-dir', 'evidence directory', '--artifact-root', 'atlas root']
