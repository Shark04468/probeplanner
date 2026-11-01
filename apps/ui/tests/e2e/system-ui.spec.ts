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
    const backendBase = process.env.REACT_APP_FLASK_BASE || 'http://127.0.0.1:5000';
    const statusResp = await page.request.get(new URL('/api/status?includeRegions=0', backendBase).href);
    expect(statusResp.ok(), 'Configured backend must be reachable for the live readiness check.').toBeTruthy();

    const status = await statusResp.json();
    expect(status?.atlasLoaded, 'Atlas must be loaded in the configured backend.').toBe(true);

    await page.goto('/');

    const statusBadge = page.getByTestId('backend-status-badge');
    await expect(statusBadge).toBeVisible();
    // Wait for the configured auto-connect; do not race its connect/disconnect toggle.
    await expect(statusBadge).toContainText(/在线|online/i, { timeout: 20000 });

    const atlasStage = page.getByTestId('global-stage-atlas');
    await expect(atlasStage).toBeVisible();
    await expect(atlasStage).toContainText('100%', { timeout: 25000 });
  });
});
