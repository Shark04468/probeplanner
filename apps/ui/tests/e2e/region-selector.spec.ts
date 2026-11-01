import { test, expect } from '@playwright/test';

test.describe('Region selector interaction regression', () => {
  test('supports multi-section expand and outer-list wheel scrolling', async ({ page }) => {
    await page.goto('/');

    const list = page.getByTestId('region-selector-list');
    await expect(list).toBeVisible();
    const headers = page.locator('[data-testid^="region-section-"]');
    await expect(headers.first()).toBeVisible();
    const headerCount = await headers.count();
    expect(headerCount).toBeGreaterThanOrEqual(1);

    await headers.nth(0).click();
    if (headerCount > 1) {
      await headers.nth(1).click();
    }

    const before = await list.evaluate((el) => el.scrollTop);
    await list.hover();
    await page.mouse.wheel(0, 900);
    const after = await list.evaluate((el) => el.scrollTop);

    expect(after).toBeGreaterThanOrEqual(before);
  });

  test('expanded section body can scroll independently when items overflow', async ({ page }) => {
    await page.goto('/');

    const list = page.getByTestId('region-selector-list');
    await expect(list).toBeVisible();

    const headers = page.locator('[data-testid^="region-section-"]');
    await expect(headers.first()).toBeVisible();
    await headers.nth(0).click();
    if ((await headers.count()) > 1) {
      await headers.nth(1).click();
    }

    const dims = await list.evaluate((el) => ({
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
      scrollTop: el.scrollTop,
    }));
    expect(dims.scrollHeight).toBeGreaterThanOrEqual(dims.clientHeight);

    await list.hover();
    await page.mouse.wheel(0, 800);

    const after = await list.evaluate((el) => el.scrollTop);
    expect(after).toBeGreaterThanOrEqual(dims.scrollTop);
  });
});
