import { expect, it } from 'vitest';
import { contactRegionHemispheres, sampledContactHemispheres } from './contactHemispheres';

it('does not infer a whole-side exception from an old majority label', () => {
  expect(sampledContactHemispheres({ hemisphere: 'right' })).toEqual([]);
  expect(sampledContactHemispheres({ hemispheres: ['unknown', 'both', 'left', 'left'] })).toEqual(['left']);
});

it('unions real interval sides across probes and restricts keys to the transmitted region set', () => {
  expect(contactRegionHemispheres([
    { acronym: 'CA1', hemispheres: ['left'] },
    { acronym: 'CA1', hemispheres: ['right'] },
    { acronym: 'CA3', hemispheres: ['left'] },
    { acronym: 'VS', hemispheres: ['midline'] },
  ], ['CA1', 'VS', 'UNKNOWN'])).toEqual([
    { key: 'CA1', hemispheres: ['left', 'right'] },
    { key: 'VS', hemispheres: ['midline'] },
    { key: 'UNKNOWN', hemispheres: [] },
  ]);
});
