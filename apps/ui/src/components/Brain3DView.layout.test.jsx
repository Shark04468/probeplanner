import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Brain3DView, { buildMriAppearancePayload, toUnityProbe } from './Brain3DView';

const separateSurfaceRenderer = vi.fn(() => null);
vi.mock('./MriSurfaceOverlay', () => ({ default: (props) => separateSurfaceRenderer(props) }));
let resize;
beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback) { resize = callback; }
    observe() {}
    disconnect() {}
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function setSize(width, height) {
  act(() => resize([{ contentRect: { width, height } }]));
}

it('labels the recorded probe model instead of the next-run preset', () => {
  render(<Brain3DView lang="en" probePreset="NP2.0-4sh"
    probes={[{ probeType: 'NP2.0-1sh', shanks: 1, depthUm: 4000 }]} />);
  expect(screen.getByText('NP2.0-1sh x1')).toBeInTheDocument();
  expect(screen.queryByText('NP2.0-4sh x1')).not.toBeInTheDocument();
});

it('retains mixed and unspecified recorded models in the viewer legend', () => {
  render(<Brain3DView lang="en" probePreset="NP2.0-4sh" probes={[
    { probeType: 'NP2.0-1sh', shanks: 1, depthUm: 4000 },
    { probeType: 'NP1.0', shanks: 1, depthUm: 4000 },
    { depthUm: 4000 },
  ]} />);
  expect(screen.getByText('NP2.0-1sh x1; NP1.0 x1; Unspecified model x1')).toBeInTheDocument();
  expect(screen.queryByText('NP2.0-4sh x3')).not.toBeInTheDocument();
});

it('identifies nominal atlas landmarks without claiming subject skull measurements', () => {
  render(<Brain3DView lang="en" />);
  expect(screen.getAllByText('Atlas origin').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Nominal lambda').length).toBeGreaterThan(0);
  expect(screen.queryByText('Bregma')).not.toBeInTheDocument();
});

it('latches shared-scene graphics faults until its renderer is reloaded', () => {
  const onStage = vi.fn();
  render(<Brain3DView lang="en" backendStatus="online" onBridgeStageChange={onStage} />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const message = (event) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: '{}' },
  })));
  message('graphicsContextLost');
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'error');
  expect(onStage.mock.lastCall[0]).toMatchObject({ stage: 'error', bridgeReady: false, instanceReady: false });
  message('graphicsContextRestored');
  message('ready');
  message('instanceReady');
  fireEvent.load(frame);
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'error');
  expect(screen.queryByRole('button', { name: 'Resync bridge', exact: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload 3D view', exact: true }));
  expect(screen.getByTitle('Brain Planner Unity')).not.toBe(frame);
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'waiting_unity');
  message('ready');
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'waiting_unity');
});

it('uses one native scene for CCF and MRI, with no second surface canvas', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  expect(buildMriAppearancePayload(true, {}, 'mri_ccf_hybrid', true).surfaceRenderer).toBe('unity');
  expect(screen.getByTestId('brain3d-viewport')).toHaveAttribute('data-surface-renderer', 'unity');
  expect(separateSurfaceRenderer).not.toHaveBeenCalled();
});

it('names the implemented depth weighting without claiming an OIT compositor', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  expect(screen.getByTestId('brain3d-weighted-oit')).toHaveTextContent('Depth weighting');
  expect(screen.queryByText('Weighted OIT')).not.toBeInTheDocument();
});

