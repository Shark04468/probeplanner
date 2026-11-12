// Diagnostic scene only. This file never starts or changes the application.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { createRequire } = require('node:module');
const { inspect } = require('./contract.cjs');
const [sourceArg, outputArg] = process.argv.slice(2);
const source = path.resolve(sourceArg), output = path.resolve(outputArg);
const requireApp = createRequire(path.join(source, 'apps/ui/package.json'));
const { chromium } = requireApp('playwright');
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const save = (name, data) => fs.writeFileSync(path.join(output, name),
  JSON.stringify(data, null, 2) + '\n', { flag: 'wx' });
const report = { kind: 'synthetic_webgl_capability_only', status: 'INCOMPLETE',
  actualApplicationTested: false, fullMacWorkflowAccepted: false, anatomyAccepted: false,
  modes: [], node: process.version, platform: process.platform, architecture: process.arch,
  os: { type: os.type(), release: os.release(), cpuModels: [...new Set(os.cpus().map(c => c.model))],
    cpuCount: os.cpus().length, totalMemoryBytes: os.totalmem() },
  playwright: requireApp('playwright/package.json').version,
  sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' }).trim(),
  packageLockSha256: sha(path.join(source, 'apps/ui/package-lock.json')),
  startedUtc: new Date().toISOString() };

async function runMode(mode, channel) {
  const result = { kind: report.kind, mode, startedUtc: new Date().toISOString(), console: [] };
  let browser, timer;
  try {
    const run = async () => {
      browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}), timeout: 30000 });
      result.browserVersion = browser.version();
      const context = await browser.newContext({ viewport: { width: 800, height: 600 } });
      const page = await context.newPage();
      page.on('console', message => { if (['warning', 'error'].includes(message.type()))
        result.console.push({ type: message.type(), text: message.text() }); });
      const cdp = await browser.newBrowserCDPSession();
      try { result.gpu = (await cdp.send('SystemInfo.getInfo')).gpu; }
      catch (error) { result.gpuObservationError = String(error); }
      await cdp.detach();
      await page.setContent('<!doctype html><html><head><title>Synthetic WebGL diagnostic</title></head>' +
        '<body style="margin:0;background:white"><canvas id="probe" width="640" height="480"></canvas></body></html>');
      result.webgl = await page.evaluate(async () => {
        const canvas = document.querySelector('#probe');
        const gl = canvas.getContext('webgl2', { antialias: false, preserveDrawingBuffer: true });
        if (!gl) return { available: false, reason: 'webgl2_context_unavailable' };
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        const data = { available: true, vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
          version: gl.getParameter(gl.VERSION), shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
          unmaskedRenderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
          unmaskedVendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
          limits: { textureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
            vertexUniformVectors: gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS),
            fragmentUniformVectors: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS) },
          attributes: gl.getContextAttributes(), extensions: gl.getSupportedExtensions(), shaders: [] };
        const makeShader = (type, source) => {
          const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
          data.shaders.push({ type, compiled: gl.getShaderParameter(shader, gl.COMPILE_STATUS),
            log: gl.getShaderInfoLog(shader) });
          return shader;
        };
        const vertex = makeShader(gl.VERTEX_SHADER, '#version 300 es\nin vec2 position; void main(){gl_Position=vec4(position,0.0,1.0);}');
        const fragment = makeShader(gl.FRAGMENT_SHADER, '#version 300 es\nprecision highp float; out vec4 color; void main(){color=vec4(0.2,0.6,0.4,1.0);}');
        const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment);
        gl.linkProgram(program); data.linked = gl.getProgramParameter(program, gl.LINK_STATUS);
        data.linkLog = gl.getProgramInfoLog(program);
        if (!data.linked) return { ...data, contextLost: gl.isContextLost(), frames: 0 };
        gl.useProgram(program);
        const vertices = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vertices);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-.8, -.8, .8, -.8, 0, .8]), gl.STATIC_DRAW);
        const location = gl.getAttribLocation(program, 'position');
        gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
        gl.viewport(0, 0, canvas.width, canvas.height); gl.clearColor(.05, .07, .1, 1);
        const frameTimes = [];
        for (let i = 0; i < 24; i++) {
          await new Promise(resolve => requestAnimationFrame(timestamp => { frameTimes.push(timestamp); resolve(); }));
          gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3);
        }
        gl.finish();
        const pixel = new Uint8Array(4); gl.readPixels(320, 240, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
        data.centerPixel = Array.from(pixel); data.drawError = gl.getError(); data.contextLost = gl.isContextLost();
        data.frames = frameTimes.length; data.frameIntervalsMs = frameTimes.slice(1).map((x, i) => x - frameTimes[i]);
        return data;
      });
      await page.locator('#probe').screenshot({ path: path.join(output, mode + '.png'), timeout: 10000 });
    };
    await Promise.race([run(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(Error('Capability mode exceeded 60000 ms')), 60000);
    })]);
  } catch (error) { result.error = String(error); }
  finally {
    clearTimeout(timer);
    if (browser) await browser.close().catch(error => { result.closeError = String(error); });
    result.endedUtc = new Date().toISOString();
    result.assessment = inspect(result);
    save(mode + '.json', result); report.modes.push(result);
  }
}

async function main() {
  fs.mkdirSync(output, { recursive: false });
  try {
    if (report.sourceCommit !== '56868d57c23cbbb1207666f5e420bf47019591c6' || report.playwright !== '1.58.2')
      throw Error('Unexpected application commit or Playwright version');
    for (const name of ['PLAN.md', 'contract.cjs', 'test_contract.cjs', 'probe.cjs'])
      fs.copyFileSync(path.join(__dirname, name), path.join(output, name), fs.constants.COPYFILE_EXCL);
    await runMode('default_headless_shell');
    await runMode('chromium_channel_headless', 'chromium');
    report.status = report.modes.some(m => m.error || m.closeError)
      ? 'FAIL_CAPABILITY_DIAGNOSTIC_INCOMPLETE' : 'COMPLETE_CAPABILITY_OBSERVATIONS_ONLY';
  } catch (error) { report.status = 'FAIL_DIAGNOSTIC_SETUP'; report.error = String(error); }
  finally {
    report.endedUtc = new Date().toISOString();
    report.files = Object.fromEntries(fs.readdirSync(output).filter(name => fs.statSync(path.join(output, name)).isFile())
      .map(name => [name, sha(path.join(output, name))]));
    save('RESULTS.json', report);
    console.log(JSON.stringify({ status: report.status, platform: report.platform, architecture: report.architecture,
      modes: report.modes.map(m => ({ mode: m.mode, assessment: m.assessment, error: m.error })) }));
    if (report.status.startsWith('FAIL')) process.exitCode = 1;
  }
}
main().catch(error => { console.error(String(error)); process.exitCode = 1; });
