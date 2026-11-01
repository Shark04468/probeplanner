import path from "node:path";
import type { JsonObject } from "../types.js";
import { loadManifest } from "../cases/loadReplay.js";
import {
  cloneJson,
  ensureObject,
  flagNumber,
  flagString,
  numberArray,
  numberOrNull,
  parseFlags,
  postOptimize,
  repoRoot,
  stringArray,
  withTask,
  writeJson,
  writeText,
} from "./common.js";

const BREGMA_UM = [5400.0, 440.0, 5700.0] as const;
const ENDPOINT_TOLERANCE_UM = 1e-6;
const DIRECTION_TOLERANCE = 1e-4;

function triplet(value: unknown): number[] | null {
  const values = numberArray(value);
  return values.length >= 3 ? values.slice(0, 3) : null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function deriveUnityPayload(probe: JsonObject): JsonObject | null {
  const entry = triplet(probe.entry_xyz) ?? triplet(probe.entry);
  const tip = triplet(probe.tip_xyz) ?? triplet(probe.tip);
  if (!entry || !tip) return null;
  const delta = [tip[0]! - entry[0]!, tip[1]! - entry[1]!, tip[2]! - entry[2]!];
  const depthUm = Math.hypot(delta[0]!, delta[1]!, delta[2]!);
  if (!Number.isFinite(depthUm) || depthUm <= 1e-6) return null;
  const dir = delta.map((value) => value / depthUm);
  const thetaDeg = Math.acos(clamp(dir[1]!, -1, 1)) * 180 / Math.PI;
  const phiDeg = Math.atan2(dir[2]!, dir[0]!) * 180 / Math.PI;
  const riskDiagnostics = ensureObject(probe.riskDiagnostics);
  return {
    ap_mm: (BREGMA_UM[0] - entry[0]!) / 1000,
    ml_mm: (entry[2]! - BREGMA_UM[2]) / 1000,
    theta_deg: thetaDeg,
    phi_deg: phiDeg,
    depthUm,
    entryDvCorr_mm: (entry[1]! - BREGMA_UM[1]) / 1000,
    nativeEndpointGeometry: true,
    entry_xyz: entry,
    tip_xyz: tip,
    entry,
    tip,
    dir,
    shanks: Math.max(1, Math.round(Number(probe.shanks ?? probe.shankCount ?? 1))),
    shankCount: Math.max(1, Math.round(Number(probe.shanks ?? probe.shankCount ?? 1))),
    shankPitchUm: numberOrNull(probe.shankPitchUm ?? probe.shankSpacingUm) ?? 0,
    shankSpacingUm: numberOrNull(probe.shankPitchUm ?? probe.shankSpacingUm) ?? 0,
    shankWidthUm: numberOrNull(probe.shankWidthUm) ?? 70,
    radiusUm: numberOrNull(probe.radiusUm ?? probe.probeRadiusUm) ?? 35,
    probeRadiusUm: numberOrNull(probe.radiusUm ?? probe.probeRadiusUm) ?? 35,
    coveredRegions: stringArray(probe.coveredRegions ?? probe.regionsHit),
    minRiskDistanceMm: numberOrNull(probe.minRiskDistanceMm) ?? numberOrNull(riskDiagnostics.minDistanceMm),
  };
}

function maxAbsDelta(a: unknown, b: unknown): number | null {
  const av = triplet(a);
  const bv = triplet(b);
  if (!av || !bv) return null;
  return Math.max(...av.map((value, index) => Math.abs(value - (bv[index] ?? Number.NaN))));
}

function classDistances(probe: JsonObject): Record<string, number> {
  const out: Record<string, number> = {};
  const riskDiagnostics = ensureObject(probe.riskDiagnostics);
  const distances = ensureObject(riskDiagnostics.classMinDistanceMm);
  for (const [key, value] of Object.entries(distances)) {
    const n = Number(value);
    if (Number.isFinite(n)) out[key] = n;
  }
  return out;
}

async function main(): Promise<void> {
  const flags = parseFlags(process.argv.slice(2));
  const root = repoRoot();
  const backendUrl = flagString(flags, "backend-url", "http://127.0.0.1:5000")!;
  const outDir = path.resolve(root, flagString(flags, "out", "reports/paper-trajectory-audit")!);
  const trajectoryLimit = flagNumber(flags, "trajectory-limit", 20);
  const timeoutMs = flagNumber(flags, "timeout-ms", 900_000);
  const manifest = await loadManifest(root);
  const candidateCases = manifest.cases.filter((item) =>
    (item.source === "repo" || item.source === "local" || item.source === "paper_replay")
    && !item.label.includes("MOs")
    && !item.label.includes("ACB"),
  );
  const trajectories = [];
  const caseRuns = [];

  for (const replayCase of candidateCases) {
    if (trajectories.length >= trajectoryLimit) break;
    const request = withTask(cloneJson(replayCase.request), `paper_traj_${replayCase.id.slice(0, 8)}`);
    console.log(`[trajectory] ${replayCase.id.slice(0, 8)} ${replayCase.label}`);
    const result = await postOptimize(backendUrl, request, timeoutMs);
    const response = result.response;
    const selected = Array.isArray(response?.selected) ? response.selected as JsonObject[] : [];
    caseRuns.push({
      caseId: replayCase.id,
      label: replayCase.label,
      ok: result.ok,
      elapsedMs: result.elapsedMs,
      selectedCount: selected.length,
      uncovered: stringArray(response?.uncovered),
      candidateCount: numberOrNull(response?.candidateCount),
    });
    for (let i = 0; i < selected.length && trajectories.length < trajectoryLimit; i += 1) {
      const probe = selected[i]!;
      const unityPayload = deriveUnityPayload(probe);
      const entryDeltaUm = maxAbsDelta(probe.entry_xyz, unityPayload?.entry_xyz);
      const tipDeltaUm = maxAbsDelta(probe.tip_xyz, unityPayload?.tip_xyz);
      const dirDelta = maxAbsDelta(probe.dir, unityPayload?.dir);
      const distances = classDistances(probe);
      trajectories.push({
        index: trajectories.length,
        caseId: replayCase.id,
        label: replayCase.label,
        targetKeys: replayCase.targetKeys,
        selectedIndex: i,
        native: {
          entry_xyz: probe.entry_xyz ?? null,
          tip_xyz: probe.tip_xyz ?? null,
          dir: probe.dir ?? null,
          shankCount: probe.shankCount ?? probe.shanks ?? null,
          shankOffsetsUm: probe.shankOffsetsUm ?? null,
          coveredRegions: probe.coveredRegions ?? [],
          riskDiagnostics: probe.riskDiagnostics ?? null,
        },
        unityPayload,
        checks: {
          hasNativeEndpoints: Boolean(triplet(probe.entry_xyz) && triplet(probe.tip_xyz) && triplet(probe.dir)),
          hasUnityEndpoints: Boolean(unityPayload),
          entryDeltaUm,
          tipDeltaUm,
          dirDelta,
          endpointMatch:
            entryDeltaUm !== null
            && tipDeltaUm !== null
            && entryDeltaUm <= ENDPOINT_TOLERANCE_UM
            && tipDeltaUm <= ENDPOINT_TOLERANCE_UM,
          dirMatch: dirDelta !== null && dirDelta <= DIRECTION_TOLERANCE,
        },
        minDistancesMm: {
          skull: distances.skull ?? null,
          brain_surface: distances.brain_surface ?? null,
          ventricle: distances.ventricle ?? null,
          vessel: distances.vessel ?? null,
          sulcus: distances.sulcus ?? null,
          global: numberOrNull(ensureObject(probe.riskDiagnostics).minDistanceMm),
        },
      });
    }
  }

  const failures = trajectories.filter((item) => !item.checks.endpointMatch || !item.checks.dirMatch);
  const summary = {
    generatedAt: new Date().toISOString(),
    backendUrl,
    requestedTrajectoryLimit: trajectoryLimit,
    collectedTrajectoryCount: trajectories.length,
    failedConsistencyCount: failures.length,
    caseRuns,
    trajectories,
  };

  const lines = [
    "# Trajectory Boundary and UI Payload Consistency Audit",
    "",
    `Generated: ${summary.generatedAt}`,
    `Backend: ${backendUrl}`,
    `Collected trajectories: ${trajectories.length}/${trajectoryLimit}`,
    `Consistency failures: ${failures.length}`,
    `Endpoint tolerance: ${ENDPOINT_TOLERANCE_UM} um`,
    `Direction tolerance: ${DIRECTION_TOLERANCE}`,
    "",
    "## Trajectories",
    "",
    "| # | Case | Targets | Entry match | Dir match | Skull mm | Brain surface mm | Ventricle mm |",
    "| ---: | --- | --- | --- | --- | ---: | ---: | ---: |",
    ...trajectories.map((item) =>
      `| ${item.index} | ${item.caseId.slice(0, 8)} ${item.label} | ${item.targetKeys.join("+")} | ${item.checks.endpointMatch} | ${item.checks.dirMatch} | ${item.minDistancesMm.skull ?? "n/a"} | ${item.minDistancesMm.brain_surface ?? "n/a"} | ${item.minDistancesMm.ventricle ?? "n/a"} |`,
    ),
    "",
  ];

  await writeJson(path.join(outDir, "trajectory-audit.json"), summary);
  await writeText(path.join(outDir, "trajectory-audit.md"), `${lines.join("\n")}\n`);
  console.log(`Wrote trajectory audit to ${path.join(outDir, "trajectory-audit.md")}`);
  if (trajectories.length < trajectoryLimit || failures.length > 0) process.exitCode = 2;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
