import path from "node:path";
import { performance } from "node:perf_hooks";
import { flagString, fmt, parseFlags, repoRoot, writeJson, writeText } from "./common.js";

interface Vec3 { x: number; y: number; z: number }
interface Brain {
  center: Vec3;
  radii: Vec3;
}
interface Obstacle {
  id: string;
  center: Vec3;
  radiusMm: number;
}
interface Target {
  id: string;
  groupId: string;
  center: Vec3;
  radiusMm: number;
}
interface Candidate {
  id: string;
  family: "designed_optimal" | "redundant_group" | "within_group_pair" | "single_target" | "invalid_obstacle" | "invalid_boundary" | "near_miss";
  entry: Vec3;
  tip: Vec3;
  depthMm: number;
  thetaDeg: number;
  phiDeg: number;
  minBoundaryMarginMm: number;
  minObstacleMarginMm: number;
  violatesBoundary: boolean;
  violatesObstacle: boolean;
  coveredTargetIds: string[];
  coverMask: number;
  targetDistancesMm: Record<string, number>;
}

const brain: Brain = {
  center: { x: 0, y: 4.4, z: 0 },
  radii: { x: 4.5, y: 4.8, z: 3.5 },
};

const obstacles: Obstacle[] = [
  { id: "central_ventricle_like_obstacle", center: { x: 0.0, y: 4.4, z: 0.0 }, radiusMm: 0.55 },
  { id: "anterior_vessel_like_obstacle", center: { x: 0.35, y: 5.8, z: 0.0 }, radiusMm: 0.32 },
  { id: "posterior_vessel_like_obstacle", center: { x: -0.35, y: 3.0, z: 0.0 }, radiusMm: 0.32 },
];

const groupSpecs = [
  { id: "G1_left_posterior", x: -2.4, z: -1.3 },
  { id: "G2_right_posterior", x: 2.4, z: -1.3 },
  { id: "G3_left_anterior", x: -2.4, z: 1.3 },
  { id: "G4_right_anterior", x: 2.4, z: 1.3 },
] as const;

const groupTargetY = [2.7, 3.7, 4.7, 5.7];
const targetRadiusMm = 0.2;
const probeClearanceMm = 0.08;
const groundTruthOptimalProbeCount = 4;

