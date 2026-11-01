import { expect, it } from 'vitest';
import { normalizeRegionRecord } from './useBackend';
import { regionMeasurements } from '../components/brain3d/contextRegions';

it('preserves backend ontology and availability through the UI catalog adapter', () => {
  const normalized = normalizeRegionRecord({ key: 'CA1', structureId: 382,
    structureIdPath: [997, 8, 382], voxelCount: 42, volumeMm3: 0.7,
    nSubregions: 3, presentInVolume: false });
  expect(regionMeasurements(normalized)).toEqual({ structureId: 382,
    structureIdPath: [997, 8, 382], voxelCount: 42, volumeScore: 0.7,
    nSubregions: 3, presentInVolume: false });
});

it('accepts snake-case status metadata without inventing mesh availability', () => {
  const normalized = normalizeRegionRecord({ key: 'CA3', structureId: 463,
    voxel_count: 50, volume_mm3: 1.5, n_subregions: 2 });
  expect(regionMeasurements(normalized)).toMatchObject({ voxelCount: 50, volumeScore: 1.5,
    nSubregions: 2, structureIdPath: [], presentInVolume: undefined });
});
