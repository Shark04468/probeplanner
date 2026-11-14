"""Materialize only hash-matched live frozen-backend mesh responses."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request

from qualify_macos import SOURCE_COMMIT, require, sha, write

MAX_BYTES = 192 * 1024**2
OPTIMIZER_SHA = '5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9'
PROFILE_SHA = '3580f40eb42b475319ea62796ef8c7b8789b1d1110a9ca4638f8e2168b09c677'
MESH_URL = re.compile(r'/api/(?:allen_mesh(?:_lod)?/[0-9]+|mri_surface_mesh(?:_lod)?/[a-z_]+)(?:\?lod=[012])?\Z')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('A local verified response must not redirect')


def validate_contract(contract):
    require(contract['sourceCommit'] == SOURCE_COMMIT, 'Source differs')
    require(contract['optimizerSha256'] == OPTIMIZER_SHA, 'Optimizer differs')
    require(contract['profileContractSha256'] == PROFILE_SHA, 'Profile differs')
    seen = set()
    for row in contract['assets']:
        require(row['kind'] == 'mesh' and MESH_URL.fullmatch(row['url']), 'Not an allowed mesh URL')
        require(row['url'] not in seen, 'Duplicate URL')
        seen.add(row['url'])
        require(isinstance(row['bytes'], int) and 0 < row['bytes'] <= MAX_BYTES, 'Invalid byte bound')
        require(re.fullmatch('[0-9a-f]{64}', row['sha256']), 'Invalid SHA')
    require(bool(seen), 'Empty response contract')


def validate_headers(row, headers, status):
    headers = {k.lower(): v for k, v in headers.items()}
    require(status == 200, 'HTTP status differs')
    require(int(headers['content-length']) == row['bytes'], 'Response length differs')
    require(headers['content-type'].startswith('text/plain'), 'Not a mesh')
    require(not headers.get('content-encoding'), 'Unexpected encoding')
    require(int(headers['x-lod']) >= 0, 'Missing LOD')
    if row['url'].startswith('/api/allen_mesh'):
        require(headers['x-structure-id'] == row['url'].split('?')[0].rsplit('/', 1)[1], 'Structure differs')
    if '?lod=' in row['url']:
        require(headers['x-lod-requested'] == row['url'].split('?lod=')[1], 'LOD request differs')
    return headers


def receive(opener, base, row, bodies, deadline, lock):
    start = time.monotonic()
    result = dict(row, status='FAIL', body='bodies/'+row['sha256']+'.obj')
    try:
        require(start < deadline, 'Preparation deadline exceeded')
        with lock:
            target = bodies/(row['sha256']+'.obj')
            partial = bodies/(row['sha256']+'.partial')
            if target.exists():
                require(target.stat().st_size == row['bytes'] and sha(target) == row['sha256'], 'Stored response changed')
            request = urllib.request.Request(base+row['url'], headers={'Accept-Encoding': 'identity'})
            with opener.open(request, timeout=30) as response:
                result['headers'] = validate_headers(row, response.headers, response.status)
                digest, count = hashlib.sha256(), 0
                sink = partial.open('xb') if not target.exists() else None
                try:
                    while True:
                        block = response.read(65536)
                        if not block:
                            break
                        count += len(block)
                        require(count <= row['bytes'], 'Oversized response')
                        require(time.monotonic() < min(start+90, deadline), 'Response deadline exceeded')
                        digest.update(block)
                        if sink:
                            sink.write(block)
                finally:
                    if sink:
                        sink.close()
                require(count == row['bytes'] and digest.hexdigest() == row['sha256'], 'Response SHA/size differs')
                if sink:
                    partial.rename(target)
            result.update(status='PASS_ACTUAL_RESPONSE_IDENTITY', verificationMethod='GET')
    except Exception as error:
        result['error'] = type(error).__name__+': '+str(error)
    result['seconds'] = time.monotonic()-start
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', required=True)
    p.add_argument('--contract', type=Path, required=True)
    p.add_argument('--static', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    base = urllib.parse.urlsplit(a.base)
    require(base.scheme == 'http' and base.hostname == '127.0.0.1' and base.port
            and not base.username and not base.password and not base.query and not base.fragment
            and base.path == '', 'Explicit loopback backend required')
    contract = json.loads(a.contract.read_text())
    validate_contract(contract)
    require(len(contract['assets']) == 3264, 'Original response inventory differs')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    bodies = out/'bodies'
    bodies.mkdir()
    start = time.monotonic()
    rows = []
    result = {'status': 'FAIL', 'contract_sha256': sha(a.contract), 'rows': rows,
              'anatomy_accepted': False, 'cold_resource_latency': False,
              'backend_cache_seeded': False, 'source_commit': SOURCE_COMMIT}
    try:
        require(shutil.disk_usage(out).free >= 6_000_000_000, 'Disk reserve insufficient')
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(a.base+'/api/paper/runtime', timeout=30) as response:
            runtime = json.load(response)
        for key in ('optimizerSha256', 'profileContractSha256'):
            require(runtime[key] == contract[key], 'Runtime identity differs')
        require(runtime['manifestEnforced'] and runtime['pathConsistencyPassed'], 'Runtime not bound')
        result['runtime'] = runtime
        groups = {}
        locks = {row['sha256']: threading.Lock() for row in contract['assets']}
        for row in contract['assets']:
            groups.setdefault(row['url'].split('?')[0].rsplit('/', 1)[1], []).append(row)
        deadline = start+1500
        def group_work(group):
            return [receive(opener, a.base, row, bodies, deadline, locks[row['sha256']]) for row in group]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(group_work, group) for group in groups.values()]
            for future in concurrent.futures.as_completed(futures):
                batch = future.result()
                rows.extend(batch)
                with (out/'RESPONSES.jsonl').open('a') as f:
                    for row in batch:
                        f.write(json.dumps(row)+'\n')
                state = {'completed': len(rows), 'expected': len(contract['assets']),
                         'failures': sum(r['status']=='FAIL' for r in rows), 'seconds': time.monotonic()-start}
                (out/'STATE.json').write_text(json.dumps(state)+'\n')
                if len(rows) % 100 < 4:
                    print(json.dumps(state), flush=True)
                if state['failures'] or time.monotonic() >= deadline or shutil.disk_usage(out).free < 6_000_000_000:
                    for pending in futures:
                        pending.cancel()
                    raise RuntimeError('Preparation failure/deadline/disk reserve; queued work cancelled')
        require(len(rows)==len(contract['assets']) and all(r['status'].startswith('PASS_') for r in rows), 'Mesh identity failure')
        static = a.static.resolve()
        for path in sorted(static.rglob('*')):
            if not path.is_file() or path.suffix == '.map':
                continue
            require(not path.is_symlink() and static in path.resolve().parents, 'Static path escapes build')
            relative = path.relative_to(static).as_posix()
            size, digest = path.stat().st_size, sha(path)
            require(0 < size <= MAX_BYTES, 'Static response exceeds bound')
            for url in (['/index.html', '/'] if relative=='index.html' else ['/'+relative]):
                request = urllib.request.Request(a.base+url, headers={'Accept-Encoding': 'identity'})
                with opener.open(request, timeout=30) as response:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    require(response.status==200 and int(headers['content-length'])==size, 'Static header mismatch')
                    actual, count = hashlib.sha256(), 0
                    for block in iter(lambda: response.read(65536), b''):
                        count += len(block)
                        require(count <= size and time.monotonic() < deadline, 'Static response bound exceeded')
                        actual.update(block)
                    require(count == size and actual.hexdigest() == digest, 'Static body differs from current frozen-source build')
                rows.append({'url': url, 'kind': 'static', 'bytes': size, 'sha256': digest,
                             'staticRelativePath': relative, 'headers': headers,
                             'verificationMethod': 'GET_AND_LOCAL_BUILD_SHA', 'status': 'PASS_STATIC_IDENTITY'})
        require(time.monotonic() < deadline, 'Total preparation deadline exceeded')
        with opener.open(a.base+'/api/paper/runtime', timeout=30) as response:
            after = json.load(response)
        for key in ('optimizerSha256', 'profileContractSha256'):
            require(after[key] == runtime[key], 'Runtime changed during preparation')
        manifest = {k: contract[k] for k in ('sourceCommit','optimizerSha256','profileContractSha256')}
        manifest.update(assets=rows, responseContractSha256=sha(a.contract), staticRoot=str(static),
                        candidatesRoot=str(out), role='actual_mac_warm_resources_only')
        write(out/'MIRROR_MANIFEST.json', manifest)
        result.update(status='PASS_WARM_RESPONSES_ONLY', manifest_sha256=sha(out/'MIRROR_MANIFEST.json'))
    except Exception as error:
        result['error'] = type(error).__name__+': '+str(error)
    finally:
        result['seconds'] = time.monotonic()-start
        write(out/'RESULTS.json', result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('rows','runtime')}), flush=True)
    return 0 if result['status'].startswith('PASS_') else 1


if __name__=='__main__':
    raise SystemExit(main())
