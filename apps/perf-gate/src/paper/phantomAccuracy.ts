import path from "node:path";
import { fmt, flagString, parseFlags, percentile, repoRoot, writeJson, writeText } from "./common.js";

interface Vec3 { x: number; y: number; z: number }
interface Target { id: string; center: Vec3; radiusMm: number }
interface Obstacle { id: string; center: Vec3; radiusMm: number }
interface Candidate { entry: Vec3; tip: Vec3; depthMm: number; thetaDeg: number; phiDeg: number; minBoundaryMarginMm: number; minObstacleMarginMm: number; violatesBoundary: boolean; violatesObstacle: boolean }

const brain = { center: { x: 0, y: 4.5, z: 0 }, radii: { x: 4.2, y: 4.6, z: 3.2 } };
const obstacles: Obstacle[] = [
  { id: "ventricle_left", center: { x: -0.75, y: 3.4, z: 0.15 }, radiusMm: 0.42 },
  { id: "ventricle_right", center: { x: 0.75, y: 3.4, z: -0.15 }, radiusMm: 0.42 },
  { id: "central_vessel", center: { x: 0.05, y: 5.2, z: 0.0 }, radiusMm: 0.28 },
  { id: "dorsal_risk", center: { x: 1.8, y: 2.4, z: 1.0 }, radiusMm: 0.35 },
];
const targets: Target[] = [
  { id: "T01_CA1_like", center: { x: -1.8, y: 4.5, z: 1.0 }, radiusMm: 0.42 },
  { id: "T02_CA3_like", center: { x: -2.2, y: 4.2, z: -0.7 }, radiusMm: 0.42 },
  { id: "T03_DG_like", center: { x: -1.2, y: 5.1, z: 0.5 }, radiusMm: 0.42 },
  { id: "T04_VPM_like", center: { x: 1.5, y: 5.2, z: -0.9 }, radiusMm: 0.42 },
  { id: "T05_LGd_like", center: { x: 1.9, y: 4.8, z: 0.8 }, radiusMm: 0.42 },
  { id: "T06_VISp_like", center: { x: 2.5, y: 3.3, z: 1.4 }, radiusMm: 0.45 },
  { id: "T07_VISrl_like", center: { x: 2.7, y: 3.6, z: -1.2 }, radiusMm: 0.45 },
  { id: "T08_LP_like", center: { x: 0.9, y: 5.7, z: 1.3 }, radiusMm: 0.42 },
  { id: "T09_TH_like", center: { x: -0.9, y: 5.8, z: -1.4 }, radiusMm: 0.42 },
  { id: "T10_RSP_like", center: { x: 0.3, y: 3.1, z: 2.0 }, radiusMm: 0.45 },
  { id: "T11_ACA_like", center: { x: -0.4, y: 2.7, z: -2.0 }, radiusMm: 0.45 },
  { id: "T12_CP_like", center: { x: 2.8, y: 5.1, z: 0.1 }, radiusMm: 0.42 },
  { id: "T13_MOs_boundary_like", center: { x: -2.9, y: 2.4, z: 0.0 }, radiusMm: 0.42 },
  { id: "T14_ACB_boundary_like", center: { x: 0.1, y: 6.9, z: -0.4 }, radiusMm: 0.42 },
  { id: "T15_deep_safe", center: { x: -1.6, y: 6.2, z: 1.6 }, radiusMm: 0.42 },
  { id: "T16_lateral_safe", center: { x: 3.0, y: 4.1, z: -0.5 }, radiusMm: 0.42 },
];

function range(min: number, max: number, step: number): number[] {
  const out = [];
  for (let value = min; value <= max + 1e-9; value += step) out.push(Number(value.toFixed(6)));
  return out;
}

function add(a: Vec3, b: Vec3): Vec3 { return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }; }
function mul(a: Vec3, scale: number): Vec3 { return { x: a.x * scale, y: a.y * scale, z: a.z * scale }; }
function dist(a: Vec3, b: Vec3): number { return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z); }

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

function obstacleMargin(p: Vec3, clearanceMm: number): number {
  return Math.min(...obstacles.map((obstacle) => dist(p, obstacle.center) - obstacle.radiusMm - clearanceMm));
}

function candidateMargins(entry: Vec3, tip: Vec3, clearanceMm: number): Pick<Candidate, "minBoundaryMarginMm" | "minObstacleMarginMm" | "violatesBoundary" | "violatesObstacle"> {
  const steps = 80;
  let minBoundaryMarginMm = Infinity;
  let minObstacleMarginMm = Infinity;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const p = add(entry, mul({ x: tip.x - entry.x, y: tip.y - entry.y, z: tip.z - entry.z }, t));
    minBoundaryMarginMm = Math.min(minBoundaryMarginMm, boundaryMargin(p));
    minObstacleMarginMm = Math.min(minObstacleMarginMm, obstacleMargin(p, clearanceMm));
  }
  return {
    minBoundaryMarginMm,
    minObstacleMarginMm,
    violatesBoundary: minBoundaryMarginMm < -1e-6,
    violatesObstacle: minObstacleMarginMm < -1e-6,
  };
}

