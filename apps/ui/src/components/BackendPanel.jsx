import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { THEME } from '../constants/theme';
import { t } from '../constants/i18n';

const STATUS_META = {
  idle: { color: '#94a3b8', dot: 'o', labelKey: 'backendIdle' },
  connecting: { color: '#60a5fa', dot: 'o', labelKey: 'backendConnecting', spin: true },
  loading: { color: '#a78bfa', dot: 'o', labelKey: 'backendLoading', spin: true },
  online: { color: '#4ade80', dot: 'o', labelKey: 'backendOnline' },
  offline: { color: '#f87171', dot: 'o', labelKey: 'backendOffline' },
};

function getShape(cap) {
  if (cap?.annotationShape) return String(cap.annotationShape);
  if (Array.isArray(cap?.atlasShape)) return cap.atlasShape.join('x');
  return '?';
}

function getVoxelSize(cap) {
  const v = cap?.voxelSizeUm ?? cap?.voxelSize;
  return v == null ? '?' : String(v);
}

function getVersion(cap) {
  return cap?.version ?? cap?.engineVersion ?? '-';
}

function getAtlasHash(cap) {
  return cap?.atlasHash ?? cap?.atlasSignature?.hash ?? '-';
}

function getRegionIndexStatus(cap) {
  const row = cap?.regionIndex;
  if (row && typeof row === 'object') return row;
  const cache = cap?.atlasSignature?.regionIndexCache && typeof cap.atlasSignature.regionIndexCache === 'object'
    ? cap.atlasSignature.regionIndexCache
    : {};
  return {
    mode: cap?.atlasSignature?.regionIndexMode || '-',
    startupFullRegionPlanning: Boolean(cap?.fullBrainPlannerReady),
    dynamicRegionProvisioning: Boolean(cap?.dynamicRegionProvisioning),
    cacheEnabled: cache.enabled,
    cacheSource: cache.source || '-',
    cacheHit: cache.source === 'cache_hit',
    cachePath: cache.path || null,
    plannerSeedCount: cache.plannerSeedCount,
    plannerRegionCount: cap?.plannerRegionCount,
    indexedRegionCount: cap?.indexedRegionCount,
    catalogRegionCount: cap?.catalogRegionCount,
    presentRegionCount: cap?.presentRegionCount,
    availableRegionCount: cap?.availableRegionCount,
    fullBrainPlannerReady: Boolean(cap?.fullBrainPlannerReady),
    catalogPlannerAligned: null,
  };
}

function getClinicalContract(cap) {
  return cap?.clinicalContract ?? null;
}

function getAtlasLoad(cap) {
  return cap?.atlasLoad ?? null;
}

function getActiveAtlasMode(cap, fallback = 'allen_sdk') {
  return cap?.activeAtlasMode ?? fallback;
}

function getQiuConfig(cap) {
  const qiu = cap?.qiu2018;
  if (!qiu || typeof qiu !== 'object') {
    return {
      annotationPath: null,
      regionMapPath: null,
      statsPath: null,
      jacobianPath: null,
      annotationConfigured: false,
      regionMapConfigured: false,
    };
  }
  return {
    annotationPath: qiu.annotationPath || null,
    regionMapPath: qiu.regionMapPath || null,
    statsPath: qiu.statsPath || null,
    jacobianPath: qiu.jacobianPath || null,
    annotationConfigured: Boolean(qiu.annotationConfigured),
    regionMapConfigured: Boolean(qiu.regionMapConfigured),
  };
}

function getMriConfig(cap) {
  const mri = cap?.mriCcfHybrid;
  if (!mri || typeof mri !== 'object') {
    return {
      annotationPath: null,
      regionMapPath: null,
      statsPath: null,
      jacobianPath: null,
      skullMaskPath: null,
      outerTissueMaskPath: null,
      layeredSdfPath: null,
      manifestPath: null,
      activeProfilePath: null,
      pathConsistency: {},
      datasetProfile: 'custom',
      annotationConfigured: false,
      regionMapConfigured: false,
      statsConfigured: false,
      jacobianConfigured: false,
      skullMaskConfigured: false,
      outerTissueConfigured: false,
      layeredSdfConfigured: false,
      useMriAppearance: false,
    };
  }
  return {
    annotationPath: mri.annotationPath || null,
    regionMapPath: mri.regionMapPath || null,
    statsPath: mri.statsPath || null,
    jacobianPath: mri.jacobianPath || null,
    skullMaskPath: mri.skullMaskPath || null,
    outerTissueMaskPath: mri.outerTissueMaskPath || null,
    layeredSdfPath: mri.layeredSdfPath || null,
    manifestPath: mri.manifestPath || null,
    activeProfilePath: mri.activeProfilePath || null,
    pathConsistency: mri.pathConsistency && typeof mri.pathConsistency === 'object' ? mri.pathConsistency : {},
    datasetProfile: String(mri.profileId || mri.datasetProfile || 'custom'),
    annotationConfigured: Boolean(mri.annotationConfigured),
    regionMapConfigured: Boolean(mri.regionMapConfigured),
    statsConfigured: Boolean(mri.statsConfigured),
    jacobianConfigured: Boolean(mri.jacobianConfigured),
    skullMaskConfigured: Boolean(mri.skullMaskConfigured),
    outerTissueConfigured: Boolean(mri.outerTissueConfigured || mri.outerTissueMaskConfigured),
    layeredSdfConfigured: Boolean(mri.layeredSdfConfigured),
    useMriAppearance: Boolean(mri.useMriAppearance),
  };
}