it('honors disabling focus with a full catalog instead of silently hiding background again', async () => {
  vi.useFakeTimers();
  const brainRegions = Object.fromEntries(Array.from({ length: 80 }, (_, index) => [
    'R' + index, { structureId: index + 1000, structureIdPath: [997, index + 1000],
      groupKey: 'CTX', category: 'cortex', voxel_count: 100, n_subregions: 0, presentInVolume: true },
  ]));
  render(<Brain3DView lang="en" backendStatus="online" brainRegions={brainRegions} />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('capabilities', { protocolVersion: 1, supportsBulkState: false, supportedMethods: ['SetClinicalFocus'] });
  message('ready');
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(window.localStorage.getItem('probeplanner.clinicalFocus')).toBe('1');
  fireEvent.click(screen.getByTestId('brain3d-clinical-focus'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(window.localStorage.getItem('probeplanner.clinicalFocus')).toBe('0');
  const commands = post.mock.calls.map(([p]) => p).filter(p => p.method === 'SetClinicalFocus');
  expect(JSON.parse(commands.at(-1).param).enabled).toBe(false);
  post.mockRestore();
});

it('does not auto-enable a second brain envelope in the MRI profile or MRI template', async () => {
  vi.useFakeTimers();
  render(<Brain3DView lang="en" backendStatus="online" activeAtlasMode="mri_ccf_hybrid" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('capabilities', { protocolVersion: 1, supportsBulkState: false, supportedMethods: ['SetShellVisible'] });
  message('ready');
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const commands = () => post.mock.calls.map(([p]) => p).filter(p => p.method === 'SetShellVisible');
  expect(commands().length).toBeGreaterThan(0);
  expect(commands().every(p => p.param === 'false')).toBe(true);
  expect(screen.getByTestId('brain3d-shell')).toBeDisabled();
  fireEvent.click(screen.getByTestId('brain3d-template-mri'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(commands().every(p => p.param === 'false')).toBe(true);
  post.mockRestore();
});

it('does not treat available legacy skull and outer masks as validated anatomy', () => {
  render(<Brain3DView lang="en" backendStatus="online" activeAtlasMode="mri_ccf_hybrid"
    mriSurfaceMeshes={{ ready: true, layers: {
      brain_surface: { ready: true, provenanceType: 'atlas_annotation' },
      outer_tissue: { ready: true, displayName: 'Validated outer boundary', source: 'outer_tissue_mask' },
      skull: { ready: true, displayName: 'CT skull', source: 'skull_mask' },
    } }} />);
  expect(screen.getByTestId('brain3d-mri-layer-skull')).toHaveTextContent('Skull mask (unverified)');
  expect(screen.getByTestId('brain3d-mri-layer-outer')).toHaveTextContent('Outer-tissue mask (unverified)');
  for (const id of ['brain3d-mri-layer-skull', 'brain3d-mri-layer-outer']) {
    expect(screen.getByTestId(id)).toHaveAttribute('title', 'Display mask only; anatomical accuracy and subject-specific skull landmarks have not been validated.');
  }
  expect(screen.queryByText('CT skull')).not.toBeInTheDocument();
});

it('also qualifies legacy mask evidence in Chinese', () => {
  render(<Brain3DView lang="cn" activeAtlasMode="mri_ccf_hybrid" mriSurfaceMeshes={{
    layers: { outer_tissue: { ready: true }, skull: { ready: true } },
  }} />);
  expect(screen.getByTestId('brain3d-mri-layer-skull')).toHaveTextContent('\u9885\u9aa8\u63a9\u819c\uff08\u672a\u9a8c\u8bc1\uff09');
  expect(screen.getByTestId('brain3d-mri-layer-outer')).toHaveTextContent('\u5916\u5c42\u7ec4\u7ec7\u63a9\u819c\uff08\u672a\u9a8c\u8bc1\uff09');
});

it('retains actual native surface states and clears them when the renderer is replaced', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  const state = { key: 'skull', status: 'loaded', requestedLod: 1, resolvedLod: 0, visible: true };
  message('surfaceState', state);
  expect(JSON.parse(screen.getByTestId('brain3d-viewport').dataset.surfaceStates)).toEqual({ skull: state });
  message('graphicsContextLost');
  fireEvent.click(screen.getByRole('button', { name: 'Reload 3D view', exact: true }));
  message('surfaceState', state);
  expect(JSON.parse(screen.getByTestId('brain3d-viewport').dataset.surfaceStates)).toEqual({});
});

it('waits for the requested atlas envelope and fails closed when it is unavailable', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('ready');
  message('outlineProgress', { loaded: 2, failed: 0, total: 2 });
  const status = screen.getByTestId('brain3d-runtime-status');
  expect(status).toHaveAttribute('data-state', 'ready');
  fireEvent.click(screen.getByTestId('brain3d-shell'));
  expect(status).toHaveAttribute('data-state', 'streaming_mesh');
  message('surfaceState', { key: 'atlas_envelope', status: 'failed', resolvedLod: -1, visible: false });
  expect(status).toHaveAttribute('data-state', 'partial_mesh');
  fireEvent.click(screen.getByTestId('brain3d-shell'));
  expect(status).toHaveAttribute('data-state', 'ready');
  fireEvent.click(screen.getByTestId('brain3d-shell'));
  const loaded = { key: 'atlas_envelope', source: 'allen_ccf_structure_mesh', structureId: 997,
    status: 'loaded', requestedLod: 1, resolvedLod: 0, visible: true };
  message('surfaceState', loaded);
  expect(status).toHaveAttribute('data-state', 'ready');
  expect(JSON.parse(screen.getByTestId('brain3d-viewport').dataset.surfaceStates).atlas_envelope).toEqual(loaded);
});

it('retains actual section responses and clears them after a renderer replacement', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  const state = { key: 'mri_section', status: 'loaded', center: -2.3, requestKey: 'test', loadedPlanes: 1 };
  message('sectionState', state);
  expect(JSON.parse(screen.getByTestId('brain3d-viewport').dataset.sectionState)).toEqual(state);
  message('graphicsContextLost');
  fireEvent.click(screen.getByRole('button', { name: 'Reload 3D view', exact: true }));
  message('sectionState', state);
  expect(JSON.parse(screen.getByTestId('brain3d-viewport').dataset.sectionState)).toEqual({});
});

it('does not rewrite transparency or rendering settings while the slice ruler is held', async () => {
  vi.useFakeTimers();
  render(<Brain3DView lang="en" backendStatus="online" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('capabilities', { protocolVersion: 1, supportsBulkState: false, supportedMethods:
    ['SetInteractionQuality', 'SetPostFxEnabled', 'SetWeightedOit', 'SetAntiAliasingMode'] });
  message('ready');
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByTestId('brain3d-runtime-status')).not.toHaveAttribute('data-state', 'waiting_unity');
  expect(post.mock.calls.map(([payload]) => payload)).toContainEqual(expect.objectContaining({ method: 'SetInteractionQuality', param: 'high' }));
  post.mockClear();
  const slider = screen.getByRole('slider', { name: 'Horizontal slice position' });
  fireEvent(slider, new MouseEvent('pointerdown', { bubbles: true, button: 0, buttons: 1, clientY: 10 }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  const sent = post.mock.calls.map(([payload]) => payload);
  expect(sent).toContainEqual(expect.objectContaining({ method: 'SetInteractionQuality', param: 'low' }));
  expect(sent.filter(command => ['SetWeightedOit', 'SetPostFxEnabled', 'SetAntiAliasingMode'].includes(command.method))).toEqual([]);
  fireEvent(window, new MouseEvent('pointerup', { bubbles: true, button: 0 }));
  post.mockRestore();
});

it('recreates the mesh loader when retrying an incomplete stream', () => {
  render(<Brain3DView lang="en" backendStatus="online" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('ready');
  message('outlineProgress', { loaded: 1, failed: 1, excluded: 5, total: 2 });
  message('streamStage', { stage: 'partial', progress: 1 });
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'partial_mesh');
  fireEvent.click(screen.getByRole('button', { name: 'Retry mesh sync', exact: true }));
  expect(screen.getByTitle('Brain Planner Unity')).not.toBe(frame);
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'waiting_unity');
  message('outlineProgress', { loaded: 2, failed: 0, total: 2 });
  expect(screen.getByTestId('brain3d-runtime-status')).toHaveAttribute('data-state', 'waiting_unity');
});

it('collapses narrow-screen controls without unmounting the scene or deleting controls', () => {
  render(<Brain3DView lang="en" />);
  const frame = screen.getByTitle('Brain Planner Unity');
  setSize(390, 844);
  const slices = screen.getByTestId('brain3d-slice-controls');
  const display = screen.getByTestId('brain3d-display-controls');
  expect(slices).not.toHaveAttribute('open');
  expect(display).not.toHaveAttribute('open');
  expect(screen.getByTestId('brain3d-opacity-slider')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Slice and contacts', { selector: 'summary' }));
  fireEvent(slices, new Event('toggle'));
  expect(slices).toHaveAttribute('open');
  expect(screen.getByTitle('Brain Planner Unity')).toBe(frame);
});

it('keeps controls open in a large viewport and collapses embedded short viewers', () => {
  render(<Brain3DView lang="en" />);
  setSize(1600, 1050);
  expect(screen.getByTestId('brain3d-slice-controls')).toHaveAttribute('open');
  expect(screen.getByTestId('brain3d-display-controls')).toHaveAttribute('open');
  setSize(1105, 459);
  expect(screen.getByTestId('brain3d-viewport')).toHaveAttribute('data-compact', 'true');
  expect(screen.getByTestId('brain3d-display-controls')).not.toHaveAttribute('open');
});

it('keeps fullscreen above an expanded compact display panel', () => {
  render(<Brain3DView lang="en" />);
  setSize(1105, 459);
  const display = screen.getByTestId('brain3d-display-controls');
  fireEvent.click(display.querySelector('summary'));
  fireEvent(display, new Event('toggle'));
  expect(display).toHaveAttribute('open');
  expect(screen.getByTestId('brain3d-fullscreen')).toHaveStyle({ bottom: 'calc(32% + 8px)' });
  fireEvent.click(display.querySelector('summary'));
  fireEvent(display, new Event('toggle'));
  expect(screen.getByTestId('brain3d-fullscreen')).toHaveStyle({ bottom: '44px' });
});

it('lets desktop users collapse and reopen controls without resetting the scene or slice', () => {
  render(<Brain3DView lang="en" />);
  setSize(1600, 1050);
  const frame = screen.getByTitle('Brain Planner Unity');
  const slicePanel = screen.getByTestId('brain3d-slice-controls');
  const displayPanel = screen.getByTestId('brain3d-display-controls');
  fireEvent.click(screen.getByTestId('brain3d-slice-axis-x'));
  const opacity = screen.getByTestId('brain3d-opacity-slider');
  fireEvent.change(opacity, { target: { value: '61' } });
  for (const panel of [slicePanel, displayPanel]) {
    fireEvent.click(panel.querySelector('summary'));
    fireEvent(panel, new Event('toggle'));
    expect(panel).not.toHaveAttribute('open');
  }
  expect(screen.getByTestId('brain3d-fullscreen')).toHaveStyle({ bottom: '44px' });
  setSize(390, 844);
  fireEvent.click(displayPanel.querySelector('summary'));
  fireEvent(displayPanel, new Event('toggle'));
  expect(displayPanel).toHaveAttribute('open');
  setSize(1600, 1050);
  expect(slicePanel).not.toHaveAttribute('open');
  expect(displayPanel).not.toHaveAttribute('open');
  for (const panel of [slicePanel, displayPanel]) {
    fireEvent.click(panel.querySelector('summary'));
    fireEvent(panel, new Event('toggle'));
    expect(panel).toHaveAttribute('open');
  }
  expect(screen.getByTestId('brain3d-fullscreen')).toHaveStyle({ bottom: '164px' });
  expect(screen.getByRole('slider', { name: 'Sagittal slice position' })).toBeInTheDocument();
  expect(opacity).toHaveValue('61');
  expect(screen.getByTitle('Brain Planner Unity')).toBe(frame);
});

it('names the slice ruler after the actual selected axis', () => {
  render(<Brain3DView lang="en" />);
  expect(screen.getByRole('slider', { name: 'Horizontal slice position' })).toBeInTheDocument();
  fireEvent.click(screen.getByTestId('brain3d-slice-axis-x'));
  expect(screen.getByRole('slider', { name: 'Sagittal slice position' })).toBeInTheDocument();
});

it('does not refetch contact profiles because the preceding response updated display data', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    probes: [{ index: 0, contactIntervals: [{ key: 'CA1', startDepthMm: 1, endDepthMm: 2 }] }],
    contactRegionSet: ['CA1'], samplingStepUm: 25,
  }) }));
  vi.stubGlobal('fetch', fetchMock);
  const viewer = (depthUm) => <Brain3DView lang="en" backendStatus="online" backendBaseUrl="http://backend.test"
    probeSpec={{ depth: 4 }}
    probes={[{ ap_mm: -2, ml_mm: -1, theta_deg: 0, phi_deg: 0, depthUm }]} />;
  const { rerender } = render(viewer(4000));
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  const requests = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/probe_contact_profile'));
  expect(requests()).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requests()).toHaveLength(1);
  rerender(viewer(4000));
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(requests()).toHaveLength(1);
  rerender(viewer(5000));
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(requests()).toHaveLength(2);
});

