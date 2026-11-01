import { useState, useCallback, useRef, useEffect } from "react";
import { PROBE_PRESETS } from "../constants/anatomy.js";
import { flaskFetch } from "./useBackend.js";
import { sanitizeConstraintModes, validateConstraintModes } from "../constants/constraintSchema.js";

const OPTIMIZE_TIMEOUT_MS_DEFAULT = 15 * 60 * 1000;
const TASK_POLL_INTERVAL_MS = 800;
const FALLBACK_PROGRESS_POLL_MS = 700;
const FALLBACK_PROGRESS_IDLE_GRACE_MS = 1800;
const FALLBACK_PROGRESS_MAX_PCT = 99;

function resolveOptimizeTimeoutMs(params = {}) {
  const algo = String(params?.algorithm || "greedy").toLowerCase();
  if (algo === "exact") return 20 * 60 * 1000;
  if (algo === "drcc") return 20 * 60 * 1000;
  if (algo === "milp") return 15 * 60 * 1000;
  return OPTIMIZE_TIMEOUT_MS_DEFAULT;
}

function normalizeBackendErrorMessage(err, timeoutMs) {
  const raw = String(err?.message || "").trim();
  const timeoutLike = isTimeoutLikeError(err);
  if (!timeoutLike) return raw || "Backend error";
  const sec = Math.max(1, Math.round((Number(timeoutMs) || 60000) / 1000));
  return (
    `Optimization request timed out after ${sec}s. ` +
    "Try reducing candidate complexity (smaller grid, lower Pareto topK), " +
    "or rerun with Greedy for a quick baseline."
  );
}

function isTimeoutLikeError(err) {
  const raw = String(err?.message || "").trim();
  const lower = raw.toLowerCase();
  return (
    lower.includes("signal timed out") ||
    lower.includes("timed out") ||
    lower.includes("timeout") ||
    err?.name === "TimeoutError"
  );
}

