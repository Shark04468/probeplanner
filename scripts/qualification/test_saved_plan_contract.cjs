const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { sha } = require('./ui_contract.cjs');
const { validateSavedInput, validateImportedExport } = require('./saved_plan_contract.cjs');
const bytes = fs.readFileSync(path.join(__dirname, 'saved_plan_input/actual-plan.pplan'));
const origin = JSON.parse(fs.readFileSync(path.join(__dirname, 'saved_plan_input/ORIGIN.json')));
const original = JSON.parse(bytes);
const runtime = original._exportRuntime;
test('original actual export retains exact source binding', () => {
  assert.equal(validateSavedInput(bytes, origin, runtime)._sourcePlan.requestId, origin.source_plan.requestId);
});
test('changed input bytes are rejected', () => {
  assert.throws(() => validateSavedInput(Buffer.concat([bytes, Buffer.from(' ')]), origin, runtime), /bytes differ/);
});
test('different runtime is rejected', () => {
  assert.throws(() => validateSavedInput(bytes, origin, { ...runtime, optimizerSha256: '0'.repeat(64) }), /Runtime differs/);
});
test('scope promotion is rejected', () => {
  assert.throws(() => validateSavedInput(bytes, { ...origin, anatomy_accepted: true }, runtime), /scope/);
  assert.throws(() => validateSavedInput(bytes, { ...origin, allowed_optimization_requests: 1 }, runtime), /scope/);
});
test('wrong original identity is rejected even if file hash is updated', () => {
  const plan = structuredClone(original); plan._sourcePlan.requestId = 'different';
  const altered = Buffer.from(JSON.stringify(plan));
  assert.throws(() => validateSavedInput(altered, { ...origin, sha256: sha(altered) }, runtime), /identity/);
});
// These are contract mutation fixtures, not real UI exports or new experiments.
function fixture() { return { ...structuredClone(original), _sourcePlan: { status: 'unverified_geometry' } }; }
test('round-trip comparison preserves unverified status', () => {
  const exported = fixture();
  assert.equal(validateImportedExport(original, { ok: true, pinpointData: exported }, exported).revalidated, false);
});
test('changed coordinate and restored certification are rejected', () => {
  const exported = fixture(); exported.probes[0].ap *= -1;
  assert.throws(() => validateImportedExport(original, { ok: true, pinpointData: exported }, exported), /geometry/);
  assert.throws(() => validateImportedExport(original, { ok: true, pinpointData: original }, original), /recertified/);
});
