import type { BackendRunMetrics, JsonObject, ReplayCase, StageObservation } from "../types.js";
import { taskId as makeTaskId } from "../util/hash.js";
import { nowMs, sleep } from "../util/time.js";

function joinUrl(base: string, route: string): string {
  return `${base.replace(/\/+$/, "")}/${route.replace(/^\/+/, "")}`;
}

async function fetchJson(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<JsonObject> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), init?.timeoutMs ?? 900_000);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const text = await res.text();
    const body = text ? JSON.parse(text) as JsonObject : {};
    if (!res.ok) {
      const error = new Error(String(body.error || body.message || `HTTP ${res.status}`));
      (error as Error & { payload?: JsonObject }).payload = body;
      throw error;
    }
    return body;
  } finally {
    clearTimeout(timeout);
  }
}

function cloneRequestWithTask(base: JsonObject, taskId: string): JsonObject {
  const request = JSON.parse(JSON.stringify(base)) as JsonObject;
  request.taskId = taskId;
  const ctx = request.clinicalContext && typeof request.clinicalContext === "object"
    ? request.clinicalContext as JsonObject
    : {};
  request.clinicalContext = {
    ...ctx,
    perfGateTaskId: taskId,
    perfGateRunAt: new Date().toISOString(),
  };
  return request;
}

function recordStage(stages: StageObservation[], task: JsonObject, elapsedMs: number): void {
  const stage = String(task.stage || task.message || "").trim();
  if (!stage) return;
  const previous = stages.at(-1);
  const status = String(task.status || "");
  const progressRaw = Number(task.progress);
  const progress = Number.isFinite(progressRaw) ? progressRaw : null;
  const message = String(task.message || "");
  if (
    previous &&
    previous.stage === stage &&
    previous.status === status &&
    previous.progress === progress &&
    previous.message === message
  ) {
    return;
  }
  stages.push({ stage, status, progress, message, seenAtMs: elapsedMs });
}

function firstSeen(stages: StageObservation[], names: string[]): number | null {
  for (const item of stages) {
    if (names.includes(item.stage)) return item.seenAtMs;
  }
  return null;
}

function elapsedBetween(stages: StageObservation[], startNames: string[], endNames: string[]): number | null {
  const start = firstSeen(stages, startNames);
  const end = firstSeen(stages, endNames);
  if (!Number.isFinite(start) || !Number.isFinite(end) || Number(end) < Number(start)) return null;
  return Number(end) - Number(start);
}

async function pollTaskStatus(options: {
  backendUrl: string;
  taskId: string;
  startedAtMs: number;
  stages: StageObservation[];
  stop: () => boolean;
  intervalMs: number;
}): Promise<void> {
  while (!options.stop()) {
    try {
      const body = await fetchJson(joinUrl(options.backendUrl, `/api/task/status/${encodeURIComponent(options.taskId)}`), {
        method: "GET",
        timeoutMs: Math.min(10_000, options.intervalMs * 4),
      });
      const task = body.task && typeof body.task === "object" ? body.task as JsonObject : {};
      recordStage(options.stages, task, nowMs() - options.startedAtMs);
      const status = String(task.status || "").toLowerCase();
      if (status === "completed" || status === "failed" || status === "cancelled") return;
    } catch {
      // A transient status miss should not invalidate the authoritative optimize request.
    }
    await sleep(options.intervalMs);
  }
}

function numberOrNull(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function arrayLen(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value)
    ? value.map((item) => Number(item)).filter((item) => Number.isFinite(item))
    : [];
}

function fallbackTargetKeys(response: JsonObject): string[] {
  const profile = response.computeProfile;
  if (!profile || typeof profile !== "object") return [];
  return stringArray((profile as JsonObject).fallbackTargetKeys);
}