function getMriLayerStatus(cap) {
  const row = cap?.mriLayerStatus;
  const risk = cap?.riskModel && typeof cap.riskModel === 'object' ? cap.riskModel : {};
  const mri = cap?.mriCcfHybrid && typeof cap.mriCcfHybrid === 'object' ? cap.mriCcfHybrid : {};
  const toBool = (...values) => values.some((value) => (
    value === true
    || value === 1
    || String(value).trim().toLowerCase() === 'true'
    || String(value).trim().toLowerCase() === '1'
    || String(value).trim().toLowerCase() === 'on'
    || String(value).trim().toLowerCase() === 'enabled'
  ));
  if (!row || typeof row !== 'object') {
    return {
      hybridActive: String(cap?.activeAtlasMode || '').trim().toLowerCase() === 'mri_ccf_hybrid',
      appearanceConfigured: toBool(mri.useMriAppearance),
      appearanceActive: toBool(mri.useMriAppearance),
      riskLayerStructuredMaskEnabled: toBool(risk.structuredMaskEnabled),
      riskLayerSdfEnabled: toBool(risk.sdfEnabled),
      riskLayerDistanceModel: risk.distanceModel || '-',
    };
  }
  return {
    hybridActive: toBool(row.hybridActive) || String(cap?.activeAtlasMode || '').trim().toLowerCase() === 'mri_ccf_hybrid',
    appearanceConfigured: toBool(row.appearanceConfigured, mri.useMriAppearance),
    appearanceActive: toBool(row.appearanceActive, row.useMriAppearance, row.enabled, mri.useMriAppearance),
    riskLayerStructuredMaskEnabled: toBool(
      row.riskLayerStructuredMaskEnabled,
      row.structuredMaskEnabled,
      risk.structuredMaskEnabled,
    ),
    riskLayerSdfEnabled: toBool(row.riskLayerSdfEnabled, row.sdfEnabled, risk.sdfEnabled),
    riskLayerDistanceModel: row.riskLayerDistanceModel || row.distanceModel || risk.distanceModel || '-',
  };
}

function getMriRegistrationSummary(cap) {
  const row = cap?.mriRegistrationSummary;
  if (!row || typeof row !== 'object') {
    return {
      configured: false,
      path: null,
      metrics: {},
      quickMode: null,
      skipDeformable: null,
      usedRigid: null,
      rigidMetric: null,
      usedAffine: null,
      affineMetric: null,
      usedDeformable: null,
      deformableMetric: null,
      voxelCountHead: null,
      voxelCountSkull: null,
      voxelCountOuterTissue: null,
      metricCi95: {},
      treAxisMm: {},
      transform: {},
      jacobianSummary: {},
    };
  }
  const summary = row.summary && typeof row.summary === 'object' ? row.summary : {};
  const registration = summary.registration && typeof summary.registration === 'object' ? summary.registration : {};
  const metrics = row.metrics && typeof row.metrics === 'object' ? row.metrics : {};
  return {
    configured: Boolean(row.configured),
    path: row.path || null,
    metrics,
    metricCi95: row.metricCi95 && typeof row.metricCi95 === 'object' ? row.metricCi95 : {},
    treAxisMm: row.treAxisMm && typeof row.treAxisMm === 'object' ? row.treAxisMm : {},
    transform: row.transform && typeof row.transform === 'object' ? row.transform : {},
    jacobianSummary: row.jacobianSummary && typeof row.jacobianSummary === 'object' ? row.jacobianSummary : {},
    quickMode: summary.quickMode ?? null,
    skipDeformable: summary.skipDeformable ?? null,
    usedRigid: registration.usedRigid ?? null,
    rigidMetric: registration.rigidMetric ?? null,
    usedAffine: registration.usedAffine ?? null,
    affineMetric: registration.affineMetric ?? null,
    usedDeformable: registration.usedDeformable ?? null,
    deformableMetric: registration.deformableMetric ?? null,
    voxelCountHead: summary.voxelCountHead ?? null,
    voxelCountSkull: summary.voxelCountSkull ?? null,
    voxelCountOuterTissue: summary.voxelCountOuterTissue ?? null,
  };
}

