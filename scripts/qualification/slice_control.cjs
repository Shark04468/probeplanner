const { performance } = require('node:perf_hooks');

async function setSliceFraction(page, fraction, timeout = 30000) {
  if (!Number.isFinite(fraction) || fraction <= 0 || fraction >= 1)
    throw Error('Slice fraction must be strictly inside the ruler');
  if (!Number.isFinite(timeout) || timeout <= 0) throw Error('Positive action budget required');
  const deadline = performance.now() + timeout;
  const remaining = () => {
    const ms = deadline - performance.now();
    if (ms <= 0) throw Error('Slice action budget exhausted');
    return ms;
  };
  const label = 'Horizontal slice position';
  const slider = page.getByRole('slider', { name: label, exact: true });
  // A raw page.mouse click can be lost while the bridge disables pointer events.
  await slider.click({ trial: true, timeout: remaining() });
  const before = await slider.evaluate(n => ({
    value: Number(n.getAttribute('aria-valuenow')),
    pointerEvents: getComputedStyle(n).pointerEvents,
    width: n.getBoundingClientRect().width, height: n.getBoundingClientRect().height,
    borderLeft: parseFloat(getComputedStyle(n).borderLeftWidth) || 0,
    borderTop: parseFloat(getComputedStyle(n).borderTopWidth) || 0,
  }));
  if (before.pointerEvents === 'none' || before.height < 10 || before.width < 2)
    throw Error('Slice receiver not ready');
  await slider.click({ position: { x: before.width / 2 - before.borderLeft,
    y: before.height * fraction - before.borderTop }, timeout: remaining() });
  // Boundary snapping is retained; verify actual acknowledged state, not a forced value.
  await page.waitForFunction(({ label, previous }) => {
    const node = [...document.querySelectorAll('[role="slider"]')]
      .find(n => n.getAttribute('aria-label') === label);
    const value = Number(node?.getAttribute('aria-valuenow'));
    const viewport = document.querySelector('[data-testid="brain3d-viewport"]');
    const section = JSON.parse(viewport?.dataset.sectionState || '{}');
    let key;
    try { key = JSON.parse(section.requestKey); } catch { return false; }
    return value > 0 && value < 100 && value !== previous &&
      key?.[0] === 'y' && Math.abs(key[1] * 100 - value) <= .51 &&
      section.axis === 'y' && section.requestedPlanes > 0 &&
      ['loading', 'loaded'].includes(section.status);
  }, { label, previous: before.value }, { timeout: remaining() });
  const after = await slider.getAttribute('aria-valuenow');
  const section = JSON.parse(await page.getByTestId('brain3d-viewport').getAttribute('data-section-state'));
  return { fractionFromTop: fraction, before, after: Number(after), section, actualInputAcknowledged: true };
}

module.exports = { setSliceFraction };
