import { describe, expect, test } from "vitest";
import { evaluateGates } from "../src/metrics/gates.js";
import type { PerfSummary } from "../src/types.js";

function summary(totalMs: number, interactiveMs: number, fps: number, frameP95 = 20): PerfSummary {
  return {
    generatedAt: "now",
    mode: "gate",
    backendUrl: "http://x",
    runHealth: {
      backendRunCount: 1,
      backendFailedCount: 0,
      browser3dRunCount: 1,
      browser3dFailedCount: 0,
    },
    suite: {
      backendTotalMs: { count: 1, median: totalMs, p95: totalMs, min: totalMs, max: totalMs },
      generationMs: { count: 0, median: null, p95: null, min: null, max: null },
      scoringMs: { count: 1, median: 100, p95: 100, min: 100, max: 100 },
      solverMs: { count: 1, median: 10, p95: 10, min: 10, max: 10 },
    },
    cases: [{
      caseId: "case-a",
      label: "a",
      source: "repo",
      targetKeys: ["CA1"],
      excludedTargetKeys: [],
      algorithm: "greedy",
      backendTotalMs: { count: 1, median: totalMs, p95: totalMs, min: totalMs, max: totalMs },
      generationMs: { count: 0, median: null, p95: null, min: null, max: null },
      scoringMs: { count: 0, median: null, p95: null, min: null, max: null },
      solverMs: { count: 0, median: null, p95: null, min: null, max: null },
      candidateCountMedian: 10,
      candidateUniverseCountMedian: 20,
      selectedCountMedian: 1,
      uncoveredCountMedian: 0,
      atlasBackedUncoveredCountMedian: 0,
      fallbackTargetKeys: [],
      requestedAlgorithms: ["greedy"],
      effectiveAlgorithms: ["greedy_bitset"],
      requestedDrccSolvers: [],
      effectiveDrccSolvers: [],
      nativeDrccApproximation: false,
      expectedSdfRiskClasses: [],
      expectedSdfOutcomes: [],
      observedSdfRiskClasses: [],
      nativeRejectReasonKeys: [],
      expectedShankCounts: [],
      effectiveShankCounts: [],
      selectedShankCounts: [],
      selectedShankOffsetCounts: [],
    }],
    browser3d: {
      interactiveReadyMs: { count: 1, median: interactiveMs, p95: interactiveMs, min: interactiveMs, max: interactiveMs },
      totalLoadMs: { count: 1, median: interactiveMs + 5000, p95: interactiveMs + 5000, min: interactiveMs + 5000, max: interactiveMs + 5000 },
      fpsMedian: { count: 1, median: fps, p95: fps, min: fps, max: fps },
      frameMsP95: { count: 1, median: frameP95, p95: frameP95, min: frameP95, max: frameP95 },
      unityResourceTotalBytes: { count: 1, median: 64 * 1024 * 1024, p95: 64 * 1024 * 1024, min: 64 * 1024 * 1024, max: 64 * 1024 * 1024 },
      unityResourceMaxLoadMs: { count: 1, median: 5000, p95: 5000, min: 5000, max: 5000 },
      jsHeapUsedBytes: { count: 1, median: 128 * 1024 * 1024, p95: 128 * 1024 * 1024, min: 128 * 1024 * 1024, max: 128 * 1024 * 1024 },
      wasmHeapUsedBytes: { count: 1, median: 256 * 1024 * 1024, p95: 256 * 1024 * 1024, min: 256 * 1024 * 1024, max: 256 * 1024 * 1024 },
      webglDrawCallsPerSecond: { count: 1, median: 100, p95: 100, min: 100, max: 100 },
      webglTrianglesPerSecond: { count: 1, median: 1000, p95: 1000, min: 1000, max: 1000 },
      webglTextureUploadBytes: { count: 1, median: 8 * 1024 * 1024, p95: 8 * 1024 * 1024, min: 8 * 1024 * 1024, max: 8 * 1024 * 1024 },
    },
  };
}

