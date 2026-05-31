import path from "node:path";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { collect3dMetrics } from "./browser/collect3dMetrics.js";
import { loadManifest, prepareCases } from "./cases/loadReplay.js";
import { runBackendCase } from "./backend/client.js";
import { summarizeRun } from "./metrics/aggregate.js";
import { evaluateGates } from "./metrics/gates.js";
import { readSummary, writeOutputs } from "./output/writers.js";
import type { PerfRunResult } from "./types.js";

function parseArgs(argv: string[]): { command: string; flags: Record<string, string | boolean> } {
  const [command = "help", ...rest] = argv;
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < rest.length; i += 1) {
    const token = rest[i] || "";
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = rest[i + 1];
    if (!next || next.startsWith("--")) {
      flags[key] = true;
    } else {
      flags[key] = next;
      i += 1;
    }
  }
  return { command, flags };
}

function flagString(flags: Record<string, string | boolean>, key: string, fallback?: string): string | undefined {
  const value = flags[key];
  return typeof value === "string" ? value : fallback;
}

function flagNumber(flags: Record<string, string | boolean>, key: string, fallback: number): number {
  const value = Number(flags[key]);
  return Number.isFinite(value) ? value : fallback;
}

function flagBool(flags: Record<string, string | boolean>, key: string): boolean {
  const value = flags[key];
  return value === true || value === "true" || value === "1";
}

function rootDir(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
}

async function commandPrepareCases(flags: Record<string, string | boolean>): Promise<void> {
  const root = rootDir();
  const sourceProject = path.resolve(root, flagString(flags, "source-project", "../probe-planner") || "../probe-planner");
  const localReplayDir = flagString(flags, "local-replay-dir");
  const manifest = await prepareCases({ rootDir: root, sourceProject, localReplayDir });
  console.log(`Prepared ${manifest.cases.length} cases in ${path.join(root, "cases")}`);
}

