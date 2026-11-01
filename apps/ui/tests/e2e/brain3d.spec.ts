import { test, expect } from '@playwright/test';

test.describe('Brain3D interaction regression', () => {
  test('camera quick-view buttons and fullscreen control are interactive', async ({ page }) => {
    await page.goto('/');

    const toggle3d = page.getByTestId('probeplanner-toggle-3d');
    await expect(toggle3d).toBeVisible();
    await toggle3d.click();
    await page.getByTestId('brain3d-display-controls').locator('summary').click();

    await expect(page.getByTestId('brain3d-view-iso')).toBeVisible();
    await expect(page.getByTestId('brain3d-view-top')).toBeVisible();
    await expect(page.getByTestId('brain3d-view-side')).toBeVisible();
    await expect(page.getByTestId('brain3d-view-front')).toBeVisible();

    await page.getByTestId('brain3d-view-top').click();
    await page.getByTestId('brain3d-view-side').click();
    await page.getByTestId('brain3d-view-front').click();
    await page.getByTestId('brain3d-view-iso').click();

    const fullscreenBtn = page.getByTestId('brain3d-fullscreen');
    await expect(fullscreenBtn).toBeVisible();
    await fullscreenBtn.click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
  });

  test('language switch updates 3D overlay labels end-to-end', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('probeplanner-toggle-3d').click();
    await page.getByTestId('brain3d-display-controls').locator('summary').click();
    await expect(page.getByTestId('brain3d-opacity-slider')).toBeVisible();

    await page.getByRole('button', { name: 'EN', exact: true }).click();
    await expect(page.getByText('Opacity').first()).toBeVisible();
    await expect(page.getByTestId('brain3d-hemi-all')).toHaveText('All');
    await expect(page.getByTestId('brain3d-selected-only')).toHaveText(/Selected/i);
  });

  test('hemisphere/filter and render toggles are clickable without desync regressions', async ({ page }) => {
    await page.goto('/');

    await page.getByTestId('probeplanner-toggle-3d').click();
    await page.getByTestId('brain3d-display-controls').locator('summary').click();
    await expect(page.getByTestId('brain3d-opacity-slider')).toBeVisible();

    await page.getByTestId('brain3d-selected-only').click();
    await page.getByTestId('brain3d-selected-only').click();

    await page.getByTestId('brain3d-hemi-left').click();
    await page.getByTestId('brain3d-hemi-right').click();
    await page.getByTestId('brain3d-hemi-all').click();
    await page.getByTestId('brain3d-hemi-lrfix').click();

    await page.getByTestId('brain3d-shell').click();
    await page.getByTestId('brain3d-axes').click();
    await page.getByTestId('brain3d-probe-guide').click();
    await page.getByTestId('brain3d-uncertainty').click();
    await page.getByTestId('brain3d-weighted-oit').click();
  });

  test('bridge snapshot state can be acknowledged from Unity channel messages', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('probeplanner-toggle-3d').click();
    await page.getByTestId('brain3d-display-controls').locator('summary').click();
    await expect(page.getByTestId('brain3d-opacity-slider')).toBeVisible();
    await page.locator('iframe[title="Brain Planner Unity"]').waitFor({ state: 'visible' });

    await page.evaluate(() => {
      const frame = document.querySelector('iframe[title="Brain Planner Unity"]');
      if (!frame || !(frame as HTMLIFrameElement).contentWindow) return;
      const source = (frame as HTMLIFrameElement).contentWindow!;
      const emit = (eventName: string, payload: unknown) => {
        window.dispatchEvent(
          new MessageEvent('message', {
            source,
            data: {
              source: 'BrainPlannerUnity',
              event: eventName,
              data: typeof payload === 'string' ? payload : JSON.stringify(payload ?? {}),
            },
          })
        );
      };
      emit('instanceReady', {});
      emit('ready', { regions: 1328, catalog: 'all' });
      emit('snapshotApplied', {});
    });

    await expect(page.getByTestId('brain3d-sync-state')).toBeVisible();
  });

  test('contact panel controls and depth table remain interactive', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('probeplanner-toggle-3d').click();
    await page.getByTestId('brain3d-slice-controls').locator('summary').click();

    await expect(page.getByTestId('brain3d-contact-depth-table')).toBeVisible();
    await page.getByTestId('brain3d-contact-mode-standard').click();
    await page.getByTestId('brain3d-contact-mode-slice_contact_persist').click();
    await page.getByTestId('brain3d-contact-style-outline').click();
    await page.getByTestId('brain3d-contact-style-hybrid').click();

    const slider = page.getByTestId('brain3d-contact-min-length');
    await expect(slider).toBeVisible();
    await slider.fill('260');
    await expect(slider).toHaveValue('260');
  });

  test('clinical export bundle action is reachable from slice panel', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('probeplanner-toggle-3d').click();
    await page.getByTestId('brain3d-slice-controls').locator('summary').click();

    const exportBtn = page.getByTestId('brain3d-export-bundle');
    await expect(exportBtn).toBeVisible();
    await exportBtn.click();
    await expect(exportBtn).toBeVisible();
  });
});
