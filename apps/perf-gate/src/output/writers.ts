import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import type { PerfRunResult, PerfSummary } from "../types.js";
import { formatMs } from "../util/time.js";

export async function readSummary(filePath: string): Promise<PerfSummary> {
  return JSON.parse(await readFile(filePath, "utf8")) as PerfSummary;
}

function csvCell(value: unknown): string {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function formatBytes(value: number | null): string {
  if (!Number.isFinite(value)) return "n/a";
  const bytes = Number(value);
  if (bytes >= 1024 * 1024) return `${Math.round((bytes / 1024 / 1024) * 10) / 10} MiB`;
  if (bytes >= 1024) return `${Math.round((bytes / 1024) * 10) / 10} KiB`;
  return `${bytes} B`;
}

function summaryCsv(summary: PerfSummary): string {
  const rows = [
    [
      "caseId",
      "label",
      "targets",
      "excludedTargets",
      "algorithm",
      "requestedAlgorithms",
      "effectiveAlgorithms",
      "requestedDrccSolvers",
      "effectiveDrccSolvers",
      "nativeDrccApproximation",
      "candidateCountMedian",
      "candidateUniverseCountMedian",
      "backendTotalMedianMs",
      "backendTotalP95Ms",
      "generationMedianMs",
      "scoringMedianMs",
      "solverMedianMs",
      "selectedMedian",
      "uncoveredMedian",
      "atlasBackedUncoveredMedian",
      "fallbackTargetsExcluded",
      "expectedSdfRiskClasses",
      "expectedSdfOutcomes",
      "observedSdfRiskClasses",
      "nativeRejectReasonKeys",
      "expectedShankCounts",
      "effectiveShankCounts",
      "selectedShankCounts",
      "selectedShankOffsetCounts",
    ],
    ...summary.cases.map((item) => [
      item.caseId,
      item.label,
      item.targetKeys.join("+"),
      (item.excludedTargetKeys ?? []).join("+"),
      item.algorithm,
      item.requestedAlgorithms.join("+"),
      item.effectiveAlgorithms.join("+"),
      item.requestedDrccSolvers.join("+"),
      item.effectiveDrccSolvers.join("+"),
      item.nativeDrccApproximation,
      item.candidateCountMedian,
      item.candidateUniverseCountMedian,
      item.backendTotalMs.median,
      item.backendTotalMs.p95,
      item.generationMs.median,
      item.scoringMs.median,
      item.solverMs.median,
      item.selectedCountMedian,
      item.uncoveredCountMedian,
      item.atlasBackedUncoveredCountMedian,
      item.fallbackTargetKeys.join("+"),
      item.expectedSdfRiskClasses.join("+"),
      item.expectedSdfOutcomes.join("+"),
      item.observedSdfRiskClasses.join("+"),
      item.nativeRejectReasonKeys.join("+"),
      item.expectedShankCounts.join("+"),
      item.effectiveShankCounts.join("+"),
      item.selectedShankCounts.join("+"),
      item.selectedShankOffsetCounts.join("+"),
    ]),
  ];
  return `${rows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`;
}

function reportMarkdown(summary: PerfSummary): string {
  const lines: string[] = [];
  lines.push("# ProbePlanner Perf Report");
  lines.push("");
  lines.push(`Generated: ${summary.generatedAt}`);
  lines.push(`Mode: ${summary.mode}`);
  lines.push(`Backend: ${summary.backendUrl}`);
  if (summary.frontendUrl) lines.push(`Frontend: ${summary.frontendUrl}`);
  lines.push("");
  lines.push("## Suite");
  lines.push("");
  lines.push(`- Backend median: ${formatMs(summary.suite.backendTotalMs.median)}`);
  lines.push(`- Backend p95: ${formatMs(summary.suite.backendTotalMs.p95)}`);
  lines.push(`- Backend failures: ${summary.runHealth.backendFailedCount}/${summary.runHealth.backendRunCount}`);
  lines.push(`- Generation median: ${formatMs(summary.suite.generationMs.median)}`);
  lines.push(`- Scoring median: ${formatMs(summary.suite.scoringMs.median)}`);
  lines.push(`- Solver median: ${formatMs(summary.suite.solverMs.median)}`);
  lines.push(`- 3D total load median: ${formatMs(summary.browser3d.totalLoadMs.median)}`);
  lines.push(`- 3D interactive median: ${formatMs(summary.browser3d.interactiveReadyMs.median)}`);
  lines.push(`- 3D FPS median: ${summary.browser3d.fpsMedian.median ?? "n/a"}`);
  lines.push(`- 3D frame p95 median: ${formatMs(summary.browser3d.frameMsP95.median)}`);
  lines.push(`- 3D Unity resources: ${formatBytes(summary.browser3d.unityResourceTotalBytes.median)}`);
  lines.push(`- 3D max resource load: ${formatMs(summary.browser3d.unityResourceMaxLoadMs.median)}`);
  lines.push(`- 3D JS heap used: ${formatBytes(summary.browser3d.jsHeapUsedBytes.median)}`);
  lines.push(`- 3D WASM heap used: ${formatBytes(summary.browser3d.wasmHeapUsedBytes.median)}`);
  lines.push(`- 3D WebGL draw calls/sec: ${summary.browser3d.webglDrawCallsPerSecond.median ?? "n/a"}`);
  lines.push(`- 3D WebGL triangles/sec: ${summary.browser3d.webglTrianglesPerSecond.median ?? "n/a"}`);
  lines.push(`- 3D texture uploads: ${formatBytes(summary.browser3d.webglTextureUploadBytes.median)}`);
  lines.push(`- 3D failures: ${summary.runHealth.browser3dFailedCount}/${summary.runHealth.browser3dRunCount}`);
  lines.push("");
  if (summary.browser3d.fpsMedian.count > 0) {
    lines.push("## 3D Profile");
    lines.push("");
    lines.push(`- FPS p95 sample median: ${summary.browser3d.fpsMedian.p95 ?? "n/a"}`);
    lines.push(`- Frame p95 median: ${formatMs(summary.browser3d.frameMsP95.median)}`);
    lines.push(`- Total load p95: ${formatMs(summary.browser3d.totalLoadMs.p95)}`);
    lines.push(`- Resource load p95: ${formatMs(summary.browser3d.unityResourceMaxLoadMs.p95)}`);
    lines.push("");
  }
  if (summary.gates) {
    lines.push("## Gates");
    lines.push("");
    lines.push(`Overall: ${summary.gates.passed ? "PASS" : "FAIL"}`);
    lines.push("");
    for (const item of summary.gates.checks) {
      lines.push(`- ${item.passed ? "PASS" : "FAIL"} ${item.name}: expected ${item.expected}, actual ${item.actual}`);
    }
    lines.push("");
  }
  lines.push("## Cases");
  lines.push("");
  lines.push("| Case | Targets | Excluded targets | Requested | Effective | DRCC solver | Shanks | Candidates | Total median | Generation | Scoring | Solver | Uncovered | Atlas-backed uncovered | Runtime fallback | SDF expected | SDF outcome | SDF observed |");
  lines.push("| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |");
  for (const item of summary.cases) {
    lines.push(
      `| ${item.caseId.slice(0, 8)} ${item.label} | ${item.targetKeys.join("+")} | ${(item.excludedTargetKeys ?? []).join(", ") || "none"} | ${item.requestedAlgorithms.join(", ") || item.algorithm || "n/a"} | ${item.effectiveAlgorithms.join(", ") || "n/a"} | ${item.requestedDrccSolvers.join(", ") || "n/a"} -> ${item.effectiveDrccSolvers.join(", ") || "n/a"}${item.nativeDrccApproximation ? " approx" : ""} | req ${item.expectedShankCounts.join(",") || "n/a"} / runtime ${item.effectiveShankCounts.join(",") || "n/a"} / selected ${item.selectedShankCounts.join(",") || "n/a"} | ${item.candidateCountMedian ?? "n/a"} | ${formatMs(item.backendTotalMs.median)} | ${formatMs(item.generationMs.median)} | ${formatMs(item.scoringMs.median)} | ${formatMs(item.solverMs.median)} | ${item.uncoveredCountMedian ?? "n/a"} | ${item.atlasBackedUncoveredCountMedian ?? "n/a"} | ${item.fallbackTargetKeys.join(", ") || "none"} | ${item.expectedSdfRiskClasses.join(", ") || "none"} | ${item.expectedSdfOutcomes.join(", ") || "none"} | ${item.observedSdfRiskClasses.join(", ") || "none"} |`,
    );
  }
  lines.push("");
  return `${lines.join("\n")}\n`;
}

export async function writeOutputs(options: {
  outDir: string;
  runResult: PerfRunResult;
  summary: PerfSummary;
  writeBaseline?: boolean;
}): Promise<void> {
  await mkdir(options.outDir, { recursive: true });
  await writeFile(
    path.join(options.outDir, "run-results.jsonl"),
    `${options.runResult.backend.map((item) => JSON.stringify({ type: "backend", ...item })).join("\n")}\n` +
      `${options.runResult.browser3d.map((item) => JSON.stringify({ type: "browser3d", ...item })).join("\n")}\n`,
    "utf8",
  );
  await writeFile(path.join(options.outDir, "summary.json"), `${JSON.stringify(options.summary, null, 2)}\n`, "utf8");
  await writeFile(path.join(options.outDir, "summary.csv"), summaryCsv(options.summary), "utf8");
  await writeFile(path.join(options.outDir, "report.md"), reportMarkdown(options.summary), "utf8");
  if (options.writeBaseline) {
    await writeFile(path.join(options.outDir, "perf-baseline.json"), `${JSON.stringify(options.summary, null, 2)}\n`, "utf8");
  }
}
