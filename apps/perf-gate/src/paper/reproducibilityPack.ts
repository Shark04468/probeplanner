import { existsSync } from "node:fs";
import path from "node:path";
import type { PerfSummary } from "../types.js";
import { flagString, fmt, parseFlags, readJson, repoRoot, writeJson, writeText } from "./common.js";

interface StabilityReport {
  runCount: number;
  backendRunCount: number;
  backendFailedCount: number;
  metrics?: {
    backendTotalMs?: {
      medianOfRunP50: number | null;
      iqrOfRunP50: [number | null, number | null];
      medianOfRunP95: number | null;
      iqrOfRunP95: [number | null, number | null];
      bootstrapP50Ci95: [number | null, number | null];
      bootstrapP95Ci95: [number | null, number | null];
    };
    scoringMs?: {
      medianOfRunP95: number | null;
      iqrOfRunP95: [number | null, number | null];
    };
    solverMs?: {
      medianOfRunP95: number | null;
      iqrOfRunP95: [number | null, number | null];
    };
  };
}

interface SupportedTargetSet {
  statusCounts: Record<string, number>;
  rows: Array<{
    target: string;
    status: string;
    robustnessStatus: string;
    main?: { appearances: number; fullCoverageRate: number | null; partialOrUncertain: number; failedCaseCount: number };
  }>;
}

interface PhantomAccuracy {
  phantom: { targetCount: number; candidateCount: number; obstacleCount: number; clearanceMm: number };
  metrics: {
    coverageRecall: number;
    selectedBoundaryViolationRate: number;
    selectedObstacleViolationRate: number;
    targetDistanceMedianMm: number | null;
    targetDistanceP95Mm: number | null;
    targetDistanceMaxMm: number | null;
    minBoundaryMarginMm: number | null;
    minObstacleMarginMm: number | null;
  };
}

interface PhantomOptimalProbeCount {
  phantom: { targetCount: number; candidateCount: number; validCoveringCandidateCount: number };
  groundTruth: {
    optimalProbeCount: number;
    groupLowerBoundProbeCount: number;
    threeProbeMaxCoveredTargets: number;
    threeProbeFullCoveragePossible: boolean;
    fiveProbeExactFullCoveragePossible: boolean;
  };
  metrics: {
    selectedProbeCount: number;
    optimalProbeCountRecovery: number;
    targetCoverageRecall: number;
    overSelectionRate: number;
    underSelectionRate: number;
    selectedBoundaryViolationRate: number;
    selectedObstacleViolationRate: number;
  };
}

async function optionalSummary(root: string, relPath: string): Promise<PerfSummary | null> {
  const full = path.resolve(root, relPath, "summary.json");
  return existsSync(full) ? await readJson<PerfSummary>(full) : null;
}

