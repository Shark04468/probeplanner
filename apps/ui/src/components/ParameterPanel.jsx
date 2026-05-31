import { t } from '../constants/i18n.js';
import { useEffect } from 'react';
import { COLORS, sectionStyle, titleStyle, labelStyle, valStyle } from '../constants/theme.js';

const C = COLORS;

function normalizeParetoWeights(params, setParams) {
  const c = Number(params.objectiveCoverageWeight ?? 0.5);
  const s = Number(params.objectiveSafetyWeight ?? 0.3);
  const a = Number(params.objectiveAngleWeight ?? 0.2);
  const sum = Math.max(0.001, c + s + a);
  setParams((prev) => ({
    ...prev,
    objectiveCoverageWeight: +(c / sum).toFixed(3),
    objectiveSafetyWeight: +(s / sum).toFixed(3),
    objectiveAngleWeight: +(a / sum).toFixed(3),
  }));
}

const OBJECTIVE_PRESETS = [
  { key: 'balanced', labelKey: 'presetBalanced', w: [0.5, 0.3, 0.2] },
  { key: 'coverage_first', labelKey: 'presetCoverageFirst', w: [0.68, 0.22, 0.1] },
  { key: 'safety_first', labelKey: 'presetSafetyFirst', w: [0.25, 0.65, 0.1] },
  { key: 'angle_first', labelKey: 'presetAngleFirst', w: [0.3, 0.2, 0.5] },
];

function applyObjectivePreset(setParams, presetKey) {
  const preset = OBJECTIVE_PRESETS.find((p) => p.key === presetKey) || OBJECTIVE_PRESETS[0];
  setParams((prev) => ({
    ...prev,
    objectivePreset: preset.key,
    objectiveCoverageWeight: preset.w[0],
    objectiveSafetyWeight: preset.w[1],
    objectiveAngleWeight: preset.w[2],
  }));
}

function toggleFlag(setParams, key) {
  setParams((p) => ({ ...p, [key]: !p[key] }));
}

