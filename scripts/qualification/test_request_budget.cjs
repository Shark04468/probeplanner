const { test } = require('node:test');
const assert = require('node:assert/strict');
const { remainingBudget, withinRequestBudget } = require('./request_budget.cjs');

test('pre-request button waiting does not consume the actual-request budget', () => {
  assert.equal(remainingBudget(7000, 7000, 600000), 600000);
  assert.equal(remainingBudget(7000, 7007, 600000), 599993);
});
test('time after the actual request is never reset', () => {
  assert.equal(remainingBudget(7000, 606000, 600000), 1000);
  assert.equal(remainingBudget(7000, 607000, 600000), 0);
  assert.equal(remainingBudget(7000, 608000, 600000), 0);
});
test('invalid or backwards clocks fail closed', () => {
  for (const args of [[NaN, 2, 5], [2, 1, 5], [1, 2, 0]]) assert.throws(() => remainingBudget(...args), /Invalid/);
});
test('expired request cannot be accepted even if a promise is already resolved', async () => {
  await assert.rejects(withinRequestBudget(Promise.resolve('late'), 0, 10, () => 11), /exhausted/);
});
test('ready request resolves and clears its timer', async () => {
  assert.equal(await withinRequestBudget(Promise.resolve('observed'), 0, 10, () => 2), 'observed');
});
test('stalled request is rejected without resubmission', async () => {
  await assert.rejects(withinRequestBudget(new Promise(() => {}), 0, 5, () => 0), /exhausted/);
});