it('preserves spin in Unity payloads, including legacy spin aliases', () => {
  expect(toUnityProbe({ spin_deg: 37.25 })).toMatchObject({ spin_deg: 37.25 });
  expect(toUnityProbe({ spin: -90 })).toMatchObject({ spin_deg: -90 });
  expect(toUnityProbe({})).toMatchObject({ spin_deg: 0 });
});

it('sends sampled contact sides without mirroring them with the label LR toggle', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({
    probes: [{ index: 0, contactIntervals: [
      { acronym: 'CA1', hemisphere: 'right', hemispheres: ['left', 'right'], dStartUm: 100, dEndUm: 300 },
      { acronym: 'CA3', hemisphere: 'left', hemispheres: ['left'], dStartUm: 300, dEndUm: 500 },
    ] }], contactRegionSet: ['CA1', 'CA3'],
  }) })));
  render(<Brain3DView lang="en" backendStatus="online" backendBaseUrl="http://backend.test"
    probes={[{ ap_mm: -2, ml_mm: -1, depthUm: 4000 }]} />);
  const frame = screen.getByTitle('Brain Planner Unity');
  const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
  const message = (event, data = {}) => act(() => window.dispatchEvent(new MessageEvent('message', {
    source: frame.contentWindow, data: { source: 'BrainPlannerUnity', event, data: JSON.stringify(data) },
  })));
  message('instanceReady');
  message('capabilities', { protocolVersion: 1, supportsBulkState: false, supportedMethods: ['SetContactProfile'] });
  message('ready');
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const contacts = () => post.mock.calls.map(([payload]) => payload)
    .filter(p => p.method === 'SetContactProfile').map(p => JSON.parse(p.param)).filter(p => p.enabled);
  const expected = [{ key: 'CA1', hemispheres: ['left', 'right'] }, { key: 'CA3', hemispheres: ['left'] }];
  expect(contacts().at(-1)?.regionHemispheres).toEqual(expected);
  fireEvent.click(screen.getByTestId('brain3d-hemi-lrfix'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(contacts().at(-1)?.regionHemispheres).toEqual(expected);
  post.mockRestore();
});

it('resamples contact profiles when spin changes without changing the centerline', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ probes: [], contactRegionSet: [] }) }));
  vi.stubGlobal('fetch', fetchMock);
  const viewer = (spin) => <Brain3DView lang="en" backendStatus="online" backendBaseUrl="http://backend.test"
    probes={[{ ap_mm: -2, ml_mm: -1, depthUm: 4000, shankCount: 2, spin }]} />;
  const { rerender } = render(viewer(0));
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  rerender(viewer(90));
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  const requests = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/probe_contact_profile'));
  expect(requests).toHaveLength(2);
  expect(requests.map(([, options]) => JSON.parse(options.body).probes[0].spin_deg)).toEqual([0, 90]);
});
