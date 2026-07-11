/**
 * ProbeplannerGUI.jsx - main UI shell.
 *
 * Notes:
 *  1. Coverage uses computeCoverage() geometry fallback.
 *  2. Algorithm timing is recorded in the frontend and displayed by solver.
 *  3. Settings panel lives on the right.
 *  4. RegionSelector includes region descriptions.
 */

import React, { useState, useCallback, useMemo, useRef, useEffect } from 'react';

import { BRAIN_REGIONS }     from './constants/brainRegions';
import { PROBE_PRESETS }     from './constants/anatomy';
import { GLOBAL_CSS }        from './constants/theme';
import { t, LANGS }          from './constants/i18n';
import { sanitizeConstraintModes } from './constants/constraintSchema';

import { useBackend, flaskFetch }        from './hooks/useBackend';
import { useOptimizer }      from './hooks/useOptimizer';

import ResizableSidebar      from './components/ResizableSidebar';
import RegionSelector        from './components/RegionSelector';
import ParameterPanel        from './components/ParameterPanel';
import ProbeSpecPanel        from './components/ProbeSpecPanel';
import ConstraintsPanel, { defaultConstraints } from './components/ConstraintsPanel';
import BackendPanel          from './components/BackendPanel';
import BrainTopView          from './components/BrainTopView';
import BrainSideView         from './components/BrainSideView';
import Brain3DView           from './components/Brain3DView';
import ResultsPanel          from './components/ResultsPanel';
import HistoryPanel          from './components/HistoryPanel';
import CoordCorrectionPanel  from './components/CoordCorrectionPanel';
import PinpointIO            from './components/PinpointIO';
import BenchmarkPanel        from './components/BenchmarkPanel';
import RegistrationQcPanel   from './components/RegistrationQcPanel';

// Colors
const C = {
  bg:        '#060e1c',
  panel:     '#08121f',
  panel2:    '#0a1828',
  border:    '#162d48',
  border2:   '#1e3d5e',
  text:      '#d8eefa',
  textSub:   '#90c8e4',
  textDim:   '#5a9ab8',
  textMuted: '#3a6888',
  accent:    '#00d4ff',
  success:   '#34d399',
  warn:      '#facc15',
  danger:    '#f87171',
};

const DEFAULT_PARAMS = {
  algorithm: 'greedy', minClearance: 1000, coverageRadius: 600,
  gridSpacing: 600, maxAngle: 15, skullTiltDeg: 0,
  targetingMode: 'single_hemisphere',
  bilateral: false, coverageBoost: false,
  robustOptimization: true,
  robustSamples: 16,
  perturbSigmaMm: 0.15,
  entrySigmaMm: 0.15,
  angleSigmaDeg: 2.0,
  depthSigmaMm: 0.2,
  riskDistanceSigmaMm: 0.08,
  riskMarginTreMm: 0,
  minRiskDistanceMm: 0.35,
  riskThresholdTemplate: 'balanced',
  paretoEnabled: true,
  paretoTopK: 12,
  objectivePreset: 'balanced',
  objectiveCoverageWeight: 0.5,
  objectiveSafetyWeight: 0.3,
  objectiveAngleWeight: 0.2,
  drccEnabled: false,
  drccRiskEpsilon: 0.08,
  drccWassersteinRadius: 0.03,
  drccEvalTopK: 240,
  drccStrict: true,
  drccPerTargetKeep: 6,
  drccCvarAlpha: 0.9,
  drccSolver: 'exact_pool',
  drccExactMaxCandidates: 42,
  drccExactTimeLimitS: 18,
  drccMisocpMaxCandidates: 56,
  drccMisocpTimeLimitS: 45,
  drccMisocpSocWeight: 0.15,
  sdfRiskIntegralWeight: 1.0,
  sdfRiskCvarWeight: 0.7,
  strictAtlasTargets: true,
  allowFallbackTargetCoverage: false,
  selectiveConformalEnabled: false,
  selectiveConformalAlpha: 0.1,
  selectiveReviewProbThreshold: 0.8,
  selectiveRejectProbThreshold: 0.35,
  targetEndpointPolicy: 'tip_in_target',
  depthOptimizationEnabled: true,
  stabilityTieBreakerEnabled: true,
  stabilityReferenceWeight: 0.1,
  stabilityEntryWeight: 1.0,
  stabilityTipWeight: 1.0,
  stabilityAngleWeight: 1.0,
};

// Algorithm descriptions
const ALGO_INFO = {
  greedy: {
    labelKey: 'algoGreedyLabel',
    descKey: 'algoGreedyDesc',
    complexity: 'O(n*k)',
    expectedMs: [100, 800],
  },
  exact: {
    labelKey: 'algoExactLabel',
    descKey: 'algoExactDesc',
    complexity: 'O(2^n) pruned',
    expectedMs: [3000, 15000],
  },
  milp: {
    labelKey: 'algoMilpLabel',
    descKey: 'algoMilpDesc',
    complexity: 'O(n^2) avg',
    expectedMs: [1000, 8000],
  },
  drcc: {
    labelKey: 'algoDrccLabel',
    descKey: 'algoDrccDesc',
    complexity: 'Risk-constrained MO',
    expectedMs: [1500, 12000],
  },
};
const LANG_LABEL_KEY = { en: 'langEnLabel', cn: 'langCnLabel' };

function normalizeNoticeLevel(level) {
  const v = String(level || '').toLowerCase();
  if (v === 'error') return 'error';
  if (v === 'warning' || v === 'warn') return 'warning';
  return 'info';
}

function getNoticeText(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.messageCn || notice.message || notice.messageEn || '').trim();
  return String(notice.messageEn || notice.message || notice.messageCn || '').trim();
}

function getNoticeTitle(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.titleCn || notice.title || notice.titleEn || '').trim();
  return String(notice.titleEn || notice.title || notice.titleCn || '').trim();
}

function getNoticeAction(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.actionCn || notice.action || notice.actionEn || '').trim();
  return String(notice.actionEn || notice.action || notice.actionCn || '').trim();
}

function buildStabilityReferences(previousResults, selectedRegions) {
  if (!previousResults || !Array.isArray(previousResults.probes)) return [];
  const requested = Array.isArray(previousResults.requestedTargets)
    ? previousResults.requestedTargets.map(String).sort()
    : [];
  const current = [...selectedRegions].map(String).sort();
  if (requested.length !== current.length || requested.some((key, index) => key !== current[index])) {
    return [];
  }
  return previousResults.probes
    .map((probe) => {
      const entry = probe.entry_xyz ?? probe.entry;
      const tip = probe.tip_xyz ?? probe.tip;
      if (!Array.isArray(entry) || entry.length !== 3 || !Array.isArray(tip) || tip.length !== 3) {
        return null;
      }
      if (![...entry, ...tip].every((value) => Number.isFinite(Number(value)))) return null;
      return {
        entryCcfUm: entry.map(Number),
        tipCcfUm: tip.map(Number),
      };
    })
    .filter(Boolean)
    .slice(0, 16);
}

function getNoticeReason(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.reasonCn || notice.reason || notice.reasonEn || notice.messageCn || notice.message || notice.messageEn || '').trim();
  return String(notice.reasonEn || notice.reason || notice.reasonCn || notice.messageEn || notice.message || notice.messageCn || '').trim();
}

function getNoticeImpact(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.impactCn || notice.impact || notice.impactEn || '').trim();
  return String(notice.impactEn || notice.impact || notice.impactCn || '').trim();
}

function getNoticeRecovery(notice, lang = 'cn') {
  if (!notice || typeof notice !== 'object') return '';
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  if (isCn) return String(notice.recoveryCn || notice.recovery || notice.recoveryEn || notice.actionCn || notice.action || notice.actionEn || '').trim();
  return String(notice.recoveryEn || notice.recovery || notice.recoveryCn || notice.actionEn || notice.action || notice.actionCn || '').trim();
}

function dedupeNotices(items) {
  const out = [];
  const seen = new Set();
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== 'object') continue;
    const key = noticeKey(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push({ ...item, _level: normalizeNoticeLevel(item.level) });
  }
  const order = { error: 0, warning: 1, info: 2 };
  out.sort((a, b) => (order[a._level] ?? 9) - (order[b._level] ?? 9));
  return out;
}

function noticeKey(notice) {
  if (!notice || typeof notice !== 'object') return '';
  return String(notice.id || notice.code || notice.messageEn || notice.messageCn || '').trim();
}

function normalizePct(raw, fallback = 0) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  if (n <= 1 && n >= 0) return Math.round(n * 100);
  return Math.max(0, Math.min(100, Math.round(n)));
}

function inferAtlasProgress(atlasInfo, backendStatus) {
  if (backendStatus === 'offline' || backendStatus === 'idle') return 0;
  if (atlasInfo?.atlasLoaded) return 100;
  const load = atlasInfo?.atlasLoad;
  if (load && typeof load === 'object') {
    const pct = normalizePct(load.progress, -1);
    if (pct >= 0) return pct;
    const loaded = Number(load.loaded || 0);
    const total = Number(load.total || 0);
    if (Number.isFinite(loaded) && Number.isFinite(total) && total > 0) {
      return normalizePct((loaded / total) * 100, 0);
    }
    const stageIndex = Number(load.stageIndex || 0);
    const stageTotal = Number(load.stageTotal || 0);
    if (Number.isFinite(stageIndex) && Number.isFinite(stageTotal) && stageTotal > 0) {
      return normalizePct(((stageIndex + 1) / stageTotal) * 100, 0);
    }
  }
  if (backendStatus === 'loading' || backendStatus === 'connecting') return 15;
  return 0;
}

function normalizeWorkflowEntry(entry, fallback = {}) {
  const row = entry && typeof entry === 'object' ? entry : {};
  const status = String(row.status || fallback.status || 'queued').trim().toLowerCase();
  const stage = String(row.stage || fallback.stage || '').trim();
  const detailRaw = String(row.message || row.detail || fallback.detail || '').trim();
  return {
    status,
    stage,
    detail: detailRaw,
    pct: normalizePct(row.progress, normalizePct(fallback.pct, 0)),
  };
}

function localizeStatusLikeText(raw, tr) {
  const text = String(raw || '').trim();
  if (!text) return '';
  const lower = text.toLowerCase();
  const canonical = lower.replace(/[\s.-]+/g, '_');
  const phraseMap = {
    task_received: tr('taskStageReceived'),
    task_completed: tr('taskStageCompleted'),
    task_finalizing: tr('taskStageFinalizing'),
    task_failed: tr('taskStageFailed'),
    task_cancelled: tr('taskStageCancelled'),
    pre_validation: tr('taskStagePreValidation'),
    payload_normalized: tr('taskStagePayloadNormalized'),
    quality_gate_check: tr('taskStageQualityGateCheck'),
    candidate_generated: tr('taskStageCandidateGenerated'),
    candidate_scored: tr('taskStageCandidateScored'),
    pareto_ranked: tr('taskStageParetoRanked'),
    solver_completed: tr('taskStageSolverCompleted'),
    coverage_boosted: tr('taskStageCoverageBoosted'),
    targeting_mode_augmented: tr('taskStageTargetingAugmented'),
    per_probe_metrics_ready: tr('taskStagePerProbeMetricsReady'),
    per_probe_metrics_computed: tr('taskStagePerProbeMetricsReady'),
    safety_contract_checked: tr('taskStageSafetyContractChecked'),
    finalizing_payload: tr('taskStageFinalizing'),
    hashing_payload: tr('taskStageHashingPayload'),
    responding: tr('taskStageResponding'),
  };
  if (phraseMap[canonical]) return phraseMap[canonical];
  if (lower === 'queued' || lower === 'pending' || lower === 'waiting' || lower === 'idle') {
    return tr('globalStageWaiting');
  }
  if (lower === 'running' || lower === 'processing' || lower === 'working') {
    return tr('running');
  }
  if (lower === 'completed' || lower === 'done' || lower === 'ready' || lower === 'success' || lower === 'ok') {
    return tr('globalStageReady');
  }
  return text;
}

