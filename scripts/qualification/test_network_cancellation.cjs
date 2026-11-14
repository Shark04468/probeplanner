const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { createMirror } = require('./asset_mirror.cjs');
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1',
  () => resolve('http://127.0.0.1:' + server.address().port)));
const close = server => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
async function waitFor(predicate) {
  const deadline = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error('Fixture observation deadline');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
async function fixture(t, handler) {
  const upstream = http.createServer(handler), events = [];
  const baseUpstream = await listen(upstream);
  const mirror = createMirror({ upstream: baseUpstream, entries: [], roots: {}, expected: {},
    event: e => events.push(e) });
  const base = await listen(mirror.server);
  t.after(async () => { await close(mirror.server); await close(upstream); });
  return { base, events };
}
test('normal response has no cancellation or upstream error', async t => {
  const { base, events } = await fixture(t, (_, res) => res.end('ok'));
  assert.equal(await (await fetch(base + '/status')).text(), 'ok');
  assert.deepEqual(events.map(e => e.kind), ['pass_through']);
  assert.equal(events[0].forwardId, 1);
});
test('genuine upstream reset remains an error and returns502', async t => {
  const { base, events } = await fixture(t, req => req.socket.destroy());
  assert.equal((await fetch(base + '/status')).status, 502);
  const e = events.find(e => e.kind === 'upstream_error');
  assert.equal(e.cause, 'upstream');
  assert.equal(e.code, 'ECONNRESET');
});
test('client cancellation is recorded before its induced upstream error', async t => {
  let received = false;
  const { base, events } = await fixture(t, () => { received = true; });
  const req = http.get(base + '/api/mri/overlay/status');
  req.on('error', () => {});
  await waitFor(() => received);
  req.destroy();
  await waitFor(() => events.some(e => e.kind === 'upstream_error'));
  const cancelled = events.findIndex(e => e.kind === 'downstream_cancel');
  const failed = events.findIndex(e => e.kind === 'upstream_error');
  assert(cancelled >= 0 && cancelled < failed);
  assert.equal(events[cancelled].upstreamErrorObserved, false);
  assert.equal(events[failed].cause, 'downstream_response_closed');
  assert.equal(events[cancelled].forwardId, events[failed].forwardId);
});
test('truncated upstream body is not relabelled as a client cancellation', async t => {
  const { base, events } = await fixture(t, (_, res) => {
    res.writeHead(200, { 'content-length': 100 }); res.write('partial');
    setTimeout(() => res.destroy(), 50);
  });
  const response = await fetch(base + '/status');
  await assert.rejects(() => response.text());
  await waitFor(() => events.some(e => e.kind === 'upstream_response_error'));
  assert.equal(events.find(e => e.kind === 'upstream_response_error').cause, 'upstream_response');
  assert(events.filter(e => e.kind === 'downstream_cancel').every(e => e.upstreamErrorObserved));
});
test('client abort during upload is not accepted as a successful POST', async t => {
  let received = false;
  const { base, events } = await fixture(t, () => { received = true; });
  const req = http.request(base + '/api/optimize', { method: 'POST', headers: { 'content-length': 1000 } });
  req.on('error', () => {}); req.write('partial');
  await waitFor(() => received); req.destroy();
  await waitFor(() => events.some(e => e.kind === 'upstream_error'));
  assert(events.some(e => e.kind === 'downstream_cancel' && e.reason === 'request_aborted'));
  assert(!events.some(e => e.kind === 'pass_through'));
  assert(events.every(e => e.method === 'POST'));
});
