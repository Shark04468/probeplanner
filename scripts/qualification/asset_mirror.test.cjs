// Synthetic transport tests only. No fixture response is used in the actual UI run.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createMirror, hash, bodyPath, assertIdentity } = require('./asset_mirror.cjs');
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve('http://127.0.0.1:' + server.address().port)));
const close = server => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
async function fixture(fn) {
  const fixtures = path.resolve(__dirname, '../../..', 'mirror_test_fixtures');
  await fs.mkdir(fixtures, { recursive: true });
  const root = await fs.mkdtemp(path.join(fixtures, 'case-'));
  const bytes = Buffer.from('v 1 2 3\n');
  await fs.writeFile(path.join(root, 'a.obj'), bytes);
  const received = [];
  const upstreamServer = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    received.push({ url: req.url, method: req.method, body: Buffer.concat(chunks).toString('hex') });
    res.writeHead(207, { 'content-type': 'application/octet-stream', 'x-real-upstream': 'fixture' });
    res.end(Buffer.from('actual-upstream-response'));
  });
  const upstream = await listen(upstreamServer);
  const entry = { url: '/api/allen_mesh_lod/382?lod=1', kind: 'mesh', body: 'a.obj',
    bytes: bytes.length, sha256: hash(bytes), headers: { 'content-type': 'text/plain',
      'cache-control': 'public, max-age=86400', 'x-lod': '0', 'x-lod-requested': '1', 'x-lod-fallback': '1' } };
  const mirror = createMirror({ upstream, entries: [entry], roots: { candidates: root, static: root }, expected: {} });
  const base = await listen(mirror.server);
  try { await fn({ root, bytes, received, mirror, base, entry }); }
  finally { await close(mirror.server); await close(upstreamServer); }
}
test('verified bytes and LOD headers delivered, no upstream fabrication', () => fixture(async ({ base, bytes, received }) => {
  const response = await fetch(base + '/api/allen_mesh_lod/382?lod=1');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('x-lod'), '0');
  assert.equal(response.headers.get('x-lod-fallback'), '1');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
  assert.equal(received.length, 0);
}));
test('same-size tampering fails closed', () => fixture(async ({ root, base }) => {
  await fs.writeFile(path.join(root, 'a.obj'), 'v 3 2 1\n');
  assert.equal((await fetch(base + '/api/allen_mesh_lod/382?lod=1')).status, 503);
}));
test('dynamic POST body/status/response passed unchanged', () => fixture(async ({ base, received }) => {
  const body = Buffer.from([0, 255, 128, 1]);
  const response = await fetch(base + '/api/export_pinpoint', { method: 'POST', body });
  assert.equal(response.status, 207);
  assert.equal(await response.text(), 'actual-upstream-response');
  assert.equal(received[0].body, body.toString('hex'));
}));
test('unknown query is not substituted with a verified different LOD', () => fixture(async ({ base, received }) => {
  assert.equal((await fetch(base + '/api/allen_mesh_lod/382?lod=2')).status, 207);
  assert.equal(received[0].url, '/api/allen_mesh_lod/382?lod=2');
}));
test('profile mutation invalidates future asset delivery', () => fixture(async ({ base }) => {
  assert.equal((await fetch(base + '/api/mri/activate', { method: 'POST', body: '{}' })).status, 207);
  assert.equal((await fetch(base + '/api/allen_mesh_lod/382?lod=1')).status, 503);
}));
test('POST to an asset is forwarded, never answered by cached GET', () => fixture(async ({ base, received }) => {
  assert.equal((await fetch(base + '/api/allen_mesh_lod/382?lod=1', { method: 'POST' })).status, 207);
  assert.equal(received[0].method, 'POST');
}));
test('range requests are real upstream pass-through', () => fixture(async ({ base, received }) => {
  assert.equal((await fetch(base + '/api/allen_mesh_lod/382?lod=1', { headers: { Range: 'bytes=0-3' } })).status, 207);
  assert.equal(received.length, 1);
}));
test('path traversal cannot select arbitrary local file', () => {
  assert.throws(() => bodyPath({ kind: 'mesh', body: '../secret.txt' }, { candidates: 'C:/test' }));
});
test('wrong profile identity is rejected', () => {
  assert.throws(() => assertIdentity({ optimizerSha256: 'a', profileContractSha256: 'wrong',
    manifestEnforced: true, pathConsistencyPassed: true }, { optimizerSha256: 'a', profileContractSha256: 'b' }));
});
test('backend readiness data is never cached', () => fixture(async ({ base, received }) => {
  await fetch(base + '/api/status');
  await fetch(base + '/api/status');
  assert.equal(received.length, 2);
}));
