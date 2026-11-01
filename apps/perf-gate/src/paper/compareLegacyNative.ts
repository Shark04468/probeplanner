import path from "node:path";
import type { CaseSummary, PerfSummary } from "../types.js";
import { flagString, fmt, parseFlags, readJson, repoRoot, writeJson, writeText } from "./common.js";

interface MetricComparison {
  legacy: number | null;
  native: number | null;
  speedup: number | null;
  delta: number | null;
}

function metric(legacy: number | null | undefined, native: number | null | undefined): MetricComparison {
  const l = Number.isFinite(legacy) ? Number(legacy) : null;
  const n = Number.isFinite(native) ? Number(native) : null;
  return {
    legacy: l,
    native: n,
    speedup: l !== null && n !== null && n > 0 ? l / n : null,
    delta: l !== null && n !== null ? n - l : null,
  };
}

function byCase(summary: PerfSummary): Map<string, CaseSummary> {
  return new Map(summary.cases.map((item) => [item.caseId, item]));
}

function finite(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function coverageRate(summary: PerfSummary): number | null {
  const total = summary.cases.length;
  if (total <= 0) return null;
  const fullyCovered = summary.cases.filter((item) => Number(item.atlasBackedUncoveredCountMedian ?? 0) <= 0).length;
  return fullyCovered / total;
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const legacyPath = path.resolve(root, flagString(flags, "legacy", "reports/paper-legacy-replay100/summary.json")!);
  const nativePath = path.resolve(root, flagString(flags, "native", "reports/paper-native-replay100/summary.json")!);
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-legacy-native-comparison")!);
  const legacy = await readJson<PerfSummary>(legacyPath);
  const native = await readJson<PerfSummary>(nativePath);
  const legacyCases = byCase(legacy);
  const nativeCases = byCase(native);
  const sharedIds = native.cases.map((item) => item.caseId).filter((id) => legacyCases.has(id));

  const perCase = sharedIds.map((caseId) => {
    const l = legacyCases.get(caseId)!;
    const n = nativeCases.get(caseId)!;
    return {
      caseId,
      label: n.label,
      source: n.source,
      targetKeys: n.targetKeys,
      totalMedianMs: metric(l.backendTotalMs.median, n.backendTotalMs.median),
      totalP95Ms: metric(l.backendTotalMs.p95, n.backendTotalMs.p95),
      generationMedianMs: metric(l.generationMs.median, n.generationMs.median),
      scoringMedianMs: metric(l.scoringMs.median, n.scoringMs.median),
      solverMedianMs: metric(l.solverMs.median, n.solverMs.median),
      legacyCandidates: finite(l.candidateCountMedian),
      nativeCandidates: finite(n.candidateCountMedian),
      legacyUncovered: finite(l.atlasBackedUncoveredCountMedian),
      nativeUncovered: finite(n.atlasBackedUncoveredCountMedian),
      legacyEffectiveAlgorithms: l.effectiveAlgorithms,
      nativeEffectiveAlgorithms: n.effectiveAlgorithms,
    };
  });

  const comparison = {
    generatedAt: new Date().toISOString(),
    legacySummary: path.relative(root, legacyPath),
    nativeSummary: path.relative(root, nativePath),
    sharedCaseCount: sharedIds.length,
    runHealth: {
      legacy: legacy.runHealth,
      native: native.runHealth,
    },
    suite: {
      backendMedianMs: metric(legacy.suite.backendTotalMs.median, native.suite.backendTotalMs.median),
      backendP95Ms: metric(legacy.suite.backendTotalMs.p95, native.suite.backendTotalMs.p95),
      generationMedianMs: metric(legacy.suite.generationMs.median, native.suite.generationMs.median),
      generationP95Ms: metric(legacy.suite.generationMs.p95, native.suite.generationMs.p95),
      scoringMedianMs: metric(legacy.suite.scoringMs.median, native.suite.scoringMs.median),
      scoringP95Ms: metric(legacy.suite.scoringMs.p95, native.suite.scoringMs.p95),
      solverMedianMs: metric(legacy.suite.solverMs.median, native.suite.solverMs.median),
      solverP95Ms: metric(legacy.suite.solverMs.p95, native.suite.solverMs.p95),
      coverageRate: metric(coverageRate(legacy), coverageRate(native)),
    },
    perCase,
  };

  const lines = [
    "# Legacy vs Native Replay Comparison",
    "",
    `Generated: ${comparison.generatedAt}`,
    `Legacy: ${comparison.legacySummary}`,
    `Native: ${comparison.nativeSummary}`,
    `Shared cases: ${comparison.sharedCaseCount}`,
    "",
    "## Suite",
    "",
    `- Backend median: legacy ${fmt(comparison.suite.backendMedianMs.legacy)} ms, native ${fmt(comparison.suite.backendMedianMs.native)} ms, speedup ${fmt(comparison.suite.backendMedianMs.speedup)}x`,
    `- Backend p95: legacy ${fmt(comparison.suite.backendP95Ms.legacy)} ms, native ${fmt(comparison.suite.backendP95Ms.native)} ms, speedup ${fmt(comparison.suite.backendP95Ms.speedup)}x`,
    `- Generation median: legacy ${fmt(comparison.suite.generationMedianMs.legacy)} ms, native ${fmt(comparison.suite.generationMedianMs.native)} ms, speedup ${fmt(comparison.suite.generationMedianMs.speedup)}x`,
    `- Scoring median: legacy ${fmt(comparison.suite.scoringMedianMs.legacy)} ms, native ${fmt(comparison.suite.scoringMedianMs.native)} ms, speedup ${fmt(comparison.suite.scoringMedianMs.speedup)}x`,
    `- Solver median: legacy ${fmt(comparison.suite.solverMedianMs.legacy)} ms, native ${fmt(comparison.suite.solverMedianMs.native)} ms, speedup ${fmt(comparison.suite.solverMedianMs.speedup)}x`,
    `- Coverage rate: legacy ${fmt((comparison.suite.coverageRate.legacy ?? 0) * 100, 1)}%, native ${fmt((comparison.suite.coverageRate.native ?? 0) * 100, 1)}%`,
    `- Failures: legacy ${legacy.runHealth.backendFailedCount}/${legacy.runHealth.backendRunCount}, native ${native.runHealth.backendFailedCount}/${native.runHealth.backendRunCount}`,
    "",
    "## Per-Case Summary",
    "",
    "| Case | Targets | Legacy total median | Native total median | Speedup | Legacy uncovered | Native uncovered |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ...perCase.map((item) =>
      `| ${item.caseId.slice(0, 8)} ${item.label} | ${item.targetKeys.join("+")} | ${fmt(item.totalMedianMs.legacy)} | ${fmt(item.totalMedianMs.native)} | ${fmt(item.totalMedianMs.speedup)}x | ${item.legacyUncovered ?? "n/a"} | ${item.nativeUncovered ?? "n/a"} |`,
    ),
    "",
  ];

  await writeJson(path.join(outDir, "comparison.json"), comparison);
  await writeText(path.join(outDir, "comparison.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote comparison to ${path.join(outDir, "comparison.md")}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
