import { describe, expect, test } from "vitest";
import { summarizeRun } from "../src/metrics/aggregate.js";
import type { CaseManifest, PerfRunResult } from "../src/types.js";

describe("summarizeRun", () => {
  test("aggregates suite and case timings", () => {
    const manifest: CaseManifest = {
      version: 1,
      generatedAt: "now",
      sourceProject: "x",
      cases: [{
        id: "case-a",
        label: "a",
        source: "repo",
        sourcePath: "a.json",
        targetKeys: ["CA1"],
        excludedTargetKeys: [],
        algorithm: "greedy"
      }],
    };
    const result: PerfRunResult = {
      startedAt: "now",
      mode: "baseline",
      backendUrl: "http://x",
      runs: 2,
      backend: [
        {
          caseId: "case-a",
          runIndex: 0,
          ok: true,
          backendTotalMs: 100,
          responseTimeMs: 90,
          generationMs: 40,
          scoringMs: 20,
          solverMs: 10,
          candidateCount: 12,
          candidateUniverseCount: 20,
          selectedCount: 1,
          uncoveredCount: 0,
          atlasBackedUncoveredCount: 0,
          fallbackUncoveredCount: 0,
          fallbackTargetKeys: [],
          requiredCoverageUnits: 1,
          coveredCoverageUnits: 1,
          algorithm: "greedy",
          requestedAlgorithm: "greedy",
          effectiveAlgorithm: "greedy_bitset",
          requestedDrccSolver: null,
          effectiveDrccSolver: null,
          nativeDrccApproximation: false,
          expectedSdfRiskClass: null,
          expectedSdfOutcome: null,
          observedSdfRiskClasses: [],
          nativeRejectReasonKeys: [],
          expectedShankCount: 4,
          effectiveShankCount: 4,
          selectedShankCounts: [4],
          selectedShankOffsetCounts: [4],
          taskId: "t1",
          stages: [],
          computeProfile: null,
          candidateStageBreakdown: null,
        },
        {
          caseId: "case-a",
          runIndex: 1,
          ok: true,
          backendTotalMs: 200,
          responseTimeMs: 190,
          generationMs: 80,
          scoringMs: 30,
          solverMs: 20,
          candidateCount: 14,
          candidateUniverseCount: 22,
          selectedCount: 1,
          uncoveredCount: 0,
          atlasBackedUncoveredCount: 0,
          fallbackUncoveredCount: 0,
          fallbackTargetKeys: [],
          requiredCoverageUnits: 1,
          coveredCoverageUnits: 1,
          algorithm: "greedy",
          requestedAlgorithm: "greedy",
          effectiveAlgorithm: "greedy_bitset",
          requestedDrccSolver: null,
          effectiveDrccSolver: null,
          nativeDrccApproximation: false,
          expectedSdfRiskClass: null,
          expectedSdfOutcome: null,
          observedSdfRiskClasses: [],
          nativeRejectReasonKeys: [],
          expectedShankCount: 4,
          effectiveShankCount: 4,
          selectedShankCounts: [4],
          selectedShankOffsetCounts: [4],
          taskId: "t2",
          stages: [],
          computeProfile: null,
          candidateStageBreakdown: null,
        },
      ],
      browser3d: [],
    };
    const summary = summarizeRun(result, manifest);
    expect(summary.suite.backendTotalMs.median).toBe(150);
    expect(summary.cases[0]?.candidateCountMedian).toBe(13);
    expect(summary.cases[0]?.effectiveShankCounts).toEqual([4]);
    expect(summary.cases[0]?.selectedShankOffsetCounts).toEqual([4]);
  });
});
