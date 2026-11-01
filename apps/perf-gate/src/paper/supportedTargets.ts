import path from "node:path";
import type { CaseSummary, PerfSummary } from "../types.js";
import { flagString, fmt, parseFlags, readJson, repoRoot, writeJson, writeText } from "./common.js";

interface TargetRecord {
  target: string;
  main: TargetStats;
  stress: TargetStats;
  ablation: TargetStats;
  all: TargetStats;
  status: "supported" | "partial" | "boundary" | "unsupported" | "insufficient_evidence";
  robustnessStatus: "all_covered" | "mixed" | "stress_limited" | "not_tested";
  note: string;
}

interface TargetStats {
  appearances: number;
  fullCovered: number;
  partialOrUncertain: number;
  failedCaseCount: number;
  sources: Set<string>;
  rejectReasons: Set<string>;
}

interface FailureAnalysis {
  byTarget?: Record<string, {
    runs: number;
    nonzeroCandidateRuns: number;
    selectedRuns: number;
    rejectReasons: string[];
  }>;
}

function addCase(stats: TargetStats, item: CaseSummary): void {
  stats.appearances += 1;
  stats.sources.add(item.source);
  const failed = item.backendTotalMs.count <= 0;
  if (failed) {
    stats.failedCaseCount += 1;
    for (const reason of item.nativeRejectReasonKeys ?? []) stats.rejectReasons.add(reason);
    return;
  }
  if ((item.atlasBackedUncoveredCountMedian ?? 0) <= 0) {
    stats.fullCovered += 1;
  } else {
    stats.partialOrUncertain += 1;
  }
  for (const reason of item.nativeRejectReasonKeys ?? []) stats.rejectReasons.add(reason);
}

function emptyStats(): TargetStats {
  return {
    appearances: 0,
    fullCovered: 0,
    partialOrUncertain: 0,
    failedCaseCount: 0,
    sources: new Set(),
    rejectReasons: new Set(),
  };
}

function mergeStats(...statsList: TargetStats[]): TargetStats {
  const merged = emptyStats();
  for (const stats of statsList) {
    merged.appearances += stats.appearances;
    merged.fullCovered += stats.fullCovered;
    merged.partialOrUncertain += stats.partialOrUncertain;
    merged.failedCaseCount += stats.failedCaseCount;
    for (const source of stats.sources) merged.sources.add(source);
    for (const reason of stats.rejectReasons) merged.rejectReasons.add(reason);
  }
  return merged;
}

function emptyRecord(target: string): TargetRecord {
  return {
    target,
    main: emptyStats(),
    stress: emptyStats(),
    ablation: emptyStats(),
    all: emptyStats(),
    status: "insufficient_evidence",
    robustnessStatus: "not_tested",
    note: "",
  };
}

