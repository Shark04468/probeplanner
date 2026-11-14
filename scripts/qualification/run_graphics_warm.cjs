// Mac-specific warm-resource wrapper; all dynamic requests remain live.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createMirror, assertIdentity, hash } = require('./asset_mirror.cjs');
const [source, output, upstream, manifestPath, savedOrigin] = process.argv.slice(2);
const out = path.resolve(output);
const write = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2)+'\n', { flag: 'wx' });
async function main() {
  const report = { status: 'FAIL', coldAssetDeliveryAccepted: false, fullMacWorkflowAccepted: false,
    anatomyAccepted: false, allowedOptimizationRequests: 0, role: 'short_actual_mac_graphics_diagnostic' };
  let mirror, child, timer, forceTimer, poll;
  try {
    if (process.platform !== 'darwin' || !savedOrigin) throw Error('Actual Mac and bound saved-plan origin required');
    const manifest = JSON.parse(fs.readFileSync(manifestPath));
    const originalHash = hash(fs.readFileSync(manifestPath));
    const runtime = async () => {
      const response = await fetch(upstream+'/api/paper/runtime', { signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw Error('Runtime unavailable');
      const value = await response.json();
      assertIdentity(value, manifest);
      return value;
    };
    const observed = await runtime();
    const event = value => fs.appendFileSync(path.join(out, 'DELIVERY_EVENTS.jsonl'), JSON.stringify(value)+'\n');
    mirror = createMirror({ upstream, entries: manifest.assets, roots: {
      static: manifest.staticRoot, candidates: manifest.candidatesRoot }, expected: manifest, event });
    mirror.assertRuntime(observed);
    await new Promise(resolve => mirror.server.listen(0, '127.0.0.1', resolve));
    const base = 'http://127.0.0.1:'+mirror.server.address().port;
    write('WARM_DEPLOYMENT.json', { ...report, upstream, base, manifestSha256: originalHash,
      runtime: observed, observationSeconds: 300, driverBudgetSeconds: 900,
      rendererPolicy: 'Observe actual renderer; no hardware or full-scene acceptance claim' });
    let polling = false;
    poll = setInterval(async () => {
      if (polling) return;
      polling = true;
      try { mirror.assertRuntime(await runtime()); }
      catch (e) { mirror.invalidate(String(e)); }
      finally { polling = false; }
    }, 30000);
    child = spawn(process.execPath, [path.join(__dirname, 'graphics_ui.cjs'), path.resolve(source), out, base, path.resolve(savedOrigin)],
      { cwd: source, stdio: 'inherit', detached: true });
    timer = setTimeout(() => {
      report.driverTimeout = true;
      if (child.exitCode === null && child.signalCode === null) {
        try { process.kill(-child.pid, 'SIGTERM'); } catch (e) { report.stopError=String(e); }
        forceTimer = setTimeout(() => {
          if (child.exitCode === null && child.signalCode === null) {
            try { process.kill(-child.pid, 'SIGKILL'); } catch (e) { report.forceStopError=String(e); }
          }
        }, 30000);
      }
    }, 900000);
    report.returncode = await new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject); });
    if (hash(fs.readFileSync(manifestPath)) !== originalHash) throw Error('Mirror manifest changed');
    const browser = JSON.parse(fs.readFileSync(path.join(out, 'BROWSER_RESULT.json')));
    report.browserStatus = browser.status;
    if (!report.driverTimeout && report.returncode===0 && browser.status==='DIAGNOSTIC_COMPLETED_NO_ACCEPTANCE')
      report.status='DIAGNOSTIC_COMPLETED_NO_ACCEPTANCE';
  } catch (e) { report.error=String(e); report.stack=e.stack; }
  finally {
    clearTimeout(timer); clearTimeout(forceTimer); clearInterval(poll);
    if (mirror) await new Promise(resolve => { mirror.server.closeAllConnections(); mirror.server.close(resolve); });
    write('WARM_DEPLOYMENT_RESULT.json', report);
    console.log(JSON.stringify(report));
    process.exitCode=report.status === 'DIAGNOSTIC_COMPLETED_NO_ACCEPTANCE' ? 0 : 1;
  }
}
main();
