// Diagnosis only: preserve native call arguments, results and thrown errors.
function installWebGLObserver(root = globalThis) {
  const key = '__probePlannerGraphicsObservation';
  if (root[key]) throw Error('WebGL observer already installed');
  const report = { role: 'observational_diagnostic_not_acceptance', contexts: [],
    observationErrors: [], overflow: false, capturedSourceBytes: 0 };
  const contexts = new WeakMap(), installed = new WeakMap();
  const maxShaders = 600, maxPrograms = 240, maxSourceBytes = 8 * 1024 * 1024;
  function state(gl) {
    let s = contexts.get(gl);
    if (!s) {
      s = { shaders: new WeakMap(), programs: new WeakMap(), buffers: new WeakMap(),
        active: null, buffer: 'default', nextBuffer: 1,
        data: { id: report.contexts.length + 1, shaders: [], programs: [], drawCalls: 0 } };
      contexts.set(gl, s);
      report.contexts.push(s.data);
    }
    return s;
  }
  function shader(gl, obj) {
    const s = state(gl);
    if (!obj) return null;
    let row = s.shaders.get(obj);
    if (!row) {
      if (s.data.shaders.length >= maxShaders) { report.overflow = true; return null; }
      row = { id: s.data.shaders.length + 1, source: null, revisions: [], compiles: [] };
      s.shaders.set(obj, row);
      s.data.shaders.push(row);
    }
    return row;
  }
  function program(gl, obj) {
    const s = state(gl);
    if (!obj) return null;
    let row = s.programs.get(obj);
    if (!row) {
      if (s.data.programs.length >= maxPrograms) { report.overflow = true; return null; }
      row = { id: s.data.programs.length + 1, links: [], draws: 0, byFramebuffer: {}, byLinkAttempt: {} };
      s.programs.set(obj, row);
      s.data.programs.push(row);
    }
    return row;
  }
  const observe = {
    shaderSource(gl, args) {
      const row = shader(gl, args[0]);
      if (!row) return;
      const source = String(args[1]);
      const size = source.length * 2;
      if (report.capturedSourceBytes + size > maxSourceBytes) { report.overflow = true; return; }
      report.capturedSourceBytes += size;
      row.source = source;
      row.revisions.push(source);
    },
    compileShader(gl, args) {
      const row = shader(gl, args[0]);
      if (!row) return;
      if (row.compiles.length >= 16) { report.overflow = true; return; }
      row.compiles.push({ succeeded: gl.getShaderParameter(args[0], gl.COMPILE_STATUS),
        sourceRevision: row.revisions.length - 1,
        log: gl.getShaderInfoLog(args[0]), shaderType: gl.getShaderParameter(args[0], gl.SHADER_TYPE) });
    },
    linkProgram(gl, args) {
      const row = program(gl, args[0]);
      if (!row) return;
      if (row.links.length >= 16) { report.overflow = true; return; }
      row.links.push({ succeeded: gl.getProgramParameter(args[0], gl.LINK_STATUS),
        log: gl.getProgramInfoLog(args[0]),
        shaderIds: (gl.getAttachedShaders(args[0]) || []).map(obj => shader(gl, obj)?.id ?? null),
        shaderBindings: (gl.getAttachedShaders(args[0]) || []).map(obj => {
          const s = shader(gl, obj);
          return s ? { shaderId: s.id, compileIndex: s.compiles.length - 1,
            sourceRevision: s.compiles.at(-1)?.sourceRevision ?? null } : null;
        }) });
    },
    useProgram(gl, args) { state(gl).active = program(gl, args[0]); },
    bindFramebuffer(gl, args) {
      if (args[0] !== gl.FRAMEBUFFER && args[0] !== gl.DRAW_FRAMEBUFFER) return;
      const s = state(gl), obj = args[1];
      if (obj && !s.buffers.has(obj)) s.buffers.set(obj, 'framebuffer-' + s.nextBuffer++);
      s.buffer = obj ? s.buffers.get(obj) : 'default';
    },
  };
  for (const method of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {
    observe[method] = gl => {
      const s = state(gl);
      s.data.drawCalls++;
      if (s.active) {
        s.active.draws++;
        const link = s.active.links.length - 1;
        s.active.byLinkAttempt[link] = (s.active.byLinkAttempt[link] || 0) + 1;
        s.active.byFramebuffer[s.buffer] = (s.active.byFramebuffer[s.buffer] || 0) + 1;
      }
    };
  }
  for (const name of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
    for (const [method, after] of Object.entries(observe)) {
      let proto = root[name]?.prototype;
      while (proto && !Object.prototype.hasOwnProperty.call(proto, method)) proto = Object.getPrototypeOf(proto);
      if (!proto || typeof proto[method] !== 'function') continue;
      if (!installed.has(proto)) installed.set(proto, new Set());
      if (installed.get(proto).has(method)) continue;
      installed.get(proto).add(method);
      const original = proto[method];
      const descriptor = Object.getOwnPropertyDescriptor(proto, method);
      Object.defineProperty(proto, method, { ...descriptor, value: function (...args) {
        const result = Reflect.apply(original, this, args);
        try { after(this, args); }
        catch (error) {
          if (report.observationErrors.length < 100) report.observationErrors.push({ method, error: String(error) });
          else report.overflow = true;
        }
        return result;
      } });
    }
  }
  Object.defineProperty(root, key, { value: () => JSON.parse(JSON.stringify(report)), configurable: false });
}

module.exports = { installWebGLObserver };