async function optionalJson<T>(root: string, relPath: string): Promise<T | null> {
  const full = path.resolve(root, relPath);
  return existsSync(full) ? await readJson<T>(full) : null;
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-reproducibility-pack")!);
  const reportDirs = {
    legacyReplay110: "reports/paper-legacy-replay110",
    nativeReplay110: "reports/paper-native-replay110",
    nativeReplay110Stability: "reports/paper-native-replay110-stability",
    comparison110: "reports/paper-legacy-native-comparison110",
    failureAnalysis: "reports/paper-failure-analysis",
    trajectoryAudit: "reports/paper-trajectory-audit",
    supportedTargetSet: "reports/paper-supported-target-set",
    phantomAccuracy: "reports/paper-phantom-accuracy",
    phantomOptimalProbeCount: "reports/paper-phantom-optimal-probe-count",
    sdfBoundary100: "reports/paper-sdf-boundary100",
    stress500: "reports/paper-stress500",
    ablation: "reports/paper-ablation",
    browser3d: "reports/paper-browser3d",
  };
  const files = [
    "README.md",
    "LICENSE",
    "package.json",
    "Cargo.toml",
    "planner-engine/Cargo.toml",
    "contracts/optimize-request.schema.json",
    "docs/artifact-packaging.md",
    "cases/manifest.json",
    "docs/evidence/2026-07-07-full-paper-experiments.md",
    "docs/evidence/2026-07-07-full-paper-experiments.json",
    "docs/evidence/2026-07-07-paper-supplemental-experiments.md",
    "docs/evidence/2026-07-07-paper-supplemental-experiments.json",
    "docs/evidence/2026-07-08-frontiers-readiness-experiments.md",
    "docs/evidence/2026-07-08-frontiers-readiness-experiments.json",
    "docs/release/frontiers-technology-code-release.md",
    "docs/release/limitations.md",
  ];
  const summaries = Object.fromEntries(await Promise.all(
    Object.entries(reportDirs).map(async ([key, rel]) => [key, await optionalSummary(root, rel)]),
  )) as Record<string, PerfSummary | null>;
  const stability = await optionalJson<StabilityReport>(root, "reports/paper-native-replay110-stability/stability.json");
  const supportedTargets = await optionalJson<SupportedTargetSet>(root, "reports/paper-supported-target-set/supported-target-set.json");
  const phantom = await optionalJson<PhantomAccuracy>(root, "reports/paper-phantom-accuracy/phantom-accuracy.json");
  const phantomOptimal = await optionalJson<PhantomOptimalProbeCount>(root, "reports/paper-phantom-optimal-probe-count/known-optimal-probe-count.json");
  const missingFiles = files.filter((file) => !existsSync(path.resolve(root, file)));
  const missingReports = Object.entries(reportDirs).filter(([, rel]) => !existsSync(path.resolve(root, rel))).map(([key]) => key);
  const supportedRows = supportedTargets?.rows ?? [];
  const boundaryTargets = supportedRows.filter((row) => row.status === "boundary").map((row) => row.target).sort();
  const supportedMainTargets = supportedRows.filter((row) => row.status === "supported").map((row) => row.target).sort();
  const partialMainTargets = supportedRows.filter((row) => row.status === "partial").map((row) => row.target).sort();
  const pack = {
    generatedAt: new Date().toISOString(),
    repository: "https://github.com/Shark04468/probeplanner.git",
    license: existsSync(path.resolve(root, "LICENSE")) ? "MIT" : "missing LICENSE file",
    runtime: {
      node: process.version,
      platform: process.platform,
      arch: process.arch,
    },
    requiredFiles: files.map((file) => ({
      path: file,
      exists: existsSync(path.resolve(root, file)),
    })),
    reportDirs: Object.fromEntries(Object.entries(reportDirs).map(([key, rel]) => [key, {
      path: rel,
      exists: existsSync(path.resolve(root, rel)),
    }])),
    missingFiles,
    missingReports,
    experimentSummaries: Object.fromEntries(Object.entries(summaries).map(([key, summary]) => [key, summary ? {
      runHealth: summary.runHealth,
      backendMedianMs: summary.suite?.backendTotalMs?.median ?? null,
      backendP95Ms: summary.suite?.backendTotalMs?.p95 ?? null,
      scoringP95Ms: summary.suite?.scoringMs?.p95 ?? null,
      solverP95Ms: summary.suite?.solverMs?.p95 ?? null,
      browserFpsMedian: summary.browser3d?.fpsMedian?.median ?? null,
      browserFrameP95MedianMs: summary.browser3d?.frameMsP95?.median ?? null,
    } : null])),
    paperEvidenceHighlights: {
      nativeReplay110Stability: stability ? {
        runCount: stability.runCount,
        backendRunCount: stability.backendRunCount,
        backendFailedCount: stability.backendFailedCount,
        backendTotalMs: stability.metrics?.backendTotalMs ?? null,
        scoringP95MedianMs: stability.metrics?.scoringMs?.medianOfRunP95 ?? null,
        solverP95MedianMs: stability.metrics?.solverMs?.medianOfRunP95 ?? null,
      } : null,
      supportedTargetSet: supportedTargets ? {
        statusCounts: supportedTargets.statusCounts,
        supportedMainTargets,
        partialMainTargets,
        boundaryTargets,
      } : null,
      phantomAccuracy: phantom ? {
        targetCount: phantom.phantom.targetCount,
        candidateCount: phantom.phantom.candidateCount,
        obstacleCount: phantom.phantom.obstacleCount,
        coverageRecall: phantom.metrics.coverageRecall,
        selectedBoundaryViolationRate: phantom.metrics.selectedBoundaryViolationRate,
        selectedObstacleViolationRate: phantom.metrics.selectedObstacleViolationRate,
        targetDistanceMedianMm: phantom.metrics.targetDistanceMedianMm,
        targetDistanceP95Mm: phantom.metrics.targetDistanceP95Mm,
        targetDistanceMaxMm: phantom.metrics.targetDistanceMaxMm,
        minBoundaryMarginMm: phantom.metrics.minBoundaryMarginMm,
        minObstacleMarginMm: phantom.metrics.minObstacleMarginMm,
      } : null,
      phantomOptimalProbeCount: phantomOptimal ? {
        targetCount: phantomOptimal.phantom.targetCount,
        candidateCount: phantomOptimal.phantom.candidateCount,
        validCoveringCandidateCount: phantomOptimal.phantom.validCoveringCandidateCount,
        groundTruthOptimalProbeCount: phantomOptimal.groundTruth.optimalProbeCount,
        selectedProbeCount: phantomOptimal.metrics.selectedProbeCount,
        optimalProbeCountRecovery: phantomOptimal.metrics.optimalProbeCountRecovery,
        targetCoverageRecall: phantomOptimal.metrics.targetCoverageRecall,
        overSelectionRate: phantomOptimal.metrics.overSelectionRate,
        underSelectionRate: phantomOptimal.metrics.underSelectionRate,
        selectedBoundaryViolationRate: phantomOptimal.metrics.selectedBoundaryViolationRate,
        selectedObstacleViolationRate: phantomOptimal.metrics.selectedObstacleViolationRate,
        threeProbeMaxCoveredTargets: phantomOptimal.groundTruth.threeProbeMaxCoveredTargets,
        threeProbeFullCoveragePossible: phantomOptimal.groundTruth.threeProbeFullCoveragePossible,
        fiveProbeExactFullCoveragePossible: phantomOptimal.groundTruth.fiveProbeExactFullCoveragePossible,
      } : null,
    },
    reproductionCommands: [
      "npm install",
      "npm --prefix apps/ui install",
      "npm run prepare:cases -- --artifact-root <ARTIFACT_ROOT>",
      "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_dev_stack.ps1 -ArtifactRoot <ARTIFACT_ROOT> -UseEngineService -UiPort 3100",
      "npm run perf:paper:replay110 -- --backend-url http://127.0.0.1:5000 --out reports/paper-native-replay110",
      "npm run perf:paper:replay110 -- --backend-url http://127.0.0.1:5000 --runs 3 --out reports/paper-native-replay110-3run",
      "npm run perf:paper:stability -- --results reports/paper-native-replay110-3run/run-results.jsonl --out reports/paper-native-replay110-stability",
      "npm run perf:paper:compare -- --legacy reports/paper-legacy-replay110/summary.json --native reports/paper-native-replay110/summary.json --out reports/paper-legacy-native-comparison110",
      "npm run perf:paper:sdf -- --backend-url http://127.0.0.1:5000",
      "npm run perf:paper:failure -- --backend-url http://127.0.0.1:5000",
      "npm run perf:paper:trajectory -- --backend-url http://127.0.0.1:5000",
      "npm run perf:paper:targets",
      "npm run perf:paper:phantom",
      "npm run perf:paper:phantom-optimal-count",
      "npm run perf:gate:3d -- --baseline reports/browser3d-profile-current-dpr1-v3/summary.json --out reports/paper-browser3d --frontend-url http://127.0.0.1:3100 --backend-url http://127.0.0.1:5000 --runs 0",
      "npm run mri:qc",
    ],
    manuscriptRestrictions: [
      "No animal or histology validation is included.",
      "Native DRCC is not full MISOCP semantic equivalence.",
      "Vessel and sulcus structured masks are not complete in the current artifact.",
      "MOs and ACB single-target planning remain candidate-generation boundary cases unless later fixed.",
    ],
  };

  const lines = [
    "# Reproducibility Pack Checklist",
    "",
    `Generated: ${pack.generatedAt}`,
    `Repository: ${pack.repository}`,
    `License: ${pack.license}`,
    "",
    "## Required Files",
    "",
    "| Path | Exists |",
    "| --- | --- |",
    ...pack.requiredFiles.map((item) => `| ${item.path} | ${item.exists} |`),
    "",
    "## Reports",
    "",
    "| Report | Path | Exists |",
    "| --- | --- | --- |",
    ...Object.entries(pack.reportDirs).map(([key, item]) => `| ${key} | ${item.path} | ${item.exists} |`),
    "",
    "## Paper Evidence Highlights",
    "",
    stability
      ? `- Native replay110 stability: ${stability.runCount} runs, ${stability.backendFailedCount}/${stability.backendRunCount} backend failures, backend p50 median ${fmt(stability.metrics?.backendTotalMs?.medianOfRunP50)} ms, backend p95 median ${fmt(stability.metrics?.backendTotalMs?.medianOfRunP95)} ms.`
      : "- Native replay110 stability: missing.",
    supportedTargets
      ? `- Supported target set: ${JSON.stringify(supportedTargets.statusCounts)}; supported main targets: ${supportedMainTargets.join(", ") || "none"}; boundary targets: ${boundaryTargets.join(", ") || "none"}.`
      : "- Supported target set: missing.",
    phantom
      ? `- Digital phantom: ${phantom.phantom.targetCount} targets, ${phantom.phantom.candidateCount} candidates, coverage recall ${fmt(phantom.metrics.coverageRecall * 100, 1)}%, boundary violation ${fmt(phantom.metrics.selectedBoundaryViolationRate * 100, 1)}%, obstacle violation ${fmt(phantom.metrics.selectedObstacleViolationRate * 100, 1)}%, target-distance p95 ${fmt(phantom.metrics.targetDistanceP95Mm)} mm.`
      : "- Digital phantom: missing.",
    phantomOptimal
      ? `- Known-optimal phantom: ground-truth ${phantomOptimal.groundTruth.optimalProbeCount} probes, selected ${phantomOptimal.metrics.selectedProbeCount}, recovery ${phantomOptimal.metrics.optimalProbeCountRecovery}, recall ${fmt(phantomOptimal.metrics.targetCoverageRecall * 100, 1)}%, 3-probe max coverage ${phantomOptimal.groundTruth.threeProbeMaxCoveredTargets}/${phantomOptimal.phantom.targetCount}, exactly-5 feasible ${phantomOptimal.groundTruth.fiveProbeExactFullCoveragePossible}.`
      : "- Known-optimal phantom: missing.",
    "",
    "## Reproduction Commands",
    "",
    ...pack.reproductionCommands.map((command) => `- \`${command}\``),
    "",
    "## Restrictions",
    "",
    ...pack.manuscriptRestrictions.map((item) => `- ${item}`),
    "",
  ];

  await writeJson(path.join(outDir, "reproducibility-pack.json"), pack);
  await writeText(path.join(outDir, "reproducibility-pack.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote reproducibility pack to ${path.join(outDir, "reproducibility-pack.md")}`);
  if (missingFiles.length > 0 || missingReports.length > 0) process.exitCode = 2;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
