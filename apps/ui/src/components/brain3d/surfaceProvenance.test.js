import { describe, expect, it } from 'vitest';
import { surfaceLabelKey } from './surfaceProvenance';

describe('surface provenance labels', () => {
  it('labels annotation as atlas-derived despite a legacy MRI name', () => {
    const status = { layers: { brain_surface: { provenanceType: 'atlas_annotation', displayName: 'MRI brain surface' } } };
    expect(surfaceLabelKey('brainSurface', 'fallback', status)).toBe('brain3dAtlasBrainSurface');
  });
  it('labels an explicitly declared MRI mask separately', () => {
    const status = { layers: { brain_surface: { provenanceType: 'declared_mri_mask' } } };
    expect(surfaceLabelKey('brainSurface', 'fallback', status)).toBe('brain3dMriMaskBrainSurface');
  });
  it('does not infer MRI provenance from missing or old metadata', () => {
    expect(surfaceLabelKey('brainSurface', 'fallback', null)).toBe('brain3dBrainSurfaceUnverified');
    expect(surfaceLabelKey('brainSurface', 'fallback', { layers: { brain_surface: { displayName: 'MRI brain surface' } } })).toBe('brain3dBrainSurfaceUnverified');
  });
  it('preserves unrelated controls', () => {
    expect(surfaceLabelKey('ccfLabels', 'brain3dMriLayerCcfLabels', null)).toBe('brain3dMriLayerCcfLabels');
  });
});
