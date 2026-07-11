export type JsonObject = Record<string, unknown>;

export interface ReplayCase {
  id: string;
  label: string;
  source: "repo" | "local" | "adversarial" | "paper_replay" | "stress" | "ablation";
  sourcePath: string;
  targetKeys: string[];
  excludedTargetKeys?: string[];
  algorithm: string;
  request: JsonObject;
}

export type ReplayCaseMeta = Omit<ReplayCase, "request">;

export interface CaseManifest {
  version: 1;
  generatedAt: string;
  sourceProject: string;
  cases: ReplayCaseMeta[];
}

export type LoadedCaseManifest = Omit<CaseManifest, "cases"> & { cases: ReplayCase[] };

export interface StageObservation {
  stage: string;
  progress: number | null;
  message: string;
  status: string;
  seenAtMs: number;
}

export interface BackendRunMetrics {
  caseId: string;
  runIndex: number;
  ok: boolean;
  error?: string;
  backendTotalMs: number;
  responseTimeMs: number | null;
  generationMs: number | null;
  scoringMs: number | null;
  solverMs: number | null;
  candidateCount: number | null;
  candidateUniverseCount: number | null;
  selectedCount: number;
  uncoveredCount: number;
  atlasBackedUncoveredCount: number;
  fallbackUncoveredCount: number;
  fallbackTargetKeys: string[];
  requiredCoverageUnits: number | null;
  coveredCoverageUnits: number | null;
  algorithm: string | null;
  requestedAlgorithm: string | null;
  effectiveAlgorithm: string | null;
  requestedDrccSolver: string | null;
  effectiveDrccSolver: string | null;
  nativeDrccApproximation: boolean;
  expectedSdfRiskClass: string | null;
  expectedSdfOutcome: string | null;
  observedSdfRiskClasses: string[];
  nativeRejectReasonKeys: string[];
  expectedShankCount: number | null;
  effectiveShankCount: number | null;
  selectedShankCounts: number[];
  selectedShankOffsetCounts: number[];
  taskId: string;
  stages: StageObservation[];
  computeProfile: unknown;
  candidateStageBreakdown: unknown;
}

export interface Browser3dMetrics {
  ok: boolean;
  error?: string;
  frontendUrl: string;
  runIndex: number;
  totalLoadMs: number | null;
  unityInstanceReadyMs: number | null;
  bridgeReadyMs: number | null;
  interactiveReadyMs: number | null;
  allOutlinesLoadedMs: number | null;
  fpsMedian: number | null;
  fpsP05: number | null;
  frameMsP95: number | null;
  bridgeLargePayloads: number;
  bridgeMaxPayloadBytes: number;
  unityResources: Browser3dResourceMetric[];
  unityResourceTotalBytes: number | null;
  unityResourceMaxLoadMs: number | null;
  jsHeapUsedBytes: number | null;
  jsHeapTotalBytes: number | null;
  wasmHeapUsedBytes: number | null;
  wasmHeapTotalBytes: number | null;
  canvasCssPixels: number | null;
  canvasDevicePixels: number | null;
  devicePixelRatio: number | null;
  webglRenderer: string | null;
  webglVendor: string | null;
  webglDrawCallsPerSecond: number | null;
  webglTrianglesPerSecond: number | null;
  webglTextureUploadBytes: number | null;
  webglTextureUploads: number | null;
  tracePath?: string;
  events: Array<{ event: string; atMs: number; data?: unknown }>;
}

export interface Browser3dResourceMetric {
  kind: "loader" | "framework" | "wasm" | "data" | "other";
  url: string;
  durationMs: number | null;
  responseEndMs: number | null;
  transferSizeBytes: number | null;
  encodedBodySizeBytes: number | null;
  decodedBodySizeBytes: number | null;
  contentLengthBytes: number | null;
  status: number | null;
  contentType: string | null;
}

export interface PerfRunResult {
  startedAt: string;
  mode: "baseline" | "gate";
  backendUrl: string;
  frontendUrl?: string;
  runs: number;
  backend: BackendRunMetrics[];
  browser3d: Browser3dMetrics[];
}

export interface NumericSummary {
  count: number;
  median: number | null;
  p95: number | null;
  min: number | null;
  max: number | null;
}

export interface CaseSummary {
  caseId: string;
  label: string;
  source: string;
  targetKeys: string[];
  excludedTargetKeys?: string[];
  algorithm: string;
  backendTotalMs: NumericSummary;
  generationMs: NumericSummary;
  scoringMs: NumericSummary;
  solverMs: NumericSummary;
  candidateCountMedian: number | null;
  candidateUniverseCountMedian: number | null;
  selectedCountMedian: number | null;
  uncoveredCountMedian: number | null;
  atlasBackedUncoveredCountMedian: number | null;
  fallbackTargetKeys: string[];
  requestedAlgorithms: string[];
  effectiveAlgorithms: string[];
  requestedDrccSolvers: string[];
  effectiveDrccSolvers: string[];
  nativeDrccApproximation: boolean;
  expectedSdfRiskClasses: string[];
  expectedSdfOutcomes: string[];
  observedSdfRiskClasses: string[];
  nativeRejectReasonKeys: string[];
  expectedShankCounts: number[];
  effectiveShankCounts: number[];
  selectedShankCounts: number[];
  selectedShankOffsetCounts: number[];
}

export interface PerfSummary {
  generatedAt: string;
  mode: "baseline" | "gate";
  backendUrl: string;
  frontendUrl?: string;
  runHealth: {
    backendRunCount: number;
    backendFailedCount: number;
    browser3dRunCount: number;
    browser3dFailedCount: number;
  };
  suite: {
    backendTotalMs: NumericSummary;
    generationMs: NumericSummary;
    scoringMs: NumericSummary;
    solverMs: NumericSummary;
  };
  cases: CaseSummary[];
  browser3d: {
    interactiveReadyMs: NumericSummary;
    totalLoadMs: NumericSummary;
    fpsMedian: NumericSummary;
    frameMsP95: NumericSummary;
    unityResourceTotalBytes: NumericSummary;
    unityResourceMaxLoadMs: NumericSummary;
    jsHeapUsedBytes: NumericSummary;
    wasmHeapUsedBytes: NumericSummary;
    webglDrawCallsPerSecond: NumericSummary;
    webglTrianglesPerSecond: NumericSummary;
    webglTextureUploadBytes: NumericSummary;
  };
  gates?: GateReport;
}

export interface GateCheck {
  name: string;
  passed: boolean;
  expected: string;
  actual: string;
}

export interface GateReport {
  passed: boolean;
  checks: GateCheck[];
}
