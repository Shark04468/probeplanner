import type { CaseManifest, CaseSummary, PerfRunResult, PerfSummary } from "../types.js";
import { median, numericSummary } from "../util/time.js";

function byCase<T extends { caseId: string }>(items: T[]): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const list = out.get(item.caseId) || [];
    list.push(item);
    out.set(item.caseId, list);
  }
  return out;
}

export function summarizeRun(result: PerfRunResult, manifest: CaseManifest): PerfSummary {
  const grouped = byCase(result.backend);
  const cases: CaseSummary[] = manifest.cases.map((item) => {
    const runs = grouped.get(item.id) || [];
    return {
      caseId: item.id,
      label: item.label,
      source: item.source,
      targetKeys: item.targetKeys,
      excludedTargetKeys: item.excludedTargetKeys ?? [],
      algorithm: item.algorithm,
      backendTotalMs: numericSummary(runs.map((run) => run.ok ? run.backendTotalMs : null)),
      generationMs: numericSummary(runs.map((run) => run.generationMs)),
      scoringMs: numericSummary(runs.map((run) => run.scoringMs)),
      solverMs: numericSummary(runs.map((run) => run.solverMs)),
      candidateCountMedian: median(runs.map((run) => run.candidateCount)),
      candidateUniverseCountMedian: median(runs.map((run) => run.candidateUniverseCount)),
      selectedCountMedian: median(runs.map((run) => run.selectedCount)),
      uncoveredCountMedian: median(runs.map((run) => run.uncoveredCount)),
      atlasBackedUncoveredCountMedian: median(runs.map((run) => run.atlasBackedUncoveredCount)),
      fallbackTargetKeys: Array.from(new Set(runs.flatMap((run) => run.fallbackTargetKeys))).sort(),
      requestedAlgorithms: Array.from(new Set(runs.map((run) => run.requestedAlgorithm).filter((value): value is string => Boolean(value)))).sort(),
      effectiveAlgorithms: Array.from(new Set(runs.map((run) => run.effectiveAlgorithm).filter((value): value is string => Boolean(value)))).sort(),
      requestedDrccSolvers: Array.from(new Set(runs.map((run) => run.requestedDrccSolver).filter((value): value is string => Boolean(value)))).sort(),
      effectiveDrccSolvers: Array.from(new Set(runs.map((run) => run.effectiveDrccSolver).filter((value): value is string => Boolean(value)))).sort(),
      nativeDrccApproximation: runs.some((run) => run.nativeDrccApproximation),
      expectedSdfRiskClasses: Array.from(new Set(runs.map((run) => run.expectedSdfRiskClass).filter((value): value is string => Boolean(value)))).sort(),
      expectedSdfOutcomes: Array.from(new Set(runs.map((run) => run.expectedSdfOutcome).filter((value): value is string => Boolean(value)))).sort(),
      observedSdfRiskClasses: Array.from(new Set(runs.flatMap((run) => run.observedSdfRiskClasses))).sort(),
      nativeRejectReasonKeys: Array.from(new Set(runs.flatMap((run) => run.nativeRejectReasonKeys))).sort(),
      expectedShankCounts: Array.from(new Set(runs.map((run) => run.expectedShankCount).filter((value): value is number => Number.isFinite(value)))).sort((a, b) => a - b),
      effectiveShankCounts: Array.from(new Set(runs.map((run) => run.effectiveShankCount).filter((value): value is number => Number.isFinite(value)))).sort((a, b) => a - b),
      selectedShankCounts: Array.from(new Set(runs.flatMap((run) => run.selectedShankCounts))).sort((a, b) => a - b),
      selectedShankOffsetCounts: Array.from(new Set(runs.flatMap((run) => run.selectedShankOffsetCounts))).sort((a, b) => a - b),
    };
  });

  return {
    generatedAt: new Date().toISOString(),
    mode: result.mode,
    backendUrl: result.backendUrl,
    frontendUrl: result.frontendUrl,
    runHealth: {
      backendRunCount: result.backend.length,
      backendFailedCount: result.backend.filter((run) => !run.ok).length,
      browser3dRunCount: result.browser3d.length,
      browser3dFailedCount: result.browser3d.filter((run) => !run.ok).length,
    },
    suite: {
      backendTotalMs: numericSummary(result.backend.map((run) => run.ok ? run.backendTotalMs : null)),
      generationMs: numericSummary(result.backend.map((run) => run.generationMs)),
      scoringMs: numericSummary(result.backend.map((run) => run.scoringMs)),
      solverMs: numericSummary(result.backend.map((run) => run.solverMs)),
    },
    cases,
    browser3d: {
      interactiveReadyMs: numericSummary(result.browser3d.map((run) => run.ok ? run.interactiveReadyMs : null)),
      totalLoadMs: numericSummary(result.browser3d.map((run) => run.ok ? run.totalLoadMs : null)),
      fpsMedian: numericSummary(result.browser3d.map((run) => run.ok ? run.fpsMedian : null)),
      frameMsP95: numericSummary(result.browser3d.map((run) => run.ok ? run.frameMsP95 : null)),
      unityResourceTotalBytes: numericSummary(result.browser3d.map((run) => run.ok ? run.unityResourceTotalBytes : null)),
      unityResourceMaxLoadMs: numericSummary(result.browser3d.map((run) => run.ok ? run.unityResourceMaxLoadMs : null)),
      jsHeapUsedBytes: numericSummary(result.browser3d.map((run) => run.ok ? run.jsHeapUsedBytes : null)),
      wasmHeapUsedBytes: numericSummary(result.browser3d.map((run) => run.ok ? run.wasmHeapUsedBytes : null)),
      webglDrawCallsPerSecond: numericSummary(result.browser3d.map((run) => run.ok ? run.webglDrawCallsPerSecond : null)),
      webglTrianglesPerSecond: numericSummary(result.browser3d.map((run) => run.ok ? run.webglTrianglesPerSecond : null)),
      webglTextureUploadBytes: numericSummary(result.browser3d.map((run) => run.ok ? run.webglTextureUploadBytes : null)),
    },
  };
}
