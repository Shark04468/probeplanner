const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { same, literal, validateExports } = require('./ui_contract.cjs');

test('canonical comparisons preserve arrays but ignore object key order', () => {
  assert.ok(same({ a: 1, b: 2 }, { b: 2, a: 1 }));
  assert.ok(!same(['CA1', 'CA3'], ['CA3', 'CA1']));
});
test('nonliteral source evaluation fails closed', () => {
  assert.throws(() => literal({ type: 'CallExpression' }), /Unsupported/);
  assert.throws(() => literal({ type: 'ObjectExpression', properties: [{ type: 'SpreadElement' }] }), /Nonliteral/);
});
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'probeplanner-ui-contract-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(dir)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(dir).startsWith('probeplanner-ui-contract-'));
    fs.rmSync(dir, { recursive: true });
  });
  const runtime = { backend: 'paper_python', profile: 'fixture-only' };
  const result = { ok: true, uncovered: [], selected: [{}], requestId: 'fixture',
    requestHash: 'req', responseHash: 'res', runtimeIdentity: runtime };
  const data = { _sourcePlan: { status: 'linked_to_saved_planner_result', requestId: 'fixture',
    requestHash: 'req', responseHash: 'res', runtimeIdentity: runtime },
    probes: [{ id: 'Probe0', ap: 1, ml: -2, dv: 0, phi: 0, theta: 10, spin: 0, depth: 5, type: 'NP2.0-1sh', shankCount: 1, coveredRegions: ['CA1', 'CA3'] }] };
  const downloads = ['pplan', 'txt'].map(format => ({ format, path: path.join(dir, 'plan.' + format),
    submitted: { planRequestId: 'fixture', planResponseHash: 'res' },
    received: { ok: true, pinpointData: structuredClone(data) } }));
  fs.writeFileSync(downloads[0].path, JSON.stringify(data));
  fs.writeFileSync(downloads[1].path, '# coordinate_system: pinpoint_ap_posterior_positive_mm\n' +
    '# probe_metadata: {"id":"Probe0","probeType":"NP2.0-1sh","shankCount":1,"coveredRegions":["CA1","CA3"]}\n' +
    'Probe0\t1\t-2\t0\t0\t10\t0\t5');
  return { result, runtime, replay: { ok: true, record: { responseHash: 'res' } }, downloads };
}
const verify = f => validateExports(f.result, f.runtime, f.replay, f.downloads);
test('matching actual-file structure passes export checker', t => assert.equal(verify(fixture(t)).passed, true));
test('partial first outcome cannot pass', t => {
  const f = fixture(t); f.result.uncovered.push('CA3'); assert.throws(() => verify(f), /not full/);
});
test('runtime provenance mismatch cannot pass', t => {
  const f = fixture(t); f.runtime = { different: true }; assert.throws(() => verify(f), /Runtime/);
});
test('unlinked export cannot pass', t => {
  const f = fixture(t); f.downloads[0].received.pinpointData._sourcePlan.status = 'unverified_geometry';
  assert.throws(() => verify(f), /not linked/);
});
test('changed JSON download cannot pass', t => {
  const f = fixture(t); fs.writeFileSync(f.downloads[0].path, '{}'); assert.throws(() => verify(f), /Downloaded JSON/);
});
test('changed text numeric trajectory cannot pass', t => {
  const f = fixture(t); fs.appendFileSync(f.downloads[1].path, '\nProbe1 1 0 0 0 0 0 5');
  assert.throws(() => verify(f), /count differs/);
});
test('changed text hardware cannot pass', t => {
  const f = fixture(t); const p = f.downloads[1].path;
  fs.writeFileSync(p, fs.readFileSync(p, 'utf8').replace('"shankCount":1', '"shankCount":2'));
  assert.throws(() => verify(f), /metadata differs/);
});
test('missing download cannot pass', t => {
  const f = fixture(t); f.downloads.pop(); assert.throws(() => verify(f), /Both/);
});
