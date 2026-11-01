import { useState, useCallback } from 'react';
import { PROBE_PRESETS } from '../constants/anatomy.js';
import { t } from '../constants/i18n.js';
import { COLORS, sectionStyle, titleStyle, titleRowStyle, labelStyle, valStyle } from '../constants/theme.js';

const C = COLORS;

export default function ProbeSpecPanel({ probePreset, setProbePreset, probeParams, setProbeParams, lang }) {
  const tr = (key) => t(key, lang);
  const [showCustom, setShowCustom] = useState(false);

  const handlePresetChange = useCallback(
    (p) => {
      setProbePreset(p);
      if (p !== 'custom') {
        const preset = PROBE_PRESETS[p];
        setProbeParams({ depthUm: preset.depthUm, entrySpacingUm: preset.entrySpacingUm, shanks: preset.shanks });
      }
    },
    [setProbePreset, setProbeParams]
  );

  const currentPreset = PROBE_PRESETS[probePreset];
  const channelsPerShank = currentPreset?.channelsPerShank ?? 384;
  const shanks = probeParams.shanks ?? currentPreset?.shanks ?? 1;
  const shankPitchUm = probeParams.shankPitchUm ?? currentPreset?.shankPitchUm;
  const totalChannels = channelsPerShank * shanks;
  const channelLabel = shanks > 1 ? `${channelsPerShank} x ${shanks} = ${totalChannels}` : `${totalChannels}`;

  return (
    <div style={sectionStyle}>
      <div style={titleRowStyle}>
        <div style={titleStyle}>{tr('probeSpec')}</div>
        <button
          style={{
            padding: '2px 9px',
            background: showCustom ? '#04182a' : '#030e1a',
            border: `1px solid ${showCustom ? '#1a4a7a' : C.border}`,
            borderRadius: 4,
            color: showCustom ? '#4a9ab8' : C.textMuted,
            fontSize: 10.5,
            cursor: 'pointer',
          }}
          onClick={() => setShowCustom((v) => !v)}
        >
          {tr('customLabel')}
        </button>
      </div>

      <select
        style={{
          width: '100%',
          background: '#030e1a',
          border: `1px solid ${C.borderBright}`,
          borderRadius: 4,
          padding: '5px 8px',
          color: C.text,
          fontSize: 11.5,
          outline: 'none',
          cursor: 'pointer',
          marginBottom: 6,
        }}
        value={probePreset}
        onChange={(e) => handlePresetChange(e.target.value)}
      >
        {Object.entries(PROBE_PRESETS).map(([k, v]) => (
          <option key={k} value={k}>
            {v.label}
          </option>
        ))}
      </select>

      {showCustom && (
        <div>
          {[
            {
              label: tr('depthMm'),
              key: 'depthUm',
              min: 2000,
              max: 12000,
              step: 500,
              disp: `${(probeParams.depthUm / 1000).toFixed(0)}mm`,
            },
            {
              label: tr('entrySpacing'),
              key: 'entrySpacingUm',
              min: 200,
              max: 5000,
              step: 100,
              disp: `${probeParams.entrySpacingUm}um`,
            },
          ].map(({ label, key, min, max, step, disp }) => (
            <div key={key} style={{ marginBottom: 9 }}>
              <div style={labelStyle}>
                <span>{label}</span>
                <span style={valStyle}>{disp}</span>
              </div>
              <input
                type="range"
                style={{ width: '100%', accentColor: C.accent, height: 3, cursor: 'pointer' }}
                min={min}
                max={max}
                step={step}
                value={probeParams[key]}
                onChange={(e) => {
                  setProbePreset('custom');
                  setProbeParams((p) => ({ ...p, [key]: +e.target.value }));
                }}
              />
            </div>
          ))}
        </div>
      )}

      {[
        [tr('shanksLabel'), `${shanks}`],
        [tr('shankPitchLabel'), Number.isFinite(shankPitchUm) ? `${shankPitchUm} um` : tr('unavailableLabel')],
        [tr('depth'), `${(probeParams.depthUm / 1000).toFixed(0)} mm`],
        [tr('channelCount'), channelLabel],
        [tr('minEntryLabel'), `${probeParams.entrySpacingUm} um`],
      ].map(([l, v]) => (
        <div
          key={l}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            padding: '4px 0',
            borderBottom: '1px solid #162d48',
            fontSize: 11,
          }}
        >
          <span style={{ color: '#5a9ab8' }}>{l}</span>
          <span style={{ color: '#a0cce4', fontWeight: 600 }}>{v}</span>
        </div>
      ))}
    </div>
  );
}
