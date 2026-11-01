import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../constants/i18n';

const FORM_STORAGE_KEY = 'probeplanner.benchmark.form.v2';
const OPTIONS_STORAGE_KEY = 'probeplanner.benchmark.options.v2';

function fmtSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '--';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function sortArtifacts(files) {
  return [...(files || [])].sort((a, b) => {
    const order = { markdown: 0, csv: 1, json: 2, plot: 3, other: 4 };
    const oa = order[a.type] ?? 9;
    const ob = order[b.type] ?? 9;
    if (oa !== ob) return oa - ob;
    return String(a.name || '').localeCompare(String(b.name || ''));
  });
}

function normalizeBase(base) {
  return String(base || '').trim().replace(/\/+$/, '');
}

function buildCandidateBases(baseUrl) {
  const bases = [];
  const push = (v) => {
    const s = normalizeBase(v);
    if (s && !bases.includes(s)) bases.push(s);
  };

  const primary = normalizeBase(baseUrl);
  push(primary);

  if (primary.includes('localhost')) push(primary.replace('localhost', '127.0.0.1'));
  if (primary.includes('127.0.0.1')) push(primary.replace('127.0.0.1', 'localhost'));

  if (typeof window !== 'undefined') {
    const origin = normalizeBase(window.location?.origin);
    if (origin && origin.startsWith('http')) push(origin);
  }

  push('http://127.0.0.1:5000');
  push('http://localhost:5000');
  return bases;
}

function isNetworkFetchError(err) {
  const msg = String(err?.message || '');
  return /Failed to fetch|NetworkError|ERR_CONNECTION|ECONNREFUSED|fetch/i.test(msg);
}

function isHttp404Error(err) {
  const msg = String(err?.message || '');
  return /HTTP 404/i.test(msg);
}

function encodePathForApi(path) {
  return String(path || '')
    .split('/')
    .filter(Boolean)
    .map((seg) => encodeURIComponent(seg))
    .join('/');
}

async function tryFetchJson(url, options) {
  const res = await fetch(url, options);
  let data = null;
  let raw = '';
  try {
    data = await res.json();
  } catch {
    try {
      raw = await res.text();
    } catch {
      raw = '';
    }
  }
  return { res, data, raw };
}

function loadStoredJson(key) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed;
  } catch {
    return null;
  }
}

function persistStoredJson(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore local storage failures
  }
}