function getMriOverlayStatus(cap) {
  const row = cap?.mriOverlayStatus;
  if (!row || typeof row !== 'object') {
    return { layers: {}, missingLayers: [], overlayModes: [] };
  }
  return {
    layers: row.layers && typeof row.layers === 'object' ? row.layers : {},
    missingLayers: Array.isArray(row.missingLayers) ? row.missingLayers : [],
    overlayModes: Array.isArray(row.overlayModes) ? row.overlayModes : [],
  };
}

function atlasModeLabel(mode, tr) {
  if (mode === 'qiu2018') return tr('atlasModeQiu2018');
  if (mode === 'mri_ccf_hybrid') return tr('atlasModeMriCcfHybrid');
  return tr('atlasModeAllenSdk');
}

function normalizeLoadProgress(capabilities) {
  const load = getAtlasLoad(capabilities);
  if (!load || typeof load !== 'object') return null;
  const atlasReady = Boolean(capabilities?.atlasLoaded);
  const step = Number(load.step ?? 0);
  const total = Number(load.totalSteps ?? 0);
  const progressRaw = Number(load.progress);
  let pct = Number.isFinite(progressRaw)
    ? (progressRaw <= 1 ? progressRaw * 100 : progressRaw)
    : null;
  if (!Number.isFinite(pct) && Number.isFinite(step) && Number.isFinite(total) && total > 0) {
    pct = (step / total) * 100;
  }
  let state = String(load.state || '');
  let phase = String(load.phase || '');
  let message = String(load.message || '');
  if (atlasReady && state.toLowerCase() !== 'error') {
    state = 'ready';
    phase = 'ready';
    pct = 100;
    if (!message || /initializing risk model/i.test(message)) {
      message = 'Atlas is ready';
    }
  }
  const clamped = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
  return {
    state,
    phase,
    message,
    step: Number.isFinite(step) ? step : 0,
    total: Number.isFinite(total) ? total : 0,
    pct: clamped,
  };
}

export default function BackendPanel({
  backendState = {},
  onConnect,
  onDisconnect,
  onModeChange,
  lang = 'cn',
}) {
  const {
    status = 'idle',
    url: currentUrl = '',
    lastSeen,
    capabilities,
    error,
    atlasMode: backendAtlasMode = 'allen_sdk',
  } = backendState;
  const tr = useCallback((k) => t(k, lang), [lang]);

  const [urlInput, setUrlInput] = useState(currentUrl || 'http://localhost:5000');
  const [modeInput, setModeInput] = useState(backendAtlasMode || getActiveAtlasMode(capabilities, 'allen_sdk'));
  const [showAdvanced, setShowAdvanced] = useState(false);

  const meta = STATUS_META[status] ?? STATUS_META.idle;
  const isConnected = status === 'online' || status === 'loading';
  const isBusy = status === 'connecting';
  const loadProgress = normalizeLoadProgress(capabilities);
  const availableModes = useMemo(() => {
    const fromCap = Array.isArray(capabilities?.supportedAtlasModes) ? capabilities.supportedAtlasModes : [];
    const cleaned = fromCap.filter((m) => typeof m === 'string' && m.trim());
    if (cleaned.length > 0) return cleaned;
    return ['allen_sdk', 'qiu2018', 'mri_ccf_hybrid'];
  }, [capabilities]);

  useEffect(() => {
    const next = backendAtlasMode || getActiveAtlasMode(capabilities, modeInput || 'allen_sdk');
    if (next && next !== modeInput) {
      setModeInput(next);
    }
  }, [backendAtlasMode, capabilities, modeInput]);

  const handleConnect = useCallback(() => {
    if (isConnected) onDisconnect?.();
    else onConnect?.({ url: urlInput.trim(), atlasMode: modeInput });
  }, [isConnected, modeInput, onConnect, onDisconnect, urlInput]);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>{tr('backendPanelTitle')}</span>
        <StatusPill meta={meta} label={tr(meta.labelKey)} />
      </div>

      <div style={styles.urlRow}>
        <input
          style={{ ...styles.urlInput, opacity: isConnected || isBusy ? 0.5 : 1 }}
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          disabled={isConnected || isBusy}
          placeholder="http://localhost:5000"
          spellCheck={false}
        />
        <button
          style={{
            ...styles.connectBtn,
            background: isConnected ? '#ef444422' : isBusy ? THEME.inputBg : `${THEME.accent}22`,
            color: isConnected ? '#f87171' : isBusy ? THEME.textSecondary : THEME.accent,
            cursor: isBusy ? 'not-allowed' : 'pointer',
          }}
          onClick={handleConnect}
          disabled={isBusy}
        >
          {isBusy ? tr('backendConnecting') : isConnected ? tr('disconnect') : tr('connect')}
        </button>
      </div>

      <div style={styles.modeRow}>
        <span style={styles.modeLabel}>{tr('atlasModeLabel')}</span>
        <select
          style={{ ...styles.modeSelect, opacity: isConnected || isBusy ? 0.72 : 1 }}
          value={modeInput}
          onChange={(e) => {
            const next = String(e.target.value || '').trim();
            setModeInput(next);
            onModeChange?.(next);
          }}
          disabled={isConnected || isBusy}
        >
          {availableModes.map((mode) => {
            return (
              <option key={mode} value={mode}>
                {atlasModeLabel(mode, tr)}
              </option>
            );
          })}
        </select>
      </div>

      <CapabilityBanner status={status} capabilities={capabilities} tr={tr} />

      {loadProgress && loadProgress.state !== 'ready' && (
        <AtlasLoadProgress load={loadProgress} tr={tr} />
      )}

      {(error || capabilities?.atlasError) && (
        <div style={styles.errorBox}>
          {tr('backendError')}: {String(error || capabilities?.atlasError)}
        </div>
      )}

      {lastSeen && (
        <div style={styles.lastSeen}>
          {tr('lastSeen')}: {new Date(lastSeen).toLocaleTimeString()}
        </div>
      )}

      <div style={styles.advancedToggle} onClick={() => setShowAdvanced((s) => !s)}>
        {showAdvanced ? 'v' : '>'} {tr('advancedSettings')}
      </div>

      {showAdvanced && <AdvancedSettings capabilities={capabilities} tr={tr} />}
    </div>
  );
}