function generateCandidates(clearanceMm: number): Candidate[] {
  const candidates: Candidate[] = [];
  for (const entryX of range(-3.6, 3.6, 0.4)) {
    for (const entryZ of range(-2.6, 2.6, 0.4)) {
      const entry = { x: entryX, y: 0.35, z: entryZ };
      if (boundaryMargin(entry) < -0.05) continue;
      for (const thetaDeg of range(0, 35, 5)) {
        const theta = thetaDeg * Math.PI / 180;
        for (const phiDeg of range(0, 315, 45)) {
          const phi = phiDeg * Math.PI / 180;
          const dir = { x: Math.sin(theta) * Math.cos(phi), y: Math.cos(theta), z: Math.sin(theta) * Math.sin(phi) };
          for (const depthMm of range(2.5, 8.5, 0.25)) {
            const tip = add(entry, mul(dir, depthMm));
            const margins = candidateMargins(entry, tip, clearanceMm);
            if (!margins.violatesBoundary && !margins.violatesObstacle) {
              candidates.push({ entry, tip, depthMm, thetaDeg, phiDeg, ...margins });
            }
          }
        }
      }
    }
  }
  return candidates;
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-phantom-accuracy")!);
  const clearanceMm = 0.2;
  const candidates = generateCandidates(clearanceMm);
  const rows = targets.map((target) => {
    const ranked = candidates
      .map((candidate) => ({ candidate, targetDistanceMm: dist(candidate.tip, target.center) }))
      .sort((a, b) => a.targetDistanceMm - b.targetDistanceMm);
    const best = ranked[0]!;
    const covered = best.targetDistanceMm <= target.radiusMm;
    return {
      targetId: target.id,
      covered,
      targetRadiusMm: target.radiusMm,
      targetDistanceMm: best.targetDistanceMm,
      boundaryViolation: best.candidate.violatesBoundary,
      obstacleViolation: best.candidate.violatesObstacle,
      minBoundaryMarginMm: best.candidate.minBoundaryMarginMm,
      minObstacleMarginMm: best.candidate.minObstacleMarginMm,
      entry: best.candidate.entry,
      tip: best.candidate.tip,
      thetaDeg: best.candidate.thetaDeg,
      phiDeg: best.candidate.phiDeg,
      depthMm: best.candidate.depthMm,
    };
  });
  const distances = rows.map((row) => row.targetDistanceMm);
  const summary = {
    generatedAt: new Date().toISOString(),
    phantom: {
      brain,
      obstacleCount: obstacles.length,
      targetCount: targets.length,
      candidateCount: candidates.length,
      clearanceMm,
      targetRadiusRangeMm: [Math.min(...targets.map((target) => target.radiusMm)), Math.max(...targets.map((target) => target.radiusMm))],
    },
    metrics: {
      coverageRecall: rows.filter((row) => row.covered).length / rows.length,
      selectedBoundaryViolationRate: rows.filter((row) => row.boundaryViolation).length / rows.length,
      selectedObstacleViolationRate: rows.filter((row) => row.obstacleViolation).length / rows.length,
      targetDistanceMedianMm: percentile(distances, 50),
      targetDistanceP95Mm: percentile(distances, 95),
      targetDistanceMaxMm: Math.max(...distances),
      minBoundaryMarginMm: Math.min(...rows.map((row) => row.minBoundaryMarginMm)),
      minObstacleMarginMm: Math.min(...rows.map((row) => row.minObstacleMarginMm)),
    },
    targets: rows,
  };
  const lines = [
    "# Digital Phantom Accuracy Validation",
    "",
    `Generated: ${summary.generatedAt}`,
    "",
    "This analytic phantom uses a fixed ellipsoid brain, known spherical obstacles, known target centers, and a deterministic entry/angle/depth candidate grid. It validates planner geometry and constraint behavior; it is not animal or histology validation.",
    "",
    "## Metrics",
    "",
    `- Candidate count: ${summary.phantom.candidateCount}`,
    `- Targets: ${summary.phantom.targetCount}`,
    `- Coverage recall: ${fmt(summary.metrics.coverageRecall * 100, 1)}%`,
    `- Target distance median: ${fmt(summary.metrics.targetDistanceMedianMm)} mm`,
    `- Target distance p95: ${fmt(summary.metrics.targetDistanceP95Mm)} mm`,
    `- Target distance max: ${fmt(summary.metrics.targetDistanceMaxMm)} mm`,
    `- Selected boundary violation rate: ${fmt(summary.metrics.selectedBoundaryViolationRate * 100, 1)}%`,
    `- Selected obstacle violation rate: ${fmt(summary.metrics.selectedObstacleViolationRate * 100, 1)}%`,
    `- Minimum boundary margin: ${fmt(summary.metrics.minBoundaryMarginMm)} mm`,
    `- Minimum obstacle margin: ${fmt(summary.metrics.minObstacleMarginMm)} mm`,
    "",
    "## Targets",
    "",
    "| Target | Covered | Target distance mm | Min boundary margin mm | Min obstacle margin mm | Theta | Phi | Depth mm |",
    "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...rows.map((row) =>
      `| ${row.targetId} | ${row.covered} | ${fmt(row.targetDistanceMm)} | ${fmt(row.minBoundaryMarginMm)} | ${fmt(row.minObstacleMarginMm)} | ${fmt(row.thetaDeg, 0)} | ${fmt(row.phiDeg, 0)} | ${fmt(row.depthMm)} |`,
    ),
    "",
  ];
  await writeJson(path.join(outDir, "phantom-accuracy.json"), summary);
  await writeText(path.join(outDir, "phantom-accuracy.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote phantom accuracy report to ${path.join(outDir, "phantom-accuracy.md")}`);
  if (summary.metrics.coverageRecall < 0.9 || summary.metrics.selectedBoundaryViolationRate > 0 || summary.metrics.selectedObstacleViolationRate > 0) {
    process.exitCode = 2;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
