import React, { useCallback, useMemo, useState } from 'react';
import { THEME } from '../constants/theme';
import { PROBE_COLORS } from '../constants/brainRegions';
import { t } from '../constants/i18n';

const fmt = (n, d = 2) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(d) : '--');

function coverageColor(frac) {
  if (frac >= 0.9) return '#4ade80';
  if (frac >= 0.6) return '#facc15';
  return '#f87171';
}

function scoreColor(score) {
  if (!Number.isFinite(score)) return '#94a3b8';
  if (score >= 0.8) return '#4ade80';
  if (score >= 0.55) return '#facc15';
  return '#f87171';
}

function localizeFreeText(message, lang, tr) {
  if (typeof message !== 'string' || !message.trim()) return '';
  const text = message.trim();

  const safety = text.match(/Continuous min safety distance\\s+([0-9.]+)\\s*mm\\s+is below threshold\\s+([0-9.]+)\\s*mm/i);
  if (safety) {
    return tr('warningContinuousDistanceBelowTemplate')
      .replace('{distance}', safety[1])
      .replace('{threshold}', safety[2]);
  }
  const unsupported = text.match(/Unsupported target regions[^:]*:\\s*(.+)$/i);
  if (unsupported) {
    return tr('warningUnsupportedRegionsTemplate')
      .replace('{regions}', unsupported[1]);
  }
  return text;
}

function resolveLocalizedViolationText(value, lang, tr) {
  if (typeof value === 'string') return localizeFreeText(value, lang, tr);
  if (!value || typeof value !== 'object') return '';
  const locale = String(lang || '').toLowerCase().startsWith('cn') ? 'cn' : 'en';
  if (locale === 'cn' && typeof value.cn === 'string' && value.cn.trim()) return localizeFreeText(value.cn, lang, tr);
  if (locale === 'en' && typeof value.en === 'string' && value.en.trim()) return localizeFreeText(value.en, lang, tr);
  if (typeof value.en === 'string' && value.en.trim()) return localizeFreeText(value.en, lang, tr);
  if (typeof value.cn === 'string' && value.cn.trim()) return localizeFreeText(value.cn, lang, tr);
  return '';
}

export default function ResultsPanel({
  results,
  probeSpec,
  selectedRegions = [],
  onHighlight,
  lang = 'cn',
  onApplyFailureRepair = null,
}) {
  const tr = useCallback((k) => t(k, lang), [lang]);
  const [expandedIdx, setExpandedIdx] = useState(null);

  if (!results) {
    return (
      <div style={styles.panel}>
        <div style={styles.emptyMsg}>{tr('resultsEmpty')}</div>
      </div>
    );
  }

  const { probes = [], coverage, warnings = [], elapsedMs, algorithm } = results;
  const backendError = typeof results.backendError === 'string' ? results.backendError : '';

  const covFrac = coverage
    ? (coverage.covered ?? 0) / Math.max(coverage.total ?? 1, 1)
    : 0;
  const covPct = (covFrac * 100).toFixed(1);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>{tr('resultsPanelTitle')}</span>
        <span style={styles.metaBadge}>
          {algorithm} | {fmt((elapsedMs ?? 0) / 1000, 1)}s
        </span>
      </div>

      <CoverageBar
        covered={coverage?.covered}
        total={coverage?.total}
        pct={covPct}
        frac={covFrac}
        tr={tr}
      />
      <FallbackTargetNotice results={results} lang={lang} />

      <ClinicalContractSummary
        contract={results.clinicalContract}
        context={results.clinicalContext}
        tr={tr}
      />
      <PrimaryEndpointsSummary summary={results.primaryEndpointSummary} tr={tr} />
      <CandidateStageBreakdown breakdown={results.candidateStageBreakdown} tr={tr} />
      <ProbeCriticalDigest probes={probes} tr={tr} />
      <StatisticalReliabilitySummary results={results} tr={tr} />

      {(results.rankingMode || results.paretoEnabled) && (
        <ExplainSummary results={results} tr={tr} />
      )}

      {backendError && (
        <div style={styles.errorBox}>
          <div style={styles.errorTitle}>{tr('optimizationFailed')}</div>
          <div style={styles.errorText}>{backendError}</div>
        </div>
      )}

      <FailureCards cards={results.failureCards} tr={tr} onApplyRepair={onApplyFailureRepair} />
      <ReproSummary results={results} tr={tr} />

      <SideCoverageSummary sideCoverage={results.sideCoverage} tr={tr} />

      <ParetoScatter probes={probes} onHighlight={onHighlight} tr={tr} />

      {warnings.length > 0 && <WarningList warnings={warnings} tr={tr} lang={lang} />}

      <div style={styles.probeList}>
        {probes.map((probe, idx) => (
          <ProbeCard
            key={idx}
            idx={idx}
            probe={probe}
            probeSpec={probeSpec}
            expanded={expandedIdx === idx}
            onToggle={() => setExpandedIdx(expandedIdx === idx ? null : idx)}
            onMouseEnter={() => onHighlight?.(idx)}
            onMouseLeave={() => onHighlight?.(null)}
            tr={tr}
            lang={lang}
          />
        ))}
      </div>

      {coverage?.byRegion && (
        <RegionCoverageTable
          byRegion={coverage.byRegion}
          selectedRegions={selectedRegions}
          tr={tr}
        />
      )}
    </div>
  );
}

function FallbackTargetNotice({ results, lang }) {
  const fallbackKeys = Array.isArray(results?.fallbackTargetKeys) ? results.fallbackTargetKeys : [];
  if (!fallbackKeys.length) return null;
  const atlasBacked = Number.isFinite(Number(results?.atlasBackedUncoveredCount))
    ? Number(results.atlasBackedUncoveredCount)
    : null;
  const isCn = String(lang || '').toLowerCase().startsWith('cn');
  return (
    <div style={{ ...styles.summaryBox, borderColor: '#8a6d1d', background: '#1f1807' }}>
      <div style={{ ...styles.summaryTitle, color: '#fde68a' }}>
        {isCn ? 'Native atlas 覆盖说明' : 'Native atlas coverage note'}
      </div>
      <div style={styles.summaryLine}>
        {isCn
          ? `以下目标未被 native atlas 索引支持，已从真实覆盖门禁中排除：${fallbackKeys.join(', ')}`
          : `These targets are not backed by the native atlas index and were excluded from hard coverage gates: ${fallbackKeys.join(', ')}`}
      </div>
      {atlasBacked !== null && (
        <div style={styles.summaryLine}>
          {isCn ? `Atlas-backed 未覆盖数：${atlasBacked}` : `Atlas-backed uncovered count: ${atlasBacked}`}
        </div>
      )}
    </div>
  );
}

function ClinicalContractSummary({ contract, context, tr }) {
  if (!contract) return null;
  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>
        {tr('clinicalContractLock')}
      </div>
      <div style={styles.summaryLine}>
        {tr('intendedUseLabel')}: {contract.intendedUse || '--'}
      </div>
      <div style={styles.summaryLine}>
        {tr('mouseAtlasLocked')}
      </div>
      <div style={styles.summaryLine}>
        {tr('decisionClassLabel')}: {contract.decisionClass || '--'}
      </div>
      {Array.isArray(contract.primaryEndpoints) && contract.primaryEndpoints.length > 0 && (
        <div style={styles.summaryLine}>
          {tr('primaryEndpointsLabel')}: {contract.primaryEndpoints.join(', ')}
        </div>
      )}
      {context?.studyArm && (
        <div style={styles.summaryLine}>
          {tr('studyArmLabel')}: {context.studyArm}
        </div>
      )}
      {context?.hospitalId && (
        <div style={styles.summaryLine}>
          {tr('hospitalDeviceLabel')}: {context.hospitalId} | {context.deviceModel || '--'}
        </div>
      )}
    </div>
  );
}

