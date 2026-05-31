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

function assertInRange(label, value, [min, max]) {
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${label} out of range [${min}, ${max}]`);
  }
}

function normalizeProbe(p, i) {
  const ap = Number(p.ap ?? p.ap_mm ?? 0);
  const ml = Number(p.ml ?? p.ml_mm ?? 0);
  const dv = Number(p.dv ?? p.entryDvCorr_mm ?? 0);
  const phi = Number(p.phi ?? p.phi_deg ?? 0);
  const theta = Number(p.theta ?? p.theta_deg ?? 0);
  const depth = Number(p.depth ?? (p.depthUm != null ? p.depthUm / 1000 : 4));

  assertInRange('ap', ap, LIMITS.ap);
  assertInRange('ml', ml, LIMITS.ml);
  assertInRange('dv', dv, LIMITS.dv);
  assertInRange('theta', theta, LIMITS.theta);
  assertInRange('depth', depth, LIMITS.depth);

  return {
    ap_mm: ap,
    ml_mm: ml,
    entryDvCorr_mm: dv,
    phi_deg: ((phi % 360) + 360) % 360,
    theta_deg: theta,
    spin_deg: Number(p.spin ?? p.spin_deg ?? 0),
    depthUm: depth * 1000,
    coveredRegions: p.coveredRegions || [],
    _id: p.id || p._id || `Probe${i}`,
  };
}

export function exportPinpointJSON(probes, probePreset = 'NP2.0-4sh') {
  const data = {
    version: '1.0',
    generator: 'probe-planner',
    timestamp: new Date().toISOString(),
    probes: probes.map((p, i) => ({
      id: `Probe${i}`,
      ap: +p.ap_mm.toFixed(3),
      ml: +p.ml_mm.toFixed(3),
      dv: +p.entryDvCorr_mm.toFixed(3),
      phi: +p.phi_deg.toFixed(2),
      theta: +p.theta_deg.toFixed(2),
      spin: +((p.spin_deg ?? 0)).toFixed(2),
      depth: +(p.depthUm / 1000).toFixed(3),
      type: probePreset,
      coveredRegions: p.coveredRegions || [],
    })),
  };
  return JSON.stringify(data, null, 2);
}

export function exportPinpointTXT(probes) {
  const lines = [
    '# id  ap(mm)  ml(mm)  dv(mm)  phi(deg)  theta(deg)  spin(deg)  depth(mm)',
  ];
  for (let i = 0; i < probes.length; i += 1) {
    const p = probes[i];
    lines.push([
      `Probe${i}`,
      p.ap_mm.toFixed(3),
      p.ml_mm.toFixed(3),
      p.entryDvCorr_mm.toFixed(3),
      p.phi_deg.toFixed(2),
      p.theta_deg.toFixed(2),
      ((p.spin_deg ?? 0)).toFixed(2),
      (p.depthUm / 1000).toFixed(3),
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
    return obj.probes.map((p, i) => normalizeProbe(p, i));
  }

  const rows = trimmed
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'));

  return rows.map((row, i) => {
    const cols = row.split(/\s+/);
    if (cols.length < 8) {
      throw new Error(`Line ${i + 1} has insufficient columns`);
    }
    return normalizeProbe(
      {
        id: cols[0],
        ap: parseFloat(cols[1]),
        ml: parseFloat(cols[2]),
        dv: parseFloat(cols[3]),
        phi: parseFloat(cols[4]),
        theta: parseFloat(cols[5]),
        spin: parseFloat(cols[6]),
        depth: parseFloat(cols[7]),
      },
      i
    );
  });
}

function toBackendTrajectory(p) {
  return {
    ap_mm: p.ap ?? p.ap_mm ?? 0,
    ml_mm: p.ml ?? p.ml_mm ?? 0,
    entryDvCorr_mm: p.dvEntry ?? p.entryDvCorr_mm ?? 0,
    phi_deg: p.azimuth ?? p.phi_deg ?? 0,
    theta_deg: p.theta_deg ?? (p.elevation != null ? 90 - p.elevation : 0),
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
        correctionFactors: correction || {},
        correctionUncertainty: correctionUncertainty || null,
      });
      if (!resp.ok) {
        throw new Error(resp.error || 'export failed');
      }

      const backendProbes = (resp.pinpointData?.probes || []).map((p) =>
        normalizeProbe(
          {
            ap: -p.ap,
            ml: p.ml,
            dv: p.dv,
            phi: p.phi,
            theta: p.theta,
            depth: p.depth,
            spin: p.spin,
            coveredRegions: p.coveredRegions,
          },
          0
        )
      );

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