function workflowLevel(status) {
  if (status === 'failed') return 'error';
  if (status === 'partial') return 'warn';
  if (status === 'completed') return 'success';
  return 'info';
}

function deriveEventTriplet(source, message, tr) {
  const reason = String(message || '').trim() || tr('eventReasonUnknown');
  if (source === 'planner') {
    return {
      reason,
      impact: tr('eventImpactPlanner'),
      recovery: tr('eventRecoveryPlanner'),
    };
  }
  if (source === 'runtime') {
    return {
      reason,
      impact: tr('eventImpactRuntime'),
      recovery: tr('eventRecoveryRuntime'),
    };
  }
  return {
    reason,
    impact: tr('eventImpactBackend'),
    recovery: tr('eventRecoveryBackend'),
  };
}

function toFinite(val) {
  const n = Number(val);
  return Number.isFinite(n) ? n : null;
}

function buildRepairOverridesFromFailureCard(card, currentParams, currentProbeParams) {
  const cf = Array.isArray(card?.counterfactualPassIf) ? card.counterfactualPassIf : [];
  const paramsPatch = {};
  const probePatch = {};
  const currentMaxAngle = toFinite(currentParams?.maxAngle);
  const currentDepthUm = toFinite(currentProbeParams?.depthUm);
  const closestDelta = card?.closestFeasibleDelta && typeof card.closestFeasibleDelta === 'object'
    ? card.closestFeasibleDelta
    : null;

  const applyDepthRetreat = (retreatUm, fallbackLimitUm = null) => {
    const retreat = Math.abs(toFinite(retreatUm) ?? 0);
    const fallbackLimit = toFinite(fallbackLimitUm);
    if (currentDepthUm === null) {
      if (fallbackLimit !== null) probePatch.depthUm = Math.max(500, Math.round(fallbackLimit));
      return;
    }
    let next = currentDepthUm;
    if (fallbackLimit !== null) next = Math.min(next, fallbackLimit);
    if (retreat > 0) next = Math.min(next, currentDepthUm - retreat);
    if (next < currentDepthUm - 1e-6) probePatch.depthUm = Math.max(500, Math.round(next));
  };

  const applySafetyFirstRepairHints = () => {
    paramsPatch.objectivePreset = 'safety_first';
    paramsPatch.constraintAwareSearch = true;
    paramsPatch.autoAvoidEnabled = true;
    paramsPatch.autoRepairEnabled = true;
    paramsPatch.strictFallbackDecompose = true;
  };

  for (const row of cf) {
    if (!row || typeof row !== 'object') continue;
    const parameter = String(row.parameter || '').trim();
    if (parameter === 'maxAngle') {
      const limit = toFinite(row.requiredAtMost);
      if (limit !== null) {
        const next = currentMaxAngle === null ? limit : Math.min(currentMaxAngle, limit);
        if (currentMaxAngle === null || next < currentMaxAngle - 1e-6) paramsPatch.maxAngle = Math.max(0.5, next);
      }
      continue;
    }
    if (parameter === 'depthUm') {
      const limit = toFinite(row.requiredAtMost);
      if (limit !== null) {
        applyDepthRetreat(0, limit);
      } else if (row.deltaUm !== undefined || row.deltaDepthUm !== undefined) {
        applyDepthRetreat(toFinite(row.deltaUm) ?? toFinite(row.deltaDepthUm));
      }
      continue;
    }
    if (parameter === 'minRiskDistanceMm') {
      // Do not weaken safety automatically. Risk threshold suggestions are
      // shown for diagnosis only; one-click repair keeps strict constraints.
      continue;
    }
    if (parameter === 'theta_deg') {
      const delta = Math.abs(toFinite(row.deltaDeg) ?? 0);
      if (delta > 0 && currentMaxAngle !== null) {
        const next = Math.max(0.5, currentMaxAngle - delta);
        if (next < currentMaxAngle - 1e-6) paramsPatch.maxAngle = next;
      }
      continue;
    }
    if (parameter === 'nearestFeasibleCandidate') {
      const deltaDepth = Math.abs(toFinite(row.deltaDepthUm) ?? 0);
      const deltaAngle = Math.abs(toFinite(row.deltaAngleDeg) ?? 0);
      applySafetyFirstRepairHints();
      paramsPatch.repairPreferredCandidate = {
        candidateId: row.candidateId ?? null,
        candidateSignature: Array.isArray(row.candidateSignature) ? row.candidateSignature : null,
        geometry: row.geometry && typeof row.geometry === 'object' ? row.geometry : null,
        deltaApUm: toFinite(row.deltaApUm),
        deltaMlUm: toFinite(row.deltaMlUm),
        deltaEntryUm: toFinite(row.deltaEntryUm),
        deltaAngleDeg: toFinite(row.deltaAngleDeg),
        deltaDepthUm: toFinite(row.deltaDepthUm),
      };
      if (deltaDepth > 0 && currentDepthUm !== null) {
        const geomDepth = toFinite(row.geometry?.depthUm);
        const nextDepth = Math.max(500, Math.round(geomDepth !== null ? Math.min(currentDepthUm, geomDepth) : currentDepthUm - deltaDepth));
        if (nextDepth < currentDepthUm - 1e-6) probePatch.depthUm = nextDepth;
      }
      if (deltaAngle > 0 && currentMaxAngle !== null) {
        const nextAngle = Math.max(0.5, currentMaxAngle - deltaAngle);
        if (nextAngle < currentMaxAngle - 1e-6) paramsPatch.maxAngle = nextAngle;
      }
    }
  }

  if (closestDelta) {
    applySafetyFirstRepairHints();
    const depthRetreatMm = Math.abs(toFinite(closestDelta.depthRetreatMm) ?? 0);
    if (depthRetreatMm > 0) applyDepthRetreat(depthRetreatMm * 1000);
    const angleDeltaDeg = Math.abs(toFinite(closestDelta.angleDeltaDeg) ?? 0);
    if (angleDeltaDeg > 0 && currentMaxAngle !== null) {
      const nextAngle = Math.max(0.5, currentMaxAngle - angleDeltaDeg);
      if (nextAngle < currentMaxAngle - 1e-6) paramsPatch.maxAngle = nextAngle;
    }
    paramsPatch.repairPreferredCandidate = {
      ...(paramsPatch.repairPreferredCandidate || {}),
      source: closestDelta.source || 'failure_card_closest_delta',
      candidateSignature: Array.isArray(card?.candidateSignature) ? card.candidateSignature : paramsPatch.repairPreferredCandidate?.candidateSignature || null,
      deltaApUm: toFinite(closestDelta.apShiftMm) !== null ? toFinite(closestDelta.apShiftMm) * 1000 : null,
      deltaMlUm: toFinite(closestDelta.mlShiftMm) !== null ? toFinite(closestDelta.mlShiftMm) * 1000 : null,
      deltaEntryUm: toFinite(closestDelta.entryShiftMm) !== null ? toFinite(closestDelta.entryShiftMm) * 1000 : null,
      deltaAngleDeg: toFinite(closestDelta.angleDeltaDeg),
      deltaDepthUm: depthRetreatMm > 0 ? depthRetreatMm * 1000 : null,
      targetSubset: Array.isArray(closestDelta.targetSubset) ? closestDelta.targetSubset : null,
    };
  }

  const changed = Object.keys(paramsPatch).length > 0 || Object.keys(probePatch).length > 0;
  if (!changed) return null;
  return { paramsPatch, probePatch };
}

