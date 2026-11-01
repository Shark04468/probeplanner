import path from "node:path";
import type { JsonObject, LoadedCaseManifest, ReplayCase } from "../types.js";
import { loadManifest } from "../cases/loadReplay.js";
import {
  cloneJson,
  ensureObject,
  flagNumber,
  flagString,
  numberOrNull,
  parseFlags,
  postOptimize,
  repoRoot,
  stringArray,
  withTask,
  writeJson,
  writeText,
} from "./common.js";

type VariantPatch = {
  params?: JsonObject;
  probeParams?: JsonObject;
  constraintModes?: JsonObject;
};

interface FailureRow {
  target: string;
  variant: string;
  hypothesis: string;
  ok: boolean;
  error: string | null;
  elapsedMs: number;
  candidateCount: number | null;
  candidateUniverseCount: number | null;
  selectedCount: number;
  uncovered: string[];
  effectiveAlgorithm: string;
  rejectReasons: string[];
  nativeEngineStatus: string;
  firstSelected: JsonObject | null;
}

const TARGETS = ["MOs", "ACB"] as const;

const VARIANTS: Array<{ name: string; patch: VariantPatch; hypothesis: string }> = [
  { name: "strict_baseline", patch: {}, hypothesis: "baseline hard constraints" },
  { name: "risk_warn", patch: { constraintModes: { continuousRiskDistance: "warn" } }, hypothesis: "SDF hard filtering" },
  { name: "risk_off", patch: { constraintModes: { continuousRiskDistance: "off" } }, hypothesis: "SDF hard filtering" },
  { name: "brain_bounds_warn", patch: { constraintModes: { brainBounds: "warn" } }, hypothesis: "brain-boundary hard filtering" },
  { name: "clearance_warn", patch: { constraintModes: { minClearance: "warn" } }, hypothesis: "capsule clearance hard filtering" },
  { name: "entry_spacing_warn", patch: { constraintModes: { minEntrySpacing: "warn" } }, hypothesis: "entry spacing hard filtering" },
  { name: "single_shank", patch: { probeParams: { shankCount: 1, shanks: 1 } }, hypothesis: "multi-shank footprint filtering" },
  { name: "four_shank", patch: { probeParams: { shankCount: 4, shanks: 4 } }, hypothesis: "multi-shank footprint filtering" },
  { name: "angle_20", patch: { params: { maxAngle: 20 } }, hypothesis: "entry-angle search envelope" },
  { name: "angle_25", patch: { params: { maxAngle: 25 } }, hypothesis: "entry-angle search envelope" },
  { name: "angle_30", patch: { params: { maxAngle: 30 } }, hypothesis: "entry-angle search envelope" },
  { name: "angle_35", patch: { params: { maxAngle: 35 } }, hypothesis: "entry-angle search envelope" },
  { name: "depth_8000", patch: { params: { depthUm: 8000 } }, hypothesis: "depth reachability" },
  { name: "depth_12000", patch: { params: { depthUm: 12000 } }, hypothesis: "depth reachability" },
  { name: "depth_14000", patch: { params: { depthUm: 14000 } }, hypothesis: "depth reachability" },
  { name: "coarse_50", patch: { params: { candidateCoarseStepUm: 50 } }, hypothesis: "candidate grid sampling" },
  { name: "coarse_75", patch: { params: { candidateCoarseStepUm: 75 } }, hypothesis: "candidate grid sampling" },
  { name: "coarse_100", patch: { params: { candidateCoarseStepUm: 100 } }, hypothesis: "candidate grid sampling" },
  { name: "candidate_limit_8192", patch: { params: { nativeCandidateLimit: 8192 } }, hypothesis: "candidate truncation" },
  {
    name: "relaxed_combo",
    patch: {
      params: { maxAngle: 35, depthUm: 14000, candidateCoarseStepUm: 50, nativeCandidateLimit: 8192 },
      probeParams: { shankCount: 1, shanks: 1 },
      constraintModes: { continuousRiskDistance: "warn", brainBounds: "warn", minClearance: "warn", minEntrySpacing: "warn" },
    },
    hypothesis: "combined search envelope and hard-constraint relaxation",
  },
];

function makeRequest(base: ReplayCase, target: string, variant: typeof VARIANTS[number]): JsonObject {
  const request = cloneJson(base.request);
  request.targetKeys = [target];
  request.params = {
    ...ensureObject(request.params),
    algorithm: variant.name === "candidate_limit_8192" ? "greedy" : String(ensureObject(request.params).algorithm || "greedy"),
    strictAtlasTargets: true,
    allowFallbackTargetCoverage: false,
    ...(variant.patch.params ?? {}),
  };
  request.probeParams = {
    ...ensureObject(request.probeParams),
    ...(variant.patch.probeParams ?? {}),
  };
  request.constraintModes = {
    ...ensureObject(request.constraintModes),
    brainBounds: "enforce",
    lambdaBoundary: "enforce",
    continuousRiskDistance: "enforce",
    minEntrySpacing: "enforce",
    minClearance: "enforce",
    ...(variant.patch.constraintModes ?? {}),
  };
  request.clinicalContext = {
    ...ensureObject(request.clinicalContext),
    replayCase: `failure_${target}_${variant.name}`,
    paperFailureAnalysis: true,
    failureAnalysisTarget: target,
    failureAnalysisVariant: variant.name,
    failureAnalysisHypothesis: variant.hypothesis,
    nativeExcludedFallbackTargetKeys: [],
  };
  return request;
}