async function commandRun(flags: Record<string, string | boolean>): Promise<void> {
  const root = rootDir();
  const mode = flagString(flags, "mode", "baseline") === "gate" ? "gate" : "baseline";
  const backendUrl = flagString(flags, "backend-url", "http://127.0.0.1:5000") || "http://127.0.0.1:5000";
  const frontendUrl = flagString(flags, "frontend-url");
  const runs = flagNumber(flags, "runs", 3);
  const browserRuns = flagNumber(flags, "3d-runs", frontendUrl ? 1 : 0);
  const outFlag = flagString(flags, "out") ?? flagString(flags, "report-dir");
  const outDir = path.resolve(root, outFlag ?? (mode === "gate" ? "reports/latest" : "reports/baseline"));
  const manifest = await loadManifest(root);
  const caseIdFilter = flagString(flags, "case-id");
  const caseLabelPrefix = flagString(flags, "case-label-prefix");
  const caseSource = flagString(flags, "case-source");
  const caseGroup = flagString(flags, "case-group");
  const caseLimit = flagNumber(flags, "case-limit", caseGroup || caseLabelPrefix || caseSource ? manifest.cases.length : 10);
  const selectedCases = manifest.cases
    .filter((item) => !caseIdFilter || item.id.startsWith(caseIdFilter) || item.id === caseIdFilter)
    .filter((item) => !caseLabelPrefix || item.label.startsWith(caseLabelPrefix))
    .filter((item) => !caseSource || item.source === caseSource)
    .filter((item) => {
      if (!caseGroup) return true;
      if (caseGroup === "sdf-adversarial") return item.source === "adversarial" || item.label.startsWith("sdf_");
      if (caseGroup === "replay10") return item.source !== "adversarial";
      throw new Error(`Unknown --case-group ${caseGroup}`);
    })
    .slice(0, Math.max(0, caseLimit));
  if (selectedCases.length <= 0) {
    throw new Error("No replay cases selected. Check --case-id or --case-limit.");
  }
  const selectedManifest = { ...manifest, cases: selectedCases };
  const backend = [];
  const backendWarmupRuns = flagNumber(flags, "backend-warmup-runs", 0);

  for (let runIndex = 0; runIndex < backendWarmupRuns; runIndex += 1) {
    for (const replayCase of selectedCases) {
      console.log(`[backend:warmup] run ${runIndex + 1}/${backendWarmupRuns} case ${replayCase.id.slice(0, 8)} ${replayCase.label}`);
      await runBackendCase({
        backendUrl,
        replayCase,
        runIndex: -1 - runIndex,
        pollIntervalMs: flagNumber(flags, "poll-ms", 500),
        timeoutMs: flagNumber(flags, "timeout-ms", 20 * 60_000),
      });
    }
  }

  for (let runIndex = 0; runIndex < runs; runIndex += 1) {
    for (const replayCase of selectedCases) {
      console.log(`[backend] run ${runIndex + 1}/${runs} case ${replayCase.id.slice(0, 8)} ${replayCase.label}`);
      backend.push(await runBackendCase({
        backendUrl,
        replayCase,
        runIndex,
        pollIntervalMs: flagNumber(flags, "poll-ms", 500),
        timeoutMs: flagNumber(flags, "timeout-ms", 20 * 60_000),
      }));
    }
  }

  const browser3d = [];
  if (frontendUrl && !flagBool(flags, "skip-3d")) {
    const trace3d = flagBool(flags, "3d-trace");
    if (trace3d) await mkdir(path.join(outDir, "traces"), { recursive: true });
    for (let runIndex = 0; runIndex < browserRuns; runIndex += 1) {
      console.log(`[3d] run ${runIndex + 1}/${browserRuns} ${frontendUrl}`);
      browser3d.push(await collect3dMetrics({
        frontendUrl,
        runIndex,
        timeoutMs: flagNumber(flags, "3d-timeout-ms", 45_000),
        sampleMs: flagNumber(flags, "3d-sample-ms", 10_000),
        tracePath: trace3d ? path.join(outDir, "traces", `browser3d-run-${runIndex + 1}.zip`) : undefined,
        headless: !flagBool(flags, "3d-headed"),
        browserChannel: flagString(flags, "3d-browser-channel"),
      }));
    }
  }

  const runResult: PerfRunResult = {
    startedAt: new Date().toISOString(),
    mode,
    backendUrl,
    frontendUrl,
    runs,
    backend,
    browser3d,
  };
  const summary = summarizeRun(runResult, selectedManifest);
  if (flagBool(flags, "require-3d") && summary.runHealth.browser3dRunCount <= 0) {
    throw new Error("--require-3d was set but no 3D runs were collected. Provide --frontend-url and do not use --skip-3d.");
  }
  if (mode === "gate") {
    const baselinePath = flagString(flags, "baseline");
    if (!baselinePath) throw new Error("--baseline is required in gate mode");
    summary.gates = evaluateGates(summary, await readSummary(path.resolve(root, baselinePath)));
  }
  await writeOutputs({ outDir, runResult, summary, writeBaseline: flagBool(flags, "write-baseline") });
  console.log(`Wrote report to ${path.join(outDir, "report.md")}`);
  const hasRunFailures = summary.runHealth.backendFailedCount > 0 || summary.runHealth.browser3dFailedCount > 0;
  if (hasRunFailures && !flagBool(flags, "allow-failures")) {
    process.exitCode = 2;
  }
  if (summary.gates && !summary.gates.passed) {
    process.exitCode = 2;
  }
}

async function main(): Promise<void> {
  const { command, flags } = parseArgs(process.argv.slice(2));
  if (command === "prepare:cases") return commandPrepareCases(flags);
  if (command === "run") return commandRun(flags);
  console.log("Usage: prepare:cases | run --mode baseline|gate");
}

main().catch((err) => {
  console.error(err instanceof Error ? err.stack || err.message : String(err));
  process.exitCode = 1;
});
