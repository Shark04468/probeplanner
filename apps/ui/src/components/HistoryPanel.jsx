/**
 * HistoryPanel.jsx
 *
 * FIX: tr('historyHint', { max: MAX_HISTORY }) was passing an object as the
 *      second argument to t(), which is the `lang` parameter — causing the
 *      translation to fail. Replaced with inline string concatenation.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { THEME } from '../constants/theme';
import { t } from '../constants/i18n';

const STORAGE_KEY = 'probePlannerHistory';
const MAX_HISTORY = 30;

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]'); }
  catch { return []; }
}

function saveHistory(list) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_HISTORY))); }
  catch { console.warn('HistoryPanel: localStorage write failed'); }
}

function uid() { return Math.random().toString(36).slice(2, 9); }

export default function HistoryPanel({ currentState, onRestore, lang = 'cn' }) {
  const tr = useCallback((k) => t(k, lang), [lang]);

  const [history,    setHistory]    = useState(loadHistory);
  const [editingId,  setEditingId]  = useState(null);
  const [editLabel,  setEditLabel]  = useState('');
  const [compareIds, setCompareIds] = useState([]);
  const labelInputRef = useRef(null);

  useEffect(() => { saveHistory(history); }, [history]);
  useEffect(() => { if (editingId) labelInputRef.current?.focus(); }, [editingId]);

  const saveCurrentPlan = useCallback(() => {
    if (!currentState?.results) return;
    const entry = {
      id:          uid(),
      label:       `${tr('plan')} ${history.length + 1}`,
      savedAt:     Date.now(),
      regions:     currentState.regions     ?? [],
      params:      currentState.params      ?? {},
      results:     currentState.results     ?? null,
      constraints: currentState.constraints ?? {},
    };
    setHistory(h => [entry, ...h]);
  }, [currentState, history.length, tr]);

  const deletePlan = useCallback((id) => {
    setHistory(h => h.filter(e => e.id !== id));
    setCompareIds(c => c.filter(i => i !== id));
  }, []);

  const startRename = useCallback((entry) => {
    setEditingId(entry.id);
    setEditLabel(entry.label);
  }, []);

  const commitRename = useCallback(() => {
    if (!editingId) return;
    setHistory(h => h.map(e =>
      e.id === editingId ? { ...e, label: editLabel.trim() || e.label } : e
    ));
    setEditingId(null);
  }, [editingId, editLabel]);

  const toggleCompare = useCallback((id) => {
    setCompareIds(c =>
      c.includes(id) ? c.filter(i => i !== id) : c.length < 3 ? [...c, id] : c
    );
  }, []);

  return (
    <div style={styles.panel}>

      <div style={styles.header}>
        <span style={styles.title}>{tr('historyPanelTitle')}</span>
        <button
          style={{ ...styles.saveBtn, opacity: currentState?.results ? 1 : 0.4, cursor: currentState?.results ? 'pointer' : 'not-allowed' }}
          onClick={saveCurrentPlan}
          disabled={!currentState?.results}
          title={tr('saveCurrentPlan')}
        >
          {tr('saveCurrentPlan')}
        </button>
      </div>

      {compareIds.length > 0 && (
        <CompareBar ids={compareIds} history={history} onClear={() => setCompareIds([])} tr={tr} />
      )}

      {history.length === 0 ? (
        <div style={styles.empty}>{tr('historyEmpty')}</div>
      ) : (
        <div style={styles.list}>
          {history.map(entry => (
            <PlanEntry
              key={entry.id}
              entry={entry}
              isEditing={editingId === entry.id}
              editLabel={editLabel}
              comparing={compareIds.includes(entry.id)}
              onRename={() => startRename(entry)}
              onEditChange={setEditLabel}
              onEditCommit={commitRename}
              onRestore={() => onRestore?.(entry)}
              onDelete={() => deletePlan(entry.id)}
              onToggleCompare={() => toggleCompare(entry.id)}
              labelInputRef={editingId === entry.id ? labelInputRef : null}
              tr={tr}
            />
          ))}
        </div>
      )}

      {/* FIX: was tr('historyHint', { max: MAX_HISTORY }) — second arg is lang, not options */}
      <div style={styles.hint}>
        {tr('historyHint')} {MAX_HISTORY} · {history.length}/{MAX_HISTORY}
      </div>
    </div>
  );
}

