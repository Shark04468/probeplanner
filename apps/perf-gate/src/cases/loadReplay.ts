import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { DEFAULT_LOCAL_REPLAY_IDS, DEFAULT_REPO_REPLAY_IDS } from "../config/defaults.js";
import type { CaseManifest, JsonObject, LoadedCaseManifest, ReplayCase } from "../types.js";
import { sha256 } from "../util/hash.js";

interface NativeTargetLookup {
  sourcePath: string;
  keys: Set<string>;
}

const NATIVE_UNSUPPORTED_TARGET_KEYS = new Set(["ADP", "FRP1", "PFC", "PTLP"]);

function userHome(): string {
  return process.env.USERPROFILE || process.env.HOME || "";
}

function requestFromReplay(raw: unknown): JsonObject {
  const obj = raw && typeof raw === "object" ? (raw as JsonObject) : {};
  const request = obj.request && typeof obj.request === "object" ? { ...(obj.request as JsonObject) } : null;
  if (!request) throw new Error("Replay file does not contain a request object");
  delete request.taskId;
  delete request.requestId;
  delete request.jobId;
  return request;
}

function targetKeysOf(request: JsonObject): string[] {
  const keys = Array.isArray(request.targetKeys) ? request.targetKeys : [];
  return keys.map((x) => String(x)).filter(Boolean);
}

function normalizeRegionKey(value: string): string {
  return value.trim().toUpperCase();
}

function isAtlasBackedTarget(lookup: NativeTargetLookup, key: string): boolean {
  const normalized = normalizeRegionKey(key);
  if (!normalized) return false;
  if (NATIVE_UNSUPPORTED_TARGET_KEYS.has(normalized)) return false;
  if (lookup.keys.has(normalized)) return true;
  for (const candidate of lookup.keys) {
    if (candidate.startsWith(normalized)) return true;
  }
  return false;
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item)).filter((item) => item.trim())
    : [];
}

export async function loadNativeTargetLookup(regionMapPath: string): Promise<NativeTargetLookup> {
  const parsed = JSON.parse(await readFile(regionMapPath, "utf8")) as JsonObject;
  const keys = new Set<string>();
  if (Array.isArray(parsed.entries)) {
    for (const rawEntry of parsed.entries) {
      if (!rawEntry || typeof rawEntry !== "object") continue;
      const entry = rawEntry as JsonObject;
      for (const value of [
        entry.canonicalAcronym,
        entry.canonical_acronym,
        entry.acronym,
        ...stringValues(entry.aliases),
      ]) {
        if (typeof value === "string" && value.trim()) keys.add(normalizeRegionKey(value));
      }
    }
  } else {
    for (const key of Object.keys(parsed)) keys.add(normalizeRegionKey(key));
  }
  if (keys.size <= 0) {
    throw new Error(`Native region map does not contain target keys: ${regionMapPath}`);
  }
  return { sourcePath: regionMapPath, keys };
}

function excludedTargetKeysOf(request: JsonObject): string[] {
  const ctx = request.clinicalContext && typeof request.clinicalContext === "object"
    ? (request.clinicalContext as JsonObject)
    : {};
  const keys = Array.isArray(ctx.nativeExcludedFallbackTargetKeys)
    ? ctx.nativeExcludedFallbackTargetKeys
    : [];
  return keys.map((x) => String(x)).filter(Boolean);
}

function normalizeNativeReplayRequest(request: JsonObject, targetLookup?: NativeTargetLookup): JsonObject {
  const out = cloneJson(request);
  const originalTargets = targetKeysOf(out);
  if (targetLookup) {
    const excluded = originalTargets.filter((key) => !isAtlasBackedTarget(targetLookup, key));
    const retained = originalTargets.filter((key) => isAtlasBackedTarget(targetLookup, key));
    if (excluded.length > 0) {
      if (retained.length <= 0) {
        throw new Error(
          `Replay case has no atlas-backed target keys after native region-map filtering (${excluded.join(", ")}).`,
        );
      }
      out.targetKeys = retained;
      const clinicalContext = out.clinicalContext && typeof out.clinicalContext === "object"
        ? { ...(out.clinicalContext as JsonObject) }
        : {};
      out.clinicalContext = {
        ...clinicalContext,
        nativeExcludedFallbackTargetKeys: excluded,
        nativeExcludedFallbackTargetReason: "not atlas-backed in native region index; excluded from real coverage gate",
        nativeTargetLookupSource: "artifact://region-map",
      };
    }
  }
  const params = out.params && typeof out.params === "object"
    ? { ...(out.params as JsonObject) }
    : {};
  out.params = {
    ...params,
    strictAtlasTargets: true,
    allowFallbackTargetCoverage: false,
  };
  const constraintModes = out.constraintModes && typeof out.constraintModes === "object"
    ? { ...(out.constraintModes as JsonObject) }
    : {};
  out.constraintModes = {
    ...constraintModes,
    brainBounds: "enforce",
    lambdaBoundary: "enforce",
    continuousRiskDistance: "enforce",
    minEntrySpacing: "enforce",
    minClearance: "enforce",
  };
  return out;
}

