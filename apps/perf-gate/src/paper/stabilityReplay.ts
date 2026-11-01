import { readFile } from "node:fs/promises";
import path from "node:path";
import type { BackendRunMetrics } from "../types.js";
import { flagNumber, flagString, fmt, median, parseFlags, percentile, repoRoot, writeJson, writeText } from "./common.js";

interface MetricBlock {
  medianOfRunP50: number | null;
  iqrOfRunP50: [number | null, number | null];
  medianOfRunP95: number | null;
  iqrOfRunP95: [number | null, number | null];
  bootstrapP50Ci95: [number | null, number | null];
  bootstrapP95Ci95: [number | null, number | null];
}

function clean(values: Array<number | null | undefined>): number[] {
  return values.filter((value): value is number => Number.isFinite(value));
}

function summarize(values: Array<number | null | undefined>): { count: number; p50: number | null; p95: number | null } {
  const xs = clean(values);
  return { count: xs.length, p50: median(xs), p95: percentile(xs, 95) };
}

function iqr(values: Array<number | null | undefined>): [number | null, number | null] {
  const xs = clean(values);
  return [percentile(xs, 25), percentile(xs, 75)];
}

function makeRng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function bootstrapCi(values: number[], reducer: (xs: number[]) => number | null, iterations: number, seed: number): [number | null, number | null] {
  if (values.length <= 0) return [null, null];
  const rng = makeRng(seed);
  const boot = [];
  for (let i = 0; i < iterations; i += 1) {
    const sample = [];
    for (let j = 0; j < values.length; j += 1) {
      sample.push(values[Math.floor(rng() * values.length)]!);
    }
    const value = reducer(sample);
    if (value !== null) boot.push(value);
  }
  return [percentile(boot, 2.5), percentile(boot, 97.5)];
}

async function readJsonl(filePath: string): Promise<BackendRunMetrics[]> {
  const text = await readFile(filePath, "utf8");
  return text
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line) as BackendRunMetrics & { type?: string })
    .filter((row) => row.type === "backend" || "backendTotalMs" in row);
}

function metricBlock(runs: BackendRunMetrics[], field: keyof BackendRunMetrics, bootstrapIterations: number): MetricBlock {
  const byRun = new Map<number, BackendRunMetrics[]>();
  for (const row of runs) {
    if (!byRun.has(row.runIndex)) byRun.set(row.runIndex, []);
    byRun.get(row.runIndex)!.push(row);
  }
  const runSummaries = [...byRun.entries()]
    .sort(([a], [b]) => a - b)
    .map(([runIndex, rows]) => ({
      runIndex,
      ...summarize(rows.filter((row) => row.ok).map((row) => row[field] as number | null)),
      failures: rows.filter((row) => !row.ok).length,
      total: rows.length,
    }));
  const allValues = clean(runs.filter((row) => row.ok).map((row) => row[field] as number | null));
  return {
    medianOfRunP50: median(runSummaries.map((item) => item.p50)),
    iqrOfRunP50: iqr(runSummaries.map((item) => item.p50)),
    medianOfRunP95: median(runSummaries.map((item) => item.p95)),
    iqrOfRunP95: iqr(runSummaries.map((item) => item.p95)),
    bootstrapP50Ci95: bootstrapCi(allValues, median, bootstrapIterations, 0x51ab1e),
    bootstrapP95Ci95: bootstrapCi(allValues, (xs) => percentile(xs, 95), bootstrapIterations, 0x95c1),
  };
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const resultPath = path.resolve(root, flagString(flags, "results", "reports/paper-native-replay110-3run/run-results.jsonl")!);
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-native-replay110-stability")!);
  const bootstrapIterations = flagNumber(flags, "bootstrap", 2000);
  const runs = await readJsonl(resultPath);
  const byRun = new Map<number, BackendRunMetrics[]>();
  for (const row of runs) {
    if (!byRun.has(row.runIndex)) byRun.set(row.runIndex, []);
    byRun.get(row.runIndex)!.push(row);
  }
  const perRun = [...byRun.entries()].sort(([a], [b]) => a - b).map(([runIndex, rows]) => ({
    runIndex,
    total: rows.length,
    failures: rows.filter((row) => !row.ok).length,
    backendTotalMs: summarize(rows.filter((row) => row.ok).map((row) => row.backendTotalMs)),
    generationMs: summarize(rows.filter((row) => row.ok).map((row) => row.generationMs)),
    scoringMs: summarize(rows.filter((row) => row.ok).map((row) => row.scoringMs)),
    solverMs: summarize(rows.filter((row) => row.ok).map((row) => row.solverMs)),
    coverageRate: rows.length > 0 ? rows.filter((row) => row.ok && row.atlasBackedUncoveredCount <= 0).length / rows.length : null,
  }));
  const summary = {
    generatedAt: new Date().toISOString(),
    resultPath: path.relative(root, resultPath),
    runCount: perRun.length,
    backendRunCount: runs.length,
    backendFailedCount: runs.filter((row) => !row.ok).length,
    metrics: {
      backendTotalMs: metricBlock(runs, "backendTotalMs", bootstrapIterations),
      generationMs: metricBlock(runs, "generationMs", bootstrapIterations),
      scoringMs: metricBlock(runs, "scoringMs", bootstrapIterations),
      solverMs: metricBlock(runs, "solverMs", bootstrapIterations),
    },
    perRun,
  };
  const lines = [
    "# Native Replay110 3-Run Stability",
    "",
    `Generated: ${summary.generatedAt}`,
    `Results: ${summary.resultPath}`,
    `Runs: ${summary.runCount}`,
    `Backend failures: ${summary.backendFailedCount}/${summary.backendRunCount}`,
    "",
    "## Stability Metrics",
    "",
    "| Metric | run-p50 median | run-p50 IQR | run-p95 median | run-p95 IQR | bootstrap p50 95% CI | bootstrap p95 95% CI |",
    "| --- | ---: | --- | ---: | --- | --- | --- |",
    ...Object.entries(summary.metrics).map(([key, value]) =>
      `| ${key} | ${fmt(value.medianOfRunP50)} | [${fmt(value.iqrOfRunP50[0])}, ${fmt(value.iqrOfRunP50[1])}] | ${fmt(value.medianOfRunP95)} | [${fmt(value.iqrOfRunP95[0])}, ${fmt(value.iqrOfRunP95[1])}] | [${fmt(value.bootstrapP50Ci95[0])}, ${fmt(value.bootstrapP50Ci95[1])}] | [${fmt(value.bootstrapP95Ci95[0])}, ${fmt(value.bootstrapP95Ci95[1])}] |`,
    ),
    "",
    "## Per Run",
    "",
    "| Run | Failures | Backend p50 | Backend p95 | Generation p95 | Scoring p95 | Solver p95 | Coverage rate |",
    "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...perRun.map((item) =>
      `| ${item.runIndex} | ${item.failures}/${item.total} | ${fmt(item.backendTotalMs.p50)} | ${fmt(item.backendTotalMs.p95)} | ${fmt(item.generationMs.p95)} | ${fmt(item.scoringMs.p95)} | ${fmt(item.solverMs.p95)} | ${fmt((item.coverageRate ?? 0) * 100, 1)}% |`,
    ),
    "",
  ];
  await writeJson(path.join(outDir, "stability.json"), summary);
  await writeText(path.join(outDir, "stability.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote stability report to ${path.join(outDir, "stability.md")}`);
  if (summary.backendFailedCount > 0 || summary.runCount < 3) process.exitCode = 2;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
