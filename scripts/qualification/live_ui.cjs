// Read-only observation plus real UI interactions; never inject plans or bridge events.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { same, sha, requireThat, validateExports } = require('./ui_contract.cjs');
const { withinRequestBudget } = require('./request_budget.cjs');
const [sourceArg, outArg, base] = process.argv.slice(2);
const source = path.resolve(sourceArg), out = path.resolve(outArg);
const { chromium } = createRequire(path.join(source, 'apps/ui/package.json'))('playwright');
const contract = JSON.parse(fs.readFileSync(path.join(out, 'REQUEST_CONTRACT.json')));
const write = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const report = { status: 'FAIL', steps: [], requests: [], responses: [], downloads: [],
  networkFailures: [], httpErrors: [], pageErrors: [], browserConsole: [], anatomyAccepted: false,
  fullMacWorkflowAccepted: false, visualReviewRequired: true, externalResubmission: false };
let browser, context, page, traceActive = false;
async function step(name, action) {
  console.log('START ' + name);
  const row = { name, startedUtc: new Date().toISOString() };
  report.steps.push(row);
  try { row.value = await action(); row.passed = true; }
  catch (e) { row.passed = false; row.error = String(e); throw e; }
  finally { row.endedUtc = new Date().toISOString(); write('stage-' + name + '.json', row); }
  console.log('DONE ' + name);
}
async function capture(name, canvas = false) {
  await page.screenshot({ path: path.join(out, name + '.png'), timeout: 60000 });
  fs.writeFileSync(path.join(out, name + '.aria.txt'), await page.locator('body').ariaSnapshot(), { flag: 'wx' });
  if (canvas) await page.frameLocator('iframe[title="Brain Planner Unity"]').locator('canvas')
    .screenshot({ path: path.join(out, name + '-canvas.png'), timeout: 60000 });
}
async function disclosure(id, open) {
  const node = page.getByTestId(id);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  if (await node.evaluate(n => n.open) !== open) await node.locator('summary').click();
  await page.waitForFunction(({ id, open }) => document.querySelector(`[data-testid="${id}"]`)?.open === open,
    { id, open }, { timeout: 10000 });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  requireThat(await node.evaluate(n => n.open) === open, 'Disclosure changed during layout: ' + id);
}
async function fullscreen() {
  await page.getByRole('button', { name: 'Fullscreen', exact: true }).click();
  await page.waitForFunction(() => {
    const v = document.querySelector('[data-testid="brain3d-viewport"]');
    const r = v?.getBoundingClientRect();
    return document.fullscreenElement === v && r && Math.abs(r.width - innerWidth) < 2 &&
      Math.abs(r.height - innerHeight) < 2 && v.dataset.compact === String(innerWidth < 900 || innerHeight < 760);
  }, null, { timeout: 30000 });
  await page.waitForTimeout(250);
}
async function sceneReady(timeout) {
  await page.waitForFunction(() => {
    const s = window.macUiObserved;
    const v = document.querySelector('[data-testid="brain3d-viewport"]');
    const surfaces = JSON.parse(v?.dataset.surfaceStates || '{}');
    const section = JSON.parse(v?.dataset.sectionState || '{}');
    const p = s?.progress;
    return p?.total > 0 && p.loaded === p.total && !p.failed &&
      ['CA1', 'CA3'].every(k => s.fills[k] === 'fillLoaded') &&
      surfaces.brain_surface?.status === 'loaded' && surfaces.brain_surface.visible &&
      Number.isInteger(surfaces.brain_surface.resolvedLod) && surfaces.brain_surface.resolvedLod >= 0 &&
      section.status === 'loaded' && section.visible && section.mode === 'mri_ccf_overlay' &&
      [1, 2].includes(section.requestedPlanes) && section.loadedPlanes === section.requestedPlanes &&
      /^[a-f0-9]{64}$/.test(section.manifestSha256 || '') &&
      document.querySelector('[data-testid="brain3d-runtime-status"]')?.dataset.state === 'ready';
  }, null, { timeout });
  return page.getByTestId('brain3d-viewport').evaluate(n => ({ ...n.dataset }));
}
async function main() {
  try {
    browser = await chromium.launch({ headless: true });
    report.browserVersion = browser.version();
    report.playwrightVersion = createRequire(path.join(source, 'apps/ui/package.json'))('playwright/package.json').version;
    report.browserMode = 'headless_no_forced_renderer';
    context = await browser.newContext({ viewport: { width: 1400, height: 1000 }, acceptDownloads: true });
    await context.tracing.start({ screenshots: true, snapshots: true }); traceActive = true;
    page = await context.newPage(); page.setDefaultTimeout(60000);
    page.on('pageerror', e => report.pageErrors.push(String(e)));
    page.on('console', m => { if (['error', 'warning'].includes(m.type())) report.browserConsole.push({ type: m.type(), text: m.text() }); });
    page.on('requestfailed', r => report.networkFailures.push({ url: r.url(), method: r.method(), error: r.failure() }));
    page.on('response', r => { if (r.status() >= 400) report.httpErrors.push({ url: r.url(), status: r.status() }); });
    await page.route('**/api/optimize', async route => {
      const body = route.request().postDataJSON();
      const { taskId, ...settings } = body;
      const matches = Boolean(taskId) && same(settings, contract.expectedRequest);
      report.requests.push({ body, matches, observedUtc: new Date().toISOString() });
      write('planning-request-' + report.requests.length + '.json', report.requests.at(-1));
      if (!matches || report.requests.length !== 1) await route.abort('blockedbyclient');
      else await route.continue();
    });
    await step('configure', async () => {
      await page.goto(base, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'EN', exact: true }).click();
      await capture('initial');
      for (const key of ['CA1', 'CA3']) {
        await page.getByRole('textbox', { name: 'Search region name or acronym...' }).fill(key);
        const button = page.locator('button[data-region-key="' + key + '"]');
        requireThat(await button.getAttribute('aria-pressed') !== 'true', 'Unexpected preselected target');
        await button.click();
      }
      await page.getByRole('textbox', { name: 'Search region name or acronym...' }).fill('');
      await page.locator('select:has(option[value="single_hemisphere"])').selectOption('single_hemisphere');
      await page.locator('select:has(option[value="tip_in_target"])').selectOption('tip_in_target');
      await page.locator('select:has(option[value="NP2.0-1sh"])').selectOption('NP2.0-1sh');
      await page.getByRole('button', { name: 'Reference stability: On', exact: true }).click();
      await page.getByRole('button', { name: 'Reference stability: Off', exact: true }).waitFor();
      await capture('configuration');
      return { literalSettingsSealed: true, selectedTargets: ['CA1', 'CA3'] };
    });
    await context.tracing.stop({ path: path.join(out, 'configuration-trace.zip') }); traceActive = false;
    await context.tracing.start({ screenshots: false, snapshots: false }); traceActive = true;
    await step('first_plan', async () => {
      const matchesOptimization = r => r.url().endsWith('/api/optimize') && r.method() === 'POST';
      const sentPromise = page.waitForRequest(matchesOptimization, { timeout: 60000 })
        .then(request => ({ request, at: performance.now(), observedUtc: new Date().toISOString() }))
        .catch(error => ({ error: String(error) }));
      const responsePromise = page.waitForResponse(r => matchesOptimization(r.request()), { timeout: 0 })
        .then(response => ({ response, at: performance.now(), observedUtc: new Date().toISOString() }))
        .catch(error => ({ error: String(error) }));
      await page.getByRole('button', { name: 'Run Optimization', exact: true }).click();
      const sent = await sentPromise;
      requireThat(!sent.error, sent.error || 'Actual request missing');
      const received = await withinRequestBudget(responsePromise, sent.at, 600000);
      requireThat(!received.error, received.error || 'Missing response');
      requireThat(received.at - sent.at <= 600000, 'Late response exceeds actual request budget');
      report.planningRequestClock = { requestObservedUtc: sent.observedUtc, responseObservedUtc: received.observedUtc,
        elapsedMs: received.at - sent.at, budgetMs: 600000, excludesPreRequestControlWait: true };
      const response = received.response;
      const body = await response.json();
      report.responses.push({ status: response.status(), body });
      write('optimization-response.json', report.responses[0]);
      requireThat(report.requests.length === 1 && report.requests[0].matches && response.ok(), 'First request failed');
      const end = Date.now() + 600000;
      let task;
      do {
        const r = await page.request.get(base + '/api/task/status/' + report.requests[0].body.taskId);
        requireThat(r.ok(), 'Task status request failed'); task = await r.json();
        if (['completed', 'failed', 'cancelled', 'error'].includes(task.task?.status)) break;
        await page.waitForTimeout(2000);
      } while (Date.now() < end);
      write('completed-task.json', task);
      requireThat(task.task?.status === 'completed', 'First task did not complete');
      report.result = task.task.result;
      requireThat(report.result.ok && Array.isArray(report.result.uncovered) && report.result.uncovered.length === 0, 'First result not full');
      report.runtime = await (await page.request.get(base + '/api/paper/runtime')).json();
      report.replay = await (await page.request.get(base + '/api/replay/' + report.result.requestId)).json();
      write('runtime.json', report.runtime); write('replay.json', report.replay);
      return { requestId: report.result.requestId, responseHash: report.result.responseHash };
    });
    await context.tracing.stop({ path: path.join(out, 'planning-action-trace.zip') }); traceActive = false;
    await context.tracing.start({ screenshots: true, snapshots: true }); traceActive = true;
    await step('inspect_and_export', async () => {
      await page.getByRole('button', { name: /^R\s*Results/ }).click();
      await page.getByText('Feasible plan', { exact: true }).waitFor();
      await capture('results');
      const center = page.locator('.pp-center');
      await center.getByText('Unified trajectory audit', { exact: true }).scrollIntoViewIfNeeded();
      await capture('audit');
      const header = center.getByRole('button', { name: /^Probe 1 AP/ });
      if (await header.getAttribute('aria-expanded') !== 'true') await header.click();
      await capture('probe-inspection');
      const details = center.locator('summary').filter({ hasText: 'Advanced diagnostics and reproducibility' });
      if (!await details.locator('..').evaluate(n => n.open)) await details.click();
      const repro = center.getByText('Reproducibility identifiers', { exact: true }).locator('..');
      requireThat((await repro.innerText()).includes(report.result.requestId), 'Displayed plan identity differs');
      await repro.scrollIntoViewIfNeeded(); await capture('provenance');
      await page.getByRole('button', { name: 'I I/O', exact: true }).click();
      for (const format of ['pplan', 'txt']) {
        await page.getByRole('button', { name: format === 'pplan' ? '.pplan (JSON)' : '.txt', exact: true }).click();
        const responsePromise = page.waitForResponse(r => r.url().endsWith('/api/export_pinpoint') && r.request().method() === 'POST');
        const downloadPromise = page.waitForEvent('download').catch(error => ({ error: String(error) }));
        await page.getByRole('button', { name: 'Download .' + format, exact: true }).click();
        const response = await responsePromise, received = await response.json();
        requireThat(response.ok() && received.ok, 'Actual export rejected');
        const download = await downloadPromise;
        requireThat(!download.error, download.error || 'Actual download missing');
        const output = path.join(out, 'actual-plan.' + format); await download.saveAs(output);
        report.downloads.push({ format, path: output, sha256: sha(fs.readFileSync(output)),
          submitted: response.request().postDataJSON(), received });
        await capture('export-' + format);
      }
      return validateExports(report.result, report.runtime, report.replay, report.downloads);
    });
    await context.tracing.stop({ path: path.join(out, 'inspection-export-trace.zip') }); traceActive = false;
    await step('complete_ccf_mri_scene', async () => {
      // Listener observes only genuine messages from this page's actual renderer frame.
      await page.evaluate(() => {
        window.macUiObserved = { events: [], camera: null, fills: {}, progress: {} };
        window.addEventListener('message', event => {
          const frame = document.querySelector('iframe[title="Brain Planner Unity"]');
          if (!frame || event.source !== frame.contentWindow || event.origin !== location.origin || event.data?.source !== 'BrainPlannerUnity') return;
          const { event: name, data } = event.data;
          let parsed; try { parsed = typeof data === 'string' ? JSON.parse(data) : data; } catch { parsed = data; }
          const observed = window.macUiObserved;
          if (name === 'cameraPose') observed.camera = parsed;
          if (name === 'outlineProgress') observed.progress = parsed;
          if (['fillLoaded', 'fillFailed', 'fillSuppressed'].includes(name)) {
            const key = typeof parsed === 'string' ? parsed : parsed?.key ?? parsed?.region ?? parsed?.regionKey ?? parsed?.id;
            if (key) observed.fills[key] = name;
          }
          if (['outlineProgress', 'fillLoaded', 'fillFailed', 'fillSuppressed', 'surfaceState', 'sectionState', 'error', 'graphicsContextLost'].includes(name))
            observed.events.push({ name, data: parsed, at: Date.now() });
        });
      });
      await page.getByRole('button', { name: 'V Regions', exact: true }).click();
      await page.getByTestId('probeplanner-toggle-3d').click();
      await fullscreen();
      await disclosure('brain3d-display-controls', true);
      await capture('scene-controls-before-template');
      await page.getByTestId('brain3d-template-mri').waitFor({ state: 'visible', timeout: 10000 });
      await page.getByTestId('brain3d-template-mri').click();
      if (await page.getByTestId('brain3d-clinical-focus').getAttribute('aria-pressed') === 'true')
        await page.getByTestId('brain3d-clinical-focus').click();
      report.unverifiedLabels = {};
      for (const key of ['outer', 'skull']) {
        const button = page.getByTestId('brain3d-mri-layer-' + key);
        const label = { text: await button.innerText(), title: await button.getAttribute('title') };
        requireThat(label.text.includes('(unverified)') && label.title.includes('have not been validated'), 'Unverified label missing');
        report.unverifiedLabels[key] = label;
        if (await button.getAttribute('aria-pressed') === 'true') await button.click();
      }
      report.omittedDisplayLayers = ['outer_tissue', 'skull'];
      await disclosure('brain3d-display-controls', false);
      await disclosure('brain3d-slice-controls', true);
      await page.getByTestId('brain3d-contact-mode-slice_contact_persist').click();
      await page.getByTestId('brain3d-slice-axis-y').click();
      await page.getByRole('button', { name: 'Below', exact: true }).click();
      const ruler = await page.getByRole('slider', { name: 'Horizontal slice position', exact: true }).boundingBox();
      requireThat(ruler?.height > 10, 'Slice ruler missing');
      await page.mouse.click(ruler.x + ruler.width / 2, ruler.y + ruler.height * .35);
      await disclosure('brain3d-slice-controls', false);
      const state = await sceneReady(1800000);
      await capture('desktop-complete', true);
      report.renderer = await page.frameLocator('iframe[title="Brain Planner Unity"]').locator('canvas').evaluate(canvas => {
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
        if (!gl) return { available: false };
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        return { available: true, width: canvas.width, height: canvas.height,
          vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER), version: gl.getParameter(gl.VERSION),
          unmaskedRenderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null };
      });
      return state;
    });
    await context.tracing.start({ screenshots: true, snapshots: true }); traceActive = true;
    await step('real_interaction', async () => {
      const canvas = page.frameLocator('iframe[title="Brain Planner Unity"]').locator('canvas');
      const box = await canvas.boundingBox(); requireThat(box?.width > 100, 'Canvas absent');
      const before = await page.evaluate(() => window.macUiObserved.camera);
      const x = box.x + box.width * .68, y = box.y + box.height * .48;
      await page.mouse.move(x, y); await page.mouse.down();
      await page.mouse.move(x + 80, y + 25, { steps: 18 }); await page.mouse.up();
      await page.waitForFunction(old => JSON.stringify(window.macUiObserved.camera) !== JSON.stringify(old), before, { timeout: 30000 });
      await page.waitForTimeout(1500); await capture('desktop-after-drag', true);
      const after = await page.evaluate(() => window.macUiObserved.camera);
      await disclosure('brain3d-slice-controls', true);
      const oldSection = await page.getByTestId('brain3d-viewport').getAttribute('data-section-state');
      const ruler = await page.getByRole('slider', { name: 'Horizontal slice position', exact: true }).boundingBox();
      await page.mouse.click(ruler.x + ruler.width / 2, ruler.y + ruler.height * .50);
      await disclosure('brain3d-slice-controls', false);
      await page.waitForFunction(old => document.querySelector('[data-testid="brain3d-viewport"]').dataset.sectionState !== old,
        oldSection, { timeout: 30000 });
      const slice = await sceneReady(300000); await capture('desktop-new-slice', true);
      await page.getByRole('button', { name: 'Exit Fullscreen', exact: true }).click();
      await page.waitForFunction(() => !document.fullscreenElement);
      await page.setViewportSize({ width: 390, height: 844 });
      await fullscreen();
      for (const id of ['brain3d-slice-controls', 'brain3d-display-controls']) await disclosure(id, false);
      const mobile = await sceneReady(300000); await page.waitForTimeout(1500);
      await capture('mobile-complete', true);
      return { before, after, slice, mobile, resizedViewportNotPhysicalMobileDevice: true };
    });
    requireThat(report.requests.length === 1 && report.requests[0].matches, 'External request count differs');
    requireThat(!report.pageErrors.length, 'Uncaught browser errors');
    report.status = 'PASS_AUTOMATED_UI_PENDING_PIXEL_AND_VISUAL_REVIEW';
  } catch (error) {
    report.error = String(error); report.stack = error.stack;
    if (page) { try { await capture('failure'); } catch (e) { report.captureError = String(e); } }
  } finally {
    if (page) {
      try {
        report.scene = await page.evaluate(() => window.macUiObserved || null);
        report.finalViewport = await page.getByTestId('brain3d-viewport').evaluate(n => ({
          ...n.dataset, rectangle: n.getBoundingClientRect().toJSON(), fullscreen: document.fullscreenElement === n,
          displayOpen: n.querySelector('[data-testid="brain3d-display-controls"]')?.open,
          sliceOpen: n.querySelector('[data-testid="brain3d-slice-controls"]')?.open,
        })).catch(() => null);
      }
      catch (e) { report.sceneReadError = String(e); }
    }
    if (context && traceActive) {
      try { await context.tracing.stop({ path: path.join(out, 'final-trace.zip') }); }
      catch (e) { report.traceError = String(e); }
    }
    if (browser) await browser.close();
    write('BROWSER_RESULT.json', report);
    console.log(JSON.stringify({ status: report.status, error: report.error }));
    process.exitCode = report.status.startsWith('PASS_') ? 0 : 1;
  }
}
main();