// Main component
export default function ProbeplannerGUI() {

  const [lang, setLang] = useState('cn');
  const tr = useCallback((k) => t(k, lang), [lang]);

  // Data state
  const [selectedRegions, setSelectedRegions] = useState(new Set());
  const [params,          setParams]          = useState(DEFAULT_PARAMS);
  const [probePreset,     setProbePreset]     = useState('NP2.0-4sh');
  const [probeParams,     setProbeParams]     = useState(() => {
    const p = PROBE_PRESETS['NP2.0-4sh'];
    return { depthUm: p.depthUm, entrySpacingUm: p.entrySpacingUm, shanks: p.shanks };
  });
  const [constraints,  setConstraints]  = useState(defaultConstraints);
  const [correction,   setCorrection]   = useState(null);

  // Timing tracking
  const runStartRef  = useRef(null);
  const autoOpenedResultRef = useRef(null);
  const [lastRunMs,  setLastRunMs]  = useState(null);
  const [lastAlgo,   setLastAlgo]   = useState(null);

  // UI state
  const [show3D,         setShow3D]         = useState(false);
  const [highlightIdx,   setHighlightIdx]   = useState(null);
  const [sagML,          setSagML]          = useState(0);
  const [showUncovered,  setShowUncovered]  = useState(true);
  const [showAllRegions, setShowAllRegions] = useState(false);
  const [focusedRegionKey, setFocusedRegionKey] = useState(null);
  const [activeTab,      setActiveTab]      = useState('views');
  const [openCards,      setOpenCards]      = useState({
    preflight: true, params: true, probe: true, constraints: false, backend: true,
  });
  const [dismissedNoticeIds, setDismissedNoticeIds] = useState([]);
  const [eventCenterOpen, setEventCenterOpen] = useState(false);
  const [eventActionState, setEventActionState] = useState({});
  const [paramMode, setParamMode] = useState('simple');
  const [unityBridgeStage, setUnityBridgeStage] = useState({
    stage: 'idle',
    progress: 0,
    title: '',
    detail: '',
    loaded: 0,
    total: 0,
    bridgeReady: false,
    instanceReady: false,
    error: '',
  });

  // Hooks
  const {
    status: backendStatus,
    atlasInfo,
    error: backendError,
    connect,
    disconnect,
    baseUrl,
    authToken,
    atlasMode,
    setAtlasMode,
  } = useBackend();
  const optimizer = useOptimizer();

  const setConstraint = useCallback((key, mode) =>
    setConstraints(prev => ({ ...prev, [key]: mode })), []);
  const toggleCard = (key) =>
    setOpenCards(s => ({ ...s, [key]: !s[key] }));

  const availableAtlasModes = useMemo(() => {
    const fromCap = Array.isArray(atlasInfo?.supportedAtlasModes)
      ? atlasInfo.supportedAtlasModes.filter((m) => typeof m === 'string' && m.trim())
      : [];
    return fromCap.length ? fromCap : ['allen_sdk', 'qiu2018', 'mri_ccf_hybrid'];
  }, [atlasInfo]);
  const atlasModeLabelKey = useCallback((modeRaw) => {
    const mode = String(modeRaw || '').trim().toLowerCase();
    if (mode === 'qiu2018') return 'atlasModeQiu2018';
    if (mode === 'mri_ccf_hybrid') return 'atlasModeMriCcfHybrid';
    return 'atlasModeAllenSdk';
  }, []);

  const handleAtlasModeQuickSwitch = useCallback((nextModeRaw) => {
    const nextMode = String(nextModeRaw || '').trim().toLowerCase();
    if (!nextMode || nextMode === atlasMode) return;
    setAtlasMode(nextMode);
    if (backendStatus === 'idle' || backendStatus === 'connecting') return;
    connect({
      url: baseUrl,
      token: authToken || undefined,
      atlasMode: nextMode,
    });
  }, [atlasMode, setAtlasMode, backendStatus, connect, baseUrl, authToken]);

  const handleToggleRegion = useCallback((key) => {
    if (key === '__clear__') { setSelectedRegions(new Set()); setFocusedRegionKey(null); return; }
    if (key === '__selectAll__') {
      const allKeys = atlasInfo?.regions ? Object.keys(atlasInfo.regions) : Object.keys(BRAIN_REGIONS);
      setSelectedRegions(new Set(allKeys));
      return;
    }
    setFocusedRegionKey(key);
    setSelectedRegions(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }, [atlasInfo]);

  const handleRegionPicked = useCallback(({ key }) => {
    if (!key) return;
    setFocusedRegionKey(key);
  }, []);

  const runOptimization = useCallback((override = null) => {
    if (!selectedRegions.size) return;
    const patch = override && typeof override === 'object' ? override : {};
    const mergedParams = { ...params, ...(patch.params || {}) };
    const mergedProbeParams = { ...probeParams, ...(patch.probeParams || {}) };
    if (patch.params && Object.keys(patch.params).length > 0) {
      setParams((prev) => ({ ...prev, ...patch.params }));
    }
    if (patch.probeParams && Object.keys(patch.probeParams).length > 0) {
      setProbeParams((prev) => ({ ...prev, ...patch.probeParams }));
    }
    const preset = PROBE_PRESETS[probePreset] || PROBE_PRESETS['NP2.0-4sh'];
    const full   = { ...preset, ...mergedProbeParams };
    runStartRef.current = performance.now();
    setLastAlgo(mergedParams.algorithm);
    const stabilityReferences = mergedParams.stabilityTieBreakerEnabled
      ? buildStabilityReferences(optimizer.results, selectedRegions)
      : [];
    const requestParams = {
      ...mergedParams,
      depthUm: full.depthUm,
      stabilityReferenceTrajectories: stabilityReferences,
      stabilityReferenceProbeCount: stabilityReferences.length,
    };
    optimizer.run({
      selectedRegions,
      params: requestParams,
      probeParams: full, probePreset,
      constraintModes: constraints,
      backendStatus,
      backendRegions: atlasInfo?.regions ? Object.keys(atlasInfo.regions) : null,
      correction: correction?.factors || correction || null,
      correctionUncertainty: correction?.uncertainty || null,
      clinicalContext: {
        species: 'mouse',
        intendedUse: 'automatic_decision',
      },
    });
  }, [selectedRegions, params, probePreset, probeParams, constraints, backendStatus, atlasInfo, correction, optimizer]);

  const handleRun = useCallback(() => {
    runOptimization();
  }, [runOptimization]);

  const handleApplyFailureRepair = useCallback((card) => {
    const repair = buildRepairOverridesFromFailureCard(card, params, probeParams);
    if (!repair) return;
    runOptimization({
      params: repair.paramsPatch,
      probeParams: repair.probePatch,
    });
  }, [params, probeParams, runOptimization]);

  const { isRunning, results } = optimizer;
  const runError = optimizer.lastError || results?.backendError || '';
  const activeAtlasMode = String(atlasInfo?.activeAtlasMode || atlasMode || 'allen_sdk').toLowerCase();
  const atlasLoaded = Boolean(atlasInfo?.atlasLoaded);
  const regionCount = Number(atlasInfo?.regionCount ?? 0);
  const indexedRegionCount = Number(atlasInfo?.indexedRegionCount ?? 0);
  const fullBrainCount = Math.max(regionCount, indexedRegionCount);
  const fullBrainReady = useMemo(() => {
    if (backendStatus !== 'online') return false;
    if (!atlasLoaded) return false;
    if (activeAtlasMode === 'allen_sdk') {
      return Boolean(atlasInfo?.fullBrainPlannerReady) || fullBrainCount >= 1000;
    }
    return true;
  }, [backendStatus, atlasLoaded, activeAtlasMode, fullBrainCount, atlasInfo]);
  const runBlockedReason = useMemo(() => {
    if (backendStatus !== 'online') return null;
    if (!atlasLoaded) return tr('runBlockedAtlasNotReady');
    if (activeAtlasMode === 'allen_sdk' && !fullBrainReady) {
      return `${tr('runBlockedFullBrainNotReady')} (${fullBrainCount})`;
    }
    return null;
  }, [backendStatus, atlasLoaded, activeAtlasMode, fullBrainReady, fullBrainCount, tr]);
  const canRun = selectedRegions.size > 0 && !isRunning && backendStatus === 'online' && fullBrainReady;
  const backendNotices = useMemo(
    () => dedupeNotices(atlasInfo?.degradationNotices || []),
    [atlasInfo]
  );
  const systemNotices = useMemo(
    () => dedupeNotices([...(backendNotices || [])]),
    [backendNotices]
  );
  const visibleSystemNotices = useMemo(
    () => systemNotices.filter((n) => !dismissedNoticeIds.includes(noticeKey(n))),
    [systemNotices, dismissedNoticeIds]
  );
  const runStageLabel = useMemo(() => {
    const stage = String(optimizer.runMsg || '').trim();
    if (!stage) return tr('running');
    const canonicalStage = stage.toLowerCase().replace(/[\s.-]+/g, '_');
    const stageMap = {
      'task.received': tr('taskStageReceived'),
      'task.completed': tr('taskStageCompleted'),
      'task.finalizing': tr('taskStageFinalizing'),
      'task.failed': tr('taskStageFailed'),
      'task.cancelled': tr('taskStageCancelled'),
      'task_received': tr('taskStageReceived'),
      'task_completed': tr('taskStageCompleted'),
      'task_finalizing': tr('taskStageFinalizing'),
      'task_failed': tr('taskStageFailed'),
      'task_cancelled': tr('taskStageCancelled'),
      'pre_validation': tr('taskStagePreValidation'),
      'payload_normalized': tr('taskStagePayloadNormalized'),
      'quality_gate_check': tr('taskStageQualityGateCheck'),
      'candidate_generated': tr('taskStageCandidateGenerated'),
      'candidate_scored': tr('taskStageCandidateScored'),
      'pareto_ranked': tr('taskStageParetoRanked'),
      'solver_completed': tr('taskStageSolverCompleted'),
      'coverage_boosted': tr('taskStageCoverageBoosted'),
      'targeting_mode_augmented': tr('taskStageTargetingAugmented'),
      'per_probe_metrics_ready': tr('taskStagePerProbeMetricsReady'),
      'safety_contract_checked': tr('taskStageSafetyContractChecked'),
      'finalizing_payload': tr('taskStageFinalizing'),
      'hashing_payload': tr('taskStageHashingPayload'),
      'responding': tr('taskStageResponding'),
    };
    if (stageMap[stage]) return stageMap[stage];
    if (stageMap[canonicalStage]) return stageMap[canonicalStage];
    const localized = localizeStatusLikeText(stage, tr);
    if (localized !== stage) return localized;
    if (/^[a-z0-9_.-]+$/i.test(stage)) return stage.replace(/[_.-]+/g, ' ');
    return stage;
  }, [optimizer.runMsg, tr]);
  const workflowStages = useMemo(() => {
    const root = atlasInfo?.workflowState;
    if (!root || typeof root !== 'object') return {};
    const stages = root.stages;
    return stages && typeof stages === 'object' ? stages : {};
  }, [atlasInfo?.workflowState]);
  const mriProfileStatus = useMemo(() => {
    const candidates = [
      atlasInfo?.mriCcfHybrid,
      atlasInfo?.activeProfile,
      atlasInfo?.artifactContract,
    ];
    return candidates.find((item) => item && typeof item === 'object') || {};
  }, [atlasInfo?.activeProfile, atlasInfo?.artifactContract, atlasInfo?.mriCcfHybrid]);
  const registrationSummaryReady = useMemo(() => {
    if (activeAtlasMode !== 'mri_ccf_hybrid') return false;
    return Boolean(
      mriProfileStatus?.registrationSummaryConfigured ||
      mriProfileStatus?.registrationSummaryPath ||
      atlasInfo?.mriRegistrationSummary?.ok
    );
  }, [
    activeAtlasMode,
    atlasInfo?.mriRegistrationSummary?.ok,
    mriProfileStatus?.registrationSummaryConfigured,
    mriProfileStatus?.registrationSummaryPath,
  ]);
  const atlasProgress = useMemo(
    () => inferAtlasProgress(atlasInfo, backendStatus),
    [atlasInfo, backendStatus]
  );
  const riskModel = useMemo(() => atlasInfo?.riskModel || {}, [atlasInfo?.riskModel]);
  const reconstructionFlow = useMemo(
    () =>
      normalizeWorkflowEntry(workflowStages.reconstruction, {
        status: atlasLoaded ? 'completed' : backendStatus === 'online' ? 'running' : 'queued',
        pct: atlasProgress,
        detail:
          atlasLoaded
            ? tr('globalStageReady')
            : String(atlasInfo?.atlasLoad?.stage || atlasInfo?.atlasLoad?.message || '').trim() ||
              tr('globalStageWaiting'),
      }),
    [atlasInfo?.atlasLoad?.message, atlasInfo?.atlasLoad?.stage, atlasLoaded, atlasProgress, backendStatus, tr, workflowStages.reconstruction]
  );
  const registrationFlow = useMemo(() => {
    const raw = normalizeWorkflowEntry(workflowStages.registration, {
      status:
        activeAtlasMode === 'mri_ccf_hybrid'
          ? (registrationSummaryReady ? 'completed' : 'queued')
          : 'completed',
      pct:
        activeAtlasMode === 'mri_ccf_hybrid'
          ? (registrationSummaryReady ? 100 : 0)
          : 100,
      detail:
        activeAtlasMode === 'mri_ccf_hybrid'
          ? (registrationSummaryReady ? tr('globalStageReady') : tr('globalStageWaiting'))
          : tr('globalStageReady'),
    });
    const rawStatus = String(raw.status || '').toLowerCase();
    const rawStage = String(raw.stage || '').toLowerCase();
    const looksIdleQueued = (
      rawStatus === 'queued' ||
      rawStatus === 'idle' ||
      rawStage === 'idle' ||
      rawStage === 'queued'
    ) && normalizePct(raw.pct, 0) <= 0;
    if (activeAtlasMode !== 'mri_ccf_hybrid' && looksIdleQueued) {
      return {
        ...raw,
        status: 'completed',
        stage: 'not_applicable',
        pct: 100,
        detail: tr('globalStageReady'),
      };
    }
    if (registrationSummaryReady && looksIdleQueued) {
      return {
        ...raw,
        status: 'completed',
        stage: 'completed',
        pct: 100,
        detail: tr('globalStageReady'),
      };
    }
    return {
      ...raw,
      detail: localizeStatusLikeText(raw.detail, tr) || raw.detail || tr('globalStageWaiting'),
    };
  }, [activeAtlasMode, registrationSummaryReady, tr, workflowStages.registration]);
  const riskFlowRaw = useMemo(
    () =>
      normalizeWorkflowEntry(workflowStages.risk, {
        status: riskModel?.enabled === false ? 'partial' : 'queued',
        pct: 0,
        detail: tr('globalStageWaiting'),
      }),
    [riskModel?.enabled, tr, workflowStages.risk]
  );
  const planningFlowRaw = useMemo(
    () =>
      normalizeWorkflowEntry(workflowStages.planning, {
        status: isRunning ? 'running' : results ? 'completed' : 'queued',
        pct: isRunning ? normalizePct(optimizer.runProgress, 0) : results ? 100 : 0,
        detail: isRunning ? runStageLabel : results ? tr('globalStageReady') : tr('globalStageWaiting'),
      }),
    [isRunning, optimizer.runProgress, results, runStageLabel, tr, workflowStages.planning]
  );
  const riskState = useMemo(
    () =>
      String(
        riskFlowRaw.status ||
          riskModel?.state ||
          riskModel?.sdfBuildState ||
          ''
      )
        .trim()
        .toLowerCase(),
    [riskFlowRaw.status, riskModel?.state, riskModel?.sdfBuildState]
  );
  const riskProgress = useMemo(() => {
    if (!atlasLoaded) return 0;
    if (Number.isFinite(Number(riskFlowRaw.pct)) && Number(riskFlowRaw.pct) > 0) {
      return normalizePct(riskFlowRaw.pct, 0);
    }
    if (riskModel?.enabled === false) return 100;
    const pct = normalizePct(riskModel?.progress, -1);
    if (pct >= 0) return pct;
    if (riskState === 'ready' || riskState === 'ok' || riskState === 'enabled') return 100;
    if (
      riskState === 'building' ||
      riskState === 'initializing' ||
      riskState === 'loading' ||
      riskState === 'pending' ||
      riskState === 'starting'
    ) return 55;
    if (riskState === 'failed' || riskState === 'error' || riskState === 'unavailable') return 100;
    if (Number(riskModel?.surfacePointCount || 0) > 0) return 100;
    return atlasLoaded ? 90 : 0;
  }, [atlasLoaded, riskFlowRaw.pct, riskModel, riskState]);
  const riskDetailLabel = useMemo(() => {
    if (riskFlowRaw.detail) return localizeStatusLikeText(riskFlowRaw.detail, tr);
    if (riskModel?.enabled === false) return tr('globalRiskDisabled');
    const msg = String(riskModel?.sdfBuildMessage || riskModel?.state || riskModel?.sdfBuildState || '').trim();
    if (!msg) return tr('globalStageWaiting');
    if (/^(ready|ok|enabled)$/i.test(msg) || /loaded cached sdf/i.test(msg)) return tr('globalStageReady');
    return msg;
  }, [riskFlowRaw.detail, riskModel?.enabled, riskModel?.sdfBuildMessage, riskModel?.state, riskModel?.sdfBuildState, tr]);
  const plannerProgress = useMemo(() => {
    if (Number.isFinite(Number(planningFlowRaw.pct)) && Number(planningFlowRaw.pct) > 0) {
      return normalizePct(planningFlowRaw.pct, 0);
    }
    if (isRunning) return normalizePct(optimizer.runProgress, 0);
    if (results) return 100;
    return 0;
  }, [isRunning, optimizer.runProgress, planningFlowRaw.pct, results]);
  const planningFlow = useMemo(
    () => ({
      ...planningFlowRaw,
      pct: plannerProgress,
      detail:
        isRunning
          ? runStageLabel
          : localizeStatusLikeText(planningFlowRaw.detail, tr) ||
            (plannerProgress >= 100 ? tr('globalStageReady') : tr('globalStageWaiting')),
      status:
        isRunning
          ? 'running'
          : plannerProgress >= 100
            ? 'completed'
            : planningFlowRaw.status || 'queued',
    }),
    [isRunning, plannerProgress, planningFlowRaw, runStageLabel, tr]
  );
  const riskFlow = useMemo(
    () => ({
      ...riskFlowRaw,
      pct: riskProgress,
      detail: riskDetailLabel,
      status: riskFlowRaw.status || (riskProgress >= 100 ? 'completed' : 'queued'),
    }),
    [riskDetailLabel, riskFlowRaw, riskProgress]
  );
  const globalStages = useMemo(() => ([
    {
      key: 'atlas',
      label: tr('globalStageAtlas'),
      pct: normalizePct(reconstructionFlow.pct, atlasProgress),
      detail:
        reconstructionFlow.detail ||
        (atlasLoaded
          ? tr('globalStageReady')
          : String(atlasInfo?.atlasLoad?.stage || '').trim() || tr('globalStageWaiting')),
      level: workflowLevel(reconstructionFlow.status),
    },
    {
      key: 'registration',
      label: tr('globalStageRegistration'),
      pct: normalizePct(registrationFlow.pct, 0),
      detail: registrationFlow.detail || tr('globalStageWaiting'),
      level: workflowLevel(registrationFlow.status),
    },
    {
      key: 'risk',
      label: tr('globalStageRisk'),
      pct: normalizePct(riskFlow.pct, riskProgress),
      detail: riskFlow.detail,
      level:
        riskModel?.enabled === false
          ? 'warn'
          : workflowLevel(riskFlow.status),
    },
    {
      key: 'planner',
      label: tr('globalStagePlanner'),
      pct: normalizePct(planningFlow.pct, plannerProgress),
      detail: planningFlow.detail,
      level: runError ? 'error' : workflowLevel(planningFlow.status),
    },
  ]), [
    atlasInfo?.atlasLoad?.stage,
    atlasLoaded,
    atlasProgress,
    planningFlow,
    plannerProgress,
    reconstructionFlow,
    registrationFlow,
    riskFlow,
    riskModel?.enabled,
    runError,
    tr,
    riskProgress,
  ]);
  const preflightItems = useMemo(() => {
    const plannerRegions = Number(atlasInfo?.plannerRegionCount ?? 0);
    return [
      {
        key: 'backend',
        label: tr('preflightBackend'),
        ok: backendStatus === 'online',
        detail: backendStatus === 'online' ? tr('backendOnline') : tr('backendOffline'),
      },
      {
        key: 'atlas',
        label: tr('preflightAtlas'),
        ok: Boolean(atlasLoaded),
        detail: atlasLoaded ? `${tr('globalStageReady')} (${fullBrainCount})` : tr('runBlockedAtlasNotReady'),
      },
      {
        key: 'risk',
        label: tr('preflightRisk'),
        ok: Boolean(atlasLoaded),
        warn: riskModel?.enabled === false,
        detail: riskDetailLabel,
      },
      {
        key: 'target',
        label: tr('preflightTargets'),
        ok: selectedRegions.size > 0,
        detail: `${selectedRegions.size}`,
      },
      {
        key: 'planner',
        label: tr('preflightPlannerRegion'),
        ok: plannerRegions > 0 || fullBrainCount > 0,
        detail: String(plannerRegions || fullBrainCount || 0),
      },
    ];
  }, [atlasInfo, atlasLoaded, backendStatus, fullBrainCount, riskDetailLabel, riskModel?.enabled, selectedRegions.size, tr]);
  const preflightReady = useMemo(
    () => preflightItems.every((item) => item.ok || item.warn),
    [preflightItems]
  );
  const mriRepairBundle = useMemo(() => {
    const mri = atlasInfo?.mriCcfHybrid || {};
    if (!mri || typeof mri !== 'object') return null;
    const payload = {
      manifestPath: mri.manifestPath || undefined,
      activeProfilePath: mri.activeProfilePath || undefined,
      annotationPath: mri.annotationPath || undefined,
      regionMapPath: mri.regionMapPath || undefined,
      regionMapV2Path: mri.regionMapV2Path || undefined,
      statsPath: mri.statsPath || undefined,
      registrationSummaryPath: mri.registrationSummaryPath || undefined,
      jacobianPath: mri.jacobianPath || undefined,
      skullMaskPath: mri.skullMaskPath || undefined,
      outerTissueMaskPath: mri.outerTissueMaskPath || undefined,
      layeredSdfPath: mri.layeredSdfPath || undefined,
      datasetProfile: mri.datasetProfile || 'custom',
      switchMode: true,
    };
    const keys = Object.keys(payload);
    const hasAnyPath = keys.some((k) => /Path$/.test(k) && String(payload[k] || '').trim().length > 0);
    return hasAnyPath ? payload : null;
  }, [atlasInfo]);

  const runEventRepair = useCallback(async (eventItem) => {
    const eventId = String(eventItem?.id || '').trim();
    if (!eventId) return;
    setEventActionState((prev) => ({ ...prev, [eventId]: { running: true, error: '' } }));
    try {
      const action = eventItem?.repairAction || {};
      const mode = String(action.mode || 'activate').toLowerCase();
      const payload = (action.payload && typeof action.payload === 'object')
        ? action.payload
        : (mriRepairBundle || {});
      if (mode === 'reconstruct') {
        await flaskFetch('/api/mri/reconstruct/start', 'POST', {
          ...payload,
          autoBuild: false,
          activateAfterBuild: true,
          switchMode: true,
        }, null, { timeoutMs: 90000 });
      } else {
        await flaskFetch('/api/mri/activate', 'POST', {
          ...payload,
          switchMode: true,
        }, null, { timeoutMs: 90000 });
      }
      await connect({
        url: baseUrl,
        token: authToken || undefined,
        atlasMode: 'mri_ccf_hybrid',
      });
      setEventActionState((prev) => ({ ...prev, [eventId]: { running: false, error: '' } }));
    } catch (err) {
      setEventActionState((prev) => ({
        ...prev,
        [eventId]: {
          running: false,
          error: String(err?.message || err || 'repair failed'),
        },
      }));
    }
  }, [authToken, baseUrl, connect, mriRepairBundle]);
  const eventItems = useMemo(() => {
    const list = [];
    if (runError) {
      const triplet = deriveEventTriplet('planner', runError, tr);
      list.push({
        id: `run:${runError}`,
        level: 'error',
        title: tr('optimizationFailed'),
        message: runError,
        source: tr('eventSourcePlanner'),
        reason: triplet.reason,
        impact: triplet.impact,
        recovery: triplet.recovery,
      });
    }
    if (backendError) {
      const triplet = deriveEventTriplet('backend', backendError, tr);
      list.push({
        id: `backend:${backendError}`,
        level: 'error',
        title: tr('backendError'),
        message: backendError,
        source: tr('eventSourceBackend'),
        reason: triplet.reason,
        impact: triplet.impact,
        recovery: triplet.recovery,
      });
    }
    visibleSystemNotices.forEach((notice) => {
      const level = normalizeNoticeLevel(notice._level || notice.level);
      const noticeMessage = getNoticeText(notice, lang);
      const fallbackTriplet = deriveEventTriplet('backend', noticeMessage, tr);
      const code = String(notice?.code || '').trim();
      const missingArtifacts = Array.isArray(atlasInfo?.missingArtifacts) ? atlasInfo.missingArtifacts : [];
      list.push({
        id: noticeKey(notice) || `${level}:${getNoticeText(notice, lang)}`,
        level,
        title: getNoticeTitle(notice, lang) || tr('eventCenterTitle'),
        message: noticeMessage,
        action: getNoticeAction(notice, lang),
        source: tr('eventSourceBackend'),
        reason: getNoticeReason(notice, lang) || fallbackTriplet.reason,
        impact: getNoticeImpact(notice, lang) || fallbackTriplet.impact,
        recovery: getNoticeRecovery(notice, lang) || fallbackTriplet.recovery,
        missingArtifacts: code === 'mri_artifact_incomplete' ? missingArtifacts : [],
        repairAction:
          code === 'mri_artifact_incomplete'
            ? { mode: 'activate', label: tr('eventRepairBtn') }
            : null,
      });
    });
    if (
      String(atlasMode || '').toLowerCase() === 'mri_ccf_hybrid' &&
      Array.isArray(atlasInfo?.missingArtifacts) &&
      atlasInfo.missingArtifacts.length > 0 &&
      !list.some((item) => String(item.id || '').includes('mri_artifact_incomplete'))
    ) {
      list.push({
        id: 'mri_artifact_incomplete:status',
        level: 'warning',
        title: tr('eventMriArtifactIncompleteTitle'),
        message: tr('eventMriArtifactIncompleteMsg'),
        source: tr('eventSourceBackend'),
        reason: `${tr('eventMissingArtifactsLabel')}: ${atlasInfo.missingArtifacts.join(', ')}`,
        impact: tr('eventMriArtifactImpact'),
        recovery: tr('eventMriArtifactRecovery'),
        missingArtifacts: atlasInfo.missingArtifacts,
        repairAction: { mode: 'activate', label: tr('eventRepairBtn') },
      });
    }
    if (unityBridgeStage?.error) {
      const triplet = deriveEventTriplet('runtime', unityBridgeStage.error, tr);
      list.push({
        id: `unity:${unityBridgeStage.error}`,
        level: 'error',
        title: tr('unityErrorLabel'),
        message: unityBridgeStage.error,
        source: tr('eventSourceRuntime'),
        reason: triplet.reason,
        impact: triplet.impact,
        recovery: triplet.recovery,
      });
    }
    const seen = new Set();
    return list.filter((item) => {
      if (!item.id || seen.has(item.id)) return false;
      seen.add(item.id);
      return !dismissedNoticeIds.includes(item.id);
    });
  }, [
    atlasInfo?.missingArtifacts,
    atlasMode,
    backendError,
    dismissedNoticeIds,
    lang,
    runError,
    tr,
    unityBridgeStage,
    visibleSystemNotices,
  ]);

  const copyDiagnostics = useCallback(async () => {
    const payload = {
      time: new Date().toISOString(),
      backendStatus,
      backendError,
      atlasMode,
      atlasLoaded,
      atlasLoad: atlasInfo?.atlasLoad || null,
      activeProfile: atlasInfo?.activeProfile || null,
      modeContracts: atlasInfo?.modeContracts || null,
      artifactContract: atlasInfo?.artifactContract || null,
      mriProfile: atlasInfo?.mriProfile || null,
      mriLayerStatus: atlasInfo?.mriLayerStatus || null,
      artifactReady: atlasInfo?.artifactReady ?? null,
      missingArtifacts: atlasInfo?.missingArtifacts || [],
      degradationReasons: atlasInfo?.degradationReasons || [],
      riskModel: atlasInfo?.riskModel || null,
      reconstructionQuality: atlasInfo?.reconstructionQuality || null,
      planner: {
        isRunning,
        runProgress: optimizer.runProgress || 0,
        runStage: optimizer.runMsg || '',
        runError: runError || '',
      },
      unity: unityBridgeStage,
      notices: eventItems.map((item) => ({
        level: item.level,
        title: item.title,
        source: item.source,
        message: item.message,
        reason: item.reason || '',
        impact: item.impact || '',
        recovery: item.recovery || '',
      })),
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    } catch {
      // ignore clipboard errors
    }
  }, [
    atlasInfo,
    atlasLoaded,
    atlasMode,
    backendError,
    backendStatus,
    eventItems,
    isRunning,
    optimizer.runMsg,
    optimizer.runProgress,
    runError,
    unityBridgeStage,
  ]);

  const dismissEvent = useCallback((eventId) => {
    const key = String(eventId || '').trim();
    if (!key) return;
    setDismissedNoticeIds((prev) => (prev.includes(key) ? prev : [...prev, key]));
  }, []);
  useEffect(() => {
    if (runError) {
      setActiveTab('results');
      setEventCenterOpen(true);
    }
  }, [runError]);

  useEffect(() => {
    if (!results || results.backendError || !Array.isArray(results.probes)) return;
    const identity = results.requestId || results.responseHash || results;
    if (autoOpenedResultRef.current === identity) return;
    autoOpenedResultRef.current = identity;
    setActiveTab('results');
  }, [results]);

  useEffect(() => {
    const onFocusBackendPanel = () => {
      setOpenCards((prev) => ({ ...prev, backend: true, preflight: true }));
      setEventCenterOpen(true);
    };
    window.addEventListener('probeplanner:focus-backend-panel', onFocusBackendPanel);
    return () => window.removeEventListener('probeplanner:focus-backend-panel', onFocusBackendPanel);
  }, []);

  // Record elapsed time when results complete.
  const prevResultsRef = useRef(null);
  useEffect(() => {
    if (!results || results === prevResultsRef.current) return;
    prevResultsRef.current = results;
    if (runStartRef.current) {
      setLastRunMs(Math.round(performance.now() - runStartRef.current));
      runStartRef.current = null;
    }
  }, [results]);

  // Result normalization
  // useOptimizer.finalise() already builds the correct shape; we only need to
  // re-localise warnings when the UI language changes.
  const resultsForPanel = useMemo(() => {
    if (!results) return null;
    return {
      ...results,
      warnings: (optimizer.safetyIssues || []).map(iss => ({
        probeIdx: iss.probeIdx ?? 0,
        message:  String(lang || '').toLowerCase().startsWith('cn')
          ? (iss.msgCn || iss.msgEn || '')
          : (iss.msgEn || iss.msgCn || ''),
      })),
    };
  }, [results, optimizer.safetyIssues, lang]);

  // 3D probes use selected payloads so native entry/tip trajectory fields are preserved.
  const probesFor3D = useMemo(() => {
    return results?.selected || [];
  }, [results]);

  const handlePinpointImport = useCallback((importedProbes) => {
    // importedProbes are raw trajectory objects from parsePinpoint (fields: ap_mm, ml_mm, etc.)
    // Normalise to the shape ResultsPanel / BrainViews expect
    const probes = importedProbes.map((p, i) => ({
      ap:        p.ap_mm       ?? 0,
      ml:        p.ml_mm       ?? 0,
      dvEntry:   p.entryDvCorr_mm ?? 0,
      azimuth:   p.phi_deg     ?? 0,
      elevation: 90 - (p.theta_deg ?? 0),
      depth:     (p.depthUm    ?? 10000) / 1000,
      regionsHit: p.coveredRegions || [],
      violations: [],
    }));
    const coveredSet = new Set(probes.flatMap(p => p.regionsHit || []));
    const total = Math.max(selectedRegions.size, coveredSet.size);
    optimizer.setResults({
      probes,
      // Keep selected for BrainTopView / BrainSideView backward compat
      selected: importedProbes,
      coverage: {
        covered:  coveredSet.size,
        total,
        fraction: total > 0 ? coveredSet.size / total : 0,
        byRegion: {},
      },
      warnings:  [],
      elapsedMs: 0,
      algorithm: 'import',
    });
    optimizer.setSafetyIssues([]);
  }, [optimizer, selectedRegions]);

  const currentSnapshot = useMemo(() => ({
    regions: [...selectedRegions], params: { ...params }, probePreset,
    results: resultsForPanel, constraints,
  }), [selectedRegions, params, probePreset, resultsForPanel, constraints]);

  const restoreSnap = useCallback((snap) => {
    if (snap.regions)     setSelectedRegions(new Set(snap.regions));
    if (snap.params)      setParams(p => ({ ...p, ...snap.params }));
    if (snap.probePreset) setProbePreset(snap.probePreset);
    if (snap.constraints) setConstraints(sanitizeConstraintModes(snap.constraints));
  }, []);

  // Current algorithm metadata.
  const algoInfo = ALGO_INFO[params.algorithm] || ALGO_INFO.greedy;
  const algoLabel = useCallback((algoKey) => {
    const info = ALGO_INFO[algoKey];
    return info ? tr(info.labelKey) : algoKey;
  }, [tr]);

  // Render
  return (
    <>
      <style>{GLOBAL_CSS + EXTRA_CSS}</style>
      <div style={S.root} className="pp-root">

        {/* Top bar */}
        <header style={S.topbar} className="pp-topbar">
          <div style={S.topLeft}>
            <span style={S.logoMark}>BP</span>
            <div>
              <div style={S.appTitle}>{tr('appTitle')}</div>
              <div style={S.appSub}>{tr('appSubtitle')}</div>
            </div>
          </div>

          <div style={S.topCenter}>
            <Chip active={showUncovered}  onClick={() => setShowUncovered(v=>!v)}  label={tr('showUncovered')} />
            <Chip active={showAllRegions} onClick={() => setShowAllRegions(v=>!v)} label={tr('showAllRegions')} />

            {/* Algorithm selector */}
            <div style={S.algoRow}>
              {Object.entries(ALGO_INFO).map(([k, info]) => (
                <button key={k}
                  onClick={() => setParams(p => ({ ...p, algorithm: k }))}
                  title={tr(info.descKey)}
                  style={{
                    ...S.algoBtn,
                    background: params.algorithm===k ? C.accent+'1c' : 'none',
                    color:      params.algorithm===k ? C.accent      : C.textMuted,
                    borderColor:params.algorithm===k ? C.accent+'44' : C.border,
                  }}>
                  {tr(info.labelKey)}
                  {params.algorithm===k && (
                    <span style={{ fontSize:8, color:C.textMuted, display:'block', lineHeight:1.2 }}>
                      ~{(info.expectedMs[0] / 1000).toFixed(1)}-{(info.expectedMs[1] / 1000).toFixed(1)}s
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <div style={S.topRight}>
            <div style={S.langRow}>
              {LANGS.map(l => (
                <button key={l}
                  style={{ ...S.langBtn, ...(lang===l ? S.langOn : {}) }}
                  onClick={() => setLang(l)}>
                  {t(LANG_LABEL_KEY[l] || 'lang', l)}
                </button>
              ))}
            </div>

            <div style={S.modeQuickWrap}>
              <span style={S.modeQuickLabel}>{tr('atlasModeLabel')}</span>
              <select
                style={{ ...S.modeQuickSelect, opacity: backendStatus === 'connecting' ? 0.65 : 1 }}
                value={atlasMode}
                onChange={(e) => handleAtlasModeQuickSwitch(e.target.value)}
                disabled={backendStatus === 'connecting'}
              >
                {availableAtlasModes.map((mode) => {
                  return (
                    <option key={mode} value={mode}>
                      {tr(atlasModeLabelKey(mode))}
                    </option>
                  );
                })}
              </select>
            </div>

            <StatusBadge status={backendStatus} tr={tr} />

            {/* Timing display */}
            {lastRunMs !== null && !isRunning && (
              <div style={{
                fontSize:10, color: lastRunMs > 5000 ? C.warn : C.success,
                background: C.panel2, border:`1px solid ${C.border}`,
                borderRadius:6, padding:'3px 9px', fontFamily:'monospace',
                flexShrink:0,
              }}>
                Time {lastRunMs < 1000 ? `${lastRunMs}ms` : `${(lastRunMs/1000).toFixed(1)}s`}
                {' · '}{algoLabel(lastAlgo)}
              </div>
            )}

            <button style={{
              ...S.runBtn,
              background: isRunning ? '#ef444415' : canRun ? C.accent+'1c' : C.panel2,
              color:      isRunning ? C.danger    : canRun ? C.accent      : C.textMuted,
              boxShadow:  (canRun && !isRunning) ? `0 0 20px ${C.accent}2a` : 'none',
              cursor:     (canRun || isRunning) ? 'pointer' : 'not-allowed',
            }}
              onClick={isRunning ? () => optimizer.cancel() : handleRun}
              disabled={!canRun && !isRunning}>
              {isRunning ? <><Spin /> {tr('cancelBtn')}</> : tr('runOptimize')}
            </button>

            {!isRunning && runBlockedReason && (
              <div style={{
                maxWidth: 280,
                fontSize: 10.5,
                lineHeight: 1.35,
                color: C.warn,
                background: '#3a2a06',
                border: `1px solid ${C.warn}66`,
                borderRadius: 6,
                padding: '4px 8px',
              }}>
                {runBlockedReason}
              </div>
            )}

            {isRunning && (
              <div style={S.progressWrap}>
                <div style={S.prog}>
                  <div style={{ ...S.progBar, width:`${optimizer.runProgress||0}%` }} />
                  <span style={S.progTxt}>{optimizer.runProgress||0}%</span>
                </div>
                <span style={S.progressStage} title={runStageLabel}>
                  {runStageLabel}
                </span>
              </div>
            )}

            <div style={S.eventDock}>
              <button
                data-testid="event-center-toggle"
                style={{ ...S.eventToggleBtn, ...(eventCenterOpen ? S.eventToggleBtnOn : null) }}
                onClick={() => setEventCenterOpen((v) => !v)}
              >
                {tr('eventCenterTitle')} ({eventItems.length})
              </button>
              {eventCenterOpen && (
                <div data-testid="event-center-panel" style={S.eventPanel} className="bp-scrollbar bp-scrollbar--thin">
                  <div style={S.eventPanelHead}>
                    <span>{tr('eventCenterTitle')}</span>
                    <button data-testid="event-center-copy" style={S.eventCopyBtn} onClick={copyDiagnostics}>
                      {tr('eventCenterCopyDiag')}
                    </button>
                  </div>
                  {eventItems.length === 0 ? (
                    <div style={S.eventEmpty}>{tr('eventCenterEmpty')}</div>
                  ) : (
                    eventItems.map((item) => {
                      const color = item.level === 'error' ? C.danger : item.level === 'warning' ? C.warn : C.accent;
                      return (
                        <div key={item.id} style={{ ...S.eventItem, borderColor: `${color}66` }}>
                          <div style={S.eventTitleRow}>
                            <span style={{ ...S.eventLevelDot, background: color }} />
                            <span style={S.eventTitle}>{item.title}</span>
                            <span style={S.eventSource}>{item.source}</span>
                          </div>
                          <div style={S.eventMsg}>{item.message}</div>
                          <div style={S.eventTriplet}>
                            {item.reason ? (
                              <div style={S.eventTripletRow}>
                                <span style={S.eventTripletLabel}>{tr('eventReasonLabel')}</span>
                                <span style={S.eventTripletText}>{item.reason}</span>
                              </div>
                            ) : null}
                            {item.impact ? (
                              <div style={S.eventTripletRow}>
                                <span style={S.eventTripletLabel}>{tr('eventImpactLabel')}</span>
                                <span style={S.eventTripletText}>{item.impact}</span>
                              </div>
                            ) : null}
                            {item.recovery ? (
                              <div style={S.eventTripletRow}>
                                <span style={S.eventTripletLabel}>{tr('eventRecoveryLabel')}</span>
                                <span style={S.eventTripletText}>{item.recovery}</span>
                              </div>
                            ) : null}
                          </div>
                          {item.action ? <div style={S.eventAction}>{item.action}</div> : null}
                          {Array.isArray(item.missingArtifacts) && item.missingArtifacts.length > 0 ? (
                            <div style={S.eventAction}>
                              <span style={S.eventTripletLabel}>{tr('eventMissingArtifactsLabel')}</span>{' '}
                              {item.missingArtifacts.join(', ')}
                            </div>
                          ) : null}
                          {item.repairAction ? (
                            <div style={S.eventItemFooter}>
                              <button
                                style={S.eventRepairBtn}
                                onClick={() => runEventRepair(item)}
                                disabled={Boolean(eventActionState[item.id]?.running)}
                              >
                                {eventActionState[item.id]?.running
                                  ? tr('eventRepairRunning')
                                  : (item.repairAction.label || tr('eventRepairBtn'))}
                              </button>
                              {eventActionState[item.id]?.error ? (
                                <span style={S.eventRepairErr}>{eventActionState[item.id]?.error}</span>
                              ) : null}
                            </div>
                          ) : null}
                          <div style={S.eventItemFooter}>
                            <button style={S.eventDismissBtn} onClick={() => dismissEvent(item.id)}>
                              {tr('eventDismiss')}
                            </button>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>
        </header>
        <div data-testid="global-stage-bar" style={S.globalStageBar} className="pp-global-stage">
          {globalStages.map((stage) => {
            const color =
              stage.level === 'error'
                ? C.danger
                : stage.level === 'warn'
                  ? C.warn
                  : stage.level === 'success'
                    ? C.success
                    : C.accent;
            return (
              <div key={stage.key} data-testid={`global-stage-${stage.key}`} style={S.globalStageItem}>
                <div style={S.globalStageHead}>
                  <span>{stage.label}</span>
                  <span style={{ color }}>{stage.pct}%</span>
                </div>
                <div style={S.globalStageTrack}>
                  <div style={{ ...S.globalStageFill, width: `${stage.pct}%`, background: color }} />
                </div>
                <div style={S.globalStageDetail} title={stage.detail}>{stage.detail}</div>
              </div>
            );
          })}
        </div>

        {/* Main three-column layout */}
        <div style={S.main} className="pp-main">

          {/* Left sidebar: region selector */}
          <ResizableSidebar defaultWidth={225} minWidth={160} maxWidth={360} side="left"
            className="pp-sidebar pp-sidebar-left"
            style={{ background:C.panel, borderRight:`1px solid ${C.border}`,
              display:'flex', flexDirection:'column', minHeight:0 }}>
            <SideHdr icon="R" title={tr('selectRegions')}>
              <span style={S.badge}>{selectedRegions.size}</span>
            </SideHdr>
            <div style={{ flex:1, minHeight:0, overflow:'hidden', display:'flex', flexDirection:'column', minWidth:0 }}>
              <div style={S.leftModeRow}>
                <span style={S.leftModeLabel}>{tr('atlasModeLabel')}</span>
                <select
                  value={atlasMode}
                  onChange={(e) => handleAtlasModeQuickSwitch(e.target.value)}
                  style={S.leftModeSelect}
                  disabled={backendStatus === 'connecting'}
                >
                  {availableAtlasModes.map((mode) => {
                    return (
                      <option key={mode} value={mode}>
                        {tr(atlasModeLabelKey(mode))}
                      </option>
                    );
                  })}
                </select>
              </div>
              <RegionSelector
                selectedRegions={selectedRegions}
                onToggleRegion={handleToggleRegion}
                results={results}
                focusedRegion={focusedRegionKey}
                regionCatalog={atlasInfo?.regions || null}
                supportedRegionKeys={atlasInfo?.regions ? Object.keys(atlasInfo.regions) : null}
                catalogRegionCount={atlasInfo?.catalogRegionCount ?? null}
                presentRegionCount={atlasInfo?.presentRegionCount ?? null}
                lang={lang}
              />
            </div>
          </ResizableSidebar>

          {/* Center column: views */}
          <div style={S.colMid} className="pp-center">

            <nav style={S.tabBar}>
              {[
                { id:'views',      icon:'V', label:tr('regions') },
                { id:'results',    icon:'R',
                  label:tr('results') +
                    (resultsForPanel ? ` (${resultsForPanel.probes.length})` : '') },
                { id:'pinpoint',   icon:'I', label:tr('pinpointIoLabel') },
                { id:'history',    icon:'H', label:tr('historyLabel') },
                { id:'correction', icon:'C', label:tr('coordCorrection') },
                { id:'regqc',      icon:'Q', label:tr('registrationQcLabel') },
                { id:'benchmark',  icon:'B', label:tr('benchmarkLabel') },
              ].map(({ id, icon, label }) => (
                <button key={id}
                  style={{ ...S.tabBtn, ...(activeTab===id ? S.tabActive : {}) }}
                  onClick={() => setActiveTab(id)}>
                  <span>{icon}</span>{label}
                </button>
              ))}

              {/* Coverage summary */}
              {resultsForPanel && (
                <div style={{
                  marginLeft:'auto', padding:'0 14px',
                  display:'flex', alignItems:'center', gap:8,
                }}>
                  <CoverageBar
                    covered={resultsForPanel.coverage.covered}
                    total={resultsForPanel.coverage.total}
                  />
                </div>
              )}
            </nav>

            {/* View tabs */}
            {activeTab === 'views' && (
              <div style={S.viewsArea}>
                <div style={S.viewGrid} className="pp-view-grid">
                  <div style={S.viewPane}>
                    <PaneHdr icon="T" label={tr('topView')} />
                    <div style={S.paneBody}>
                      <BrainTopView
                        selectedRegions={selectedRegions} results={resultsForPanel} lang={lang}
                        hoveredProbe={highlightIdx} onHoverProbe={setHighlightIdx}
                        showUncovered={showUncovered} showAllRegions={showAllRegions}
                      />
                    </div>
                  </div>
                  <div style={S.viewPane}>
                    <PaneHdr icon="S" label={tr('sideView')} />
                    <div style={S.paneBody}>
                      <BrainSideView
                        selectedRegions={selectedRegions} results={resultsForPanel} lang={lang}
                        hoveredProbe={highlightIdx} onHoverProbe={setHighlightIdx}
                        showUncovered={showUncovered} showAllRegions={showAllRegions}
                        sagML={sagML} onSagMLChange={setSagML}
                      />
                    </div>
                  </div>
                </div>

                {/* 3D view */}
                <div style={S.d3Wrap}>
                  <button
                    style={S.d3Toggle}
                    onClick={() => setShow3D(s=>!s)}
                    data-testid="probeplanner-toggle-3d"
                    aria-label={tr('view3d')}
                  >
                    <span style={{ fontSize:9, color:C.textMuted }}>{show3D ? '-' : '+'}</span>
                    <span style={S.d3Label}>{tr('view3d')}</span>
                    {probePreset && <span style={S.smallTag}>{probePreset}</span>}
                    {probesFor3D.length > 0 && (
                      <span style={{ ...S.smallTag, color:C.success, background:C.success+'18',
                        borderColor:C.success+'44' }}>
                        {probesFor3D.length} {tr('probes')}
                      </span>
                    )}
                    <span style={{ marginLeft:'auto', fontSize:9, color:C.textMuted }}>
                      {show3D ? tr('hideLabel') : `${tr('showLabel')} · ${tr('zoomHint')}`}
                    </span>
                  </button>
                  {show3D && (
                    <div style={S.d3Body}>
                      <Brain3DView
                        probes={probesFor3D}
                        selectedRegions={[...selectedRegions]}
                        brainRegions={atlasInfo?.regions || BRAIN_REGIONS}
                        activeAtlasMode={activeAtlasMode}
                        mriConfig={atlasInfo?.mriCcfHybrid || null}
                        mriLayerStatus={atlasInfo?.mriLayerStatus || null}
                        mriSurfaceMeshes={atlasInfo?.mriSurfaceMeshes || null}
                        focusedRegion={focusedRegionKey}
                        onToggleRegion={handleToggleRegion}
                        onRegionPicked={handleRegionPicked}
                        backendBaseUrl={baseUrl}
                        authToken={authToken}
                        backendStatus={backendStatus}
                        probeSpec={{ depth: (probeParams.depthUm || 10000) / 1000 }}
                        probePreset={probePreset}
                        highlightIdx={highlightIdx}
                        sideCoverage={resultsForPanel?.sideCoverage || null}
                        lang={lang}
                        onBridgeStageChange={setUnityBridgeStage}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'results' && (
              <div style={S.tabContent}>
                {!resultsForPanel
                  ? <Empty icon="R" msg={tr('resultsEmpty')} />
                  : <ResultsPanel
                      results={resultsForPanel}
                      probeSpec={{ depth:(probeParams.depthUm||10000)/1000 }}
                      constraints={constraints}
                      selectedRegions={[...selectedRegions]}
                      onHighlight={setHighlightIdx}
                      lang={lang}
                      onApplyFailureRepair={handleApplyFailureRepair}
                    />
                }
              </div>
            )}

            {activeTab === 'pinpoint' && (
              <div style={S.tabContent}>
                <PinpointIO
                  results={resultsForPanel}
                  probePreset={probePreset}
                  correction={correction?.factors || correction || null}
                  correctionUncertainty={correction?.uncertainty || null}
                  onImport={handlePinpointImport}
                  lang={lang}
                />
              </div>
            )}

            {activeTab === 'history' && (
              <div style={S.tabContent}>
                <HistoryPanel currentState={currentSnapshot} onRestore={restoreSnap} lang={lang} />
              </div>
            )}

            {activeTab === 'correction' && (
              <div style={S.tabContent}>
                <CoordCorrectionPanel correction={correction} onChange={setCorrection} lang={lang} />
              </div>
            )}

            {activeTab === 'regqc' && (
              <div style={S.tabContent}>
                <RegistrationQcPanel
                  baseUrl={baseUrl}
                  authToken={authToken}
                  backendStatus={backendStatus}
                  lang={lang}
                />
              </div>
            )}

            {activeTab === 'benchmark' && (
              <div style={S.tabContent}>
                <BenchmarkPanel
                  baseUrl={baseUrl}
                  authToken={authToken}
                  backendStatus={backendStatus}
                  lang={lang}
                />
              </div>
            )}
          </div>

          {/* Right sidebar: planning settings */}
          <ResizableSidebar defaultWidth={270} minWidth={210} maxWidth={420} side="right"
            className="pp-sidebar pp-sidebar-right"
            style={{ background:C.bg, borderLeft:`1px solid ${C.border}`,
              display:'flex', flexDirection:'column' }}>
            <SideHdr icon="A" title={tr('advancedSettings')} />
            <div style={{ flex:1, overflowY:'auto', padding:'8px 8px 16px', minWidth:0 }}>

              <ACard icon="F" title={tr('preflightTitle')}
                open={openCards.preflight} onToggle={() => toggleCard('preflight')}
                badge={preflightReady ? tr('preflightReady') : tr('preflightNotReady')}
                badgeColor={preflightReady ? C.success : C.warn}>
                <div style={{ display:'grid', gap:6 }}>
                  {preflightItems.map((item) => {
                    const color = item.ok ? C.success : item.warn ? C.warn : C.danger;
                    return (
                      <div key={item.key} style={{
                        border:`1px solid ${color}44`,
                        background:'#041120',
                        borderRadius:6,
                        padding:'5px 8px',
                      }}>
                        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:8 }}>
                          <span style={{ fontSize:11, color:C.textSub }}>{item.label}</span>
                          <span style={{ fontSize:10, color, fontFamily:'monospace' }}>
                            {item.ok ? tr('globalStageReady') : item.warn ? tr('constraintWarn') : tr('constraintOff')}
                          </span>
                        </div>
                        <div style={{ marginTop:2, fontSize:10, color:C.textMuted, lineHeight:1.35 }}>{item.detail}</div>
                      </div>
                    );
                  })}
                </div>
              </ACard>

              {/* Algorithm explanation card */}
              <div style={{
                marginBottom:6, padding:'8px 10px',
                background:C.accent+'0c', border:`1px solid ${C.accent}30`,
                borderRadius:7, fontSize:10,
              }}>
                <div style={{ color:C.accent, fontWeight:700, marginBottom:2 }}>
                  {tr(algoInfo.labelKey)}
                  <span style={{ marginLeft:8, color:C.textMuted, fontWeight:400,
                    fontFamily:'monospace', fontSize:9 }}>
                    {algoInfo.complexity}
                  </span>
                </div>
                <div style={{ color:C.textDim, lineHeight:1.5 }}>
                  {tr(algoInfo.descKey)}
                </div>
                <div style={{ marginTop:4, color:C.textMuted }}>
                  {tr('expectedTimeLabel')}: {(algoInfo.expectedMs[0] / 1000).toFixed(1)}-{(algoInfo.expectedMs[1] / 1000).toFixed(1)}s
                  {lastRunMs && lastAlgo===params.algorithm && (
                    <span style={{ color:C.success, marginLeft:8 }}>
                      {tr('actualTimeLabel')}: {lastRunMs<1000 ? `${lastRunMs}ms` : `${(lastRunMs/1000).toFixed(1)}s`}
                    </span>
                  )}
                </div>
              </div>

              <ACard icon="P" title={tr('parameters')}
                open={openCards.params} onToggle={() => toggleCard('params')}>
                <div style={S.paramModeRow}>
                  <span style={S.paramModeLabel}>{tr('paramModeLabel')}</span>
                  <div style={S.paramModeBtnGroup}>
                    <button
                      style={{ ...S.paramModeBtn, ...(paramMode === 'simple' ? S.paramModeBtnOn : null) }}
                      onClick={() => setParamMode('simple')}
                    >
                      {tr('paramModeSimple')}
                    </button>
                    <button
                      style={{ ...S.paramModeBtn, ...(paramMode === 'expert' ? S.paramModeBtnOn : null) }}
                      onClick={() => setParamMode('expert')}
                    >
                      {tr('paramModeExpert')}
                    </button>
                  </div>
                </div>
                <ParameterPanel params={params} setParams={setParams} lang={lang} mode={paramMode} />
              </ACard>

              <ACard icon="D" title={tr('probeSpec')}
                open={openCards.probe} onToggle={() => toggleCard('probe')}>
                <ProbeSpecPanel
                  probePreset={probePreset} setProbePreset={setProbePreset}
                  probeParams={probeParams} setProbeParams={setProbeParams}
                  lang={lang}
                />
              </ACard>

              <ACard icon="C" title={tr('constraints')}
                open={openCards.constraints} onToggle={() => toggleCard('constraints')}
                badge={`${Object.values(constraints).filter(v=>v==='enforce').length}`}
                badgeColor={C.success}>
                <ConstraintsPanel constraints={constraints} onChange={setConstraint} lang={lang} />
              </ACard>

              <ACard icon="B" title={tr('backendPanelTitle')}
                open={openCards.backend} onToggle={() => toggleCard('backend')}
                badge={backendStatus==='online' ? tr('backendOnline') : null}
                badgeColor={C.success}>
                <BackendPanel
                  backendState={{ status:backendStatus, url:baseUrl, error: backendError,
                    lastSeen:atlasInfo?.lastSeen??null, capabilities:atlasInfo, atlasMode }}
                  onConnect={connect}
                  onDisconnect={disconnect}
                  onModeChange={setAtlasMode}
                  lang={lang}
                />
              </ACard>

            </div>
          </ResizableSidebar>

        </div>
      </div>
    </>
  );
}

// Child components
function StatusBadge({ status, tr }) {
  const M = {
    idle:       { c:C.textMuted, dot:'*', l:tr('backendIdle') },
    connecting: { c:'#60a5fa',   dot:'*', l:tr('backendConnecting'), spin:true },
    loading:    { c:'#a78bfa',   dot:'*', l:tr('backendLoading'), spin:true },
    online:     { c:C.success,   dot:'*', l:tr('backendOnline') },
    offline:    { c:C.danger,    dot:'*', l:tr('backendOffline') },
  };
  const m = M[status]||M.idle;
  return (
    <div data-testid="backend-status-badge" style={{ display:'flex', alignItems:'center', gap:5, fontSize:10,
      color:m.c, background:m.c+'14', border:`1px solid ${m.c}30`,
      borderRadius:20, padding:'3px 10px', whiteSpace:'nowrap', flexShrink:0 }}>
      <span style={m.spin ? { animation:'spin 1s linear infinite', display:'inline-block' } : {}}>
        {m.dot}
      </span>
      {m.l}
    </div>
  );
}

function CoverageBar({ covered, total }) {
  const pct = total > 0 ? Math.round(covered / total * 100) : 0;
  const color = pct >= 80 ? C.success : pct >= 50 ? C.warn : C.danger;
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
      <div style={{ width:60, height:4, background:C.border, borderRadius:2 }}>
        <div style={{ width:`${pct}%`, height:'100%', background:color,
          borderRadius:2, transition:'width 0.4s' }} />
      </div>
      <span style={{ fontSize:11, color, fontWeight:700, fontFamily:'monospace' }}>
        {covered}/{total}
      </span>
      <span style={{ fontSize:9, color:C.textMuted }}>{pct}%</span>
    </div>
  );
}

function Spin() {
  return <span style={{ display:'inline-block', animation:'spin 1s linear infinite', marginRight:4 }}>*</span>;
}
function Chip({ active, onClick, label }) {
  return (
    <button onClick={onClick} style={{
      background: active ? C.accent+'18' : 'none',
      border:`1px solid ${active ? C.accent+'55' : C.border}`,
      borderRadius:6, color: active ? C.accent : C.textMuted,
      fontSize:11, padding:'4px 11px', cursor:'pointer',
      fontWeight:active ? 600 : 400, transition:'all 0.15s',
    }}>{label}</button>
  );
}
function SideHdr({ icon, title, children }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:7,
      padding:'10px 13px', flexShrink:0,
      background:C.panel2, borderBottom:`1px solid ${C.border}`, minWidth:0 }}>
      <span style={{ fontSize:14, flexShrink:0 }}>{icon}</span>
      <span style={{ fontSize:11, fontWeight:700, color:C.textDim,
        textTransform:'uppercase', letterSpacing:'0.1em',
        flex:1, minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
        {title}
      </span>
      {children}
    </div>
  );
}
function ACard({ icon, title, open, onToggle, badge, badgeColor=C.accent, children }) {
  return (
    <div style={{ marginBottom:5, borderRadius:8,
      border:`1px solid ${open ? C.border2 : C.border}`,
      background:open ? C.panel2 : C.panel, overflow:'hidden', minWidth:0 }}>
      <button onClick={onToggle} style={{
        width:'100%', display:'flex', alignItems:'center', gap:7,
        padding:'9px 12px', background:'none', border:'none',
        cursor:'pointer', color:open ? C.text : C.textDim, minWidth:0 }}>
        <span style={{ fontSize:14, flexShrink:0 }}>{icon}</span>
        <span style={{ flex:1, fontSize:11, fontWeight:700,
          textTransform:'uppercase', letterSpacing:'0.07em',
          textAlign:'left', minWidth:0, overflow:'hidden',
          textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {title}
        </span>
        {badge && (
          <span style={{ fontSize:9, fontWeight:700, color:badgeColor,
            background:badgeColor+'1a', borderRadius:10,
            padding:'1px 7px', flexShrink:0 }}>
            {badge}
          </span>
        )}
        <span style={{ fontSize:9, color:C.textMuted, flexShrink:0 }}>{open ? '-' : '+'}</span>
      </button>
      {open && <div style={{ padding:'2px 12px 12px', minWidth:0 }}>{children}</div>}
    </div>
  );
}
function PaneHdr({ icon, label }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6,
      padding:'7px 12px 6px', background:C.panel2,
      borderBottom:`1px solid ${C.border}`, flexShrink:0, minWidth:0 }}>
      <span style={{ fontSize:13, flexShrink:0 }}>{icon}</span>
      <span style={{ fontSize:11, fontWeight:700, color:C.textDim,
        textTransform:'uppercase', letterSpacing:'0.08em',
        overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
        {label}
      </span>
    </div>
  );
}
function Empty({ icon, msg }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center',
      justifyContent:'center', height:'60%', gap:12, opacity:0.45 }}>
      <span style={{ fontSize:42 }}>{icon}</span>
      <span style={{ fontSize:13, color:C.textDim }}>{msg}</span>
    </div>
  );
}

const EXTRA_CSS = `
  * { box-sizing: border-box; }
  @keyframes spin { to { transform: rotate(360deg); } }
  :focus-visible { outline: 2px solid #00d4ff; outline-offset: 2px; }
  @media (max-width: 1100px) {
    .pp-root { height: 100vh !important; min-height: 0; overflow-x: hidden !important; overflow-y: auto !important; }
    .pp-topbar { align-items: flex-start !important; padding: 8px 10px !important; }
    .pp-topbar > div { max-width: 100%; }
    .pp-topbar > div:nth-child(3) { width: 100%; justify-content: center !important; }
    .pp-global-stage { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
    .pp-main { flex-direction: column !important; overflow: visible !important; }
    .pp-sidebar { width: 100% !important; min-height: 300px !important; max-height: 46vh; }
    .pp-sidebar > div:last-child { display: none !important; }
    .pp-center { min-height: 720px; flex: none !important; }
    .pp-view-grid { grid-template-columns: 1fr !important; overflow-y: auto; }
    .pp-view-grid > div { min-height: 320px; }
  }
  @media (max-width: 640px) {
    .pp-global-stage { grid-template-columns: 1fr !important; }
    .pp-sidebar { min-height: 340px !important; max-height: 55vh; }
    .pp-center { min-height: 760px; }
  }
`;

const S = {
  root: { display:'flex', flexDirection:'column', height:'100vh',
    background:C.bg, color:C.text,
    fontFamily:"'Inter','PingFang SC',sans-serif", overflow:'hidden' },
  topbar: { display:'flex', alignItems:'center', justifyContent:'space-between',
    padding:'6px 16px', minHeight:58, flexShrink:0, flexWrap:'wrap',
    background:C.panel, borderBottom:`1px solid ${C.border}`, gap:10, zIndex:20 },
  topLeft:  { display:'flex', alignItems:'center', gap:12, flexShrink:0 },
  logoMark: {
    width: 24,
    height: 24,
    borderRadius: 6,
    border: `1px solid ${C.accent}55`,
    background: `${C.accent}22`,
    color: C.accent,
    fontSize: 11,
    fontWeight: 800,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: 'monospace',
    letterSpacing: 0.2,
    flexShrink: 0,
  },
  appTitle: { fontSize:14, fontWeight:700, color:C.text, lineHeight:1.25 },
  appSub:   { fontSize:9, color:C.textMuted, lineHeight:1.3 },
  topCenter:{ display:'flex', alignItems:'center', gap:6, flex:1, justifyContent:'center', flexWrap:'wrap' },
  topRight: {
    display:'flex',
    alignItems:'center',
    justifyContent:'flex-end',
    gap:8,
    rowGap:6,
    flexShrink:0,
    flexWrap:'wrap',
    minWidth:0,
  },
  langRow:  { display:'flex', borderRadius:6, overflow:'hidden', border:`1px solid ${C.border}` },
  langBtn:  { background:'none', border:'none', color:C.textMuted, fontSize:11, padding:'4px 10px', cursor:'pointer' },
  langOn:   { background:C.accent+'22', color:C.accent, fontWeight:700 },
  modeQuickWrap: {
    display:'flex',
    alignItems:'center',
    gap:6,
    background:C.panel2,
    border:`1px solid ${C.border}`,
    borderRadius:6,
    padding:'2px 6px',
    minWidth:0,
  },
  modeQuickLabel: { fontSize:10, color:C.textMuted, whiteSpace:'nowrap' },
  modeQuickSelect: {
    background:'transparent',
    border:'none',
    color:C.textSub,
    fontSize:11,
    outline:'none',
    minWidth:110,
    cursor:'pointer',
  },
  leftModeRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 10px',
    borderBottom: `1px solid ${C.border}`,
    background: C.panel2,
    flexShrink: 0,
  },
  leftModeLabel: {
    fontSize: 10,
    color: C.textMuted,
    whiteSpace: 'nowrap',
  },
  leftModeSelect: {
    flex: 1,
    minWidth: 0,
    background: '#071526',
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    color: C.textSub,
    fontSize: 11,
    padding: '4px 8px',
    outline: 'none',
  },
  algoRow:  { display:'flex', gap:4 },
  algoBtn:  { border:'1px solid', borderRadius:6, padding:'4px 10px',
    fontSize:10, cursor:'pointer', transition:'all 0.15s', textAlign:'center', lineHeight:1.3 },
  runBtn: { display:'flex', alignItems:'center', gap:4,
    border:'none', borderRadius:8, padding:'7px 20px',
    fontSize:12, fontWeight:700, letterSpacing:'0.04em',
    transition:'all 0.15s', whiteSpace:'nowrap', flexShrink:0 },
  progressWrap: { display:'flex', alignItems:'center', gap:6, minWidth:0, maxWidth:260 },
  prog:    { position:'relative', width:70, height:20, background:C.panel2, borderRadius:10,
    overflow:'hidden', border:`1px solid ${C.border}`, flexShrink:0 },
  progBar: { height:'100%', background:C.accent+'55', transition:'width 0.3s', borderRadius:10 },
  progTxt: { position:'absolute', inset:0, display:'flex', alignItems:'center',
    justifyContent:'center', fontSize:9, color:C.accent, fontFamily:'monospace', fontWeight:700 },
  progressStage: {
    fontSize: 10,
    color: C.textSub,
    maxWidth: 180,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  globalStageBar: {
    flexShrink: 0,
    display: 'grid',
    gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
    gap: 8,
    padding: '8px 12px',
    borderBottom: `1px solid ${C.border}`,
    background: '#051120',
  },
  globalStageItem: {
    border: `1px solid ${C.border}`,
    borderRadius: 8,
    background: '#071728',
    padding: '6px 8px',
    minWidth: 0,
  },
  globalStageHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 10,
    color: C.textSub,
    marginBottom: 4,
    gap: 6,
  },
  globalStageTrack: {
    height: 4,
    borderRadius: 999,
    background: '#0a253b',
    overflow: 'hidden',
  },
  globalStageFill: {
    height: '100%',
    borderRadius: 999,
    transition: 'width 0.25s ease',
  },
  globalStageDetail: {
    marginTop: 4,
    fontSize: 9.5,
    color: C.textMuted,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  eventDock: {
    position: 'relative',
    zIndex: 12,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    gap: 6,
    width: 'auto',
    minWidth: 118,
    maxWidth: 'min(420px, calc(100vw - 24px))',
    flexShrink: 0,
    marginLeft: 4,
  },
  eventToggleBtn: {
    border: `1px solid ${C.border2}`,
    background: '#081829cc',
    color: C.textSub,
    borderRadius: 8,
    padding: '6px 10px',
    fontSize: 11,
    cursor: 'pointer',
    boxShadow: '0 4px 12px #000a1666',
    whiteSpace: 'nowrap',
  },
  eventToggleBtnOn: {
    borderColor: `${C.accent}88`,
    color: C.accent,
    background: `${C.accent}1a`,
  },
  eventPanel: {
    position: 'absolute',
    right: 0,
    top: 'calc(100% + 6px)',
    zIndex: 20,
    border: `1px solid ${C.border2}`,
    background: '#061422f2',
    borderRadius: 10,
    maxHeight: 420,
    width: 380,
    maxWidth: 'min(420px, calc(100vw - 24px))',
    overflowY: 'auto',
    boxShadow: '0 12px 30px #000a16cc',
    padding: 8,
    display: 'grid',
    gap: 8,
  },
  eventPanelHead: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    color: C.textSub,
    fontSize: 11,
    fontWeight: 700,
    borderBottom: `1px solid ${C.border}`,
    paddingBottom: 6,
  },
  eventCopyBtn: {
    border: `1px solid ${C.border2}`,
    background: '#082036',
    color: C.textSub,
    borderRadius: 6,
    padding: '3px 8px',
    fontSize: 10,
    cursor: 'pointer',
  },
  eventEmpty: {
    color: C.textMuted,
    fontSize: 11,
    textAlign: 'center',
    padding: 10,
  },
  eventItem: {
    border: `1px solid ${C.border}`,
    borderRadius: 8,
    background: '#081a2a',
    padding: '6px 8px',
    display: 'grid',
    gap: 4,
  },
  eventTitleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    minWidth: 0,
  },
  eventLevelDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    flexShrink: 0,
  },
  eventTitle: {
    color: C.text,
    fontSize: 11,
    fontWeight: 700,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  eventSource: {
    marginLeft: 'auto',
    fontSize: 9.5,
    color: C.textMuted,
    fontFamily: 'monospace',
    flexShrink: 0,
  },
  eventMsg: {
    color: C.textSub,
    fontSize: 10.5,
    lineHeight: 1.45,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  eventTriplet: {
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    background: '#06121f',
    padding: '5px 6px',
    display: 'grid',
    gap: 4,
  },
  eventTripletRow: {
    display: 'grid',
    gap: 2,
  },
  eventTripletLabel: {
    fontSize: 9,
    color: C.textMuted,
    letterSpacing: 0.2,
  },
  eventTripletText: {
    fontSize: 10.5,
    color: C.textSub,
    lineHeight: 1.35,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  eventAction: {
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    background: '#06121f',
    color: C.textMuted,
    fontSize: 10,
    padding: '4px 6px',
    lineHeight: 1.4,
  },
  eventItemFooter: {
    display: 'flex',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  eventRepairBtn: {
    border: `1px solid #16a34a`,
    background: '#052513',
    color: '#86efac',
    borderRadius: 6,
    padding: '3px 8px',
    fontSize: 10,
    cursor: 'pointer',
  },
  eventRepairErr: {
    fontSize: 9.5,
    color: '#fca5a5',
    maxWidth: 260,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  eventDismissBtn: {
    border: `1px solid ${C.border2}`,
    background: '#0a2238',
    color: C.textSub,
    borderRadius: 6,
    padding: '3px 8px',
    fontSize: 10,
    cursor: 'pointer',
  },
  paramModeRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    marginBottom: 8,
    paddingBottom: 8,
    borderBottom: `1px solid ${C.border}`,
  },
  paramModeLabel: {
    fontSize: 10,
    color: C.textMuted,
  },
  paramModeBtnGroup: {
    display: 'flex',
    gap: 4,
  },
  paramModeBtn: {
    border: `1px solid ${C.border2}`,
    background: '#091e31',
    color: C.textSub,
    borderRadius: 6,
    padding: '3px 8px',
    fontSize: 10,
    cursor: 'pointer',
  },
  paramModeBtnOn: {
    borderColor: `${C.accent}88`,
    color: C.accent,
    background: `${C.accent}1a`,
  },
  noticeStripWrap: {
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    padding: '8px 16px',
    background: '#061325',
    borderBottom: `1px solid ${C.border}`,
    maxHeight: 180,
    overflowY: 'auto',
  },
  noticeStripItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    background: C.panel2,
    border: `1px solid ${C.border}`,
    borderRadius: 8,
    padding: '8px 10px',
  },
  noticeStripTextWrap: { flex: 1, minWidth: 0 },
  noticeStripTitle: {
    fontSize: 11,
    fontWeight: 700,
    marginBottom: 3,
  },
  noticeStripMsg: {
    fontSize: 10.5,
    lineHeight: 1.45,
    color: C.text,
    wordBreak: 'break-word',
  },
  noticeDismissBtn: {
    border: `1px solid ${C.border2}`,
    background: C.panel,
    color: C.textSub,
    borderRadius: 6,
    padding: '4px 8px',
    fontSize: 10,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  noticeModalBackdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(2, 10, 22, 0.62)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1800,
    padding: 16,
  },
  noticeModalCard: {
    width: 'min(640px, 96vw)',
    background: C.panel,
    border: `1px solid ${C.border2}`,
    borderRadius: 12,
    boxShadow: `0 24px 56px ${C.bg}cc`,
    padding: '14px 16px 12px',
  },
  noticeModalTitle: {
    fontSize: 16,
    fontWeight: 700,
    color: C.text,
    marginBottom: 8,
  },
  noticeModalMsg: {
    fontSize: 13,
    lineHeight: 1.6,
    color: C.text,
    marginBottom: 8,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  noticeModalAction: {
    fontSize: 12,
    lineHeight: 1.6,
    color: C.textSub,
    padding: '8px 10px',
    borderRadius: 8,
    background: C.panel2,
    border: `1px solid ${C.border}`,
  },
  noticeModalFooter: {
    display: 'flex',
    justifyContent: 'flex-end',
    marginTop: 12,
  },
  noticeModalBtn: {
    border: `1px solid ${C.accent}66`,
    background: `${C.accent}20`,
    color: C.accent,
    borderRadius: 8,
    padding: '6px 14px',
    fontSize: 12,
    fontWeight: 700,
    cursor: 'pointer',
  },
  badge:   { fontSize:10, fontWeight:700, color:C.accent,
    background:C.accent+'18', borderRadius:12, padding:'1px 8px', flexShrink:0 },
  smallTag:{ fontSize:9, color:C.accent, background:C.accent+'18',
    border:`1px solid ${C.accent}44`, borderRadius:10, padding:'1px 7px', flexShrink:0 },
  main: { display:'flex', flex:1, overflow:'hidden' },
  colMid: { flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minWidth:0 },
  tabBar: { display:'flex', background:C.panel,
    borderBottom:`1px solid ${C.border}`, flexShrink:0,
    overflowX:'auto', overflowY:'hidden', alignItems:'center' },
  tabBtn: { display:'flex', alignItems:'center', gap:5,
    background:'none', borderWidth:0, borderStyle:'solid',
    borderTopColor:'transparent', borderRightColor:'transparent',
    borderBottomColor:'transparent', borderLeftColor:'transparent', borderBottomWidth:2,
    color:C.textMuted, fontSize:12, padding:'11px 14px',
    cursor:'pointer', fontWeight:500, whiteSpace:'nowrap' },
  tabActive: { color:C.text, borderBottomColor:C.accent, fontWeight:700 },
  viewsArea: { flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minHeight:0 },
  viewGrid: { display:'grid', gridTemplateColumns:'1fr 1fr',
    flex:'1 1 0', minHeight:0, gap:1, background:C.border },
  viewPane: { background:C.panel, display:'flex', flexDirection:'column', overflow:'hidden' },
  paneBody: { flex:1, overflow:'hidden', position:'relative', minHeight:0 },
  d3Wrap:   { background:C.panel, borderTop:`1px solid ${C.border}`, flexShrink:0 },
  d3Toggle: { width:'100%', display:'flex', alignItems:'center', gap:8,
    background:'none', border:'none', cursor:'pointer', padding:'9px 16px', color:C.textDim },
  d3Label:  { fontSize:12, fontWeight:700, color:C.textSub,
    textTransform:'uppercase', letterSpacing:'0.08em' },
  d3Body:   { background:'#030810', borderTop:`1px solid ${C.border}`, height:460, overflow:'hidden' },
  tabContent: { flex:1, overflowY:'auto', padding:16 },
};
