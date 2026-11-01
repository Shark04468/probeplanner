// @vitest-environment node
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const html = readFileSync(new URL('../../../public/unity/index.html', import.meta.url), 'utf8');
const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function wrapper() {
  const events = {};
  const canvasEvents = {};
  const parent = { postMessage: vi.fn() };
  const element = { style: {}, addEventListener: (name, fn) => { canvasEvents[name] = fn; } };
  const win = {
    parent, location: { search: '?backend=http%3A%2F%2F127.0.0.1%3A5039', origin: 'http://127.0.0.1:5039' },
    devicePixelRatio: 1, innerWidth: 1280, innerHeight: 720,
    addEventListener: (name, fn) => { events[name] = fn; },
    unityInstance: { SendMessage: vi.fn() },
  };
  vm.runInNewContext(inline, {
    window: win, URLSearchParams, console: { warn: vi.fn(), error: vi.fn() },
    document: { getElementById: () => element, createElement: () => ({}), body: { appendChild: vi.fn() } },
  });
  return { win, parent, events, canvasEvents, emit: (data, extra = {}) => events.message({
    data: { target: 'BrainPlannerUnity', ...data }, source: parent, origin: win.location.origin, ...extra,
  }) };
}

describe('compiled Unity viewer wrapper contract', () => {
  it('reports context loss and stops dispatch and heartbeat acknowledgements', () => {
    const { win, parent, canvasEvents, emit } = wrapper();
    const event = { preventDefault: vi.fn() };
    canvasEvents.webglcontextlost(event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(parent.postMessage.mock.calls[0][0]).toMatchObject({ event: 'graphicsContextLost', data: 'Unity' });
    emit({ method: 'SetProbes' });
    emit({ method: 'Heartbeat' });
    expect(win.unityInstance.SendMessage).not.toHaveBeenCalled();
    expect(parent.postMessage).toHaveBeenCalledTimes(1);
  });
  it('does not treat a restored buffer as scene recovery', () => {
    const { win, parent, canvasEvents, emit } = wrapper();
    canvasEvents.webglcontextlost({ preventDefault: vi.fn() });
    canvasEvents.webglcontextrestored();
    expect(parent.postMessage.mock.calls[1][0]).toMatchObject({ event: 'graphicsContextRestored', data: 'reload_required' });
    emit({ method: 'Heartbeat' });
    emit({ method: 'SetStateSnapshot', param: '{}' });
    expect(win.unityInstance.SendMessage).not.toHaveBeenCalled();
    expect(parent.postMessage).toHaveBeenCalledTimes(2);
    const reloaded = wrapper();
    reloaded.emit({ method: 'SetStateSnapshot', param: '{}' });
    expect(reloaded.win.unityInstance.SendMessage).toHaveBeenCalledOnce();
  });
  it('sends typed commands through the unique dispatcher and waits for C# acknowledgement', () => {
    const { win, parent, emit } = wrapper();
    emit({ method: 'SetClinicalFocus', param: '{"enabled":true}', stateVersion: 12 });
    const [object, method, payload] = win.unityInstance.SendMessage.mock.calls[0];
    expect([object, method]).toEqual(['BrainController', 'DispatchCommand']);
    expect(JSON.parse(payload)).toMatchObject({ method: 'SetClinicalFocus', param: '{"enabled":true}', stateVersion: 12 });
    expect(parent.postMessage).not.toHaveBeenCalled();
  });
  it('does not invent a snapshot-applied event before C# handles the snapshot', () => {
    const { parent, emit } = wrapper();
    emit({ method: 'SetStateSnapshot', param: '{}' });
    expect(parent.postMessage).not.toHaveBeenCalled();
  });
  it('does not hide dispatch errors behind successful acknowledgements', () => {
    const { win, parent, emit } = wrapper();
    win.unityInstance.SendMessage.mockImplementation(() => { throw new Error('Maximum call stack size exceeded'); });
    emit({ method: 'SetSlice', param: '{}' });
    expect(parent.postMessage.mock.calls[0][0].event).toBe('error');
  });
  it('reports previously suppressed WASM runtime faults', () => {
    const { parent, events } = wrapper();
    events.error({ message: 'RuntimeError: table index is out of bounds wasm-function' });
    expect(parent.postMessage.mock.calls[0][0].event).toBe('error');
  });
  it('rejects foreign window and origin messages', () => {
    const { win, emit } = wrapper();
    emit({ method: 'SetProbes' }, { source: {} });
    emit({ method: 'SetProbes' }, { origin: 'https://unrelated.invalid' });
    expect(win.unityInstance.SendMessage).not.toHaveBeenCalled();
  });
  it('advertises commands implemented by the recovered and revised C# bridge', () => {
    const { parent, emit } = wrapper();
    emit({ method: 'GetCapabilities' });
    const caps = JSON.parse(parent.postMessage.mock.calls[0][0].data);
    const source = readFileSync(new URL('../../../../unity/Assets/Scripts/Bridge/ReactBridge.cs', import.meta.url), 'utf8');
    const typed = [...source.matchAll(/\{ nameof\((\w+)\), \1 \}/g)].map(m => m[1]);
    for (const method of caps.supportedMethods.filter(m => !['GetCapabilities', 'Heartbeat'].includes(m))) {
      expect(typed).toContain(method);
    }
    expect(caps.unsupportedMethods).toEqual([]);
  });
});