export default function BenchmarkPanel({
  baseUrl = 'http://localhost:5000',
  authToken = '',
  backendStatus = 'idle',
  lang = 'cn',
}) {
  const tr = useCallback((k) => t(k, lang), [lang]);
  const [status, setStatus] = useState(null);
  const [resolvedBase, setResolvedBase] = useState(normalizeBase(baseUrl));
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [form, setForm] = useState({
    replayDir: '',
    outDir: '',
    expertCsv: '',
    manualOverrideCsv: '',
    trialCsv: '',
    residualCsv: '',
    paramTemplate: '',
  });
  const [options, setOptions] = useState({
    runReliability: true,
    runExternalValidation: true,
    runPaperPackage: true,
    runGoldenReplayCheck: true,
    runVersionSignature: true,
    runSilentStudy: true,
    runInterventional: false,
    runUncertaintyCalibration: false,
    strictGoldenReplay: false,
  });
  const hydratedRef = useRef(false);
  const backendOnline = backendStatus === 'online';

  const headers = useMemo(() => {
    const out = { 'Content-Type': 'application/json' };
    if (authToken) out.Authorization = `Bearer ${authToken}`;
    return out;
  }, [authToken]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const storedForm = loadStoredJson(FORM_STORAGE_KEY);
    const storedOptions = loadStoredJson(OPTIONS_STORAGE_KEY);
    if (storedForm) {
      setForm((prev) => ({ ...prev, ...storedForm }));
    }
    if (storedOptions) {
      setOptions((prev) => ({ ...prev, ...storedOptions }));
    }
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    persistStoredJson(FORM_STORAGE_KEY, form);
  }, [form]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    persistStoredJson(OPTIONS_STORAGE_KEY, options);
  }, [options]);

  const resolveErrorMessage = useCallback((err) => {
    if (isNetworkFetchError(err)) return tr('benchmarkBackendOffline');
    if (isHttp404Error(err)) return tr('benchmarkApiMissing');
    return err?.message || tr('benchmarkError');
  }, [tr]);

  const requestJson = useCallback(async (path, optionsArg = {}) => {
    const bases = buildCandidateBases(baseUrl);
    let lastError = null;
    for (const base of bases) {
      const url = `${base}${path}`;
      try {
        const { res, data, raw } = await tryFetchJson(url, {
          method: optionsArg.method || 'GET',
          headers,
          body: optionsArg.body || undefined,
        });

        if (res.status === 404) {
          lastError = new Error(`HTTP 404 @ ${url}`);
          continue;
        }
        if (!res.ok || data?.ok === false) {
          throw new Error(data?.error || raw || `HTTP ${res.status}`);
        }
        setResolvedBase(base);
        return data;
      } catch (err) {
        lastError = err;
        if (!isNetworkFetchError(err)) {
          throw err;
        }
      }
    }
    throw lastError || new Error('Failed to reach backend');
  }, [baseUrl, headers]);

  const mergeFormFromStatus = useCallback((data) => {
    const tpl = data?.templatePaths || {};
    setForm((prev) => ({
      replayDir: prev.replayDir || data?.replayDir || '',
      outDir: prev.outDir || data?.outDir || '',
      expertCsv: prev.expertCsv || tpl.expertCsv || '',
      manualOverrideCsv: prev.manualOverrideCsv || tpl.manualOverrideCsv || '',
      trialCsv: prev.trialCsv || tpl.trialCsv || '',
      residualCsv: prev.residualCsv || tpl.residualCsv || '',
      paramTemplate: prev.paramTemplate || tpl.paramTemplate || '',
    }));
  }, []);

  const buildStatusPath = useCallback((payloadForm) => {
    const q = new URLSearchParams();
    const pairs = {
      replayDir: payloadForm.replayDir,
      outDir: payloadForm.outDir,
      expertCsv: payloadForm.expertCsv,
      manualOverrideCsv: payloadForm.manualOverrideCsv,
      trialCsv: payloadForm.trialCsv,
      residualCsv: payloadForm.residualCsv,
      paramTemplate: payloadForm.paramTemplate,
    };
    Object.entries(pairs).forEach(([key, value]) => {
      const v = String(value || '').trim();
      if (v) q.set(key, v);
    });
    const qs = q.toString();
    return `/api/benchmark/status${qs ? `?${qs}` : ''}`;
  }, []);

  const loadStatus = useCallback(async (payloadForm = form) => {
    if (!backendOnline) {
      setStatus(null);
      setError(tr('benchmarkBackendOffline'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await requestJson(buildStatusPath(payloadForm));
      setStatus(data);
      if (!hydratedRef.current) {
        mergeFormFromStatus(data);
        hydratedRef.current = true;
      }
    } catch (err) {
      setError(resolveErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [backendOnline, buildStatusPath, form, mergeFormFromStatus, requestJson, resolveErrorMessage, tr]);

  useEffect(() => {
    if (!backendOnline) {
      setStatus(null);
      setError(tr('benchmarkBackendOffline'));
      return;
    }
    loadStatus();
  }, [backendOnline, loadStatus, tr]);

  const applyStatusToForm = useCallback((data) => {
    const tpl = data?.templatePaths || {};
    setForm((prev) => ({
      replayDir: data?.replayDir || prev.replayDir,
      outDir: data?.outDir || prev.outDir,
      expertCsv: tpl.expertCsv || prev.expertCsv,
      manualOverrideCsv: tpl.manualOverrideCsv || prev.manualOverrideCsv,
      trialCsv: tpl.trialCsv || prev.trialCsv,
      residualCsv: tpl.residualCsv || prev.residualCsv,
      paramTemplate: tpl.paramTemplate || prev.paramTemplate,
    }));
  }, []);

  const initWorkspace = useCallback(async () => {
    if (!backendOnline) {
      setError(tr('benchmarkBackendOffline'));
      return;
    }
    setInitializing(true);
    setError('');
    try {
      const data = await requestJson('/api/benchmark/init', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          overwriteTemplates: false,
        }),
      });
      setStatus(data);
      applyStatusToForm(data);
      hydratedRef.current = true;
    } catch (err) {
      setError(resolveErrorMessage(err));
    } finally {
      setInitializing(false);
      loadStatus(form);
    }
  }, [applyStatusToForm, backendOnline, form, loadStatus, requestJson, resolveErrorMessage, tr]);

  const runBenchmark = useCallback(async () => {
    if (!backendOnline) {
      setError(tr('benchmarkBackendOffline'));
      return;
    }
    setRunning(true);
    setError('');
    try {
      const data = await requestJson('/api/benchmark/run', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          options,
        }),
      });
      setStatus(data);
      applyStatusToForm(data);
      hydratedRef.current = true;
    } catch (err) {
      setError(resolveErrorMessage(err));
    } finally {
      setRunning(false);
      loadStatus(form);
    }
  }, [applyStatusToForm, backendOnline, form, loadStatus, options, requestJson, resolveErrorMessage, tr]);

  const updateForm = useCallback((key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  const toggleOption = useCallback((key) => {
    setOptions((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const files = useMemo(() => sortArtifacts(status?.files || []), [status?.files]);
  const outDir = status?.outDir || form.outDir || '';
  const plotFiles = useMemo(() => files.filter((f) => f.type === 'plot'), [files]);
  const pipeline = useMemo(() => {
    if (Array.isArray(status?.pipeline)) return status.pipeline;
    if (Array.isArray(status?.lastRun?.pipeline)) return status.lastRun.pipeline;
    return [];
  }, [status?.lastRun?.pipeline, status?.pipeline]);
  const createdFiles = status?.createdFiles || [];
  const evidenceChecklist = useMemo(() => buildEvidenceChecklist(files, pipeline), [files, pipeline]);

  const buildFileUrl = useCallback((name) => {
    const encodedOut = encodeURIComponent(outDir);
    const encodedName = encodePathForApi(name);
    return `${resolvedBase}/api/benchmark/file/${encodedName}?outDir=${encodedOut}`;
  }, [outDir, resolvedBase]);

  const optionRows = [
    { key: 'runReliability', label: tr('benchmarkOptionReliability') },
    { key: 'runExternalValidation', label: tr('benchmarkOptionExternalValidation') },
    { key: 'runPaperPackage', label: tr('benchmarkOptionPaperPackage') },
    { key: 'runGoldenReplayCheck', label: tr('benchmarkOptionGoldenReplay') },
    { key: 'runVersionSignature', label: tr('benchmarkOptionVersionSignature') },
    { key: 'runSilentStudy', label: tr('benchmarkOptionSilentStudy') },
    { key: 'runInterventional', label: tr('benchmarkOptionInterventional') },
    { key: 'runUncertaintyCalibration', label: tr('benchmarkOptionUncertaintyCalibration') },
    { key: 'strictGoldenReplay', label: tr('benchmarkOptionStrictGoldenReplay') },
  ];

  return (
    <div style={styles.wrap}>
      <div style={styles.headerRow}>
        <div style={styles.title}>{tr('benchmarkPanelTitle')}</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={styles.btn} onClick={() => loadStatus(form)} disabled={loading || running || initializing || !backendOnline}>
            {tr('benchmarkRefresh')}
          </button>
          <button
            style={{ ...styles.btn, ...styles.btnWarn }}
            onClick={initWorkspace}
            disabled={initializing || running || !backendOnline}
          >
            {initializing ? tr('benchmarkInitializing') : tr('benchmarkInit')}
          </button>
          <button
            style={{ ...styles.btn, ...styles.btnPrimary }}
            onClick={runBenchmark}
            disabled={running || initializing || !backendOnline}
          >
            {running ? tr('benchmarkRunning') : tr('benchmarkRun')}
          </button>
        </div>
      </div>

      {error && (
        <div style={styles.error}>
          {tr('benchmarkError')}: {error}
        </div>
      )}

      <div style={styles.formGrid}>
        <LabeledInput label={tr('benchmarkReplayDir')} value={form.replayDir} onChange={(v) => updateForm('replayDir', v)} />
        <LabeledInput label={tr('benchmarkOutDir')} value={form.outDir} onChange={(v) => updateForm('outDir', v)} />
        <LabeledInput label={tr('benchmarkExpertCsv')} value={form.expertCsv} onChange={(v) => updateForm('expertCsv', v)} />
        <LabeledInput label={tr('benchmarkManualOverrideCsv')} value={form.manualOverrideCsv} onChange={(v) => updateForm('manualOverrideCsv', v)} />
        <LabeledInput label={tr('benchmarkTrialCsv')} value={form.trialCsv} onChange={(v) => updateForm('trialCsv', v)} />
        <LabeledInput label={tr('benchmarkResidualCsv')} value={form.residualCsv} onChange={(v) => updateForm('residualCsv', v)} />
        <LabeledInput label={tr('benchmarkParamTemplate')} value={form.paramTemplate} onChange={(v) => updateForm('paramTemplate', v)} />
      </div>

      <div style={styles.optionWrap}>
        <div style={styles.sectionTitle}>{tr('benchmarkRunOptions')}</div>
        <div style={styles.optionGrid}>
          {optionRows.map((row) => (
            <label key={row.key} style={styles.optionItem}>
              <input
                type="checkbox"
                checked={Boolean(options[row.key])}
                onChange={() => toggleOption(row.key)}
                style={{ accentColor: '#22d3ee' }}
              />
              <span>{row.label}</span>
            </label>
          ))}
        </div>
      </div>

      <div style={styles.meta}>
        <div><strong>{tr('benchmarkBackendBase')}:</strong> {resolvedBase || '--'}</div>
        <div><strong>{tr('benchmarkReplayCount')}:</strong> {status?.replayCount ?? '--'}</div>
        <div><strong>{tr('benchmarkTemplateDir')}:</strong> {status?.templateDir || '--'}</div>
      </div>

      {createdFiles.length > 0 && (
        <div style={styles.infoBox}>
          <div style={styles.sectionTitle}>{tr('benchmarkCreatedFiles')}</div>
          <div style={styles.inlineList}>{createdFiles.join(' | ')}</div>
        </div>
      )}

      <div style={styles.infoBox}>
        <div style={styles.sectionTitle}>{tr('benchmarkEvidenceTitle')}</div>
        <div style={styles.evidenceGrid}>
          {evidenceChecklist.map((item) => (
            <div key={item.key} style={styles.evidenceRow}>
              <span style={item.ok ? styles.evidenceOk : styles.evidenceMiss}>{item.ok ? 'OK' : '--'}</span>
              <span>{tr(item.labelKey)}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={styles.sectionTitle}>{tr('benchmarkPipeline')}</div>
      {!pipeline.length ? (
        <div style={styles.empty}>{tr('benchmarkNoPipeline')}</div>
      ) : (
        <div style={styles.pipelineList}>
          {pipeline.map((step, idx) => (
            <div key={`${step.name || 'step'}-${idx}`} style={styles.pipelineItem}>
              <div style={styles.pipelineHead}>
                <span>{step.name || `step-${idx + 1}`}</span>
                <span style={pipelineStatusStyle(step.status)}>
                  {step.status || '--'}
                </span>
              </div>
              {Array.isArray(step.stderrTail) && step.stderrTail.length > 0 && (
                <div style={styles.pipelineTail}>
                  {step.stderrTail.join(' | ')}
                </div>
              )}
              {(!step.stderrTail || step.stderrTail.length === 0) && Array.isArray(step.stdoutTail) && step.stdoutTail.length > 0 && (
                <div style={styles.pipelineTail}>
                  {step.stdoutTail.join(' | ')}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={styles.sectionTitle}>{tr('benchmarkFiles')}</div>
      {!files.length ? (
        <div style={styles.empty}>{tr('benchmarkNoFiles')}</div>
      ) : (
        <div style={styles.fileList}>
          {files.map((f) => (
            <div key={f.name} style={styles.fileRow}>
              <div style={styles.fileName} title={f.name}>{f.name}</div>
              <div style={styles.fileMeta}>
                <span>{fmtSize(f.sizeBytes)}</span>
                <a href={buildFileUrl(f.name)} target="_blank" rel="noreferrer" style={styles.link}>
                  {tr('benchmarkOpen')}
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      {plotFiles.length > 0 && (
        <>
          <div style={styles.sectionTitle}>{tr('benchmarkPlots')}</div>
          <div style={styles.plots}>
            {plotFiles.map((f) => (
              <a key={f.name} href={buildFileUrl(f.name)} target="_blank" rel="noreferrer" style={styles.plotCard}>
                <img src={buildFileUrl(f.name)} alt={f.name} style={styles.plotImg} />
                <div style={styles.plotLabel}>{f.name}</div>
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function LabeledInput({ label, value, onChange }) {
  return (
    <label style={styles.inputLabel}>
      <span>{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={styles.input}
      />
    </label>
  );
}

function pipelineStatusStyle(status) {
  const raw = String(status || '').toLowerCase();
  if (raw === 'ok') return { color: '#4ade80' };
  if (raw === 'skipped') return { color: '#facc15' };
  if (raw === 'missing_script') return { color: '#f59e0b' };
  if (raw === 'failed') return { color: '#f87171' };
  return { color: '#94a3b8' };
}

function buildEvidenceChecklist(files, pipeline) {
  const names = new Set((files || []).map((f) => String(f.name || '').toLowerCase()));
  const hasName = (re) => Array.from(names).some((name) => re.test(name));
  const pipelineOk = (name) =>
    (pipeline || []).some((step) => String(step?.name || '').toLowerCase() === name && String(step?.status || '').toLowerCase() === 'ok');
  return [
    {
      key: 'coverage',
      labelKey: 'benchmarkEvidenceCoverage',
      ok: hasName(/coverage/) && (hasName(/\.png$/) || hasName(/\.csv$/)),
    },
    {
      key: 'safety',
      labelKey: 'benchmarkEvidenceSafety',
      ok: hasName(/safety/) && (hasName(/distance/) || hasName(/clearance/)),
    },
    {
      key: 'pass_rate',
      labelKey: 'benchmarkEvidencePassRate',
      ok: hasName(/pass/) && hasName(/rate/),
    },
    {
      key: 'sankey',
      labelKey: 'benchmarkEvidenceSankey',
      ok: hasName(/sankey/),
    },
    {
      key: 'calibration',
      labelKey: 'benchmarkEvidenceCalibration',
      ok: hasName(/calibration|reliability/),
    },
    {
      key: 'mri_qc',
      labelKey: 'benchmarkEvidenceMriQc',
      ok: hasName(/mri_ccf.*qc|registration_qc|overlay/),
    },
    {
      key: 'contact_table',
      labelKey: 'benchmarkEvidenceContactTable',
      ok: hasName(/contact_table|contact_summary/),
    },
    {
      key: 'ablation',
      labelKey: 'benchmarkEvidenceAblation',
      ok: hasName(/ablation/),
    },
    {
      key: 'paper',
      labelKey: 'benchmarkEvidencePaperPack',
      ok: pipelineOk('paper_package') || hasName(/paper_figures/),
    },
    {
      key: 'signature',
      labelKey: 'benchmarkEvidenceSignature',
      ok: pipelineOk('version_signature') || names.has('version_signature.json'),
    },
  ];
}

const styles = {
  wrap: {
    background: '#08182a',
    border: '1px solid #1a3f61',
    borderRadius: 8,
    padding: 12,
    display: 'grid',
    gap: 10,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  title: {
    color: '#8fdcff',
    fontSize: 12,
    fontWeight: 700,
  },
  btn: {
    border: '1px solid #245074',
    background: '#072238',
    color: '#9edaff',
    borderRadius: 6,
    padding: '5px 9px',
    fontSize: 11,
    cursor: 'pointer',
  },
  btnPrimary: {
    border: '1px solid #0ea5e9',
    background: '#0b3452',
    color: '#67e8f9',
  },
  btnWarn: {
    border: '1px solid #f59e0b',
    background: '#3a2610',
    color: '#fcd34d',
  },
  formGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
    gap: 8,
  },
  inputLabel: {
    display: 'grid',
    gap: 4,
    color: '#93b7d7',
    fontSize: 10.5,
  },
  input: {
    border: '1px solid #1f4466',
    borderRadius: 6,
    background: '#061726',
    color: '#cde6ff',
    fontSize: 11,
    padding: '6px 8px',
    fontFamily: 'monospace',
    outline: 'none',
  },
  optionWrap: {
    border: '1px solid #1b3854',
    borderRadius: 6,
    padding: 8,
    background: '#071a2b',
    display: 'grid',
    gap: 8,
  },
  optionGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 6,
  },
  optionItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    color: '#a7c8e6',
    fontSize: 11,
  },
  meta: {
    display: 'grid',
    gap: 4,
    color: '#93b7d7',
    fontSize: 10.5,
    fontFamily: 'monospace',
    wordBreak: 'break-all',
  },
  sectionTitle: {
    color: '#7dd3fc',
    fontSize: 11,
    fontWeight: 600,
    marginTop: 2,
  },
  inlineList: {
    color: '#9fc2df',
    fontSize: 10.5,
    fontFamily: 'monospace',
    wordBreak: 'break-all',
  },
  infoBox: {
    border: '1px solid #1b3854',
    borderRadius: 6,
    background: '#071a2b',
    padding: '8px 10px',
    display: 'grid',
    gap: 4,
  },
  evidenceGrid: {
    display: 'grid',
    gap: 4,
  },
  evidenceRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    color: '#9fc2df',
    fontSize: 10.5,
  },
  evidenceOk: {
    color: '#4ade80',
    fontSize: 9,
    width: 10,
    display: 'inline-flex',
    justifyContent: 'center',
  },
  evidenceMiss: {
    color: '#f59e0b',
    fontSize: 9,
    width: 10,
    display: 'inline-flex',
    justifyContent: 'center',
  },
  empty: {
    color: '#6f8ea8',
    fontSize: 11,
  },
  pipelineList: {
    display: 'grid',
    gap: 6,
  },
  pipelineItem: {
    border: '1px solid #1b3854',
    borderRadius: 6,
    padding: '6px 8px',
    background: '#071a2b',
    display: 'grid',
    gap: 4,
  },
  pipelineHead: {
    display: 'flex',
    justifyContent: 'space-between',
    color: '#c6e7ff',
    fontSize: 10.5,
    fontFamily: 'monospace',
    gap: 8,
  },
  pipelineTail: {
    color: '#94b8d8',
    fontSize: 10,
    fontFamily: 'monospace',
    wordBreak: 'break-word',
    opacity: 0.95,
  },
  fileList: {
    display: 'grid',
    gap: 6,
  },
  fileRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    border: '1px solid #1b3854',
    borderRadius: 6,
    padding: '6px 8px',
    background: '#071a2b',
  },
  fileName: {
    color: '#c6e7ff',
    fontSize: 10.5,
    fontFamily: 'monospace',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  fileMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    color: '#86a8c4',
    fontSize: 10,
    flexShrink: 0,
  },
  link: {
    color: '#22d3ee',
    textDecoration: 'none',
  },
  plots: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
    gap: 8,
  },
  plotCard: {
    border: '1px solid #1b3854',
    borderRadius: 6,
    background: '#061726',
    padding: 6,
    textDecoration: 'none',
  },
  plotImg: {
    width: '100%',
    height: 120,
    objectFit: 'contain',
    background: '#020b14',
    borderRadius: 4,
  },
  plotLabel: {
    marginTop: 6,
    color: '#94b8d8',
    fontSize: 10,
    fontFamily: 'monospace',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  error: {
    color: '#fca5a5',
    fontSize: 11,
  },
};

