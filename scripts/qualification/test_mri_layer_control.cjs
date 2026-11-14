const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readState, setLayerVisible } = require('./mri_layer_control.cjs');
test('false differs from absent or malformed state', () => {
  assert.equal(readState('{"outerBoundary":false}', 'outer'), false);
  for (const raw of [null, '{}', '{"outerBoundary":"false"}', 'not json'])
    assert.throws(() => readState(raw, 'outer'));
});
test('MRI layer names are explicitly mapped', () => {
  assert.equal(readState('{"outerBoundary":true,"skull":false}', 'outer'), true);
  assert.equal(readState('{"outerBoundary":true,"skull":false}', 'skull'), false);
  assert.throws(() => readState('{}', 'invented'));
});
test('missing aria-pressed cannot prevent a required click', async () => {
  let state = true, clicks = 0, acknowledged = false;
  const page = { evaluate: async () => JSON.stringify({ outerBoundary: state }),
    getByTestId: id => {
      assert.equal(id, 'brain3d-mri-layer-outer');
      return { isDisabled: async () => false, click: async () => { clicks++; state = false; } };
    },
    waitForFunction: async (_fn, args) => { assert.equal(args.runtimeKey, 'outer_tissue'); acknowledged = true; } };
  const outcome = await setLayerVisible(page, 'outer', false);
  assert.equal(clicks, 1); assert.equal(acknowledged, true); assert.equal(outcome.after, false);
});
test('unchanged state still requires runtime acknowledgement', async () => {
  const page = { evaluate: async () => '{"skull":false}',
    getByTestId: () => ({ click: async () => assert.fail('unnecessary toggle') }),
    waitForFunction: async () => { throw Error('No runtime acknowledgement'); } };
  await assert.rejects(setLayerVisible(page, 'skull', false), /No runtime acknowledgement/);
});
test('disabled required control cannot silently pass', async () => {
  const page = { evaluate: async () => '{"skull":true}',
    getByTestId: () => ({ isDisabled: async () => true }) };
  await assert.rejects(setLayerVisible(page, 'skull', false), /disabled/);
});