function serialiseStats(stats: TargetStats): {
  appearances: number;
  fullCovered: number;
  partialOrUncertain: number;
  failedCaseCount: number;
  fullCoverageRate: number | null;
  sources: string[];
  rejectReasons: string[];
} {
  return {
    appearances: stats.appearances,
    fullCovered: stats.fullCovered,
    partialOrUncertain: stats.partialOrUncertain,
    failedCaseCount: stats.failedCaseCount,
    fullCoverageRate: stats.appearances > 0 ? stats.fullCovered / stats.appearances : null,
    sources: [...stats.sources].sort(),
    rejectReasons: [...stats.rejectReasons].sort(),
  };
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const nativePath = path.resolve(root, flagString(flags, "native", "reports/paper-native-replay110/summary.json")!);
  const stressPath = path.resolve(root, flagString(flags, "stress", "reports/paper-stress500/summary.json")!);
  const ablationPath = path.resolve(root, flagString(flags, "ablation", "reports/paper-ablation/summary.json")!);
  const failurePath = path.resolve(root, flagString(flags, "failure", "reports/paper-failure-analysis/failure-analysis.json")!);
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-supported-target-set")!);
  const native = await readJson<PerfSummary>(nativePath);
  const stress = await readJson<PerfSummary>(stressPath);
  const ablation = await readJson<PerfSummary>(ablationPath);
  const failure = await readJson<FailureAnalysis>(failurePath);
  const records = new Map<string, TargetRecord>();
  for (const [scope, summary] of [
    ["main", native],
    ["stress", stress],
    ["ablation", ablation],
  ] as const) {
    for (const item of summary.cases) {
      for (const target of item.targetKeys) {
        if (!records.has(target)) records.set(target, emptyRecord(target));
        addCase(records.get(target)![scope], item);
      }
    }
  }
  for (const [target, info] of Object.entries(failure.byTarget ?? {})) {
    if (!records.has(target)) records.set(target, emptyRecord(target));
    const record = records.get(target)!;
    for (const reason of info.rejectReasons ?? []) {
      record.main.rejectReasons.add(reason);
      record.all.rejectReasons.add(reason);
    }
  }

  for (const record of records.values()) {
    record.all = mergeStats(record.main, record.stress, record.ablation);
    const failureInfo = failure.byTarget?.[record.target];
    const robustness = mergeStats(record.stress, record.ablation);
    if (robustness.appearances <= 0) {
      record.robustnessStatus = "not_tested";
    } else if (robustness.failedCaseCount > 0 || robustness.partialOrUncertain > 0) {
      record.robustnessStatus = robustness.fullCovered > 0 ? "mixed" : "stress_limited";
    } else {
      record.robustnessStatus = "all_covered";
    }
    if (failureInfo) {
      record.status = "boundary";
      record.note = "fail-closed boundary case in dedicated MOs/ACB failure analysis";
    } else if (record.main.appearances < 3) {
      record.status = "insufficient_evidence";
      record.note = "fewer than 3 appearances in the main replay110 evidence set";
    } else if (record.main.fullCovered <= 0 && (record.main.failedCaseCount > 0 || record.main.partialOrUncertain > 0)) {
      record.status = "unsupported";
      record.note = "no fully covered successful main replay110 case";
    } else if (record.main.partialOrUncertain > 0 || record.main.failedCaseCount > 0) {
      record.status = "partial";
      record.note = "main replay110 has at least one partial, timeout, or failed planning case";
    } else {
      record.status = "supported";
      record.note = "all main replay110 appearances were fully covered; stress/ablation robustness is reported separately";
    }
  }

  const rows = [...records.values()].sort((a, b) => {
    const order = { boundary: 0, unsupported: 1, partial: 2, insufficient_evidence: 3, supported: 4 };
    return order[a.status] - order[b.status] || a.target.localeCompare(b.target);
  }).map((record) => ({
    target: record.target,
    status: record.status,
    robustnessStatus: record.robustnessStatus,
    main: serialiseStats(record.main),
    stress: serialiseStats(record.stress),
    ablation: serialiseStats(record.ablation),
    allEvidence: serialiseStats(record.all),
    note: record.note,
  }));
  const summary = {
    generatedAt: new Date().toISOString(),
    inputs: {
      native: path.relative(root, nativePath),
      stress: path.relative(root, stressPath),
      ablation: path.relative(root, ablationPath),
      failure: path.relative(root, failurePath),
    },
    statusCounts: rows.reduce<Record<string, number>>((acc, row) => {
      acc[row.status] = (acc[row.status] ?? 0) + 1;
      return acc;
    }, {}),
    rows,
  };
  const lines = [
    "# Supported Target Set",
    "",
    `Generated: ${summary.generatedAt}`,
    "",
    "Status definitions:",
    "",
    "- `supported`: all main replay110 appearances were fully covered.",
    "- `partial`: at least one main replay110 partial, timeout, or failed planning case exists.",
    "- `boundary`: dedicated fail-closed target-specific failure analysis exists; do not present as universally supported.",
    "- `unsupported`: no fully covered successful main replay110 case.",
    "- `insufficient_evidence`: fewer than 3 main replay110 appearances.",
    "",
    "Stress500 and ablation results are reported separately as robustness evidence. They are not used to turn a main replay110 supported target into a main-experiment failure.",
    "",
    "## Status Counts",
    "",
    ...Object.entries(summary.statusCounts).map(([status, count]) => `- ${status}: ${count}`),
    "",
    "## Targets",
    "",
    "| Target | Status | Main full rate | Main appearances | Main partial/failed | Robustness | Stress+ablation full rate | Stress+ablation partial/failed | Reject reasons | Note |",
    "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- | --- |",
    ...rows.map((row) =>
      `| ${row.target} | ${row.status} | ${fmt((row.main.fullCoverageRate ?? 0) * 100, 1)}% | ${row.main.appearances} | ${row.main.partialOrUncertain + row.main.failedCaseCount} | ${row.robustnessStatus} | ${fmt((((row.stress.fullCovered + row.ablation.fullCovered) / Math.max(1, row.stress.appearances + row.ablation.appearances)) * 100), 1)}% | ${row.stress.partialOrUncertain + row.stress.failedCaseCount + row.ablation.partialOrUncertain + row.ablation.failedCaseCount} | ${row.allEvidence.rejectReasons.join(", ") || "none"} | ${row.note} |`,
    ),
    "",
  ];
  await writeJson(path.join(outDir, "supported-target-set.json"), summary);
  await writeText(path.join(outDir, "supported-target-set.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote supported target set to ${path.join(outDir, "supported-target-set.md")}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
