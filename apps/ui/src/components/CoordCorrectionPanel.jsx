import React, { useState, useCallback, useMemo } from 'react';
import { THEME } from '../constants/theme';
import { t } from '../constants/i18n';

const ATLAS_BL_DIST_MM = 4.0;
const ATLAS_ML_DIST_MM = 2.0;
const DEFAULT_SIGMA_MM = 0.1;

const toNum = (v) => parseFloat(v);
const valid = (v) => !Number.isNaN(v) && Number.isFinite(v);
const clampPositive = (v, fallback) => (valid(v) && v > 0 ? v : fallback);

function computeCorrection({
  bregmaAP,
  bregmaML,
  bregmaDV,
  lambdaAP,
  lambdaML,
  lambdaDV,
  midlineML,
}) {
  if (!valid(bregmaAP) || !valid(lambdaAP)) return null;

  const dAP = bregmaAP - lambdaAP;
  const dML = valid(lambdaML) ? bregmaML - lambdaML : 0;
  const dDV = valid(lambdaDV) && valid(bregmaDV) ? bregmaDV - lambdaDV : 0;
  const lambdaDist = Math.sqrt(dAP * dAP + dML * dML + dDV * dDV);
  if (lambdaDist < 0.1) return null;

  const apScale = ATLAS_BL_DIST_MM / lambdaDist;

  let mlScale = apScale;
  if (valid(midlineML)) {
    const mlMeasured = Math.abs(midlineML - (valid(bregmaML) ? bregmaML : 0));
    if (mlMeasured > 0.05) mlScale = ATLAS_ML_DIST_MM / mlMeasured;
  }

  const dvScale = (apScale + mlScale) / 2;
  const bAP = valid(bregmaAP) ? bregmaAP : 0;
  const bML = valid(bregmaML) ? bregmaML : 0;
  const bDV = valid(bregmaDV) ? bregmaDV : 0;

  const factors = {
    ap: apScale,
    ml: mlScale,
    dv: dvScale,
    apShift: -bAP * apScale,
    mlShift: -bML * mlScale,
    dvShift: -bDV * dvScale,
  };

  const relErr = clampPositive(DEFAULT_SIGMA_MM / lambdaDist, 0.02);
  const k95 = 1.96;
  const uncertainty = {
    sigmaMm: DEFAULT_SIGMA_MM,
    apScaleCi95: [
      Math.max(0, apScale * (1 - k95 * relErr)),
      apScale * (1 + k95 * relErr),
    ],
    mlScaleCi95: [
      Math.max(0, mlScale * (1 - k95 * relErr)),
      mlScale * (1 + k95 * relErr),
    ],
    dvScaleCi95: [
      Math.max(0, dvScale * (1 - k95 * relErr)),
      dvScale * (1 + k95 * relErr),
    ],
    apShiftCi95: [factors.apShift - k95 * DEFAULT_SIGMA_MM * apScale, factors.apShift + k95 * DEFAULT_SIGMA_MM * apScale],
    mlShiftCi95: [factors.mlShift - k95 * DEFAULT_SIGMA_MM * mlScale, factors.mlShift + k95 * DEFAULT_SIGMA_MM * mlScale],
    dvShiftCi95: [factors.dvShift - k95 * DEFAULT_SIGMA_MM * dvScale, factors.dvShift + k95 * DEFAULT_SIGMA_MM * dvScale],
  };

  return {
    apScale,
    mlScale,
    dvScale,
    mlTiltDeg: Math.atan2(dDV, dML || 1e-9) * (180 / Math.PI),
    apTiltDeg: Math.atan2(dDV, dAP || 1e-9) * (180 / Math.PI),
    bregmaAP: bAP,
    bregmaML: bML,
    lambdaDist,
    factors,
    uncertainty,
  };
}

const EMPTY_FORM = {
  bregmaAP: '',
  bregmaML: '',
  bregmaDV: '',
  lambdaAP: '',
  lambdaML: '',
  lambdaDV: '',
  midlineML: '',
};