function add(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function sub(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function mul(a: Vec3, scale: number): Vec3 {
  return { x: a.x * scale, y: a.y * scale, z: a.z * scale };
}

function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function dist(a: Vec3, b: Vec3): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

function bitCount(mask: number): number {
  let n = mask;
  let count = 0;
  while (n !== 0) {
    n &= n - 1;
    count += 1;
  }
  return count;
}

function ellipsoidValue(p: Vec3): number {
  return ((p.x - brain.center.x) / brain.radii.x) ** 2
    + ((p.y - brain.center.y) / brain.radii.y) ** 2
    + ((p.z - brain.center.z) / brain.radii.z) ** 2;
}

function boundaryMargin(p: Vec3): number {
  const value = Math.sqrt(ellipsoidValue(p));
  const minRadius = Math.min(brain.radii.x, brain.radii.y, brain.radii.z);
  return (1 - value) * minRadius;
}

function obstacleMargin(p: Vec3): number {
  return Math.min(...obstacles.map((obstacle) => dist(p, obstacle.center) - obstacle.radiusMm - probeClearanceMm));
}

function segmentDistance(point: Vec3, entry: Vec3, tip: Vec3): number {
  const ab = sub(tip, entry);
  const ap = sub(point, entry);
  const denom = dot(ab, ab);
  const t = denom <= 0 ? 0 : Math.max(0, Math.min(1, dot(ap, ab) / denom));
  const closest = add(entry, mul(ab, t));
  return dist(point, closest);
}

function trajectoryAngles(entry: Vec3, tip: Vec3): Pick<Candidate, "depthMm" | "thetaDeg" | "phiDeg"> {
  const delta = sub(tip, entry);
  const depthMm = Math.hypot(delta.x, delta.y, delta.z);
  const thetaDeg = depthMm <= 0 ? 0 : Math.acos(Math.max(-1, Math.min(1, delta.y / depthMm))) * 180 / Math.PI;
  const phiDeg = Math.atan2(delta.z, delta.x) * 180 / Math.PI;
  return { depthMm, thetaDeg, phiDeg: phiDeg < 0 ? phiDeg + 360 : phiDeg };
}

function candidateMargins(entry: Vec3, tip: Vec3): Pick<Candidate, "minBoundaryMarginMm" | "minObstacleMarginMm" | "violatesBoundary" | "violatesObstacle"> {
  const steps = 96;
  let minBoundaryMarginMm = Infinity;
  let minObstacleMarginMm = Infinity;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const p = add(entry, mul(sub(tip, entry), t));
    minBoundaryMarginMm = Math.min(minBoundaryMarginMm, boundaryMargin(p));
    minObstacleMarginMm = Math.min(minObstacleMarginMm, obstacleMargin(p));
  }
  return {
    minBoundaryMarginMm,
    minObstacleMarginMm,
    violatesBoundary: minBoundaryMarginMm < -1e-9,
    violatesObstacle: minObstacleMarginMm < -1e-9,
  };
}

function buildTargets(): Target[] {
  const targets: Target[] = [];
  for (const group of groupSpecs) {
    groupTargetY.forEach((y, targetIndex) => {
      targets.push({
        id: `${group.id}_T${targetIndex + 1}`,
        groupId: group.id,
        center: { x: group.x, y, z: group.z },
        radiusMm: targetRadiusMm,
      });
    });
  }
  return targets;
}

function makeCandidate(
  id: string,
  family: Candidate["family"],
  entry: Vec3,
  tip: Vec3,
  targets: Target[],
  targetIndexById: Map<string, number>,
): Candidate {
  const targetDistancesMm: Record<string, number> = {};
  const coveredTargetIds: string[] = [];
  let coverMask = 0;
  for (const target of targets) {
    const distanceMm = segmentDistance(target.center, entry, tip);
    targetDistancesMm[target.id] = distanceMm;
    if (distanceMm <= target.radiusMm + 1e-9) {
      coveredTargetIds.push(target.id);
      coverMask |= 1 << targetIndexById.get(target.id)!;
    }
  }
  return {
    id,
    family,
    entry,
    tip,
    ...trajectoryAngles(entry, tip),
    ...candidateMargins(entry, tip),
    coveredTargetIds,
    coverMask,
    targetDistancesMm,
  };
}

function buildCandidates(targets: Target[]): Candidate[] {
  const targetIndexById = new Map(targets.map((target, index) => [target.id, index] as const));
  const candidates: Candidate[] = [];

  for (const group of groupSpecs) {
    const entry = { x: group.x, y: 1.5, z: group.z };
    const tip = { x: group.x, y: 6.9, z: group.z };
    candidates.push(makeCandidate(`P_${group.id}_designed`, "designed_optimal", entry, tip, targets, targetIndexById));
  }

  for (const group of groupSpecs) {
    const entry = { x: group.x + 0.03, y: 1.55, z: group.z - 0.03 };
    const tip = { x: group.x + 0.03, y: 6.85, z: group.z - 0.03 };
    candidates.push(makeCandidate(`P_${group.id}_redundant`, "redundant_group", entry, tip, targets, targetIndexById));
  }

  for (const group of groupSpecs) {
    for (let idx = 0; idx < groupTargetY.length - 1; idx += 1) {
      const y0 = groupTargetY[idx]!;
      const y1 = groupTargetY[idx + 1]!;
      const entry = { x: group.x, y: y0 - 0.08, z: group.z };
      const tip = { x: group.x, y: y1 + 0.08, z: group.z };
      candidates.push(makeCandidate(`P_${group.id}_pair_${idx + 1}_${idx + 2}`, "within_group_pair", entry, tip, targets, targetIndexById));
    }
  }

  for (const target of targets) {
    const entry = { x: target.center.x, y: target.center.y - 0.09, z: target.center.z };
    const tip = { x: target.center.x, y: target.center.y + 0.09, z: target.center.z };
    candidates.push(makeCandidate(`P_${target.id}_single`, "single_target", entry, tip, targets, targetIndexById));
  }

  for (let idx = 0; idx < 4; idx += 1) {
    const offset = (idx - 1.5) * 0.16;
    candidates.push(makeCandidate(
      `P_obstacle_cross_${idx + 1}`,
      "invalid_obstacle",
      { x: offset, y: 1.5, z: 0.0 },
      { x: offset, y: 6.9, z: 0.0 },
      targets,
      targetIndexById,
    ));
  }

  for (let idx = 0; idx < 4; idx += 1) {
    const sign = idx % 2 === 0 ? -1 : 1;
    candidates.push(makeCandidate(
      `P_boundary_out_${idx + 1}`,
      "invalid_boundary",
      { x: sign * 4.95, y: 1.2 + idx * 0.2, z: sign * 2.9 },
      { x: sign * 4.95, y: 6.9, z: sign * 2.9 },
      targets,
      targetIndexById,
    ));
  }

  for (const group of groupSpecs) {
    for (const dx of [-0.32, 0.32]) {
      candidates.push(makeCandidate(
        `P_${group.id}_near_miss_${dx < 0 ? "left" : "right"}`,
        "near_miss",
        { x: group.x + dx, y: 1.5, z: group.z },
        { x: group.x + dx, y: 6.9, z: group.z },
        targets,
        targetIndexById,
      ));
    }
  }

  return candidates;
}

function coveredGroupIds(candidate: Candidate, targetById: Map<string, Target>): string[] {
  return Array.from(new Set(candidate.coveredTargetIds.map((targetId) => targetById.get(targetId)!.groupId))).sort();
}

function combinationBetter(a: Candidate[], b: Candidate[] | null): boolean {
  if (!b) return true;
  const aMargin = Math.min(...a.map((candidate) => Math.min(candidate.minBoundaryMarginMm, candidate.minObstacleMarginMm)));
  const bMargin = Math.min(...b.map((candidate) => Math.min(candidate.minBoundaryMarginMm, candidate.minObstacleMarginMm)));
  if (Math.abs(aMargin - bMargin) > 1e-9) return aMargin > bMargin;
  const aDesigned = a.filter((candidate) => candidate.family === "designed_optimal").length;
  const bDesigned = b.filter((candidate) => candidate.family === "designed_optimal").length;
  if (aDesigned !== bDesigned) return aDesigned > bDesigned;
  return a.map((candidate) => candidate.id).join("|") < b.map((candidate) => candidate.id).join("|");
}

function findMinimumFullCover(candidates: Candidate[], targetCount: number): Candidate[] {
  const fullMask = (1 << targetCount) - 1;
  for (let k = 1; k <= candidates.length; k += 1) {
    let best: Candidate[] | null = null;
    const current: Candidate[] = [];

    function search(start: number, coverMask: number): void {
      if (current.length === k) {
        if (coverMask === fullMask && combinationBetter(current, best)) best = [...current];
        return;
      }
      const slots = k - current.length;
      for (let idx = start; idx <= candidates.length - slots; idx += 1) {
        current.push(candidates[idx]!);
        search(idx + 1, coverMask | candidates[idx]!.coverMask);
        current.pop();
      }
    }

    search(0, 0);
    if (best) return best;
  }
  return [];
}

function bestCoverageAtMostK(candidates: Candidate[], k: number): { coveredCount: number; coverMask: number; selectedIds: string[] } {
  let bestMask = 0;
  let bestIds: string[] = [];
  const current: Candidate[] = [];

  function maybeRecord(mask: number): void {
    const coveredCount = bitCount(mask);
    const bestCount = bitCount(bestMask);
    if (coveredCount > bestCount || (coveredCount === bestCount && current.length < bestIds.length)) {
      bestMask = mask;
      bestIds = current.map((candidate) => candidate.id);
    }
  }

  function search(start: number, mask: number): void {
    maybeRecord(mask);
    if (current.length === k) return;
    for (let idx = start; idx < candidates.length; idx += 1) {
      current.push(candidates[idx]!);
      search(idx + 1, mask | candidates[idx]!.coverMask);
      current.pop();
    }
  }

  search(0, 0);
  return { coveredCount: bitCount(bestMask), coverMask: bestMask, selectedIds: bestIds };
}

function bestCoverageExactlyK(candidates: Candidate[], k: number): { coveredCount: number; coverMask: number; selectedIds: string[] } {
  let bestMask = 0;
  let bestIds: string[] = [];
  const current: Candidate[] = [];

  function search(start: number, mask: number): void {
    if (current.length === k) {
      const coveredCount = bitCount(mask);
      const bestCount = bitCount(bestMask);
      if (coveredCount > bestCount) {
        bestMask = mask;
        bestIds = current.map((candidate) => candidate.id);
      }
      return;
    }
    const slots = k - current.length;
    for (let idx = start; idx <= candidates.length - slots; idx += 1) {
      current.push(candidates[idx]!);
      search(idx + 1, mask | candidates[idx]!.coverMask);
      current.pop();
    }
  }

  search(0, 0);
  return { coveredCount: bitCount(bestMask), coverMask: bestMask, selectedIds: bestIds };
}

function csvCell(value: unknown): string {
  const text = Array.isArray(value) ? value.join(";") : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;
}

async function main(): Promise<void> {
  const started = performance.now();
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-phantom-optimal-probe-count")!);
  const evidenceDir = path.resolve(root, flagString(flags, "evidence-out", "docs/evidence")!);
  const evidenceStem = flagString(flags, "evidence-stem", "2026-07-09-digital-phantom-known-optimal-probe-count")!;

  const targets = buildTargets();
  const targetById = new Map(targets.map((target) => [target.id, target] as const));
  const candidates = buildCandidates(targets);
  const validCandidates = candidates.filter((candidate) =>
    !candidate.violatesBoundary
    && !candidate.violatesObstacle
    && candidate.coveredTargetIds.length > 0);
  const selected = findMinimumFullCover(validCandidates, targets.length);
  const selectedMask = selected.reduce((mask, candidate) => mask | candidate.coverMask, 0);
  const coveredTargetCount = bitCount(selectedMask);
  const threeProbeBest = bestCoverageAtMostK(validCandidates, 3);
  const fiveProbeBest = bestCoverageAtMostK(validCandidates, 5);
  const fiveProbeExactBest = bestCoverageExactlyK(validCandidates, 5);
  const selectedBoundaryViolations = selected.filter((candidate) => candidate.violatesBoundary).length;
  const selectedObstacleViolations = selected.filter((candidate) => candidate.violatesObstacle).length;
  const validCandidateGroupSpans = validCandidates.map((candidate) => coveredGroupIds(candidate, targetById).length);
  const maxTargetGroupsPerValidCandidate = Math.max(...validCandidateGroupSpans);
  const targetGroupCount = new Set(targets.map((target) => target.groupId)).size;
  const groupLowerBoundProbeCount = Math.ceil(targetGroupCount / maxTargetGroupsPerValidCandidate);
  const selectedProbeCount = selected.length;
  const coverageRecall = coveredTargetCount / targets.length;
  const optimalProbeCountRecovery = Number(selectedProbeCount === groundTruthOptimalProbeCount && coverageRecall === 1);
  const overSelectionRate = Math.max(0, selectedProbeCount - groundTruthOptimalProbeCount) / groundTruthOptimalProbeCount;
  const underSelectionRate = Math.max(0, groundTruthOptimalProbeCount - selectedProbeCount) / groundTruthOptimalProbeCount;
  const elapsedMs = performance.now() - started;

  const selectedRows = selected.map((candidate) => ({
    candidateId: candidate.id,
    family: candidate.family,
    coveredTargets: candidate.coveredTargetIds,
    coveredGroups: coveredGroupIds(candidate, targetById),
    entry: candidate.entry,
    tip: candidate.tip,
    thetaDeg: candidate.thetaDeg,
    phiDeg: candidate.phiDeg,
    depthMm: candidate.depthMm,
    minBoundaryMarginMm: candidate.minBoundaryMarginMm,
    minObstacleMarginMm: candidate.minObstacleMarginMm,
  }));

  const summary = {
    generatedAt: new Date().toISOString(),
    runId: "digital-phantom-known-optimal-probe-count",
    method: {
      description: "Analytic 16-target phantom with four spatial target groups. Each valid candidate covers targets from at most one group; four designed candidates cover all groups, proving the optimal probe count is exactly four.",
      plannerSelector: "exact minimal set cover over valid, constraint-screened phantom candidates",
      coverageCriterion: "target is covered when its center-to-trajectory segment distance is <= target radius",
      boundaryCriterion: "candidate is boundary-valid when all sampled trajectory points remain inside the ellipsoid brain",
      obstacleCriterion: "candidate is obstacle-valid when sampled trajectory clearance to every obstacle is non-negative",
    },
    phantom: {
      brain,
      obstacles,
      targetCount: targets.length,
      targetGroupCount,
      targetsPerGroup: groupTargetY.length,
      targetRadiusMm,
      candidateCount: candidates.length,
      validCoveringCandidateCount: validCandidates.length,
      invalidBoundaryCandidateCount: candidates.filter((candidate) => candidate.violatesBoundary).length,
      invalidObstacleCandidateCount: candidates.filter((candidate) => candidate.violatesObstacle).length,
      probeClearanceMm,
    },
    groundTruth: {
      optimalProbeCount: groundTruthOptimalProbeCount,
      designedSolutionCandidateIds: groupSpecs.map((group) => `P_${group.id}_designed`),
      maxTargetGroupsPerValidCandidate,
      groupLowerBoundProbeCount,
      threeProbeFullCoveragePossible: threeProbeBest.coveredCount === targets.length,
      threeProbeMaxCoveredTargets: threeProbeBest.coveredCount,
      fiveProbeFullCoveragePossible: fiveProbeBest.coveredCount === targets.length,
      fiveProbeExactFullCoveragePossible: fiveProbeExactBest.coveredCount === targets.length,
    },
    metrics: {
      selectedProbeCount,
      optimalProbeCountRecovery,
      targetCoverageRecall: coverageRecall,
      coveredTargetCount,
      overSelectionRate,
      underSelectionRate,
      selectedBoundaryViolationRate: selected.length > 0 ? selectedBoundaryViolations / selected.length : 0,
      selectedObstacleViolationRate: selected.length > 0 ? selectedObstacleViolations / selected.length : 0,
      minSelectedBoundaryMarginMm: selected.length > 0 ? Math.min(...selected.map((candidate) => candidate.minBoundaryMarginMm)) : null,
      minSelectedObstacleMarginMm: selected.length > 0 ? Math.min(...selected.map((candidate) => candidate.minObstacleMarginMm)) : null,
      runtimeMs: elapsedMs,
    },
    selectedCandidates: selectedRows,
    targets: targets.map((target) => ({
      id: target.id,
      groupId: target.groupId,
      center: target.center,
      radiusMm: target.radiusMm,
      covered: (selectedMask & (1 << targets.indexOf(target))) !== 0,
    })),
    candidates: candidates.map((candidate) => ({
      id: candidate.id,
      family: candidate.family,
      valid: !candidate.violatesBoundary && !candidate.violatesObstacle,
      coveredTargetIds: candidate.coveredTargetIds,
      coveredGroups: coveredGroupIds(candidate, targetById),
      minBoundaryMarginMm: candidate.minBoundaryMarginMm,
      minObstacleMarginMm: candidate.minObstacleMarginMm,
      violatesBoundary: candidate.violatesBoundary,
      violatesObstacle: candidate.violatesObstacle,
    })),
  };

  const csvHeader = [
    "run_id",
    "target_count",
    "candidate_count",
    "valid_covering_candidate_count",
    "ground_truth_optimal_probe_count",
    "selected_probe_count",
    "optimal_probe_count_recovery",
    "target_coverage_recall",
    "over_selection_rate",
    "under_selection_rate",
    "obstacle_violation_rate",
    "boundary_violation_rate",
    "three_probe_max_covered_targets",
    "three_probe_full_coverage_possible",
    "five_probe_full_coverage_possible",
    "five_probe_exact_full_coverage_possible",
    "runtime_ms",
  ];
  const csvValues = [
    summary.runId,
    summary.phantom.targetCount,
    summary.phantom.candidateCount,
    summary.phantom.validCoveringCandidateCount,
    summary.groundTruth.optimalProbeCount,
    summary.metrics.selectedProbeCount,
    summary.metrics.optimalProbeCountRecovery,
    summary.metrics.targetCoverageRecall,
    summary.metrics.overSelectionRate,
    summary.metrics.underSelectionRate,
    summary.metrics.selectedObstacleViolationRate,
    summary.metrics.selectedBoundaryViolationRate,
    summary.groundTruth.threeProbeMaxCoveredTargets,
    summary.groundTruth.threeProbeFullCoveragePossible,
    summary.groundTruth.fiveProbeFullCoveragePossible,
    summary.groundTruth.fiveProbeExactFullCoveragePossible,
    summary.metrics.runtimeMs,
  ];
  const csv = `${csvHeader.join(",")}\n${csvValues.map(csvCell).join(",")}\n`;

  const md = [
    "# Digital Phantom Known-Optimal Probe Count Validation",
    "",
    `Generated: ${summary.generatedAt}`,
    "",
    "This experiment adds a digital phantom with a known optimal answer for automatic probe-count planning. Sixteen targets are placed into four separated spatial groups. Every valid candidate trajectory covers targets from at most one group, while four designed trajectories cover all four groups. Therefore the lower bound and feasible solution match: the ground-truth optimal probe count is exactly 4.",
    "",
    "## Ground-truth proof",
    "",
    `- Target count: ${summary.phantom.targetCount}`,
    `- Target groups: ${summary.phantom.targetGroupCount}`,
    `- Targets per group: ${summary.phantom.targetsPerGroup}`,
    `- Candidate count: ${summary.phantom.candidateCount}`,
    `- Valid covering candidates: ${summary.phantom.validCoveringCandidateCount}`,
    `- Max target groups covered by any valid candidate: ${summary.groundTruth.maxTargetGroupsPerValidCandidate}`,
    `- Group lower-bound probe count: ${summary.groundTruth.groupLowerBoundProbeCount}`,
    `- Designed feasible solution probe count: ${summary.groundTruth.optimalProbeCount}`,
    `- Best coverage using at most 3 probes: ${summary.groundTruth.threeProbeMaxCoveredTargets}/${summary.phantom.targetCount}`,
    `- Full coverage using at most 3 probes possible: ${summary.groundTruth.threeProbeFullCoveragePossible}`,
    `- Full coverage using at most 5 probes possible: ${summary.groundTruth.fiveProbeFullCoveragePossible}`,
    `- Full coverage using exactly 5 probes possible: ${summary.groundTruth.fiveProbeExactFullCoveragePossible}`,
    "",
    "## Metrics",
    "",
    "| Metric | Value |",
    "| --- | ---: |",
    `| optimal probe count recovery | ${summary.metrics.optimalProbeCountRecovery} |`,
    `| ground-truth optimal probe count | ${summary.groundTruth.optimalProbeCount} |`,
    `| selected probe count | ${summary.metrics.selectedProbeCount} |`,
    `| target coverage recall | ${fmt(summary.metrics.targetCoverageRecall * 100, 1)}% |`,
    `| over-selection rate | ${fmt(summary.metrics.overSelectionRate * 100, 1)}% |`,
    `| under-selection rate | ${fmt(summary.metrics.underSelectionRate * 100, 1)}% |`,
    `| obstacle violation rate | ${fmt(summary.metrics.selectedObstacleViolationRate * 100, 1)}% |`,
    `| boundary violation rate | ${fmt(summary.metrics.selectedBoundaryViolationRate * 100, 1)}% |`,
    `| min selected obstacle margin | ${fmt(summary.metrics.minSelectedObstacleMarginMm)} mm |`,
    `| min selected boundary margin | ${fmt(summary.metrics.minSelectedBoundaryMarginMm)} mm |`,
    `| runtime | ${fmt(summary.metrics.runtimeMs, 1)} ms |`,
    "",
    "## Selected probes",
    "",
    "| Candidate | Family | Covered targets | Entry mm | Tip mm | Theta deg | Phi deg | Depth mm | Boundary margin mm | Obstacle margin mm |",
    "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ...selectedRows.map((row) =>
      `| ${row.candidateId} | ${row.family} | ${row.coveredTargets.length} | (${fmt(row.entry.x)}, ${fmt(row.entry.y)}, ${fmt(row.entry.z)}) | (${fmt(row.tip.x)}, ${fmt(row.tip.y)}, ${fmt(row.tip.z)}) | ${fmt(row.thetaDeg, 1)} | ${fmt(row.phiDeg, 1)} | ${fmt(row.depthMm)} | ${fmt(row.minBoundaryMarginMm)} | ${fmt(row.minObstacleMarginMm)} |`,
    ),
    "",
  ].join("\n");

  await writeJson(path.join(outDir, "known-optimal-probe-count.json"), summary);
  await writeText(path.join(outDir, "known-optimal-probe-count.md"), `${md}\n`);
  await writeText(path.join(outDir, "known-optimal-probe-count.csv"), csv);
  await writeJson(path.join(evidenceDir, `${evidenceStem}.json`), summary);
  await writeText(path.join(evidenceDir, `${evidenceStem}.md`), `${md}\n`);
  await writeText(path.join(evidenceDir, `${evidenceStem}.csv`), csv);

  console.log(`Wrote known-optimal phantom report to ${path.join(outDir, "known-optimal-probe-count.md")}`);
  console.log(`Wrote evidence copy to ${path.join(evidenceDir, `${evidenceStem}.md`)}`);

  if (
    summary.metrics.optimalProbeCountRecovery !== 1
    || summary.metrics.targetCoverageRecall !== 1
    || summary.metrics.overSelectionRate !== 0
    || summary.metrics.underSelectionRate !== 0
    || summary.metrics.selectedBoundaryViolationRate !== 0
    || summary.metrics.selectedObstacleViolationRate !== 0
    || summary.groundTruth.threeProbeFullCoveragePossible
    || !summary.groundTruth.fiveProbeExactFullCoveragePossible
  ) {
    process.exitCode = 2;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
