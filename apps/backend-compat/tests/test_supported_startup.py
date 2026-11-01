"""Supported commands must fail before touching unrelated processes or loading data."""
import argparse
import json
from pathlib import Path
import socket
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND / 'scripts'))
import start_paper_backend as launcher


def args(tmp_path, port, check_only=False, static_dir=None):
    return argparse.Namespace(profile=tmp_path / 'profile', run_dir=tmp_path / 'new run',
                              static_dir=static_dir, port=port, check_only=check_only)


def test_root_and_backend_package_use_explicit_paper_python():
    root = json.loads((ROOT / 'package.json').read_text())['scripts']
    backend = json.loads((BACKEND / 'package.json').read_text())['scripts']
    expected = 'python apps/backend-compat/scripts/start_paper_backend.py'
    assert root['backend:start'] == expected
    assert root['paper:start'] == expected
    assert backend['start'] == 'python scripts/start_paper_backend.py'


def test_supported_tests_include_the_whole_current_backend_suite():
    root = json.loads((ROOT / 'package.json').read_text())['scripts']
    backend = json.loads((BACKEND / 'package.json').read_text())['scripts']
    assert root['backend:test'] == 'python -m pytest apps/backend-compat/tests -q'
    assert backend['test'] == 'python -m pytest tests -q'


@pytest.mark.parametrize('name', ['start_dev_stack.ps1', 'start_desktop_stack.ps1'])
def test_combined_launchers_guard_missing_dependency_before_side_effects(name):
    text = (ROOT / 'scripts' / name).read_text(encoding='utf-8')
    guard = text.index('Test-Path -LiteralPath $LegacyBackendLauncher')
    assert guard < text.index('stop_local_stack.ps1')
    assert guard < text.index('Start-Process')
    assert 'npm run paper:start' in text[:text.index('stop_local_stack.ps1')]


def test_occupied_port_rejected_without_creating_run_dir(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        config = args(tmp_path, occupied.getsockname()[1])
        with pytest.raises(ValueError, match='port.*unavailable'):
            launcher.preflight(config)
        assert not config.run_dir.exists()
        assert occupied.fileno() >= 0


def test_check_only_does_not_require_a_free_listener_port(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        config = args(tmp_path, occupied.getsockname()[1], check_only=True)
        launcher.preflight(config)
        assert not config.run_dir.exists()


@pytest.mark.parametrize('port', [0, -1, 65536])
def test_port_range_is_checked(tmp_path, port):
    with pytest.raises(ValueError, match='port must be'):
        launcher.preflight(args(tmp_path, port))


def test_existing_run_is_preserved(tmp_path):
    config = args(tmp_path, 5039)
    config.run_dir.mkdir()
    marker = config.run_dir / 'do-not-overwrite.json'
    marker.write_bytes(b'original')
    with pytest.raises(ValueError, match='run-dir already exists'):
        launcher.preflight(config)
    assert marker.read_bytes() == b'original'


@pytest.mark.parametrize('kind', ['absent', 'no-index', 'index-is-directory'])
def test_invalid_static_directory_fails_before_atlas_load(tmp_path, kind):
    static = tmp_path / 'static assets'
    if kind != 'absent':
        static.mkdir()
    if kind == 'index-is-directory':
        (static / 'index.html').mkdir()
    config = args(tmp_path, 5039, check_only=True, static_dir=static)
    with pytest.raises(ValueError, match='static-dir.*index.html'):
        launcher.preflight(config)
    assert not config.run_dir.exists()


def test_valid_static_and_new_output_with_spaces_preflight(tmp_path):
    static = tmp_path / 'static assets'
    static.mkdir()
    (static / 'index.html').write_text('<!doctype html><title>Fixture</title>')
    config = args(tmp_path, 5039, check_only=True, static_dir=static)
    launcher.preflight(config)
    assert not config.run_dir.exists()


def test_main_rejects_occupied_port_before_expensive_loader(tmp_path, monkeypatch, capsys):
    def unexpected_import(*unused):
        raise AssertionError('Atlas loader was called before preflight')
    monkeypatch.setattr(launcher, 'load_paper_backend', unexpected_import)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        config = args(tmp_path, occupied.getsockname()[1])
        monkeypatch.setattr(sys, 'argv', ['start_paper_backend.py', '--profile', str(config.profile),
                                         '--run-dir', str(config.run_dir), '--port', str(config.port)])
        with pytest.raises(SystemExit) as error:
            launcher.main()
        assert error.value.code == 2
        assert 'port' in capsys.readouterr().err
        assert not config.run_dir.exists()