function pickBase(manifest: LoadedCaseManifest, target: string): ReplayCase {
  const candidates = manifest.cases.filter((item) =>
    item.source === "paper_replay"
    && item.targetKeys.length <= 2
    && !item.targetKeys.includes(target)
    && item.algorithm === "greedy",
  );
  return candidates[0] ?? manifest.cases.find((item) => item.source === "paper_replay") ?? manifest.cases[0]!;
}

function rejectReasons(response: JsonObject | undefined): string[] {
  const breakdown = ensureObject(response?.candidateStageBreakdown);
  const reasons = ensureObject(breakdown.nativeRejectReasons);
  return Object.keys(reasons).sort();
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const backendUrl = flagString(flags, "backend-url", "http://127.0.0.1:5000")!;
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-failure-analysis")!);
  const timeoutMs = flagNumber(flags, "timeout-ms", 900_000);
  const manifest = await loadManifest(root);
  const rows: FailureRow[] = [];

  for (const target of TARGETS) {
    const base = pickBase(manifest, target);
    for (const variant of VARIANTS) {
      const request = withTask(makeRequest(base, target, variant), `paper_fail_${target}_${variant.name}`);
      console.log(`[failure] ${target} ${variant.name}`);
      const result = await postOptimize(backendUrl, request, timeoutMs);
      const response = result.response;
      const selected = Array.isArray(response?.selected) ? response.selected as JsonObject[] : [];
      const uncovered = stringArray(response?.uncovered);
      rows.push({
        target,
        variant: variant.name,
        hypothesis: variant.hypothesis,
        ok: result.ok,
        error: result.error ?? null,
        elapsedMs: result.elapsedMs,
        candidateCount: numberOrNull(response?.candidateCount),
        candidateUniverseCount: numberOrNull(response?.candidateUniverseCount),
        selectedCount: selected.length,
        uncovered,
        effectiveAlgorithm: String(ensureObject(ensureObject(response?.computeProfile).solverRuntimePolicy).effectiveAlgorithm || response?.algorithm || ""),
        rejectReasons: rejectReasons(response),
        nativeEngineStatus: String(ensureObject(response?.candidateStageBreakdown).nativeEngineStatus || ""),
        firstSelected: selected[0] ? {
          entry_xyz: selected[0].entry_xyz ?? null,
          tip_xyz: selected[0].tip_xyz ?? null,
          dir: selected[0].dir ?? null,
          riskDiagnostics: selected[0].riskDiagnostics ?? null,
        } : null,
      });
    }
  }

  const byTarget = Object.fromEntries(TARGETS.map((target) => {
    const targetRows = rows.filter((item) => item.target === target);
    const nonzero = targetRows.filter((item) => Number(item.candidateCount) > 0);
    return [target, {
      runs: targetRows.length,
      nonzeroCandidateRuns: nonzero.length,
      selectedRuns: targetRows.filter((item) => item.selectedCount > 0).length,
      allZeroCandidate: nonzero.length === 0,
      rejectReasons: Array.from(new Set(targetRows.flatMap((item) => item.rejectReasons))).sort(),
      bestVariants: targetRows
        .filter((item) => Number(item.candidateCount) > 0 || item.selectedCount > 0)
        .map((item) => ({ variant: item.variant, candidateCount: item.candidateCount, selectedCount: item.selectedCount })),
    }];
  }));

  const summary = {
    generatedAt: new Date().toISOString(),
    backendUrl,
    variantCount: VARIANTS.length,
    targetCount: TARGETS.length,
    runCount: rows.length,
    byTarget,
    rows,
  };

  const lines = [
    "# MOs/ACB Failure Analysis",
    "",
    `Generated: ${summary.generatedAt}`,
    `Backend: ${backendUrl}`,
    "",
    "## Target Summary",
    "",
    "| Target | Runs | Nonzero candidate runs | Selected runs | All zero candidate | Reject reasons |",
    "| --- | ---: | ---: | ---: | --- | --- |",
    ...Object.entries(byTarget).map(([target, item]) =>
      `| ${target} | ${item.runs} | ${item.nonzeroCandidateRuns} | ${item.selectedRuns} | ${item.allZeroCandidate} | ${item.rejectReasons.join(", ") || "none"} |`,
    ),
    "",
    "## Variant Results",
    "",
    "| Target | Variant | Hypothesis | OK | Candidates | Selected | Uncovered | Reject reasons |",
    "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ...rows.map((item) =>
      `| ${item.target} | ${item.variant} | ${item.hypothesis} | ${item.ok} | ${item.candidateCount ?? "n/a"} | ${item.selectedCount} | ${item.uncovered.join("+") || "none"} | ${item.rejectReasons.join(", ") || "none"} |`,
    ),
    "",
  ];

  await writeJson(path.join(outDir, "failure-analysis.json"), summary);
  await writeText(path.join(outDir, "failure-analysis.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote failure analysis to ${path.join(outDir, "failure-analysis.md")}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
