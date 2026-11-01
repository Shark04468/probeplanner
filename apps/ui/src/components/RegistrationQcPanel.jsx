import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { t } from '../constants/i18n';
import { measurementView, registrationGateState } from './registrationEvidence';

const C = {
  bg: '#061121',
  panel: '#08182a',
  panel2: '#0b2035',
  border: '#183653',
  border2: '#25527a',
  text: '#d7eefb',
  sub: '#8cc8e6',
  dim: '#5c9abd',
  accent: '#00d4ff',
  good: '#34d399',
  warn: '#facc15',
  bad: '#fb7185',
};

function normalizeBase(base) {
  return String(base || '').trim().replace(/\/+$/, '');
}

function fmt(value, suffix = '', digits = 3) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  if (Number.isFinite(n)) return `${n.toFixed(digits)}${suffix}`;
  return String(value);
}

function fmtBool(value, tr) {
  if (value === true) return tr('on');
  if (value === false) return tr('off');
  return '-';
}

function statusColor(value) {
  const s = String(value || '').toLowerCase();
  if (s === 'pass' || s === 'passed' || value === true) return C.good;
  if (s === 'recorded') return C.sub;
  if (s === 'assumed' || s === 'warn' || s === 'unknown' || value === null || value === undefined) return C.warn;
  return C.bad;
}

function gateColor(value) {
  if (value === true) return C.good;
  if (value === false) return C.bad;
  return C.warn;
}

function downloadText(filename, text, mimeType) {
  if (typeof window === 'undefined') return;
  const blob = new Blob([text], { type: mimeType });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

function Pill({ children, color = C.accent }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      border: `1px solid ${color}66`,
      background: `${color}18`,
      color,
      borderRadius: 999,
      padding: '3px 9px',
      fontSize: 11,
      fontWeight: 800,
    }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color }} />
      {children}
    </span>
  );
}

function Card({ title, children }) {
  return (
    <section style={{
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      background: C.panel,
      padding: 14,
      minWidth: 0,
    }}>
      <div style={{
        color: C.text,
        fontSize: 14,
        fontWeight: 900,
        letterSpacing: '.04em',
        marginBottom: 10,
      }}>
        {title}
      </div>
      {children}
    </section>
  );
}

function MetricGrid({ rows }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
      gap: 10,
    }}>
      {rows.map((row) => (
        <div key={row.label} style={{
          border: `1px solid ${C.border}`,
          background: C.panel2,
          borderRadius: 10,
          padding: 10,
          minHeight: 58,
        }}>
          <div style={{ color: C.dim, fontSize: 11, marginBottom: 5 }}>{row.label}</div>
          <div style={{ color: row.color || C.text, fontSize: 17, fontWeight: 900 }}>
            {row.value}
          </div>
          {row.note && <div style={{ color: C.dim, fontSize: 10, marginTop: 4 }}>{row.note}</div>}
        </div>
      ))}
    </div>
  );
}

function Row({ label, value, color, mono = false }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '180px minmax(0, 1fr)',
      gap: 12,
      alignItems: 'start',
      borderBottom: `1px solid ${C.border}66`,
      padding: '7px 0',
    }}>
      <div style={{ color: C.dim, fontSize: 12 }}>{label}</div>
      <div style={{
        color: color || C.text,
        fontSize: 12,
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Consolas, monospace' : undefined,
        wordBreak: 'break-all',
      }}>
        {value ?? '-'}
      </div>
    </div>
  );
}