function StatusPill({ meta, label }) {
  return (
    <div style={{ ...styles.pill, borderColor: `${meta.color}55` }}>
      <span style={{ ...styles.pillDot, color: meta.color, ...(meta.spin ? styles.spin : {}) }}>
        {meta.dot}
      </span>
      <span style={{ color: meta.color, fontSize: 11, fontWeight: 600 }}>{label}</span>
    </div>
  );
}

function CapabilityBanner({ status, capabilities, tr }) {
  const activeMode = getActiveAtlasMode(capabilities, 'allen_sdk');
  const modeLabel = atlasModeLabel(activeMode, tr);
  const qiu = getQiuConfig(capabilities);
  const mri = getMriConfig(capabilities);
  if (status === 'online' && capabilities) {
    if (activeMode === 'qiu2018' && !qiu.annotationConfigured) {
      return (
        <div style={{ ...styles.banner, borderColor: '#facc1544' }}>
          <span style={styles.bannerIcon}>!</span>
          <div style={styles.bannerText}>
            <strong style={{ color: '#facc15' }}>{tr('qiuConfigTitle')}</strong>
            <br />
            <span style={{ color: THEME.textSecondary, fontSize: 10 }}>{tr('qiuConfigHint')}</span>
          </div>
        </div>
      );
    }
    if (activeMode === 'mri_ccf_hybrid' && !mri.annotationConfigured) {
      return (
        <div style={{ ...styles.banner, borderColor: '#facc1544' }}>
          <span style={styles.bannerIcon}>!</span>
          <div style={styles.bannerText}>
            <strong style={{ color: '#facc15' }}>{tr('mriConfigTitle')}</strong>
            <br />
            <span style={{ color: THEME.textSecondary, fontSize: 10 }}>{tr('mriConfigHint')}</span>
          </div>
        </div>
      );
    }
    return (
      <div style={{ ...styles.banner, borderColor: '#4ade8044' }}>
        <span style={styles.bannerIcon}>OK</span>
        <div style={styles.bannerText}>
          <strong style={{ color: '#4ade80' }}>{tr('backendActive')}</strong>
          <br />
          <span style={{ color: THEME.textSecondary, fontSize: 10 }}>
            {modeLabel} | {getShape(capabilities)} | {getVoxelSize(capabilities)} um
          </span>
        </div>
      </div>
    );
  }

  if (status === 'offline') {
    return (
      <div style={{ ...styles.banner, borderColor: '#f8717144' }}>
        <span style={styles.bannerIcon}>!</span>
        <div style={styles.bannerText}>
          <strong style={{ color: '#f87171' }}>{tr('backendOffline')}</strong>
          <br />
          <span style={{ color: THEME.textSecondary, fontSize: 10 }}>{tr('fallbackWorker')}</span>
        </div>
      </div>
    );
  }

  return (
    <div style={{ ...styles.banner, borderColor: THEME.border }}>
      <span style={styles.bannerIcon}>i</span>
      <span style={{ color: THEME.textSecondary, fontSize: 11 }}>{tr('backendDescription')}</span>
    </div>
  );
}

function AtlasLoadProgress({ load, tr }) {
  if (!load) return null;
  const title = tr('backendLoadProgressTitle');
  const stepText = load.total > 0 ? ` (${load.step}/${load.total})` : '';
  return (
    <div style={styles.progressCard}>
      <div style={styles.progressHeader}>
        <span>{title}</span>
        <span style={styles.progressPct}>{load.pct.toFixed(0)}%</span>
      </div>
      <div style={styles.progressTrack}>
        <div style={{ ...styles.progressFill, width: `${load.pct}%` }} />
      </div>
      <div style={styles.progressMeta}>
        <div>{tr('backendLoadStageLabel')}: {load.phase || load.state || '-'}{stepText}</div>
        {load.message ? <div>{tr('backendLoadDetailLabel')}: {load.message}</div> : null}
      </div>
    </div>
  );
}

