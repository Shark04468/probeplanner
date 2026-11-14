const test = require('node:test');
const assert = require('node:assert/strict');
const { installWebGLObserver } = require('./observe_webgl.cjs');

function fixture() {
  const sentinel = {};
  class GL {
    constructor() { Object.assign(this, { COMPILE_STATUS: 1, SHADER_TYPE: 2, LINK_STATUS: 3, FRAMEBUFFER: 4, DRAW_FRAMEBUFFER: 5 }); this.calls = []; }
    shaderSource(...args) { this.calls.push(['shaderSource', ...args]); return sentinel; }
    compileShader(...args) { this.calls.push(['compileShader', ...args]); }
    getShaderParameter(_, code) { return code === this.COMPILE_STATUS ? true : 35632; }
    getShaderInfoLog() { return ''; }
    linkProgram(...args) { this.calls.push(['linkProgram', ...args]); }
    getProgramParameter() { return true; }
    getProgramInfoLog() { return ''; }
    getAttachedShaders(obj) { return obj.shaders || []; }
    useProgram(...args) { this.calls.push(['useProgram', ...args]); }
    bindFramebuffer(...args) { this.calls.push(['bindFramebuffer', ...args]); }
    drawArrays(...args) { this.calls.push(['drawArrays', ...args]); }
    drawElements(...args) { this.calls.push(['drawElements', ...args]); }
    getError() { throw Error('Observer must not consume error state'); }
  }
  class GL2 extends GL {}
  const root = { WebGLRenderingContext: GL, WebGL2RenderingContext: GL2 };
  installWebGLObserver(root);
  return { root, gl: new GL2(), sentinel, snapshot: root.__probePlannerGraphicsObservation };
}

test('forwards exact inputs, result, and shared inherited methods once', () => {
  const { gl, sentinel, snapshot } = fixture(), shader = {}, program = { shaders: [shader] };
  assert.equal(gl.shaderSource(shader, 'unchanged source'), sentinel);
  gl.compileShader(shader); gl.linkProgram(program); gl.useProgram(program); gl.drawArrays(4, 0, 3);
  assert.equal(gl.calls.filter(c => c[0] === 'drawArrays').length, 1);
  const s = snapshot().contexts[0];
  assert.equal(s.drawCalls, 1); assert.equal(s.programs[0].draws, 1);
  assert.equal(s.shaders[0].source, 'unchanged source');
  assert.deepEqual(s.programs[0].links[0].shaderIds, [1]);
});
test('tracks default and offscreen draws separately without consuming errors', () => {
  const { gl, snapshot } = fixture(); gl.useProgram({}); gl.drawArrays(4, 0, 3);
  gl.bindFramebuffer(gl.FRAMEBUFFER, {}); gl.drawElements(4, 3, 1, 0);
  assert.deepEqual(snapshot().contexts[0].programs[0].byFramebuffer, { default: 1, 'framebuffer-1': 1 });
});
test('keeps native exceptions and does not report failed call as successful', () => {
  const error = Error('native'); class GL { shaderSource() { throw error; } }
  const root = { WebGLRenderingContext: GL }; installWebGLObserver(root);
  assert.throws(() => new GL().shaderSource({}, 'x'), e => e === error);
  assert.deepEqual(root.__probePlannerGraphicsObservation().contexts, []);
});
test('retains observer failure without breaking native call', () => {
  const { gl, snapshot } = fixture(); gl.getShaderInfoLog = () => { throw Error('read unavailable'); };
  assert.doesNotThrow(() => gl.compileShader({}));
  assert.equal(snapshot().observationErrors.length, 1);
});
test('explicitly flags oversized sources and bounded record overflow', () => {
  const { gl, snapshot } = fixture(); gl.shaderSource({}, 'x'.repeat(5 * 1024 * 1024));
  assert.equal(snapshot().overflow, true); assert.equal(snapshot().contexts[0].shaders[0].source, null);
});
test('rejects duplicate installation and returns detached snapshots', () => {
  const { root, gl, snapshot } = fixture(); gl.shaderSource({}, 'original');
  assert.throws(() => installWebGLObserver(root), /already installed/);
  const copy = snapshot(); copy.contexts[0].shaders[0].source = 'mutated';
  assert.equal(snapshot().contexts[0].shaders[0].source, 'original');
});
test('binds each compile/link to its original source revision after reuse', () => {
  const { gl, snapshot } = fixture(), shader = {}, program = { shaders: [shader] };
  gl.shaderSource(shader, 'first'); gl.compileShader(shader); gl.linkProgram(program); gl.useProgram(program);
  gl.drawArrays(4, 0, 3);
  gl.shaderSource(shader, 'second'); gl.compileShader(shader); gl.linkProgram(program); gl.drawArrays(4, 0, 3);
  const c = snapshot().contexts[0];
  assert.deepEqual(c.shaders[0].revisions, ['first','second']);
  assert.deepEqual(c.shaders[0].compiles.map(c => c.sourceRevision), [0,1]);
  assert.deepEqual(c.programs[0].links.map(l => l.shaderBindings[0]), [
    { shaderId: 1, compileIndex: 0, sourceRevision: 0 }, { shaderId: 1, compileIndex: 1, sourceRevision: 1 }]);
  assert.deepEqual(c.programs[0].byLinkAttempt, { 0: 1, 1: 1 });
});
