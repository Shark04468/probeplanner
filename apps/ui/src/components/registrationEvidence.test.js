import { describe, expect, it } from 'vitest';
import { measurementValue, measurementView, registrationGateState } from './registrationEvidence';

describe('registration measurement presentation', () => {
  it('does not display old diagnostics as measurements', () => {
    expect(measurementView({ metrics: { treMm: 0.2, dice: 0.9 } }).treMm).toBeNull();
  });
  it('does not coerce missing, string, or infinite values to zero', () => {
    for (const value of [null, undefined, '', '0', Infinity, NaN]) {
      expect(measurementValue({ metricAssessment: { dice: { usableAsMeasurement: true } }, measurementMetrics: { dice: value } }, 'dice')).toBeNull();
    }
  });
  it('retains a genuinely measured zero', () => {
    expect(measurementValue({ metricAssessment: { treMm: { usableAsMeasurement: true } }, measurementMetrics: { treMm: 0 } }, 'treMm')).toBe(0);
  });
  it('does not infer median, maximum or CI from a scalar TRE', () => {
    const result = measurementView({ metricAssessment: { treMm: { usableAsMeasurement: true } }, measurementMetrics: { treMm: 0.2 }, metricCi95: { treMm: [0.18, 0.22] } });
    expect(result.treMedianMm).toBeNull();
    expect(result.treMaxMm).toBeNull();
    expect(result.treCi95Mm).toBeNull();
  });
  it('renders incomplete separately from pass and failure', () => {
    expect(registrationGateState({ passed: true, status: 'INCOMPLETE_MEASUREMENT_EVIDENCE' })).toBe('incomplete');
    expect(registrationGateState({ passed: false, status: 'INCOMPLETE_MEASUREMENT_EVIDENCE' })).toBe('incomplete');
    expect(registrationGateState({ passed: true })).toBe('incomplete');
    expect(registrationGateState({ passed: false, status: 'FAIL' })).toBe('failed');
    expect(registrationGateState({ passed: true, status: 'PASS' })).toBe('passed');
  });
});