function AdvancedSettings({ capabilities, tr }) {
  const contract = getClinicalContract(capabilities);
  const activeMode = getActiveAtlasMode(capabilities, 'allen_sdk');
  const modeLabel = atlasModeLabel(activeMode, tr);
  const qiu = getQiuConfig(capabilities);
  const mri = getMriConfig(capabilities);
  const regionIndex = getRegionIndexStatus(capabilities);
  const mriLayer = getMriLayerStatus(capabilities);
  const mriReg = getMriRegistrationSummary(capabilities);
  const mriOverlay = getMriOverlayStatus(capabilities);
  const reconstructionQuality = capabilities?.reconstructionQuality && typeof capabilities.reconstructionQuality === 'object'
    ? capabilities.reconstructionQuality
    : {};
  const qualityThresholds = reconstructionQuality?.thresholds && typeof reconstructionQuality.thresholds === 'object'
    ? reconstructionQuality.thresholds
    : {};
  const qualityPassed = reconstructionQuality?.passed;
  const qualityLight = qualityPassed === false
    ? tr('qualityBlocked')
    : qualityPassed === true
      ? tr('qualityPassed')
      : tr('qualityPending');
  const hasRegMetrics = Boolean(
    Number.isFinite(Number(mriReg?.metrics?.dice)) ||
    Number.isFinite(Number(mriReg?.metrics?.hd95Mm)) ||
    Number.isFinite(Number(mriReg?.metrics?.treMm)) ||
    Number.isFinite(Number(mriReg?.metrics?.jacobianAnomalyRatio))
  );
  const riskSourceTag = String(capabilities?.riskModel?.sourceTag || '-');
  const qiuVisible =
    activeMode === 'qiu2018' ||
    qiu.annotationConfigured ||
    qiu.regionMapConfigured ||
    Boolean(qiu.statsPath) ||
    Boolean(qiu.jacobianPath);
  const mriVisible =
    activeMode === 'mri_ccf_hybrid' ||
    mri.annotationConfigured ||
    mri.regionMapConfigured ||
    mri.statsConfigured ||
    mri.jacobianConfigured ||
    mri.skullMaskConfigured ||
    mri.outerTissueConfigured ||
    mri.layeredSdfConfigured;
  const qiuStatus = (v) => (v ? tr('statusConfigured') : tr('statusMissing'));
  return (
    <div style={styles.advanced}>
      <AdvRow label={tr('atlasModeLabel')} value={modeLabel} />
      <AdvRow label={tr('annotationShape')} value={getShape(capabilities)} />
      <AdvRow label={tr('voxelSize')} value={getVoxelSize(capabilities) !== '?' ? `${getVoxelSize(capabilities)} um` : '-'} />
      <AdvRow label={tr('backendVersion')} value={getVersion(capabilities)} />
      <AdvRow label={tr('atlasHashLabel')} value={getAtlasHash(capabilities)} />
      {contract && <AdvRow label={tr('intendedUseSimpleLabel')} value={contract.intendedUse || '-'} />}
      {contract && <AdvRow label={tr('speciesLockLabel')} value={contract.species || '-'} />}
      <div style={styles.qiuBlock}>
        <div style={styles.qiuTitle}>{tr('regionIndexStatusTitle')}</div>
        <AdvRow label={tr('regionIndexModeLabel')} value={regionIndex.mode || '-'} />
        <AdvRow label={tr('regionIndexFullReadyLabel')} value={regionIndex.fullBrainPlannerReady ? tr('qualityPassed') : tr('qualityPending')} />
        <AdvRow label={tr('regionIndexCacheLabel')} value={regionIndex.cacheEnabled === false ? tr('off') : `${regionIndex.cacheSource || '-'}${regionIndex.cacheHit ? ' / hit' : ''}`} />
        <AdvRow label={tr('regionIndexPlannerCountLabel')} value={regionIndex.plannerRegionCount ?? '-'} />
        <AdvRow label={tr('regionIndexCatalogCountLabel')} value={regionIndex.catalogRegionCount ?? '-'} />
        <AdvRow label={tr('regionIndexPresentCountLabel')} value={regionIndex.presentRegionCount ?? '-'} />
        <AdvRow label={tr('regionIndexIndexedCountLabel')} value={regionIndex.indexedRegionCount ?? '-'} />
        <AdvRow label={tr('regionIndexAlignmentLabel')} value={regionIndex.catalogPlannerAligned === false ? tr('qualityBlocked') : tr('qualityPassed')} />
        <AdvRow label={tr('regionIndexCachePathLabel')} value={regionIndex.cachePath || '-'} />
      </div>
      {qiuVisible && (
        <div style={styles.qiuBlock}>
          <div style={styles.qiuTitle}>{tr('qiuConfigTitle')}</div>
          <div style={styles.qiuHint}>{tr('qiuConfigHint')}</div>
          <AdvRow label={tr('qiuAnnotationConfigured')} value={qiuStatus(qiu.annotationConfigured)} />
          <AdvRow label={tr('qiuRegionMapConfigured')} value={qiuStatus(qiu.regionMapConfigured)} />
          <AdvRow label={tr('qiuStatsConfigured')} value={qiuStatus(Boolean(qiu.statsPath))} />
          <AdvRow label={tr('qiuJacobianConfigured')} value={qiuStatus(Boolean(qiu.jacobianPath))} />
          <AdvRow label={tr('qiuAnnotationPathLabel')} value={qiu.annotationPath || '-'} />
          <AdvRow label={tr('qiuRegionMapPathLabel')} value={qiu.regionMapPath || '-'} />
          <AdvRow label={tr('qiuStatsPathLabel')} value={qiu.statsPath || '-'} />
          <AdvRow label={tr('qiuJacobianPathLabel')} value={qiu.jacobianPath || '-'} />
        </div>
      )}
      {mriVisible && (
        <div style={styles.qiuBlock}>
          <div style={styles.qiuTitle}>{tr('mriConfigTitle')}</div>
          <div style={styles.qiuHint}>{tr('mriConfigHint')}</div>
          <AdvRow label={tr('mriDatasetProfileLabel')} value={mri.datasetProfile || 'custom'} />
          <AdvRow label={tr('mriManifestPathLabel')} value={mri.manifestPath || '-'} />
          <AdvRow
            label={tr('mriPathConsistencyLabel')}
            value={mri.pathConsistency?.passed === false ? tr('qualityBlocked') : tr('qualityPassed')}
          />
          {Array.isArray(mri.pathConsistency?.mismatches) && mri.pathConsistency.mismatches.length > 0 && (
            <div style={styles.qiuHint}>
              {tr('mriPathMismatchLabel')}: {mri.pathConsistency.mismatches.slice(0, 3).map((x) => `${x.artifact}/${x.kind}`).join('; ')}
            </div>
          )}
          <AdvRow label={tr('mriAppearanceConfiguredLabel')} value={qiuStatus(mri.useMriAppearance)} />
          <AdvRow label={tr('mriAppearanceActiveLabel')} value={mriLayer.appearanceActive ? tr('on') : tr('off')} />
          <AdvRow label={tr('mriRiskLayerStructuredLabel')} value={mriLayer.riskLayerStructuredMaskEnabled ? tr('on') : tr('off')} />
          <AdvRow label={tr('mriRiskLayerSdfLabel')} value={mriLayer.riskLayerSdfEnabled ? tr('on') : tr('off')} />
          <AdvRow label={tr('mriRiskLayerDistanceModelLabel')} value={mriLayer.riskLayerDistanceModel || '-'} />
          <AdvRow label={tr('mriRiskSourceTagLabel')} value={riskSourceTag} />
          <AdvRow label={tr('mriRegistrationQualityLightLabel')} value={qualityLight} />
          {Array.isArray(reconstructionQuality?.blockingReasons) && reconstructionQuality.blockingReasons.length > 0 && (
            <div style={styles.qiuHint}>
              {tr('mriRegistrationBlockingReasonsLabel')}: {reconstructionQuality.blockingReasons.join('; ')}
            </div>
          )}
          <AdvRow label={tr('mriAnnotationConfigured')} value={qiuStatus(mri.annotationConfigured)} />
          <AdvRow label={tr('mriRegionMapConfigured')} value={qiuStatus(mri.regionMapConfigured)} />
          <AdvRow label={tr('mriStatsConfigured')} value={qiuStatus(mri.statsConfigured)} />
          <AdvRow label={tr('mriJacobianConfigured')} value={qiuStatus(mri.jacobianConfigured)} />
          <AdvRow label={tr('mriSkullMaskConfigured')} value={qiuStatus(mri.skullMaskConfigured)} />
          <AdvRow label={tr('mriOuterTissueConfigured')} value={qiuStatus(mri.outerTissueConfigured)} />
          <AdvRow label={tr('mriLayeredSdfConfigured')} value={qiuStatus(mri.layeredSdfConfigured)} />
          <AdvRow label={tr('mriAnnotationPathLabel')} value={mri.annotationPath || '-'} />
          <AdvRow label={tr('mriRegionMapPathLabel')} value={mri.regionMapPath || '-'} />
          <AdvRow label={tr('mriStatsPathLabel')} value={mri.statsPath || '-'} />
          <AdvRow label={tr('mriJacobianPathLabel')} value={mri.jacobianPath || '-'} />
          <AdvRow label={tr('mriSkullMaskPathLabel')} value={mri.skullMaskPath || '-'} />
          <AdvRow label={tr('mriOuterTissuePathLabel')} value={mri.outerTissueMaskPath || '-'} />
          <AdvRow label={tr('mriLayeredSdfPathLabel')} value={mri.layeredSdfPath || '-'} />
          <div style={styles.qiuTitle}>{tr('mriRegistrationSummaryTitle')}</div>
          <AdvRow label={tr('mriRegistrationConfiguredLabel')} value={qiuStatus(mriReg.configured)} />
          <AdvRow label={tr('mriRegistrationSummaryPathLabel')} value={mriReg.path || '-'} />
          <AdvRow label={tr('mriRegistrationQuickModeLabel')} value={mriReg.quickMode == null ? '-' : (mriReg.quickMode ? tr('on') : tr('off'))} />
          <AdvRow label={tr('mriRegistrationSkipDeformableLabel')} value={mriReg.skipDeformable == null ? '-' : (mriReg.skipDeformable ? tr('on') : tr('off'))} />
          <AdvRow label={tr('mriRegistrationUsedRigidLabel')} value={mriReg.usedRigid == null ? '-' : (mriReg.usedRigid ? tr('on') : tr('off'))} />
          <AdvRow label={tr('mriRegistrationRigidMetricLabel')} value={mriReg.rigidMetric == null ? '-' : Number(mriReg.rigidMetric).toFixed(6)} />
          <AdvRow label={tr('mriRegistrationUsedAffineLabel')} value={mriReg.usedAffine == null ? '-' : (mriReg.usedAffine ? tr('on') : tr('off'))} />
          <AdvRow label={tr('mriRegistrationAffineMetricLabel')} value={mriReg.affineMetric == null ? '-' : Number(mriReg.affineMetric).toFixed(6)} />
          <AdvRow label={tr('mriRegistrationUsedDeformableLabel')} value={mriReg.usedDeformable == null ? '-' : (mriReg.usedDeformable ? tr('on') : tr('off'))} />
          <AdvRow label={tr('mriRegistrationDeformableMetricLabel')} value={mriReg.deformableMetric == null ? '-' : Number(mriReg.deformableMetric).toFixed(6)} />
          <AdvRow
            label={tr('mriRegistrationDiceLabel')}
            value={Number.isFinite(Number(mriReg?.metrics?.dice)) ? Number(mriReg.metrics.dice).toFixed(6) : '-'}
          />
          <AdvRow
            label={tr('mriRegistrationHd95Label')}
            value={Number.isFinite(Number(mriReg?.metrics?.hd95Mm)) ? `${Number(mriReg.metrics.hd95Mm).toFixed(6)} mm` : '-'}
          />
          <AdvRow
            label={tr('mriRegistrationTreLabel')}
            value={Number.isFinite(Number(mriReg?.metrics?.treMm)) ? `${Number(mriReg.metrics.treMm).toFixed(6)} mm` : '-'}
          />
          <AdvRow
            label={tr('mriRegistrationJacobianLabel')}
            value={Number.isFinite(Number(mriReg?.metrics?.jacobianAnomalyRatio)) ? Number(mriReg.metrics.jacobianAnomalyRatio).toFixed(6) : '-'}
          />
          <AdvRow
            label={tr('mriJacobianStdLabel')}
            value={Number.isFinite(Number(mriReg?.jacobianSummary?.std)) ? Number(mriReg.jacobianSummary.std).toFixed(6) : '-'}
          />
          <AdvRow
            label={tr('mriJacobianEvidenceLabel')}
            value={mriReg?.jacobianSummary?.realJacobianEvidence === false ? tr('mriJacobianPlaceholder') : (mriReg?.jacobianSummary?.evidence || '-')}
          />
          <AdvRow
            label={tr('mriRegistrationTransformTypeLabel')}
            value={mriReg?.transform?.type || '-'}
          />
          <AdvRow
            label={tr('mriRegistrationTreCiLabel')}
            value={
              Array.isArray(mriReg?.metricCi95?.treMm)
                ? `${Number(mriReg.metricCi95.treMm[0]).toFixed(3)} - ${Number(mriReg.metricCi95.treMm[1]).toFixed(3)} mm`
                : '-'
            }
          />
          <div style={styles.qiuTitle}>{tr('mriOverlayQcTitle')}</div>
          <AdvRow
            label={tr('mriOverlayModesLabel')}
            value={mriOverlay.overlayModes.length > 0 ? mriOverlay.overlayModes.join(', ') : '-'}
          />
          <AdvRow
            label={tr('mriOverlayMissingLayersLabel')}
            value={mriOverlay.missingLayers.length > 0 ? mriOverlay.missingLayers.join(', ') : tr('none')}
          />
          {Object.entries(mriOverlay.layers || {}).map(([key, layer]) => (
            <AdvRow
              key={`mri-overlay-${key}`}
              label={`${tr('mriOverlayLayerLabel')} ${key}`}
              value={qiuStatus(Boolean(layer?.configured))}
            />
          ))}
          <AdvRow
            label={tr('mriRegistrationThresholdsLabel')}
            value={
              qualityThresholds && Object.keys(qualityThresholds).length > 0
                ? JSON.stringify(qualityThresholds)
                : '-'
            }
          />
          <AdvRow label={tr('mriRegistrationVoxelHeadLabel')} value={mriReg.voxelCountHead ?? '-'} />
          <AdvRow label={tr('mriRegistrationVoxelSkullLabel')} value={mriReg.voxelCountSkull ?? '-'} />
          <AdvRow label={tr('mriRegistrationVoxelOuterLabel')} value={mriReg.voxelCountOuterTissue ?? '-'} />
          {!hasRegMetrics && (
            <div style={styles.qiuHint}>{tr('mriRegistrationMetricsMissingHint')}</div>
          )}
        </div>
      )}
    </div>
  );
}