function PrimaryEndpointsSummary({ summary, tr }) {
  if (!summary) return null;
  const cov = Number(summary.coverageRate);
  const safetyP = Number(summary.safetyPassProbability);
  const minD = Number(summary.continuousMinSafetyDistanceMm);
  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>
        {tr('primaryEndpointSummaryTitle')}
      </div>
      <div style={styles.summaryLine}>
        {tr('coverageRateLabel')}: {Number.isFinite(cov) ? `${(cov * 100).toFixed(1)}%` : '--'}
      </div>
      <div style={styles.summaryLine}>
        {tr('continuousMinSafetyDistanceLabel')}: {Number.isFinite(minD) ? `${minD.toFixed(3)} mm` : '--'}
      </div>
      <div style={styles.summaryLine}>
        {tr('safetyProbabilityLabel')}: {Number.isFinite(safetyP) ? `${(safetyP * 100).toFixed(1)}%` : '--'}
      </div>
      <div style={styles.summaryLine}>
        {tr('planningTimeLabel')}: {summary.planningTimeMs ?? '--'} ms
      </div>
      <div style={styles.summaryLine}>
        {tr('expertAcceptanceRateLabel')}: {Number.isFinite(Number(summary.expertAcceptanceRate))
          ? `${(Number(summary.expertAcceptanceRate) * 100).toFixed(1)}%`
          : '--'}
      </div>
    </div>
  );
}

function resolveCandidateStageLabel(stage, tr) {
  const key = String(stage || '').trim().toLowerCase();
  if (key === 'generated_specs') return tr('candidateStageGeneratedSpecs');
  if (key === 'coarse_refine_kept') return tr('candidateStageCoarseRefine');
  if (key === 'risk_prefilter_kept') return tr('candidateStagePrefilter');
  if (key === 'constraint_feasible_pool') return tr('candidateStageConstraintFeasible');
  if (key === 'scored') return tr('candidateStageScored');
  if (key === 'selected') return tr('candidateStageSelected');
  if (key === 'generatedcandidates') return tr('candidateFunnelGenerated');
  if (key === 'afterbrainbounds') return tr('candidateFunnelAfterBrainBounds');
  if (key === 'afterhemisphere') return tr('candidateFunnelAfterHemisphere');
  if (key === 'aftercoarserisk') return tr('candidateFunnelAfterCoarseRisk');
  if (key === 'aftercoverage') return tr('candidateFunnelAfterCoverage');
  if (key === 'afterspacing') return tr('candidateFunnelAfterSpacing');
  if (key === 'afterfinerisk') return tr('candidateFunnelAfterFineRisk');
  return stage || '--';
}