// ── PlanEntry ─────────────────────────────────────────────────────────────────
function PlanEntry({
  entry, isEditing, editLabel, comparing,
  onRename, onEditChange, onEditCommit,
  onRestore, onDelete, onToggleCompare,
  labelInputRef, tr,
}) {
  const covPct = entry.results?.coverage
    ? ((entry.results.coverage.covered / Math.max(entry.results.coverage.total || 0, 1)) * 100).toFixed(0)
    : null;

  return (
    <div style={{ ...styles.entry, borderColor: comparing ? THEME.accent + '88' : THEME.border }}>
      <div style={styles.entryTop}>
        {isEditing ? (
          <input ref={labelInputRef} style={styles.renameInput}
            value={editLabel} onChange={e => onEditChange(e.target.value)}
            onBlur={onEditCommit} onKeyDown={e => e.key === 'Enter' && onEditCommit()} />
        ) : (
          <span style={styles.entryLabel} onDoubleClick={onRename} title={tr('doubleClickRename')}>
            {entry.label}
          </span>
        )}
        <span style={styles.entryTime}>
          {new Date(entry.savedAt).toLocaleDateString()}{' '}
          {new Date(entry.savedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      <div style={styles.entryStats}>
        <StatChip label={tr('probes')}    value={entry.results?.probes?.length ?? entry.results?.selected?.length ?? '—'} />
        <StatChip label={tr('regions')}   value={entry.regions.length} />
        {covPct !== null && <StatChip label={tr('coverage')} value={`${covPct}%`} />}
        <StatChip label={tr('algorithm')} value={entry.params?.algorithm ?? '—'} />
      </div>

      <div style={styles.regionPreview}>
        {entry.regions.slice(0, 6).join(', ')}
        {entry.regions.length > 6 && ` +${entry.regions.length - 6}`}
      </div>

      <div style={styles.entryActions}>
        <ActionBtn onClick={onRestore}       label={tr('restore')}                                  color={THEME.accent} />
        <ActionBtn onClick={onToggleCompare} label={comparing ? tr('removeCompare') : tr('compare')} color='#a78bfa'     />
        <ActionBtn onClick={onRename}        label={tr('rename')}                                    color={THEME.textSecondary} />
        <ActionBtn onClick={onDelete}        label={tr('delete')}                                    color='#f87171'    />
      </div>
    </div>
  );
}

function StatChip({ label, value }) {
  return (
    <div style={styles.statChip}>
      <span style={styles.statLabel}>{label}</span>
      <span style={styles.statValue}>{value}</span>
    </div>
  );
}

function ActionBtn({ onClick, label, color }) {
  return (
    <button style={{ ...styles.actionBtn, color }} onClick={onClick}>{label}</button>
  );
}

// ── CompareBar ────────────────────────────────────────────────────────────────
function CompareBar({ ids, history, onClear, tr }) {
  const entries = ids.map(id => history.find(e => e.id === id)).filter(Boolean);
  return (
    <div style={styles.compareBar}>
      <div style={styles.compareHeader}>
        <span style={styles.compareTitle}>{tr('comparingPlans')}</span>
        <button style={styles.clearCompare} onClick={onClear}>{tr('clearCompare')}</button>
      </div>
      <div style={styles.compareGrid}>
        {['probes', 'coverage', 'regions', 'algorithm'].map(metric => (
          <CompareRow key={metric} metric={metric} entries={entries} tr={tr} />
        ))}
      </div>
    </div>
  );
}

function CompareRow({ metric, entries, tr }) {
  const getValue = (entry) => {
    switch (metric) {
      case 'probes':    return entry.results?.probes?.length ?? entry.results?.selected?.length ?? '—';
      case 'coverage':  {
        const c = entry.results?.coverage;
        return c ? `${((c.covered / Math.max(c.total || 0, 1)) * 100).toFixed(0)}%` : '—';
      }
      case 'regions':   return entry.regions.length;
      case 'algorithm': return entry.params?.algorithm ?? '—';
      default:          return '—';
    }
  };
  return (
    <div style={styles.compareRow}>
      <span style={styles.compareMetric}>{tr(metric)}</span>
      {entries.map(e => <span key={e.id} style={styles.compareValue}>{getValue(e)}</span>)}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const styles = {
  panel:   { background: THEME.panelBg, borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 },
  header:  { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  title:   { color: THEME.textPrimary, fontWeight: 600, fontSize: 13 },
  saveBtn: { background: THEME.accent + '22', color: THEME.accent, border: 'none', borderRadius: 5, padding: '4px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer' },
  empty:   { color: THEME.textSecondary, fontSize: 12, textAlign: 'center', padding: '12px 0' },
  list:    { display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 320, overflowY: 'auto' },
  entry:   { background: THEME.inputBg, borderRadius: 6, border: '1px solid', padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 5 },
  entryTop:{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  entryLabel: { color: THEME.textPrimary, fontWeight: 600, fontSize: 12, cursor: 'default' },
  entryTime:  { color: THEME.textSecondary, fontSize: 10, fontFamily: 'monospace' },
  renameInput:{ background: THEME.panelBg, border: `1px solid ${THEME.accent}`, borderRadius: 4, color: THEME.textPrimary, fontSize: 12, padding: '2px 6px', outline: 'none', flex: 1 },
  entryStats: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  statChip:   { display: 'flex', gap: 3, background: THEME.panelBg, borderRadius: 4, padding: '1px 6px', fontSize: 10 },
  statLabel:  { color: THEME.textSecondary },
  statValue:  { color: THEME.textPrimary, fontWeight: 600 },
  regionPreview: { fontSize: 10, color: THEME.textSecondary, fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  entryActions:  { display: 'flex', gap: 6 },
  actionBtn:     { background: 'none', border: 'none', fontSize: 10, fontWeight: 600, cursor: 'pointer', padding: '1px 4px', borderRadius: 3 },
  hint:          { fontSize: 10, color: THEME.textSecondary, textAlign: 'right' },
  compareBar:    { background: THEME.inputBg, border: `1px solid ${THEME.accent}44`, borderRadius: 6, padding: '8px 10px' },
  compareHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  compareTitle:  { fontSize: 11, color: '#a78bfa', fontWeight: 600 },
  clearCompare:  { background: 'none', border: 'none', color: THEME.textSecondary, fontSize: 10, cursor: 'pointer' },
  compareGrid:   { display: 'flex', flexDirection: 'column', gap: 3 },
  compareRow:    { display: 'flex', gap: 10, fontSize: 11 },
  compareMetric: { color: THEME.textSecondary, width: 70 },
  compareValue:  { color: THEME.textPrimary, fontFamily: 'monospace', fontWeight: 600, flex: 1 },
};