function algorithmOf(request: JsonObject): string {
  const params = request.params && typeof request.params === "object" ? (request.params as JsonObject) : {};
  return String(params.algorithm || "greedy");
}

function labelOf(id: string, request: JsonObject): string {
  const ctx = request.clinicalContext && typeof request.clinicalContext === "object"
    ? (request.clinicalContext as JsonObject)
    : {};
  const replayCase = String(ctx.replayCase || "").trim();
  if (replayCase) return replayCase;
  return `${targetKeysOf(request).join("+") || "case"}-${id.slice(0, 8)}`;
}

function manifestSourcePath(item: ReplayCase): string {
  if (item.source === "adversarial") return item.sourcePath;
  if (item.source === "paper_replay" || item.source === "stress" || item.source === "ablation") return item.sourcePath;
  return `${item.source}-replay:${item.id}`;
}

async function loadReplayCase(id: string, source: "repo" | "local", sourcePath: string, targetLookup?: NativeTargetLookup): Promise<ReplayCase> {
  const text = await readFile(sourcePath, "utf8");
  const parsed = JSON.parse(text) as JsonObject;
  const request = normalizeNativeReplayRequest(requestFromReplay(parsed), targetLookup);
  return {
    id,
    label: labelOf(id, request),
    source,
    sourcePath,
    targetKeys: targetKeysOf(request),
    excludedTargetKeys: excludedTargetKeysOf(request),
    algorithm: algorithmOf(request),
    request,
  };
}

const SDF_ADVERSARIAL_CLASSES = [
  { riskClass: "skull", label: "sdf_skull_edge", thresholdMm: 25.0 },
  { riskClass: "brain_surface", label: "sdf_brain_surface_edge", thresholdMm: 25.0 },
  { riskClass: "ventricle", label: "sdf_ventricle_edge", thresholdMm: 25.0 },
  { riskClass: "vessel", label: "sdf_vessel_edge", thresholdMm: 25.0 },
  { riskClass: "sulcus", label: "sdf_sulcus_edge", thresholdMm: 25.0 },
];

const SDF_RISK_CLASSES = SDF_ADVERSARIAL_CLASSES.map((item) => item.riskClass);
const SDF_REJECT_THRESHOLDS_MM = [10, 12, 15, 18, 20, 22, 25, 28, 30, 35] as const;
const SDF_PASS_THRESHOLDS_MM = [0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045] as const;

const PAPER_TARGET_POOL = [
  "CA1",
  "CA3",
  "DG",
  "MOs",
  "VISp",
  "VISrl",
  "VISal",
  "VISpm",
  "LGd",
  "VPM",
  "LP",
  "CP",
  "ACB",
  "TH",
  "RSP",
] as const;

