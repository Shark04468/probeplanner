"""Actual Mac fresh planning, followed by inspection with a bound derived viewer."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

from qualify_macos import junit_summary, require, run_step, sha, write
from replay_macos import BASE_URL, BUNDLE_SHA, PROFILE_SHA
from frontend_junit import frontend_summary
from install_viewer_candidate import install_candidate, CANDIDATE_COMMIT, CONTRACT_SHA
from run_lifecycle_candidate import pixel_audit
from fresh_candidate_identity import contract, verify_source, verify_runtime, validate_fresh_roots, derived_mesh_contract


def stop_owned(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
    return dict(pid=process.pid, returncode=process.returncode, reaped=process.poll() is not None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'qualification', 'output', 'data', 'warm-response-contract']:
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    source, qualified, out, data = (x.resolve() for x in (a.source, a.qualification, a.output, a.data))
    roots = [source, qualified, out, data]
    require(all(x != y and x not in y.parents and y not in x.parents
                for i, x in enumerate(roots) for y in roots[i+1:]), 'Separate roots required')
    out.mkdir(parents=True, exist_ok=False)
    c = contract()
    report = dict(status='FAIL', source_commit=c['sourceCommit'], base_frozen_commit=c['baseFrozenCommit'],
        viewer_candidate_commit=CANDIDATE_COMMIT, steps=[], process_cleanup=[],
        full_macos_workflow_accepted=False, anatomy_accepted=False, native_planning_accepted=False,
        cold_asset_delivery_accepted=False, new_untouched_evidence=False, frozen_runtime_used=False)
    processes, logs = [], []
    script = Path(__file__).resolve().parent
    try:
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Actual arm64 Mac required')
        report['source_before'] = verify_source(source)
        q = json.loads((qualified/'RESULTS.json').read_text())
        require(q['status'] == 'PASS_DERIVED_PREREQUISITES_ONLY' and q['source_commit'] == c['sourceCommit'],
                'Wrong prerequisites')
        for name, expected in q['files'].items():
            require(sha(qualified/name) == expected, 'Prerequisite hash mismatch: '+name)
        junit_summary(qualified/'BACKEND_TESTS.xml', 112)
        junit_summary(qualified/'FULL_BACKEND_TESTS.xml', 381)
        require(subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.14.0', 'Node differs')
        write(out/'RUN_CONTRACT.json', dict(role='actual_mac_derived_fresh_request_and_viewer',
            source_commit=c['sourceCommit'], base_frozen_commit=c['baseFrozenCommit'],
            backend_donor_commit=c['backendDonorCommit'], harness_commit=os.environ.get('GITHUB_SHA'),
            viewer_candidate_commit=CANDIDATE_COMMIT, candidate_contract_sha256=CONTRACT_SHA,
            host=platform.platform(), cpu_count=os.cpu_count(), qualification_sha256=sha(qualified/'RESULTS.json'),
            profile_sha256=PROFILE_SHA, bundle_sha256=BUNDLE_SHA,
            original_mesh_contract_sha256=sha(a.warm_response_contract),
            allowed_optimization_requests=1, saved_plan_forbidden=True,
            budgets_seconds=dict(startup=600, planning=600, complete_scene=1800, driver=4800),
            warm_resource_scope='Rendering responses only; preparation backend is reaped before fresh backend startup',
            scripts={f.name: sha(f) for f in script.glob('*') if f.is_file()}))

        def step(name, command, timeout, cwd=source):
            row = run_step(out, cwd, name, command, timeout)
            report['steps'].append(row)
            return row

        step('npm_root', ['npm', 'ci', '--no-audit', '--no-fund'], 600)
        step('npm_ui', ['npm', 'ci', '--no-audit', '--no-fund'], 600, source/'apps/ui')
        step('frontend_tests', ['npm', 'run', 'test:frontend', '--', '--reporter=junit',
            '--outputFile='+str(out/'FRONTEND_TESTS.xml')], 600, source/'apps/ui')
        report['frontend_tests'] = frontend_summary(out/'FRONTEND_TESTS.xml')
        step('build_ui', ['npm', 'run', 'build'], 600, source/'apps/ui')
        report['viewer_deployment'] = install_candidate(source/'apps/ui/build', script/'viewer_candidate',
                                                        out/'VIEWER_DEPLOYMENT.json')
        step('install_browser', ['node', 'node_modules/playwright/cli.js', 'install', 'chromium'], 600, source/'apps/ui')
        os.environ['PROBEPLANNER_TEST_DEPENDENCY_SOURCE'] = str(source)
        tests = sorted(set(script.glob('test_*.cjs')) | set(script.glob('*.test.cjs')))
        step('helper_tests', ['node', '--test', '--test-concurrency=1', *map(str, tests)], 300)
        step('seal_request', ['node', str(script/'ui_contract.cjs'), str(source), str(out/'REQUEST_CONTRACT.json')], 60)
        data.mkdir(parents=True, exist_ok=False)
        step('fetch_profile', [sys.executable, 'scripts/release/fetch_public_profile.py', '--base-url', BASE_URL,
            '--manifest-sha256', BUNDLE_SHA, '--downloads', str(data/'downloads'), '--profile', str(data/'profile'),
            '--report', str(out/'PROFILE_DOWNLOAD.json')], 1500)
        require(sha(data/'profile/paper_profile.json') == PROFILE_SHA, 'Profile contract differs')
        require(shutil.disk_usage(data).free >= 6_000_000_000, 'Insufficient disk reserve')

        def start_backend(name):
            runtime_dir = data/name
            require(not runtime_dir.exists(), 'Backend runtime is not fresh')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            base = 'http://127.0.0.1:'+str(port)
            command = [sys.executable, 'apps/backend-compat/scripts/start_paper_backend.py',
                '--profile', str(data/'profile'), '--run-dir', str(runtime_dir),
                '--static-dir', str(source/'apps/ui/build'), '--port', str(port), '--risk-query-workers', '4']
            write(out/(name+'-command.json'), dict(command=command, cache_initially_empty=True, base=base))
            log = (out/(name+'.log')).open('x', encoding='utf-8')
            logs.append(log)
            process = subprocess.Popen(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            deadline, runtime = time.monotonic()+600, None
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(base+'/api/paper/runtime', timeout=3) as response:
                        runtime = json.load(response)
                    break
                except (OSError, ValueError):
                    time.sleep(2)
            require(runtime is not None, 'Backend startup failed: '+name)
            verify_runtime(runtime)
            write(out/(name+'-runtime.json'), runtime)
            return process, base

        original_meshes = json.loads(a.warm_response_contract.read_text())
        derived = derived_mesh_contract(original_meshes)
        write(out/'DERIVED_MESH_CONTRACT.json', derived)
        prep, prep_base = start_backend('preparation_runtime')
        step('prepare_warm_assets', [sys.executable, str(script/'prepare_fresh_assets.py'), '--base', prep_base,
            '--contract', str(out/'DERIVED_MESH_CONTRACT.json'), '--static', str(source/'apps/ui/build'),
            '--output', str(data/'warm_assets')], 1650)
        warm = json.loads((data/'warm_assets/RESULTS.json').read_text())
        require(warm['status'] == 'PASS_WARM_RESPONSES_ONLY', 'Warm preparation failed')
        cleanup = stop_owned(prep)
        report['process_cleanup'].append(dict(role='preparation', **cleanup))
        require(cleanup['reaped'], 'Preparation backend not reaped')
        validate_fresh_roots(data/'preparation_runtime', data/'planning_runtime')
        write(out/'PREPARATION_ISOLATION.json', dict(preparation_pid=prep.pid, preparation_reaped=True,
            preparation_runtime=str(data/'preparation_runtime'), fresh_runtime=str(data/'planning_runtime'),
            fresh_runtime_does_not_exist=True, backend_cache_copied=False))
        fresh, base = start_backend('planning_runtime')
        require(fresh.pid != prep.pid, 'Unexpected immediate PID reuse')
        shutil.copyfile(data/'warm_assets/MIRROR_MANIFEST.json', out/'MIRROR_MANIFEST.json')
        step('actual_ui', ['node', str(script/'run_fresh_candidate_warm.cjs'), str(source), str(out), base,
            str(data/'warm_assets/MIRROR_MANIFEST.json')], 4800)
        browser = json.loads((out/'BROWSER_RESULT.json').read_text())
        require(browser['status'] == 'PASS_AUTOMATED_UI_PENDING_PIXEL_AND_VISUAL_REVIEW', 'UI failed')
        require(len(browser['requests']) == 1 and browser['requests'][0]['matches'], 'Fresh request count differs')
        require(browser['planningRequestClock']['completePlanElapsedMs'] <= 600000, 'Planning budget exceeded')
        report['pixels'] = pixel_audit(out)
        write(out/'PIXEL_AUDIT.json', report['pixels'])
        os.environ['REACT_APP_FLASK_BASE'] = base
        os.environ['PLAYWRIGHT_JUNIT_OUTPUT_FILE'] = str(out/'BROWSER_REGRESSIONS.xml')
        step('original_browser_regressions', ['node', 'node_modules/playwright/cli.js', 'test',
            '--retries=0', '--workers=1', '--trace=on', '--reporter=junit',
            '--output='+str(out/'browser-regression-artifacts')], 900, source/'apps/ui')
        report['browser_regressions'] = junit_summary(out/'BROWSER_REGRESSIONS.xml', 11)
        profile = json.loads((data/'profile/paper_profile.json').read_text())
        require(sha(data/'profile/paper_profile.json') == PROFILE_SHA, 'Profile contract changed')
        for name, expected in profile['files'].items():
            require(sha(data/'profile'/name) == expected, 'Profile member changed: '+name)
        report['source_after'] = verify_source(source)
        report['status'] = 'PASS_DERIVED_FRESH_UI_AUTOMATION_PENDING_INDEPENDENT_REVIEW'
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        for process in processes:
            try:
                report['process_cleanup'].append(stop_owned(process))
            except Exception as error:
                report['status'] = 'FAIL_CLEANUP'
                report.setdefault('cleanup_errors', []).append(str(error))
        for log in logs:
            log.close()
        for name in ['preparation_runtime', 'planning_runtime', 'warm_assets']:
            root = data/name
            if root.exists():
                for f in root.rglob('*'):
                    if f.is_file() and f.suffix in ('.json', '.jsonl') and 'cache' not in f.relative_to(root).parts:
                        dest = out/'retained_data'/name/f.relative_to(root)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(f, dest)
        report['files'] = {f.relative_to(out).as_posix(): sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
        write(out/'RESULTS.json', report)
        print(json.dumps(dict(status=report['status'], error=report.get('error'))), flush=True)
    return 0 if report['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
