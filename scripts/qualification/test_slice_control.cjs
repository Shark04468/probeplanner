const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const { createRequire } = require('node:module');
const { setSliceFraction } = require('./slice_control.cjs');
const dependencySource = process.env.PROBEPLANNER_TEST_DEPENDENCY_SOURCE;
if (!dependencySource) throw Error('Explicit installed test dependency source required');
const { chromium } = createRequire(path.join(dependencySource, 'apps/ui/package.json'))('playwright');

test('slice actions use a real receiver and actual acknowledgement', async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  async function fixture({ delay = 0, noChange = false, noAck = false, blocked = false, snapped = false } = {}) {
    const page = await browser.newPage();
    // Deliberately synthetic unit fixture, never a software or publication screenshot.
    await page.setContent(`<div data-testid="brain3d-viewport"></div>
      <div role="slider" aria-label="Horizontal slice position" aria-valuenow="100"
      style="width:16px;height:360px;border:1px solid;pointer-events:none"></div>`);
    await page.evaluate(({ delay, noChange, noAck, blocked, snapped }) => {
      const slider = document.querySelector('[role="slider"]');
      const viewport = document.querySelector('[data-testid="brain3d-viewport"]');
      window.actualMouseEvents = [];
      slider.addEventListener('pointerdown', event => {
        window.actualMouseEvents.push({ trusted: event.isTrusted, button: event.button });
        if (noChange) return;
        const rect = slider.getBoundingClientRect();
        const normalized = snapped ? .67 : 1 - (event.clientY - rect.top) / rect.height;
        slider.setAttribute('aria-valuenow', String(Math.round(normalized * 100)));
        if (!noAck) viewport.dataset.sectionState = JSON.stringify({ status: 'loading', axis: 'y',
          requestedPlanes: 1, requestKey: JSON.stringify(['y', normalized]) });
      });
      if (!blocked) setTimeout(() => { slider.style.pointerEvents = 'auto'; }, delay);
    }, { delay, noChange, noAck, blocked, snapped });
    return page;
  }
  await t.test('waits for a delayed interactive receiver and clicks exactly once', async () => {
    const page = await fixture({ delay: 250 });
    try {
      const result = await setSliceFraction(page, .35, 5000);
      assert.equal(result.after, 65);
      assert.equal(result.before.pointerEvents, 'auto');
      assert.deepEqual(await page.evaluate(() => window.actualMouseEvents), [{ trusted: true, button: 0 }]);
    } finally { await page.close(); }
  });
  await t.test('retains an actual boundary-snapped value', async () => {
    const page = await fixture({ snapped: true });
    try { assert.equal((await setSliceFraction(page, .35, 5000)).after, 67); }
    finally { await page.close(); }
  });
  for (const [name, options] of [['disabled receiver', { blocked: true }],
      ['no-op input', { noChange: true }], ['missing renderer acknowledgement', { noAck: true }]]) {
    await t.test('fails closed for ' + name, async () => {
      const page = await fixture(options);
      try {
        await assert.rejects(() => setSliceFraction(page, .35, 1000));
        const events = await page.evaluate(() => window.actualMouseEvents);
        assert.equal(events.length, options.blocked ? 0 : 1);
        assert(events.every(e => e.trusted && e.button === 0));
      }
      finally { await page.close(); }
    });
  }
  await t.test('rejects invalid requests before any browser access', async () => {
    for (const v of [0, 1, NaN, -.1]) await assert.rejects(() => setSliceFraction(null, v));
    await assert.rejects(() => setSliceFraction(null, .35, 0));
  });
});