const PAPER_REPLAY_TEMPLATES: Array<{
  targets: readonly string[];
  algorithm: string;
  drccSolver?: string;
  shanks: number;
  maxAngle: number;
  coarseStepUm: number;
}> = [
  { targets: ["CA1"], algorithm: "greedy", shanks: 4, maxAngle: 15, coarseStepUm: 100 },
  { targets: ["VPM"], algorithm: "greedy", shanks: 4, maxAngle: 15, coarseStepUm: 100 },
  { targets: ["LP", "CA3"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["VISpm", "CA1"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["VISp", "LP"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["CA3", "VPM"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["TH", "VISp"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["DG", "VPM", "CA3"], algorithm: "drcc", drccSolver: "exact_pool", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["LP", "CA1", "VISrl"], algorithm: "drcc", drccSolver: "heuristic", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["VISp", "VPM", "RSP"], algorithm: "drcc", drccSolver: "heuristic", shanks: 1, maxAngle: 18, coarseStepUm: 150 },
  { targets: ["LGd", "CA1", "VISpm"], algorithm: "drcc", drccSolver: "exact_pool", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
  { targets: ["CA1", "LGd", "CA3", "VPM"], algorithm: "drcc", drccSolver: "hybrid", shanks: 4, maxAngle: 18, coarseStepUm: 150 },
  { targets: ["LP", "DG", "VPM", "CA3", "LGd"], algorithm: "drcc", drccSolver: "cvar_relax", shanks: 1, maxAngle: 18, coarseStepUm: 150 },
  { targets: ["VISpm", "TH"], algorithm: "exact", shanks: 1, maxAngle: 15, coarseStepUm: 150 },
];

const PAPER_REPLAY_CASE_COUNT = 100;
const STRESS_CASE_COUNT = 500;

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function targetPool(targetLookup?: NativeTargetLookup): string[] {
  const retained = PAPER_TARGET_POOL.filter((key) => !targetLookup || isAtlasBackedTarget(targetLookup, key));
  if (retained.length > 0) return retained;
  return ["CA1"];
}

function pickTargets(pool: string[], seed: number, size: number): string[] {
  const out: string[] = [];
  let cursor = (seed * 7 + 3) % pool.length;
  for (let i = 0; out.length < size && i < pool.length * 2; i += 1) {
    const key = pool[cursor % pool.length];
    if (key && !out.includes(key)) out.push(key);
    cursor += 5 + (seed % 4);
  }
  return out.length > 0 ? out : [pool[0] || "CA1"];
}

function paperCaseRequest(base: ReplayCase, options: {
  label: string;
  targets: string[];
  algorithm?: string;
  drccSolver?: string;
  shanks?: number;
  maxAngle?: number;
  nativeCandidateLimit?: number;
  coarseStepUm?: number;
  constraintModes?: Record<string, string>;
  riskThresholdScale?: number;
}): JsonObject {
  const request = cloneJson(base.request);
  request.targetKeys = options.targets;
  const params = request.params && typeof request.params === "object"
    ? { ...(request.params as JsonObject) }
    : {};
  const riskThresholds = params.riskThresholdsMm && typeof params.riskThresholdsMm === "object"
    ? { ...(params.riskThresholdsMm as JsonObject) }
    : {};
  if (Number.isFinite(options.riskThresholdScale)) {
    for (const key of SDF_RISK_CLASSES) {
      const current = Number(riskThresholds[key]);
      riskThresholds[key] = Number.isFinite(current)
        ? current * Number(options.riskThresholdScale)
        : 0.35 * Number(options.riskThresholdScale);
    }
  }
  request.params = {
    ...params,
    algorithm: options.algorithm ?? "greedy",
    drccSolver: options.drccSolver ?? params.drccSolver ?? "exact_pool",
    drccEnabled: (options.algorithm ?? params.algorithm) === "drcc",
    nativeCandidateLimit: options.nativeCandidateLimit ?? 4096,
    exactMaxCandidates: 36,
    drccExactMaxCandidates: 36,
    drccPerTargetKeep: 6,
    candidateCoarseStepUm: options.coarseStepUm ?? 150,
    maxAngle: options.maxAngle ?? Math.max(Number(params.maxAngle || 15), 15),
    depthUm: Number(params.depthUm || 10000),
    strictAtlasTargets: true,
    allowFallbackTargetCoverage: false,
    riskThresholdsMm: Object.keys(riskThresholds).length > 0 ? riskThresholds : params.riskThresholdsMm,
  };
  const probeParams = request.probeParams && typeof request.probeParams === "object"
    ? { ...(request.probeParams as JsonObject) }
    : {};
  const shankValue = options.shanks ?? (Number(probeParams.shankCount ?? probeParams.shanks ?? 1) || 1);
  const shanks = Math.max(1, Math.round(shankValue));
  request.probeParams = {
    ...probeParams,
    shankCount: shanks,
    shanks,
    entrySpacingUm: Number(probeParams.entrySpacingUm || 3500),
    shankPitchUm: Number(probeParams.shankPitchUm || 250),
    shankWidthUm: Number(probeParams.shankWidthUm || 70),
  };
  const constraintModes = request.constraintModes && typeof request.constraintModes === "object"
    ? { ...(request.constraintModes as JsonObject) }
    : {};
  request.constraintModes = {
    ...constraintModes,
    brainBounds: "enforce",
    lambdaBoundary: "enforce",
    continuousRiskDistance: "enforce",
    minEntrySpacing: "enforce",
    minClearance: "enforce",
    ...(options.constraintModes ?? {}),
  };
  const clinicalContext = request.clinicalContext && typeof request.clinicalContext === "object"
    ? { ...(request.clinicalContext as JsonObject) }
    : {};
  delete clinicalContext.nativeExcludedFallbackTargetKeys;
  delete clinicalContext.nativeExcludedFallbackTargetReason;
  delete clinicalContext.nativeTargetLookupSource;
  request.clinicalContext = {
    ...clinicalContext,
    nativeExcludedFallbackTargetKeys: [],
    replayCase: options.label,
    paperEvidenceCase: true,
  };
  return request;
}

function makeGeneratedCase(
  source: ReplayCase["source"],
  label: string,
  request: JsonObject,
): ReplayCase {
  const id = `${source}_${sha256(request).slice(0, 16)}`;
  return {
    id,
    label,
    source,
    sourcePath: `generated:${source}:${label}`,
    targetKeys: targetKeysOf(request),
    excludedTargetKeys: excludedTargetKeysOf(request),
    algorithm: algorithmOf(request),
    request,
  };
}

function makeSdfAdversarialCases(baseCases: ReplayCase[]): ReplayCase[] {
  const preferred = baseCases.find((item) => item.id.startsWith("5360cd81")) ?? baseCases[0];
  if (!preferred) return [];
  const out: ReplayCase[] = [];
  for (const spec of SDF_ADVERSARIAL_CLASSES) {
    for (const thresholdMm of SDF_REJECT_THRESHOLDS_MM) {
      out.push(makeSdfCase(preferred, {
        ...spec,
        label: `${spec.label}_threshold_${String(thresholdMm).replace(".", "p")}`,
        thresholdMm,
        outcome: "reject",
        intent: "fail-closed layered SDF rejection gate",
      }));
    }
    for (const thresholdMm of SDF_PASS_THRESHOLDS_MM) {
      out.push(makeSdfCase(preferred, {
        riskClass: spec.riskClass,
        label: `sdf_${spec.riskClass}_pass_control_threshold_${String(thresholdMm).replace(".", "p")}`,
        thresholdMm,
        outcome: "pass",
        intent: "layered SDF enforce-mode pass control gate with low positive thresholds",
        allClassThresholds: thresholdMm,
      }));
    }
  }
  return out;
}

function makeSdfCase(
  preferred: ReplayCase,
  spec: {
    riskClass: string;
    label: string;
    thresholdMm: number;
    outcome: "reject" | "pass";
    intent: string;
    allClassThresholds?: number;
  },
): ReplayCase {
    const request = cloneJson(preferred.request);
    request.targetKeys = Array.isArray(request.targetKeys) && request.targetKeys.length > 0
      ? request.targetKeys.slice(0, Math.min(3, request.targetKeys.length))
      : ["CA1"];
    const params = request.params && typeof request.params === "object"
      ? { ...(request.params as JsonObject) }
      : {};
    const thresholds = params.riskThresholdsMm && typeof params.riskThresholdsMm === "object"
      ? { ...(params.riskThresholdsMm as JsonObject) }
      : {};
    if (Number.isFinite(spec.allClassThresholds)) {
      for (const className of SDF_RISK_CLASSES) thresholds[className] = spec.allClassThresholds;
    }
    thresholds[spec.riskClass] = spec.thresholdMm;
    request.params = {
      ...params,
      algorithm: "greedy",
      nativeCandidateLimit: 4096,
      minRiskDistanceMm: spec.outcome === "pass" ? 0.01 : 0.35,
      riskThresholdsMm: thresholds,
      strictAtlasTargets: true,
      allowFallbackTargetCoverage: false,
      depthUm: Number(params.depthUm || 10000),
      maxAngle: Math.max(Number(params.maxAngle || 15), 15),
      candidateCoarseStepUm: 100,
    };
    const constraintModes = request.constraintModes && typeof request.constraintModes === "object"
      ? { ...(request.constraintModes as JsonObject) }
      : {};
    request.constraintModes = {
      ...constraintModes,
      brainBounds: "enforce",
      lambdaBoundary: "enforce",
      continuousRiskDistance: "enforce",
      minEntrySpacing: "enforce",
      minClearance: "enforce",
    };
    const clinicalContext = request.clinicalContext && typeof request.clinicalContext === "object"
      ? { ...(request.clinicalContext as JsonObject) }
      : {};
    request.clinicalContext = {
      ...clinicalContext,
      replayCase: spec.label,
      sdfAdversarial: true,
      sdfRiskClass: spec.riskClass,
      sdfExpectedOutcome: spec.outcome,
      sdfExpectedThresholdMm: spec.thresholdMm,
      sdfAdversarialIntent: spec.intent,
    };
    const id = `sdf_${spec.riskClass}_${sha256(request).slice(0, 12)}`;
    return {
      id,
      label: spec.label,
      source: "adversarial",
      sourcePath: `generated:sdf-adversarial:${spec.riskClass}:${spec.outcome}`,
      targetKeys: targetKeysOf(request),
      excludedTargetKeys: excludedTargetKeysOf(request),
      algorithm: algorithmOf(request),
      request,
    };
}

function makePaperReplayCases(baseCases: ReplayCase[], targetLookup?: NativeTargetLookup): ReplayCase[] {
  const templates = PAPER_REPLAY_TEMPLATES.filter((item) =>
    !targetLookup || item.targets.every((key) => isAtlasBackedTarget(targetLookup, key)),
  );
  const stableTemplates = templates.length > 0
    ? templates
    : [{ targets: ["CA1"], algorithm: "greedy", shanks: 4, maxAngle: 15, coarseStepUm: 100 }];
  const out: ReplayCase[] = [];
  for (let i = 0; i < PAPER_REPLAY_CASE_COUNT; i += 1) {
    const base = baseCases[i % baseCases.length];
    if (!base) continue;
    const template = stableTemplates[i % stableTemplates.length]!;
    const selectedTargets = [...template.targets];
    const label = `paper_replay100_${String(i + 11).padStart(3, "0")}_${selectedTargets.join("_")}`;
    out.push(makeGeneratedCase("paper_replay", label, paperCaseRequest(base, {
      label,
      targets: selectedTargets,
      algorithm: template.algorithm,
      drccSolver: template.drccSolver,
      shanks: template.shanks,
      maxAngle: template.maxAngle,
      nativeCandidateLimit: 4096,
      coarseStepUm: template.coarseStepUm,
    })));
  }
  return out;
}

function makeStressCases(baseCases: ReplayCase[], targetLookup?: NativeTargetLookup): ReplayCase[] {
  const pool = targetPool(targetLookup);
  const out: ReplayCase[] = [];
  for (let i = 0; i < STRESS_CASE_COUNT; i += 1) {
    const base = baseCases[(i * 3) % baseCases.length];
    if (!base) continue;
    const size = i < 200 ? 1 : i < 400 ? 2 + (i % 2) : 4 + (i % 3);
    const selectedTargets = pickTargets(pool, i * 11 + 5, Math.min(size, pool.length));
    const label = `stress_${String(i + 1).padStart(4, "0")}_${selectedTargets.join("_")}`;
    out.push(makeGeneratedCase("stress", label, paperCaseRequest(base, {
      label,
      targets: selectedTargets,
      algorithm: i % 17 === 0 ? "exact" : "greedy",
      shanks: i % 5 === 0 ? 4 : 1,
      maxAngle: 15 + (i % 3) * 5,
      nativeCandidateLimit: 2048,
      coarseStepUm: 200,
    })));
  }
  return out;
}

function makeAblationCases(baseCases: ReplayCase[], targetLookup?: NativeTargetLookup): ReplayCase[] {
  const pool = targetPool(targetLookup);
  const variants: Array<{
    suffix: string;
    algorithm?: string;
    drccSolver?: string;
    constraintModes?: Record<string, string>;
  }> = [
    { suffix: "risk_enforce", constraintModes: { continuousRiskDistance: "enforce" } },
    { suffix: "risk_warn", constraintModes: { continuousRiskDistance: "warn" } },
    { suffix: "risk_off", constraintModes: { continuousRiskDistance: "off" } },
    { suffix: "entry_spacing_warn", constraintModes: { minEntrySpacing: "warn" } },
    { suffix: "clearance_warn", constraintModes: { minClearance: "warn" } },
    { suffix: "brain_bounds_warn", constraintModes: { brainBounds: "warn" } },
    { suffix: "greedy", algorithm: "greedy" },
    { suffix: "exact", algorithm: "exact" },
    { suffix: "drcc_exact_pool", algorithm: "drcc", drccSolver: "exact_pool" },
    { suffix: "drcc_hybrid", algorithm: "drcc", drccSolver: "hybrid" },
    { suffix: "drcc_heuristic", algorithm: "drcc", drccSolver: "heuristic" },
    { suffix: "drcc_cvar_relax", algorithm: "drcc", drccSolver: "cvar_relax" },
  ];
  const out: ReplayCase[] = [];
  for (let baseIndex = 0; baseIndex < Math.min(8, baseCases.length); baseIndex += 1) {
    const base = baseCases[baseIndex];
    if (!base) continue;
    const selectedTargets = pickTargets(pool, baseIndex * 13 + 1, baseIndex < 3 ? 1 : 3 + (baseIndex % 3));
    for (const variant of variants) {
      const label = `ablation_${baseIndex + 1}_${variant.suffix}_${selectedTargets.join("_")}`;
      out.push(makeGeneratedCase("ablation", label, paperCaseRequest(base, {
        label,
        targets: selectedTargets,
        algorithm: variant.algorithm,
        drccSolver: variant.drccSolver,
        shanks: baseIndex % 2 === 0 ? 4 : 1,
        maxAngle: 15,
        nativeCandidateLimit: 4096,
        coarseStepUm: 150,
        constraintModes: variant.constraintModes,
      })));
    }
  }
  return out;
}

export async function prepareCases(options: {
  rootDir: string;
  sourceProject: string;
  localReplayDir?: string;
  nativeRegionMapPath?: string;
}): Promise<CaseManifest> {
  const outDir = path.join(options.rootDir, "cases", "requests");
  await mkdir(outDir, { recursive: true });
  const localReplayDir = options.localReplayDir || path.join(userHome(), ".probeplanner", "replay");
  const targetLookup = options.nativeRegionMapPath
    ? await loadNativeTargetLookup(options.nativeRegionMapPath)
    : undefined;
  const cases: ReplayCase[] = [];

  for (const id of DEFAULT_REPO_REPLAY_IDS) {
    const sourcePath = path.resolve(options.sourceProject, "replay", `${id}.json`);
    cases.push(await loadReplayCase(id, "repo", sourcePath, targetLookup));
  }
  for (const id of DEFAULT_LOCAL_REPLAY_IDS) {
    const sourcePath = path.resolve(localReplayDir, `${id}.json`);
    cases.push(await loadReplayCase(id, "local", sourcePath, targetLookup));
  }

  if (cases.length !== 10) {
    throw new Error(`Expected 10 replay cases, got ${cases.length}`);
  }
  cases.push(...makePaperReplayCases(cases, targetLookup));
  cases.push(...makeStressCases(cases, targetLookup));
  cases.push(...makeAblationCases(cases, targetLookup));
  cases.push(...makeSdfAdversarialCases(cases));

  for (const item of cases) {
    const payload = {
      id: item.id,
      label: item.label,
      source: item.source,
      requestHash: sha256(item.request),
      targetKeys: item.targetKeys,
      excludedTargetKeys: item.excludedTargetKeys ?? [],
      algorithm: item.algorithm,
      request: item.request,
    };
    await writeFile(path.join(outDir, `${item.id}.json`), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  }

  const manifest: CaseManifest = {
    version: 1,
    generatedAt: new Date().toISOString(),
    sourceProject: "cases/requests",
    cases: cases.map((item) => ({
      id: item.id,
      label: item.label,
      source: item.source,
      sourcePath: manifestSourcePath(item),
      targetKeys: item.targetKeys,
      excludedTargetKeys: item.excludedTargetKeys ?? [],
      algorithm: item.algorithm,
    })),
  };
  await writeFile(path.join(options.rootDir, "cases", "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return manifest;
}

export async function loadManifest(rootDir: string): Promise<LoadedCaseManifest> {
  const manifestPath = path.join(rootDir, "cases", "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as CaseManifest;
  if (manifest.version !== 1 || !Array.isArray(manifest.cases) || manifest.cases.length < 10) {
    throw new Error("Invalid case manifest. Run npm run prepare:cases first.");
  }
  const cases: ReplayCase[] = [];
  for (const item of manifest.cases) {
    const requestPath = path.join(rootDir, "cases", "requests", `${item.id}.json`);
    const raw = JSON.parse(await readFile(requestPath, "utf8")) as JsonObject;
    cases.push({
      ...item,
      excludedTargetKeys: item.excludedTargetKeys ?? [],
      request: (raw.request && typeof raw.request === "object" ? raw.request : {}) as JsonObject,
    });
  }
  return { ...manifest, cases };
}
