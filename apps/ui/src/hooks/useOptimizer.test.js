import { act, renderHook, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { useOptimizer } from './useOptimizer';
import { flaskFetch } from './useBackend';

vi.mock('./useBackend', async (importOriginal) => ({
  ...(await importOriginal()),
  flaskFetch: vi.fn(),
}));

const CONSTRAINTS = {
  maxAngle: 'enforce',
  minEntrySpacing: 'enforce',
  minClearance: 'enforce',
  lambdaBoundary: 'enforce',
  brainBounds: 'enforce',
  continuousRiskDistance: 'enforce',
  hemisphere: 'warn',
};

beforeEach(() => {
  vi.clearAllMocks();
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
  expect(payload.constraintModes.continuousRiskDistance).toBe('enforce');
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

test('useOptimizer sends strict endpoint and stability controls and preserves unified probe audit', async () => {
  flaskFetch.mockResolvedValue({
    ok: true,
    selected: [
      {
        ap_mm: 0.4,
        ml_mm: 1.1,
        entryDvCorr_mm: 0.44,
        phi_deg: 2,
        theta_deg: 5,
        depthUm: 4125,
        entry_xyz: [5000, 440, 6800],
        tip_xyz: [5200, 4500, 6900],
        dir: [0.05, 0.99, 0.02],
        coveredRegions: ['CA1'],
        brainContainmentPassed: true,
        tipInTarget: true,
        distalOvershootMm: 0,
        internalRiskViolationCount: 0,
        minimumInternalRiskMarginMm: null,
        entrySurfaceCrossingStatus: 'allowed_entry_surface_semantics',
        internalRiskAudit: { claimStatus: 'unavailable', unavailableClasses: ['vessel'] },
      },
    ],
    uncovered: [],
    safetyIssues: [],
    candidateFunnelDiagnostics: { generatedCandidates: 120, afterBrainBounds: 86 },
  });

  const { result } = renderHook(() => useOptimizer());
  const stabilityReferenceTrajectories = [
    { entryCcfUm: [5000, 440, 6800], tipCcfUm: [5200, 4500, 6900] },
  ];

  await act(async () => {
    result.current.run({
      selectedRegions: new Set(['CA1']),
      params: {
        algorithm: 'greedy',
        maxAngle: 15,
        targetEndpointPolicy: 'tip_in_target',
        depthOptimizationEnabled: true,
        stabilityTieBreakerEnabled: true,
        stabilityReferenceWeight: 0.1,
        stabilityReferenceTrajectories,
      },
      probeParams: { depthUm: 7600, entrySpacingUm: 3500 },
      probePreset: 'NP2.0-4sh',
      constraintModes: CONSTRAINTS,
      backendStatus: 'online',
      correction: null,
      correctionUncertainty: null,
    });
  });

  await waitFor(() => expect(result.current.results?.probes?.length).toBe(1));
  const payload = flaskFetch.mock.calls[0][2];
  expect(payload.params.targetEndpointPolicy).toBe('tip_in_target');
  expect(payload.params.depthOptimizationEnabled).toBe(true);
  expect(payload.params.stabilityReferenceWeight).toBe(0.1);
  expect(payload.params.stabilityReferenceTrajectories).toEqual(stabilityReferenceTrajectories);
  expect(result.current.results.requestedTargets).toEqual(['CA1']);
  expect(result.current.results.probes[0]).toMatchObject({
    brainContainmentPassed: true,
    tipInTarget: true,
    distalOvershootMm: 0,
    entrySurfaceCrossingStatus: 'allowed_entry_surface_semantics',
  });
  expect(result.current.results.probes[0].internalRiskAudit.claimStatus).toBe('unavailable');
  expect(result.current.results.candidateFunnelDiagnostics.afterBrainBounds).toBe(86);
  expect(result.current.results.stabilitySummary.referenceProbeCount).toBe(1);
});
