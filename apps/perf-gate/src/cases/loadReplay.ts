import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { DEFAULT_LOCAL_REPLAY_IDS, DEFAULT_REPO_REPLAY_IDS } from "../config/defaults.js";
import type { CaseManifest, JsonObject, LoadedCaseManifest, ReplayCase } from "../types.js";
import { sha256 } from "../util/hash.js";

const KNOWN_NON_ATLAS_TARGET_KEYS = new Set(["PFC", "PTLp"]);

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

function excludedTargetKeysOf(request: JsonObject): string[] {
  const ctx = request.clinicalContext && typeof request.clinicalContext === "object"
    ? (request.clinicalContext as JsonObject)
    : {};
  const keys = Array.isArray(ctx.nativeExcludedFallbackTargetKeys)
    ? ctx.nativeExcludedFallbackTargetKeys
    : [];
  return keys.map((x) => String(x)).filter(Boolean);
}

function normalizeNativeReplayRequest(request: JsonObject): JsonObject {
  const out = cloneJson(request);
  const originalTargets = targetKeysOf(out);
  const excluded = originalTargets.filter((key) => KNOWN_NON_ATLAS_TARGET_KEYS.has(key));
  const retained = originalTargets.filter((key) => !KNOWN_NON_ATLAS_TARGET_KEYS.has(key));
  if (retained.length > 0 && excluded.length > 0) {
    out.targetKeys = retained;
    const clinicalContext = out.clinicalContext && typeof out.clinicalContext === "object"
      ? { ...(out.clinicalContext as JsonObject) }
      : {};
    out.clinicalContext = {
      ...clinicalContext,
      nativeExcludedFallbackTargetKeys: excluded,
      nativeExcludedFallbackTargetReason: "not atlas-backed in native region index; excluded from real coverage gate",
    };
  }
  const params = out.params && typeof out.params === "object"
    ? { ...(out.params as JsonObject) }
    : {};
  out.params = {
    ...params,
    strictAtlasTargets: true,
    allowFallbackTargetCoverage: false,
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

async function loadReplayCase(id: string, source: "repo" | "local", sourcePath: string): Promise<ReplayCase> {
  const text = await readFile(sourcePath, "utf8");
  const parsed = JSON.parse(text) as JsonObject;
  const request = normalizeNativeReplayRequest(requestFromReplay(parsed));
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

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function makeSdfAdversarialCases(baseCases: ReplayCase[]): ReplayCase[] {
  const preferred = baseCases.find((item) => item.id.startsWith("5360cd81")) ?? baseCases[0];
  if (!preferred) return [];
  const out: ReplayCase[] = [];
  for (const spec of SDF_ADVERSARIAL_CLASSES) {
    out.push(makeSdfCase(preferred, {
      ...spec,
      label: spec.label,
      outcome: "reject",
      intent: "fail-closed layered SDF rejection gate",
    }));
    out.push(makeSdfCase(preferred, {
      riskClass: spec.riskClass,
      label: `sdf_${spec.riskClass}_pass_control`,
      thresholdMm: -10.0,
      outcome: "pass",
      intent: "layered SDF permissive control gate; near-threshold semantics are unit-tested in planner-engine",
      allClassThresholds: -10.0,
    }));
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
      minRiskDistanceMm: spec.outcome === "pass" ? -10.0 : 0.35,
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

export async function prepareCases(options: {
  rootDir: string;
  sourceProject: string;
  localReplayDir?: string;
}): Promise<CaseManifest> {
  const outDir = path.join(options.rootDir, "cases", "requests");
  await mkdir(outDir, { recursive: true });
  const localReplayDir = options.localReplayDir || path.join(userHome(), ".probeplanner", "replay");
  const cases: ReplayCase[] = [];

  for (const id of DEFAULT_REPO_REPLAY_IDS) {
    const sourcePath = path.resolve(options.sourceProject, "replay", `${id}.json`);
    cases.push(await loadReplayCase(id, "repo", sourcePath));
  }
  for (const id of DEFAULT_LOCAL_REPLAY_IDS) {
    const sourcePath = path.resolve(localReplayDir, `${id}.json`);
    cases.push(await loadReplayCase(id, "local", sourcePath));
  }

  if (cases.length !== 10) {
    throw new Error(`Expected 10 replay cases, got ${cases.length}`);
  }
  cases.push(...makeSdfAdversarialCases(cases));

  for (const item of cases) {
    const payload = {
      id: item.id,
      label: item.label,
      source: item.source,
      requestHash: sha256(item.request),
      targetKeys: item.targetKeys,
      algorithm: item.algorithm,
      request: item.request,
    };
    await writeFile(path.join(outDir, `${item.id}.json`), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  }

  const manifest: CaseManifest = {
    version: 1,
    generatedAt: new Date().toISOString(),
    sourceProject: path.relative(options.rootDir, path.resolve(options.sourceProject)) || ".",
    cases: cases.map((item) => ({
      id: item.id,
      label: item.label,
      source: item.source,
      sourcePath: item.source === "adversarial"
        ? item.sourcePath
        : path.relative(options.rootDir, item.sourcePath),
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
