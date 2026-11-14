// These synthetic shaders validate the recorder, not the ProbePlanner scene.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { installWebGLObserver } = require('./observe_webgl.cjs');
const [dependencySource, output] = process.argv.slice(2).map(p => path.resolve(p));
const requireBrowser = createRequire(path.join(dependencySource, 'apps/ui/package.json'));
const { chromium } = requireBrowser('playwright');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
fs.mkdirSync(output);
const report = { status: 'FAIL', role: 'SYNTHETIC_OBSERVER_FIXTURES_ONLY',
  applicationTested: false, anatomyAccepted: false, macTested: false, cases: [] };
let browser;
async function main() {
  try {
    const unit = spawnSync(process.execPath, ['--test', path.join(__dirname, 'observer.test.cjs')], { encoding: 'utf8', timeout: 30000 });
    fs.writeFileSync(path.join(output, 'UNIT_TESTS.log'), unit.stdout + unit.stderr, { flag: 'wx' });
    assert.equal(unit.status, 0);
    browser = await chromium.launch({ headless: true, channel: 'chromium' });
    report.browserVersion = browser.version();
    report.playwrightVersion = requireBrowser('playwright/package.json').version;
    report.hostPlatform = process.platform;
    for (const kind of ['webgl', 'webgl2']) {
      const results = [];
      for (const instrumented of [false, true]) {
        const context = await browser.newContext({ viewport: { width: 128, height: 128 } });
        try {
          if (instrumented) await context.addInitScript(installWebGLObserver);
          const page = await context.newPage();
          await page.goto('data:text/html,<canvas width="64" height="64"></canvas>');
          const observed = await page.evaluate(kind => {
            const canvas = document.querySelector('canvas');
            const gl = canvas.getContext(kind, { preserveDrawingBuffer: true, antialias: false });
            if (!gl) throw Error('No context: ' + kind);
            const webgl2 = kind === 'webgl2';
            const vs = (webgl2 ? '#version 300 es\nin' : 'attribute') +
              ' vec2 position; void main() { gl_Position=vec4(position,0.0,1.0); }';
            const fs = webgl2 ? '#version 300 es\nprecision highp float; out vec4 result; void main(){ result=vec4(1.0,0.0,1.0,1.0); }' :
              'precision highp float; void main(){ gl_FragColor=vec4(1.0,0.0,1.0,1.0); }';
            function compile(type, source) {
              const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader); return shader;
            }
            const vertex = compile(gl.VERTEX_SHADER, vs), fragment = compile(gl.FRAGMENT_SHADER, fs);
            const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
            if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program));
            gl.useProgram(program);
            gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
            const location = gl.getAttribLocation(program, 'position');
            gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
            gl.viewport(0,0,64,64); gl.drawArrays(gl.TRIANGLES, 0, 3);
            const pixels = new Uint8Array(64*64*4); gl.readPixels(0,0,64,64,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
            const invalid = compile(gl.FRAGMENT_SHADER, 'deliberately invalid synthetic fixture');
            const badProgram = gl.createProgram(); gl.attachShader(badProgram, vertex); gl.attachShader(badProgram, invalid); gl.linkProgram(badProgram);
            const ext = gl.getExtension('WEBGL_debug_renderer_info');
            return { pixels: Array.from(pixels), failedCompile: !gl.getShaderParameter(invalid, gl.COMPILE_STATUS),
              failedLink: !gl.getProgramParameter(badProgram, gl.LINK_STATUS),
              renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
              observation: window.__probePlannerGraphicsObservation?.() ?? null };
          }, kind);
          assert.equal(observed.failedCompile, true); assert.equal(observed.failedLink, true);
          assert.deepEqual(observed.pixels.slice(0, 4), [255, 0, 255, 255]);
          observed.pixelsSha256 = hash(Buffer.from(observed.pixels)); delete observed.pixels;
          if (instrumented) {
            assert.equal(observed.observation.overflow, false);
            assert.deepEqual(observed.observation.observationErrors, []);
            const gl = observed.observation.contexts[0];
            assert.equal(gl.drawCalls, 1);
            assert.equal(gl.programs.filter(p => p.draws > 0).length, 1);
            assert.equal(gl.shaders.flatMap(s => s.compiles).filter(c => c.succeeded === false).length, 1);
            assert.equal(gl.programs.flatMap(p => p.links).filter(l => l.succeeded === false).length, 1);
          }
          results.push({ instrumented, ...observed });
        } finally { await context.close(); }
      }
      assert.equal(results[0].pixelsSha256, results[1].pixelsSha256);
      report.cases.push({ kind, results, samePixels: true });
    }
    report.status = 'PASS_OBSERVER_FIXTURES_ONLY';
  } catch (error) { report.error = String(error); report.stack = error.stack; }
  finally {
    if (browser) await browser.close();
    report.sources = Object.fromEntries(['observe_webgl.cjs', 'observer.test.cjs', 'qualify_observer.cjs'].map(name =>
      [name, hash(fs.readFileSync(path.join(__dirname, name)))]));
    fs.writeFileSync(path.join(output, 'RESULT.json'), JSON.stringify(report, null, 2)+'\n', { flag: 'wx' });
    console.log(JSON.stringify({ status: report.status, error: report.error, cases: report.cases.length }));
    process.exitCode = report.status === 'PASS_OBSERVER_FIXTURES_ONLY' ? 0 : 1;
  }
}
main();