function resolveCandidateReasonLabel(code, tr) {
  const key = String(code || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const map = {
    bounds: 'candidateReasonBounds',
    emptytrajectory: 'candidateReasonEmptyTrajectory',
    empty_trajectory: 'candidateReasonEmptyTrajectory',
    brainmasknointersection: 'candidateReasonBrainMaskNoIntersection',
    brain_mask_no_intersection: 'candidateReasonBrainMaskNoIntersection',
    brainmaskrejected: 'candidateReasonBrainMaskRejected',
    brain_mask_rejected: 'candidateReasonBrainMaskRejected',
    brainmaskrecoverablekept: 'candidateReasonBrainMaskRecoverableKept',
    brain_mask_recoverable_kept: 'candidateReasonBrainMaskRecoverableKept',
    nocoverage: 'candidateReasonNoCoverage',
    no_coverage: 'candidateReasonNoCoverage',
    risk_prefilter_rejected: 'candidateReasonRiskPrefilterRejected',
    risk_prefilter_near_not_kept: 'candidateReasonRiskPrefilterNearNotKept',
    risk_prefilter_fallback_near_only: 'candidateReasonRiskPrefilterNearFallback',
    enforced_constraints_blocked: 'candidateReasonConstraintBlocked',
    selected_blocker_count: 'candidateReasonSelectedBlockers',
    solver_or_collision_filtered: 'candidateReasonSolverCollisionFiltered',
    infeasible_targets: 'candidateReasonInfeasibleTargets',
    out_of_brain: 'candidateReasonOutOfBrain',
    ventricle_hit: 'candidateReasonVentricleHit',
    angle_limit: 'candidateReasonAngleLimit',
    depth_limit: 'candidateReasonDepthLimit',
    no_target_coverage: 'candidateReasonNoCoverage',
    hemisphere_mismatch: 'candidateReasonHemisphereMismatch',
    probe_spacing: 'candidateReasonProbeSpacing',
    mri_quality_gate: 'candidateReasonMriQualityGate',
    risk_distance: 'candidateReasonRiskDistance',
  };
  const trKey = map[key];
  return trKey ? tr(trKey) : code || '--';
}

function CandidateStageBreakdown({ breakdown, tr }) {
  if (!breakdown || typeof breakdown !== 'object') return null;
  const stages = Array.isArray(breakdown.stages) ? breakdown.stages : [];
  if (!stages.length) return null;
  const summary = breakdown.summary && typeof breakdown.summary === 'object' ? breakdown.summary : null;
  const funnelDiag = breakdown.candidateFunnelDiagnostics && typeof breakdown.candidateFunnelDiagnostics === 'object'
    ? breakdown.candidateFunnelDiagnostics
    : null;
  const funnel = funnelDiag?.funnel && typeof funnelDiag.funnel === 'object' ? funnelDiag.funnel : null;
  const rejectionCounts = funnelDiag?.rejectionCounts && typeof funnelDiag.rejectionCounts === 'object'
    ? funnelDiag.rejectionCounts
    : null;
  const topRejects = rejectionCounts
    ? Object.entries(rejectionCounts)
      .filter(([, n]) => Number(n) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .slice(0, 4)
    : [];
  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>{tr('candidateStageBreakdownTitle')}</div>
      {summary && (
        <div style={styles.summaryLine}>
          {tr('candidateStageSummaryTemplate')
            .replace('{spec}', String(summary.specTotal ?? '--'))
            .replace('{refined}', String(summary.refinedCount ?? '--'))
            .replace('{prefilter}', String(summary.prefilterKept ?? '--'))
            .replace('{safe}', String(summary.safePoolCount ?? '--'))
            .replace('{selected}', String(summary.selectedCount ?? '--'))}
        </div>
      )}
      {funnel && (
        <div style={styles.summaryLine}>
          {tr('candidateFunnelLineTemplate')
            .replace('{generated}', String(funnel.generatedCandidates ?? '--'))
            .replace('{brain}', String(funnel.afterBrainBounds ?? '--'))
            .replace('{hemi}', String(funnel.afterHemisphere ?? '--'))
            .replace('{coarse}', String(funnel.afterCoarseRisk ?? '--'))
            .replace('{coverage}', String(funnel.afterCoverage ?? '--'))
            .replace('{spacing}', String(funnel.afterSpacing ?? '--'))
            .replace('{fine}', String(funnel.afterFineRisk ?? '--'))
            .replace('{selected}', String(funnel.selected ?? '--'))}
        </div>
      )}
      {topRejects.length > 0 && (
        <div style={styles.summaryLine}>
          {tr('candidateFunnelRejectTopLabel')}: {topRejects
            .map(([code, count]) => `${resolveCandidateReasonLabel(code, tr)}=${Number(count)}`)
            .join(' | ')}
        </div>
      )}
      <div style={styles.candidateStageTable}>
        {stages.map((row, idx) => {
          const reasons = Array.isArray(row?.reasons) ? row.reasons : [];
          const dropCount = Number(row?.droppedFromPrev ?? 0);
          const dropRatio = Number(row?.dropRatioFromPrev);
          return (
            <div key={`stage-${idx}`} style={styles.candidateStageRow}>
              <div style={styles.candidateStageHead}>
                <span>{resolveCandidateStageLabel(row?.stage, tr)}</span>
                <span>{tr('candidateStageCountLabel')}: {Number(row?.count ?? 0)}</span>
                <span>
                  {tr('candidateStageDroppedLabel')}: {dropCount > 0 ? dropCount : 0}
                  {Number.isFinite(dropRatio) && dropCount > 0 ? ` (${(dropRatio * 100).toFixed(1)}%)` : ''}
                </span>
              </div>
              {reasons.length > 0 && (
                <div style={styles.candidateStageReasonLine}>
                  {tr('candidateStageReasonLabel')}: {reasons
                    .slice(0, 3)
                    .map((item) => `${resolveCandidateReasonLabel(item?.code, tr)}=${Number(item?.count ?? 0)}`)
                    .join(' | ')}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProbeCriticalDigest({ probes, tr }) {
  if (!Array.isArray(probes) || probes.length <= 0) return null;

  const rows = probes.map((probe, idx) => {
    const contacts = Array.isArray(probe?.contactIntervals)
      ? probe.contactIntervals.length
      : Number(probe?.contactProfile?.intervalCount || 0);
    const riskDiag = probe?.riskDiagnostics && typeof probe.riskDiagnostics === 'object'
      ? probe.riskDiagnostics
      : null;
    const candidates = Array.isArray(riskDiag?.dangerSegmentsTop3) && riskDiag.dangerSegmentsTop3.length > 0
      ? riskDiag.dangerSegmentsTop3
      : (Array.isArray(riskDiag?.thresholdSegmentsMm) ? riskDiag.thresholdSegmentsMm : []);
    const seg = candidates[0] || null;
    const segStart = Number(
      seg?.startDepthMm ??
      seg?.startMm ??
      seg?.depthStartMm ??
      (Array.isArray(seg?.segmentDepthMm) ? seg.segmentDepthMm[0] : NaN),
    );
    const segEnd = Number(
      seg?.endDepthMm ??
      seg?.endMm ??
      seg?.depthEndMm ??
      (Array.isArray(seg?.segmentDepthMm) ? seg.segmentDepthMm[1] : NaN),
    );
    const segClass = String(
      seg?.sourceClass ??
      seg?.hitStructureClass ??
      seg?.source ??
      seg?.class ??
      seg?.bucket ??
      '--',
    ).trim();
    const segText = Number.isFinite(segStart) && Number.isFinite(segEnd)
      ? `${segClass} ${fmt(segStart, 3)}~${fmt(segEnd, 3)} mm`
      : (segClass && segClass !== '--' ? segClass : tr('probeCriticalNoRiskLabel'));
    const minDist = Number(
      probe?.statisticalSafety?.minDistanceMm ??
      probe?.minRiskDistanceMm ??
      riskDiag?.minDistanceMm,
    );
    const safetyProb = Number(
      probe?.statisticalSafety?.pSafetyGtThreshold ??
      probe?.safetyDistanceConfidence?.probability,
    );
    return {
      id: idx,
      contacts: Number.isFinite(contacts) ? contacts : 0,
      segText,
      minDist,
      safetyProb,
    };
  });

  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>{tr('probeCriticalDigestTitle')}</div>
      <div style={styles.criticalTable}>
        <div style={styles.criticalHead}>
          <span>{tr('probeCriticalProbeLabel')}</span>
          <span>{tr('probeCriticalContactsLabel')}</span>
          <span>{tr('probeCriticalTopRiskLabel')}</span>
          <span>{tr('probeCriticalMinRiskDistanceLabel')}</span>
          <span>{tr('probeCriticalSafetyProbLabel')}</span>
        </div>
        {rows.map((row) => (
          <div key={`critical-${row.id}`} style={styles.criticalRow}>
            <span>{`P${row.id + 1}`}</span>
            <span>{row.contacts}</span>
            <span>{row.segText}</span>
            <span>{Number.isFinite(row.minDist) ? `${fmt(row.minDist, 3)} mm` : '--'}</span>
            <span>{Number.isFinite(row.safetyProb) ? `${(row.safetyProb * 100).toFixed(1)}%` : '--'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ExplainSummary({ results, tr }) {
  const w = results.objectiveWeights || {};
  const rp = results.robustParams || {};
  const ps = results.paretoSummary || {};
  const us = results.uncertaintySummary || {};
  const riskPolicy = results.riskConstraintPolicy || {};
  const prefPreview = results.preferencePreview || {};
  const drcc = results.drccSummary || {};
  const sc = results.selectiveConformalSummary || {};
  const prefProfiles = results.preferenceProfiles || {};
  const stratCal = results.stratifiedCalibration || {};
  const stratGlobal = stratCal.global || {};
  const nativeEngineActive = Boolean(
    results.nativeEngineService?.enabled ||
    results.candidateStageBreakdown?.engineService ||
    results.computeProfile?.solverRuntimePolicy?.supportedNativeDrccSolvers
  );
  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>
        {tr('multiObjectiveScoringTitle')}
      </div>
      <div style={styles.summaryLine}>
        {tr('rankingModeLabel')}: {results.rankingMode || 'default'}
      </div>
      <div style={styles.summaryLine}>
        {tr('targetingModeLabel')}: {results.targetingMode === 'bilateral_symmetric'
          ? tr('bilateralSymmetricLabel')
          : tr('singleHemisphereLabel')}
      </div>
      <div style={styles.summaryLine}>
        {tr('robustOptimizationLabel')}: {results.robustOptimization ? 'on' : 'off'} | Pareto: {results.paretoEnabled ? 'on' : 'off'}
      </div>
      {Array.isArray(results.uncoveredSides) && results.uncoveredSides.length > 0 && (
        <div style={styles.summaryLine}>
          {tr('uncoveredSidesLabel')}: {results.uncoveredSides
            .map((item) => `${item.key}/${item.side === 'left' ? 'L' : 'R'}`)
            .join(', ')}
        </div>
      )}
      {results.robustParams && (
        <div style={styles.summaryLine}>
          {tr('robustParamsLabel')}: N={rp.samples} | entrySigma={fmt(rp.entrySigmaMm, 2)}mm | angleSigma={fmt(rp.angleSigmaDeg, 2)}deg | depthSigma={fmt(rp.depthSigmaMm, 2)}mm | {tr('riskMarginTreLabel')}={fmt(rp.riskMarginTreMm, 2)}mm
        </div>
      )}
      {results.paretoParams && (
        <div style={styles.summaryLine}>
          {tr('paretoPresetLabel')}: topK={results.paretoParams.topK} | {results.objectivePreset || results.paretoParams.objectivePreset || 'balanced'}
        </div>
      )}
      {results.paretoSummary && (
        <div style={styles.summaryLine}>
          {tr('frontOneSizeLabel')}: {ps.frontOneSize}
        </div>
      )}
      {results.uncertaintySummary && (
        <div style={styles.summaryLine}>
          {tr('uncertaintySummaryLabel')}: meanCoverage={fmt(us.meanCoverage, 3)} | meanPassRate={fmt(us.meanPassRate, 3)} | meanSafetyP={fmt(us.meanSafetyPassProbability, 3)} | n={us.probeCount ?? 0}
        </div>
      )}
      {riskPolicy?.constraint && (
        <div style={styles.summaryLine}>
          {tr('riskPolicyLabel')}: {riskPolicy.requestedMode || '--'} -> {riskPolicy.effectiveMode || '--'} ({riskPolicy.reason || 'default'})
        </div>
      )}
      {results.drccEnabled && (
        <div style={styles.summaryLine}>
          {tr('drccSummaryLabel')}: eps={fmt(drcc.epsilon, 3)} | W={fmt(drcc.wassersteinRadius, 3)} | eval={drcc.evaluatedCandidates ?? '--'} | profile={drcc.profile || '--'} | solver={drcc.solverSelected || drcc.solver || '--'}
        </div>
      )}
      {results.drccEnabled && drcc?.misocp && !nativeEngineActive && (
        <div style={styles.summaryLine}>
          {tr('drccSolverMisocpLabel')} ({tr('drccSolverLegacyOnlyLabel')}): {drcc.misocp.solverUsed || '--'} | status={drcc.misocp.status || '--'} | obj={fmt(drcc.misocp.objective, 3)} | soc={fmt(drcc.misocp.socNormValue, 3)}
        </div>
      )}
      {results.drccEnabled && prefProfiles?.safety_first && (
        <div style={styles.summaryLine}>
          {tr('preferenceProfilesLabel')}: B {prefProfiles.balanced?.selectedCount ?? '--'} / C {prefProfiles.coverage_first?.selectedCount ?? '--'} / S {prefProfiles.safety_first?.selectedCount ?? '--'}
        </div>
      )}
      {sc?.enabled && (
        <div style={styles.summaryLine}>
          {tr('selectiveConformalSummaryLabel')}: alpha={fmt(sc.alpha, 2)} | q={fmt(sc.qHatMm, 3)}mm | A/R/J={sc.decisionCounts?.accept ?? 0}/{sc.decisionCounts?.review_required ?? 0}/{sc.decisionCounts?.reject ?? 0}
        </div>
      )}
      {stratGlobal?.count > 0 && (
        <div style={styles.summaryLine}>
          {tr('stratifiedCalibrationLabel')}: n={stratGlobal.count} | {tr('calibrationEceLabel')}={fmt(stratGlobal.ece, 3)} | {tr('calibrationBrierLabel')}={fmt(stratGlobal.brier, 3)}
        </div>
      )}
      {prefPreview?.balanced?.top?.[0] && (
        <div style={styles.summaryLine}>
          {tr('preferencePreviewLabel')}: B#{prefPreview.balanced.top[0].id ?? '?'} | C#{prefPreview.coverage_first?.top?.[0]?.id ?? '?'} | S#{prefPreview.safety_first?.top?.[0]?.id ?? '?'}
        </div>
      )}
      {(Number.isFinite(w.coverage) || Number.isFinite(w.safety) || Number.isFinite(w.angle)) && (
        <div style={styles.summaryLine}>
          {tr('weightsLabel')}: C {fmt(w.coverage, 2)} | S {fmt(w.safety, 2)} | A {fmt(w.angle, 2)}
        </div>
      )}
    </div>
  );
}

function StatisticalReliabilitySummary({ results, tr }) {
  const contract = results?.statisticalContract || {};
  const strat = results?.stratifiedCalibration || {};
  const global = strat?.global || {};
  const dimensions = Array.isArray(strat?.dimensions) ? strat.dimensions : [];
  const groups = Array.isArray(strat?.groups) ? strat.groups : [];
  const fieldCoverage = contract?.fieldCoverage && typeof contract.fieldCoverage === 'object'
    ? contract.fieldCoverage
    : {};
  const coverageLine = Object.entries(fieldCoverage)
    .map(([key, val]) => {
      const count = Number(val?.count ?? 0);
      const ratio = Number(val?.ratio ?? 0);
      return `${key}:${count} (${(ratio * 100).toFixed(0)}%)`;
    })
    .join(' | ');
  const focusGroups = groups
    .filter((row) => ['brain_region', 'hemisphere', 'batch', 'atlas_mode', 'mri_mode', 'probe_type'].includes(String(row?.dimension || '')))
    .sort((a, b) => Number(b?.count ?? 0) - Number(a?.count ?? 0))
    .slice(0, 8);

  const hasContent =
    Number(global?.count ?? 0) > 0 ||
    dimensions.length > 0 ||
    Object.keys(fieldCoverage).length > 0;
  if (!hasContent) return null;

  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>
        {tr('statisticalReliabilityTitle')}
      </div>
      <div style={styles.summaryLine}>
        {tr('probeCountLabel')}: {contract?.probeCount ?? 0}
      </div>
      {coverageLine ? (
        <div style={styles.summaryLine}>
          {tr('requiredFieldCoverageLabel')}: {coverageLine}
        </div>
      ) : null}
      {Number(global?.count ?? 0) > 0 ? (
        <div style={styles.summaryLine}>
          {tr('stratifiedCalibrationLabel')}: n={global.count} | {tr('calibrationEceLabel')}={fmt(global.ece, 3)} | {tr('calibrationBrierLabel')}={fmt(global.brier, 3)} | {tr('calibrationSlopeLabel')}={fmt(global.calibrationSlope, 3)}
        </div>
      ) : null}
      {dimensions.length > 0 && (
        <div style={styles.matrixTable}>
          <div style={styles.matrixHead}>
            <span>{tr('stratifiedDimensionsTitle')}</span>
            <span>{tr('groupCountLabel')}</span>
            <span>{tr('sampleCountLabel')}</span>
            <span>{tr('calibrationEceLabel')}</span>
            <span>{tr('calibrationBrierLabel')}</span>
            <span>{tr('calibrationSlopeLabel')}</span>
          </div>
          {dimensions.map((row) => (
            <div key={String(row.dimension)} style={styles.matrixRow}>
              <span>{row.dimension}</span>
              <span>{row.groupCount ?? '--'}</span>
              <span>{row.sampleCount ?? '--'}</span>
              <span>{fmt(Number(row.eceWeighted), 3)}</span>
              <span>{fmt(Number(row.brierWeighted), 3)}</span>
              <span>{fmt(Number(row.calibrationSlopeWeighted), 3)}</span>
            </div>
          ))}
        </div>
      )}
      {focusGroups.length > 0 && (
        <div style={styles.summaryLine}>
          {tr('stratifiedGroupsPreviewLabel')}: {focusGroups
            .map((row) => `${row.dimension}/${row.label}:n${row.count},ECE${fmt(Number(row.ece), 3)},B${fmt(Number(row.brier), 3)}`)
            .join(' | ')}
        </div>
      )}
    </div>
  );
}

function ParetoScatter({ probes, onHighlight, tr }) {
  const points = useMemo(() => {
    return (probes || [])
      .map((p, idx) => {
        const c = Number(p.objectiveScores?.coverage);
        const s = Number(p.objectiveScores?.safety);
        const a = Number(p.objectiveScores?.angle);
        if (![c, s, a].every(Number.isFinite)) return null;
        return {
          idx,
          coverage: c,
          safety: s,
          angle: a,
          rank: Number(p.paretoRank),
        };
      })
      .filter(Boolean);
  }, [probes]);

  if (!points.length) return null;

  const width = 210;
  const height = 142;
  const pad = 20;
  const xOf = (v) => pad + Math.max(0, Math.min(1, v)) * (width - pad * 2);
  const yOf = (v) => height - pad - Math.max(0, Math.min(1, v)) * (height - pad * 2);
  const frontOne = points
    .filter((p) => Number.isFinite(p.rank) && p.rank === 1)
    .sort((a, b) => a.coverage - b.coverage);
  const frontPath = frontOne
    .map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${xOf(p.coverage)} ${yOf(p.safety)}`)
    .join(' ');

  return (
    <div style={styles.scatterWrap}>
      <div style={styles.scatterTitle}>
        {tr('interactiveParetoTitle')}
      </div>
      <svg width={width} height={height} style={styles.scatterSvg}>
        <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="#315174" strokeWidth="1" />
        <line x1={pad} y1={pad} x2={pad} y2={height - pad} stroke="#315174" strokeWidth="1" />
        {frontPath ? (
          <path
            d={frontPath}
            fill="none"
            stroke="#4ade80"
            strokeWidth="1.1"
            strokeDasharray="3 2"
            opacity="0.75"
          />
        ) : null}
        {points.map((p) => {
          const x = xOf(p.coverage);
          const y = yOf(p.safety);
          const r = 3 + Math.max(0, Math.min(1, p.angle)) * 2;
          const c = Number.isFinite(p.rank) && p.rank === 1 ? '#4ade80' : '#60a5fa';
          return (
            <circle
              key={p.idx}
              cx={x}
              cy={y}
              r={r}
              fill={c}
              opacity="0.92"
              stroke="#e2e8f0"
              strokeWidth="0.7"
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => onHighlight?.(p.idx)}
              onMouseLeave={() => onHighlight?.(null)}
              onClick={() => onHighlight?.(p.idx)}
            />
          );
        })}
        <text x={width / 2 - 22} y={height - 4} fill="#9fb3ca" fontSize="9">{tr('coverageShortLabel')}</text>
        <text x={2} y={12} fill="#9fb3ca" fontSize="9">{tr('safetyShortLabel')}</text>
      </svg>
    </div>
  );
}

const BRAIN_BOUNDS_REASON_KEY = {
  tip_outside_brain: 'brainBoundsReasonTipOutside',
  trajectory_exits_brain_midway: 'brainBoundsReasonMidwayExit',
  no_brain_intersection: 'brainBoundsReasonNoIntersection',
  ok: 'brainBoundsReasonOk',
};

function resolveBrainBoundsReasonLabel(reasonCode, tr) {
  const key = BRAIN_BOUNDS_REASON_KEY[String(reasonCode || '').trim().toLowerCase()];
  return key ? tr(key) : tr('brainBoundsReasonUnknown');
}

function formatBrainBoundsSegmentLabel(card, tr) {
  const seg = Array.isArray(card?.segmentDepthMm)
    ? card.segmentDepthMm.map((v) => Number(v))
    : null;
  if (seg && seg.length >= 2 && Number.isFinite(seg[0]) && Number.isFinite(seg[1])) {
    return tr('brainBoundsOutsideSegmentTemplate')
      .replace('{start}', fmt(seg[0], 3))
      .replace('{end}', fmt(seg[1], 3));
  }
  return tr('brainBoundsOutsideSegmentUnavailable');
}

function FailureCards({ cards, tr, onApplyRepair }) {
  const rows = Array.isArray(cards) ? cards : [];
  if (!rows.length) return null;
  return (
    <div style={styles.failBox}>
      <div style={styles.failTitle}>
        {tr('failureCardsTitle')} ({rows.length})
      </div>
      {rows.map((card, idx) => {
        const seg = Array.isArray(card?.segmentDepthMm)
          ? `${fmt(card.segmentDepthMm[0], 3)} ~ ${fmt(card.segmentDepthMm[1], 3)} mm`
          : '--';
        const cls = card?.hitStructureClass || card?.hitSource || 'unknown';
        const alt = card?.alternativeCandidate;
        const altReason = alt?.reasonCn || alt?.reasonEn || alt?.reasonCode || '--';
        const cf = Array.isArray(card?.counterfactualPassIf) ? card.counterfactualPassIf : [];
        const closestDelta = card?.closestFeasibleDelta && typeof card.closestFeasibleDelta === 'object'
          ? card.closestFeasibleDelta
          : null;
        const canRepair = typeof onApplyRepair === 'function' && cf.length > 0;
        const constraint = String(card?.constraint || '').trim();
        const summary = card?.brainMaskSummary && typeof card.brainMaskSummary === 'object'
          ? card.brainMaskSummary
          : null;
        if (constraint === 'brainBounds' && summary) {
          const maxOutsideGapMm = Number(summary.maxOutsideGapUm) / 1000.0;
          const outsideGapThresholdMm = Number(summary.outsideGapUmThreshold) / 1000.0;
          const tipInsideText = summary.tipInside === true
            ? tr('brainBoundsTipInsideYes')
            : (summary.tipInside === false ? tr('brainBoundsTipInsideNo') : '--');
          return (
            <div key={`${card?.probeIdx ?? idx}-${idx}`} style={styles.failItem}>
              <div style={styles.failHead}>
                P{(card?.probeIdx ?? 0) + 1} - {tr('constraintBrainBoundsLabel')}
              </div>
              <div style={styles.failLine}>
                {tr('brainBoundsReasonLabel')}: {resolveBrainBoundsReasonLabel(summary.reason, tr)}
              </div>
              <div style={styles.failLine}>
                {tr('brainBoundsOutsideSegmentLabel')}: {formatBrainBoundsSegmentLabel(card, tr)}
              </div>
              {Number.isFinite(maxOutsideGapMm) && (
                <div style={styles.failLine}>
                  {tr('brainBoundsOutsideGapLabel')}: {fmt(maxOutsideGapMm, 3)} mm
                </div>
              )}
              {Number.isFinite(outsideGapThresholdMm) && (
                <div style={styles.failLine}>
                  {tr('brainBoundsGapThresholdLabel')}: {fmt(outsideGapThresholdMm, 3)} mm
                </div>
              )}
              <div style={styles.failLine}>
                {tr('brainBoundsTipInsideLabel')}: {tipInsideText}
              </div>
              {Number.isFinite(Number(summary.sampleCount)) && (
                <div style={styles.failLine}>
                  {tr('segmentSampleCountLabel')}: {Number(summary.sampleCount)}
                </div>
              )}
              {altReason !== '--' && (
                <div style={styles.failLine}>
                  {tr('alternativeReasonLabel')}: {altReason}
                </div>
              )}
              {canRepair && (
                <button
                  type="button"
                  style={styles.failActionBtn}
                  onClick={() => onApplyRepair(card)}
                >
                  {tr('applyRepairAndRerunLabel')}
                </button>
              )}
            </div>
          );
        }
        return (
          <div key={`${card?.probeIdx ?? idx}-${idx}`} style={styles.failItem}>
            <div style={styles.failHead}>
              P{(card?.probeIdx ?? 0) + 1} - {card?.constraint || tr('riskDefaultLabel')}
            </div>
            <div style={styles.failLine}>
              {tr('hitClassLabel')}: {cls}
            </div>
            <div style={styles.failLine}>
              {tr('segmentLabel')}: {seg}
            </div>
            <div style={styles.failLine}>
              {tr('minDistanceThresholdLabel')}: {fmt(card?.minDistanceMm, 3)} / {fmt(card?.thresholdMm, 3)} mm
            </div>
            {card?.thresholdSource && (
              <div style={styles.failLine}>
                {tr('thresholdSourceLabel')}: {card.thresholdSource}
              </div>
            )}
            {Number.isFinite(Number(card?.segmentSampleCount)) && (
              <div style={styles.failLine}>
                {tr('segmentSampleCountLabel')}: {card.segmentSampleCount}
              </div>
            )}
            <div style={styles.failLine}>
              {tr('alternativeReasonLabel')}: {altReason}
            </div>
            {closestDelta && (
              <div style={styles.failLine}>
                {tr('closestFeasibleDeltaLabel')}: AP {fmt(Number(closestDelta.apShiftMm) * 1000, 0)}um / ML {fmt(Number(closestDelta.mlShiftMm) * 1000, 0)}um / {fmt(closestDelta.angleDeltaDeg, 2)}deg / {fmt(closestDelta.depthRetreatMm, 3)}mm
              </div>
            )}
            {cf.length > 0 && (
              <div style={styles.failLine}>
                {tr('counterfactualSuggestionLabel')}: {cf
                  .slice(0, 2)
                  .map((row) => {
                    if (row?.parameter === 'minRiskDistanceMm') {
                      return `minRiskDistance <= ${fmt(row.requiredAtMost, 3)} mm`;
                    }
                    if (row?.parameter === 'depthUm') {
                      return `depth <= ${fmt(Number(row.requiredAtMost) / 1000, 3)} mm`;
                    }
                    if (row?.parameter === 'maxAngle') {
                      return `maxAngle <= ${fmt(row.requiredAtMost, 2)} deg`;
                    }
                    if (row?.parameter === 'nearestFeasibleCandidate') {
                      return `nearest feasible #${row.candidateId ?? '?'} (AP=${fmt(row.deltaApUm, 1)}um, ML=${fmt(row.deltaMlUm, 1)}um, angle=${fmt(row.deltaAngleDeg, 2)}deg, depth=${fmt(row.deltaDepthUm, 1)}um)`;
                    }
                    if (row?.parameter === 'theta_deg') {
                      return `theta adjust ~ ${fmt(row.deltaDeg, 2)} deg`;
                    }
                    if (row?.parameter === 'entryOffsetUm') {
                      return `entry shift ~ ${fmt(row.deltaUm, 1)} um`;
                    }
                    return `${row?.parameter || 'param'} adjust`;
                  })
                  .join(' | ')}
              </div>
            )}
            {canRepair && (
              <button
                type="button"
                style={styles.failActionBtn}
                onClick={() => onApplyRepair(card)}
              >
                {tr('applyRepairAndRerunLabel')}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ReproSummary({ results, tr }) {
  const requestHash = results?.requestHash;
  const responseHash = results?.responseHash;
  const deterministicResponseHash = results?.deterministicResponseHash;
  const requestId = results?.requestId;
  const engineVersion = results?.engineVersion;
  const atlasHash = results?.atlasHash;
  if (!requestHash && !responseHash && !deterministicResponseHash && !requestId) return null;

  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>
        {tr('reproSignatureTitle')}
      </div>
      {requestId && <div style={styles.summaryLine}>requestId: {requestId}</div>}
      {engineVersion && <div style={styles.summaryLine}>engineVersion: {engineVersion}</div>}
      {atlasHash && <div style={styles.summaryLine}>atlasHash: {atlasHash}</div>}
      {requestHash && <div style={styles.summaryLine}>requestHash: {requestHash}</div>}
      {responseHash && <div style={styles.summaryLine}>responseHash: {responseHash}</div>}
      {deterministicResponseHash && (
        <div style={styles.summaryLine}>deterministicResponseHash: {deterministicResponseHash}</div>
      )}
    </div>
  );
}

function SideCoverageSummary({ sideCoverage, tr }) {
  const rows = Object.entries(sideCoverage || {});
  if (!rows.length) return null;
  return (
    <div style={styles.summaryBox}>
      <div style={styles.summaryTitle}>{tr('sideCoverageTitle')}</div>
      {rows.map(([key, side]) => {
        const left = Boolean(side?.left);
        const right = Boolean(side?.right);
        const status = left && right
          ? tr('bilateralLabel')
          : left
            ? 'L'
            : right
              ? 'R'
              : tr('noneLabel');
        return (
          <div key={key} style={styles.summaryLine}>
            {key}: {status}
          </div>
        );
      })}
    </div>
  );
}

function CoverageBar({ covered, total, pct, frac, tr }) {
  return (
    <div style={styles.covBox}>
      <div style={styles.covRow}>
        <span style={styles.covLabel}>{tr('coverageLabel')}</span>
        <span style={{ ...styles.covPct, color: coverageColor(frac) }}>{pct}%</span>
        <span style={styles.covDetail}>
          ({covered ?? '?'} / {total ?? '?'} {tr('voxels')})
        </span>
      </div>
      <div style={styles.barTrack}>
        <div
          style={{
            ...styles.barFill,
            width: `${Math.min(100, frac * 100)}%`,
            background: coverageColor(frac),
          }}
        />
      </div>
    </div>
  );
}

function WarningList({ warnings, tr, lang }) {
  return (
    <div style={styles.warnBox}>
      <div style={styles.warnTitle}>{tr('warningsTitle')} ({warnings.length})</div>
      {warnings.map((w, i) => (
        <div key={i} style={styles.warnItem}>
          <span style={styles.warnProbe}>P{(w.probeIdx ?? 0) + 1}</span>
          <span style={styles.warnText}>{localizeFreeText(w.message, lang)}</span>
        </div>
      ))}
    </div>
  );
}

function ProbeCard({ idx, probe, probeSpec, expanded, onToggle, onMouseEnter, onMouseLeave, lang, tr }) {
  const color = PROBE_COLORS[idx % PROBE_COLORS.length] ?? '#60a5fa';
  const robustScore = Number(probe.robustScore);
  const explainScore = Number(probe.explainScore);
  const covMean = Number(probe.coverageConfidence?.mean);
  const covCi = probe.coverageConfidence?.ci95;
  const passRate = Number(probe.coverageConfidence?.passRate);
  const safetyCi = probe.safetyMarginCi95;
  const statisticalSafety = probe.statisticalSafety && typeof probe.statisticalSafety === 'object'
    ? probe.statisticalSafety
    : null;
  const minSafetyDistance = Number(
    statisticalSafety?.minDistanceMm ?? probe.minSafetyDistanceMm ?? probe.minSafetyDistanceMeanMm
  );
  const minSafetyDistanceCi = statisticalSafety?.minDistanceCi95 ?? probe.minSafetyDistanceCi95;
  const safetyDistConf = statisticalSafety
    ? {
      probability: statisticalSafety.pSafetyGtThreshold,
      ci95: statisticalSafety.pSafetyGtThresholdCi95,
      thresholdMm: statisticalSafety.thresholdMm,
    }
    : probe.safetyDistanceConfidence;
  const riskDiag = probe.riskDiagnostics;
  const drcc = probe.drccMetrics;
  const sc = probe.selectiveConformal;
  const unc = probe.uncertainty;
  const primaryEp = probe.primaryEndpoints;

  return (
    <div
      style={{ ...styles.probeCard, borderLeftColor: color }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div style={styles.probeHeader} onClick={onToggle}>
        <span style={{ ...styles.probeName, color }}>
          {tr('probe')} {idx + 1}
        </span>
        <span style={styles.probeSummary}>
          AP {fmt(probe.ap)} ML {fmt(probe.ml)} Az {fmt(probe.azimuth, 1)}deg El {fmt(probe.elevation, 1)}deg
        </span>
        <span style={styles.probeRegionCount}>
          {probe.regionsHit?.length ?? 0} {tr('regionsHit')}
        </span>
        <span style={{ ...styles.scoreChip, color: scoreColor(explainScore) }}>
          {tr('scoreLabel')} {Number.isFinite(explainScore) ? explainScore.toFixed(3) : '--'}
        </span>
        <span style={styles.expandArrow}>{expanded ? tr('hideLabel') : tr('showLabel')}</span>
      </div>

      {expanded && (
        <div style={styles.probeDetail}>
          <DetailRow label="AP (mm)" value={fmt(probe.ap)} />
          <DetailRow label="ML (mm)" value={fmt(probe.ml)} />
          <DetailRow label="DV entry (mm)" value={fmt(probe.dvEntry)} />
          <DetailRow label={tr('azimuth')} value={`${fmt(probe.azimuth, 1)}deg`} />
          <DetailRow label={tr('elevation')} value={`${fmt(probe.elevation, 1)}deg`} />
          <DetailRow label={tr('depth')} value={`${fmt(probeSpec?.depth)} mm`} />
          <DetailRow
            label={tr('paretoRankLabel')}
            value={Number.isFinite(probe.paretoRank) ? `F${probe.paretoRank}` : '--'}
          />
          <DetailRow
            label={tr('robustScoreLabel')}
            value={Number.isFinite(robustScore) ? robustScore.toFixed(3) : '--'}
            valueColor={scoreColor(robustScore)}
          />
          <DetailRow
            label={tr('explainScoreLabel')}
            value={Number.isFinite(explainScore) ? explainScore.toFixed(3) : '--'}
            valueColor={scoreColor(explainScore)}
          />

          {probe.coverageConfidence && Array.isArray(covCi) && (
            <DetailRow
              label={tr('coverage95CiLabel')}
              value={
                Number.isFinite(covMean)
                  ? `${(covMean * 100).toFixed(1)}% [${(covCi[0] * 100).toFixed(1)}, ${(covCi[1] * 100).toFixed(1)}]`
                  : '--'
              }
            />
          )}
          {probe.coverageConfidence && (
            <DetailRow
              label={tr('passRateLabel')}
              value={Number.isFinite(passRate) ? `${(passRate * 100).toFixed(1)}%` : '--'}
            />
          )}
          {primaryEp && (
            <DetailRow
              label={tr('endpointCoverageLabel')}
              value={
                Number.isFinite(Number(primaryEp.coverageRate))
                  ? `${(Number(primaryEp.coverageRate) * 100).toFixed(1)}%`
                  : '--'
              }
            />
          )}
          {Array.isArray(safetyCi) && (
            <DetailRow
              label={tr('minSafetyMargin95CiLabel')}
              value={`${fmt(safetyCi[0], 3)} ~ ${fmt(safetyCi[1], 3)} mm`}
            />
          )}
          {Number.isFinite(minSafetyDistance) && (
            <DetailRow
              label={tr('continuousMinSafetyDistanceLabel')}
              value={`${fmt(minSafetyDistance, 3)} mm`}
              valueColor={scoreColor(minSafetyDistance >= 0.35 ? 0.9 : minSafetyDistance >= 0.2 ? 0.55 : 0.2)}
            />
          )}
          {Array.isArray(minSafetyDistanceCi) && (
            <DetailRow
              label={tr('minSafetyDistance95CiLabel')}
              value={`${fmt(minSafetyDistanceCi[0], 3)} ~ ${fmt(minSafetyDistanceCi[1], 3)} mm`}
            />
          )}
          {safetyDistConf && (
            <DetailRow
              label={tr('safetyProbThresholdLabel')}
              value={
                Number.isFinite(Number(safetyDistConf.probability))
                  ? `${(Number(safetyDistConf.probability) * 100).toFixed(1)}% [${(Number(safetyDistConf.ci95?.[0] ?? 0) * 100).toFixed(1)}, ${(Number(safetyDistConf.ci95?.[1] ?? 0) * 100).toFixed(1)}], thr=${fmt(safetyDistConf.thresholdMm, 3)} mm`
                  : '--'
              }
            />
          )}
          {drcc && (
            <DetailRow
              label={tr('drccWorstCaseProbLabel')}
              value={`${fmt(drcc.worstCaseViolationProb, 3)} (eps=${fmt(drcc.riskBudgetEpsilon, 3)})`}
              valueColor={scoreColor((1 - Number(drcc.worstCaseViolationProb ?? 1)))}
            />
          )}
          {drcc && (
            <DetailRow
              label={tr('sdfRiskFunctionalLabel')}
              value={`int=${fmt(drcc.riskIntegralShortfallMm, 3)} | tail=${fmt(drcc.riskTailShortfallMm, 3)} mm`}
            />
          )}
          {sc && (
            <DetailRow
              label={tr('selectiveConformalDecisionLabel')}
              value={`${sc.decision || '--'} | p*=${fmt(sc.probabilityGuaranteed, 3)} | lb=${fmt(sc.guaranteedLowerBoundMm, 3)} mm`}
            />
          )}
          {riskDiag && (
            <DetailRow
              label={tr('riskHitSegmentLabel')}
              value={
                Array.isArray(riskDiag.thresholdSegmentsMm) && riskDiag.thresholdSegmentsMm[0]
                  ? `${fmt(riskDiag.thresholdSegmentsMm[0].startDepthMm, 3)} ~ ${fmt(riskDiag.thresholdSegmentsMm[0].endDepthMm, 3)} mm`
                  : `${tr('closestDepthPrefix')} ${fmt(riskDiag.closestDepthMm, 3)} mm`
              }
            />
          )}
          {riskDiag && (
            <DetailRow
              label={tr('riskClassLabel')}
              value={`${riskDiag.nearestSourceClass || riskDiag.nearestSource || '--'}`}
            />
          )}
          {unc && (
            <DetailRow
              label={tr('tipSigmaLabel')}
              value={`${fmt(unc.tipSigmaMm, 3)} mm`}
            />
          )}
          {unc && (
            <DetailRow
              label={tr('coneHalfAngleLabel')}
              value={`${fmt(unc.coneHalfAngleDeg, 2)} deg`}
            />
          )}

          {probe.objectiveScores && (
            <div style={styles.objBox}>
              <ObjRow
                label={tr('coverageObjectiveLabel')}
                raw={probe.objectiveScores.coverage}
                weighted={probe.objectiveWeighted?.coverage}
              />
              <ObjRow
                label={tr('safetyObjectiveLabel')}
                raw={probe.objectiveScores.safety}
                weighted={probe.objectiveWeighted?.safety}
              />
              <ObjRow
                label={tr('angleObjectiveLabel')}
                raw={probe.objectiveScores.angle}
                weighted={probe.objectiveWeighted?.angle}
              />
            </div>
          )}

          {probe.explain?.summary && (
            <div style={styles.explainText}>{probe.explain.summary}</div>
          )}
          {probe.explain?.whySelected && (
            <div style={styles.explainText}>{probe.explain.whySelected}</div>
          )}
          {probe.explain?.vsAlternative && (
            <div style={styles.explainText}>{probe.explain.vsAlternative}</div>
          )}

          {probe.regionsHit?.length > 0 && (
            <div style={styles.regionsRow}>
              <span style={styles.detailLabel}>{tr('regionsHit')}:</span>
              <div style={styles.regionChips}>
                {probe.regionsHit.map((r) => (
                  <span key={r} style={styles.regionChip}>{r}</span>
                ))}
              </div>
            </div>
          )}

          {probe.violations?.length > 0 && (
            <div style={styles.violationsBox}>
              {probe.violations.map((v, i) => (
                <span key={i} style={styles.violationChip}>
                  ! {resolveLocalizedViolationText(v, lang, tr)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ObjRow({ label, raw, weighted }) {
  return (
    <div style={styles.objRow}>
      <span style={styles.objLabel}>{label}</span>
      <span style={styles.objVal}>{fmt(raw, 3)}</span>
      <span style={styles.objVal}>{fmt(weighted, 3)}</span>
    </div>
  );
}

function DetailRow({ label, value, valueColor }) {
  return (
    <div style={styles.detailRow}>
      <span style={styles.detailLabel}>{label}</span>
      <span style={{ ...styles.detailValue, color: valueColor || THEME.textPrimary }}>{value}</span>
    </div>
  );
}

function RegionCoverageTable({ byRegion, selectedRegions, tr }) {
  const rows = useMemo(
    () =>
      Object.entries(byRegion)
        .filter(([r]) => selectedRegions.includes(r))
        .sort(([, a], [, b]) => b.frac - a.frac),
    [byRegion, selectedRegions]
  );

  if (!rows.length) return null;

  return (
    <div style={styles.regionTable}>
      <div style={styles.regionTableTitle}>{tr('regionCoverageTitle')}</div>
      {rows.map(([region, stat]) => (
        <div key={region} style={styles.regionRow}>
          <span style={styles.regionName}>{region}</span>
          <div style={styles.miniBarTrack}>
            <div
              style={{
                ...styles.miniBarFill,
                width: `${Math.min(100, (stat.frac ?? 0) * 100)}%`,
                background: coverageColor(stat.frac ?? 0),
              }}
            />
          </div>
          <span style={{ ...styles.regionPct, color: coverageColor(stat.frac ?? 0) }}>
            {((stat.frac ?? 0) * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  );
}

const styles = {
  panel: {
    background: THEME.panelBg,
    borderRadius: 8,
    padding: '12px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    minHeight: 80,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: {
    color: THEME.textPrimary,
    fontWeight: 600,
    fontSize: 13,
  },
  metaBadge: {
    fontSize: 10,
    color: THEME.textSecondary,
    background: THEME.inputBg,
    borderRadius: 4,
    padding: '2px 7px',
    fontFamily: 'monospace',
  },
  emptyMsg: {
    color: THEME.textSecondary,
    fontSize: 12,
    textAlign: 'center',
    padding: '16px 0',
  },
  covBox: {
    background: THEME.inputBg,
    borderRadius: 6,
    padding: '8px 10px',
  },
  covRow: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
    marginBottom: 5,
  },
  covLabel: { fontSize: 11, color: THEME.textSecondary },
  covPct: { fontSize: 20, fontWeight: 700 },
  covDetail: { fontSize: 10, color: THEME.textSecondary },
  barTrack: {
    height: 6,
    background: THEME.border,
    borderRadius: 3,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 3,
    transition: 'width 0.4s ease',
  },
  summaryBox: {
    background: '#051426',
    border: '1px solid #1e3a5f',
    borderRadius: 6,
    padding: '8px 10px',
    display: 'grid',
    gap: 3,
  },
  summaryTitle: {
    color: '#93c5fd',
    fontSize: 11,
    fontWeight: 700,
    marginBottom: 2,
  },
  summaryLine: {
    color: '#94a3b8',
    fontSize: 10.5,
    fontFamily: 'monospace',
  },
  candidateStageTable: {
    marginTop: 4,
    border: '1px solid #1b3758',
    borderRadius: 5,
    overflow: 'hidden',
    background: '#061628',
    display: 'grid',
    gap: 0,
  },
  candidateStageRow: {
    padding: '5px 6px',
    borderTop: '1px solid #10263d',
    display: 'grid',
    gap: 3,
  },
  candidateStageHead: {
    display: 'grid',
    gridTemplateColumns: '1.4fr 1fr 1.3fr',
    gap: 6,
    fontSize: 9.5,
    color: '#cbd5e1',
    fontFamily: 'monospace',
  },
  candidateStageReasonLine: {
    fontSize: 9.5,
    color: '#9fb3ca',
    fontFamily: 'monospace',
  },
  matrixTable: {
    marginTop: 4,
    border: '1px solid #1b3758',
    borderRadius: 5,
    overflow: 'hidden',
    background: '#061628',
  },
  matrixHead: {
    display: 'grid',
    gridTemplateColumns: '1.4fr 0.8fr 0.8fr 0.9fr 0.9fr 1fr',
    gap: 6,
    fontSize: 9.5,
    fontWeight: 700,
    color: '#93c5fd',
    background: '#0b2038',
    padding: '4px 6px',
  },
  matrixRow: {
    display: 'grid',
    gridTemplateColumns: '1.4fr 0.8fr 0.8fr 0.9fr 0.9fr 1fr',
    gap: 6,
    fontSize: 9.5,
    color: '#cbd5e1',
    padding: '4px 6px',
    borderTop: '1px solid #10263d',
    fontFamily: 'monospace',
  },
  criticalTable: {
    marginTop: 4,
    border: '1px solid #1b3758',
    borderRadius: 5,
    overflow: 'hidden',
    background: '#061628',
  },
  criticalHead: {
    display: 'grid',
    gridTemplateColumns: '0.7fr 0.9fr 2.2fr 1.2fr 1.1fr',
    gap: 6,
    fontSize: 9.5,
    fontWeight: 700,
    color: '#93c5fd',
    background: '#0b2038',
    padding: '4px 6px',
  },
  criticalRow: {
    display: 'grid',
    gridTemplateColumns: '0.7fr 0.9fr 2.2fr 1.2fr 1.1fr',
    gap: 6,
    fontSize: 9.5,
    color: '#cbd5e1',
    padding: '4px 6px',
    borderTop: '1px solid #10263d',
    fontFamily: 'monospace',
    alignItems: 'center',
  },
  errorBox: {
    background: '#3f1015',
    border: '1px solid #9f1239',
    borderRadius: 6,
    padding: '8px 10px',
    display: 'grid',
    gap: 4,
  },
  errorTitle: {
    color: '#fecdd3',
    fontSize: 11,
    fontWeight: 700,
  },
  errorText: {
    color: '#fda4af',
    fontSize: 10.5,
    fontFamily: 'monospace',
    lineHeight: 1.4,
    wordBreak: 'break-word',
  },
  scatterWrap: {
    background: '#07162a',
    border: '1px solid #1b3758',
    borderRadius: 6,
    padding: '6px 8px',
    display: 'grid',
    gap: 4,
    width: 'fit-content',
  },
  scatterTitle: {
    color: '#9fc4ea',
    fontSize: 10.5,
  },
  scatterSvg: {
    background: '#071224',
    borderRadius: 4,
    border: '1px solid #1b3555',
  },
  warnBox: {
    background: '#451a0320',
    border: '1px solid #facc1544',
    borderRadius: 6,
    padding: '8px 10px',
  },
  warnTitle: {
    color: '#facc15',
    fontSize: 11,
    fontWeight: 600,
    marginBottom: 5,
  },
  warnItem: {
    display: 'flex',
    gap: 8,
    fontSize: 11,
    color: THEME.textSecondary,
    paddingBottom: 3,
  },
  warnProbe: { fontWeight: 700, color: THEME.textPrimary, minWidth: 22 },
  warnText: { flex: 1 },
  failBox: {
    background: '#3b0b1322',
    border: '1px solid #fb718544',
    borderRadius: 6,
    padding: '8px 10px',
    display: 'grid',
    gap: 6,
  },
  failTitle: {
    color: '#fb7185',
    fontSize: 11,
    fontWeight: 700,
  },
  failItem: {
    background: '#3b1020',
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    padding: '6px 8px',
    display: 'grid',
    gap: 2,
  },
  failHead: {
    color: '#fecdd3',
    fontSize: 10.5,
    fontWeight: 700,
  },
  failLine: {
    color: '#fda4af',
    fontSize: 10,
    fontFamily: 'monospace',
  },
  failActionBtn: {
    marginTop: 4,
    justifySelf: 'start',
    border: '1px solid #f97316',
    color: '#fdba74',
    background: '#7c2d1230',
    borderRadius: 4,
    fontSize: 10,
    padding: '2px 8px',
    cursor: 'pointer',
  },
  probeList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  probeCard: {
    background: THEME.inputBg,
    borderRadius: 6,
    borderLeft: '3px solid transparent',
    overflow: 'hidden',
  },
  probeHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '7px 10px',
    cursor: 'pointer',
    userSelect: 'none',
  },
  probeName: { fontSize: 12, fontWeight: 700, minWidth: 50 },
  probeSummary: {
    fontSize: 10,
    color: THEME.textSecondary,
    fontFamily: 'monospace',
    flex: 1,
  },
  probeRegionCount: {
    fontSize: 10,
    color: THEME.textSecondary,
    background: THEME.border,
    borderRadius: 10,
    padding: '1px 7px',
  },
  scoreChip: {
    fontSize: 10,
    fontFamily: 'monospace',
    background: '#0b2035',
    border: '1px solid #1f3a5b',
    borderRadius: 10,
    padding: '1px 7px',
    minWidth: 78,
    textAlign: 'center',
  },
  expandArrow: { fontSize: 10, color: THEME.textSecondary, minWidth: 34, textAlign: 'right' },
  probeDetail: {
    padding: '8px 10px 10px',
    borderTop: `1px solid ${THEME.border}`,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  detailRow: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
  },
  detailLabel: { color: THEME.textSecondary },
  detailValue: { fontFamily: 'monospace' },
  objBox: {
    marginTop: 4,
    padding: '5px 6px',
    borderRadius: 5,
    border: '1px solid #1f3555',
    background: '#081629',
    display: 'grid',
    gap: 3,
  },
  objRow: {
    display: 'grid',
    gridTemplateColumns: '1fr auto auto',
    gap: 8,
    alignItems: 'center',
  },
  objLabel: { color: '#93c5fd', fontSize: 10.5 },
  objVal: { color: '#cbd5e1', fontSize: 10, fontFamily: 'monospace' },
  explainText: {
    marginTop: 4,
    fontSize: 10.5,
    lineHeight: 1.45,
    color: '#b6c6dd',
    background: '#0a1524',
    border: '1px solid #1c2e45',
    borderRadius: 5,
    padding: '6px 7px',
  },
  regionsRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    marginTop: 4,
  },
  regionChips: { display: 'flex', flexWrap: 'wrap', gap: 4 },
  regionChip: {
    fontSize: 10,
    background: `${THEME.accent}22`,
    color: THEME.accent,
    borderRadius: 4,
    padding: '1px 6px',
  },
  violationsBox: { display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 },
  violationChip: {
    fontSize: 10,
    background: '#45240520',
    color: '#facc15',
    borderRadius: 4,
    padding: '1px 6px',
  },
  regionTable: {
    borderTop: `1px solid ${THEME.border}`,
    paddingTop: 8,
    display: 'flex',
    flexDirection: 'column',
    gap: 5,
  },
  regionTableTitle: {
    fontSize: 11,
    fontWeight: 600,
    color: THEME.textSecondary,
    marginBottom: 2,
  },
  regionRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 11,
  },
  regionName: {
    width: 70,
    color: THEME.textPrimary,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  miniBarTrack: {
    flex: 1,
    height: 4,
    background: THEME.border,
    borderRadius: 2,
    overflow: 'hidden',
  },
  miniBarFill: { height: '100%', borderRadius: 2 },
  regionPct: {
    width: 32,
    textAlign: 'right',
    fontFamily: 'monospace',
    fontWeight: 600,
    fontSize: 10,
  },
};





