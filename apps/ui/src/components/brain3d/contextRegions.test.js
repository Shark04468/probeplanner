import { expect, it } from 'vitest';
import { regionMeasurements, selectContextKeys } from './contextRegions';

it('reads the actual snake-case status catalog without losing volume or hierarchy', () => {
  expect(regionMeasurements({ voxel_count: 200, volume_mm3: 3, n_subregions: 5,
    structureId: 30, structureIdPath: [1, 20, 30], presentInVolume: true }))
    .toMatchObject({ voxelCount: 200, volumeScore: 3, nSubregions: 5, structureIdPath: [1, 20, 30] });
  expect(regionMeasurements({ voxelCount: 9, nSubregions: 2 }).volumeScore).toBe(9);
});

const metadata = new Map([
  ['parent', { structureId: 10, structureIdPath: [1, 10], volumeScore: 100 }],
  ['target', { structureId: 20, structureIdPath: [1, 10, 20], volumeScore: 10 }],
  ['target-layer', { structureId: 21, structureIdPath: [1, 10, 20, 21], volumeScore: 2 }],
  ['neighbor', { structureId: 30, structureIdPath: [1, 10, 30], volumeScore: 20 }],
  ['neighbor-layer', { structureId: 31, structureIdPath: [1, 10, 30, 31], volumeScore: 1 }],
  ['absent', { structureId: 40, structureIdPath: [1, 40], volumeScore: 80, presentInVolume: false }],
]);

it('keeps targets and contacts while avoiding redundant automatic parent/child fills', () => {
  const chosen = selectContextKeys(metadata, ['target'], [...metadata.keys()], 72);
  expect(chosen).toEqual(['target', 'neighbor']);
  expect(selectContextKeys(metadata, ['target', 'target-layer'], [...metadata.keys()], 1))
    .toEqual(['target', 'target-layer']);
});

it('selects the same background for reversed catalog order and honors the fill budget', () => {
  expect(selectContextKeys(metadata, [], [...metadata.keys()].reverse(), 1)).toEqual(['parent']);
  expect(selectContextKeys(metadata, [], [...metadata.keys()], 1)).toEqual(['parent']);
});