function createTaskId() {
  return `task_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function normalizeTaskProgressToPct(task) {
  const raw = Number(task?.progress);
  if (!Number.isFinite(raw)) return null;
  const pct = raw <= 1 ? raw * 100 : raw;
  return Math.max(0, Math.min(100, pct));
}

function canonicalTargetKey(raw) {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  // Accept side-aware labels from 3D picker (e.g. "CA1 R", "CA1/L", "CA1_R").
  const stripped = trimmed
    .replace(/\s+[LR]$/i, "")
    .replace(/[/_-][LR]$/i, "")
    .trim();
  return stripped || null;
}

function buildByRegion(probes, targetKeys) {
  const byRegion = {};
  for (const key of targetKeys || []) {
    let frac = 0;
    for (const p of probes) {
      const cc = p.channelCoverage?.[key];
      if (cc?.score > frac) frac = cc.score;
    }
    if (frac === 0 && probes.some((p) => (p.regionsHit || []).includes(key))) {
      frac = 1;
    }
    byRegion[key] = { frac };
  }
  return byRegion;
}

function toFiniteNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function isFiniteTriplet(value) {
  return Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every((v) => Number.isFinite(Number(v)));
}

function isNativeKernelResponse(data) {
  const status = String(data?.nativeEngineService?.status || "").toLowerCase();
  const version = String(data?.engineVersion || "").toLowerCase();
  return status.includes("native_kernel") || version.includes("planner-engine-rust");
}

function makeNativeGeometryContractError(data, index) {
  const err = new Error(
    `Native planner response is missing entry_xyz/tip_xyz/dir for selected candidate ${index}.`
  );
  err.payload = data;
  return err;
}

function deriveBrainBoundsSegmentDepthMm(summary) {
  if (!summary || typeof summary !== "object") return null;
  const stepUm = toFiniteNumber(summary.stepUm) ?? 25;
  const toMm = (index) => Math.max(0, Number(index)) * stepUm / 1000.0;

  const fromSegmentMm = Array.isArray(summary.maxOutsideGapSegmentMm)
    ? summary.maxOutsideGapSegmentMm.map((v) => toFiniteNumber(v))
    : null;
  if (fromSegmentMm && fromSegmentMm.length >= 2 && fromSegmentMm[0] !== null && fromSegmentMm[1] !== null) {
    return [fromSegmentMm[0], fromSegmentMm[1]];
  }

  const gapStartIdx = toFiniteNumber(summary.maxOutsideGapStartIndex);
  const gapEndIdx = toFiniteNumber(summary.maxOutsideGapEndIndex);
  if (gapStartIdx !== null && gapEndIdx !== null) {
    return [toMm(gapStartIdx), toMm(gapEndIdx)];
  }

  const reason = String(summary.reason || "").trim().toLowerCase();
  const sampleCount = toFiniteNumber(summary.sampleCount);
  const lastInside = toFiniteNumber(summary.lastInsideIndex);

  if (reason === "tip_outside_brain" && sampleCount !== null && sampleCount > 0) {
    const startIdx = Math.max(0, Math.floor((lastInside ?? -1) + 1));
    const endIdx = Math.max(startIdx, Math.floor(sampleCount - 1));
    return [toMm(startIdx), toMm(endIdx)];
  }
  if (reason === "no_brain_intersection" && sampleCount !== null && sampleCount > 0) {
    return [0, toMm(Math.floor(sampleCount - 1))];
  }
  return null;
}

function deriveBrainBoundsFailureCards(backendIssues, existingFailureCards) {
  const issues = Array.isArray(backendIssues) ? backendIssues : [];
  const existing = Array.isArray(existingFailureCards) ? existingFailureCards : [];
  const existingKey = new Set(
    existing.map((card) => `${Number(card?.probeIdx ?? -1)}::${String(card?.constraint || "")}`)
  );
  const derived = [];
  for (const issue of issues) {
    if (!issue || String(issue.constraint || "") !== "brainBounds") continue;
    const summary = issue.brainMaskSummary;
    if (!summary || summary.available === false || summary.passed === true) continue;
    const probeIdx = Math.max(0, Number(issue.probeIdx ?? 0) || 0);
    const key = `${probeIdx}::brainBounds`;
    if (existingKey.has(key)) continue;
    const segmentDepthMm = deriveBrainBoundsSegmentDepthMm(summary);
    derived.push({
      probeIdx,
      constraint: "brainBounds",
      segmentDepthMm,
      segmentRangeMm: segmentDepthMm,
      segmentSampleCount: Number(summary.sampleCount ?? 0) || 0,
      hitStructureClass: "brain_mask",
      thresholdSource: "brainMaskContinuity",
      brainMaskSummary: summary,
      alternativeCandidate: {
        reasonCode: String(summary.reason || ""),
        reasonEn: issue.msgEn || "",
        reasonCn: issue.msgCn || "",
      },
      counterfactualPassIf: [],
    });
  }
  return derived;
}

function buildDiagnosticErrorResult(payload, msg, targetKeys, startMs) {
  const raw = payload && typeof payload === "object" ? payload : {};
  const backendIssues = Array.isArray(raw.safetyIssues) ? raw.safetyIssues : [];
  const backendFailureCards = Array.isArray(raw.failureCards) ? raw.failureCards : [];
  const derivedBrainBoundsCards = deriveBrainBoundsFailureCards(backendIssues, backendFailureCards);
  const total = Array.isArray(targetKeys) ? targetKeys.length : 0;
  return {
    probes: [],
    selected: [],
    uncovered: raw.uncovered ?? targetKeys,
    coverage: { covered: 0, total, byRegion: {} },
    warnings: [],
    elapsedMs: Date.now() - (startMs ?? Date.now()),
    algorithm: "error",
    backendError: msg,
    requestedTargets: [...(targetKeys || [])],
    safetyIssues: backendIssues,
    candidateCount: raw.candidateCount ?? null,
    candidateUniverseCount: raw.candidateUniverseCount ?? null,
    failureCards: [...backendFailureCards, ...derivedBrainBoundsCards],
    candidateStageBreakdown: raw.candidateStageBreakdown ?? null,
    candidateFunnelDiagnostics: raw.candidateFunnelDiagnostics ?? null,
    fallbackTargetKeys: raw.fallbackTargetKeys ?? raw.computeProfile?.fallbackTargetKeys ?? [],
    fallbackUncovered: raw.fallbackUncovered ?? [],
    atlasBackedUncovered: raw.atlasBackedUncovered ?? [],
    atlasBackedUncoveredCount: raw.atlasBackedUncoveredCount ?? null,
    autoAvoidPrefilter: raw.autoAvoidPrefilter ?? null,
    autoRepairSummary: raw.autoRepairSummary ?? null,
    repairPreferenceSummary: raw.repairPreferenceSummary ?? null,
    strictFallbackSummary: raw.strictFallbackSummary ?? null,
    computeProfile: raw.computeProfile ?? null,
    riskConstraintPolicy: raw.riskConstraintPolicy ?? null,
    reconstructionQuality: raw.reconstructionQuality ?? null,
    mriLayerStatus: raw.mriLayerStatus ?? null,
  };
}

export function useOptimizer() {
  const [results, setResults] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [runProgress, setRunProgress] = useState(0);
  const [runMsg, setRunMsg] = useState("");
  const [lastError, setLastError] = useState("");
  const [safetyIssues, setSafetyIssues] = useState([]);
  const [milpInfo, setMilpInfo] = useState(null);
  const activeReqRef = useRef(0);
  const activeTaskIdRef = useRef(null);
  const taskPollTimerRef = useRef(null);
  const fallbackProgressTimerRef = useRef(null);
  const lastBackendProgressAtRef = useRef(0);
  const lastTaskProgressPctRef = useRef(0);
  const lastTaskStageRef = useRef("");

  const stopTaskPolling = useCallback(() => {
    if (taskPollTimerRef.current) {
      clearInterval(taskPollTimerRef.current);
      taskPollTimerRef.current = null;
    }
    if (fallbackProgressTimerRef.current) {
      clearInterval(fallbackProgressTimerRef.current);
      fallbackProgressTimerRef.current = null;
    }
  }, []);

  const finalise = useCallback((raw, targetKeys, startMs) => {
    const backendIssues = Array.isArray(raw.safetyIssues) ? raw.safetyIssues : [];
    const fallbackTargetKeys = Array.isArray(raw.fallbackTargetKeys)
      ? raw.fallbackTargetKeys
      : Array.isArray(raw.computeProfile?.fallbackTargetKeys)
        ? raw.computeProfile.fallbackTargetKeys
        : [];

      const probes = (raw.selected || []).map((t, i) => {
        const perProbeIssues = backendIssues.filter((iss) => (iss.probeIdx ?? -1) === i);
        return {
          ...t,
          probeIdx: i,
        ap: t.ap_mm_corrected ?? t.ap_mm,
        ml: t.ml_mm_corrected ?? t.ml_mm,
        dvEntry: t.entryDvCorr_mm_corrected ?? t.entryDvCorr_mm ?? 0,
        azimuth: t.phi_deg ?? 0,
        elevation: 90 - (t.theta_deg ?? 0),
        regionsHit: t.coveredRegions ?? [],
          violations:
            Array.isArray(t.violations) && t.violations.length > 0
              ? t.violations
              : perProbeIssues
                  .map((iss) => ({
                    cn: iss.msgCn ?? iss.msgEn ?? "",
                    en: iss.msgEn ?? iss.msgCn ?? "",
                  }))
                  .filter((v) => (v.cn || v.en)),
          robustScore: t.robustScore ?? t.robustness ?? null,
          paretoRank: t.paretoRank ?? null,
          explainScore: t.explainScore ?? null,
          objectiveScores: t.objectiveScores ?? null,
          objectiveWeighted: t.objectiveWeighted ?? null,
          explain: t.explain ?? null,
          coverageConfidence: t.coverageConfidence ?? null,
          safetyDistanceConfidence: t.safetyDistanceConfidence ?? null,
          safetyMarginCi95: t.safetyMarginCi95 ?? null,
          riskDiagnostics: t.riskDiagnostics ?? null,
          uncertainty: t.uncertainty ?? null,
          selectiveConformal: t.selectiveConformal ?? null,
          drccMetrics: t.drccMetrics ?? null,
          drccFeasible: t.drccFeasible ?? null,
          primaryEndpoints: t.primaryEndpoints ?? null,
          statisticalSafety: t.statisticalSafety ?? null,
          minDistanceMm: t.minDistanceMm ?? null,
          minDistanceCi95: t.minDistanceCi95 ?? null,
          pSafetyGtThreshold: t.pSafetyGtThreshold ?? null,
          pSafetyGtThresholdCi95: t.pSafetyGtThresholdCi95 ?? null,
          coveredRegionSides: t.coveredRegionSides ?? null,
          brainContainmentPassed: t.brainContainmentPassed ?? null,
          tipInTarget: t.tipInTarget ?? null,
          distalOvershootMm: t.distalOvershootMm ?? null,
          internalRiskViolationCount: t.internalRiskViolationCount ?? null,
          minimumInternalRiskMarginMm: t.minimumInternalRiskMarginMm ?? null,
          entrySurfaceCrossingStatus: t.entrySurfaceCrossingStatus ?? null,
          brainContainmentAudit: t.brainContainmentAudit ?? null,
          targetEndpointAudit: t.targetEndpointAudit ?? null,
          internalRiskAudit: t.internalRiskAudit ?? null,
          entrySurfaceAudit: t.entrySurfaceAudit ?? null,
        };
      });

    const coveredSet = new Set(probes.flatMap((p) => p.regionsHit));
    const total = targetKeys?.length ?? 0;
    const requiredCoverageUnits = Number.isFinite(raw.requiredCoverageUnits)
      ? Number(raw.requiredCoverageUnits)
      : total;
    const coveredCoverageUnits = Number.isFinite(raw.coveredCoverageUnits)
      ? Number(raw.coveredCoverageUnits)
      : coveredSet.size;
    const warnings = backendIssues
      .filter((iss) => (iss.mode ?? "warn") === "warn")
      .map((iss) => ({
        probeIdx: iss.probeIdx ?? 0,
        message: iss.msgCn ?? iss.msgEn ?? "",
      }));

    let algorithmLabel = raw.algorithm || "greedy";
    if (algorithmLabel === "milp" && raw.milpStatus === "Optimal") algorithmLabel = "milp(opt)";
    if (algorithmLabel === "milp" && raw.milpStatus === "Feasible") algorithmLabel = "milp(feas)";

      const backendFailureCards = Array.isArray(raw.failureCards) ? raw.failureCards : [];
      const derivedBrainBoundsCards = deriveBrainBoundsFailureCards(backendIssues, backendFailureCards);
      const result = {
        probes,
        coverage: {
        covered: coveredCoverageUnits,
        total: requiredCoverageUnits,
        byRegion: buildByRegion(probes, targetKeys),
      },
      warnings,
      elapsedMs: Date.now() - (startMs ?? Date.now()),
      algorithm: algorithmLabel,
      selected: probes,
      requestedTargets: [...(targetKeys || [])],
      uncovered: raw.uncovered ?? [],
      fallbackTargetKeys,
      fallbackUncovered: Array.isArray(raw.fallbackUncovered) ? raw.fallbackUncovered : [],
      atlasBackedUncovered: Array.isArray(raw.atlasBackedUncovered) ? raw.atlasBackedUncovered : [],
      atlasBackedUncoveredCount: Number.isFinite(Number(raw.atlasBackedUncoveredCount))
        ? Number(raw.atlasBackedUncoveredCount)
        : null,
      voxelMode: raw.voxelMode ?? false,
      milpStatus: raw.milpStatus ?? null,
      milpNodes: raw.milpNodes ?? null,
      candidateCount: raw.candidateCount ?? null,
      candidateUniverseCount: raw.candidateUniverseCount ?? null,
      correctionApplied: raw.correctionApplied ?? null,
        correctionUncertainty: raw.correctionUncertainty ?? null,
        requestId: raw.requestId ?? null,
        engineVersion: raw.engineVersion ?? null,
        runtimeIdentity: raw.runtimeIdentity ?? null,
        rankingMode: raw.rankingMode ?? null,
        robustOptimization: raw.robustOptimization ?? null,
        paretoEnabled: raw.paretoEnabled ?? null,
        objectiveWeights: raw.objectiveWeights ?? null,
        paretoSummary: raw.paretoSummary ?? null,
        robustParams: raw.robustParams ?? null,
        paretoParams: raw.paretoParams ?? null,
        objectivePreset: raw.objectivePreset ?? null,
        preferencePreview: raw.preferencePreview ?? null,
        preferenceProfiles: raw.preferenceProfiles ?? null,
        uncertaintySummary: raw.uncertaintySummary ?? null,
        drccEnabled: raw.drccEnabled ?? false,
        drccSummary: raw.drccSummary ?? null,
        selectiveConformalSummary: raw.selectiveConformalSummary ?? null,
        stratifiedCalibration: raw.stratifiedCalibration ?? null,
        statisticalContract: raw.statisticalContract ?? null,
        targetingMode: raw.targetingMode ?? "single_hemisphere",
        uncoveredSides: Array.isArray(raw.uncoveredSides) ? raw.uncoveredSides : [],
        sideCoverage: raw.sideCoverage ?? null,
        atlasSignature: raw.atlasSignature ?? null,
        atlasHash: raw.atlasHash ?? null,
        requestHash: raw.requestHash ?? null,
        responseHash: raw.responseHash ?? null,
        deterministicResponseHash: raw.deterministicResponseHash ?? null,
        riskModel: raw.riskModel ?? null,
        mriLayerStatus: raw.mriLayerStatus ?? null,
        riskConstraintPolicy: raw.riskConstraintPolicy ?? null,
        degradationNotices: Array.isArray(raw.degradationNotices) ? raw.degradationNotices : [],
        failureCards: [...backendFailureCards, ...derivedBrainBoundsCards],
        autoAvoidPrefilter: raw.autoAvoidPrefilter ?? null,
        autoRepairSummary: raw.autoRepairSummary ?? null,
        repairPreferenceSummary: raw.repairPreferenceSummary ?? null,
        strictFallbackSummary: raw.strictFallbackSummary ?? null,
        computeProfile: raw.computeProfile ?? null,
        candidateCache: raw.candidateCache ?? null,
        candidateStageBreakdown: raw.candidateStageBreakdown ?? null,
        candidateFunnelDiagnostics: raw.candidateFunnelDiagnostics ?? null,
        plannerCalibrationProfile: raw.plannerCalibrationProfile ?? null,
        uncertaintyCalibration: raw.uncertaintyCalibration ?? null,
        clinicalContract: raw.clinicalContract ?? null,
        clinicalContext: raw.clinicalContext ?? null,
        primaryEndpointSummary: raw.primaryEndpointSummary ?? null,
        objectiveRiskConfig: raw.objectiveRiskConfig ?? null,
        stabilitySummary: raw.stabilitySummary ?? null,
        plannerExecutionBackend: raw.plannerExecutionBackend ?? null,
        nativeEngineBypassReason: raw.nativeEngineBypassReason ?? null,
        reproducibility: {
          requestId: raw.requestId ?? null,
          engineVersion: raw.engineVersion ?? null,
          atlasHash: raw.atlasHash ?? null,
          requestHash: raw.requestHash ?? null,
          responseHash: raw.responseHash ?? null,
          deterministicResponseHash: raw.deterministicResponseHash ?? null,
        },
      };

    setSafetyIssues(backendIssues);
    setResults(result);
    setLastError("");
    setRunProgress(100);
    setTimeout(() => {
      setIsRunning(false);
      setRunProgress(0);
      setRunMsg("");
    }, 300);
  }, []);

  const cancel = useCallback(() => {
    const taskId = activeTaskIdRef.current;
    if (taskId) {
      flaskFetch("/api/task/cancel", "POST", { taskId }, null, { timeoutMs: 6000 }).catch(() => {});
    }
    activeReqRef.current += 1;
    activeTaskIdRef.current = null;
    stopTaskPolling();
    setIsRunning(false);
    setRunProgress(0);
    setRunMsg("");
    setLastError("");
  }, [stopTaskPolling]);

  const run = useCallback(
    ({
      selectedRegions,
      params,
      probeParams,
      probePreset,
      constraintModes,
      backendStatus,
      backendRegions,
      correction,
      correctionUncertainty,
      clinicalContext,
    }) => {
      const normalizedTargets = [...selectedRegions]
        .map(canonicalTargetKey)
        .filter(Boolean);
      const targetKeys = [...new Set(normalizedTargets)];
      if (!targetKeys.length) {
        const msg = "No valid target regions selected.";
        setLastError(msg);
        setResults({
          probes: [],
          selected: [],
          uncovered: [],
          coverage: { covered: 0, total: 0, byRegion: {} },
          warnings: [],
          elapsedMs: 0,
          algorithm: "blocked",
          backendError: msg,
        });
        setIsRunning(false);
        setRunProgress(0);
        setRunMsg("");
        return;
      }
      const backendRegionSet = new Set(Array.isArray(backendRegions) ? backendRegions : []);
      if (backendRegionSet.size > 0) {
        const unsupported = targetKeys.filter((key) => !backendRegionSet.has(key));
        if (unsupported.length > 0) {
          // Backend now supports on-demand region provisioning; proceed and let
          // server attempt dynamic registration from atlas acronym index.
          console.info(
            "Target regions not in current status list; requesting backend auto-provision:",
            unsupported
          );
        }
      }

      const normalizedConstraintModes = sanitizeConstraintModes(constraintModes || {});
      const contractErrors = validateConstraintModes(normalizedConstraintModes);
      if (contractErrors.length > 0) {
        const msg = contractErrors.join("; ");
        setLastError(msg);
        setResults({
          probes: [],
          selected: [],
          uncovered: targetKeys,
          coverage: { covered: 0, total: targetKeys.length, byRegion: {} },
          warnings: contractErrors.map((msg, i) => ({ probeIdx: i, message: msg })),
          elapsedMs: 0,
          algorithm: "contract-error",
          backendError: msg,
        });
        setIsRunning(false);
        setRunProgress(0);
        setRunMsg("");
        return;
      }

      if (backendStatus !== "online") {
        const msg = "Backend must be online. Worker fallback is disabled by design.";
        setLastError(msg);
        setResults({
          probes: [],
          selected: [],
          uncovered: targetKeys,
          coverage: { covered: 0, total: targetKeys.length, byRegion: {} },
          warnings: [],
          elapsedMs: 0,
          algorithm: "blocked",
          backendError: msg,
        });
        setIsRunning(false);
        setRunProgress(0);
        setRunMsg("");
        return;
      }

      setIsRunning(true);
      setRunProgress(0);
      setRunMsg("");
      setResults(null);
      setLastError("");
      setSafetyIssues([]);
      setMilpInfo(null);
      stopTaskPolling();

      const requestToken = activeReqRef.current + 1;
      activeReqRef.current = requestToken;
      const taskId = createTaskId();
      activeTaskIdRef.current = taskId;

      const startMs = Date.now();
      const presetConfig = PROBE_PRESETS[probePreset] || PROBE_PRESETS["NP2.0-4sh"];
      const fullProbeParams = { ...presetConfig, ...probeParams, probeType: probePreset || probeParams?.probeType || 'custom' };
      const optimizeTimeoutMs = resolveOptimizeTimeoutMs(params);
      const fallbackBudgetMs = Math.max(30000, Math.floor(optimizeTimeoutMs * 0.85));
      let keepPollingAfterTimeout = false;

      setRunMsg("task.received");
      setRunProgress(2);
      lastBackendProgressAtRef.current = Date.now();
      lastTaskProgressPctRef.current = 2;
      lastTaskStageRef.current = "task.received";

      fallbackProgressTimerRef.current = setInterval(() => {
        if (activeReqRef.current !== requestToken) return;
        const idleMs = Date.now() - (lastBackendProgressAtRef.current || 0);
        if (idleMs < FALLBACK_PROGRESS_IDLE_GRACE_MS) return;
        const elapsedMs = Date.now() - startMs;
        const heuristic = Math.max(
          5,
          Math.min(
            FALLBACK_PROGRESS_MAX_PCT,
            Math.round((elapsedMs / fallbackBudgetMs) * FALLBACK_PROGRESS_MAX_PCT)
          )
        );
        setRunProgress((prev) => {
          const base = Math.max(prev, heuristic);
          if (base >= 90 && idleMs >= FALLBACK_PROGRESS_IDLE_GRACE_MS * 2) {
            return Math.min(FALLBACK_PROGRESS_MAX_PCT, base + 1);
          }
          return base;
        });
      }, FALLBACK_PROGRESS_POLL_MS);

      taskPollTimerRef.current = setInterval(async () => {
        if (activeReqRef.current !== requestToken) return;
        try {
          const stat = await flaskFetch(
            `/api/task/status/${encodeURIComponent(taskId)}`,
            "GET",
            null,
            null,
            { timeoutMs: 7000 }
          );
          if (activeReqRef.current !== requestToken) return;
          const task = stat?.task || {};
          const taskStatus = String(task?.status || "").trim().toLowerCase();
          const pct = normalizeTaskProgressToPct(task);
          const stage = String(task?.stage || task?.message || "").trim();
          const stageChanged =
            Boolean(stage) &&
            stage !== String(lastTaskStageRef.current || "");
          if (Number.isFinite(pct)) {
            const pctRounded = Math.max(1, Math.min(99, Math.round(pct)));
            const pctAdvanced = pctRounded > Number(lastTaskProgressPctRef.current || 0);
            if (pctAdvanced || stageChanged) {
              lastBackendProgressAtRef.current = Date.now();
            }
            if (pctAdvanced) lastTaskProgressPctRef.current = pctRounded;
            setRunProgress((prev) => Math.max(prev, pctRounded));
          } else if (stageChanged) {
            lastBackendProgressAtRef.current = Date.now();
          }
          if (stage) {
            const stageLower = stage.toLowerCase();
            if (stageLower !== "queued" || !lastTaskStageRef.current) {
              setRunMsg(stage);
              lastTaskStageRef.current = stage;
            }
          }
          if (taskStatus === "completed") {
            lastBackendProgressAtRef.current = Date.now();
            const taskResult = task && typeof task.result === "object" ? task.result : null;
            if (taskResult && taskResult.ok === true) {
              activeReqRef.current += 1;
              activeTaskIdRef.current = null;
              stopTaskPolling();
              setRunProgress(100);
              setRunMsg("task.completed");
              finalise({
                ...taskResult,
                stabilitySummary: {
                  enabled: Boolean(params.stabilityTieBreakerEnabled),
                  referenceProbeCount: Array.isArray(params.stabilityReferenceTrajectories)
                    ? params.stabilityReferenceTrajectories.length
                    : 0,
                  referenceWeight: Number(params.stabilityReferenceWeight ?? 0.1),
                },
              }, targetKeys, startMs);
              return;
            }
            if (taskResult && taskResult.ok === false) {
              const detailText = Array.isArray(taskResult.details)
                ? taskResult.details.map((x) => String(x || "").trim()).filter(Boolean).join("; ")
                : "";
              const baseError = String(taskResult.error || taskResult.code || "Task failed");
              const msg = detailText ? `${baseError} (${detailText})` : baseError;
              activeReqRef.current += 1;
              activeTaskIdRef.current = null;
              stopTaskPolling();
              setIsRunning(false);
              setRunProgress(0);
              setRunMsg("task.failed");
              setLastError(msg);
              setResults(buildDiagnosticErrorResult(taskResult, msg, targetKeys, startMs));
              return;
            }
            setRunProgress((prev) => Math.max(prev, 99));
            if (!stage || String(stage).trim().toLowerCase() === "queued") {
              setRunMsg("task.finalizing");
              lastTaskStageRef.current = "task.finalizing";
            }
          } else if (
            taskStatus === "running" &&
            Number(lastTaskProgressPctRef.current || 0) >= 94 &&
            (!stage || String(stage).trim().toLowerCase() === "queued")
          ) {
            setRunMsg("task.finalizing");
            lastTaskStageRef.current = "task.finalizing";
          }
          if (taskStatus === "failed" || taskStatus === "cancelled") {
            const taskDetails = Array.isArray(task?.details)
              ? task.details
                  .map((x) => String(x || "").trim())
                  .filter(Boolean)
                  .slice(0, 3)
              : [];
            let msg = String(task?.message || task?.stage || "Task failed").trim();
            if (taskDetails.length) {
              const merged = taskDetails.join("; ");
              const lowerMsg = msg.toLowerCase();
              const hasMerged = taskDetails.some((d) => lowerMsg.includes(String(d).toLowerCase()));
              if (!hasMerged) {
                msg = msg ? `${msg} (${merged})` : merged;
              }
            }
            activeReqRef.current += 1;
            activeTaskIdRef.current = null;
            stopTaskPolling();
            setIsRunning(false);
            setRunProgress(0);
            setRunMsg(taskStatus === "cancelled" ? "task.cancelled" : "task.failed");
            setLastError(msg);
            const taskResult = task && typeof task.result === "object" ? task.result : null;
            setResults(buildDiagnosticErrorResult(taskResult, msg, targetKeys, startMs));
            return;
          }
        } catch {
          // Ignore transient task-status polling errors; optimize request remains authoritative.
        }
      }, TASK_POLL_INTERVAL_MS);

      flaskFetch(
        "/api/optimize",
        "POST",
        {
          taskId,
          targetKeys,
          params: { ...params, skullTiltDeg: params.skullTiltDeg || 0 },
          probeParams: fullProbeParams,
          constraintModes: normalizedConstraintModes,
          correctionFactors: correction || null,
          correctionUncertainty: correctionUncertainty || null,
          clinicalContext: clinicalContext || null,
        },
        null,
        { timeoutMs: optimizeTimeoutMs }
      )
        .then((data) => {
          if (activeReqRef.current !== requestToken) return;
          if (!data.ok) throw new Error(data.error || "Backend error");
          const nativeKernelActive = isNativeKernelResponse(data);
          if (nativeKernelActive) {
            const badGeometryIndex = (data.selected || []).findIndex(
              (t) => !isFiniteTriplet(t.entry_xyz) || !isFiniteTriplet(t.tip_xyz) || !isFiniteTriplet(t.dir)
            );
            if (badGeometryIndex >= 0) {
              throw makeNativeGeometryContractError(data, badGeometryIndex);
            }
          }
          setRunProgress((prev) => Math.max(prev, 97));
          setRunMsg("task.completed");

          const raw = {
            selected: (data.selected || []).map((t) => ({
              ...t,
              entry:
                (nativeKernelActive ? t.entry_xyz : null) ??
                t.entry_xyz ??
                t.entry ?? [5400 - (t.ap_mm ?? 0) * 1000, 440, 5700 + (t.ml_mm ?? 0) * 1000],
              tip: (nativeKernelActive ? t.tip_xyz : null) ?? t.tip_xyz ?? t.tip ?? [5400, 440 + (t.depthUm ?? 10000), 5700],
              dir: (nativeKernelActive ? t.dir : null) ?? t.dir ?? [0, 1, 0],
              coveredRegions: t.coveredRegions ?? [],
            })),
            uncovered: data.uncovered ?? [],
            voxelMode: true,
            algorithm: data.algorithm ?? null,
            milpStatus: data.milpStatus,
            milpNodes: data.milpNodes,
            candidateCount: data.candidateCount,
            candidateUniverseCount: data.candidateUniverseCount ?? null,
            safetyIssues: data.safetyIssues ?? [],
            correctionApplied: data.correctionApplied ?? null,
            correctionUncertainty: data.correctionUncertainty ?? null,
            requestId: data.requestId ?? null,
            engineVersion: data.engineVersion ?? null,
            runtimeIdentity: data.runtimeIdentity ?? null,
            rankingMode: data.rankingMode ?? null,
            robustOptimization: data.robustOptimization ?? null,
            paretoEnabled: data.paretoEnabled ?? null,
            objectiveWeights: data.objectiveWeights ?? null,
            paretoSummary: data.paretoSummary ?? null,
            robustParams: data.robustParams ?? null,
            paretoParams: data.paretoParams ?? null,
            objectivePreset: data.objectivePreset ?? null,
            preferencePreview: data.preferencePreview ?? null,
            preferenceProfiles: data.preferenceProfiles ?? null,
            uncertaintySummary: data.uncertaintySummary ?? null,
            drccEnabled: data.drccEnabled ?? false,
            drccSummary: data.drccSummary ?? null,
            selectiveConformalSummary: data.selectiveConformalSummary ?? null,
            stratifiedCalibration: data.stratifiedCalibration ?? null,
            statisticalContract: data.statisticalContract ?? null,
            targetingMode: data.targetingMode ?? "single_hemisphere",
            uncoveredSides: data.uncoveredSides ?? [],
            sideCoverage: data.sideCoverage ?? null,
            requiredCoverageUnits: data.requiredCoverageUnits ?? null,
            coveredCoverageUnits: data.coveredCoverageUnits ?? null,
            atlasSignature: data.atlasSignature ?? null,
            atlasHash: data.atlasHash ?? null,
            requestHash: data.requestHash ?? null,
            responseHash: data.responseHash ?? null,
            deterministicResponseHash: data.deterministicResponseHash ?? null,
            riskModel: data.riskModel ?? null,
            mriLayerStatus: data.mriLayerStatus ?? null,
            riskConstraintPolicy: data.riskConstraintPolicy ?? null,
            degradationNotices: data.degradationNotices ?? [],
            failureCards: data.failureCards ?? [],
            autoAvoidPrefilter: data.autoAvoidPrefilter ?? null,
            autoRepairSummary: data.autoRepairSummary ?? null,
            repairPreferenceSummary: data.repairPreferenceSummary ?? null,
            strictFallbackSummary: data.strictFallbackSummary ?? null,
            computeProfile: data.computeProfile ?? null,
            fallbackTargetKeys: data.fallbackTargetKeys ?? data.computeProfile?.fallbackTargetKeys ?? [],
            fallbackUncovered: data.fallbackUncovered ?? [],
            atlasBackedUncovered: data.atlasBackedUncovered ?? [],
            atlasBackedUncoveredCount: data.atlasBackedUncoveredCount ?? null,
            candidateStageBreakdown: data.candidateStageBreakdown ?? null,
            candidateFunnelDiagnostics: data.candidateFunnelDiagnostics ?? null,
            plannerCalibrationProfile: data.plannerCalibrationProfile ?? null,
            candidateCache: data.candidateCache ?? null,
            uncertaintyCalibration: data.uncertaintyCalibration ?? null,
            clinicalContract: data.clinicalContract ?? null,
            clinicalContext: data.clinicalContext ?? null,
            primaryEndpointSummary: data.primaryEndpointSummary ?? null,
            objectiveRiskConfig: data.objectiveRiskConfig ?? null,
            stabilitySummary: {
              enabled: Boolean(params.stabilityTieBreakerEnabled),
              referenceProbeCount: Array.isArray(params.stabilityReferenceTrajectories)
                ? params.stabilityReferenceTrajectories.length
                : 0,
              referenceWeight: Number(params.stabilityReferenceWeight ?? 0.1),
            },
            plannerExecutionBackend: data.plannerExecutionBackend ?? null,
            nativeEngineBypassReason: data.nativeEngineBypassReason ?? null,
          };
          finalise(raw, targetKeys, startMs);
        })
        .catch((err) => {
          if (activeReqRef.current !== requestToken) return;
          console.error("Backend error:", err);
          if (isTimeoutLikeError(err)) {
            keepPollingAfterTimeout = true;
            const timeoutText = normalizeBackendErrorMessage(err, optimizeTimeoutMs);
            setRunMsg(`${timeoutText} Waiting for backend task completion...`);
            setRunProgress((prev) => Math.max(prev, 94));
            setLastError("");
            return;
          }
          const errMsg = normalizeBackendErrorMessage(err, optimizeTimeoutMs);
          setLastError(errMsg);
          setRunMsg("");
          setIsRunning(false);
          setRunProgress(0);
          setResults(buildDiagnosticErrorResult(err?.payload, errMsg, targetKeys, startMs));
        })
        .finally(() => {
          if (keepPollingAfterTimeout) {
            return;
          }
          if (activeReqRef.current === requestToken) {
            activeTaskIdRef.current = null;
          }
          stopTaskPolling();
        });
    },
    [finalise, stopTaskPolling]
  );

  useEffect(() => () => stopTaskPolling(), [stopTaskPolling]);

  return {
    results,
    setResults,
    isRunning,
    runProgress,
    runMsg,
    lastError,
    safetyIssues,
    setSafetyIssues,
    milpInfo,
    run,
    cancel,
  };
}
