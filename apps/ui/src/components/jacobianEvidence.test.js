import { describe, expect, it } from 'vitest';
import { measurementJacobianSummary, measurementView } from './registrationEvidence';

describe('Jacobian derivation evidence presentation', () => {
  it('hides an unbound nonconstant legacy summary and numeric zero', () => {
    const report = {
      metrics: { jacobianAnomalyRatio: 0 },
      jacobianSummary: { realJacobianEvidence: true, std: 0.005, evidence: 'sampled_volume' },
      metricAssessment: { jacobianAnomalyRatio: { usableAsMeasurement: false } },
      measurementMetrics: { jacobianAnomalyRatio: null },
    };
    expect(measurementView(report).jacobianAnomalyRatio).toBeNull();
    expect(measurementJacobianSummary(report)).toEqual({});
  });
  it('retains a bound zero and constant determinant without inferring fakery', () => {
    const summary = { realJacobianEvidence: false, std: 0, evidence: 'sampled_constant_volume' };
    const report = {
      jacobianSummary: summary,
      metricAssessment: { jacobianAnomalyRatio: { usableAsMeasurement: true } },
      measurementMetrics: { jacobianAnomalyRatio: 0 },
    };
    expect(measurementJacobianSummary(report)).toBe(summary);
    expect(measurementView(report).jacobianAnomalyRatio).toBe(0);
  });
  it('does not promote strings or missing metric values through a summary', () => {
    for (const value of [null, undefined, '0', NaN, Infinity]) {
      expect(measurementJacobianSummary({
        jacobianSummary: { std: 0.005 },
        metricAssessment: { jacobianAnomalyRatio: { usableAsMeasurement: true } },
        measurementMetrics: { jacobianAnomalyRatio: value },
      })).toEqual({});
    }
  });
  it('ignores malformed summary containers', () => {
    for (const summary of [null, [], 'legacy']) {
      expect(measurementJacobianSummary({
        jacobianSummary: summary,
        metricAssessment: { jacobianAnomalyRatio: { usableAsMeasurement: true } },
        measurementMetrics: { jacobianAnomalyRatio: 0 },
      })).toEqual({});
    }
  });
});