function AdvRow({ label, value }) {
  return (
    <div style={styles.advRow}>
      <span style={styles.advLabel}>{label}</span>
      <span style={styles.advValue}>{value}</span>
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
    gap: 9,
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: THEME.textPrimary, fontWeight: 600, fontSize: 13 },
  pill: { display: 'flex', alignItems: 'center', gap: 5, border: '1px solid', borderRadius: 20, padding: '2px 9px' },
  pillDot: { fontSize: 12, lineHeight: 1 },
  spin: { display: 'inline-block', animation: 'spin 1s linear infinite' },
  urlRow: { display: 'flex', gap: 6 },
  urlInput: {
    flex: 1,
    background: THEME.inputBg,
    border: `1px solid ${THEME.border}`,
    borderRadius: 5,
    color: THEME.textPrimary,
    fontSize: 11,
    padding: '5px 8px',
    fontFamily: 'monospace',
    outline: 'none',
  },
  connectBtn: { border: 'none', borderRadius: 5, padding: '5px 12px', fontSize: 11, fontWeight: 600 },
  modeRow: { display: 'flex', alignItems: 'center', gap: 8 },
  modeLabel: { fontSize: 10, color: THEME.textSecondary, minWidth: 62 },
  modeSelect: {
    flex: 1,
    background: THEME.inputBg,
    border: `1px solid ${THEME.border}`,
    borderRadius: 5,
    color: THEME.textPrimary,
    fontSize: 11,
    padding: '4px 8px',
    outline: 'none',
  },
  banner: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 8,
    border: '1px solid',
    borderRadius: 6,
    padding: '7px 10px',
    background: '#ffffff04',
  },
  bannerIcon: { fontSize: 12, lineHeight: 1.3, color: THEME.textSecondary, minWidth: 14 },
  bannerText: { fontSize: 11, color: THEME.textPrimary, lineHeight: 1.5 },
  lastSeen: { fontSize: 10, color: THEME.textSecondary, fontFamily: 'monospace' },
  advancedToggle: { fontSize: 10, color: THEME.textSecondary, cursor: 'pointer', userSelect: 'none' },
  advanced: {
    background: THEME.inputBg,
    borderRadius: 5,
    padding: '7px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  qiuBlock: {
    marginTop: 8,
    border: `1px solid ${THEME.border}`,
    borderRadius: 6,
    background: '#09182b',
    padding: '7px 8px',
    display: 'grid',
    gap: 4,
  },
  qiuTitle: { fontSize: 10, color: THEME.accent, fontWeight: 700 },
  qiuHint: { fontSize: 10, color: THEME.textSecondary, lineHeight: 1.35 },
  advRow: { display: 'flex', justifyContent: 'space-between', fontSize: 11 },
  advLabel: { color: THEME.textSecondary },
  advValue: {
    color: THEME.textPrimary,
    fontFamily: 'monospace',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
    textAlign: 'right',
    maxWidth: '64%',
  },
  progressCard: {
    border: `1px solid ${THEME.border}`,
    borderRadius: 6,
    padding: '8px 10px',
    background: '#071325',
    display: 'grid',
    gap: 6,
  },
  progressHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 11,
    color: THEME.textPrimary,
  },
  progressPct: { color: '#67e8f9', fontFamily: 'monospace', fontWeight: 700 },
  progressTrack: {
    width: '100%',
    height: 6,
    borderRadius: 999,
    background: '#0a1a2a',
    overflow: 'hidden',
    border: '1px solid #13324d',
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #00d4ff, #34d399)',
    transition: 'width 0.35s ease',
  },
  progressMeta: {
    display: 'grid',
    gap: 2,
    fontSize: 10,
    color: THEME.textSecondary,
    lineHeight: 1.4,
  },
  errorBox: {
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    background: '#2a0d0d',
    color: '#fecaca',
    fontSize: 11,
    padding: '6px 8px',
    lineHeight: 1.45,
    wordBreak: 'break-word',
  },
};