function expectedSdfRiskClass(request: JsonObject): string | null {
  const ctx = request.clinicalContext;
  if (!ctx || typeof ctx !== "object") return null;
  const value = (ctx as JsonObject).sdfRiskClass;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function expectedSdfOutcome(request: JsonObject): string | null {
  const ctx = request.clinicalContext;
  if (!ctx || typeof ctx !== "object") return null;
  const value = (ctx as JsonObject).sdfExpectedOutcome;
  return typeof value === "string" && value.trim() ? value.trim().toLowerCase() : null;
}

function nativeRejectReasonKeys(response: JsonObject): string[] {
  const breakdown = response.candidateStageBreakdown;
  if (!breakdown || typeof breakdown !== "object") return [];
  const reasons = (breakdown as JsonObject).nativeRejectReasons;
  if (!reasons || typeof reasons !== "object") return [];
  return Object.keys(reasons as JsonObject).sort();
}

function expectedShankCount(request: JsonObject): number | null {
  const probeParams = request.probeParams && typeof request.probeParams === "object"
    ? request.probeParams as JsonObject
    : {};
  for (const key of ["shankCount", "shanks", "nShanks"]) {
    const n = Number(probeParams[key]);
    if (Number.isFinite(n) && n > 0) return Math.round(n);
  }
  return null;
}

function selectedShankCounts(response: JsonObject): number[] {
  const selected = Array.isArray(response.selected) ? response.selected : [];
  const out: number[] = [];
  for (const item of selected) {
    if (!item || typeof item !== "object") continue;
    const candidate = item as JsonObject;
    const n = Number(candidate.shankCount ?? candidate.shanks);
    if (Number.isFinite(n) && n > 0) out.push(Math.round(n));
  }
  return out;
}

function selectedShankOffsetCounts(response: JsonObject): number[] {
  const selected = Array.isArray(response.selected) ? response.selected : [];
  const out: number[] = [];
  for (const item of selected) {
    if (!item || typeof item !== "object") continue;
    const candidate = item as JsonObject;
    const offsets = numberArray(candidate.shankOffsetsUm);
    if (offsets.length > 0) out.push(offsets.length);
  }
  return out;
}

function observedSdfRiskClasses(response: JsonObject): string[] {
  const out = new Set<string>();
  const selected = Array.isArray(response.selected) ? response.selected : [];
  for (const item of selected) {
    if (!item || typeof item !== "object") continue;
    const candidate = item as JsonObject;
    for (const containerKey of ["riskDiagnostics", "riskPrefilter"]) {
      const container = candidate[containerKey];
      if (!container || typeof container !== "object") continue;
      const distances = (container as JsonObject).classMinDistanceMm;
      if (!distances || typeof distances !== "object") continue;
      for (const key of Object.keys(distances as JsonObject)) out.add(key);
    }
  }
  return Array.from(out).sort();
}

function nestedNumberOrNull(value: unknown, path: string[]): number | null {
  let cur = value;
  for (const key of path) {
    if (!cur || typeof cur !== "object") return null;
    cur = (cur as JsonObject)[key];
  }
  return numberOrNull(cur);
}

function nestedStringOrNull(value: unknown, path: string[]): string | null {
  let cur = value;
  for (const key of path) {
    if (!cur || typeof cur !== "object") return null;
    cur = (cur as JsonObject)[key];
  }
  return typeof cur === "string" && cur.trim() ? cur.trim() : null;
}

function phaseMs(response: JsonObject, stages: StageObservation[], startNames: string[], endNames: string[], profileKey: string): number | null {
  return elapsedBetween(stages, startNames, endNames)
    ?? nestedNumberOrNull(response.computeProfile, ["nativeTimingsMs", profileKey])
    ?? nestedNumberOrNull(response.computeProfile, [profileKey]);
}

export async function runBackendCase(options: {
  backendUrl: string;
  replayCase: ReplayCase;
  runIndex: number;
  pollIntervalMs?: number;
  timeoutMs?: number;
}): Promise<BackendRunMetrics> {
  const taskId = makeTaskId(`perf_${options.replayCase.id.slice(0, 8)}`);
  const stages: StageObservation[] = [];
  const startedAtMs = nowMs();
  let stopped = false;
  const poller = pollTaskStatus({
    backendUrl: options.backendUrl,
    taskId,
    startedAtMs,
    stages,
    stop: () => stopped,
    intervalMs: options.pollIntervalMs ?? 500,
  });

  try {
    const request = cloneRequestWithTask(options.replayCase.request, taskId);
    const response = await fetchJson(joinUrl(options.backendUrl, "/api/optimize"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      timeoutMs: options.timeoutMs ?? 20 * 60_000,
    });
    const backendTotalMs = nowMs() - startedAtMs;
    stopped = true;
    await poller;
    const uncovered = stringArray(response.uncovered);
    const fallbackKeys = fallbackTargetKeys(response);
    const fallbackKeySet = new Set(fallbackKeys);
    const fallbackUncoveredCount = uncovered.filter((key) => fallbackKeySet.has(key)).length;
    const expectedRiskClass = expectedSdfRiskClass(request);
    const expectedShanks = expectedShankCount(request);
    const requestAlgorithm = typeof (request.params as JsonObject | undefined)?.algorithm === "string"
      ? String((request.params as JsonObject).algorithm)
      : null;
    const runtimePolicy = response.computeProfile && typeof response.computeProfile === "object"
      ? (response.computeProfile as JsonObject).solverRuntimePolicy
      : null;
    const runtimePolicyObj = runtimePolicy && typeof runtimePolicy === "object" ? runtimePolicy as JsonObject : {};

    return {
      caseId: options.replayCase.id,
      runIndex: options.runIndex,
      ok: response.ok !== false,
      backendTotalMs,
      responseTimeMs: numberOrNull(response.timeMs),
      generationMs: phaseMs(response, stages, ["candidate_generating"], ["candidate_generated", "candidate_prefiltered"], "generationMs"),
      scoringMs: phaseMs(response, stages, ["candidate_scoring"], ["candidate_scored"], "scoringMs"),
      solverMs: phaseMs(response, stages, ["candidate_scored", "pareto_ranked"], ["solver_completed"], "solverMs"),
      candidateCount: numberOrNull(response.candidateCount),
      candidateUniverseCount: numberOrNull(response.candidateUniverseCount),
      selectedCount: arrayLen(response.selected),
      uncoveredCount: uncovered.length,
      atlasBackedUncoveredCount: uncovered.length - fallbackUncoveredCount,
      fallbackUncoveredCount,
      fallbackTargetKeys: fallbackKeys,
      requiredCoverageUnits: numberOrNull(response.requiredCoverageUnits),
      coveredCoverageUnits: numberOrNull(response.coveredCoverageUnits),
      algorithm: typeof response.algorithm === "string" ? response.algorithm : null,
      requestedAlgorithm: nestedStringOrNull(response.computeProfile, ["solverRuntimePolicy", "requestedAlgorithm"])
        ?? requestAlgorithm,
      effectiveAlgorithm: nestedStringOrNull(response.computeProfile, ["solverRuntimePolicy", "effectiveAlgorithm"])
        ?? nestedStringOrNull(response.computeProfile, ["effectiveAlgorithm"])
        ?? (typeof response.algorithm === "string" ? response.algorithm : null),
      requestedDrccSolver: String(nestedStringOrNull(response.computeProfile, ["solverRuntimePolicy", "requestedAlgorithm"]) ?? requestAlgorithm ?? "").toLowerCase() === "drcc" && typeof runtimePolicyObj.requestedDrccSolver === "string"
        ? runtimePolicyObj.requestedDrccSolver
        : (String(requestAlgorithm ?? "").toLowerCase() === "drcc" && typeof (request.params as JsonObject | undefined)?.drccSolver === "string" ? String((request.params as JsonObject).drccSolver) : null),
      effectiveDrccSolver: typeof runtimePolicyObj.effectiveDrccSolver === "string"
        ? runtimePolicyObj.effectiveDrccSolver
        : null,
      nativeDrccApproximation: runtimePolicyObj.nativeDrccApproximation === true,
      expectedSdfRiskClass: expectedRiskClass,
      expectedSdfOutcome: expectedSdfOutcome(request),
      observedSdfRiskClasses: observedSdfRiskClasses(response),
      nativeRejectReasonKeys: nativeRejectReasonKeys(response),
      expectedShankCount: expectedShanks,
      effectiveShankCount: numberOrNull(runtimePolicyObj.shankCount),
      selectedShankCounts: selectedShankCounts(response),
      selectedShankOffsetCounts: selectedShankOffsetCounts(response),
      taskId,
      stages,
      computeProfile: response.computeProfile,
      candidateStageBreakdown: response.candidateStageBreakdown,
    };
  } catch (err) {
    stopped = true;
    await poller;
    return {
      caseId: options.replayCase.id,
      runIndex: options.runIndex,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      backendTotalMs: nowMs() - startedAtMs,
      responseTimeMs: null,
      generationMs: elapsedBetween(stages, ["candidate_generating"], ["candidate_generated", "candidate_prefiltered"]),
      scoringMs: elapsedBetween(stages, ["candidate_scoring"], ["candidate_scored"]),
      solverMs: elapsedBetween(stages, ["candidate_scored", "pareto_ranked"], ["solver_completed"]),
      candidateCount: null,
      candidateUniverseCount: null,
      selectedCount: 0,
      uncoveredCount: 0,
      atlasBackedUncoveredCount: 0,
      fallbackUncoveredCount: 0,
      fallbackTargetKeys: [],
      requiredCoverageUnits: null,
      coveredCoverageUnits: null,
      algorithm: null,
      requestedAlgorithm: typeof (options.replayCase.request.params as JsonObject | undefined)?.algorithm === "string"
        ? String((options.replayCase.request.params as JsonObject).algorithm)
        : null,
      effectiveAlgorithm: null,
      requestedDrccSolver: String((options.replayCase.request.params as JsonObject | undefined)?.algorithm ?? "").toLowerCase() === "drcc" && typeof (options.replayCase.request.params as JsonObject | undefined)?.drccSolver === "string"
        ? String((options.replayCase.request.params as JsonObject).drccSolver)
        : null,
      effectiveDrccSolver: null,
      nativeDrccApproximation: false,
      expectedSdfRiskClass: expectedSdfRiskClass(options.replayCase.request),
      expectedSdfOutcome: expectedSdfOutcome(options.replayCase.request),
      observedSdfRiskClasses: [],
      nativeRejectReasonKeys: [],
      expectedShankCount: expectedShankCount(options.replayCase.request),
      effectiveShankCount: null,
      selectedShankCounts: [],
      selectedShankOffsetCounts: [],
      taskId,
      stages,
      computeProfile: null,
      candidateStageBreakdown: null,
    };
  }
}
