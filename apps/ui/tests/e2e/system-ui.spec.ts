import { test, expect } from '@playwright/test';

test.describe('System UI regression', () => {
  test('global workflow stage bar renders 4 canonical stages', async ({ page }) => {
    await page.goto('/');

    const stageBar = page.getByTestId('global-stage-bar');
    await expect(stageBar).toBeVisible();

    await expect(page.getByTestId('global-stage-atlas')).toBeVisible();
    await expect(page.getByTestId('global-stage-registration')).toBeVisible();
    await expect(page.getByTestId('global-stage-risk')).toBeVisible();
    await expect(page.getByTestId('global-stage-planner')).toBeVisible();
  });

  test('event center can be expanded and diagnostics action stays reachable', async ({ page }) => {
    await page.goto('/');

    const toggle = page.getByTestId('event-center-toggle');
    await expect(toggle).toBeVisible();
    await toggle.click();

    const panel = page.getByTestId('event-center-panel');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('event-center-copy')).toBeVisible();
  });

  test('backend + atlas readiness is reflected by status badge and atlas stage', async ({ page }) => {
    let statusResp;
    try {
      statusResp = await page.request.get('http://127.0.0.1:5000/api/status?includeRegions=0');
    } catch {
      test.skip(true, 'Backend is not reachable for live alignment check.');
      return;
    }

    test.skip(!statusResp.ok(), 'Backend is not reachable for live alignment check.');

    const status = await statusResp.json();
    test.skip(!status?.atlasLoaded, 'Atlas not loaded yet in backend.');

    await page.goto('/');

    const statusBadge = page.getByTestId('backend-status-badge');
    await expect(statusBadge).toBeVisible();
    if (!(await statusBadge.textContent())?.match(/在线|online/i)) {
      const connectBtn = page.getByRole('button', { name: /连接|connect/i }).first();
      if (await connectBtn.isVisible().catch(() => false)) {
        await connectBtn.click();
      }
    }

    await expect(statusBadge).toContainText(/在线|online/i, { timeout: 20000 });

    const atlasStage = page.getByTestId('global-stage-atlas');
    await expect(atlasStage).toBeVisible();
    await expect(atlasStage).toContainText('100%', { timeout: 25000 });
  });
});
