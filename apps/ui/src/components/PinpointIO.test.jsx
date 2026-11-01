import { describe, expect, it } from 'vitest';
import { exportPinpointJSON, exportPinpointTXT, parsePinpoint, toBackendTrajectory } from './PinpointIO';

const trajectory = { ap_mm: -2.727, ml_mm: -2.234, entryDvCorr_mm: 0,
  phi_deg: 270, theta_deg: 10, spin_deg: 0, depthUm: 5325,
  probeType: 'NP2.0-1sh', coveredRegions: ['CA1', 'CA3'] };

const hardwareTrajectory = { ...trajectory, probeType: 'custom two shank', spin_deg: 37.25,
  shankCount: 2, shankPitchUm: 312.5, radiusUm: 22, shankWidthUm: 44, shankThicknessUm: 18 };

describe('recorded hardware conservation', () => {
  it('forwards recorded hardware and spin to the backend without preset replacement', () => {
    expect(toBackendTrajectory(hardwareTrajectory)).toMatchObject(hardwareTrajectory);
    expect(toBackendTrajectory({ ...trajectory, shanks: 2, shankSpacingUm: 300, probeRadiusUm: 25 }))
      .toMatchObject({ shankCount: 2, shankPitchUm: 300, radiusUm: 25 });
  });
  it.each([['JSON', exportPinpointJSON], ['TXT', exportPinpointTXT]])('preserves hardware and spin in %s', (_, encode) => {
    expect(parsePinpoint(encode([hardwareTrajectory]))[0]).toMatchObject(hardwareTrajectory);
  });
  it('does not invent hardware for legacy text', () => {
    const parsed = parsePinpoint('P1 -2.727 -2.234 0 270 10 0 5.325')[0];
    expect(parsed.shankCount).toBeUndefined();
    expect(parsed.shankPitchUm).toBeUndefined();
  });
  it.each([{ shankCount: 1.5 }, { shankPitchUm: -1 }, { radiusUm: 'NaN' },
    { shankCount: 2, shanks: 4 }])('rejects invalid or inconsistent hardware %j', fields => {
    expect(() => parsePinpoint(JSON.stringify({ generator: 'probe-planner', probes: [{ ...hardwareTrajectory, ...fields }] }))).toThrow();
  });
  it('rejects orphan or duplicate TXT metadata and malformed numeric tokens', () => {
    const text = exportPinpointTXT([hardwareTrajectory]);
    expect(() => parsePinpoint(text.replace('"id":"Probe0"', '"id":"missing"'))).toThrow('matching probe');
    expect(() => parsePinpoint(text + '\n# probe_metadata: {"id":"Probe0"}')).toThrow('duplicate');
    expect(() => parsePinpoint('P1 2x 0 0 0 0 0 4')).toThrow();
  });
  it.each([exportPinpointJSON, exportPinpointTXT])('does not quantize recorded pose during local export', encode => {
    const precise = { ...hardwareTrajectory, ap_mm: -2.72712345, theta_deg: 10.123456, depthUm: 5325.1234 };
    const result = parsePinpoint(encode([precise]))[0];
    expect(result.ap_mm).toBe(precise.ap_mm);
    expect(result.theta_deg).toBe(precise.theta_deg);
    expect(result.depthUm).toBeCloseTo(precise.depthUm, 8);
  });
});

describe('plan coordinate round trips', () => {
  it('keeps raw input and resolved physical fields distinct for linked backend export', () => {
    const corrected = { ...trajectory, ap: -9, ml: -9, dvEntry: -9,
      ap_mm_corrected: -1.5, ml_mm_corrected: 1.25, entryDvCorr_mm_corrected: .4, depthUm_corrected: 1500 };
    expect(toBackendTrajectory(corrected)).toMatchObject({ ...trajectory,
      ap_mm_corrected: -1.5, ml_mm_corrected: 1.25, entryDvCorr_mm_corrected: .4, depthUm_corrected: 1500 });
  });
  it.each([exportPinpointJSON, exportPinpointTXT])('exports resolved coordinates and depth without applying correction twice', encode => {
    const corrected = { ...trajectory, ap_mm_corrected: -1.5, ml_mm_corrected: 1.25,
      entryDvCorr_mm_corrected: .4, depthUm_corrected: 1500 };
    expect(parsePinpoint(encode([corrected]))[0]).toMatchObject({
      ap_mm: -1.5, ml_mm: 1.25, entryDvCorr_mm: .4, depthUm: 1500 });
  });
  it('round trips explicitly marked Pinpoint JSON without mirroring AP', () => {
    const text = exportPinpointJSON([trajectory]);
    expect(JSON.parse(text).probes[0].ap).toBe(2.727);
    expect(parsePinpoint(text)[0]).toMatchObject(trajectory);
  });
  it('decodes existing server exports using their recorded software convention', () => {
    const text = JSON.stringify({ _software: 'ProbeplannerGUI / optimizer_server', probes: [
      { ap: 2.727, ml: -2.234, dv: 0, depth: 5.325, phi: 270, theta: 10,
        probeType: 'NP2.0-1sh', coveredRegions: ['CA1', 'CA3'] },
    ] });
    expect(parsePinpoint(text)[0]).toMatchObject(trajectory);
  });
  it('round trips marked text geometry', () => {
    const parsed = parsePinpoint(exportPinpointTXT([trajectory]))[0];
    for (const key of ['ap_mm', 'ml_mm', 'entryDvCorr_mm', 'phi_deg', 'theta_deg', 'depthUm']) {
      expect(parsed[key]).toBeCloseTo(trajectory[key], 6);
    }
  });
  it('retains the documented internal sign in legacy frontend files', () => {
    expect(parsePinpoint(JSON.stringify({ generator: 'probe-planner', probes: [trajectory] }))[0].ap_mm).toBe(-2.727);
    expect(parsePinpoint('# id ap ml dv phi theta spin depth\nP1 -2.727 -2.234 0 270 10 0 5.325')[0].ap_mm).toBe(-2.727);
  });
  it('rejects ambiguous or unsupported JSON conventions instead of guessing a sign', () => {
    expect(() => parsePinpoint('{"probes":[{"ap":1}]}')).toThrow('Missing coordinate system');
    expect(() => parsePinpoint('{"_coordinateSystem":"unknown","probes":[]}')).toThrow('Unsupported coordinate system');
  });
  it('rejects non-finite angles', () => {
    expect(() => parsePinpoint(JSON.stringify({ generator: 'probe-planner', probes: [{ ...trajectory, phi_deg: 'NaN' }] }))).toThrow('Non-finite');
  });
});