export default function RegistrationQcPanel({
  baseUrl = 'http://localhost:5000',
  authToken = '',
  backendStatus = 'idle',
  lang = 'cn',
}) {
  const tr = useCallback((key) => t(key, lang), [lang]);
  const [qc, setQc] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState('');
  const backendOnline = backendStatus === 'online';
  const cleanBase = useMemo(() => normalizeBase(baseUrl) || 'http://localhost:5000', [baseUrl]);

  const headers = useMemo(() => {
    const out = { 'Content-Type': 'application/json' };
    if (authToken) out.Authorization = `Bearer ${authToken}`;
    return out;
  }, [authToken]);

  const fetchQc = useCallback(async () => {
    if (!backendOnline) {
      setError(tr('registrationQcBackendOffline'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${cleanBase}/api/mri/registration/qc`, { headers });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
      setQc(data);
    } catch (err) {
      setError(err?.message || tr('registrationQcNoData'));
    } finally {
      setLoading(false);
    }
  }, [backendOnline, cleanBase, headers, tr]);

  useEffect(() => {
    fetchQc();
  }, [fetchQc]);

  const copyJson = useCallback(async () => {
    if (!qc) return;
    const text = JSON.stringify(qc, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setToast(tr('registrationQcCopied'));
    } catch {
      downloadText('registration_qc.json', text, 'application/json;charset=utf-8');
      setToast(tr('registrationQcExported'));
    }
  }, [qc, tr]);

  const exportCsv = useCallback(async () => {
    if (!backendOnline) return;
    try {
      const res = await fetch(`${cleanBase}/api/mri/registration/qc/export?format=csv`, { headers });
      const text = await res.text();
      if (!res.ok) throw new Error(text || `HTTP ${res.status}`);
      downloadText('registration_qc.csv', text, 'text/csv;charset=utf-8');
      setToast(tr('registrationQcExported'));
    } catch (err) {
      setError(err?.message || tr('registrationQcNoData'));
    }
  }, [backendOnline, cleanBase, headers, tr]);

  const metrics = measurementView(qc);
  const transform = qc?.transform || {};
  const checks = qc?.checks || {};
  const overlay = qc?.overlay || {};
  const gate = qc?.qualityGate || {};
  const gateState = registrationGateState(gate);
  const gatePassed = gateState === 'incomplete' ? null : gateState === 'passed';
  const gateLabel = gatePassed === true
    ? tr('registrationQcPassed')
    : gatePassed === false
      ? tr('registrationQcBlocked')
      : tr('registrationQcIncomplete');

  const metricRows = [
    { label: tr('mriRegistrationDiceLabel'), value: fmt(metrics.dice, '', 3), color: metrics.dice !== null && metrics.dice >= 0.8 ? C.good : C.warn },
    { label: tr('mriRegistrationHd95Label'), value: fmt(metrics.hd95Mm, ' mm', 3), color: metrics.hd95Mm !== null && metrics.hd95Mm <= 0.45 ? C.good : C.warn },
    { label: tr('registrationQcTreMean'), value: fmt(metrics.treMeanMm, ' mm', 3), color: metrics.treMeanMm !== null && metrics.treMeanMm <= 0.3 ? C.good : C.warn },
    { label: tr('registrationQcTreMedian'), value: fmt(metrics.treMedianMm, ' mm', 3) },
    { label: tr('registrationQcTreMax'), value: fmt(metrics.treMaxMm, ' mm', 3) },
    { label: tr('mriRegistrationTreCiLabel'), value: Array.isArray(metrics.treCi95Mm) ? `${fmt(metrics.treCi95Mm[0], ' mm')} - ${fmt(metrics.treCi95Mm[1], ' mm')}` : '-' },
    { label: tr('registrationQcTreAxis'), value: `AP ${fmt(metrics.treAxisMm?.AP, ' mm')} / ML ${fmt(metrics.treAxisMm?.ML, ' mm')} / DV ${fmt(metrics.treAxisMm?.DV, ' mm')}` },
    { label: tr('registrationQcJacobianMean'), value: fmt(metrics.jacobianMean, '', 3) },
    { label: tr('registrationQcJacobianStd'), value: fmt(metrics.jacobianStd, '', 3), color: metrics.jacobianStd === 0 ? C.warn : C.text },
    { label: tr('registrationQcJacobianMin'), value: fmt(metrics.jacobianMin, '', 3) },
    { label: tr('registrationQcJacobianMax'), value: fmt(metrics.jacobianMax, '', 3) },
    { label: tr('registrationQcJacobianAnomaly'), value: fmt(metrics.jacobianAnomalyRatio, '', 4), color: metrics.jacobianAnomalyRatio === null ? C.warn : metrics.jacobianAnomalyRatio <= 0.03 ? C.good : C.bad },
  ];

  const overlayLabel = {
    orthogonalOverlay: tr('registrationQcOrthogonal'),
    checkerboardOverlay: tr('registrationQcCheckerboard'),
    ccfBoundaryOverlay: tr('registrationQcCcfBoundary'),
    mriBoundaryOverlay: tr('registrationQcMriBoundary'),
    skullBoundaryOverlay: tr('registrationQcSkullBoundary'),
    outerBoundaryOverlay: tr('registrationQcOuterBoundary'),
    jacobianHeatmap: tr('registrationQcJacobianHeatmap'),
  };
  const previewImages = [
    { key: 'boundary-coronal', label: `${tr('registrationQcCcfBoundary')} / AP`, mode: 'boundary', axis: 'coronal' },
    { key: 'boundary-sagittal', label: `${tr('registrationQcCcfBoundary')} / ML`, mode: 'boundary', axis: 'sagittal' },
    { key: 'boundary-horizontal', label: `${tr('registrationQcCcfBoundary')} / DV`, mode: 'boundary', axis: 'horizontal' },
    { key: 'checkerboard-coronal', label: tr('registrationQcCheckerboard'), mode: 'checkerboard', axis: 'coronal' },
    { key: 'jacobian-coronal', label: tr('registrationQcJacobianHeatmap'), mode: 'jacobian', axis: 'coronal' },
  ];

  return (
    <div style={{ padding: 16, display: 'grid', gap: 14 }}>
      <header style={{
        display: 'flex',
        justifyContent: 'space-between',
        gap: 12,
        alignItems: 'flex-start',
        border: `1px solid ${C.border}`,
        borderRadius: 14,
        background: '#071426',
        padding: 16,
      }}>
        <div>
          <div style={{ color: C.text, fontSize: 20, fontWeight: 950 }}>{tr('registrationQcTitle')}</div>
          <div style={{ color: C.sub, fontSize: 12, marginTop: 6 }}>{tr('registrationQcSubtitle')}</div>
          {qc?.profileId && (
            <div style={{ color: C.dim, fontSize: 11, marginTop: 8, fontFamily: 'ui-monospace, Consolas, monospace' }}>
              {qc.profileId}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Pill color={gateColor(gatePassed)}>{gateLabel}</Pill>
          <button style={styles.btn} onClick={fetchQc} disabled={loading}>{loading ? tr('running') : tr('registrationQcRefresh')}</button>
          <button style={styles.btn} onClick={copyJson} disabled={!qc}>{tr('registrationQcCopyJson')}</button>
          <button style={styles.primaryBtn} onClick={exportCsv} disabled={!qc || !backendOnline}>{tr('registrationQcExportCsv')}</button>
        </div>
      </header>

      {toast && <div style={styles.toast}>{toast}</div>}
      {error && <div style={styles.error}>{error}</div>}
      {!qc && !error && <div style={styles.empty}>{tr('registrationQcNoData')}</div>}

      {qc && (
        <>
          <Card title={tr('registrationQcMetrics')}>
            <MetricGrid rows={metricRows} />
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
            <Card title={tr('registrationQcQualityGate')}>
              <Row label={tr('registrationQcQualityGate')} value={gateLabel} color={gateColor(gatePassed)} />
              <Row label={tr('mriRegistrationThresholdsLabel')} value={JSON.stringify(gate.thresholds || {})} mono />
              {(gate.blockingReasons || []).length > 0 && (
                <div style={styles.blockedHint}>
                  <div style={{ color: C.bad, fontWeight: 900, marginBottom: 6 }}>{tr('registrationQcBlockingReasons')}</div>
                  {(gate.blockingReasons || []).map((reason, idx) => (
                    <div key={`${reason}-${idx}`} style={{ color: C.text, fontSize: 12, marginTop: 4 }}>{reason}</div>
                  ))}
                  <div style={{ color: C.sub, fontSize: 12, marginTop: 8 }}>{tr('registrationQcQualityBlockedHint')}</div>
                </div>
              )}
            </Card>

            <Card title={tr('registrationQcTransform')}>
              <Row label={tr('registrationQcRigid')} value={fmtBool(transform.rigid, tr)} color={transform.rigid ? C.good : C.warn} />
              <Row label={tr('registrationQcAffine')} value={fmtBool(transform.affine, tr)} color={transform.affine ? C.good : C.warn} />
              <Row label={tr('registrationQcSyn')} value={fmtBool(transform.synDeformable, tr)} color={transform.synDeformable ? C.good : C.warn} />
              <Row label={tr('registrationQcSkipDeformable')} value={fmtBool(transform.skipDeformable, tr)} color={transform.skipDeformable ? C.warn : C.good} />
              <Row label={tr('mriRegistrationTransformTypeLabel')} value={transform.type || '-'} />
            </Card>
          </div>

          <Card title={tr('registrationQcChecks')}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
              {[
                ['fixedMovingDirection', tr('registrationQcFixedMoving')],
                ['spacing', tr('registrationQcSpacing')],
                ['interpolationPolicy', tr('registrationQcInterpolation')],
              ].map(([key, label]) => {
                const row = checks[key] || {};
                const color = statusColor(row.status);
                const labelKey = row.status === 'recorded'
                  ? 'registrationQcRecorded'
                  : row.status === 'assumed'
                  ? 'registrationQcAssumed'
                  : row.status === 'unknown' || row.passed == null
                    ? 'registrationQcUnknown'
                    : row.passed
                      ? 'registrationQcPassed'
                      : 'registrationQcBlocked';
                return (
                  <div key={key} style={styles.checkCard}>
                    <Pill color={color}>{tr(labelKey)}</Pill>
                    <div style={{ color: C.text, fontWeight: 900, marginTop: 10 }}>{label}</div>
                    <div style={{ color: C.sub, fontSize: 12, marginTop: 6, wordBreak: 'break-word' }}>{row.message || '-'}</div>
                    <div style={{ color: C.dim, fontSize: 11, marginTop: 6 }}>{row.required || '-'}</div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title={tr('registrationQcOverlays')}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
              {Object.entries(overlayLabel).map(([key, label]) => {
                const row = overlay.layers?.[key] || {};
                const configured = Boolean(row.configured);
                return (
                  <div key={key} style={styles.overlayCard}>
                    <Pill color={configured ? C.good : C.warn}>
                      {configured ? tr('registrationQcConfigured') : tr('registrationQcMissing')}
                    </Pill>
                    <div style={{ color: C.text, fontWeight: 900, marginTop: 10 }}>{label}</div>
                    <div style={{ color: C.dim, fontSize: 11, marginTop: 6, wordBreak: 'break-all' }}>
                      {row.path || row.source || '-'}
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{ color: C.text, fontSize: 13, fontWeight: 900, margin: '14px 0 8px' }}>
              {tr('registrationQcOverlayPreviews')}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
              {previewImages.map((img) => {
                const src = `${cleanBase}/api/mri/registration/qc/overlay_image?mode=${encodeURIComponent(img.mode)}&axis=${encodeURIComponent(img.axis)}&r=${encodeURIComponent(qc.requestId || '')}`;
                return (
                  <div key={img.key} style={styles.previewCard}>
                    <div style={{ color: C.sub, fontSize: 12, fontWeight: 800, marginBottom: 8 }}>{img.label}</div>
                    <img
                      src={src}
                      alt={img.label}
                      style={{ width: '100%', height: 170, objectFit: 'contain', background: '#020916', borderRadius: 8, border: `1px solid ${C.border}` }}
                    />
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title={tr('registrationQcPaths')}>
            <Row label={tr('mriManifestPathLabel')} value={qc.manifestPath || '-'} mono />
            <Row label={tr('mriStatsPathLabel')} value={qc.statsPath || '-'} mono />
            <Row label={tr('mriRegistrationSummaryPathLabel')} value={qc.registrationSummaryPath || '-'} mono />
            <Row label={tr('mriPathConsistencyLabel')} value={qc.pathConsistency?.passed === true ? tr('allValid') : qc.pathConsistency?.passed === false ? tr('mriPathMismatchLabel') : tr('registrationQcUnknown')} color={qc.pathConsistency?.passed === true ? C.good : C.warn} />
          </Card>
        </>
      )}
    </div>
  );
}

const styles = {
  btn: {
    border: `1px solid ${C.border2}`,
    background: '#081a2d',
    color: C.sub,
    borderRadius: 9,
    padding: '8px 11px',
    fontWeight: 800,
    cursor: 'pointer',
  },
  primaryBtn: {
    border: `1px solid ${C.accent}88`,
    background: `${C.accent}18`,
    color: C.accent,
    borderRadius: 9,
    padding: '8px 11px',
    fontWeight: 900,
    cursor: 'pointer',
  },
  error: {
    border: `1px solid ${C.bad}66`,
    background: `${C.bad}18`,
    color: C.text,
    borderRadius: 12,
    padding: 12,
  },
  empty: {
    border: `1px solid ${C.border}`,
    background: C.panel,
    color: C.sub,
    borderRadius: 12,
    padding: 16,
  },
  toast: {
    justifySelf: 'start',
    border: `1px solid ${C.good}66`,
    background: `${C.good}16`,
    color: C.good,
    borderRadius: 999,
    padding: '6px 12px',
    fontWeight: 800,
  },
  blockedHint: {
    border: `1px solid ${C.bad}66`,
    background: `${C.bad}12`,
    borderRadius: 10,
    padding: 10,
    marginTop: 10,
  },
  checkCard: {
    border: `1px solid ${C.border}`,
    background: C.panel2,
    borderRadius: 10,
    padding: 11,
    minHeight: 120,
  },
  overlayCard: {
    border: `1px solid ${C.border}`,
    background: C.panel2,
    borderRadius: 10,
    padding: 11,
    minHeight: 110,
  },
  previewCard: {
    border: `1px solid ${C.border}`,
    background: '#061426',
    borderRadius: 10,
    padding: 10,
  },
};