export default function CoordCorrectionPanel({ correction, onChange, lang = 'cn' }) {
  const tr = useCallback((k) => t(k, lang), [lang]);

  const [form, setForm] = useState(EMPTY_FORM);
  const [enabled, setEnabled] = useState(!!correction);

  const computed = useMemo(
    () =>
      computeCorrection({
        bregmaAP: toNum(form.bregmaAP),
        bregmaML: toNum(form.bregmaML),
        bregmaDV: toNum(form.bregmaDV),
        lambdaAP: toNum(form.lambdaAP),
        lambdaML: toNum(form.lambdaML),
        lambdaDV: toNum(form.lambdaDV),
        midlineML: toNum(form.midlineML),
      }),
    [form]
  );

  const handleApply = useCallback(() => {
    onChange?.(computed ?? null);
    setEnabled(true);
  }, [computed, onChange]);

  const handleReset = useCallback(() => {
    setForm(EMPTY_FORM);
    setEnabled(false);
    onChange?.(null);
  }, [onChange]);

  const setField = useCallback((field, value) => {
    setForm((f) => ({ ...f, [field]: value }));
  }, []);

  const canApply = computed !== null;

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>{tr('coordCorrPanelTitle')}</span>
        <div style={styles.statusBadge(enabled && correction)}>
          {enabled && correction ? tr('corrActive') : tr('corrInactive')}
        </div>
      </div>

      <div style={styles.desc}>{tr('coordCorrDesc')}</div>

      <div style={styles.grid}>
        <LandmarkGroup
          title={tr('bregmaLabel')}
          color="#60a5fa"
          fields={[
            { key: 'bregmaAP', label: 'AP' },
            { key: 'bregmaML', label: 'ML' },
            { key: 'bregmaDV', label: 'DV', optional: true },
          ]}
          form={form}
          onChange={setField}
          tr={tr}
        />
        <LandmarkGroup
          title={tr('lambdaLabel')}
          color="#f59e0b"
          fields={[
            { key: 'lambdaAP', label: 'AP' },
            { key: 'lambdaML', label: 'ML', optional: true },
            { key: 'lambdaDV', label: 'DV', optional: true },
          ]}
          form={form}
          onChange={setField}
          tr={tr}
        />
      </div>

      <div style={styles.midlineRow}>
        <span style={styles.midlineLabel}>{tr('midlineLabel')}</span>
        <NumInput
          value={form.midlineML}
          onChange={(v) => setField('midlineML', v)}
          placeholder={tr('optional')}
          style={{ width: 80 }}
        />
      </div>

      {computed && <CorrectionPreview c={computed} tr={tr} />}

      <div style={styles.actions}>
        <button
          style={{ ...styles.btn, ...styles.btnApply, opacity: canApply ? 1 : 0.4 }}
          onClick={handleApply}
          disabled={!canApply}
        >
          {tr('applyCorrection')}
        </button>
        <button style={{ ...styles.btn, ...styles.btnReset }} onClick={handleReset}>
          {tr('resetCorrection')}
        </button>
      </div>

      {enabled && correction && (
        <div style={styles.appliedBox}>
          <DetailRow label={tr('apScale')} value={correction.apScale?.toFixed(4)} />
          <DetailRow label={tr('mlTilt')} value={`${correction.mlTiltDeg?.toFixed(2)}${tr('angleUnit')}`} />
          <DetailRow label={tr('apTilt')} value={`${correction.apTiltDeg?.toFixed(2)}${tr('angleUnit')}`} />
          <DetailRow label={tr('blDistLabel')} value={correction.lambdaDist?.toFixed(2)} />
          {correction.uncertainty?.apScaleCi95 && (
            <DetailRow
              label={tr('apScaleCi95Label')}
              value={`${correction.uncertainty.apScaleCi95[0].toFixed(3)} - ${correction.uncertainty.apScaleCi95[1].toFixed(3)}`}
            />
          )}
        </div>
      )}
    </div>
  );
}

