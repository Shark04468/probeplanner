import { describe, expect, it } from 'vitest';
import { ccfUmToViewer, regionToWorld, probePointAtDepth, normalizeProbeSegment, axisCoordValue, resolvedSliceCoordinate } from './ccfCoordinates';

describe('CCF/viewer coordinate consistency', () => {
  it('uses resolved physical coordinates and depth when raw controls differ', () => {
    const probe = { ap_mm: 4, ml_mm: 3, entryDvCorr_mm: 2, depthUm: 1000,
      ap_mm_corrected: -2, ml_mm_corrected: -1, entryDvCorr_mm_corrected: .5, depthUm_corrected: 2000,
      theta_deg: 30, phi_deg: 0 };
    const segment = normalizeProbeSegment(probe);
    expect(segment.entry).toEqual({ x: -1, y: -.5, z: -2 });
    expect(segment.tip.z).toBeCloseTo(-3, 12);
    expect(segment.tip.y).toBeCloseTo(-.5-Math.sqrt(3), 12);
  });
  it('uses the actual snapped clip plane, but never an older section acknowledgement', () => {
    const section = { requestKey: 'current', center: -2.29936981, status: 'loaded' };
    expect(resolvedSliceCoordinate(-2.35, section, 'current', true)).toBe(section.center);
    expect(resolvedSliceCoordinate(-2.35, section, 'new', true)).toBe(-2.35);
    expect(resolvedSliceCoordinate(-2.35, section, 'current', false)).toBe(-2.35);
    expect(resolvedSliceCoordinate(-2.35, { ...section, center: NaN }, 'current', true)).toBe(-2.35);
  });
  it('does not negate AP twice for posterior region labels', () => {
    expect(regionToWorld({ ap: -2.627, dv: 2.695, ml: -2.734 }, 'left'))
      .toEqual({ x: -2.734, y: -2.695, z: -2.627 });
    expect(ccfUmToViewer([8027, 3135, 2966])).toEqual({ x: -2.734, y: -2.695, z: -2.627 });
  });
  it.each([0, 45, 90, 135, 180, 225, 270, 315])('matches a CCF trajectory endpoint at phi=%s', (phi) => {
    const theta = 20 * Math.PI / 180;
    const p = phi * Math.PI / 180;
    const entry = [8100, 700, 3600];
    const depth = 4000;
    const tip = [entry[0] + depth * Math.sin(theta) * Math.cos(p),
      entry[1] + depth * Math.cos(theta), entry[2] + depth * Math.sin(theta) * Math.sin(p)];
    const probe = { ap_mm: -2.7, ml_mm: -2.1, entryDvCorr_mm: 0.26, theta_deg: 20, phi_deg: phi };
    const actual = probePointAtDepth(probe, depth);
    const expected = ccfUmToViewer(tip);
    for (const axis of ['x', 'y', 'z']) expect(actual[axis]).toBeCloseTo(expected[axis], 12);
  });
  it('only mirrors ML and only reverses the DV slice label', () => {
    const probe = { ap_mm: -2, ml_mm: 1, entryDvCorr_mm: 0.5, theta_deg: 0, phi_deg: 0 };
    expect(probePointAtDepth(probe, 1000, true)).toEqual({ x: -1, y: -1.5, z: -2 });
    expect(axisCoordValue('z', -2)).toBe(-2);
    expect(axisCoordValue('y', -2)).toBe(2);
  });
});
