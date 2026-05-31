import { act, renderHook, waitFor } from '@testing-library/react';
import { useOptimizer } from './useOptimizer';
import { flaskFetch } from './useBackend';

jest.mock('./useBackend', () => ({
  ...jest.requireActual('./useBackend'),
  flaskFetch: jest.fn(),
}));

const CONSTRAINTS = {
  maxAngle: 'enforce',
  minEntrySpacing: 'enforce',
  minClearance: 'enforce',
  lambdaBoundary: 'enforce',
  brainBounds: 'enforce',
  hemisphere: 'warn',
};

beforeEach(() => {
  jest.clearAllMocks();
});

test('useOptimizer preserves request/response hashes in reproducibility report', async () => {
  const requestHash = 'a'.repeat(64);
  const responseHash = 'b'.repeat(64);
  const deterministicResponseHash = 'c'.repeat(64);
  flaskFetch.mockResolvedValue({
    ok: true,
    selected: [
      {
        ap_mm: 1.2,
        ml_mm: -0.6,
        entryDvCorr_mm: 0.0,
        phi_deg: 10,
        theta_deg: 12,
        depthUm: 7000,
        coveredRegions: ['VISp'],
        overlapVoxels: { VISp: 10 },
      },
    ],
    uncovered: [],
    safetyIssues: [],
    requestId: 'req-test-1',
    engineVersion: 'engine-test',
    atlasHash: 'atlas-hash-test',
    requestHash,
    responseHash,
    deterministicResponseHash,
    targetingMode: 'single_hemisphere',
    clinicalContract: { species: 'mouse', intendedUse: 'automatic_decision' },
    clinicalContext: { studyArm: 'silent' },
    primaryEndpointSummary: { coverageRate: 1, planningTimeMs: 120 },
  });

  const { result } = renderHook(() => useOptimizer());

  await act(async () => {
    result.current.run({
      selectedRegions: new Set(['VISp']),
      params: { algorithm: 'greedy', maxAngle: 15 },
      probeParams: { depthUm: 10000, entrySpacingUm: 3500 },
      probePreset: 'NP2.0-4sh',
      constraintModes: CONSTRAINTS,
      backendStatus: 'online',
      correction: null,
      correctionUncertainty: null,
    });
  });

  await waitFor(() => expect(result.current.results).not.toBeNull());
  expect(result.current.results.requestHash).toBe(requestHash);
  expect(result.current.results.responseHash).toBe(responseHash);
  expect(result.current.results.deterministicResponseHash).toBe(deterministicResponseHash);
  expect(result.current.results.reproducibility.requestHash).toBe(requestHash);
  expect(result.current.results.reproducibility.responseHash).toBe(responseHash);
  expect(result.current.results.reproducibility.deterministicResponseHash).toBe(deterministicResponseHash);
  expect(result.current.results.clinicalContract.species).toBe('mouse');
  expect(result.current.results.clinicalContext.studyArm).toBe('silent');
  expect(result.current.results.primaryEndpointSummary.coverageRate).toBe(1);
});

test('useOptimizer canonicalizes side-suffixed target keys before request', async () => {
  flaskFetch.mockResolvedValue({
    ok: true,
    selected: [],
    uncovered: [],
    safetyIssues: [],
  });

  const { result } = renderHook(() => useOptimizer());

  await act(async () => {
    result.current.run({
      selectedRegions: new Set(['CA1 R']),
      params: { algorithm: 'greedy', maxAngle: 15 },
      probeParams: { depthUm: 10000, entrySpacingUm: 3500 },
      probePreset: 'NP2.0-4sh',
      constraintModes: CONSTRAINTS,
      backendStatus: 'online',
      backendRegions: ['CA1', 'SSp'],
      correction: null,
      correctionUncertainty: null,
    });
  });

  await waitFor(() => expect(flaskFetch).toHaveBeenCalledTimes(1));
  const payload = flaskFetch.mock.calls[0][2];
  expect(payload.targetKeys).toEqual(['CA1']);
});

test('useOptimizer does not pre-block unknown target and lets backend auto-provision attempt', async () => {
  flaskFetch.mockResolvedValue({
    ok: true,
    selected: [],
    uncovered: ['GPi'],
    safetyIssues: [],
  });

  const { result } = renderHook(() => useOptimizer());

  await act(async () => {
    result.current.run({
      selectedRegions: new Set(['GPi']),
      params: { algorithm: 'greedy', maxAngle: 15 },
      probeParams: { depthUm: 10000, entrySpacingUm: 3500 },
      probePreset: 'NP2.0-4sh',
      constraintModes: CONSTRAINTS,
      backendStatus: 'online',
      backendRegions: ['CA1', 'SSp'],
      correction: null,
      correctionUncertainty: null,
    });
  });

  await waitFor(() => expect(flaskFetch).toHaveBeenCalledTimes(1));
  const payload = flaskFetch.mock.calls[0][2];
  expect(payload.targetKeys).toEqual(['GPi']);
  expect(result.current.lastError).toBe('');
});
