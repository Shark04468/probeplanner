import React, { useState, useRef, useCallback } from 'react';
import { flaskFetch } from '../hooks/useBackend';
import { t } from '../constants/i18n';

const C = {
  bg: '#060e1c',
  panel: '#08121f',
  panel2: '#0a1828',
  border: '#162d48',
  text: '#d8eefa',
  textSub: '#90c8e4',
  textDim: '#5a9ab8',
  textMuted: '#3a6888',
  accent: '#00d4ff',
  success: '#34d399',
  warn: '#facc15',
  danger: '#f87171',
};

const LIMITS = {
  ap: [-10, 10],
  ml: [-10, 10],
  dv: [-15, 15],
  theta: [0, 90],
  depth: [0.1, 20],
};

const PINPOINT_COORDINATES = 'pinpoint_ap_posterior_positive_mm';
const INTERNAL_COORDINATES = 'probeplanner_ap_anterior_positive_mm';

function recordedHardware(probe) {
  const result = {};
  const fields = {
    shankCount: ['shankCount', 'shanks', 'nShanks'],
    shankPitchUm: ['shankPitchUm', 'shankSpacingUm'],
    radiusUm: ['radiusUm', 'probeRadiusUm'],
    shankWidthUm: ['shankWidthUm'],
    shankThicknessUm: ['shankThicknessUm'],
  };
  for (const [key, aliases] of Object.entries(fields)) {
    const values = aliases.filter(alias => probe[alias] != null).map(alias => {
      const raw = probe[alias];
      if (typeof raw === 'boolean' || String(raw).trim() === '') throw new Error(`Invalid ${key}`);
      return Number(raw);
    });
    if (!values.length) continue;
    const value = values[0];
    if (!Number.isFinite(value) || value < 0 || values.some(v => v !== value)
      || (key === 'shankCount' && (!Number.isInteger(value) || value < 1 || value > 64))) {
      throw new Error(`Invalid or inconsistent ${key}`);
    }
    result[key] = value;
  }
  return result;
}

function assertInRange(label, value, [min, max]) {
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${label} out of range [${min}, ${max}]`);
  }
}

function normalizeProbe(p, i, posteriorPositive = false) {
  const ap = (posteriorPositive ? -1 : 1) * Number(p.ap ?? p.ap_mm ?? 0);
  const ml = Number(p.ml ?? p.ml_mm ?? 0);
  const dv = Number(p.dv ?? p.entryDvCorr_mm ?? 0);
  const phi = Number(p.phi ?? p.phi_deg ?? 0);
  const theta = Number(p.theta ?? p.theta_deg ?? 0);
  const depth = Number(p.depth ?? (p.depthUm != null ? p.depthUm / 1000 : 4));
  const spin = Number(p.spin ?? p.spin_deg ?? 0);

  assertInRange('ap', ap, LIMITS.ap);
  assertInRange('ml', ml, LIMITS.ml);
  assertInRange('dv', dv, LIMITS.dv);
  assertInRange('theta', theta, LIMITS.theta);
  assertInRange('depth', depth, LIMITS.depth);
  if (!Number.isFinite(phi) || !Number.isFinite(spin)) throw new Error('Non-finite probe angle');

  return {
    ap_mm: ap,
    ml_mm: ml,
    entryDvCorr_mm: dv,
    phi_deg: ((phi % 360) + 360) % 360,
    theta_deg: theta,
    spin_deg: spin,
    depthUm: depth * 1000,
    probeType: p.probeType ?? p.type ?? null,
    ...recordedHardware(p),
    coveredRegions: p.coveredRegions || [],
    _id: p.id || p._id || `Probe${i}`,
  };
}

export function exportPinpointJSON(probes, probePreset = null) {
  const data = {
    version: '1.0',
    generator: 'probe-planner',
    _coordinateSystem: PINPOINT_COORDINATES,
    timestamp: new Date().toISOString(),
    probes: probes.map((p, i) => ({
      id: `Probe${i}`,
      ap: -(p.ap_mm_corrected ?? p.ap_mm),
      ml: p.ml_mm_corrected ?? p.ml_mm,
      dv: p.entryDvCorr_mm_corrected ?? p.entryDvCorr_mm,
      phi: p.phi_deg,
      theta: p.theta_deg,
      spin: p.spin_deg ?? 0,
      depth: (p.depthUm_corrected ?? p.depthUm) / 1000,
      type: p.probeType || probePreset,
      ...recordedHardware(p),
      coveredRegions: p.coveredRegions || [],
    })),
  };
  return JSON.stringify(data, null, 2);
}

export function exportPinpointTXT(probes) {
  const lines = [
    `# coordinate_system: ${PINPOINT_COORDINATES}`,
    '# id  ap(mm)  ml(mm)  dv(mm)  phi(deg)  theta(deg)  spin(deg)  depth(mm)',
  ];
  for (let i = 0; i < probes.length; i += 1) {
    const p = probes[i];
    // Comment metadata keeps the legacy eight-column geometry readable.
    lines.push(`# probe_metadata: ${JSON.stringify({ id: `Probe${i}`, probeType: p.probeType ?? null,
      ...recordedHardware(p), coveredRegions: p.coveredRegions || [] })}`);
    lines.push([
      `Probe${i}`,
      -(p.ap_mm_corrected ?? p.ap_mm),
      p.ml_mm_corrected ?? p.ml_mm,
      p.entryDvCorr_mm_corrected ?? p.entryDvCorr_mm,
      p.phi_deg,
      p.theta_deg,
      p.spin_deg ?? 0,
      (p.depthUm_corrected ?? p.depthUm) / 1000,
    ].join('\t'));
  }
  return lines.join('\n');
}

