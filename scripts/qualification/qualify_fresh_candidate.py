"""Run unchanged application tests on an actual Mac derived-source checkout."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

from qualify_macos import LOCK, TEST_FILES, junit_summary, require, run_step, sha, write
from fresh_candidate_identity import contract, verify_source


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    source, out = a.source.resolve(), a.output.resolve()
    require(source != out and source not in out.parents and out not in source.parents, 'Separate roots required')
    out.mkdir(parents=True, exist_ok=False)
    result = dict(status='FAIL', source_commit=contract()['sourceCommit'], steps=[],
                  frozen_source_used=False, full_macos_workflow_accepted=False, anatomy_accepted=False)
    try:
        result['source_before'] = verify_source(source)
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Actual arm64 Mac required')
        require(sys.version_info[:3] == (3, 10, 20), 'Python differs')
        write(out/'RUN_CONTRACT.json', dict(role='derived_backend_prerequisites_only',
            source=result['source_before'], host=platform.platform(), cpu_count=os.cpu_count(), python=sys.version,
            harness_commit=os.environ.get('GITHUB_SHA'), github_run_id=os.environ.get('GITHUB_RUN_ID'),
            expected_counts={'legacy': 112, 'full': 381}, counts_overlap=True,
            lock_sha256=sha(source/LOCK)))
        steps = [
            ('bootstrap', ['uv', 'pip', 'install', '--python', sys.executable,
                'pip==23.0.1', 'setuptools==79.0.1', 'wheel==0.48.0'], 180),
            ('install', [sys.executable, '-m', 'pip', 'install', '--no-cache-dir',
                '--report', str(out/'INSTALL_REPORT.json'), '-r', LOCK], 1500),
            ('pip_check', [sys.executable, '-m', 'pip', 'check'], 90),
            ('freeze', [sys.executable, '-m', 'pip', 'freeze', '--all'], 90),
            ('imports', [sys.executable, '-c',
                "import numpy, scipy, flask, allensdk, nibabel, ants, SimpleITK; print('Runtime imports passed')"], 180),
            ('legacy_tests', [sys.executable, '-m', 'pytest', *TEST_FILES, '-q',
                '--junitxml='+str(out/'BACKEND_TESTS.xml')], 600),
            ('full_tests', [sys.executable, '-m', 'pytest', 'apps/backend-compat/tests', '-q',
                '--junitxml='+str(out/'FULL_BACKEND_TESTS.xml')], 600),
        ]
        for name, command, timeout in steps:
            result['steps'].append(run_step(out, source, name, command, timeout))
            if name == 'legacy_tests':
                result['legacy_tests'] = junit_summary(out/'BACKEND_TESTS.xml', 112)
            if name == 'full_tests':
                result['full_tests'] = junit_summary(out/'FULL_BACKEND_TESTS.xml', 381)
        result['source_after'] = verify_source(source)
        result['status'] = 'PASS_DERIVED_PREREQUISITES_ONLY'
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
    finally:
        result['files'] = {f.relative_to(out).as_posix(): sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
        write(out/'RESULTS.json', result)
        print(json.dumps(dict(status=result['status'], error=result.get('error'))), flush=True)
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
