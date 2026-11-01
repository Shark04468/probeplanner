import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { buildBaseCandidates, resolveBase, useBackend } from './useBackend';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

it('discovers the configured atlas on first connection without mutating the server', async () => {
  localStorage.clear();
  const reply = data => ({ ok: true, json: async () => data });
  const fetch = vi.fn(async url => reply(url.includes('/api/status')
    ? { atlasLoaded: true, activeAtlasMode: 'mri_ccf_hybrid' } : { ok: true, regions: [] }));
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useBackend());
  await waitFor(() => expect(result.current.status).toBe('online'));
  expect(result.current.atlasMode).toBe('mri_ccf_hybrid');
  expect(fetch.mock.calls.some(([url, options]) => url.includes('/atlas/mode') && options.method === 'POST')).toBe(false);
  const statusUrl = fetch.mock.calls.find(([url]) => url.includes('/api/status'))[0];
  expect(statusUrl).not.toContain('atlasMode=');
  unmount();
});

it('requires an explicit request before switching a configured atlas', async () => {
  localStorage.setItem('probePlannerAtlasMode', 'allen_sdk');
  let mode = 'mri_ccf_hybrid';
  const reply = data => ({ ok: true, json: async () => data });
  const fetch = vi.fn(async (url, options) => {
    if (url.includes('/atlas/mode') && options.method === 'POST') {
      mode = JSON.parse(options.body).mode;
      return reply({ ok: true, activeAtlasMode: mode });
    }
    return reply(url.includes('/api/status') ? { atlasLoaded: true, activeAtlasMode: mode } : { ok: true, regions: [] });
  });
  vi.stubGlobal('fetch', fetch);
  const { result, unmount } = renderHook(() => useBackend());
  await waitFor(() => expect(result.current.status).toBe('online'));
  expect(mode).toBe('mri_ccf_hybrid');
  await act(async () => result.current.connect({ atlasMode: 'qiu2018' }));
  expect(mode).toBe('qiu2018');
  expect(result.current.atlasMode).toBe('qiu2018');
  unmount();
});

it('keeps the local dev-server fallback without redirecting explicit backend ports', () => {
  const candidates = buildBaseCandidates(window.location.origin);
  expect(candidates[0]).toBe(window.location.origin);
  expect(candidates).toContain('http://127.0.0.1:5000');
  expect(buildBaseCandidates('http://127.0.0.1:5043'))
    .toEqual(['http://127.0.0.1:5043', 'http://localhost:5043']);
});

it('keeps the actual atlas and reports an explicitly rejected mode change', async () => {
  const reply = data => ({ ok: true, json: async () => data });
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.includes('/atlas/mode') && options.method === 'POST') return {
      ok: false, status: 409, json: async () => ({ error: 'Paper profile mode is locked' }),
      text: async () => JSON.stringify({ error: 'Paper profile mode is locked' }),
    };
    return reply(url.includes('/api/status') ? { atlasLoaded: true, activeAtlasMode: 'mri_ccf_hybrid' } : { ok: true, regions: [] });
  }));
  const { result, unmount } = renderHook(() => useBackend());
  await waitFor(() => expect(result.current.status).toBe('online'));
  await act(async () => result.current.connect({ atlasMode: 'allen_sdk' }));
  expect(result.current.atlasMode).toBe('mri_ccf_hybrid');
  expect(result.current.status).toBe('online');
  expect(result.current.error).toContain('locked');
  unmount();
});

it('uses the served application origin unless a backend is explicitly configured', () => {
  localStorage.clear();
  expect(resolveBase()).toBe(window.location.origin);
  localStorage.setItem('probePlannerBackendBase', 'http://localhost:5042/');
  expect(resolveBase()).toBe('http://localhost:5042');
  expect(resolveBase('http://localhost:5043/')).toBe('http://localhost:5043');
});

it('publishes the real catalog while optional MRI enrichment is still pending', async () => {
  localStorage.setItem('probePlannerBackendBase', 'http://localhost:5042');
  localStorage.setItem('probePlannerAtlasMode', 'mri_ccf_hybrid');
  let finishSummary;
  const reply = data => ({ ok: true, json: async () => data });
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url.includes('/api/status')) return reply({ atlasLoaded: true });
    if (url.includes('/api/regions?')) return reply({ regions: [{ key: 'CA1', structureId: 382,
      structureIdPath: [997, 8, 382], voxelCount: 42 }] });
    if (url.includes('/registration/summary')) return new Promise(resolve => {
      finishSummary = () => resolve(reply({ ok: true, measurementEvidenceComplete: false }));
    });
    return reply({ ok: true });
  }));
  const { result, unmount } = renderHook(() => useBackend());
  await waitFor(() => expect(result.current.atlasInfo?.regions.CA1.structureId).toBe(382));
  expect(result.current.atlasInfo.mriRegistrationSummary).toBeUndefined();
  await act(async () => finishSummary());
  await waitFor(() => expect(result.current.atlasInfo.mriRegistrationSummary?.ok).toBe(true));
  expect(result.current.atlasInfo.regions.CA1.structureIdPath).toEqual([997, 8, 382]);
  unmount();
});

it('ignores pending catalog and summary responses after disconnect', async () => {
  localStorage.setItem('probePlannerBackendBase', 'http://localhost:5042');
  localStorage.setItem('probePlannerAtlasMode', 'mri_ccf_hybrid');
  const pending = [];
  const reply = data => ({ ok: true, json: async () => data });
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url.includes('/api/status')) return reply({ atlasLoaded: true });
    if (url.includes('/api/regions?') || url.includes('/registration/summary')) {
      return new Promise(resolve => pending.push(() => resolve(reply({ ok: true,
        regions: [{ key: 'CA1', structureId: 382 }] }))));
    }
    return reply({ ok: true });
  }));
  const { result, unmount } = renderHook(() => useBackend());
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => result.current.disconnect());
  await act(async () => pending.forEach(resolve => resolve()));
  expect(result.current.atlasInfo).toBeNull();
  expect(result.current.status).toBe('idle');
  unmount();
});