export function parsePinpoint(text) {
  const trimmed = text.trim();
  if (!trimmed) return [];

  if (trimmed.startsWith('{')) {
    const obj = JSON.parse(trimmed);
    if (!Array.isArray(obj.probes)) {
      throw new Error('Missing probes[] in JSON');
    }
    const convention = obj._coordinateSystem || obj.coordinateSystem;
    let posteriorPositive;
    if (convention === PINPOINT_COORDINATES) posteriorPositive = true;
    else if (convention === INTERNAL_COORDINATES) posteriorPositive = false;
    else if (convention) throw new Error('Unsupported coordinate system');
    else if (obj._software === 'ProbeplannerGUI / optimizer_server') posteriorPositive = true;
    else if (obj.generator === 'probe-planner' || obj.probes.every(p => p.ap_mm != null)) posteriorPositive = false;
    else throw new Error('Missing coordinate system: AP sign cannot be inferred');
    return obj.probes.map((p, i) => normalizeProbe(p, i, posteriorPositive));
  }

  const marker = trimmed.match(/^# coordinate_system:\s*(\S+)\s*$/m)?.[1];
  if (marker && marker !== PINPOINT_COORDINATES && marker !== INTERNAL_COORDINATES) {
    throw new Error('Unsupported coordinate system');
  }

  const metadata = new Map();
  for (const line of trimmed.split('\n')) {
    const match = line.trim().match(/^# probe_metadata:\s*(.+)$/);
    if (!match) continue;
    const entry = JSON.parse(match[1]);
    if (!entry || typeof entry.id !== 'string' || metadata.has(entry.id)) throw new Error('Invalid or duplicate probe metadata');
    metadata.set(entry.id, entry);
  }
  const rows = trimmed
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'));

  const seen = new Set();
  const parsed = rows.map((row, i) => {
    const cols = row.split(/\s+/);
    if (cols.length < 8) {
      throw new Error(`Line ${i + 1} has insufficient columns`);
    }
    if (seen.has(cols[0])) throw new Error('Duplicate probe id');
    seen.add(cols[0]);
    return normalizeProbe(
      {
        ...metadata.get(cols[0]),
        id: cols[0],
        ap: Number(cols[1]),
        ml: Number(cols[2]),
        dv: Number(cols[3]),
        phi: Number(cols[4]),
        theta: Number(cols[5]),
        spin: Number(cols[6]),
        depth: Number(cols[7]),
      },
      i,
      marker === PINPOINT_COORDINATES
    );
  });
  if ([...metadata.keys()].some(id => !seen.has(id))) throw new Error('Metadata has no matching probe');
  return parsed;
}

export function toBackendTrajectory(p) {
  return {
    ap_mm: p.ap_mm ?? p.ap ?? 0,
    ml_mm: p.ml_mm ?? p.ml ?? 0,
    entryDvCorr_mm: p.entryDvCorr_mm ?? p.dvEntry ?? 0,
    ...Object.fromEntries(['ap_mm_corrected', 'ml_mm_corrected', 'entryDvCorr_mm_corrected', 'depthUm_corrected']
      .filter(key => p[key] != null).map(key => [key, p[key]])),
    phi_deg: p.azimuth ?? p.phi_deg ?? 0,
    theta_deg: p.theta_deg ?? (p.elevation != null ? 90 - p.elevation : 0),
    spin_deg: p.spin_deg ?? p.spin ?? 0,
    probeType: p.probeType ?? p.type ?? null,
    ...recordedHardware(p),
    skullTiltDeg: p.skullTiltDeg ?? 0,
    depthUm: p.depthUm ?? ((p.depth ?? 4) * 1000),
    coveredRegions: p.regionsHit ?? p.coveredRegions ?? [],
  };
}

export default function PinpointIO({
  results = null,
  probePreset = 'NP2.0-4sh',
  correction = null,
  correctionUncertainty = null,
  onImport = null,
  lang = 'cn',
}) {
  const tr = useCallback((key) => t(key, lang), [lang]);
  const [tab, setTab] = useState('export');
  const [format, setFormat] = useState('json');
  const [importText, setImportText] = useState('');
  const [importErr, setImportErr] = useState('');
  const [importOk, setImportOk] = useState('');
  const [exportErr, setExportErr] = useState('');
  const [exportOk, setExportOk] = useState('');
  const fileRef = useRef(null);

  const probes = results?.probes || [];

  const handleExport = async () => {
    if (!probes.length) return;
    setExportErr('');
    setExportOk('');

    try {
      const trajectories = (results?.selected || probes).map(toBackendTrajectory);
      const resp = await flaskFetch('/api/export_pinpoint', 'POST', {
        trajectories,
        probeType: probePreset,
        correctionFactors: results?.correctionFactors || correction || {},
        correctionUncertainty: results?.correctionUncertainty || correctionUncertainty || null,
        planRequestId: results?.requestId || null,
        planResponseHash: results?.responseHash || null,
      });
      if (!resp.ok) {
        throw new Error(resp.error || 'export failed');
      }

      const backendProbes = parsePinpoint(JSON.stringify(resp.pinpointData));

      const content = format === 'json'
        ? JSON.stringify(resp.pinpointData, null, 2)
        : exportPinpointTXT(backendProbes);
      const ext = format === 'json' ? 'pplan' : 'txt';
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `probe_plan_${Date.now()}.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
      setExportOk(
        tr('pinpointExportedTemplate')
          .replace('{count}', String(backendProbes.length))
          .replace('{id}', String(resp.requestId || 'no-id'))
      );
    } catch (e) {
      setExportErr(e.message || String(e));
    }
  };

  const handleImportText = () => {
    setImportErr('');
    setImportOk('');
    try {
      const parsed = parsePinpoint(importText);
      if (!parsed.length) throw new Error('No probes parsed');
      onImport?.(parsed);
      setImportOk(
        tr('pinpointImportedTemplate').replace('{count}', String(parsed.length))
      );
      setImportText('');
    } catch (e) {
      setImportErr(`Import failed: ${e.message}`);
    }
  };

  const handleFileLoad = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setImportText(ev.target.result);
    reader.readAsText(file);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%' }}>
      <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: `1px solid ${C.border}` }}>
        {[
          { id: 'export', label: tr('pinpointTabExport') },
          { id: 'import', label: tr('pinpointTabImport') },
        ].map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              flex: 1,
              padding: '10px 8px',
              background: tab === id ? C.accent + '18' : C.panel,
              border: 'none',
              borderBottom: tab === id ? `2px solid ${C.accent}` : '2px solid transparent',
              color: tab === id ? C.accent : C.textDim,
              cursor: 'pointer',
              fontWeight: 700,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'export' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
          <div style={S.row}>
            <span style={S.label}>{tr('pinpointFormatLabel')}</span>
            <div style={{ display: 'flex', gap: 6 }}>
              {[
                { id: 'json', label: '.pplan (JSON)' },
                { id: 'txt', label: '.txt' },
              ].map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setFormat(id)}
                  style={{
                    ...S.chipBtn,
                    background: format === id ? C.accent + '22' : C.panel2,
                    color: format === id ? C.accent : C.textDim,
                    border: `1px solid ${format === id ? C.accent + '55' : C.border}`,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div style={S.infoBox}>
            <span style={{ color: C.textDim }}>{tr('pinpointProbeCount')}</span>
            <span style={{ color: probes.length ? C.accent : C.warn, fontWeight: 700 }}>
              {probes.length}
            </span>
          </div>

          {exportErr && <div style={{ ...S.alertBox, background: C.danger + '12', borderColor: C.danger + '55', color: C.danger }}>{tr('pinpointErrorPrefix')}: {exportErr}</div>}
          {exportOk && <div style={{ ...S.alertBox, background: C.success + '12', borderColor: C.success + '55', color: C.success }}>{exportOk}</div>}

          <button
            onClick={handleExport}
            disabled={!probes.length}
            style={{
              ...S.actionBtn,
              background: probes.length ? C.accent + '20' : C.panel2,
              color: probes.length ? C.accent : C.textMuted,
              borderColor: probes.length ? C.accent + '55' : C.border,
              cursor: probes.length ? 'pointer' : 'not-allowed',
            }}
          >
            {tr('pinpointDownloadLabel')} {format === 'json' ? '.pplan' : '.txt'}
          </button>
        </div>
      )}

      {tab === 'import' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
          <div style={S.row}>
            <span style={S.label}>{tr('pinpointFileLabel')}</span>
            <button onClick={() => fileRef.current?.click()} style={S.chipBtn}>
              {tr('pinpointSelectFile')}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".pplan,.txt,.json"
              onChange={handleFileLoad}
              style={{ display: 'none' }}
            />
          </div>

          <textarea
            value={importText}
            onChange={(e) => {
              setImportText(e.target.value);
              setImportErr('');
              setImportOk('');
            }}
            placeholder={tr('pinpointPastePlaceholder')}
            style={{
              flex: 1,
              minHeight: 180,
              background: '#030810',
              border: `1px solid ${importErr ? C.danger + '88' : C.border}`,
              borderRadius: 6,
              padding: 10,
              color: C.textSub,
              fontSize: 11,
              fontFamily: 'monospace',
              lineHeight: 1.6,
              resize: 'vertical',
              outline: 'none',
            }}
          />

          {importErr && <div style={{ ...S.alertBox, background: C.danger + '12', borderColor: C.danger + '55', color: C.danger }}>{importErr}</div>}
          {importOk && <div style={{ ...S.alertBox, background: C.success + '12', borderColor: C.success + '55', color: C.success }}>{importOk}</div>}

          <button
            onClick={handleImportText}
            disabled={!importText.trim()}
            style={{
              ...S.actionBtn,
              background: importText.trim() ? C.success + '18' : C.panel2,
              color: importText.trim() ? C.success : C.textMuted,
              borderColor: importText.trim() ? C.success + '55' : C.border,
              cursor: importText.trim() ? 'pointer' : 'not-allowed',
            }}
          >
            {tr('pinpointImportProbesBtn')}
          </button>
        </div>
      )}
    </div>
  );
}

const S = {
  row: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  label: { fontSize: 11, color: '#5a9ab8', minWidth: 44, flexShrink: 0 },
  chipBtn: {
    background: '#0a1828',
    border: '1px solid #162d48',
    borderRadius: 5,
    color: '#90c8e4',
    fontSize: 11,
    padding: '4px 10px',
    cursor: 'pointer',
  },
  actionBtn: {
    border: '1px solid',
    borderRadius: 7,
    padding: '9px 16px',
    fontSize: 12,
    fontWeight: 700,
    transition: 'all 0.15s',
    flexShrink: 0,
  },
  infoBox: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '7px 12px',
    background: '#0a1828',
    borderRadius: 6,
    border: '1px solid #162d48',
    fontSize: 11,
  },
  alertBox: {
    padding: '7px 12px',
    borderRadius: 6,
    border: '1px solid',
    fontSize: 11,
  },
};
