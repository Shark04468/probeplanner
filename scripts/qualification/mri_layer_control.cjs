const STORAGE_KEY = 'probeplanner.mriAppearanceLayers';
const KEYS = { outer: ['outerBoundary', 'outer_tissue'], skull: ['skull', 'skull'] };

function readState(serialized, buttonKey) {
  if (!KEYS[buttonKey]) throw Error('Unsupported MRI layer control');
  if (typeof serialized !== 'string') throw Error('MRI layer state absent');
  const value = JSON.parse(serialized)?.[KEYS[buttonKey][0]];
  if (typeof value !== 'boolean') throw Error('MRI layer state must be explicit boolean');
  return value;
}

async function setLayerVisible(page, buttonKey, visible) {
  if (typeof visible !== 'boolean') throw Error('Explicit visibility required');
  const [storageField, runtimeKey] = KEYS[buttonKey] || [];
  if (!storageField) throw Error('Unsupported MRI layer control');
  const before = readState(await page.evaluate(key => localStorage.getItem(key), STORAGE_KEY), buttonKey);
  const button = page.getByTestId('brain3d-mri-layer-' + buttonKey);
  if (before !== visible) {
    if (await button.isDisabled()) throw Error('Required layer control is disabled');
    await button.click();
  }
  await page.waitForFunction(({ storageKey, storageField, runtimeKey, visible }) => {
    const stored = JSON.parse(localStorage.getItem(storageKey) || 'null');
    const viewport = document.querySelector('[data-testid="brain3d-viewport"]');
    const surfaces = JSON.parse(viewport?.dataset.surfaceStates || '{}');
    return stored?.[storageField] === visible && surfaces[runtimeKey]?.visible === visible;
  }, { storageKey: STORAGE_KEY, storageField, runtimeKey, visible }, { timeout: 30000 });
  const after = readState(await page.evaluate(key => localStorage.getItem(key), STORAGE_KEY), buttonKey);
  if (after !== visible) throw Error('Layer state changed before acknowledgement');
  return { layer: runtimeKey, before, after, clicked: before !== visible, runtimeAcknowledged: true };
}

module.exports = { readState, setLayerVisible };