function LandmarkGroup({ title, color, fields, form, onChange, tr }) {
  return (
    <div style={styles.lgBox}>
      <div style={{ ...styles.lgTitle, color }}>{title}</div>
      {fields.map(({ key, label, optional }) => (
        <div key={key} style={styles.lgRow}>
          <span style={styles.lgLabel}>
            {label}
            {optional ? '*' : ''}
          </span>
          <NumInput
            value={form[key]}
            onChange={(v) => onChange(key, v)}
            placeholder={optional ? tr('optionalShort') : '0.00'}
          />
        </div>
      ))}
    </div>
  );
}

function NumInput({ value, onChange, placeholder, style = {} }) {
  return (
    <input
      type="number"
      step="0.01"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{ ...styles.numInput, ...style }}
    />
  );
}

function CorrectionPreview({ c, tr }) {
  return (
    <div style={styles.preview}>
      <div style={styles.previewTitle}>- {tr('previewTitle')} -</div>
      <DetailRow
        label={tr('apScale')}
        value={c.apScale.toFixed(4)}
        highlight={Math.abs(c.apScale - 1) > 0.05}
      />
      <DetailRow
        label={tr('mlTilt')}
        value={`${c.mlTiltDeg.toFixed(2)}${tr('angleUnit')}`}
        highlight={Math.abs(c.mlTiltDeg) > 3}
      />
      <DetailRow
        label={tr('apTilt')}
        value={`${c.apTiltDeg.toFixed(2)}${tr('angleUnit')}`}
        highlight={Math.abs(c.apTiltDeg) > 3}
      />
      <DetailRow label={tr('blDistLabel')} value={c.lambdaDist.toFixed(2)} />
      <DetailRow
        label={tr('apScaleCi95Label')}
        value={`${c.uncertainty.apScaleCi95[0].toFixed(3)} - ${c.uncertainty.apScaleCi95[1].toFixed(3)}`}
      />
    </div>
  );
}

function DetailRow({ label, value, highlight = false }) {
  return (
    <div style={styles.detailRow}>
      <span style={styles.detailLabel}>{label}</span>
      <span style={{ ...styles.detailValue, color: highlight ? '#facc15' : THEME.textPrimary }}>
        {value}
      </span>
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
  },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: THEME.textPrimary, fontWeight: 600, fontSize: 13 },
  statusBadge: (active) => ({
    fontSize: 10,
    fontWeight: 600,
    color: active ? '#4ade80' : THEME.textSecondary,
    background: active ? '#4ade8022' : THEME.inputBg,
    borderRadius: 10,
    padding: '2px 8px',
    border: `1px solid ${active ? '#4ade8044' : THEME.border}`,
  }),
  desc: { fontSize: 11, color: THEME.textSecondary, lineHeight: 1.5 },
  grid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 },
  lgBox: {
    background: THEME.inputBg,
    borderRadius: 6,
    padding: '8px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 5,
  },
  lgTitle: { fontSize: 11, fontWeight: 700, marginBottom: 2 },
  lgRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 },
  lgLabel: { fontSize: 11, color: THEME.textSecondary, width: 22 },
  numInput: {
    background: THEME.panelBg,
    border: `1px solid ${THEME.border}`,
    borderRadius: 4,
    color: THEME.textPrimary,
    fontSize: 11,
    padding: '3px 6px',
    width: 72,
    textAlign: 'right',
    outline: 'none',
  },
  midlineRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 },
  midlineLabel: { color: THEME.textSecondary },
  preview: {
    background: THEME.inputBg,
    border: `1px dashed ${THEME.accent}55`,
    borderRadius: 6,
    padding: '7px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  previewTitle: {
    fontSize: 10,
    color: THEME.textSecondary,
    textAlign: 'center',
    marginBottom: 2,
  },
  detailRow: { display: 'flex', justifyContent: 'space-between', fontSize: 11 },
  detailLabel: { color: THEME.textSecondary },
  detailValue: { fontFamily: 'monospace' },
  actions: { display: 'flex', gap: 8 },
  btn: {
    border: 'none',
    borderRadius: 5,
    padding: '5px 12px',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
  },
  btnApply: { background: THEME.accent + '22', color: THEME.accent },
  btnReset: { background: '#f8717122', color: '#f87171' },
  appliedBox: {
    background: '#4ade8010',
    border: '1px solid #4ade8033',
    borderRadius: 6,
    padding: '7px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
};
