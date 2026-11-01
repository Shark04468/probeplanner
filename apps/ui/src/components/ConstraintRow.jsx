import { t } from '../constants/i18n.js';

const modeColors = {
  enforce: ['#002d48', '#00d4ff'],
  warn: ['#2e2000', '#facc15'],
  off: ['#3a0808', '#ff6060'],
};

export default function ConstraintRow({ def, mode, onChange, lang }) {
  const tr = (key) => t(key, lang);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid #152338' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: '#b8daf0', fontWeight: 600, lineHeight: 1.3 }}>{tr(def.labelKey)}</div>
        <div style={{ fontSize: 10.5, color: '#6aaac8', marginTop: 2, lineHeight: 1.4 }}>{tr(def.descKey)}</div>
      </div>
      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
        {['enforce', 'warn', 'off'].map((m) => {
          const [bg, fg] = modeColors[m];
          const active = mode === m;
          const label = m === 'enforce' ? tr('constraintEnforce') : m === 'warn' ? tr('constraintWarn') : tr('constraintOff');
          return (
            <button
              key={m}
              onClick={() => onChange(m)}
              style={{
                padding: '4px 9px',
                background: active ? bg : '#0a1828',
                border: `1px solid ${active ? fg : m === 'off' ? '#4a1a1a' : '#162d48'}`,
                borderRadius: 4,
                color: active ? fg : m === 'off' ? '#7a3030' : '#3a6888',
                fontSize: 10.5,
                cursor: 'pointer',
                fontWeight: active ? 700 : 400,
                transition: 'all 0.15s',
                minWidth: 36,
                textAlign: 'center',
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
