"""Fresh frozen-source Mac UI execution, without changing the paper application."""
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

from qualify_macos import SOURCE_COMMIT, junit_summary, require, run_step, sha, write
from replay_macos import BASE_URL, BUNDLE_SHA, PROFILE_SHA
from frontend_junit import frontend_summary


def pixel_audit(out):
    from PIL import Image, ImageChops, ImageStat
    names = ['desktop-complete', 'desktop-after-drag', 'desktop-new-slice', 'mobile-complete']
    records = {}
    images = {}
    for name in names:
        p = out / (name + '-canvas.png')
        with Image.open(p) as original:
            im = original.convert('RGB')
        stats = ImageStat.Stat(im)
        colors = im.resize((160, 160)).getcolors(25600) or []
        record = dict(size=list(im.size), sha256=sha(p), stddev=stats.stddev,
                      sampled_unique_colors=len(colors))
        require(min(im.size) >= 200, 'Canvas capture too small: ' + name)
        require(max(stats.stddev) > 5 and len(colors) >= 50, 'Blank/flat canvas: ' + name)
        images[name] = im
        records[name] = record
    for name in ['desktop-after-drag', 'desktop-new-slice']:
        require(images[name].size == images['desktop-complete'].size, 'Canvas sizes differ')
        delta = ImageStat.Stat(ImageChops.difference(images[name], images['desktop-complete'])).mean
        records[name]['mean_pixel_difference_from_initial'] = delta
        require(max(delta) > 0.5, 'Interaction did not visibly change canvas: ' + name)
    return dict(status='PASS_PIXELS_ONLY_VISUAL_REVIEW_REQUIRED', captures=records)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--qualification', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--saved-plan-origin', type=Path)
    a = p.parse_args()
    source, qualified, out, data = (x.resolve() for x in (a.source, a.qualification, a.output, a.data))
    roots = [source, qualified, out, data]
    require(all(x != y and x not in y.parents and y not in x.parents
                for i, x in enumerate(roots) for y in roots[i+1:]), 'Use separate roots')
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status='FAIL', source_commit=SOURCE_COMMIT, steps=[],
                  full_macos_workflow_accepted=False, anatomy_accepted=False,
                  new_untouched_evidence=False, manuscript_or_release_changed=False)
    process = None
    log = None
    try:
        require(platform.system() == 'Darwin', 'Actual Mac required')
        q = json.loads((qualified / 'RESULTS.json').read_text())
        require(q['status'] == 'PASS_PREREQUISITES_ONLY', 'Prerequisites failed')
        for name, expected in q['files'].items():
            require(sha(qualified / name) == expected, 'Prerequisite hash mismatch: ' + name)
        junit_summary(qualified / 'BACKEND_TESTS.xml')
        require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() == SOURCE_COMMIT, 'Source differs')
        require(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True).strip(), 'Source dirty')
        require(subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.14.0', 'Node version differs')
        saved_origin = None
        if a.saved_plan_origin:
            saved_origin = json.loads(a.saved_plan_origin.read_text(encoding='utf-8'))
            require(saved_origin['role'] == 'actual_saved_plan_scene_inspection_only', 'Saved-plan role differs')
            require(saved_origin['source_commit'] == SOURCE_COMMIT, 'Saved source differs')
            require(saved_origin['filename'] == 'actual-plan.pplan', 'Saved filename differs')
            saved = a.saved_plan_origin.parent / saved_origin['filename']
            require(sha(saved) == saved_origin['sha256'], 'Saved bytes differ')
            retained = out / 'saved_input'
            retained.mkdir()
            shutil.copyfile(saved, retained / saved_origin['filename'])
            shutil.copyfile(a.saved_plan_origin, retained / 'ORIGIN.json')
        role = 'actual_saved_plan_scene_inspection_only' if saved_origin else 'actual_first_request_ui_portability'
        report['workflow_role'] = role
        write(out / 'RUN_CONTRACT.json', dict(role=role,
            source_commit=SOURCE_COMMIT, harness_commit=os.environ.get('GITHUB_SHA'),
            host=platform.platform(), architecture=platform.machine(),
            qualification_sha256=sha(qualified / 'RESULTS.json'),
            source_locks={f: sha(source / f) for f in ['package-lock.json', 'apps/ui/package-lock.json']},
            scripts={f.name: sha(f) for f in Path(__file__).parent.glob('*') if f.is_file()},
            expected_profile_sha256=PROFILE_SHA, bundle_sha256=BUNDLE_SHA,
            saved_plan_origin=saved_origin, allowed_optimization_requests=0 if saved_origin else 1,
            visual_review_required=True, screenshot_anatomy_validation=False,
            budgets_seconds=dict(startup=600, planning=600, complete_scene=1800, driver=4800),
            browser_library='frozen Playwright 1.58.2 standalone driver, not a new spec'))
        def step(name, command, timeout, cwd=source):
            record = run_step(out, cwd, name, command, timeout)
            report['steps'].append(record)
        step('npm_root', ['npm', 'ci', '--no-audit', '--no-fund'], 600)
        step('npm_ui', ['npm', 'ci', '--no-audit', '--no-fund'], 600, source / 'apps/ui')
        step('frontend_tests', ['npm', 'run', 'test:frontend', '--', '--reporter=junit',
             '--outputFile=' + str(out / 'FRONTEND_TESTS.xml')], 600, source / 'apps/ui')
        report['frontend_tests'] = frontend_summary(out / 'FRONTEND_TESTS.xml')
        step('build_ui', ['npm', 'run', 'build'], 600, source / 'apps/ui')
        step('install_browser', ['node', 'node_modules/playwright/cli.js', 'install', 'chromium'], 600, source / 'apps/ui')
        script = Path(__file__).resolve().parent
        if not saved_origin:
            step('seal_request', ['node', str(script / 'ui_contract.cjs'), str(source), str(out / 'REQUEST_CONTRACT.json')], 60)
        data.mkdir(parents=True, exist_ok=False)
        step('fetch_profile', [sys.executable, 'scripts/release/fetch_public_profile.py', '--base-url', BASE_URL,
            '--manifest-sha256', BUNDLE_SHA, '--downloads', str(data / 'downloads'),
            '--profile', str(data / 'profile'), '--report', str(out / 'PROFILE_DOWNLOAD.json')], 1500)
        require(sha(data / 'profile/paper_profile.json') == PROFILE_SHA, 'Profile contract differs')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        base = 'http://127.0.0.1:' + str(port)
        command = [sys.executable, 'apps/backend-compat/scripts/start_paper_backend.py',
            '--profile', str(data / 'profile'), '--run-dir', str(data / 'runtime'),
            '--static-dir', str(source / 'apps/ui/build'), '--port', str(port)]
        write(out / 'BACKEND_COMMAND.json', dict(command=command, base=base, cache_initially_empty=True))
        log = (out / 'backend.log').open('x', encoding='utf-8')
        process = subprocess.Popen(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 600
        runtime = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urllib.request.urlopen(base + '/api/paper/runtime', timeout=3) as r:
                    runtime = json.load(r)
                break
            except (OSError, ValueError):
                time.sleep(2)
        require(runtime and runtime['profileContractSha256'] == PROFILE_SHA, 'Cold HTTP startup failed')
        require(runtime['optimizerSha256'] == '5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9', 'Optimizer differs')
        require(all(runtime[k] is True for k in ['manifestEnforced', 'pathConsistencyPassed', 'cacheInitiallyEmpty']), 'Runtime gate failed')
        write(out / 'COLD_RUNTIME.json', runtime)
        browser_command = ['node', str(script / 'live_ui.cjs'), str(source), str(out), base]
        if saved_origin:
            browser_command.append(str(out / 'saved_input/ORIGIN.json'))
        step('actual_ui', browser_command, 4800)
        report['pixels'] = pixel_audit(out)
        write(out / 'PIXEL_AUDIT.json', report['pixels'])
        browser = json.loads((out / 'BROWSER_RESULT.json').read_text())
        expected_status = ('PASS_AUTOMATED_SAVED_PLAN_SCENE_PENDING_PIXEL_AND_VISUAL_REVIEW' if saved_origin else
                           'PASS_AUTOMATED_UI_PENDING_PIXEL_AND_VISUAL_REVIEW')
        require(browser['status'] == expected_status, 'Browser stage failed')
        # Separate contexts: fixture tests do not contribute to the real-plan evidence above.
        os.environ['REACT_APP_FLASK_BASE'] = base
        os.environ['PLAYWRIGHT_JUNIT_OUTPUT_FILE'] = str(out / 'BROWSER_REGRESSIONS.xml')
        step('frozen_browser_regressions', ['node', 'node_modules/playwright/cli.js', 'test',
             '--retries=0', '--workers=1', '--trace=on', '--reporter=junit',
             '--output=' + str(out / 'browser-regression-artifacts')], 900, source / 'apps/ui')
        report['browser_regressions'] = junit_summary(out / 'BROWSER_REGRESSIONS.xml', 11)
        profile_contract = json.loads((data / 'profile/paper_profile.json').read_text())
        require(sha(data / 'profile/paper_profile.json') == PROFILE_SHA, 'Postflight profile contract differs')
        for name, expected in profile_contract['files'].items():
            require(sha(data / 'profile' / name) == expected, 'Postflight profile member differs: ' + name)
        report['profile_members_reverified'] = len(profile_contract['files'])
        require(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True).strip(), 'Frozen source changed')
        report['status'] = ('PASS_AUTOMATED_SAVED_PLAN_SCENE_PENDING_INDEPENDENT_ARTIFACT_REVIEW' if saved_origin else
                            'PASS_AUTOMATED_MAC_UI_PENDING_INDEPENDENT_ARTIFACT_REVIEW')
    except Exception as e:
        report['error'] = type(e).__name__ + ': ' + str(e)
        report['traceback'] = traceback.format_exc()
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=30)
            report['backend_terminal_code'] = process.returncode
        if log:
            log.close()
        runtime_dir = data / 'runtime'
        if runtime_dir.exists():
            retained = out / 'runtime_records'
            retained.mkdir()
            for f in runtime_dir.rglob('*.json'):
                if 'cache' in f.relative_to(runtime_dir).parts:
                    continue
                dest = retained / f.relative_to(runtime_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
        report['source_dirty_after'] = subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True).strip()
        report['files'] = {f.relative_to(out).as_posix(): sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
        write(out / 'RESULTS.json', report)
        print(json.dumps(dict(status=report['status'], error=report.get('error'))), flush=True)
    return 0 if report['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
