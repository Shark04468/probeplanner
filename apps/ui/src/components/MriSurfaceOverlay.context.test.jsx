import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import MriSurfaceOverlay from './MriSurfaceOverlay';

const state = vi.hoisted(() => ({ renderers: [], failInit: false }));
vi.mock('three', async (original) => ({
  ...await original(),
  WebGLRenderer: class {
    constructor() {
      if (state.failInit) throw new Error('Context creation failed');
      this.domElement = document.createElement('canvas');
      this.render = vi.fn();
      this.dispose = vi.fn();
      this.forceContextLoss = vi.fn();
      state.renderers.push(this);
    }
    setClearColor() {}
    setSize() {}
    setPixelRatio() {}
  },
}));

let tick;
beforeEach(() => {
  state.renderers = [];
  state.failInit = false;
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => { tick = callback; return 1; }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('reports overlay context loss, stops rendering, and requires a fresh renderer', () => {
  const onFault = vi.fn();
  const props = { active: true, layers: {}, onRendererFault: onFault };
  const { container, rerender } = render(<MriSurfaceOverlay key="first" {...props} />);
  const first = state.renderers[0];
  const canvas = container.querySelector('canvas');
  const lost = new Event('webglcontextlost', { cancelable: true });
  fireEvent(canvas, lost);
  expect(lost.defaultPrevented).toBe(true);
  expect(onFault).toHaveBeenCalledWith('MRI surface');
  const priorRenders = first.render.mock.calls.length;
  fireEvent(canvas, new Event('webglcontextrestored'));
  act(() => tick());
  expect(first.render).toHaveBeenCalledTimes(priorRenders);
  rerender(<MriSurfaceOverlay key="reloaded" {...props} />);
  expect(first.dispose).toHaveBeenCalledOnce();
  expect(first.forceContextLoss).toHaveBeenCalledOnce();
  expect(container.querySelector('canvas')).not.toBe(canvas);
  expect(state.renderers[1].render).toHaveBeenCalled();
  fireEvent(canvas, new Event('webglcontextlost', { cancelable: true }));
  expect(onFault).toHaveBeenCalledOnce();
});

it('retains one renderer when MRI appearance is toggled and stops drawing while inactive', () => {
  const { rerender } = render(<MriSurfaceOverlay active layers={{}} />);
  const renderer = state.renderers[0];
  const before = renderer.render.mock.calls.length;
  rerender(<MriSurfaceOverlay active={false} layers={{}} />);
  act(() => tick());
  expect(renderer.render).toHaveBeenCalledTimes(before);
  rerender(<MriSurfaceOverlay active layers={{}} />);
  act(() => tick());
  expect(renderer.render).toHaveBeenCalledTimes(before + 1);
  expect(state.renderers).toHaveLength(1);
  expect(renderer.dispose).not.toHaveBeenCalled();
});

it('reports failed context creation instead of leaving an apparently healthy blank overlay', () => {
  state.failInit = true;
  const onFault = vi.fn();
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const { container } = render(<MriSurfaceOverlay active layers={{}} onRendererFault={onFault} />);
  expect(onFault).toHaveBeenCalledWith('MRI surface');
  expect(container.querySelector('canvas')).toBeNull();
  expect(error).toHaveBeenCalledWith('[MriSurfaceOverlay] renderer initialization failed', expect.any(Error));
});
