import { expect, it } from 'vitest';
import { meshStreamStatus } from './meshStreamStatus';

it('does not count failed downloads as successfully loaded anatomy', () => {
  expect(meshStreamStatus({ loaded: 9, failed: 1, total: 10 })).toMatchObject({ finished: true, ready: false });
});
it('waits for requested target fills even after outlines finish', () => {
  expect(meshStreamStatus({ loaded: 10, total: 10 }, ['CA1'])).toMatchObject({ ready: false, pendingTargets: ['CA1'] });
});
it('does not accept a missing or suppressed target as ready', () => {
  expect(meshStreamStatus({ loaded: 10, total: 10 }, ['CA1'], { CA1: 'failed' }).ready).toBe(false);
  expect(meshStreamStatus({ loaded: 10, total: 10 }, ['CA1'], { CA1: 'suppressed' }).ready).toBe(false);
});
it('accepts complete outlines and target fills, retaining excluded catalog counts separately', () => {
  expect(meshStreamStatus({ loaded: 10, failed: 0, total: 10, excluded: 5 }, ['CA1'], { CA1: 'loaded' }).ready).toBe(true);
  expect(meshStreamStatus({ loaded: 0, total: 0 }).ready).toBe(false);
});

it('does not declare the hybrid scene ready before its requested surfaces load', () => {
  const progress = { loaded: 10, total: 10 };
  expect(meshStreamStatus(progress, [], {}, ['brain_surface'], {}).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, ['brain_surface'], {
    brain_surface: { status: 'loading', resolvedLod: -1 },
  }).pendingSurfaces).toEqual(['brain_surface']);
});

it('retains surface failures and verifies actual LOD before accepting a surface', () => {
  const progress = { loaded: 10, total: 10 };
  expect(meshStreamStatus(progress, [], {}, ['skull'], {
    skull: { status: 'failed', resolvedLod: -1 },
  })).toMatchObject({ ready: false, failedSurfaces: ['skull'] });
  expect(meshStreamStatus(progress, [], {}, ['skull'], {
    skull: { status: 'loaded', resolvedLod: -1, visible: true },
  }).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, ['skull'], {
    skull: { status: 'loaded', requestedLod: 1, resolvedLod: 0, visible: true },
  }).ready).toBe(true);
});

it('does not gate a CCF-only scene on hidden surface failures', () => {
  expect(meshStreamStatus({ loaded: 10, total: 10 }, [], {}, [], {
    skull: { status: 'failed', resolvedLod: -1 },
  }).ready).toBe(true);
});

it('does not accept a requested envelope merely because its hidden mesh is cached', () => {
  expect(meshStreamStatus({ loaded: 10, total: 10 }, [], {}, ['atlas_envelope'], {
    atlas_envelope: { status: 'loaded', requestedLod: 1, resolvedLod: 0, visible: false },
  })).toMatchObject({ ready: false, pendingSurfaces: ['atlas_envelope'] });
});

it('waits for the actual MRI section rather than accepting complete shell meshes', () => {
  const progress = { loaded: 10, total: 10 };
  const section = { status: 'loaded', visible: true, loadedPlanes: 1, requestedPlanes: 1, manifestSha256: 'a'.repeat(64) };
  expect(meshStreamStatus(progress, [], {}, [], {}, true, {}).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, section).ready).toBe(true);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { ...section, requestedPlanes: 2 }).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { ...section, visible: false }).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { ...section, manifestSha256: '' }).ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { ...section, requestKey: 'old' }, 'new').ready).toBe(false);
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { ...section, requestKey: 'new' }, 'new').ready).toBe(true);
});

it('retains a section failure and does not require a disabled section', () => {
  const progress = { loaded: 10, total: 10 };
  expect(meshStreamStatus(progress, [], {}, [], {}, true, { status: 'failed' })).toMatchObject({
    ready: false, failedSections: ['mri_section'],
  });
  expect(meshStreamStatus(progress, [], {}, [], {}, false, { status: 'failed' }).ready).toBe(true);
});
