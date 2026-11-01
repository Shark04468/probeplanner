"""Release counts and version routing must fail closed, without changing old tests."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_release_checks_v030 as checks


def report(tmp_path, count=112, failure=None, skipped=None, suite_errors=0):
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests=str(count), errors=str(suite_errors), failures='0')
    for i in range(count):
        case = ET.SubElement(suite, 'testcase', classname='unit', name='test_' + str(i))
        if failure is not None and i == 0:
            ET.SubElement(case, failure, message='failure retained')
        if skipped is not None and i == 0:
            case.set('classname', skipped[0])
            case.set('name', skipped[1])
            ET.SubElement(case, 'skipped', message='host privilege')
    path = tmp_path / 'junit.xml'
    ET.ElementTree(root).write(path, encoding='utf-8')
    return path


def test_exact_112_gate(tmp_path):
    result = checks.junit_counts(report(tmp_path), expected=112)
    assert result['countGatePassed'] and result['passed'] == 112


@pytest.mark.parametrize('count', [0, 111, 113])
def test_wrong_original_count_fails(tmp_path, count):
    assert not checks.junit_counts(report(tmp_path, count), expected=112)['countGatePassed']


@pytest.mark.parametrize('failure', ['failure', 'error'])
def test_failed_test_blocks_release(tmp_path, failure):
    assert not checks.junit_counts(report(tmp_path, failure=failure), expected=112)['countGatePassed']


def test_suite_level_error_cannot_be_hidden(tmp_path):
    assert not checks.junit_counts(report(tmp_path, suite_errors=1), expected=112)['countGatePassed']


def test_skip_in_original_subset_blocks_release(tmp_path):
    assert not checks.junit_counts(report(tmp_path, skipped=checks.ALLOWED_SKIP), expected=112)['countGatePassed']


def test_documented_windows_privilege_skip_only_for_packaging(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    result = checks.junit_counts(report(tmp_path, 170, skipped=checks.ALLOWED_SKIP), packaging=True)
    assert result['countGatePassed'] and result['passed'] == 169


def test_skip_cannot_be_used_on_nonwindows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert not checks.junit_counts(report(tmp_path, 170, skipped=checks.ALLOWED_SKIP), packaging=True)['countGatePassed']


def test_other_skip_fails_packaging_gate(tmp_path):
    assert not checks.junit_counts(report(tmp_path, 170, skipped=('other', 'test_missing')), packaging=True)['countGatePassed']


def test_packaging_minimum_count_cannot_disappear(tmp_path):
    assert not checks.junit_counts(report(tmp_path, 169), packaging=True)['countGatePassed']


def test_packaging_pass_without_skip(tmp_path):
    assert checks.junit_counts(report(tmp_path, 170), packaging=True)['countGatePassed']


def test_workflow_keeps_v030_out_of_old_autopublishing_job():
    import yaml
    path = checks.ROOT / '.github/workflows/paper-release.yml'
    workflow = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    old = workflow['jobs']['verify-and-release']
    assert 'v0.2.1-paper' in old['if'] and 'v0.3.0-paper' not in old['if']
    current = workflow['jobs']['verify-v030-draft']
    assert "github.event_name == 'workflow_dispatch'" in current['if']
    assert current['permissions']['contents'] == 'read'
    scripts = '\n'.join(step.get('run', '') for step in current['steps'])
    assert 'run_release_checks_v030.py' in scripts and '--require-clean' in scripts
    assert '--tag $env:STAGED_TAG' not in scripts
    assert current['needs'] == 'download-v030-draft'
    checkout = next(step for step in current['steps'] if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['ref'] == '${{ inputs.source_commit }}'
    assert checkout['with']['persist-credentials'] == 'false'
    assert 'gh release create' not in scripts and 'gh release edit' not in scripts
    assert '--case confirmation/reused_negative_control' in scripts
    downloader = workflow['jobs']['download-v030-draft']
    assert downloader['permissions']['contents'] == 'write'
    assert not any(step.get('uses', '').startswith('actions/checkout@') for step in downloader['steps'])
    download_commands = '\n'.join(step.get('run', '') for step in downloader['steps'])
    assert '"$CANDIDATE_COMMIT" == "$GITHUB_SHA"' in download_commands
    assert '.isDraft == true' in download_commands and '.targetCommitish == $sha' in download_commands
    assert 'gh release download' in download_commands
    for forbidden in ('gh release create', 'gh release edit', 'python ', 'npm ', 'git push', 'git tag'):
        assert forbidden not in download_commands


def test_release_preserves_evaluated_bytes_across_git_platforms():
    lines = (checks.ROOT / '.gitattributes').read_text().splitlines()
    rules = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith('#')]
    assert rules[-1] == '* -text'