describe("evaluateGates", () => {
  test("passes a strong improvement", () => {
    const report = evaluateGates(summary(600, 20_000, 55), summary(120_000, 40_000, 55));
    expect(report.passed).toBe(true);
  });

  test("fails insufficient backend gain", () => {
    const report = evaluateGates(summary(90_000, 20_000, 55), summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("median"))).toBe(true);
  });

  test("passes 3d refresh-rate relative fps drift when absolute fps is strong", () => {
    const report = evaluateGates(summary(600, 13_000, 120), summary(120_000, 10_000, 240));
    expect(report.passed).toBe(true);
    expect(report.checks.find((item) => item.name === "3d fps not worse")?.passed).toBe(true);
  });

  test("fails 3d refresh-rate relative fps drift without strong absolute margin", () => {
    const report = evaluateGates(summary(600, 13_000, 40), summary(120_000, 10_000, 240));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name === "3d fps not worse")).toBe(true);
  });

  test("fails 3d when any run frame p95 exceeds the absolute limit", () => {
    const current = summary(600, 20_000, 120);
    current.browser3d.frameMsP95 = { count: 3, median: 20, p95: 63, min: 17, max: 63 };
    const report = evaluateGates(current, summary(120_000, 40_000, 120));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name === "3d worst-run frame p95 absolute")).toBe(true);
  });

  test("fails drcc downgrade to greedy", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.algorithm = "drcc";
    current.cases[0]!.requestedAlgorithms = ["drcc"];
    current.cases[0]!.effectiveAlgorithms = ["greedy_bitset"];
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("drcc stays native"))).toBe(true);
  });

  test("checks sdf adversarial diagnostics", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedSdfRiskClasses = ["vessel"];
    current.cases[0]!.expectedSdfOutcomes = ["reject"];
    current.cases[0]!.nativeRejectReasonKeys = ["risk_vessel"];
    current.cases[0]!.selectedCountMedian = 0;
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(true);
  });

  test("skips relative backend improvement for sdf semantic gates", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedSdfRiskClasses = ["vessel"];
    current.cases[0]!.expectedSdfOutcomes = ["reject"];
    current.cases[0]!.nativeRejectReasonKeys = ["risk_vessel"];
    current.cases[0]!.selectedCountMedian = 0;
    const report = evaluateGates(current, summary(590, 20_000, 55));
    expect(report.passed).toBe(true);
    expect(report.checks.some((item) => item.name === "backend suite relative improvement skipped for sdf gate")).toBe(true);
  });

  test("fails sdf reject if a probe is still selected", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedSdfRiskClasses = ["vessel"];
    current.cases[0]!.expectedSdfOutcomes = ["reject"];
    current.cases[0]!.nativeRejectReasonKeys = ["risk_vessel"];
    current.cases[0]!.selectedCountMedian = 1;
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("sdf vessel rejects"))).toBe(true);
  });

  test("checks sdf pass control diagnostics", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedSdfRiskClasses = ["vessel"];
    current.cases[0]!.expectedSdfOutcomes = ["pass"];
    current.cases[0]!.nativeRejectReasonKeys = [];
    current.cases[0]!.observedSdfRiskClasses = ["vessel"];
    current.cases[0]!.selectedCountMedian = 1;
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(true);
  });

  test("fails runtime fallback targets", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.fallbackTargetKeys = ["PFC"];
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("no runtime fallback targets"))).toBe(true);
  });

  test("fails drcc solver mismatch", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.algorithm = "drcc";
    current.cases[0]!.requestedAlgorithms = ["drcc"];
    current.cases[0]!.effectiveAlgorithms = ["drcc_exact_pool_bitset"];
    current.cases[0]!.requestedDrccSolvers = ["hybrid"];
    current.cases[0]!.effectiveDrccSolvers = ["exact_pool"];
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("drcc solver honored"))).toBe(true);
  });

  test("fails native misocp approximation claims", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.algorithm = "drcc";
    current.cases[0]!.requestedAlgorithms = ["drcc"];
    current.cases[0]!.effectiveAlgorithms = ["drcc_misocp_approx_exact_pool_bitset"];
    current.cases[0]!.requestedDrccSolvers = ["misocp"];
    current.cases[0]!.effectiveDrccSolvers = ["exact_pool"];
    current.cases[0]!.nativeDrccApproximation = true;
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("no native misocp claim"))).toBe(true);
  });

  test("fails multi-shank payload downgrade", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedShankCounts = [4];
    current.cases[0]!.effectiveShankCounts = [4];
    current.cases[0]!.selectedShankCounts = [1];
    current.cases[0]!.selectedShankOffsetCounts = [1];
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(false);
    expect(report.checks.some((item) => !item.passed && item.name.includes("multi-shank selected payload"))).toBe(true);
  });

  test("passes multi-shank runtime and selected payload", () => {
    const current = summary(600, 20_000, 55);
    current.cases[0]!.expectedShankCounts = [4];
    current.cases[0]!.effectiveShankCounts = [4];
    current.cases[0]!.selectedShankCounts = [4];
    current.cases[0]!.selectedShankOffsetCounts = [4];
    const report = evaluateGates(current, summary(120_000, 40_000, 55));
    expect(report.passed).toBe(true);
  });
});