export default function ParameterPanel({ params, setParams, lang, mode = 'expert' }) {
  const tr = (key) => t(key, lang);
  const expertMode = String(mode || 'expert') === 'expert';
  const robustEnabled = Boolean(params.robustOptimization);
  const paretoEnabled = Boolean(params.paretoEnabled);
  const drccEnabled = params.algorithm === 'drcc' || Boolean(params.drccEnabled);
  useEffect(() => {
    if (params.drccSolver === 'misocp') {
      setParams((p) => ({ ...p, drccSolver: 'exact_pool' }));
    }
  }, [params.drccSolver, setParams]);
  const objectivePreset = params.objectivePreset || 'balanced';
  const targetingMode = params.targetingMode || 'single_hemisphere';
  const riskThresholdTemplate = String(params.riskThresholdTemplate || 'balanced').toLowerCase();
  const riskTemplateOptions = [
    { key: 'conservative', labelKey: 'riskThresholdTemplateConservative' },
    { key: 'balanced', labelKey: 'riskThresholdTemplateBalanced' },
    { key: 'aggressive', labelKey: 'riskThresholdTemplateAggressive' },
  ];
  const patchCalibration = (updates) =>
    setParams((p) => ({
      ...p,
      ...updates,
      uncertaintyCalibration: {
        ...(p.uncertaintyCalibration || {}),
        ...updates,
      },
    }));

  const sliders = [
    {
      key: 'minClearance',
      label: tr('minClearance'),
      min: 300,
      max: 3000,
      step: 100,
      disp: `${params.minClearance}um`,
    },
    {
      key: 'coverageRadius',
      label: tr('coverageRadius'),
      min: 200,
      max: 1200,
      step: 50,
      disp: `${params.coverageRadius}um`,
    },
    {
      key: 'gridSpacing',
      label: tr('gridSpacing'),
      min: 300,
      max: 1500,
      step: 100,
      disp: `${params.gridSpacing}um`,
    },
    {
      key: 'maxAngle',
      label: tr('maxAngle'),
      min: 5,
      max: 25,
      step: 1,
      disp: `${params.maxAngle}${tr('angleUnit')}`,
    },
    {
      key: 'skullTiltDeg',
      label: tr('skullTilt'),
      min: 0,
      max: 8,
      step: 0.5,
      disp: (
        <span>
          {params.skullTiltDeg}
          {tr('angleUnit')}
          <span style={{ fontSize: 9.5, color: '#4a8a70', marginLeft: 6 }}>
            {params.skullTiltDeg > 0
              ? `(~${(3 * Math.tan((params.skullTiltDeg * Math.PI) / 180)).toFixed(2)}mm @ AP±3)`
              : tr('off')}
          </span>
        </span>
      ),
    },
  ];

  const simpleSliderKeys = new Set(['minClearance', 'coverageRadius', 'gridSpacing', 'maxAngle', 'skullTiltDeg']);
  const visibleSliders = expertMode ? sliders : sliders.filter((item) => simpleSliderKeys.has(item.key));
  const toggleItems = expertMode
    ? [
        {
          key: 'bilateral',
          pre: tr('bilateral'),
          disabled: targetingMode === 'bilateral_symmetric',
        },
        { key: 'coverageBoost', pre: tr('coverageBoost'), disabled: false },
        { key: 'robustOptimization', pre: tr('robustOptimizationLabel'), disabled: false },
        { key: 'paretoEnabled', pre: tr('paretoPresetLabel'), disabled: false },
      ]
    : [
        {
          key: 'bilateral',
          pre: tr('bilateral'),
          disabled: targetingMode === 'bilateral_symmetric',
        },
        { key: 'coverageBoost', pre: tr('coverageBoost'), disabled: false },
      ];

  return (
    <div style={sectionStyle}>
      <div style={titleStyle}>{tr('parameters')}</div>

      <div style={{ marginBottom: 10 }}>
        <div style={labelStyle}>
          <span>{tr('algorithm')}</span>
        </div>
        <select
          style={{
            width: '100%',
            background: '#030e1a',
            border: `1px solid ${C.borderBright}`,
            borderRadius: 4,
            padding: '5px 8px',
            color: '#7ac8e8',
            fontSize: 11.5,
            outline: 'none',
            cursor: 'pointer',
          }}
          value={params.algorithm}
          onChange={(e) => setParams((p) => ({ ...p, algorithm: e.target.value }))}
        >
          <option value="greedy">{tr('greedy')}</option>
          <option value="exact">{tr('exact')}</option>
          <option value="milp">{tr('algoMilpLabel')}</option>
          <option value="drcc">{tr('drccLabel')}</option>
        </select>
      </div>

      <div style={{ marginBottom: 10 }}>
        <div style={labelStyle}>
          <span>{tr('targetingModeLabel')}</span>
        </div>
        <select
          style={{
            width: '100%',
            background: '#030e1a',
            border: `1px solid ${C.borderBright}`,
            borderRadius: 4,
            padding: '5px 8px',
            color: '#7ac8e8',
            fontSize: 11.5,
            outline: 'none',
            cursor: 'pointer',
          }}
          value={targetingMode}
          onChange={(e) => {
            const mode = e.target.value;
            setParams((p) => ({
              ...p,
              targetingMode: mode,
              bilateral: mode === 'bilateral_symmetric' ? true : p.bilateral,
            }));
          }}
        >
          <option value="single_hemisphere">{tr('singleHemisphereLabel')}</option>
          <option value="bilateral_symmetric">{tr('bilateralSymmetricLabel')}</option>
        </select>
      </div>

      {!expertMode && (
        <div
          style={{
            marginBottom: 8,
            padding: '6px 8px',
            border: '1px solid #17425f',
            borderRadius: 6,
            background: '#041120',
            fontSize: 10.5,
            color: '#8ec9e9',
            lineHeight: 1.45,
          }}
        >
          {tr('paramSimpleHint')}
        </div>
      )}

      {visibleSliders.map(({ key, label, min, max, step, disp }) => (
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
            value={params[key]}
            onChange={(e) => setParams((p) => ({ ...p, [key]: +e.target.value }))}
          />
        </div>
      ))}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
        {toggleItems.map(({ key, pre, disabled }) => (
          <div
            key={key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              padding: '4px 8px',
              background: '#030e1a',
              border: `1px solid ${C.border}`,
              borderRadius: 5,
            }}
          >
            <span style={{ fontSize: 11, color: C.textSub }}>{pre}</span>
            <button
              onClick={() => {
                if (disabled) return;
                toggleFlag(setParams, key);
              }}
              style={{
                padding: '2px 8px',
                background: params[key] ? '#012a14' : '#110208',
                border: `1px solid ${params[key] ? '#1a6a3a' : '#4a1020'}`,
                borderRadius: 9,
                color: params[key] ? '#34d399' : '#f87171',
                fontSize: 10.5,
                cursor: disabled ? 'not-allowed' : 'pointer',
                fontWeight: 700,
                opacity: disabled ? 0.72 : 1,
              }}
              disabled={disabled}
            >
              {disabled ? tr('on') : params[key] ? tr('on') : tr('off')}
            </button>
          </div>
        ))}
      </div>

      {expertMode && robustEnabled && (
        <div
          style={{
            marginTop: 9,
            padding: '7px 8px',
            border: '1px solid #14425f',
            borderRadius: 6,
            background: '#041120',
            display: 'grid',
            gap: 8,
          }}
        >
          <RangeField
            label={tr('robustSamplesLabel')}
            value={params.robustSamples ?? 16}
            display={`${params.robustSamples ?? 16}`}
            min={4}
            max={48}
            step={2}
            color="#22d3ee"
            onChange={(value) => setParams((p) => ({ ...p, robustSamples: value }))}
          />

          <RangeField
            label={tr('entrySigmaLabel')}
            value={params.entrySigmaMm ?? params.perturbSigmaMm ?? 0.15}
            display={`${Number(params.entrySigmaMm ?? params.perturbSigmaMm ?? 0.15).toFixed(2)} mm`}
            min={0.02}
            max={0.6}
            step={0.01}
            color="#22d3ee"
            onChange={(value) =>
              patchCalibration({
                perturbSigmaMm: value,
                entrySigmaMm: value,
              })
            }
          />

          <RangeField
            label={tr('angleSigmaLabel')}
            value={params.angleSigmaDeg ?? 2}
            display={`${Number(params.angleSigmaDeg ?? 2).toFixed(2)} deg`}
            min={0.2}
            max={8}
            step={0.1}
            color="#22d3ee"
            onChange={(value) => patchCalibration({ angleSigmaDeg: value })}
          />

          <RangeField
            label={tr('depthSigmaLabel')}
            value={params.depthSigmaMm ?? 0.2}
            display={`${Number(params.depthSigmaMm ?? 0.2).toFixed(2)} mm`}
            min={0.02}
            max={1.2}
            step={0.01}
            color="#22d3ee"
            onChange={(value) => patchCalibration({ depthSigmaMm: value })}
          />

          <RangeField
            label={tr('riskDistanceSigmaLabel')}
            value={params.riskDistanceSigmaMm ?? 0.08}
            display={`${Number(params.riskDistanceSigmaMm ?? 0.08).toFixed(2)} mm`}
            min={0.01}
            max={0.6}
            step={0.01}
            color="#22d3ee"
            onChange={(value) => patchCalibration({ riskDistanceSigmaMm: value })}
          />

          <RangeField
            label={tr('riskMarginTreLabel')}
            value={params.riskMarginTreMm ?? 0}
            display={`${Number(params.riskMarginTreMm ?? 0).toFixed(2)} mm`}
            min={0}
            max={1.5}
            step={0.01}
            color="#facc15"
            onChange={(value) => patchCalibration({ riskMarginTreMm: value })}
          />

          <RangeField
            label={tr('minRiskDistanceLabel')}
            value={params.minRiskDistanceMm ?? 0.35}
            display={`${Number(params.minRiskDistanceMm ?? 0.35).toFixed(2)} mm`}
            min={0.0}
            max={2.0}
            step={0.01}
            color="#34d399"
            onChange={(value) => setParams((p) => ({ ...p, minRiskDistanceMm: value }))}
          />

          <div style={{ marginTop: 2 }}>
            <div style={labelStyle}>
              <span>{tr('riskThresholdTemplateLabel')}</span>
              <span style={valStyle}>
                {tr(
                  riskTemplateOptions.find((item) => item.key === riskThresholdTemplate)?.labelKey ||
                    'riskThresholdTemplateBalanced',
                )}
              </span>
            </div>
            <select
              style={{
                width: '100%',
                background: '#031120',
                border: '1px solid #1c4f66',
                borderRadius: 5,
                padding: '4px 6px',
                color: '#9edaff',
                fontSize: 10.5,
                outline: 'none',
                cursor: 'pointer',
              }}
              value={riskThresholdTemplate}
              onChange={(e) =>
                setParams((p) => ({
                  ...p,
                  riskThresholdTemplate: e.target.value,
                }))
              }
            >
              {riskTemplateOptions.map((item) => (
                <option key={item.key} value={item.key}>
                  {tr(item.labelKey)}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {expertMode && paretoEnabled && (
        <div
          style={{
            marginTop: 9,
            padding: '7px 8px',
            border: '1px solid #1f3d68',
            borderRadius: 6,
            background: '#051223',
            display: 'grid',
            gap: 8,
          }}
        >
          <RangeField
            label={tr('paretoTopKLabel')}
            value={params.paretoTopK ?? 12}
            display={`${params.paretoTopK ?? 12}`}
            min={4}
            max={24}
            step={1}
            color="#60a5fa"
            onChange={(value) => setParams((p) => ({ ...p, paretoTopK: value }))}
          />

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {OBJECTIVE_PRESETS.map((preset) => {
              const active = objectivePreset === preset.key;
              return (
                <button
                  key={preset.key}
                  onClick={() => applyObjectivePreset(setParams, preset.key)}
                  style={{
                    padding: '3px 7px',
                    borderRadius: 4,
                    border: `1px solid ${active ? '#3b82f6' : '#1f3d68'}`,
                    background: active ? '#0b2a4b' : '#071629',
                    color: active ? '#93c5fd' : '#8fb6e7',
                    fontSize: 10,
                    cursor: 'pointer',
                    fontWeight: active ? 700 : 500,
                  }}
                >
                  {tr(preset.labelKey)}
                </button>
              );
            })}
          </div>

          <WeightSlider
            label={tr('coverageWeightLabel')}
            value={params.objectiveCoverageWeight ?? 0.5}
            onChange={(value) =>
              setParams((p) => ({
                ...p,
                objectiveCoverageWeight: value,
              }))
            }
          />
          <WeightSlider
            label={tr('safetyWeightLabel')}
            value={params.objectiveSafetyWeight ?? 0.3}
            onChange={(value) =>
              setParams((p) => ({
                ...p,
                objectiveSafetyWeight: value,
              }))
            }
          />
          <WeightSlider
            label={tr('incidenceWeightLabel')}
            value={params.objectiveAngleWeight ?? 0.2}
            onChange={(value) =>
              setParams((p) => ({
                ...p,
                objectiveAngleWeight: value,
              }))
            }
          />

          <button
            onClick={() => normalizeParetoWeights(params, setParams)}
            style={{
              marginTop: 2,
              padding: '4px 8px',
              borderRadius: 5,
              border: '1px solid #275a8e',
              background: '#0b2138',
              color: '#93c5fd',
              fontSize: 10.5,
              cursor: 'pointer',
            }}
          >
            {tr('normalizeWeightsLabel')}
          </button>
        </div>
      )}

      {expertMode && drccEnabled && (
        <div
          style={{
            marginTop: 9,
            padding: '7px 8px',
            border: '1px solid #205977',
            borderRadius: 6,
            background: '#041624',
            display: 'grid',
            gap: 8,
          }}
        >
          <div style={{ ...labelStyle, color: '#9adcf2' }}>
            <span>{tr('drccSettingsLabel')}</span>
          </div>
          <RangeField
            label={tr('drccRiskEpsilonLabel')}
            value={params.drccRiskEpsilon ?? 0.08}
            display={`${Number(params.drccRiskEpsilon ?? 0.08).toFixed(3)}`}
            min={0.01}
            max={0.30}
            step={0.005}
            color="#22d3ee"
            onChange={(value) => setParams((p) => ({ ...p, drccEnabled: true, drccRiskEpsilon: value }))}
          />
          <RangeField
            label={tr('drccWassersteinRadiusLabel')}
            value={params.drccWassersteinRadius ?? 0.03}
            display={`${Number(params.drccWassersteinRadius ?? 0.03).toFixed(3)}`}
            min={0.0}
            max={0.20}
            step={0.005}
            color="#22d3ee"
            onChange={(value) =>
              setParams((p) => ({
                ...p,
                drccEnabled: true,
                drccWassersteinRadius: value,
              }))
            }
          />
          <RangeField
            label={tr('drccEvalTopKLabel')}
            value={params.drccEvalTopK ?? 240}
            display={`${params.drccEvalTopK ?? 240}`}
            min={40}
            max={600}
            step={10}
            color="#60a5fa"
            onChange={(value) => setParams((p) => ({ ...p, drccEvalTopK: value }))}
          />
          <div style={{ marginTop: 2 }}>
            <div style={labelStyle}>
              <span>{tr('drccSolverLabel')}</span>
              <span style={valStyle}>{String(params.drccSolver === 'misocp' ? 'exact_pool' : (params.drccSolver ?? 'exact_pool'))}</span>
            </div>
            <select
              style={{
                width: '100%',
                background: '#031120',
                border: '1px solid #245074',
                borderRadius: 5,
                padding: '4px 6px',
                color: '#9edaff',
                fontSize: 10.5,
                outline: 'none',
                cursor: 'pointer',
              }}
              value={params.drccSolver === 'misocp' ? 'exact_pool' : (params.drccSolver ?? 'exact_pool')}
              onChange={(e) => setParams((p) => ({ ...p, drccSolver: e.target.value }))}
            >
              <option value="exact_pool">{tr('drccSolverExactPoolLabel')}</option>
              <option value="cvar_relax">{tr('drccSolverCvarRelaxLabel')}</option>
              <option value="hybrid">{tr('drccSolverHybridLabel')}</option>
              <option value="heuristic">{tr('drccSolverHeuristicLabel')}</option>
            </select>
          </div>
          <RangeField
            label={tr('drccExactMaxCandidatesLabel')}
            value={params.drccExactMaxCandidates ?? 42}
            display={`${params.drccExactMaxCandidates ?? 42}`}
            min={8}
            max={120}
            step={2}
            color="#93c5fd"
            onChange={(value) => setParams((p) => ({ ...p, drccExactMaxCandidates: value }))}
          />
          <RangeField
            label={tr('drccExactTimeLimitLabel')}
            value={params.drccExactTimeLimitS ?? 18}
            display={`${Number(params.drccExactTimeLimitS ?? 18).toFixed(1)}s`}
            min={2}
            max={120}
            step={1}
            color="#93c5fd"
            onChange={(value) => setParams((p) => ({ ...p, drccExactTimeLimitS: value }))}
          />
          <RangeField
            label={tr('sdfRiskIntegralWeightLabel')}
            value={params.sdfRiskIntegralWeight ?? 1.0}
            display={`${Number(params.sdfRiskIntegralWeight ?? 1.0).toFixed(2)}`}
            min={0.0}
            max={3.0}
            step={0.05}
            color="#34d399"
            onChange={(value) => setParams((p) => ({ ...p, sdfRiskIntegralWeight: value }))}
          />
          <RangeField
            label={tr('sdfRiskCvarWeightLabel')}
            value={params.sdfRiskCvarWeight ?? 0.7}
            display={`${Number(params.sdfRiskCvarWeight ?? 0.7).toFixed(2)}`}
            min={0.0}
            max={3.0}
            step={0.05}
            color="#34d399"
            onChange={(value) => setParams((p) => ({ ...p, sdfRiskCvarWeight: value }))}
          />
          <RangeField
            label={tr('selectiveConformalAlphaLabel')}
            value={params.selectiveConformalAlpha ?? 0.1}
            display={`${Number(params.selectiveConformalAlpha ?? 0.1).toFixed(2)}`}
            min={0.01}
            max={0.30}
            step={0.01}
            color="#f59e0b"
            onChange={(value) =>
              setParams((p) => ({
                ...p,
                selectiveConformalEnabled: true,
                selectiveConformalAlpha: value,
              }))
            }
          />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <TinyToggle
              label={tr('drccStrictLabel')}
              on={Boolean(params.drccStrict)}
              onText={tr('on')}
              offText={tr('off')}
              onClick={() => setParams((p) => ({ ...p, drccStrict: !p.drccStrict }))}
            />
            <TinyToggle
              label={tr('selectiveConformalEnabledLabel')}
              on={Boolean(params.selectiveConformalEnabled)}
              onText={tr('on')}
              offText={tr('off')}
              onClick={() =>
                setParams((p) => ({
                  ...p,
                  selectiveConformalEnabled: !p.selectiveConformalEnabled,
                }))
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}

function RangeField({ label, value, display, min, max, step, color, onChange }) {
  return (
    <div>
      <div style={labelStyle}>
        <span>{label}</span>
        <span style={valStyle}>{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(+e.target.value)}
        style={{ width: '100%', accentColor: color, height: 3, cursor: 'pointer' }}
      />
    </div>
  );
}

function WeightSlider({ label, value, onChange }) {
  return (
    <div style={{ marginBottom: 1 }}>
      <div style={labelStyle}>
        <span>{label}</span>
        <span style={valStyle}>{Number(value).toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(+e.target.value)}
        style={{ width: '100%', accentColor: '#60a5fa', height: 3, cursor: 'pointer' }}
      />
    </div>
  );
}

function TinyToggle({ label, on, onClick, onText = 'On', offText = 'Off' }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '4px 8px',
        borderRadius: 5,
        border: `1px solid ${on ? '#2f8f5e' : '#66465a'}`,
        background: on ? '#0e2d1e' : '#1a1118',
        color: on ? '#86efac' : '#fda4af',
        fontSize: 10.5,
        cursor: 'pointer',
      }}
    >
      {label}: {on ? onText : offText}
    </button>
  );
}

