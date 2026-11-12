const { test } = require('node:test');
const assert = require('node:assert/strict');
const { inspect } = require('./contract.cjs');
const fixture = () => ({ kind: 'synthetic_webgl_capability_only', mode: 'default_headless_shell',
  webgl: { available: true, linked: true, contextLost: false, drawError: 0,
    frames: 24, centerPixel: [51, 153, 102, 255] } });
test('basic pixel success cannot become application or anatomy acceptance', () => {
  assert.deepEqual(inspect(fixture()), { basicRendering: true, pixelCorrect: true,
    actualApplicationTested: false, fullMacWorkflowAccepted: false, anatomyAccepted: false });
});
test('wrong pixel cannot pass', () => {
  const r = fixture(); r.webgl.centerPixel = [255, 0, 255, 255];
  assert.equal(inspect(r).basicRendering, false);
});
test('lost context cannot pass', () => {
  const r = fixture(); r.webgl.contextLost = true; assert.equal(inspect(r).basicRendering, false);
});
test('unavailable WebGL cannot pass', () => {
  const r = fixture(); r.webgl = { available: false }; assert.equal(inspect(r).basicRendering, false);
});
test('failed shader or rendering API cannot pass', () => {
  for (const [key, value] of [['linked', false], ['drawError', 1280], ['frames', 0]]) {
    const r = fixture(); r.webgl[key] = value; assert.equal(inspect(r).basicRendering, false);
  }
});
test('mode error overrides good pixels', () => {
  const r = fixture(); r.error = 'timeout'; assert.equal(inspect(r).basicRendering, false);
});
