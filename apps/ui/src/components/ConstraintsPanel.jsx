import React from 'react';
import { DEFAULT_CONSTRAINTS } from '../constants/constraintSchema';
import { t } from '../constants/i18n';

const C = {
  panel2: '#0a1828',
  border: '#1b3656',
  text: '#e3f3ff',
  textMuted: '#7da7c5',
  warn: '#facc15',
  danger: '#f87171',
  off: '#84a3be',
};

export const defaultConstraints = DEFAULT_CONSTRAINTS;

const CONSTRAINT_DEFS = [
  { key: 'maxAngle', icon: 'ANG', labelKey: 'constraintMaxAngleLabel', descKey: 'constraintMaxAngleDesc' },
  { key: 'minEntrySpacing', icon: 'SP', labelKey: 'constraintMinEntrySpacingLabel', descKey: 'constraintMinEntrySpacingDesc' },
  { key: 'minClearance', icon: 'CLR', labelKey: 'constraintMinClearanceLabel', descKey: 'constraintMinClearanceDesc' },
  { key: 'lambdaBoundary', icon: 'LAM', labelKey: 'constraintLambdaLabel', descKey: 'constraintLambdaDesc' },
  { key: 'brainBounds', icon: 'B', labelKey: 'constraintBrainBoundsLabel', descKey: 'constraintBrainBoundsDesc' },
  { key: 'continuousRiskDistance', icon: 'SDF', labelKey: 'constraintContinuousRiskLabel', descKey: 'constraintContinuousRiskDesc' },
  { key: 'hemisphere', icon: 'L/R', labelKey: 'constraintHemisphereLabel', descKey: 'constraintHemisphereDesc' },
];

const MODES = [
  { id: 'enforce', short: 'E', labelKey: 'constraintEnforce', color: C.danger },
  { id: 'warn', short: 'W', labelKey: 'constraintWarn', color: C.warn },
  { id: 'off', short: 'O', labelKey: 'constraintOff', color: C.off },
];

export default function ConstraintsPanel({ constraints = {}, onChange, lang = 'cn' }) {
  const tr = (key) => t(key, lang);
  const enfCount = Object.values(constraints).filter((v) => v === 'enforce').length;
  const warnCount = Object.values(constraints).filter((v) => v === 'warn').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div
        style={{
          display: 'flex',
          gap: 6,
          padding: '6px 0 8px',
          borderBottom: `1px solid ${C.border}`,
          marginBottom: 4,
        }}
      >
        {[
          { val: enfCount, label: tr('constraintEnforce'), color: C.danger },
          { val: warnCount, label: tr('constraintWarn'), color: C.warn },
          { val: CONSTRAINT_DEFS.length - enfCount - warnCount, label: tr('constraintOff'), color: C.off },
        ].map(({ val, label, color }) => (
          <div
            key={label}
            style={{
              flex: 1,
              textAlign: 'center',
              padding: '5px 4px',
              background: `${color}14`,
              borderRadius: 6,
              border: `1px solid ${color}36`,
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 700, color }}>{val}</div>
            <div style={{ fontSize: 9, color: '#9fc0d8', marginTop: 1 }}>{label}</div>
          </div>
        ))}
      </div>

      {CONSTRAINT_DEFS.map((def) => {
        const mode = constraints[def.key] || 'off';
        const label = tr(def.labelKey);
        const desc = tr(def.descKey);

        return (
          <div
            key={def.key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 5px',
              borderRadius: 6,
              background: mode === 'enforce' ? `${C.danger}10` : mode === 'warn' ? `${C.warn}12` : '#071423',
              minWidth: 0,
              border: `1px solid ${mode === 'off' ? '#10263d' : `${MODES.find((m) => m.id === mode)?.color}30`}`,
            }}
          >
            <span
              style={{
                fontSize: 9,
                minWidth: 28,
                height: 18,
                borderRadius: 4,
                border: `1px solid ${C.border}`,
                color: '#afd0e8',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                letterSpacing: '0.02em',
                background: '#0a1d31',
                flexShrink: 0,
                fontWeight: 700,
              }}
            >
              {def.icon}
            </span>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  color: C.text,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={label}
              >
                {label}
              </div>
              <div
                style={{
                  fontSize: 10,
                  color: '#79a3c2',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={desc}
              >
                {desc}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
              {MODES.map((m) => {
                const selected = mode === m.id;
                return (
                  <button
                    key={m.id}
                    title={tr(m.labelKey)}
                    onClick={() => onChange(def.key, m.id)}
                    style={{
                      width: 30,
                      height: 24,
                      borderRadius: 6,
                      border: `1px solid ${selected ? `${m.color}95` : '#23415f'}`,
                      background: selected ? `${m.color}2b` : '#0a1a2d',
                      color: selected ? m.color : '#8cb2cf',
                      cursor: 'pointer',
                      fontSize: 10,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'all 0.12s',
                      fontWeight: 800,
                      boxShadow: selected ? `0 0 0 1px ${m.color}30 inset` : 'none',
                    }}
                  >
                    {m.short}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}

      <div
        style={{
          marginTop: 6,
          padding: '6px 8px',
          background: C.panel2,
          borderRadius: 5,
          border: `1px solid ${C.border}`,
          fontSize: 10,
          color: '#8fb2ce',
          lineHeight: 1.6,
        }}
      >
        [E] {tr('constraintLegendEnforce')} | [W] {tr('constraintLegendWarn')} | [O] {tr('constraintLegendOff')}
      </div>
    </div>
  );
}
