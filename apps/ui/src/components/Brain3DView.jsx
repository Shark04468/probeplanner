import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../constants/i18n';
import {
  CLINICAL_CLASS_LABEL_KEY,
  CLINICAL_CLASS_ORDER,
  DISPLAY_LAYER_LABEL_KEY,
  DISPLAY_LAYER_ORDER,
  classifyRegionDisplay,
} from '../constants/atlasDisplayPolicy';
import { BREGMA_UM, PROBE_PRESETS } from '../constants/anatomy';
import MriSurfaceOverlay from './MriSurfaceOverlay';
import unityBridge from './brain3d/unityBridge';

const {
  BRIDGE_METRIC_ABSOLUTE_KEYS,
  UNITY_COMMAND_FLUSH_INTERVAL_MS,
  UNITY_COMMAND_RETRY_DELAYS_MS,
  UNITY_FALLBACK_MAX_PAYLOAD_BYTES,
  UNITY_FALLBACK_UNSUPPORTED_METHODS,
  UNITY_LARGE_PAYLOAD_METHODS,
  UNITY_LEGACY_BRIDGE_COMPAT,
  UNITY_NO_RETRY_METHODS,
  estimateUnityPayloadBytes,
  normalizeUnityCapabilities,
  summarizeUnityCapabilities,
} = unityBridge;

const UNITY_SOURCE = process.env.REACT_APP_UNITY_URL || '/unity/index.html?v=20260513-interactivefix';
const ENABLE_MESH_HEALTHCHECK =
  String(process.env.REACT_APP_MESH_HEALTHCHECK || '')
    .trim()
    .toLowerCase() === '1';
const AUTO_FOLLOW_FOCUSED_REGION_SLICE =
  String(process.env.REACT_APP_SLICE_AUTO_FOLLOW_FOCUS || '')
    .trim()
    .toLowerCase() === '1';
const HEARTBEAT_INTERVAL_MS = 2000;
const HEARTBEAT_TIMEOUT_MS = 12000;
const HEARTBEAT_TIMEOUT_LOADING_MS = 45000;
const RESYNC_CLEAR_DELAY_MS = 900;
const RESYNC_COOLDOWN_MS = 1800;
const MESH_STALL_RESYNC_MS = 30000;
const CAMERA_POSE_SUPPRESS_MS = 220;
const CAMERA_VIEWS = ['iso', 'top', 'side', 'front'];
const VIEW_TEMPLATES = ['overview', 'target', 'risk', 'fine', 'mri'];
const VIEW_TEMPLATE_LABEL_KEY = {
  overview: 'brain3dTemplateOverview',
  target: 'brain3dTemplateTarget',
  risk: 'brain3dTemplateRisk',
  fine: 'brain3dTemplateFine',
  mri: 'brain3dTemplateMri',
};
const CAMERA_PRESETS = {
  iso: { theta: 0.55, phi: 1.25, distance: 18 },
  top: { theta: 0, phi: 0.05, distance: 18 },
  side: { theta: 0, phi: Math.PI * 0.5, distance: 18 },
  front: { theta: Math.PI * 0.5, phi: Math.PI * 0.5, distance: 18 },
};
const CAMERA_LIMITS = {
  minDistance: 4,
  maxDistance: 55,
  minPhi: 0.05,
  maxPhi: Math.PI - 0.05,
};
const CAMERA_PIVOT = { x: 0, y: -2.8, z: -0.8 };
// Keep projection parameters aligned with Unity CameraController defaults.
const UNITY_FOV_DEG = 38;

const PROBE_COLORS = {
  'NP2.0-4sh': '#00d4ff',
  'NP2.0-1sh': '#34d399',
  'NP1.0': '#a78bfa',
  custom: '#facc15',
};

const SLICE_AXES = ['y', 'z', 'x']; // y: horizontal, z: coronal, x: sagittal
const SLICE_AXIS_LABEL_KEY = {
  y: 'brain3dSliceAxisHorizontal',
  z: 'brain3dSliceAxisCoronal',
  x: 'brain3dSliceAxisSagittal',
};
const SLICE_MODES = ['below', 'slab'];
const CONTACT_DISPLAY_MODES = ['standard', 'slice_contact_persist', 'contact_only'];
const CONTACT_DISPLAY_STYLES = ['hybrid', 'outline', 'fill'];

const DEFAULT_SLICE_PRESETS = [
  { id: 'ca1', region: 'CA1', axis: 'z', mode: 'slab', thicknessMm: 0.3, label: 'CA1' },
  { id: 'vpm', region: 'VPM', axis: 'z', mode: 'slab', thicknessMm: 0.3, label: 'VPM' },
  { id: 'acb', region: 'ACB', axis: 'z', mode: 'slab', thicknessMm: 0.35, label: 'ACB' },
];
const SLICE_THICKNESS_PRESETS_UM = [100, 200, 300, 500];
const CLINICAL_FOCUS_CATEGORY_LIMIT = 10;
const CLINICAL_FOCUS_GROUP_LIMIT = 10;
const CLINICAL_FOCUS_KEY_LIMIT = 128;
const CLINICAL_FOCUS_MAX_VISIBLE = 72;
const UNITY_CONTACT_REGION_LIMIT = 160;
const UNITY_CONTACT_REGION_KEY_MAX = 64;
const AGGREGATE_PARENT_KEYS = new Set([
  'root',
  'grey',
  'CTX',
  'CTXpl',
  'Isocortex',
  'BS',
  'CNU',
  'HB',
  'IB',
  'CB',
  'CBX',
  'HEM',
  'fiber tracts',
]);
const AGGREGATE_SUBREGION_THRESHOLD = 96;
const AGGREGATE_VOLUME_THRESHOLD = 1200000;
const RISK_CONTEXT_KEYS = ['VS', 'AQ', 'V3', 'V4', 'VL', 'IVF', 'chpl', 'SEZ'];
const DEFAULT_CONTEXT_KEYS = ['VISp', 'SSp', 'MOp', 'MOs', 'CA1', 'DG', 'CP', 'VPM', 'ACA'];
const TARGET_TEMPLATE_LAYER_VISIBILITY = { L1: true, L2: true, L3: false, L4: false };
const TARGET_TEMPLATE_CLASS_VISIBILITY = CLINICAL_CLASS_ORDER.reduce((acc, key) => {
  acc[key] = key !== 'pathway' && key !== 'nerve_fissure';
  return acc;
}, {});
const TARGET_TEMPLATE_ENV_LIGHT = 0.72;
const MRI_APPEARANCE_STORAGE_KEY = 'probeplanner.mriAppearance';
const MRI_APPEARANCE_LAYERS_STORAGE_KEY = 'probeplanner.mriAppearanceLayers';
const DEFAULT_MRI_APPEARANCE_LAYERS = Object.freeze({
  outerBoundary: true,
  skull: true,
  brainSurface: true,
  ccfLabels: true,
  overlay: true,
});
const MRI_SURFACE_LAYER_BY_UI_KEY = Object.freeze({
  brainSurface: 'brain_surface',
  outerBoundary: 'outer_tissue',
  skull: 'skull',
});
const MRI_APPEARANCE_LAYER_ITEMS = Object.freeze([
  ['brainSurface', 'brain3dMriLayerBrainSurface', 'brain3d-mri-layer-surface'],
  ['outerBoundary', 'brain3dMriLayerOuterBoundary', 'brain3d-mri-layer-outer'],
  ['skull', 'brain3dMriLayerSkull', 'brain3d-mri-layer-skull'],
  ['ccfLabels', 'brain3dMriLayerCcfLabels', 'brain3d-mri-layer-ccf-labels'],
  ['overlay', 'brain3dMriOverlay', 'brain3d-mri-overlay'],
]);

const REGION_KEY_PATTERN = /^[A-Za-z0-9_:-]+$/;

function sanitizeContactRegionsForUnity(regions) {
  const out = [];
  const seen = new Set();
  if (!Array.isArray(regions)) return out;
  for (const raw of regions) {
    const key = String(raw || '').trim();
    if (!key || key.length > UNITY_CONTACT_REGION_KEY_MAX || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
    if (out.length >= UNITY_CONTACT_REGION_LIMIT) break;
  }
  return out;
}

function normalizeMriAppearanceLayers(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    outerBoundary: typeof source.outerBoundary === 'boolean'
      ? source.outerBoundary
      : typeof source.showOuterBoundary === 'boolean'
        ? source.showOuterBoundary
        : DEFAULT_MRI_APPEARANCE_LAYERS.outerBoundary,
    skull: typeof source.skull === 'boolean'
      ? source.skull
      : typeof source.showSkull === 'boolean'
        ? source.showSkull
        : DEFAULT_MRI_APPEARANCE_LAYERS.skull,
    brainSurface: typeof source.brainSurface === 'boolean'
      ? source.brainSurface
      : typeof source.showBrainSurface === 'boolean'
        ? source.showBrainSurface
        : DEFAULT_MRI_APPEARANCE_LAYERS.brainSurface,
    ccfLabels: typeof source.ccfLabels === 'boolean'
      ? source.ccfLabels
      : typeof source.showCcfLabels === 'boolean'
        ? source.showCcfLabels
        : DEFAULT_MRI_APPEARANCE_LAYERS.ccfLabels,
    overlay: typeof source.overlay === 'boolean'
      ? source.overlay
      : source.overlayMode
        ? String(source.overlayMode) === 'mri_ccf_overlay'
        : DEFAULT_MRI_APPEARANCE_LAYERS.overlay,
  };
}

function buildMriAppearancePayload(enabled, layers, activeAtlasMode, isMriHybridMode) {
  const cleanLayers = normalizeMriAppearanceLayers(layers);
  const active = Boolean(enabled && isMriHybridMode);
  const overlayActive = Boolean(active && cleanLayers.overlay);
  return {
    enabled: active,
    useMriAppearance: active,
    atlasMode: isMriHybridMode ? 'mri_ccf_hybrid' : String(activeAtlasMode || 'allen_sdk'),
    showOuterBoundary: Boolean(active && cleanLayers.outerBoundary),
    showSkull: Boolean(active && cleanLayers.skull),
    showBrainSurface: Boolean(active && cleanLayers.brainSurface),
    showCcfLabels: Boolean(!active || (overlayActive && cleanLayers.ccfLabels)),
    overlayMode: active ? (overlayActive ? 'mri_ccf_overlay' : 'mri_only') : 'ccf_only',
  };
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

function findClosestBoundaryCandidate(candidates, value) {
  if (!Array.isArray(candidates) || candidates.length <= 0 || !Number.isFinite(value)) return null;
  let lo = 0;
  let hi = candidates.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const coord = Number(candidates[mid]?.coord);
    if (!Number.isFinite(coord)) {
      lo = mid + 1;
      continue;
    }
    if (coord < value) lo = mid + 1;
    else hi = mid - 1;
  }
  const idxs = [clamp(lo, 0, candidates.length - 1), clamp(lo - 1, 0, candidates.length - 1)];
  let best = null;
  idxs.forEach((idx) => {
    const item = candidates[idx];
    if (!item) return;
    const coord = Number(item.coord);
    if (!Number.isFinite(coord)) return;
    const distance = Math.abs(coord - value);
    if (!best || distance < best.distance) {
      best = {
        key: String(item.key || '').trim(),
        coord,
        distance,
      };
    }
  });
  return best;
}

function mapOpacity(slider) {
  return clamp(slider / 100, 0, 1);
}

function isAggregateParentRegion(key, meta) {
  if (!key || !meta || typeof meta !== 'object') return false;
  if (AGGREGATE_PARENT_KEYS.has(key)) return true;
  if (Number(meta.nSubregions || 0) >= AGGREGATE_SUBREGION_THRESHOLD) return true;
  if (Number(meta.voxelCount || 0) >= AGGREGATE_VOLUME_THRESHOLD) return true;
  return false;
}

function clampDepthMm(v, depthMm) {
  if (!Number.isFinite(v)) return null;
  return clamp(v, 0, Math.max(0, depthMm));
}

function deriveActiveSegmentMm(probe, depthMm) {
  const explicitStartMm = Number(
    probe?.activeStartDepthMm ??
      probe?.activeDepthStartMm ??
      probe?.recordingStartDepthMm ??
      probe?.activeStartUm / 1000,
  );
  const explicitEndMm = Number(
    probe?.activeEndDepthMm ??
      probe?.activeDepthEndMm ??
      probe?.recordingEndDepthMm ??
      probe?.activeEndUm / 1000,
  );

  if (Number.isFinite(explicitStartMm) || Number.isFinite(explicitEndMm)) {
    const s = clampDepthMm(Number.isFinite(explicitStartMm) ? explicitStartMm : 0, depthMm);
    const e = clampDepthMm(
      Number.isFinite(explicitEndMm) ? explicitEndMm : depthMm,
      depthMm,
    );
    if (Number.isFinite(s) && Number.isFinite(e) && e > s + 1e-4) {
      return { startDepthMm: s, endDepthMm: e };
    }
  }

  const recordingLengthMm = Number(
    probe?.recordingLengthMm ??
      probe?.activeLengthMm ??
      probe?.recordingLengthUm / 1000 ??
      probe?.activeLengthUm / 1000,
  );
  if (Number.isFinite(recordingLengthMm) && recordingLengthMm > 0) {
    const end = clampDepthMm(depthMm, depthMm);
    const start = clampDepthMm(end - recordingLengthMm, depthMm);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start + 1e-4) {
      return { startDepthMm: start, endDepthMm: end };
    }
  }

  // Fallback: distal 70% as active recording window.
  const fallbackStart = clampDepthMm(depthMm * 0.30, depthMm);
  const fallbackEnd = clampDepthMm(depthMm, depthMm);
  if (Number.isFinite(fallbackStart) && Number.isFinite(fallbackEnd) && fallbackEnd > fallbackStart + 1e-4) {
    return { startDepthMm: fallbackStart, endDepthMm: fallbackEnd };
  }
  return null;
}

function buildContactIntervalsForUnity(probe, depthMm) {
  const rows = Array.isArray(probe?.contactIntervals) ? probe.contactIntervals : [];
  const out = [];
  rows.forEach((row) => {
    const start = clampDepthMm(Number(row?.startDepthMm ?? row?.dStartUm / 1000), depthMm);
    const end = clampDepthMm(Number(row?.endDepthMm ?? row?.dEndUm / 1000), depthMm);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start + 1e-4) return;
    out.push({
      startDepthMm: start,
      endDepthMm: end,
      key: String(row?.key || row?.regionKey || '').trim(),
      label: String(row?.label || row?.name || '').trim(),
      contactStrength: Number.isFinite(Number(row?.contactStrength)) ? Number(row.contactStrength) : 0,
      lengthUm: Number.isFinite(Number(row?.lengthUm))
        ? Number(row.lengthUm)
        : Number.isFinite(Number(row?.sampleCount))
          ? Number(row.sampleCount) * 25
          : Math.round((end - start) * 1000),
    });
  });
  return out
    .sort((a, b) => (b.contactStrength || 0) - (a.contactStrength || 0))
    .slice(0, 12);
}

function buildRiskSegmentsForUnity(probe, depthMm) {
  const diag = probe?.riskDiagnostics && typeof probe.riskDiagnostics === 'object'
    ? probe.riskDiagnostics
    : {};
  const rows = Array.isArray(diag?.dangerSegmentsTop3)
    ? diag.dangerSegmentsTop3
    : Array.isArray(diag?.thresholdSegmentsMm)
      ? diag.thresholdSegmentsMm
      : [];

  const out = [];
  rows.forEach((row) => {
    const start = clampDepthMm(Number(row?.startDepthMm), depthMm);
    const end = clampDepthMm(Number(row?.endDepthMm), depthMm);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start + 1e-4) return;
    const thresholdMm = Number(
      row?.thresholdMm ??
        row?.classThresholdMm ??
        diag?.classThresholdMm?.[row?.sourceClass] ??
        diag?.classThresholdMm?.default ??
        probe?.minRiskDistanceMm ??
        0.35,
    );
    const minDistanceMm = Number(
      row?.minDistanceMm ??
        row?.minDistMm ??
        probe?.minRiskDistanceMm ??
        diag?.minDistanceMm ??
        0,
    );
    out.push({
      startDepthMm: start,
      endDepthMm: end,
      sourceClass: String(
        row?.sourceClass ||
          row?.classLabel ||
          diag?.nearestSourceClass ||
          diag?.nearestSource ||
          'risk',
      ).trim(),
      minDistanceMm: Number.isFinite(minDistanceMm) ? minDistanceMm : 0,
      thresholdMm: Number.isFinite(thresholdMm) ? thresholdMm : 0.35,
      severity: Number.isFinite(thresholdMm) && Number.isFinite(minDistanceMm)
        ? thresholdMm - minDistanceMm
        : 0,
    });
  });
  return out
    .sort((a, b) => (b.severity || 0) - (a.severity || 0))
    .slice(0, 3);
}

function finiteTriplet(value) {
  if (!Array.isArray(value) || value.length < 3) return null;
  const triplet = value.slice(0, 3).map(Number);
  return triplet.every(Number.isFinite) ? triplet : null;
}

function deriveNativeEndpointGeometry(probe) {
  const entry = finiteTriplet(probe?.entry_xyz) ?? finiteTriplet(probe?.entry);
  const tip = finiteTriplet(probe?.tip_xyz) ?? finiteTriplet(probe?.tip);
  if (!entry || !tip) return null;

  const delta = [tip[0] - entry[0], tip[1] - entry[1], tip[2] - entry[2]];
  const depthUm = Math.hypot(delta[0], delta[1], delta[2]);
  if (!Number.isFinite(depthUm) || depthUm <= 1e-6) return null;

  const dir = delta.map((v) => v / depthUm);
  const thetaDeg = (Math.acos(clamp(dir[1], -1, 1)) * 180) / Math.PI;
  const phiDeg = (Math.atan2(dir[2], dir[0]) * 180) / Math.PI;

  return {
    ap_mm: (BREGMA_UM[0] - entry[0]) / 1000,
    ml_mm: (entry[2] - BREGMA_UM[2]) / 1000,
    theta_deg: thetaDeg,
    phi_deg: phiDeg,
    depthUm,
    entryDvCorr_mm: (entry[1] - BREGMA_UM[1]) / 1000,
    entry_xyz: entry,
    tip_xyz: tip,
    entry,
    tip,
    dir,
  };
}

function toUnityProbe(probe, probePreset, probeSpec) {
  const endpointGeometry = deriveNativeEndpointGeometry(probe);
  const thetaDeg = endpointGeometry?.theta_deg ??
    probe.theta_deg ??
    (typeof probe.elevation === 'number' ? 90 - probe.elevation : 0);

  const depthUm =
    endpointGeometry?.depthUm ??
    probe.depthUm ??
    (typeof probe.depth === 'number' ? probe.depth * 1000 : (probeSpec?.depth || 4) * 1000);
  const depthMm = Math.max(0, Number(depthUm) / 1000);
  const activeSegment = deriveActiveSegmentMm(probe, depthMm);
  const contactIntervals = buildContactIntervalsForUnity(probe, depthMm);
  const riskSegments = buildRiskSegmentsForUnity(probe, depthMm);
  const presetSpec = PROBE_PRESETS[probe?.probeType ?? probePreset] || PROBE_PRESETS[probePreset] || {};
  const shanks = Number(probe.shanks ?? probe.shankCount ?? probeSpec?.shanks ?? presetSpec.shanks ?? 1);
  const shankPitchUm = Number(
    probe.shankPitchUm ??
      probe.shankSpacingUm ??
      probeSpec?.shankPitchUm ??
      probeSpec?.shankSpacingUm ??
      presetSpec.shankPitchUm ??
      (shanks > 1 ? 250 : 0),
  );
  const shankWidthUm = Number(probe.shankWidthUm ?? probeSpec?.shankWidthUm ?? presetSpec.shankWidthUm ?? 70);
  const radiusUm = Number(probe.radiusUm ?? probe.probeRadiusUm ?? probeSpec?.radiusUm ?? Math.max(5, shankWidthUm / 2));

  return {
    ap_mm: endpointGeometry?.ap_mm ?? probe.ap ?? probe.ap_mm ?? 0,
    ml_mm: endpointGeometry?.ml_mm ?? probe.ml ?? probe.ml_mm ?? 0,
    theta_deg: thetaDeg,
    phi_deg: endpointGeometry?.phi_deg ?? probe.phi_deg ?? probe.azimuth ?? 0,
    depthUm,
    entryDvCorr_mm: endpointGeometry?.entryDvCorr_mm ?? probe.entryDvCorr_mm ?? probe.dvEntry ?? 0,
    nativeEndpointGeometry: Boolean(endpointGeometry),
    entry_xyz: endpointGeometry?.entry_xyz ?? finiteTriplet(probe?.entry_xyz) ?? null,
    tip_xyz: endpointGeometry?.tip_xyz ?? finiteTriplet(probe?.tip_xyz) ?? null,
    entry: endpointGeometry?.entry ?? finiteTriplet(probe?.entry) ?? finiteTriplet(probe?.entry_xyz) ?? null,
    tip: endpointGeometry?.tip ?? finiteTriplet(probe?.tip) ?? finiteTriplet(probe?.tip_xyz) ?? null,
    dir: endpointGeometry?.dir ?? finiteTriplet(probe?.dir) ?? null,
    probeType: probe.probeType ?? probePreset,
    shanks: Number.isFinite(shanks) ? Math.max(1, Math.round(shanks)) : 1,
    shankCount: Number.isFinite(shanks) ? Math.max(1, Math.round(shanks)) : 1,
    shankPitchUm: Number.isFinite(shankPitchUm) ? shankPitchUm : 0,
    shankSpacingUm: Number.isFinite(shankPitchUm) ? shankPitchUm : 0,
    shankWidthUm: Number.isFinite(shankWidthUm) ? shankWidthUm : 70,
    radiusUm: Number.isFinite(radiusUm) ? radiusUm : 35,
    probeRadiusUm: Number.isFinite(radiusUm) ? radiusUm : 35,
    coveredRegions: probe.coveredRegions ?? probe.regionsHit ?? [],
    uncertainty: probe.uncertainty ?? null,
    activeStartDepthMm: activeSegment?.startDepthMm ?? null,
    activeEndDepthMm: activeSegment?.endDepthMm ?? null,
    contactIntervals,
    riskSegments,
    minRiskDistanceMm: Number.isFinite(Number(probe?.minRiskDistanceMm))
      ? Number(probe.minRiskDistanceMm)
      : Number.isFinite(Number(probe?.riskDiagnostics?.minDistanceMm))
        ? Number(probe.riskDiagnostics.minDistanceMm)
        : null,
  };
}

function validateRegions(regions) {
  if (!Array.isArray(regions)) {
    return { ok: false, reason: 'selectedRegions must be an array' };
  }
  for (const key of regions) {
    if (typeof key !== 'string' || !REGION_KEY_PATTERN.test(key)) {
      return { ok: false, reason: `invalid region key: ${String(key)}` };
    }
  }
  return { ok: true };
}

function validateProbes(probes) {
  if (!Array.isArray(probes)) {
    return { ok: false, reason: 'probes must be an array' };
  }
  for (let i = 0; i < probes.length; i += 1) {
    const p = probes[i];
    const finite =
      Number.isFinite(p.ap_mm) &&
      Number.isFinite(p.ml_mm) &&
      Number.isFinite(p.theta_deg) &&
      Number.isFinite(p.phi_deg) &&
      Number.isFinite(p.depthUm) &&
      Number.isFinite(p.entryDvCorr_mm);
    if (!finite) {
      return { ok: false, reason: `probe[${i}] contains non-finite numbers` };
    }
    if (p.nativeEndpointGeometry && (!finiteTriplet(p.entry_xyz) || !finiteTriplet(p.tip_xyz))) {
      return { ok: false, reason: `probe[${i}] native endpoints are invalid` };
    }
    if (p.depthUm <= 0 || p.depthUm > 15000) {
      return { ok: false, reason: `probe[${i}] depthUm out of range` };
    }
  }
  return { ok: true };
}

function parseJSONSafely(raw, fallback = null) {
  if (typeof raw !== 'string') return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function getRegionKeyFromPayload(payload) {
  if (typeof payload === 'string' && REGION_KEY_PATTERN.test(payload)) return payload;
  if (!payload || typeof payload !== 'object') return null;
  const candidate = payload.key ?? payload.region ?? payload.regionKey ?? payload.id ?? null;
  return typeof candidate === 'string' && REGION_KEY_PATTERN.test(candidate) ? candidate : null;
}

function parseHemisphere(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const raw = String(payload.hemisphere ?? payload.side ?? '').trim().toLowerCase();
  if (raw === 'left' || raw === 'l') return 'left';
  if (raw === 'right' || raw === 'r') return 'right';
  return null;
}

function normalizeRegionCategory(value) {
  if (!value) return 'atlas';
  const raw = String(value).trim().toLowerCase();
  if (!raw) return 'atlas';
  if (raw.includes('visual') || raw === 'vis') return 'visual';
  if (raw.includes('hippocampus') || raw.includes('hippo')) return 'hippocampus';
  if (raw.includes('thalam')) return 'thalamus';
  if (raw.includes('frontal') || raw.includes('isocortex')) return 'frontal';
  if (raw.includes('midbrain') || raw.includes('tect')) return 'midbrain';
  if (raw.includes('basal') || raw.includes('striat') || raw.includes('ganglia')) return 'basal_ganglia';
  return raw;
}

function normalizeRegionGroup(value) {
  if (!value) return '';
  const raw = String(value).trim().toLowerCase();
  if (!raw) return '';
  if (raw.includes('visual')) return 'visual';
  if (raw.includes('hippocamp')) return 'hippocampus';
  if (raw.includes('thalam')) return 'thalamus';
  if (raw.includes('frontal') || raw.includes('isocortex')) return 'frontal';
  if (raw.includes('midbrain')) return 'midbrain';
  if (raw.includes('basal') || raw.includes('ganglia') || raw.includes('striat')) return 'basal_ganglia';
  return raw.replace(/\s+/g, '_');
}

function keyVariantsForLookup(key) {
  if (typeof key !== 'string' || !key.trim()) return [];
  const source = key.trim();
  const variants = new Set([source]);
  variants.add(source.replace(/[\s_](L|R)$/i, ''));
  variants.add(source.replace(/(L|R)$/i, ''));
  variants.add(source.replace(/-(L|R)$/i, ''));
  variants.add(source.replace(/\s+(left|right)$/i, ''));
  return Array.from(variants).filter(Boolean);
}

function getFullscreenElement() {
  return (
    document.fullscreenElement ||
    document.webkitFullscreenElement ||
    document.mozFullScreenElement ||
    document.msFullscreenElement ||
    null
  );
}

async function requestFullscreenSafe(el) {
  if (!el) return;
  if (el.requestFullscreen) {
    await el.requestFullscreen();
    return;
  }
  if (el.webkitRequestFullscreen) {
    el.webkitRequestFullscreen();
    return;
  }
  if (el.mozRequestFullScreen) {
    el.mozRequestFullScreen();
    return;
  }
  if (el.msRequestFullscreen) {
    el.msRequestFullscreen();
  }
}

async function exitFullscreenSafe() {
  if (document.exitFullscreen) {
    await document.exitFullscreen();
    return;
  }
  if (document.webkitExitFullscreen) {
    document.webkitExitFullscreen();
    return;
  }
  if (document.mozCancelFullScreen) {
    document.mozCancelFullScreen();
    return;
  }
  if (document.msExitFullscreen) {
    document.msExitFullscreen();
  }
}

function flipHemisphere(hemi) {
  if (hemi === 'left') return 'right';
  if (hemi === 'right') return 'left';
  return hemi;
}

function vecSub(a, b) {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function vecDot(a, b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function vecCross(a, b) {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

function vecLength(v) {
  return Math.hypot(v.x, v.y, v.z);
}

function vecNormalize(v) {
  const len = vecLength(v);
  if (len < 1e-6) return { x: 0, y: 0, z: 0 };
  return { x: v.x / len, y: v.y / len, z: v.z / len };
}

function getCameraBasis(camera) {
  const sinPhi = Math.sin(camera.phi);
  const pos = {
    x: CAMERA_PIVOT.x + camera.distance * sinPhi * Math.cos(camera.theta),
    y: CAMERA_PIVOT.y + camera.distance * Math.cos(camera.phi),
    z: CAMERA_PIVOT.z + camera.distance * sinPhi * Math.sin(camera.theta),
  };

  const forward = vecNormalize(vecSub(CAMERA_PIVOT, pos));
  let worldUp = { x: 0, y: 1, z: 0 };
  let right = vecCross(forward, worldUp);
  if (vecLength(right) < 1e-5) {
    worldUp = { x: 0, y: 0, z: 1 };
    right = vecCross(forward, worldUp);
  }
  right = vecNormalize(right);
  const up = vecNormalize(vecCross(right, forward));
  return { pos, forward, right, up };
}

function projectPoint(world, basis, width, height, fovDeg = UNITY_FOV_DEG) {
  if (width <= 0 || height <= 0) return null;
  const rel = vecSub(world, basis.pos);
  const xCam = vecDot(rel, basis.right);
  const yCam = vecDot(rel, basis.up);
  const zCam = vecDot(rel, basis.forward);
  if (zCam <= 0.001) return null;

  const aspect = width / height;
  const focal = 1 / Math.tan((fovDeg * Math.PI) / 360);
  const ndcX = (xCam / zCam) * (focal / aspect);
  const ndcY = (yCam / zCam) * focal;
  const x = (ndcX * 0.5 + 0.5) * width;
  const y = (1 - (ndcY * 0.5 + 0.5)) * height;
  return { x, y, depth: zCam };
}

function regionToWorld(reg, hemisphere, invertLr = false) {
  const mlAbs = Math.abs(reg.ml ?? 0);
  const mlSign = hemisphere === 'right' ? 1 : -1;
  const ml = mlAbs * (invertLr ? -mlSign : mlSign);
  // Allen mesh vertices are loaded with z = -(AP - bregma) / 1000 in Unity.
  // So AP must be negated for overlay projection to align with rendered meshes.
  return { x: ml, y: -(reg.dv ?? 0), z: -(reg.ap ?? 0) };
}

function getAnchorRadius(depth) {
  return clamp(320 / (depth + 6), 12, 28);
}

function axisCoordKey(axis) {
  if (axis === 'x') return 'ml';
  if (axis === 'z') return 'ap';
  return 'dv';
}

function axisCoordValue(axis, worldCoord) {
  if (axis === 'x') return worldCoord;
  return -worldCoord;
}

function formatMm(value) {
  const v = Number(value || 0);
  const abs = Math.abs(v);
  if (abs >= 10) return v.toFixed(1);
  if (abs >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

function axisValueOfPoint(point, axis) {
  if (axis === 'x') return point.x;
  if (axis === 'z') return point.z;
  return point.y;
}

function probePointAtDepth(probe, depthUm, invertLr = false) {
  if (!probe || !Number.isFinite(depthUm)) return null;
  const thetaDeg = Number(probe.theta_deg);
  const phiDeg = Number(probe.phi_deg);
  if (!Number.isFinite(thetaDeg) || !Number.isFinite(phiDeg)) return null;

  const thetaRad = (thetaDeg * Math.PI) / 180;
  const phiRad = (phiDeg * Math.PI) / 180;
  const dirAp = Math.sin(thetaRad) * Math.cos(phiRad);
  const dirDv = Math.cos(thetaRad);
  const dirMl = Math.sin(thetaRad) * Math.sin(phiRad);

  let entryX = Number(probe.ml_mm ?? 0);
  const entryY = -Number(probe.entryDvCorr_mm ?? 0);
  const entryZ = -Number(probe.ap_mm ?? 0);
  let dirX = dirMl;
  const dirY = -dirDv;
  const dirZ = -dirAp;

  if (invertLr) {
    entryX *= -1;
    dirX *= -1;
  }

  const scale = Number(depthUm) / 1000;
  return {
    x: entryX + dirX * scale,
    y: entryY + dirY * scale,
    z: entryZ + dirZ * scale,
  };
}

function isPointVisibleInSlice(point, axis, coord, mode, thicknessMm) {
  const value = axisValueOfPoint(point, axis);
  if (mode === 'slab') {
    const half = Math.max(0.0005, Number(thicknessMm || 0) * 0.5);
    return Math.abs(value - coord) <= half;
  }
  return value <= coord;
}

function normalizeProbeSegment(probe) {
  const thetaDeg = Number(probe?.theta_deg ?? 0);
  const phiDeg = Number(probe?.phi_deg ?? 0);
  const depthMm = Number(probe?.depthUm ?? 0) / 1000;
  const entry = {
    x: Number(probe?.ml_mm ?? 0),
    y: -Number(probe?.entryDvCorr_mm ?? 0),
    z: -Number(probe?.ap_mm ?? 0),
  };
  const theta = (thetaDeg * Math.PI) / 180;
  const phi = (phiDeg * Math.PI) / 180;
  const dir = {
    x: Math.sin(theta) * Math.sin(phi),
    y: -Math.cos(theta),
    z: -Math.sin(theta) * Math.cos(phi),
  };
  const tip = {
    x: entry.x + dir.x * depthMm,
    y: entry.y + dir.y * depthMm,
    z: entry.z + dir.z * depthMm,
  };
  return { entry, tip };
}

function probeIntersectsSlice(probe, axis, coord, mode, thicknessMm) {
  const { entry, tip } = normalizeProbeSegment(probe);
  const a = axisValueOfPoint(entry, axis);
  const b = axisValueOfPoint(tip, axis);
  const segMin = Math.min(a, b);
  const segMax = Math.max(a, b);
  if (mode === 'slab') {
    const half = Math.max(0.0005, Number(thicknessMm || 0) * 0.5);
    return segMax >= coord - half && segMin <= coord + half;
  }
  return segMin <= coord;
}

async function annotateAndDownloadScreenshot(dataUrl, fileName, lines = []) {
  const img = new Image();
  img.decoding = 'async';
  img.src = dataUrl;
  await img.decode();
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  if (lines.length > 0) {
    const textLines = lines.filter(Boolean).slice(0, 6);
    const padding = 14;
    const lineH = 20;
    const boxW = Math.min(canvas.width - 20, 520);
    const boxH = padding * 2 + lineH * textLines.length;
    ctx.fillStyle = 'rgba(2, 22, 36, 0.72)';
    ctx.strokeStyle = 'rgba(63, 198, 255, 0.68)';
    ctx.lineWidth = 1.5;
    ctx.fillRect(10, 10, boxW, boxH);
    ctx.strokeRect(10, 10, boxW, boxH);
    ctx.fillStyle = '#c7f2ff';
    ctx.font = '600 15px "Segoe UI", "Microsoft YaHei", sans-serif';
    textLines.forEach((line, idx) => {
      ctx.fillText(line, 18, 10 + padding + lineH * (idx + 0.72));
    });
  }
  const link = document.createElement('a');
  link.href = canvas.toDataURL('image/png');
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function csvEscape(value) {
  if (value === null || value === undefined) return '';
  const text = Array.isArray(value) ? value.join('|') : String(value);
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function downloadTextFile(fileName, text, mime = 'text/csv;charset=utf-8') {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function declutterAnchors(anchors, width, height) {
  if (!Array.isArray(anchors) || !anchors.length) return [];
  const placed = [];
  const sorted = [...anchors].sort((a, b) => a.depth - b.depth);
  const minX = 24;
  const maxX = Math.max(minX, width - 24);
  const minY = 22;
  const maxY = Math.max(minY, height - 22);

  return sorted.map((anchor) => {
    let x = clamp(anchor.x, minX, maxX);
    let y = clamp(anchor.y, minY, maxY);
    let attempt = 0;
    while (attempt < 20) {
      let collision = false;
      for (let i = 0; i < placed.length; i += 1) {
        const p = placed[i];
        if (Math.abs(p.x - x) < 62 && Math.abs(p.y - y) < 24) {
          collision = true;
          break;
        }
      }
      if (!collision) break;
      const ring = Math.floor(attempt / 2) + 1;
      const sign = attempt % 2 === 0 ? 1 : -1;
      y = clamp(y + sign * ring * 8, minY, maxY);
      if (attempt % 3 === 0) x = clamp(x + sign * 8, minX, maxX);
      attempt += 1;
    }
    placed.push({ x, y });
    return { ...anchor, displayX: x, displayY: y };
  });
}

function normalizeTheta(theta) {
  let next = theta % (Math.PI * 2);
  if (next < 0) next += Math.PI * 2;
  return next;
}

function angularDistance(a, b) {
  const d = Math.abs(a - b) % (Math.PI * 2);
  return d > Math.PI ? (Math.PI * 2 - d) : d;
}

function inferViewFromPose(theta, phi, distance) {
  const th = normalizeTheta(theta);
  const candidate = CAMERA_VIEWS.find((view) => {
    const preset = CAMERA_PRESETS[view];
    return (
      angularDistance(th, normalizeTheta(preset.theta)) <= 0.06 &&
      Math.abs(phi - preset.phi) <= 0.06 &&
      Math.abs(distance - preset.distance) <= 1.2
    );
  });
  return candidate || 'custom';
}

function useDragOffset(initial = { x: 0, y: 0 }) {
  const [offset, setOffset] = useState(initial);
  const dragRef = useRef(null);

  const onHandlePointerDown = useCallback((e) => {
    if (e.button !== 0 && e.pointerType !== 'touch') return;
    e.preventDefault();
    e.stopPropagation();

    const start = { x: e.clientX, y: e.clientY };
    const origin = { ...offset };
    dragRef.current = { start, origin, pointerId: e.pointerId };

    const onMove = (moveEvent) => {
      if (!dragRef.current) return;
      setOffset({
        x: dragRef.current.origin.x + (moveEvent.clientX - dragRef.current.start.x),
        y: dragRef.current.origin.y + (moveEvent.clientY - dragRef.current.start.y),
      });
    };

    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerup', onUp, { once: true });
    window.addEventListener('pointercancel', onUp, { once: true });
  }, [offset]);

  return { offset, onHandlePointerDown };
}

export default function Brain3DView({
  probes = [],
  selectedRegions = [],
  probePreset = 'NP2.0-4sh',
  probeSpec = { depth: 4 },
  highlightIdx = null,
  brainRegions = {},
  focusedRegion = null,
  sideCoverage = null,
  onToggleRegion = null,
  onRegionPicked = null,
  backendBaseUrl = '',
  authToken = '',
  lang = 'cn',
  backendStatus = 'idle',
  activeAtlasMode = 'allen_sdk',
  mriConfig = null,
  mriLayerStatus = null,
  mriSurfaceMeshes = null,
  onBridgeStageChange = null,
}) {
  const iframeRef = useRef(null);
  const viewportRef = useRef(null);
  const handshakeTimerRef = useRef(null);
  const highlightStateRef = useRef(new Set());
  const commandVersionRef = useRef(0);
  const commandQueueRef = useRef(new Map());
  const commandFlushTimerRef = useRef(null);
  const ackedVersionByMethodRef = useRef(new Map());
  const unityCapabilitiesRef = useRef(normalizeUnityCapabilities(null));
  const bridgeMetricsRef = useRef({
    startedAt: Date.now(),
    queued: 0,
    sent: 0,
    dropped: 0,
    skipped: 0,
    acked: 0,
    bytesSent: 0,
    largePayloads: 0,
    flushes: 0,
    lastFlushAt: 0,
    lastPublishedAt: 0,
    maxPayloadBytes: 0,
    lastAckLatencyMs: null,
  });
  const heartbeatAckAtRef = useRef(Date.now());
  const bridgeStaleRef = useRef(false);
  const bridgeSyncClearTimerRef = useRef(null);
  const lastResyncAtRef = useRef(0);
  const fullSnapshotNeedsCameraRef = useRef(true);
  const cameraDragActiveRef = useRef(false);
  const suppressPoseUntilRef = useRef(0);
  const pushFullStateSnapshotRef = useRef(null);
  const bridgeReadyFallbackTimerRef = useRef(null);
  const instanceReadyAtRef = useRef(0);
  const bridgeFallbackPromotedRef = useRef(false);
  const templateBootstrapRef = useRef(false);
  const fillNoticeTimerRef = useRef(null);
  const sliceTrackRef = useRef(null);
  const sliceDragCleanupRef = useRef(null);
  const sliceScreenshotMetaRef = useRef(null);
  const sliceSnapHintTimerRef = useRef(null);
  const screenshotExportQueueRef = useRef([]);
  const screenshotExportBusyRef = useRef(false);
  const screenshotExportPumpTimerRef = useRef(null);
  const meshStallWatchRef = useRef({ loaded: -1, total: -1, at: Date.now() });
  const contactOverlaySentRef = useRef(false);
  const mriAppearanceInitRef = useRef(false);
  const mriVisualProfileBackupRef = useRef(null);
  const mriVisualProfileAppliedRef = useRef(false);

  const [instanceReady, setInstanceReady] = useState(false);
  const [bridgeReady, setBridgeReady] = useState(false);
  const [bridgeSyncState, setBridgeSyncState] = useState('ok');
  const [unityBridgeInfo, setUnityBridgeInfo] = useState(() => summarizeUnityCapabilities(unityCapabilitiesRef.current));
  const [snapshotSeq, setSnapshotSeq] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [missingBuild, setMissingBuild] = useState(false);
  const [runtimeError, setRuntimeError] = useState('');
  const [contractError, setContractError] = useState('');
  const [fillNotice, setFillNotice] = useState('');
  const [opacitySlider, setOpacitySlider] = useState(38);
  const [outlineProgress, setOutlineProgress] = useState({ loaded: 0, total: 0 });
  const [catalogSource, setCatalogSource] = useState('unknown');
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [clinicalFocus, setClinicalFocus] = useState(true);
  const [hemisphereFilter, setHemisphereFilter] = useState('all');
  const [showShell, setShowShell] = useState(false);
  const [showAxes, setShowAxes] = useState(false);
  const [showProbeGuides, setShowProbeGuides] = useState(false);
  const [showUncertainty, setShowUncertainty] = useState(false);
  const [useMriAppearance, setUseMriAppearance] = useState(true);
  const [mriAppearanceLayers, setMriAppearanceLayers] = useState(DEFAULT_MRI_APPEARANCE_LAYERS);
  const [useWeightedOit, setUseWeightedOit] = useState(true);
  const [oitWeight, setOitWeight] = useState(1.0);
  const [coronalSlice, setCoronalSlice] = useState(1.0);
  const [sliceAxisValues, setSliceAxisValues] = useState({ x: 1.0, y: 1.0, z: 1.0 });
  const [sliceAxis, setSliceAxis] = useState('y');
  const [sliceMode, setSliceMode] = useState('below');
  const [sliceThicknessMm, setSliceThicknessMm] = useState(0.3);
  const [sliceSnapEnabled, setSliceSnapEnabled] = useState(true);
  const [sliceSnapThresholdMm, setSliceSnapThresholdMm] = useState(0.18);
  const [sliceFilterProbes, setSliceFilterProbes] = useState(true);
  const [sliceDragActive, setSliceDragActive] = useState(false);
  const [sliceSnapHint, setSliceSnapHint] = useState({
    active: false,
    key: '',
    distanceMm: null,
    coord: null,
    axis: 'y',
  });
  const [slicePresets, setSlicePresets] = useState([]);
  const [contactDisplayMode, setContactDisplayMode] = useState('slice_contact_persist');
  const [contactDisplayStyle, setContactDisplayStyle] = useState('hybrid');
  const [contactMinLengthUm, setContactMinLengthUm] = useState(120);
  const [contactProfileData, setContactProfileData] = useState({
    probes: [],
    contactRegionSet: [],
    samplingStepUm: 25,
    minContactLengthUm: 0,
    contactMode: 'cylinder',
    atlasHash: '',
  });
  const [contactProfileLoading, setContactProfileLoading] = useState(false);
  const [contactProfileError, setContactProfileError] = useState('');
  const [activeContactIntervalId, setActiveContactIntervalId] = useState('');
  const [postFxPreset, setPostFxPreset] = useState('pinpoint');
  const [antiAliasingMode, setAntiAliasingMode] = useState('taa');
  const [postFxEnabled, setPostFxEnabled] = useState(true);
  const [ambientOcclusionEnabled, setAmbientOcclusionEnabled] = useState(true);
  const [environmentLighting, setEnvironmentLighting] = useState(TARGET_TEMPLATE_ENV_LIGHT);
  const [invertLr, setInvertLr] = useState(true);
  const [cameraLocked, setCameraLocked] = useState(false);
  const [activeTemplate, setActiveTemplate] = useState('target');
  const [layerVisibility, setLayerVisibility] = useState({ ...TARGET_TEMPLATE_LAYER_VISIBILITY });
  const [classVisibility, setClassVisibility] = useState(() => ({ ...TARGET_TEMPLATE_CLASS_VISIBILITY }));
  const [recentPick, setRecentPick] = useState(null);
  const [regionSideHints, setRegionSideHints] = useState({});
  const [streamStage, setStreamStage] = useState({
    stage: '',
    progress: 0,
    detail: '',
    loaded: 0,
    total: 0,
  });
  const isMriHybridMode = useMemo(
    () => String(activeAtlasMode || '').trim().toLowerCase() === 'mri_ccf_hybrid',
    [activeAtlasMode],
  );
  const mriAppearanceEnabled = Boolean(isMriHybridMode && useMriAppearance);
  const mriAppearancePayload = useMemo(
    () => buildMriAppearancePayload(
      mriAppearanceEnabled,
      mriAppearanceLayers,
      activeAtlasMode,
      isMriHybridMode,
    ),
    [activeAtlasMode, isMriHybridMode, mriAppearanceEnabled, mriAppearanceLayers],
  );
  const mriLayerExplain = useMemo(() => {
    if (!isMriHybridMode) return null;
    const row = mriLayerStatus && typeof mriLayerStatus === 'object' ? mriLayerStatus : {};
    const risk = row.riskModel && typeof row.riskModel === 'object' ? row.riskModel : {};
    const toBool = (...values) => values.some((value) => (
      value === true
      || value === 1
      || String(value).trim().toLowerCase() === 'true'
      || String(value).trim().toLowerCase() === '1'
      || String(value).trim().toLowerCase() === 'on'
      || String(value).trim().toLowerCase() === 'enabled'
    ));
    return {
      // The visible MRI appearance switch is the UI source of truth; backend echo can lag.
      appearanceActive: toBool(mriAppearanceEnabled, row.appearanceActive, row.useMriAppearance, row.enabled),
      structured: toBool(
        row.riskLayerStructuredMaskEnabled,
        row.structuredMaskEnabled,
        row.structuredMaskActive,
        risk.structuredMaskEnabled,
        risk.structuredMaskActive,
      ),
      sdf: toBool(
        row.riskLayerSdfEnabled,
        row.sdfEnabled,
        row.sdfActive,
        risk.sdfEnabled,
        risk.sdfActive,
      ),
      distanceModel: String(row.riskLayerDistanceModel || row.distanceModel || risk.distanceModel || '--'),
      surfaceReady: mriSurfaceMeshes && typeof mriSurfaceMeshes === 'object'
        ? Boolean(mriSurfaceMeshes.ready)
        : null,
    };
  }, [isMriHybridMode, mriAppearanceEnabled, mriLayerStatus, mriSurfaceMeshes]);
  const mriSurfaceLayerReady = useMemo(() => {
    const out = {};
    const status = mriSurfaceMeshes && typeof mriSurfaceMeshes === 'object' ? mriSurfaceMeshes : null;
    const hasStatus = Boolean(
      status
      && (
        typeof status.ready === 'boolean'
        || Array.isArray(status.missingLayers)
        || (status.layers && typeof status.layers === 'object')
      ),
    );
    Object.entries(MRI_SURFACE_LAYER_BY_UI_KEY).forEach(([uiKey, apiLayer]) => {
      if (!hasStatus) {
        out[uiKey] = true;
        return;
      }
      const layers = status.layers && typeof status.layers === 'object' ? status.layers : {};
      const missing = new Set(Array.isArray(status.missingLayers) ? status.missingLayers : []);
      const layer = layers[apiLayer] && typeof layers[apiLayer] === 'object' ? layers[apiLayer] : null;
      out[uiKey] = Boolean(layer?.ready) && !missing.has(apiLayer);
    });
    return out;
  }, [mriSurfaceMeshes]);
  const [cameraState, setCameraState] = useState({
    ...CAMERA_PRESETS.iso,
    view: 'iso',
  });
  const tr = useCallback((key) => t(key, lang), [lang]);
  const catalogDisplay = useMemo(() => {
    const isCn = String(lang || '').toLowerCase().startsWith('zh') || String(lang || '').toLowerCase() === 'cn';
    const mapCn = {
      backend_unity_all: '\u5168\u8111\u76ee\u5f55',
      backend_unity_all_refresh: '\u5168\u8111\u76ee\u5f55(\u5237\u65b0)',
      backend_all: '\u540e\u7aef\u5168\u76ee\u5f55',
      backend_planner: '\u89c4\u5212\u5b50\u96c6',
      default: '\u9ed8\u8ba4\u76ee\u5f55',
      unknown: '\u672a\u77e5',
    };
    const mapEn = {
      backend_unity_all: 'Full Brain Catalog',
      backend_unity_all_refresh: 'Full Brain Catalog (Refreshed)',
      backend_all: 'Backend Full Catalog',
      backend_planner: 'Planner Subset',
      default: 'Default Catalog',
      unknown: 'Unknown',
    };
    const display = (isCn ? mapCn : mapEn)[catalogSource] || catalogSource;
    return `${isCn ? '\u76ee\u5f55' : 'Catalog'}: ${display}`;
  }, [catalogSource, lang]);
  const gizmoDrag = useDragOffset({ x: 0, y: 0 });
  const selectedPanelDrag = useDragOffset({ x: -136, y: 24 });
  const sliceRanges = useMemo(() => {
    const seed = {
      x: { min: -6, max: 6 },
      y: { min: -9, max: 1 },
      z: { min: -6, max: 6 },
    };
    const keys = brainRegions && typeof brainRegions === 'object' ? Object.keys(brainRegions) : [];
    let hasAny = false;
    keys.forEach((key) => {
      const reg = brainRegions[key];
      if (!reg || !Number.isFinite(reg.ap) || !Number.isFinite(reg.dv) || !Number.isFinite(reg.ml)) return;
      hasAny = true;
      const mlAbs = Math.abs(reg.ml);
      const xVals = [-mlAbs, mlAbs];
      const yVal = -reg.dv;
      const zVal = -reg.ap;
      xVals.forEach((v) => {
        seed.x.min = Math.min(seed.x.min, v);
        seed.x.max = Math.max(seed.x.max, v);
      });
      seed.y.min = Math.min(seed.y.min, yVal);
      seed.y.max = Math.max(seed.y.max, yVal);
      seed.z.min = Math.min(seed.z.min, zVal);
      seed.z.max = Math.max(seed.z.max, zVal);
    });
    if (!hasAny) return seed;
    const pad = 0.5;
    return {
      x: { min: seed.x.min - pad, max: seed.x.max + pad },
      y: { min: seed.y.min - pad, max: seed.y.max + pad },
      z: { min: seed.z.min - pad, max: seed.z.max + pad },
    };
  }, [brainRegions]);
  const activeSliceRange = sliceRanges[sliceAxis] || sliceRanges.y;
  const activeSliceCoord =
    activeSliceRange.min + clamp(coronalSlice, 0, 1) * (activeSliceRange.max - activeSliceRange.min);
  const sliceEnabled = coronalSlice < 0.9995;
  const sliceAxisCoord = axisCoordValue(sliceAxis, activeSliceCoord);
  const sliceAxisCoordKey = axisCoordKey(sliceAxis);

  const setActiveSliceNormalized = useCallback((value, axisOverride = null) => {
    const axisKey = SLICE_AXES.includes(axisOverride) ? axisOverride : sliceAxis;
    const next = clamp(Number(value), 0, 1);
    setCoronalSlice(next);
    setSliceAxisValues((prev) => ({ ...prev, [axisKey]: next }));
  }, [sliceAxis]);

  const handleSelectSliceAxis = useCallback((axis) => {
    if (!SLICE_AXES.includes(axis)) return;
    const current = clamp(coronalSlice, 0, 1);
    const stored = Number(sliceAxisValues?.[axis]);
    setSliceAxisValues((prev) => ({ ...prev, [sliceAxis]: current }));
    setSliceAxis(axis);
    setCoronalSlice(Number.isFinite(stored) ? clamp(stored, 0, 1) : current);
  }, [coronalSlice, sliceAxis, sliceAxisValues]);

  const selectedRegionsSignature = JSON.stringify(Array.isArray(selectedRegions) ? selectedRegions : []);
  const selectedRegionSet = useMemo(
    () => new Set(Array.isArray(selectedRegions) ? selectedRegions : []),
    [selectedRegions],
  );
  const regionDisplayMetaByKey = useMemo(() => {
    const map = new Map();
    if (!brainRegions || typeof brainRegions !== 'object') return map;
    Object.entries(brainRegions).forEach(([key, meta]) => {
      if (!key || !meta || typeof meta !== 'object') return;
      const normalizedGroup =
        normalizeRegionGroup(
          meta.groupKey || meta.group || meta.groupName || meta.groupNameCn || meta.system || '',
        ) || '';
      const normalizedCategory = normalizeRegionCategory(meta.category || meta.system || meta.group || 'atlas');
      const { clinicalClass, layer } = classifyRegionDisplay({
        groupKey: meta.groupKey || meta.group || '',
        groupName: meta.groupName || meta.group || '',
      });
      const volumeScore = Number(meta.volumeMm3 ?? meta.voxelCount ?? 0);
      map.set(key, {
        category: normalizedCategory,
        group: normalizedGroup,
        clinicalClass,
        layer,
        volumeScore: Number.isFinite(volumeScore) ? volumeScore : 0,
        voxelCount: Number(meta.voxelCount ?? 0) || 0,
        nSubregions: Number(meta.nSubregions ?? 0) || 0,
        aggregateParent: false,
      });
    });
    map.forEach((meta, key) => {
      map.set(key, {
        ...meta,
        aggregateParent: isAggregateParentRegion(key, meta),
      });
    });
    return map;
  }, [brainRegions]);
  const regionCategoryByKey = useMemo(() => {
    const map = new Map();
    regionDisplayMetaByKey.forEach((meta, key) => map.set(key, meta.category));
    return map;
  }, [regionDisplayMetaByKey]);
  const regionGroupByKey = useMemo(() => {
    const map = new Map();
    regionDisplayMetaByKey.forEach((meta, key) => {
      if (meta.group) map.set(key, meta.group);
    });
    return map;
  }, [regionDisplayMetaByKey]);
  const layerCounts = useMemo(() => {
    const counts = { L1: 0, L2: 0, L3: 0, L4: 0 };
    regionDisplayMetaByKey.forEach((meta) => {
      if (counts[meta.layer] !== undefined) counts[meta.layer] += 1;
    });
    return counts;
  }, [regionDisplayMetaByKey]);
  const clinicalClassCounts = useMemo(() => {
    const counts = {};
    CLINICAL_CLASS_ORDER.forEach((key) => {
      counts[key] = 0;
    });
    regionDisplayMetaByKey.forEach((meta) => {
      if (Object.prototype.hasOwnProperty.call(counts, meta.clinicalClass)) counts[meta.clinicalClass] += 1;
    });
    return counts;
  }, [regionDisplayMetaByKey]);
  const visibleKeysByPolicy = useMemo(() => {
    const rows = [];
    regionDisplayMetaByKey.forEach((meta, key) => {
      if (!layerVisibility?.[meta.layer]) return;
      if (classVisibility?.[meta.clinicalClass] === false) return;
      if (meta.aggregateParent && !selectedRegionSet.has(key)) return;
      rows.push({ key, volumeScore: meta.volumeScore });
    });
    rows.sort((a, b) => (b.volumeScore || 0) - (a.volumeScore || 0));
    return rows.map((r) => r.key);
  }, [classVisibility, layerVisibility, regionDisplayMetaByKey, selectedRegionSet]);
  const hasInteractiveFocus = useMemo(() => {
    const selectedCount = Array.isArray(selectedRegions) ? selectedRegions.length : 0;
    const sideCoverageCount =
      sideCoverage && typeof sideCoverage === 'object' ? Object.keys(sideCoverage).length : 0;
    const contactCount = Array.isArray(contactProfileData?.contactRegionSet)
      ? contactProfileData.contactRegionSet.length
      : 0;
    return (
      selectedCount > 0 ||
      Boolean(focusedRegion) ||
      Boolean(recentPick?.key) ||
      sideCoverageCount > 0 ||
      contactCount > 0
    );
  }, [contactProfileData, focusedRegion, recentPick?.key, selectedRegions, sideCoverage]);
  const activeSystemGroups = useMemo(() => {
    const groups = [];
    const seen = new Set();
    const contactKeys = Array.isArray(contactProfileData?.contactRegionSet)
      ? contactProfileData.contactRegionSet
      : [];
    const seedKeys = [
      ...(Array.isArray(selectedRegions) ? selectedRegions : []),
      ...(focusedRegion ? [focusedRegion] : []),
      ...(recentPick?.key ? [recentPick.key] : []),
      ...Object.keys(sideCoverage || {}),
      ...contactKeys,
    ];
    seedKeys.forEach((regionKey) => {
      const variants = keyVariantsForLookup(regionKey);
      for (let i = 0; i < variants.length; i += 1) {
        const g = regionGroupByKey.get(variants[i]);
        if (!g || seen.has(g)) continue;
        seen.add(g);
        groups.push(g);
        break;
      }
    });
    return groups;
  }, [contactProfileData, focusedRegion, recentPick?.key, regionGroupByKey, selectedRegions, sideCoverage]);
  const selectedFocusKeys = useMemo(() => {
    const ordered = [];
    const seen = new Set();
    const selectedList = Array.isArray(selectedRegions) ? selectedRegions : [];
    const sideCoverageKeys = Object.keys(sideCoverage || {});
    const contactKeys = Array.isArray(contactProfileData?.contactRegionSet)
      ? contactProfileData.contactRegionSet
      : [];
    const allCandidates = [
      ...selectedList,
      ...(focusedRegion ? [focusedRegion] : []),
      ...(recentPick?.key ? [recentPick.key] : []),
      ...sideCoverageKeys,
      ...contactKeys,
      ...RISK_CONTEXT_KEYS,
    ];
    const tryPush = (value) => {
      if (!value || seen.has(value)) return false;
      if (!regionDisplayMetaByKey.has(value)) return false;
      seen.add(value);
      ordered.push(value);
      return true;
    };
    allCandidates.forEach((regionKey) => {
      const variants = keyVariantsForLookup(regionKey);
      for (let i = 0; i < variants.length; i += 1) {
        if (tryPush(variants[i])) break;
      }
    });

    const defaultContext = [...DEFAULT_CONTEXT_KEYS, ...RISK_CONTEXT_KEYS];
    if (!hasInteractiveFocus) {
      defaultContext.forEach((regionKey) => {
        const variants = keyVariantsForLookup(regionKey);
        for (let i = 0; i < variants.length; i += 1) {
          if (tryPush(variants[i])) break;
        }
      });
    }

    const softContextLimit = hasInteractiveFocus ? CLINICAL_FOCUS_MAX_VISIBLE : 32;
    const pushFromVisibleByGroup = (groups) => {
      if (!Array.isArray(groups) || groups.length <= 0) return;
      for (let i = 0; i < visibleKeysByPolicy.length; i += 1) {
        if (ordered.length >= softContextLimit) break;
        const key = visibleKeysByPolicy[i];
        if (seen.has(key)) continue;
        const meta = regionDisplayMetaByKey.get(key);
        if (!meta) continue;
        if (meta.aggregateParent) continue;
        if (!groups.includes(meta.group)) continue;
        seen.add(key);
        ordered.push(key);
      }
    };

    if (hasInteractiveFocus) {
      pushFromVisibleByGroup(activeSystemGroups);
      for (let i = 0; i < visibleKeysByPolicy.length; i += 1) {
        if (ordered.length >= softContextLimit) break;
        const key = visibleKeysByPolicy[i];
        if (seen.has(key)) continue;
        const meta = regionDisplayMetaByKey.get(key);
        if (!meta || meta.aggregateParent) continue;
        if (meta.layer === 'L3' || meta.layer === 'L4') continue;
        if (meta.clinicalClass === 'pathway' || meta.clinicalClass === 'nerve_fissure') continue;
        const inActiveGroup = activeSystemGroups.includes(meta.group);
        const inRiskContext = RISK_CONTEXT_KEYS.includes(key);
        if (!inActiveGroup && !inRiskContext) continue;
        seen.add(key);
        ordered.push(key);
      }
    } else {
      for (let i = 0; i < visibleKeysByPolicy.length; i += 1) {
        if (ordered.length >= softContextLimit) break;
        const key = visibleKeysByPolicy[i];
        if (seen.has(key)) continue;
        const meta = regionDisplayMetaByKey.get(key);
        if (!meta) continue;
        if (meta.aggregateParent) continue;
        if (meta.layer === 'L3' || meta.layer === 'L4') continue;
        if (meta.clinicalClass === 'pathway' || meta.clinicalClass === 'nerve_fissure') continue;
        seen.add(key);
        ordered.push(key);
      }
    }

    if (ordered.length <= 0) {
      ['VISp', 'CA1', 'DG', 'VPM', 'MOp', 'CP', 'ACA', 'MRN'].forEach((key) => {
        if (!seen.has(key)) {
          seen.add(key);
          ordered.push(key);
        }
      });
    }
    return ordered.slice(0, CLINICAL_FOCUS_KEY_LIMIT);
  }, [activeSystemGroups, contactProfileData, focusedRegion, hasInteractiveFocus, recentPick?.key, regionDisplayMetaByKey, selectedRegions, sideCoverage, visibleKeysByPolicy]);
  const selectedFocusCategories = useMemo(() => {
    if (!hasInteractiveFocus) return [];
    const ordered = [];
    const seen = new Set();
    selectedFocusKeys.forEach((key) => {
      const cat = regionCategoryByKey.get(key);
      if (!cat || seen.has(cat)) return;
      seen.add(cat);
      ordered.push(cat);
    });
    return ordered.slice(0, CLINICAL_FOCUS_CATEGORY_LIMIT);
  }, [hasInteractiveFocus, regionCategoryByKey, selectedFocusKeys]);
  const selectedFocusGroups = useMemo(() => {
    if (!hasInteractiveFocus) return [];
    const ordered = [];
    const seen = new Set();
    selectedFocusKeys.forEach((key) => {
      const group = regionGroupByKey.get(key);
      if (!group || seen.has(group)) return;
      seen.add(group);
      ordered.push(group);
    });
    return ordered.slice(0, CLINICAL_FOCUS_GROUP_LIMIT);
  }, [hasInteractiveFocus, regionGroupByKey, selectedFocusKeys]);
  const visiblePolicyCount = visibleKeysByPolicy.length;
  const focusAutoLimited = visiblePolicyCount > CLINICAL_FOCUS_MAX_VISIBLE;
  const toggleLayerVisibility = useCallback((layerKey) => {
    setLayerVisibility((prev) => ({ ...prev, [layerKey]: !prev?.[layerKey] }));
  }, []);
  const toggleClassVisibility = useCallback((classKey) => {
    setClassVisibility((prev) => ({ ...prev, [classKey]: !prev?.[classKey] }));
  }, []);
  useEffect(() => {
    try {
      const raw = window.localStorage?.getItem('probeplanner.slicePresets');
      const parsed = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) {
        setSlicePresets(
          parsed
            .filter((x) => x && SLICE_AXES.includes(x.axis))
            .slice(0, 8),
        );
      }
    } catch {
      setSlicePresets([]);
    }
  }, []);

  useEffect(() => {
    try {
      const raw = window.localStorage?.getItem('probeplanner.clinicalFocus');
      if (raw === '0' || raw === 'false') {
        setClinicalFocus(false);
      } else if (raw === '1' || raw === 'true') {
        setClinicalFocus(true);
      }
    } catch {
      // ignore storage access issues
    }
  }, []);
  useEffect(() => {
    try {
      const raw = window.localStorage?.getItem('probeplanner.environmentLighting');
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) {
        setEnvironmentLighting(clamp(parsed, 0, 1));
      }
    } catch {
      // ignore storage access issues
    }
  }, []);
  useEffect(() => {
    if (!isMriHybridMode) {
      mriAppearanceInitRef.current = false;
      return;
    }
    if (mriAppearanceInitRef.current) return;
    let stored = null;
    try {
      const raw = String(window.localStorage?.getItem(MRI_APPEARANCE_STORAGE_KEY) || '').trim().toLowerCase();
      if (raw === '1' || raw === 'true' || raw === 'on' || raw === 'yes') stored = true;
      else if (raw === '0' || raw === 'false' || raw === 'off' || raw === 'no') stored = false;
    } catch {
      stored = null;
    }
    const backendDefault =
      typeof mriConfig?.useMriAppearance === 'boolean'
        ? Boolean(mriConfig.useMriAppearance)
        : true;
    setUseMriAppearance(stored === null ? backendDefault : stored);
    let storedLayers = null;
    try {
      const rawLayers = window.localStorage?.getItem(MRI_APPEARANCE_LAYERS_STORAGE_KEY);
      storedLayers = rawLayers ? JSON.parse(rawLayers) : null;
    } catch {
      storedLayers = null;
    }
    const backendLayers = {
      showOuterBoundary: mriConfig?.showOuterBoundary,
      showSkull: mriConfig?.showSkull,
      showBrainSurface: mriConfig?.showBrainSurface,
      showCcfLabels: mriConfig?.showCcfLabels,
      overlayMode: mriConfig?.overlayMode,
    };
    setMriAppearanceLayers(normalizeMriAppearanceLayers(storedLayers || backendLayers));
    mriAppearanceInitRef.current = true;
  }, [
    isMriHybridMode,
    mriConfig?.overlayMode,
    mriConfig?.showBrainSurface,
    mriConfig?.showCcfLabels,
    mriConfig?.showOuterBoundary,
    mriConfig?.showSkull,
    mriConfig?.useMriAppearance,
  ]);

  useEffect(() => {
    const backup = mriVisualProfileBackupRef.current;
    const shouldApply = Boolean(isMriHybridMode && mriAppearanceEnabled);

    if (!shouldApply) {
      if (backup) {
        setSelectedOnly(Boolean(backup.selectedOnly));
        setClinicalFocus(Boolean(backup.clinicalFocus));
        setLayerVisibility(backup.layerVisibility || { ...TARGET_TEMPLATE_LAYER_VISIBILITY });
        setClassVisibility(backup.classVisibility || { ...TARGET_TEMPLATE_CLASS_VISIBILITY });
        setShowShell(Boolean(backup.showShell));
        setShowAxes(Boolean(backup.showAxes));
        setShowProbeGuides(Boolean(backup.showProbeGuides));
        setShowUncertainty(Boolean(backup.showUncertainty));
        setUseWeightedOit(Boolean(backup.useWeightedOit));
        setOitWeight(Number.isFinite(backup.oitWeight) ? backup.oitWeight : 1.0);
        setPostFxPreset(String(backup.postFxPreset || 'pinpoint'));
        setAntiAliasingMode(String(backup.antiAliasingMode || 'taa'));
        setPostFxEnabled(Boolean(backup.postFxEnabled));
        setAmbientOcclusionEnabled(Boolean(backup.ambientOcclusionEnabled));
        setEnvironmentLighting(
          clamp(
            Number.isFinite(backup.environmentLighting) ? backup.environmentLighting : TARGET_TEMPLATE_ENV_LIGHT,
            0,
            1,
          ),
        );
        setActiveTemplate(String(backup.activeTemplate || 'target'));
        mriVisualProfileBackupRef.current = null;
      }
      mriVisualProfileAppliedRef.current = false;
      return;
    }

    if (mriVisualProfileAppliedRef.current) return;

    if (!backup) {
      mriVisualProfileBackupRef.current = {
        selectedOnly,
        clinicalFocus,
        layerVisibility: { ...layerVisibility },
        classVisibility: { ...classVisibility },
        showShell,
        showAxes,
        showProbeGuides,
        showUncertainty,
        useWeightedOit,
        oitWeight,
        postFxPreset,
        antiAliasingMode,
        postFxEnabled,
        ambientOcclusionEnabled,
        environmentLighting,
        activeTemplate,
      };
    }

    // MRI hybrid visual profile: make the mode change obvious and clinically readable.
    setSelectedOnly(false);
    setClinicalFocus(true);
    setLayerVisibility({ L1: true, L2: true, L3: false, L4: false });
    setClassVisibility({ ...TARGET_TEMPLATE_CLASS_VISIBILITY });
    setShowShell(true);
    setShowAxes(false);
    setShowProbeGuides(true);
    setShowUncertainty(false);
    setUseWeightedOit(true);
    setOitWeight(0.85);
    setPostFxPreset('mri');
    setAntiAliasingMode('taa');
    setPostFxEnabled(true);
    setAmbientOcclusionEnabled(true);
    setEnvironmentLighting(0.82);
    setActiveTemplate('mri');
    mriVisualProfileAppliedRef.current = true;
  }, [
    activeTemplate,
    ambientOcclusionEnabled,
    antiAliasingMode,
    classVisibility,
    clinicalFocus,
    environmentLighting,
    isMriHybridMode,
    layerVisibility,
    mriAppearanceEnabled,
    oitWeight,
    postFxEnabled,
    postFxPreset,
    selectedOnly,
    showAxes,
    showProbeGuides,
    showShell,
    showUncertainty,
    useWeightedOit,
  ]);

  useEffect(() => {
    try {
      window.localStorage?.setItem('probeplanner.slicePresets', JSON.stringify(slicePresets.slice(0, 8)));
    } catch {
      // ignore localStorage quota/access issues.
    }
  }, [slicePresets]);

  useEffect(() => {
    try {
      window.localStorage?.setItem('probeplanner.clinicalFocus', clinicalFocus ? '1' : '0');
    } catch {
      // ignore storage access issues
    }
  }, [clinicalFocus]);
  useEffect(() => {
    const selectedCount = Array.isArray(selectedRegions) ? selectedRegions.length : 0;
    if (!clinicalFocus && selectedCount <= 0 && visiblePolicyCount > CLINICAL_FOCUS_MAX_VISIBLE) {
      setClinicalFocus(true);
    }
  }, [clinicalFocus, selectedRegions, visiblePolicyCount]);
  useEffect(() => {
    try {
      window.localStorage?.setItem(
        'probeplanner.environmentLighting',
        String(clamp(environmentLighting, 0, 1).toFixed(3)),
      );
    } catch {
      // ignore storage access issues
    }
  }, [environmentLighting]);
  useEffect(() => {
    if (!isMriHybridMode) return;
    try {
      window.localStorage?.setItem(MRI_APPEARANCE_STORAGE_KEY, useMriAppearance ? '1' : '0');
    } catch {
      // ignore storage access issues
    }
  }, [isMriHybridMode, useMriAppearance]);
  useEffect(() => {
    if (!isMriHybridMode) return;
    try {
      window.localStorage?.setItem(
        MRI_APPEARANCE_LAYERS_STORAGE_KEY,
        JSON.stringify(normalizeMriAppearanceLayers(mriAppearanceLayers)),
      );
    } catch {
      // ignore storage access issues
    }
  }, [isMriHybridMode, mriAppearanceLayers]);
  useEffect(() => {
    try {
      const raw = window.localStorage?.getItem('probeplanner.layerVisibility');
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === 'object') {
        setLayerVisibility((prev) => ({
          ...prev,
          L1: parsed.L1 !== false,
          L2: parsed.L2 !== false,
          L3: Boolean(parsed.L3),
          L4: Boolean(parsed.L4),
        }));
      }
    } catch {
      // ignore
    }
  }, []);
  useEffect(() => {
    try {
      window.localStorage?.setItem('probeplanner.layerVisibility', JSON.stringify(layerVisibility));
    } catch {
      // ignore
    }
  }, [layerVisibility]);
  useEffect(() => {
    try {
      const raw = window.localStorage?.getItem('probeplanner.classVisibility');
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === 'object') {
        setClassVisibility((prev) => {
          const out = { ...prev };
          CLINICAL_CLASS_ORDER.forEach((key) => {
            if (Object.prototype.hasOwnProperty.call(parsed, key)) out[key] = Boolean(parsed[key]);
          });
          return out;
        });
      }
    } catch {
      // ignore
    }
  }, []);
  useEffect(() => {
    try {
      window.localStorage?.setItem('probeplanner.classVisibility', JSON.stringify(classVisibility));
    } catch {
      // ignore
    }
  }, [classVisibility]);
  const unitySource = useMemo(() => {
    const base = String(backendBaseUrl || '').trim().replace(/\/+$/, '');
    if (!base) return UNITY_SOURCE;
    const sep = UNITY_SOURCE.includes('?') ? '&' : '?';
    return `${UNITY_SOURCE}${sep}backend=${encodeURIComponent(base)}`;
  }, [backendBaseUrl]);
  const cleanBackendBase = useMemo(
    () => String(backendBaseUrl || '').trim().replace(/\/+$/, ''),
    [backendBaseUrl],
  );
  const syncMriAppearanceBackend = useCallback(
    async (payloadOrEnabled) => {
      if (!isMriHybridMode) return;
      if (backendStatus !== 'online' || !cleanBackendBase) return;
      const payload = typeof payloadOrEnabled === 'object' && payloadOrEnabled
        ? payloadOrEnabled
        : buildMriAppearancePayload(
            Boolean(payloadOrEnabled),
            mriAppearanceLayers,
            activeAtlasMode,
            isMriHybridMode,
          );
      const token = String(authToken || '').trim();
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers.Authorization = `Bearer ${token}`;
      try {
        await fetch(`${cleanBackendBase}/api/mri/appearance`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            useMriAppearance: Boolean(payload.enabled),
            showOuterBoundary: Boolean(payload.showOuterBoundary),
            showSkull: Boolean(payload.showSkull),
            showBrainSurface: Boolean(payload.showBrainSurface),
            showCcfLabels: Boolean(payload.showCcfLabels),
            overlayMode: payload.overlayMode || 'ccf_only',
          }),
        });
      } catch {
        // non-blocking: local render toggle still applies
      }
    },
    [activeAtlasMode, authToken, backendStatus, cleanBackendBase, isMriHybridMode, mriAppearanceLayers],
  );
  const handleToggleMriAppearance = useCallback(() => {
    const next = !useMriAppearance;
    setUseMriAppearance(next);
    syncMriAppearanceBackend(
      buildMriAppearancePayload(
        Boolean(next && isMriHybridMode),
        mriAppearanceLayers,
        activeAtlasMode,
        isMriHybridMode,
      ),
    );
  }, [
    activeAtlasMode,
    isMriHybridMode,
    mriAppearanceLayers,
    syncMriAppearanceBackend,
    useMriAppearance,
  ]);
  const handleToggleMriAppearanceLayer = useCallback((layerKey) => {
    setMriAppearanceLayers((prev) => {
      const next = normalizeMriAppearanceLayers(prev);
      next[layerKey] = !next[layerKey];
      return next;
    });
  }, []);
  useEffect(() => {
    if (!isMriHybridMode || !mriAppearanceInitRef.current) return;
    syncMriAppearanceBackend(mriAppearancePayload);
  }, [isMriHybridMode, mriAppearancePayload, syncMriAppearanceBackend]);
  const probeSyncSignature = JSON.stringify({
    probePreset,
    depth: probeSpec?.depth ?? null,
    probes: Array.isArray(probes) ? probes.map((probe) => ({
      ap: probe.ap ?? probe.ap_mm ?? null,
      ml: probe.ml ?? probe.ml_mm ?? null,
      theta: probe.theta_deg ?? probe.elevation ?? null,
      phi: probe.phi_deg ?? probe.azimuth ?? null,
      depth: probe.depthUm ?? probe.depth ?? null,
      entryDv: probe.entryDvCorr_mm ?? probe.dvEntry ?? null,
      probeType: probe.probeType ?? null,
      minRisk: probe.minRiskDistanceMm ?? probe?.riskDiagnostics?.minDistanceMm ?? null,
      riskSegCount: Array.isArray(probe?.riskDiagnostics?.dangerSegmentsTop3)
        ? probe.riskDiagnostics.dangerSegmentsTop3.length
        : Array.isArray(probe?.riskDiagnostics?.thresholdSegmentsMm)
          ? probe.riskDiagnostics.thresholdSegmentsMm.length
          : 0,
      contactCount: Array.isArray(probe?.contactIntervals) ? probe.contactIntervals.length : 0,
    })) : [],
      slice: {
        axis: sliceAxis,
        mode: sliceMode,
        normalized: coronalSlice,
        thicknessMm: sliceThicknessMm,
        enabled: sliceEnabled,
        filter: sliceFilterProbes,
      },
  });
  const probePayloadRaw = useMemo(
    () => (Array.isArray(probes) ? probes.map((probe) => toUnityProbe(probe, probePreset, probeSpec)) : []),
    [probes, probePreset, probeSpec],
  );
  const probePayloadAugmented = useMemo(() => {
    const base = probePayloadRaw.map((row) => ({ ...row }));
    const profileProbes = Array.isArray(contactProfileData?.probes) ? contactProfileData.probes : [];
    profileProbes.forEach((item) => {
      const idx = Number(item?.index);
      if (!Number.isFinite(idx) || idx < 0 || idx >= base.length) return;
      const intervals = Array.isArray(item?.contactIntervals) ? item.contactIntervals : [];
      if (!intervals.length) return;
      const depthMm = Math.max(0, Number(base[idx]?.depthUm || 0) / 1000);
      const mapped = intervals
        .map((seg) => {
          const start = clampDepthMm(Number(seg?.startDepthMm ?? seg?.dStartUm / 1000), depthMm);
          const end = clampDepthMm(Number(seg?.endDepthMm ?? seg?.dEndUm / 1000), depthMm);
          if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start + 1e-4) return null;
          return {
            startDepthMm: start,
            endDepthMm: end,
            key: String(seg?.key || '').trim(),
            label: String(seg?.label || '').trim(),
            contactStrength: Number.isFinite(Number(seg?.contactStrength)) ? Number(seg.contactStrength) : 0,
            lengthUm: Number.isFinite(Number(seg?.lengthUm))
              ? Number(seg.lengthUm)
              : Math.round((end - start) * 1000),
          };
        })
        .filter(Boolean);
      if (!mapped.length) return;
      const existing = Array.isArray(base[idx].contactIntervals) ? base[idx].contactIntervals : [];
      if (existing.length < mapped.length) {
        base[idx].contactIntervals = mapped;
      }
    });
    return base;
  }, [contactProfileData?.probes, probePayloadRaw]);
  const probePayloadVisible = useMemo(() => {
    if (!sliceFilterProbes || !sliceEnabled) return probePayloadAugmented;
    return probePayloadAugmented.filter((probe) =>
      probeIntersectsSlice(
        probe,
        sliceAxis,
        activeSliceCoord,
        sliceMode,
        sliceMode === 'slab' ? sliceThicknessMm : 0,
      ),
    );
  }, [activeSliceCoord, probePayloadAugmented, sliceAxis, sliceEnabled, sliceFilterProbes, sliceMode, sliceThicknessMm]);
  const visibleProbeCount = probePayloadVisible.length;
  const totalProbeCount = probePayloadAugmented.length;
  const contactRequestProbes = useMemo(
    () =>
      probePayloadAugmented.map((probe) => ({
        ...probe,
        radiusUm: Number.isFinite(Number(probe.radiusUm))
          ? Number(probe.radiusUm)
          : Number(probeSpec?.radiusUm ?? 35),
      })),
    [probePayloadAugmented, probeSpec],
  );
  const contactRegionSet = useMemo(() => {
    const src = Array.isArray(contactProfileData?.contactRegionSet)
      ? contactProfileData.contactRegionSet
      : [];
    const dedup = new Set();
    src.forEach((raw) => {
      const key = String(raw || '').trim();
      if (key) dedup.add(key);
    });
    return [...dedup];
  }, [contactProfileData]);
  const unityContactRegionSet = useMemo(
    () => sanitizeContactRegionsForUnity(contactRegionSet),
    [contactRegionSet],
  );
  const contactRegionSetLookup = useMemo(() => new Set(contactRegionSet), [contactRegionSet]);
  const sliceBoundaryCandidates = useMemo(() => {
    const buckets = { x: [], y: [], z: [] };
    if (!brainRegions || typeof brainRegions !== 'object') return buckets;

    Object.entries(brainRegions).forEach(([key, reg]) => {
      if (!reg || !Number.isFinite(reg.ap) || !Number.isFinite(reg.dv) || !Number.isFinite(reg.ml)) return;
      const meta = regionDisplayMetaByKey.get(key);
      const includeByLayer = meta && (meta.layer === 'L1' || meta.layer === 'L2');
      const includeByContext =
        selectedRegionSet.has(key) ||
        contactRegionSetLookup.has(key) ||
        RISK_CONTEXT_KEYS.includes(key);
      if (!includeByLayer && !includeByContext) return;
      if (meta?.aggregateParent) return;

      const ap = Number(reg.ap);
      const dv = Number(reg.dv);
      const mlAbs = Math.abs(Number(reg.ml));
      if (Number.isFinite(ap)) buckets.z.push({ key, coord: -ap });
      if (Number.isFinite(dv)) buckets.y.push({ key, coord: -dv });
      if (Number.isFinite(mlAbs)) {
        buckets.x.push({ key, coord: mlAbs });
        buckets.x.push({ key, coord: -mlAbs });
      }
    });

    (['x', 'y', 'z']).forEach((axis) => {
      buckets[axis].sort((a, b) => a.coord - b.coord);
      const dedup = [];
      let prev = null;
      buckets[axis].forEach((item) => {
        if (prev && Math.abs(item.coord - prev.coord) < 1e-4) return;
        dedup.push(item);
        prev = item;
      });
      buckets[axis] = dedup;
    });

    return buckets;
  }, [brainRegions, contactRegionSetLookup, regionDisplayMetaByKey, selectedRegionSet]);
  const contactIntervals = useMemo(() => {
    const rows = [];
    const probes = Array.isArray(contactProfileData?.probes) ? contactProfileData.probes : [];
    probes.forEach((probeItem) => {
      const probeIndex = Number(probeItem?.index);
      const intervals = Array.isArray(probeItem?.contactIntervals) ? probeItem.contactIntervals : [];
      intervals.forEach((interval, idx) => {
        const acronym = String(interval?.acronym || interval?.region || '').trim();
        if (!acronym) return;
        const dStartUm = Number(interval?.dStartUm ?? interval?.startUm ?? 0);
        const dEndUm = Number(interval?.dEndUm ?? interval?.endUm ?? dStartUm);
        const lengthUm = Number(interval?.lengthUm ?? Math.max(0, dEndUm - dStartUm));
        const minDistanceMm = Number(interval?.minDistanceMm);
        const shankIndices = Array.isArray(interval?.shankIndices) ? interval.shankIndices : [];
        rows.push({
          id: `${probeIndex}-${idx}-${acronym}-${dStartUm}-${dEndUm}`,
          probeIndex: Number.isFinite(probeIndex) ? probeIndex : 0,
          probeId: String(interval?.probeId || `P${Number.isFinite(probeIndex) ? probeIndex + 1 : 1}`),
          acronym,
          region: String(interval?.region || acronym),
          name: String(interval?.name || acronym),
          hemisphere: String(interval?.hemisphere || '').toLowerCase(),
          dStartUm,
          dEndUm,
          lengthUm,
          contactStrength: Number(interval?.contactStrength ?? 0),
          riskClass: String(interval?.riskClass || ''),
          minDistanceMm: Number.isFinite(minDistanceMm) ? minDistanceMm : null,
          thresholdMm: Number.isFinite(Number(interval?.thresholdMm)) ? Number(interval.thresholdMm) : null,
          shankCount: Number.isFinite(Number(interval?.shankCount)) ? Number(interval.shankCount) : null,
          shankSpacingUm: Number.isFinite(Number(interval?.shankSpacingUm)) ? Number(interval.shankSpacingUm) : null,
          probeRadiusUm: Number.isFinite(Number(interval?.probeRadiusUm)) ? Number(interval.probeRadiusUm) : null,
          shankIndices,
        });
      });
    });
    rows.sort((a, b) => {
      if (a.probeIndex !== b.probeIndex) return a.probeIndex - b.probeIndex;
      if (a.dStartUm !== b.dStartUm) return a.dStartUm - b.dStartUm;
      return String(a.acronym).localeCompare(String(b.acronym));
    });
    return rows;
  }, [contactProfileData]);
  const visibleContactIntervals = useMemo(
    () => contactIntervals.slice(0, 48),
    [contactIntervals],
  );
  useEffect(() => {
    if (visibleContactIntervals.length <= 0) {
      if (activeContactIntervalId) setActiveContactIntervalId('');
      return;
    }
    if (activeContactIntervalId) {
      const exists = visibleContactIntervals.some((row) => String(row.id) === String(activeContactIntervalId));
      if (exists) return;
    }
    setActiveContactIntervalId(String(visibleContactIntervals[0].id || ''));
  }, [activeContactIntervalId, visibleContactIntervals]);
  const activeContactIntervalIndex = useMemo(() => {
    if (!activeContactIntervalId) return visibleContactIntervals.length > 0 ? 0 : -1;
    return visibleContactIntervals.findIndex((row) => String(row.id) === String(activeContactIntervalId));
  }, [activeContactIntervalId, visibleContactIntervals]);
  const contactHeatRange = useMemo(() => {
    let maxLen = 1;
    let maxStrength = 0.01;
    contactIntervals.forEach((row) => {
      if (Number.isFinite(row.lengthUm)) maxLen = Math.max(maxLen, row.lengthUm);
      if (Number.isFinite(row.contactStrength)) maxStrength = Math.max(maxStrength, row.contactStrength);
    });
    return { maxLen, maxStrength };
  }, [contactIntervals]);
  const handleExportContactCsv = useCallback(() => {
    const headers = [
      'probeId',
      'region',
      'hemisphere',
      'dStartUm',
      'dEndUm',
      'lengthUm',
      'contactStrength',
      'riskClass',
      'minDistanceMm',
      'shankIndices',
      'shankCount',
      'shankSpacingUm',
      'probeRadiusUm',
    ];
    const lines = [headers.join(',')];
    contactIntervals.forEach((row) => {
      lines.push(
        headers.map((key) => {
          if (key === 'probeId') return csvEscape(row.probeId || `P${row.probeIndex + 1}`);
          if (key === 'region') return csvEscape(row.region || row.acronym);
          return csvEscape(row[key]);
        }).join(','),
      );
    });
    downloadTextFile(`probe_contact_depth_table_${Date.now()}.csv`, `${lines.join('\n')}\n`);
  }, [contactIntervals]);
  const contactOverlayPayload = useMemo(
    () => ({
      enabled: unityContactRegionSet.length > 0,
      mode: contactDisplayMode,
      style: contactDisplayStyle,
      minLengthUm: Number(contactMinLengthUm),
      regions: unityContactRegionSet,
      totalRegionCount: contactRegionSet.length,
      truncated: unityContactRegionSet.length < contactRegionSet.length,
    }),
    [contactDisplayMode, contactDisplayStyle, contactMinLengthUm, contactRegionSet.length, unityContactRegionSet],
  );
  const contactOverlayJson = useMemo(
    () => JSON.stringify(contactOverlayPayload),
    [contactOverlayPayload],
  );
  const contactOverlayEnabled = contactOverlayPayload.enabled && unityContactRegionSet.length > 0;
  const contactOverlayDisabledJson = useMemo(
    () => JSON.stringify({
      enabled: false,
      mode: 'standard',
      style: 'outline',
      minLengthUm: Number(contactMinLengthUm),
      regions: [],
      totalRegionCount: 0,
      truncated: false,
    }),
    [contactMinLengthUm],
  );
  const sliceModeLabel = sliceMode === 'slab' ? tr('brain3dSliceModeSlab') : tr('brain3dSliceModeBelow');
  const sliceAxisLabel = tr(SLICE_AXIS_LABEL_KEY[sliceAxis] || 'brain3dSliceAxisCoronal');

  const publishBridgeMetrics = useCallback(() => {
    const metrics = bridgeMetricsRef.current;
    const now = Date.now();
    if (now - metrics.lastPublishedAt < 1000) return;
    metrics.lastPublishedAt = now;
    const elapsedS = Math.max(0.001, (now - metrics.startedAt) / 1000);
    const detail = {
      ...metrics,
      elapsedS,
      sentPerSecond: Number((metrics.sent / elapsedS).toFixed(2)),
      bytesPerSecond: Number((metrics.bytesSent / elapsedS).toFixed(1)),
      queuedDepth: commandQueueRef.current.size,
      capabilities: summarizeUnityCapabilities(unityCapabilitiesRef.current),
    };
    window.dispatchEvent(new CustomEvent('probeplanner:unity-bridge-metrics', { detail }));
  }, []);

  const updateBridgeMetrics = useCallback((patch) => {
    const metrics = bridgeMetricsRef.current;
    Object.keys(patch || {}).forEach((key) => {
      const value = patch[key];
      if (
        typeof value === 'number' &&
        typeof metrics[key] === 'number' &&
        !BRIDGE_METRIC_ABSOLUTE_KEYS.has(key)
      ) {
        metrics[key] += value;
      } else {
        metrics[key] = value;
      }
    });
    publishBridgeMetrics();
  }, [publishBridgeMetrics]);

  const isUnityMethodAllowed = useCallback((method) => {
    const name = String(method || '').trim();
    if (!name) return false;
    const caps = unityCapabilitiesRef.current;
    if (!caps || caps.protocolVersion <= 0) {
      if (!UNITY_LEGACY_BRIDGE_COMPAT) {
        return name === 'GetCapabilities' || name === 'Heartbeat';
      }
      return !UNITY_FALLBACK_UNSUPPORTED_METHODS.has(name);
    }
    if (!caps.negotiated && !UNITY_LEGACY_BRIDGE_COMPAT) {
      return name === 'GetCapabilities' || name === 'Heartbeat';
    }
    if (caps.unsupportedMethods && caps.unsupportedMethods.has(name)) return false;
    if (caps.supportedMethods && caps.supportedMethods.size > 0) {
      return caps.supportedMethods.has(name);
    }
    return true;
  }, []);

  const sendToUnity = useCallback((method, param, stateVersion = null, meta = {}) => {
    if (!isUnityMethodAllowed(method)) {
      updateBridgeMetrics({ skipped: 1 });
      return null;
    }
    const iframe = iframeRef.current;
    if (!iframe || !instanceReady) return;
    const version = Number.isFinite(stateVersion) ? stateVersion : ++commandVersionRef.current;
    const payload = String(param ?? '');
    const payloadBytes = estimateUnityPayloadBytes(payload);
    const caps = unityCapabilitiesRef.current || {};
    const maxPayloadBytes = Number(caps.maxPayloadBytes || UNITY_FALLBACK_MAX_PAYLOAD_BYTES);
    const isLargePayload = UNITY_LARGE_PAYLOAD_METHODS.has(method) && payloadBytes > maxPayloadBytes;
    iframe.contentWindow?.postMessage(
      {
        target: 'BrainPlannerUnity',
        method,
        param: payload,
        stateVersion: version,
        payloadBytes,
        queuedAt: Number.isFinite(Number(meta.queuedAt)) ? Number(meta.queuedAt) : undefined,
        sentAt: Date.now(),
        transport: 'inline-json',
        largePayload: isLargePayload,
      },
      '*'
    );
    updateBridgeMetrics({
      sent: 1,
      bytesSent: payloadBytes,
      largePayloads: isLargePayload ? 1 : 0,
      maxPayloadBytes: Math.max(bridgeMetricsRef.current.maxPayloadBytes || 0, payloadBytes),
    });
    return version;
  }, [instanceReady, isUnityMethodAllowed, updateBridgeMetrics]);

  const unitySupportsBulkState = useCallback(() => {
    const caps = unityCapabilitiesRef.current;
    return Boolean(
      caps &&
      caps.protocolVersion > 0 &&
      caps.negotiated &&
      caps.supportsBulkState !== false &&
      isUnityMethodAllowed('SetStateSnapshot')
    );
  }, [isUnityMethodAllowed]);

  const flushUnityCommandQueue = useCallback(() => {
    if (commandFlushTimerRef.current) {
      window.clearTimeout(commandFlushTimerRef.current);
      commandFlushTimerRef.current = null;
    }
    const entries = Array.from(commandQueueRef.current.values());
    commandQueueRef.current.clear();
    if (!entries.length) return;
    updateBridgeMetrics({ flushes: 1, lastFlushAt: Date.now() });

    entries.forEach((entry) => {
      if (!entry || entry.disposed) return;
      const retryDelays = UNITY_NO_RETRY_METHODS.has(entry.method)
        ? [0]
        : UNITY_COMMAND_RETRY_DELAYS_MS;
      const sendAttempt = (attemptIdx) => {
        if (entry.disposed) return;
        const acked = ackedVersionByMethodRef.current.get(entry.method) || 0;
        if (acked >= entry.version) return;
        sendToUnity(entry.method, entry.param, entry.version, { queuedAt: entry.queuedAt });
        if (attemptIdx + 1 >= retryDelays.length) return;
        const nextDelay = retryDelays[attemptIdx + 1] - retryDelays[attemptIdx];
        const tid = window.setTimeout(() => sendAttempt(attemptIdx + 1), nextDelay);
        entry.timers.push(tid);
      };
      sendAttempt(0);
    });
  }, [sendToUnity, updateBridgeMetrics]);

  const scheduleUnityCommandFlush = useCallback(() => {
    if (commandFlushTimerRef.current) return;
    commandFlushTimerRef.current = window.setTimeout(flushUnityCommandQueue, UNITY_COMMAND_FLUSH_INTERVAL_MS);
  }, [flushUnityCommandQueue]);

  const sendToUnityReliable = useCallback((method, param) => {
    if (!isUnityMethodAllowed(method)) {
      updateBridgeMetrics({ skipped: 1 });
      return () => {};
    }
    if (!instanceReady) return () => {};
    const name = String(method || '').trim();
    const previous = commandQueueRef.current.get(name);
    if (previous) {
      previous.disposed = true;
      previous.timers.forEach((tid) => window.clearTimeout(tid));
      updateBridgeMetrics({ dropped: 1 });
    }
    const entry = {
      method: name,
      param,
      version: ++commandVersionRef.current,
      queuedAt: Date.now(),
      timers: [],
      disposed: false,
    };
    commandQueueRef.current.set(name, entry);
    updateBridgeMetrics({ queued: 1 });
    scheduleUnityCommandFlush();
    return () => {
      entry.disposed = true;
      entry.timers.forEach((tid) => window.clearTimeout(tid));
      if (commandQueueRef.current.get(name) === entry) {
        commandQueueRef.current.delete(name);
      }
    };
  }, [instanceReady, isUnityMethodAllowed, scheduleUnityCommandFlush, updateBridgeMetrics]);

  const updateCoronalSliceFromClientY = useCallback((clientY) => {
    const track = sliceTrackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    if (!rect || rect.height <= 0) return;
    const y = clamp(clientY, rect.top, rect.bottom);
    const normalizedRaw = clamp(1 - ((y - rect.top) / rect.height), 0, 1);
    let normalizedNext = normalizedRaw;
    let hint = {
      active: false,
      key: '',
      distanceMm: null,
      coord: null,
      axis: sliceAxis,
    };

    const axisRange = activeSliceRange;
    if (
      sliceSnapEnabled &&
      axisRange &&
      Number.isFinite(axisRange.min) &&
      Number.isFinite(axisRange.max) &&
      axisRange.max > axisRange.min
    ) {
      const rawCoord = axisRange.min + normalizedRaw * (axisRange.max - axisRange.min);
      const nearest = findClosestBoundaryCandidate(sliceBoundaryCandidates[sliceAxis], rawCoord);
      if (nearest) {
        hint = {
          active: nearest.distance <= sliceSnapThresholdMm,
          key: nearest.key || '',
          distanceMm: nearest.distance,
          coord: nearest.coord,
          axis: sliceAxis,
        };
        if (nearest.distance <= sliceSnapThresholdMm) {
          normalizedNext = clamp((nearest.coord - axisRange.min) / (axisRange.max - axisRange.min), 0, 1);
        }
      }
    }

    if (sliceSnapHintTimerRef.current) {
      window.clearTimeout(sliceSnapHintTimerRef.current);
      sliceSnapHintTimerRef.current = null;
    }
    setSliceSnapHint(hint);
    setActiveSliceNormalized(normalizedNext);
  }, [activeSliceRange, setActiveSliceNormalized, sliceAxis, sliceBoundaryCandidates, sliceSnapEnabled, sliceSnapThresholdMm]);

  const stopCoronalSliceDrag = useCallback(() => {
    if (typeof sliceDragCleanupRef.current === 'function') {
      sliceDragCleanupRef.current();
    }
    sliceDragCleanupRef.current = null;
    setSliceDragActive(false);
    if (sliceSnapHintTimerRef.current) window.clearTimeout(sliceSnapHintTimerRef.current);
    sliceSnapHintTimerRef.current = window.setTimeout(() => {
      setSliceSnapHint((prev) => ({ ...prev, active: false, key: '' }));
      sliceSnapHintTimerRef.current = null;
    }, 1200);
  }, []);

  const onCoronalSlicePointerDown = useCallback((e) => {
    // Require primary left mouse button hold to drag the slice ruler.
    if (e.pointerType && e.pointerType !== 'mouse') return;
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    setSliceDragActive(true);
    updateCoronalSliceFromClientY(e.clientY);

    const onMove = (moveEvent) => {
      if (moveEvent.pointerType && moveEvent.pointerType !== 'mouse') return;
      if ((Number(moveEvent.buttons) & 1) !== 1) {
        stopCoronalSliceDrag();
        return;
      }
      updateCoronalSliceFromClientY(moveEvent.clientY);
    };
    const onUp = () => stopCoronalSliceDrag();

    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerup', onUp, { once: true });
    window.addEventListener('pointercancel', onUp, { once: true });
    sliceDragCleanupRef.current = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [stopCoronalSliceDrag, updateCoronalSliceFromClientY]);

  useEffect(() => () => {
    if (sliceSnapHintTimerRef.current) {
      window.clearTimeout(sliceSnapHintTimerRef.current);
      sliceSnapHintTimerRef.current = null;
    }
  }, []);

  const applySlicePreset = useCallback((preset) => {
    if (!preset || !SLICE_AXES.includes(preset.axis)) return;
    const range = sliceRanges[preset.axis] || sliceRanges.y;
    handleSelectSliceAxis(preset.axis);
    setSliceMode(preset.mode === 'slab' ? 'slab' : 'below');
    setSliceThicknessMm(clamp(Number(preset.thicknessMm || 0.3), 0.08, 2));
    const normalized = Number.isFinite(preset.normalized)
      ? clamp(preset.normalized, 0, 1)
      : clamp((Number(preset.coord ?? range.max) - range.min) / Math.max(1e-6, range.max - range.min), 0, 1);
    setActiveSliceNormalized(normalized, preset.axis);
  }, [handleSelectSliceAxis, setActiveSliceNormalized, sliceRanges]);

  const handleSaveSlicePreset = useCallback(() => {
    const region = focusedRegion || selectedRegions[0] || '';
    const normalized = clamp(coronalSlice, 0, 1);
    const next = {
      id: `${Date.now()}`,
      label: region || `${sliceAxis.toUpperCase()} ${formatMm(sliceAxisCoord)}mm`,
      region,
      axis: sliceAxis,
      mode: sliceMode,
      thicknessMm: Number(sliceThicknessMm.toFixed(3)),
      normalized: Number(normalized.toFixed(4)),
      coord: Number(activeSliceCoord.toFixed(4)),
      createdAt: Date.now(),
    };
    setSlicePresets((prev) => [next, ...prev].slice(0, 8));
  }, [
    activeSliceCoord,
    coronalSlice,
    focusedRegion,
    selectedRegions,
    sliceAxis,
    sliceAxisCoord,
    sliceMode,
    sliceThicknessMm,
  ]);

  const handleDeleteSlicePreset = useCallback((id) => {
    setSlicePresets((prev) => prev.filter((p) => p.id !== id));
  }, []);

  const pumpScreenshotExportQueue = useCallback(() => {
    if (!bridgeReady) return;
    if (screenshotExportBusyRef.current) return;
    const queue = screenshotExportQueueRef.current;
    if (!Array.isArray(queue) || queue.length <= 0) return;
    const nextMeta = queue.shift();
    if (!nextMeta || typeof nextMeta !== 'object') return;
    screenshotExportBusyRef.current = true;
    sliceScreenshotMetaRef.current = nextMeta;
    sendToUnityReliable('RequestScreenshot', nextMeta.tag || 'slice_export');
  }, [bridgeReady, sendToUnityReliable]);

  const enqueueScreenshotExports = useCallback((entries) => {
    if (!bridgeReady || !Array.isArray(entries) || entries.length <= 0) return;
    const normalized = entries
      .map((row) => {
        const lines = Array.isArray(row?.lines) ? row.lines.filter(Boolean) : [];
        return {
          fileName: String(row?.fileName || `brain3d_export_${Date.now()}.png`),
          lines,
          tag: String(row?.tag || 'slice_export'),
        };
      })
      .filter((row) => row.fileName);
    if (normalized.length <= 0) return;
    screenshotExportQueueRef.current = normalized;
    pumpScreenshotExportQueue();
  }, [bridgeReady, pumpScreenshotExportQueue]);

  const handleExportSliceImage = useCallback(() => {
    if (!bridgeReady) return;
    const axisCoordLabelMap = {
      ap: tr('brain3dSliceCoordAp'),
      dv: tr('brain3dSliceCoordDv'),
      ml: tr('brain3dSliceCoordMl'),
    };
    const coordLabel = axisCoordLabelMap[sliceAxisCoordKey] || sliceAxisCoordKey.toUpperCase();
    const selectedLabel = selectedRegions.slice(0, 4).join(', ');
    enqueueScreenshotExports([
      {
        fileName: `slice_${sliceAxis}_${Date.now()}.png`,
        tag: 'slice_export',
        lines: [
          `${tr('brain3dSliceAxis')}: ${sliceAxisLabel}`,
          `${tr('brain3dSliceMode')}: ${sliceModeLabel}`,
          `${coordLabel}: ${formatMm(sliceAxisCoord)} mm`,
          `${tr('brain3dSliceThickness')}: ${Math.round(sliceThicknessMm * 1000)} um`,
          `${tr('brain3dSliceProbeFilter')}: ${visibleProbeCount}/${totalProbeCount}`,
          selectedLabel ? `${tr('brain3dSelected')}: ${selectedLabel}` : '',
        ],
      },
    ]);
  }, [
    bridgeReady,
    enqueueScreenshotExports,
    selectedRegions,
    sliceAxis,
    sliceAxisCoord,
    sliceAxisCoordKey,
    sliceAxisLabel,
    sliceModeLabel,
    sliceThicknessMm,
    totalProbeCount,
    tr,
    visibleProbeCount,
  ]);

  const handleExportClinicalBundle = useCallback(() => {
    if (!bridgeReady) return;
    const axisCoordLabelMap = {
      ap: tr('brain3dSliceCoordAp'),
      dv: tr('brain3dSliceCoordDv'),
      ml: tr('brain3dSliceCoordMl'),
    };
    const coordLabel = axisCoordLabelMap[sliceAxisCoordKey] || sliceAxisCoordKey.toUpperCase();
    const selectedLabel = selectedRegions.slice(0, 8).join(', ');
    const topContacts = contactIntervals.slice(0, 3);
    const riskRows = [];
    probePayloadVisible.forEach((probe, idx) => {
      const rows = Array.isArray(probe?.riskSegments) ? probe.riskSegments : [];
      rows.forEach((seg) => {
        riskRows.push({
          probeIndex: idx + 1,
          cls: String(seg?.sourceClass || seg?.class || '--'),
          minDistanceMm: Number(seg?.minDistanceMm ?? Number.POSITIVE_INFINITY),
          dStartUm: Number(seg?.dStartUm ?? 0),
          dEndUm: Number(seg?.dEndUm ?? 0),
        });
      });
    });
    riskRows.sort((a, b) => (a.minDistanceMm || 0) - (b.minDistanceMm || 0));
    const topRisk = riskRows.slice(0, 3);
    const ts = Date.now();
    const sharedLines = [
      `${tr('brain3dSliceAxis')}: ${sliceAxisLabel}`,
      `${tr('brain3dSliceMode')}: ${sliceModeLabel}`,
      `${coordLabel}: ${formatMm(sliceAxisCoord)} mm`,
      `${tr('brain3dSliceThickness')}: ${Math.round(sliceThicknessMm * 1000)} um`,
      `${tr('brain3dSliceProbeFilter')}: ${visibleProbeCount}/${totalProbeCount}`,
      selectedLabel ? `${tr('brain3dSelected')}: ${selectedLabel}` : '',
    ];
    const topContactLine = topContacts.length
      ? `${tr('brain3dContactDepthTable')}: ${topContacts.map((r) => `${r.acronym}[${Math.round(r.dStartUm)}-${Math.round(r.dEndUm)}um]`).join(' | ')}`
      : `${tr('brain3dContactDepthTable')}: --`;
    const topRiskLine = topRisk.length
      ? `${tr('probeCriticalTopRiskLabel')}: ${topRisk.map((r) => `P${r.probeIndex}:${r.cls}@${formatMm(r.minDistanceMm)}mm`).join(' | ')}`
      : `${tr('probeCriticalTopRiskLabel')}: --`;
    enqueueScreenshotExports([
      {
        fileName: `01_slice_${sliceAxis}_${ts}.png`,
        tag: 'slice_export_bundle_slice',
        lines: [
          ...sharedLines,
          `${tr('brain3dExportSliceFigure')}`,
        ],
      },
      {
        fileName: `02_probe_crossline_${ts}.png`,
        tag: 'slice_export_bundle_probe',
        lines: [
          ...sharedLines,
          `${tr('brain3dExportProbeFigure')}`,
          topContactLine,
        ],
      },
      {
        fileName: `03_risk_segment_${ts}.png`,
        tag: 'slice_export_bundle_risk',
        lines: [
          ...sharedLines,
          `${tr('brain3dExportRiskFigure')}`,
          topRiskLine,
        ],
      },
      {
        fileName: `04_case_report_${ts}.png`,
        tag: 'slice_export_bundle_case',
        lines: [
          ...sharedLines,
          `${tr('brain3dExportCaseFigure')}`,
          topContactLine,
          topRiskLine,
        ],
      },
    ]);
  }, [
    bridgeReady,
    contactIntervals,
    enqueueScreenshotExports,
    probePayloadVisible,
    selectedRegions,
    sliceAxis,
    sliceAxisCoord,
    sliceAxisCoordKey,
    sliceAxisLabel,
    sliceModeLabel,
    sliceThicknessMm,
    totalProbeCount,
    tr,
    visibleProbeCount,
  ]);

  useEffect(() => {
    if (!cleanBackendBase || backendStatus !== 'online') {
      setContactProfileLoading(false);
      setContactProfileError('');
      setContactProfileData({
        probes: [],
        contactRegionSet: [],
        samplingStepUm: 25,
        minContactLengthUm: Number(contactMinLengthUm) || 0,
        contactMode: 'cylinder',
        atlasHash: '',
      });
      return undefined;
    }

    if (!Array.isArray(contactRequestProbes) || contactRequestProbes.length <= 0) {
      setContactProfileLoading(false);
      setContactProfileError('');
      setContactProfileData({
        probes: [],
        contactRegionSet: [],
        samplingStepUm: 25,
        minContactLengthUm: Number(contactMinLengthUm) || 0,
        contactMode: 'cylinder',
        atlasHash: '',
      });
      return undefined;
    }

    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setContactProfileLoading(true);
      setContactProfileError('');
      try {
        const token = String(authToken || '').trim();
        const headers = {
          'Content-Type': 'application/json',
        };
        if (token) headers.Authorization = `Bearer ${token}`;
        const res = await fetch(`${cleanBackendBase}/api/probe_contact_profile`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            probes: contactRequestProbes,
            stepUm: 25,
            minContactLengthUm: Number(contactMinLengthUm) || 0,
            contactMode: 'cylinder',
          }),
          signal: controller.signal,
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) {
          const code = String(json?.code || '').trim().toLowerCase();
          if (
            res.status === 503 &&
            (code === 'atlas_not_ready' || code === 'atlas_loading' || code === 'atlas_initializing')
          ) {
            // Atlas is still warming up; avoid surfacing this as a hard error in the contact panel.
            if (!cancelled) {
              setContactProfileError('');
            }
            return;
          }
          const detail = Array.isArray(json?.details) ? json.details.join(' | ') : '';
          throw new Error(json?.error || detail || `HTTP ${res.status}`);
        }
        if (cancelled) return;
        setContactProfileData({
          probes: Array.isArray(json?.probes) ? json.probes : [],
          contactRegionSet: Array.isArray(json?.contactRegionSet) ? json.contactRegionSet : [],
          samplingStepUm: Number(json?.samplingStepUm ?? 25),
          minContactLengthUm: Number(json?.minContactLengthUm ?? 0),
          contactMode: String(json?.contactMode || 'cylinder'),
          atlasHash: String(json?.atlasHash || ''),
        });
      } catch (err) {
        if (cancelled || err?.name === 'AbortError') return;
        setContactProfileError(String(err?.message || tr('brain3dContactError')));
        setContactProfileData({
          probes: [],
          contactRegionSet: [],
          samplingStepUm: 25,
          minContactLengthUm: Number(contactMinLengthUm) || 0,
          contactMode: 'cylinder',
          atlasHash: '',
        });
      } finally {
        if (!cancelled) setContactProfileLoading(false);
      }
    }, 180);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [authToken, backendStatus, cleanBackendBase, contactMinLengthUm, contactRequestProbes, tr]);

  const handleContactIntervalClick = useCallback((row, options = {}) => {
    if (!row || !Number.isFinite(row.probeIndex)) return;
    const probe = contactRequestProbes[row.probeIndex];
    if (!probe) return;
    const opts = {
      includeSelection: options.includeSelection !== false,
      includeFly: options.includeFly !== false,
      includeHighlight: options.includeHighlight !== false,
    };
    setActiveContactIntervalId(String(row.id || ''));

    const midpointUm = (Number(row.dStartUm || 0) + Number(row.dEndUm || 0)) * 0.5;
    const point = probePointAtDepth(probe, midpointUm, invertLr);
    if (point) {
      const min = activeSliceRange.min;
      const max = activeSliceRange.max;
      const axisValue = axisValueOfPoint(point, sliceAxis);
      if (Number.isFinite(axisValue) && Number.isFinite(min) && Number.isFinite(max) && max > min) {
        setActiveSliceNormalized(clamp((axisValue - min) / (max - min), 0, 1));
      }
    }

    if (opts.includeSelection && typeof onToggleRegion === 'function' && row.acronym && !selectedRegionSet.has(row.acronym)) {
      onToggleRegion(row.acronym);
    }
    if (opts.includeSelection && typeof onRegionPicked === 'function' && row.acronym) {
      onRegionPicked({
        key: row.acronym,
        hemisphere: row.hemisphere || null,
        source: 'contact_depth_table',
      });
    }
    if (bridgeReady && row.acronym) {
      if (opts.includeFly) {
        const flyDistance = Number.isFinite(cameraState?.distance)
          ? clamp(Number(cameraState.distance) * 0.85, 8, 20)
          : 12;
        sendToUnityReliable(
          'FocusRegion',
          JSON.stringify({
            key: row.acronym,
            distance: Number(flyDistance.toFixed(2)),
            hemisphere: row.hemisphere || undefined,
            source: 'contact_depth_table',
          }),
        );
      }
      if (opts.includeHighlight) {
        sendToUnityReliable('SetRegionHighlight', JSON.stringify({ key: row.acronym, highlight: true }));
      }
    }
  }, [
    activeSliceRange.max,
    activeSliceRange.min,
    bridgeReady,
    cameraState?.distance,
    contactRequestProbes,
    invertLr,
    onRegionPicked,
    onToggleRegion,
    selectedRegionSet,
    sendToUnityReliable,
    setActiveSliceNormalized,
    sliceAxis,
  ]);

  const handleContactIntervalAction = useCallback((row, action, evt = null) => {
    if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
    if (!row) return;
    if (action === 'snap') {
      handleContactIntervalClick(row, {
        includeSelection: false,
        includeFly: false,
        includeHighlight: true,
      });
      setSliceSnapHint({
        active: true,
        key: row.acronym || '',
        distanceMm: 0,
        axis: sliceAxis,
      });
      return;
    }
    if (action === 'risk') {
      handleContactIntervalClick(row, {
        includeSelection: true,
        includeFly: false,
        includeHighlight: true,
      });
      return;
    }
    if (action === 'export') {
      handleContactIntervalClick(row, {
        includeSelection: true,
        includeFly: false,
        includeHighlight: true,
      });
      handleExportSliceImage();
      return;
    }
    handleContactIntervalClick(row, {
      includeSelection: true,
      includeFly: true,
      includeHighlight: true,
    });
  }, [handleContactIntervalClick, handleExportSliceImage, sliceAxis]);
  const handleContactDepthTableKeyDown = useCallback((evt) => {
    if (!visibleContactIntervals.length) return;
    const key = String(evt?.key || '').toLowerCase();
    let idx = activeContactIntervalIndex;
    if (!Number.isFinite(idx) || idx < 0) idx = 0;
    if (key === 'arrowdown') {
      evt.preventDefault();
      const next = Math.min(visibleContactIntervals.length - 1, idx + 1);
      setActiveContactIntervalId(String(visibleContactIntervals[next]?.id || ''));
      return;
    }
    if (key === 'arrowup') {
      evt.preventDefault();
      const next = Math.max(0, idx - 1);
      setActiveContactIntervalId(String(visibleContactIntervals[next]?.id || ''));
      return;
    }
    const row = visibleContactIntervals[Math.max(0, Math.min(idx, visibleContactIntervals.length - 1))];
    if (!row) return;
    if (key === 'enter' || key === 'f') {
      evt.preventDefault();
      handleContactIntervalAction(row, 'fly');
      return;
    }
    if (key === 's') {
      evt.preventDefault();
      handleContactIntervalAction(row, 'snap');
      return;
    }
    if (key === 'h' || key === 'r') {
      evt.preventDefault();
      handleContactIntervalAction(row, 'risk');
      return;
    }
    if (key === 'e') {
      evt.preventDefault();
      handleContactIntervalAction(row, 'export');
    }
  }, [activeContactIntervalIndex, handleContactIntervalAction, visibleContactIntervals]);

  const requestFullSnapshot = useCallback((source = 'manual', options = {}) => {
    const nonManual = source !== 'manual';
    const now = Date.now();
    if (nonManual && now - lastResyncAtRef.current < RESYNC_COOLDOWN_MS) {
      return false;
    }
    if (nonManual) {
      lastResyncAtRef.current = now;
    }
    if (options.withCamera) {
      fullSnapshotNeedsCameraRef.current = true;
    }
    setSnapshotSeq((v) => v + 1);
    if (nonManual) {
      setBridgeSyncState('resync');
      if (bridgeSyncClearTimerRef.current) {
        window.clearTimeout(bridgeSyncClearTimerRef.current);
      }
      bridgeSyncClearTimerRef.current = window.setTimeout(() => {
        setBridgeSyncState('ok');
      }, RESYNC_CLEAR_DELAY_MS);
    }
    return true;
  }, []);

  const pushFullStateSnapshot = useCallback((options = {}) => {
    if (!bridgeReady) return () => {};
    const includeCamera = Boolean(options.withCamera);
    const cleanups = [];
    const selectedPayload = Array.isArray(selectedRegions) ? selectedRegions : [];
    const probePayload = probePayloadVisible;
    const unityHemisphere = invertLr ? flipHemisphere(hemisphereFilter) : hemisphereFilter;
    const cameraPayload = {
      theta: normalizeTheta(cameraState.theta),
      phi: clamp(cameraState.phi, CAMERA_LIMITS.minPhi, CAMERA_LIMITS.maxPhi),
      distance: clamp(cameraState.distance, CAMERA_LIMITS.minDistance, CAMERA_LIMITS.maxDistance),
      view: cameraState.view,
      locked: Boolean(cameraLocked),
    };
    const snapshot = {
      selectedRegions: selectedPayload,
      probes: probePayload,
      highlightIdx: Number.isFinite(highlightIdx) ? Number(highlightIdx) : -1,
      opacity: Number(mapOpacity(opacitySlider).toFixed(3)),
      coronalSlice: Number(coronalSlice.toFixed(4)),
      slice: {
        axis: sliceAxis,
        mode: sliceMode,
        thicknessMm: Number(sliceThicknessMm.toFixed(3)),
        snapToBoundary: Boolean(sliceSnapEnabled),
        snapThresholdMm: Number(sliceSnapThresholdMm.toFixed(3)),
        normalized: Number(coronalSlice.toFixed(4)),
      },
      selectedOnly: Boolean(selectedOnly),
      hemisphereFilter: unityHemisphere,
      shellVisible: Boolean(showShell),
      axesVisible: Boolean(showAxes),
      probeGuideVisible: Boolean(showProbeGuides),
      uncertaintyVisible: Boolean(showUncertainty),
      weightedOit: Boolean(useWeightedOit),
      mriAppearance: mriAppearancePayload,
      clinicalFocus: {
        enabled: Boolean(clinicalFocus),
        categories: selectedFocusCategories,
        groups: selectedFocusGroups,
        keys: selectedFocusKeys,
      },
      invertLr: Boolean(invertLr),
      postFxPreset,
      antialiasing: antiAliasingMode,
      postFxEnabled: Boolean(postFxEnabled),
      ambientOcclusionEnabled: Boolean(ambientOcclusionEnabled),
      environmentLighting: Number(clamp(environmentLighting, 0, 1).toFixed(3)),
    };
    if (contactOverlayEnabled) {
      snapshot.contactProfile = contactOverlayPayload;
    }
    if (includeCamera) {
      snapshot.camera = cameraPayload;
    }

    cleanups.push(sendToUnityReliable('SetStateSnapshot', JSON.stringify(snapshot)));
    if (unitySupportsBulkState()) {
      return () => {
        cleanups.forEach((cleanup) => cleanup && cleanup());
      };
    }
    cleanups.push(sendToUnityReliable('SetSelectedRegions', JSON.stringify(selectedPayload)));
    cleanups.push(sendToUnityReliable('SetProbes', JSON.stringify(probePayload)));
    cleanups.push(sendToUnityReliable('SetHighlightProbe', String(highlightIdx ?? -1)));
    cleanups.push(sendToUnityReliable('SetOpacity', mapOpacity(opacitySlider).toFixed(3)));
    cleanups.push(
      sendToUnityReliable(
        'SetSlice',
        JSON.stringify({
          axis: sliceAxis,
          enabled: sliceEnabled,
          normalized: Number(coronalSlice.toFixed(4)),
          thicknessMm: Number(sliceThicknessMm.toFixed(3)),
          useSlab: sliceMode === 'slab',
          snapToBoundary: Boolean(sliceSnapEnabled),
          snapThresholdMm: Number(sliceSnapThresholdMm.toFixed(3)),
        }),
      ),
    );
    cleanups.push(sendToUnityReliable('SetSelectedOnly', selectedOnly ? 'true' : 'false'));
    cleanups.push(sendToUnityReliable('SetHemisphereFilter', unityHemisphere));
    cleanups.push(sendToUnityReliable('SetShellVisible', showShell ? 'true' : 'false'));
    cleanups.push(sendToUnityReliable('SetAxesVisible', showAxes ? 'true' : 'false'));
    cleanups.push(sendToUnityReliable('SetProbeGuideVisible', showProbeGuides ? 'true' : 'false'));
    cleanups.push(sendToUnityReliable('SetProbeUncertaintyVisible', showUncertainty ? 'true' : 'false'));
    cleanups.push(
      sendToUnityReliable(
        'SetWeightedOit',
        JSON.stringify({
          enabled: Boolean(useWeightedOit),
          weightScale: Number(oitWeight),
        }),
      ),
    );
    cleanups.push(
      sendToUnityReliable(
        'SetMriAppearance',
        JSON.stringify(mriAppearancePayload),
      ),
    );
    if (contactOverlayEnabled) {
      contactOverlaySentRef.current = true;
      cleanups.push(sendToUnityReliable('SetContactProfile', contactOverlayJson));
    } else if (contactOverlaySentRef.current) {
      contactOverlaySentRef.current = false;
      cleanups.push(sendToUnityReliable('SetContactProfile', contactOverlayDisabledJson));
    }
    cleanups.push(
      sendToUnityReliable(
        'SetClinicalFocus',
        JSON.stringify({
          enabled: Boolean(clinicalFocus),
          categories: selectedFocusCategories,
          groups: selectedFocusGroups,
          keys: selectedFocusKeys,
        }),
      ),
    );
    cleanups.push(sendToUnityReliable('SetPostFxPreset', postFxPreset));
    cleanups.push(sendToUnityReliable('SetAntiAliasingMode', antiAliasingMode));
    cleanups.push(sendToUnityReliable('SetPostFxEnabled', postFxEnabled ? 'true' : 'false'));
    cleanups.push(
      sendToUnityReliable(
        'SetAmbientOcclusionEnabled',
        ambientOcclusionEnabled ? 'true' : 'false',
      ),
    );
    cleanups.push(
      sendToUnityReliable(
        'SetEnvironmentLighting',
        Number(clamp(environmentLighting, 0, 1)).toFixed(3),
      ),
    );
    if (includeCamera) {
      cleanups.push(sendToUnityReliable('SetCameraAngle', JSON.stringify(cameraPayload)));
      cleanups.push(sendToUnityReliable('SetCameraLocked', cameraLocked ? 'true' : 'false'));
      if (cameraState.view && cameraState.view !== 'custom') {
        cleanups.push(sendToUnityReliable('SetCameraView', cameraState.view));
        cleanups.push(sendToUnityReliable('SetView', cameraState.view));
      }
    }
    return () => {
      cleanups.forEach((cleanup) => cleanup && cleanup());
    };
  }, [
    bridgeReady,
    cameraLocked,
    cameraState,
    clinicalFocus,
    contactOverlayPayload,
    contactOverlayJson,
    contactOverlayDisabledJson,
    contactOverlayEnabled,
    coronalSlice,
    hemisphereFilter,
    highlightIdx,
    invertLr,
    opacitySlider,
    probePayloadVisible,
    sliceAxis,
    sliceEnabled,
    sliceMode,
    sliceSnapEnabled,
    sliceSnapThresholdMm,
    sliceThicknessMm,
    selectedOnly,
    selectedFocusCategories,
    selectedFocusGroups,
    selectedFocusKeys,
    selectedRegions,
    sendToUnityReliable,
    showAxes,
    showProbeGuides,
    showShell,
    showUncertainty,
    postFxPreset,
    antiAliasingMode,
    postFxEnabled,
    ambientOcclusionEnabled,
    environmentLighting,
    mriAppearancePayload,
    oitWeight,
    useWeightedOit,
    unitySupportsBulkState,
  ]);

  useEffect(() => {
    pushFullStateSnapshotRef.current = pushFullStateSnapshot;
  }, [pushFullStateSnapshot]);

  useEffect(() => {
    if (!viewportRef.current) return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry?.contentRect) return;
      setViewportSize({
        width: Math.round(entry.contentRect.width),
        height: Math.round(entry.contentRect.height),
      });
    });
    observer.observe(viewportRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onFullscreenChange = () => {
      const target = viewportRef.current;
      setIsFullscreen(Boolean(target) && getFullscreenElement() === target);
    };
    onFullscreenChange();
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    document.addEventListener('mozfullscreenchange', onFullscreenChange);
    document.addEventListener('MSFullscreenChange', onFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', onFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
      document.removeEventListener('mozfullscreenchange', onFullscreenChange);
      document.removeEventListener('MSFullscreenChange', onFullscreenChange);
    };
  }, []);

  useEffect(() => {
    if (!bridgeReady && !instanceReady) {
      handshakeTimerRef.current = window.setTimeout(() => {
        if (!bridgeReady && !instanceReady) setMissingBuild(true);
      }, 12000);
    } else {
      setMissingBuild(false);
    }

    const promoteBridgeReadyFallback = (reason = 'bridge_ready_fallback') => {
      if (bridgeReady || bridgeFallbackPromotedRef.current) return;
      bridgeFallbackPromotedRef.current = true;
      setBridgeReady(false);
      setBridgeSyncState('stale');
      setRuntimeError((prev) => prev || `${tr('brain3dBridgeFallbackHint')} (${reason}: capabilities required)`);
    };

    const onMessage = (event) => {
      if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;
      if (!event.data || event.data.source !== 'BrainPlannerUnity') return;

      const { event: name, data } = event.data;
      if (name === 'capabilities' || name === 'bridgeCapabilities') {
        const parsed = parseJSONSafely(data, {});
        const normalized = normalizeUnityCapabilities(parsed);
        unityCapabilitiesRef.current = normalized;
        setUnityBridgeInfo(summarizeUnityCapabilities(normalized));
        if (bridgeReady) requestFullSnapshot('capabilities_refresh', { withCamera: false });
        return;
      }
      if (name === 'instanceReady') {
        const now = Date.now();
        if (!instanceReadyAtRef.current) {
          instanceReadyAtRef.current = now;
          bridgeFallbackPromotedRef.current = false;
        }
        setInstanceReady(true);
        setMissingBuild(false);
        iframeRef.current?.contentWindow?.postMessage(
          {
            target: 'BrainPlannerUnity',
            method: 'GetCapabilities',
            param: '',
            stateVersion: ++commandVersionRef.current,
            sentAt: now,
            transport: 'inline-json',
          },
          '*',
        );
        if (!bridgeReady && !bridgeReadyFallbackTimerRef.current) {
          bridgeReadyFallbackTimerRef.current = window.setTimeout(() => {
            bridgeReadyFallbackTimerRef.current = null;
            promoteBridgeReadyFallback('bridge_ready_fallback');
          }, 1500);
        } else if (!bridgeReady && now - instanceReadyAtRef.current > 2500) {
          promoteBridgeReadyFallback('bridge_ready_fallback_forced');
        }
        return;
      }
      if (name === 'ready') {
        setBridgeReady(true);
        setBridgeSyncState('ok');
        bridgeStaleRef.current = false;
        heartbeatAckAtRef.current = Date.now();
        setMissingBuild(false);
        if (handshakeTimerRef.current) window.clearTimeout(handshakeTimerRef.current);
        if (bridgeReadyFallbackTimerRef.current) {
          window.clearTimeout(bridgeReadyFallbackTimerRef.current);
          bridgeReadyFallbackTimerRef.current = null;
        }
        bridgeFallbackPromotedRef.current = false;
        const parsed = parseJSONSafely(data, {});
        if (typeof parsed?.regions === 'number') {
          setOutlineProgress((prev) => ({ ...prev, total: parsed.regions }));
        }
        if (typeof parsed?.catalog === 'string' && parsed.catalog.trim()) {
          setCatalogSource(parsed.catalog.trim());
        }
        if (parsed?.capabilities && typeof parsed.capabilities === 'object') {
          const normalized = normalizeUnityCapabilities(parsed.capabilities);
          unityCapabilitiesRef.current = normalized;
          setUnityBridgeInfo(summarizeUnityCapabilities(normalized));
        }
        requestFullSnapshot('ready', { withCamera: true });
        return;
      }
      if (name === 'outlineProgress') {
        const parsed = parseJSONSafely(data, {});
        heartbeatAckAtRef.current = Date.now();
        if (bridgeStaleRef.current) {
          bridgeStaleRef.current = false;
          setBridgeSyncState('resync');
          requestFullSnapshot('outline_progress_recover', { withCamera: false });
        }
        setOutlineProgress({
          loaded: parsed?.loaded ?? 0,
          total: parsed?.total ?? 0,
        });
        return;
      }
      if (name === 'streamStage') {
        const parsed = parseJSONSafely(data, {});
        setStreamStage({
          stage: String(parsed?.stage || '').trim(),
          progress: Number.isFinite(Number(parsed?.progress)) ? Number(parsed.progress) : 0,
          detail: String(parsed?.detail || '').trim(),
          loaded: Number.isFinite(Number(parsed?.loaded)) ? Number(parsed.loaded) : 0,
          total: Number.isFinite(Number(parsed?.total)) ? Number(parsed.total) : 0,
        });
        return;
      }
      if (name === 'stateAck' || name === 'ack') {
        const parsed = parseJSONSafely(data, data) || {};
        const method = typeof parsed?.method === 'string' ? parsed.method : '';
        const version = Number(parsed?.stateVersion ?? parsed?.version);
        if (method && Number.isFinite(version)) {
          const prev = ackedVersionByMethodRef.current.get(method) || 0;
          if (version >= prev) {
            ackedVersionByMethodRef.current.set(method, version);
          }
          updateBridgeMetrics({
            acked: 1,
            lastAckLatencyMs: Number.isFinite(Number(parsed?.sentAt))
              ? Math.max(0, Date.now() - Number(parsed.sentAt))
              : bridgeMetricsRef.current.lastAckLatencyMs,
          });
          if (method === 'Heartbeat') {
            heartbeatAckAtRef.current = Date.now();
            if (bridgeStaleRef.current) {
              bridgeStaleRef.current = false;
              requestFullSnapshot('heartbeat_recover', { withCamera: true });
            }
          }
        }
        return;
      }
      if (name === 'heartbeatAck') {
        heartbeatAckAtRef.current = Date.now();
        if (bridgeStaleRef.current) {
          bridgeStaleRef.current = false;
          requestFullSnapshot('heartbeat_recover', { withCamera: true });
        }
        return;
      }
      if (name === 'snapshotApplied') {
        bridgeStaleRef.current = false;
        setBridgeSyncState('ok');
        if (bridgeSyncClearTimerRef.current) {
          window.clearTimeout(bridgeSyncClearTimerRef.current);
          bridgeSyncClearTimerRef.current = null;
        }
        return;
      }
      if (name === 'focusRegionAck') {
        const parsed = parseJSONSafely(data, {});
        const key = getRegionKeyFromPayload(parsed);
        if (!key) return;
        setRecentPick((prev) => (prev && prev.key === key ? { ...prev, ts: Date.now() } : prev));
        return;
      }
      if (name === 'regionClicked' || name === 'regionClick' || name === 'regionSelected' || name === 'toggleRegion') {
        const parsed = parseJSONSafely(data, data);
        const key = getRegionKeyFromPayload(parsed);
        const rawHemisphere = parseHemisphere(parsed);
        const hemisphere = invertLr ? flipHemisphere(rawHemisphere) : rawHemisphere;
        if (!key) return;
        const world = parsed && typeof parsed === 'object' ? parsed.world || null : null;
        const coordsObj = parsed && typeof parsed === 'object' ? parsed.coords || null : null;
        const coords = {
          ap: Number.isFinite(Number(coordsObj?.ap))
            ? Number(coordsObj.ap)
            : Number.isFinite(Number(world?.z))
              ? -Number(world.z)
              : null,
          dv: Number.isFinite(Number(coordsObj?.dv))
            ? Number(coordsObj.dv)
            : Number.isFinite(Number(world?.y))
              ? -Number(world.y)
              : null,
          ml: Number.isFinite(Number(coordsObj?.ml))
            ? Number(coordsObj.ml)
            : Number.isFinite(Number(world?.x))
              ? Number(world.x)
              : null,
        };

        setRecentPick({ key, hemisphere, coords, ts: Date.now() });
        if (hemisphere) setRegionSideHints((prev) => ({ ...prev, [key]: hemisphere }));
        sendToUnityReliable('SetRegionHighlight', JSON.stringify({ key, highlight: true }));
        window.dispatchEvent(
          new CustomEvent('probeplanner:focus-region', {
            detail: { key, hemisphere, source: 'brain3d' },
          }),
        );
        if (typeof onRegionPicked === 'function') onRegionPicked({ key, hemisphere });
        if (typeof onToggleRegion === 'function') onToggleRegion(key);
        return;
      }
      if (name === 'screenshot') {
        const parsed = parseJSONSafely(data, {});
        const raw = typeof parsed?.data === 'string' ? parsed.data : '';
        const finalizeAndContinue = () => {
          screenshotExportBusyRef.current = false;
          if (!Array.isArray(screenshotExportQueueRef.current) || screenshotExportQueueRef.current.length <= 0) return;
          if (screenshotExportPumpTimerRef.current) window.clearTimeout(screenshotExportPumpTimerRef.current);
          screenshotExportPumpTimerRef.current = window.setTimeout(() => {
            screenshotExportPumpTimerRef.current = null;
            pumpScreenshotExportQueue();
          }, 110);
        };
        if (!raw.startsWith('data:image/')) {
          finalizeAndContinue();
          return;
        }
        const meta = sliceScreenshotMetaRef.current || {};
        annotateAndDownloadScreenshot(
          raw,
          meta.fileName || `slice_${Date.now()}.png`,
          Array.isArray(meta.lines) ? meta.lines : [],
        ).catch((err) => {
          setRuntimeError(`Screenshot export failed: ${err?.message || 'unknown error'}`);
        }).finally(() => {
          finalizeAndContinue();
        });
        sliceScreenshotMetaRef.current = null;
        return;
      }
      if (name === 'cameraPose') {
        if (cameraDragActiveRef.current) return;
        if (Date.now() < suppressPoseUntilRef.current) return;
        const parsed = parseJSONSafely(data, {});
        const theta = Number(parsed?.theta);
        const phi = Number(parsed?.phi);
        const distance = Number(parsed?.distance);
        if (!Number.isFinite(theta) || !Number.isFinite(phi) || !Number.isFinite(distance)) return;
        const normalizedTheta = normalizeTheta(theta);
        const normalizedPhi = clamp(phi, CAMERA_LIMITS.minPhi, CAMERA_LIMITS.maxPhi);
        const normalizedDistance = clamp(distance, CAMERA_LIMITS.minDistance, CAMERA_LIMITS.maxDistance);
        setCameraState({
          theta: normalizedTheta,
          phi: normalizedPhi,
          distance: normalizedDistance,
          view: inferViewFromPose(normalizedTheta, normalizedPhi, normalizedDistance),
        });
        return;
      }
      if (name === 'error') {
        const msg = String(data || 'Unity runtime error');
        if (/SetEnvironmentLighting/i.test(msg)) return;
        if (/SetSlice/i.test(msg) && /Maximum call stack size exceeded/i.test(msg)) return;
        if (/SetContactProfile/i.test(msg)) {
          setContactProfileError(tr('brain3dContactError'));
          return;
        }
        if (
          bridgeReady ||
          !/bridge|ready|init|undefined|not found|sendmessage/i.test(msg)
        ) {
          setRuntimeError(msg);
        }
        setMissingBuild(false);
        return;
      }
      if (name === 'fillSuppressed') {
        const parsed = parseJSONSafely(data, {});
        const key = getRegionKeyFromPayload(parsed) || String(parsed?.key || '').trim();
        const reason = String(parsed?.reason || '').trim();
        const reasonLabelMap = {
          aggregate_parent: tr('brain3dFillSuppressedReasonParent'),
          aggregate_subregions: tr('brain3dFillSuppressedReasonSubregions'),
          aggregate_volume: tr('brain3dFillSuppressedReasonVolume'),
          aggregate_extent: tr('brain3dFillSuppressedReasonExtent'),
        };
        const reasonLabel = reasonLabelMap[reason] || tr('brain3dFillSuppressedReasonGeneric');
        const keyLabel = key ? ` (${key})` : '';
        setFillNotice(`${tr('brain3dFillSuppressed')}${keyLabel}: ${reasonLabel}`);
        if (fillNoticeTimerRef.current) window.clearTimeout(fillNoticeTimerRef.current);
        fillNoticeTimerRef.current = window.setTimeout(() => {
          setFillNotice('');
          fillNoticeTimerRef.current = null;
        }, 9000);
      }
    };

    window.addEventListener('message', onMessage);
    return () => {
      window.removeEventListener('message', onMessage);
      if (handshakeTimerRef.current) window.clearTimeout(handshakeTimerRef.current);
      if (bridgeSyncClearTimerRef.current) window.clearTimeout(bridgeSyncClearTimerRef.current);
      if (bridgeReadyFallbackTimerRef.current) window.clearTimeout(bridgeReadyFallbackTimerRef.current);
      if (fillNoticeTimerRef.current) window.clearTimeout(fillNoticeTimerRef.current);
      instanceReadyAtRef.current = 0;
      bridgeFallbackPromotedRef.current = false;
    };
  }, [bridgeReady, instanceReady, invertLr, onRegionPicked, onToggleRegion, pumpScreenshotExportQueue, requestFullSnapshot, sendToUnityReliable, tr, updateBridgeMetrics]);

  useEffect(() => () => {
    if (screenshotExportPumpTimerRef.current) {
      window.clearTimeout(screenshotExportPumpTimerRef.current);
      screenshotExportPumpTimerRef.current = null;
    }
    if (commandFlushTimerRef.current) {
      window.clearTimeout(commandFlushTimerRef.current);
      commandFlushTimerRef.current = null;
    }
    commandQueueRef.current.forEach((entry) => {
      if (entry?.timers) entry.timers.forEach((tid) => window.clearTimeout(tid));
    });
    commandQueueRef.current.clear();
  }, []);

  useEffect(() => {
    if (!bridgeReady) {
      bridgeStaleRef.current = false;
      setBridgeSyncState('ok');
      return undefined;
    }
    heartbeatAckAtRef.current = Date.now();
    const tick = () => {
      const now = Date.now();
      const total = Number(outlineProgress.total || 0);
      const loaded = Number(outlineProgress.loaded || 0);
      const meshStreaming = total > 0 && loaded < total;
      const timeoutMs = meshStreaming ? HEARTBEAT_TIMEOUT_LOADING_MS : HEARTBEAT_TIMEOUT_MS;
      sendToUnity('Heartbeat', JSON.stringify({ ts: now }));
      if (now - heartbeatAckAtRef.current > timeoutMs) {
        if (!bridgeStaleRef.current) {
          bridgeStaleRef.current = true;
          setBridgeSyncState('stale');
        }
      }
    };
    tick();
    const timer = window.setInterval(tick, HEARTBEAT_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [bridgeReady, outlineProgress.loaded, outlineProgress.total, sendToUnity]);

  useEffect(() => {
    if (!bridgeReady) {
      meshStallWatchRef.current = { loaded: -1, total: -1, at: Date.now() };
      return undefined;
    }
    const loaded = Number(outlineProgress.loaded || 0);
    const total = Number(outlineProgress.total || 0);
    const prev = meshStallWatchRef.current;
    const changed = loaded !== prev.loaded || total !== prev.total;
    if (changed) {
      meshStallWatchRef.current = { loaded, total, at: Date.now() };
      return undefined;
    }
    const timer = window.setInterval(() => {
      const watch = meshStallWatchRef.current;
      const now = Date.now();
      const inMeshStreaming = watch.total > 0 && watch.loaded < watch.total;
      if (!inMeshStreaming) return;
      if (now - watch.at < MESH_STALL_RESYNC_MS) return;
      requestFullSnapshot('mesh_stage_stall_auto', { withCamera: false });
      meshStallWatchRef.current = { ...watch, at: now };
    }, 4000);
    return () => window.clearInterval(timer);
  }, [bridgeReady, outlineProgress.loaded, outlineProgress.total, requestFullSnapshot]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    const includeCamera = fullSnapshotNeedsCameraRef.current;
    fullSnapshotNeedsCameraRef.current = false;
    const push = pushFullStateSnapshotRef.current;
    if (typeof push !== 'function') return undefined;
    return push({ withCamera: includeCamera });
  }, [bridgeReady, snapshotSeq]);

  useEffect(() => {
    if (!ENABLE_MESH_HEALTHCHECK) return undefined;
    if (!bridgeReady || runtimeError) return undefined;
    if ((outlineProgress.total ?? 0) <= 0) return undefined;
    if ((outlineProgress.loaded ?? 0) > 0) return undefined;
    const cleanBase = String(backendBaseUrl || '').trim().replace(/\/+$/, '');
    if (!cleanBase) return undefined;

    const timer = window.setTimeout(async () => {
      const token = String(authToken || '').trim();
      const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
      const candidates = [
        `${cleanBase}/api/allen_mesh_lod/315?lod=0`,
        `${cleanBase}/api/allen_mesh/315`,
      ];
      let lastStatus = null;
      let lastError = '';
      for (const url of candidates) {
        try {
          const res = await fetch(url, {
            method: 'GET',
            headers,
            signal: AbortSignal.timeout(8000),
          });
          if (res.ok) {
            setRuntimeError('');
            return;
          }
          lastStatus = res.status;
        } catch (err) {
          lastError = err?.message || 'network error';
        }
      }
      if (lastStatus != null) {
        setRuntimeError(`Mesh endpoint HTTP ${lastStatus} @ ${cleanBase}`);
      } else {
        setRuntimeError(`Mesh endpoint unavailable @ ${cleanBase}: ${lastError || 'network error'}`);
      }
    }, 9000);

    return () => window.clearTimeout(timer);
  }, [authToken, backendBaseUrl, bridgeReady, outlineProgress.loaded, outlineProgress.total, runtimeError]);

  useEffect(() => {
    if ((outlineProgress.loaded ?? 0) > 0 && typeof runtimeError === 'string' && runtimeError.startsWith('Mesh endpoint')) {
      setRuntimeError('');
    }
  }, [outlineProgress.loaded, runtimeError]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    const check = validateRegions(selectedRegions);
    if (!check.ok) {
      setContractError(`${tr('bridgeContractViolationLabel')}: ${check.reason}`);
      return undefined;
    }
    setContractError('');
    return sendToUnityReliable('SetSelectedRegions', JSON.stringify(selectedRegions));
  }, [bridgeReady, selectedRegions, selectedRegionsSignature, sendToUnityReliable, tr]);

  useEffect(() => () => stopCoronalSliceDrag(), [stopCoronalSliceDrag]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    const payload = probePayloadVisible;
    const check = validateProbes(payload);
    if (!check.ok) {
      setContractError(`${tr('bridgeContractViolationLabel')}: ${check.reason}`);
      return undefined;
    }
    setContractError('');
    return sendToUnityReliable('SetProbes', JSON.stringify(payload));
  }, [bridgeReady, probePayloadVisible, probeSyncSignature, sendToUnityReliable, tr]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetHighlightProbe', String(highlightIdx ?? -1));
  }, [bridgeReady, highlightIdx, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetOpacity', mapOpacity(opacitySlider).toFixed(3));
  }, [bridgeReady, opacitySlider, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
      'SetSlice',
      JSON.stringify({
        axis: sliceAxis,
        enabled: sliceEnabled,
        normalized: Number(coronalSlice.toFixed(4)),
        thicknessMm: Number(sliceThicknessMm.toFixed(3)),
        useSlab: sliceMode === 'slab',
        snapToBoundary: Boolean(sliceSnapEnabled),
        snapThresholdMm: Number(sliceSnapThresholdMm.toFixed(3)),
      }),
    );
  }, [
    bridgeReady,
    coronalSlice,
    sendToUnityReliable,
    sliceAxis,
    sliceEnabled,
    sliceMode,
    sliceSnapEnabled,
    sliceSnapThresholdMm,
    sliceThicknessMm,
  ]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    if (contactOverlayEnabled) {
      contactOverlaySentRef.current = true;
      return sendToUnityReliable('SetContactProfile', contactOverlayJson);
    }
    if (contactOverlaySentRef.current) {
      contactOverlaySentRef.current = false;
      return sendToUnityReliable('SetContactProfile', contactOverlayDisabledJson);
    }
    return undefined;
  }, [bridgeReady, contactOverlayDisabledJson, contactOverlayEnabled, contactOverlayJson, sendToUnityReliable]);

  useEffect(() => {
    if (!AUTO_FOLLOW_FOCUSED_REGION_SLICE) return;
    if (sliceDragActive) return;
    if (!focusedRegion) return;
    const reg = brainRegions?.[focusedRegion];
    if (!reg || !Number.isFinite(reg.ap) || !Number.isFinite(reg.dv) || !Number.isFinite(reg.ml)) return;
    const hemiHint =
      regionSideHints[focusedRegion] ||
      (hemisphereFilter === 'all' ? 'left' : hemisphereFilter);
    const world = regionToWorld(reg, hemiHint, invertLr);
    const coord = axisValueOfPoint(world, sliceAxis);
    const min = activeSliceRange.min;
    const max = activeSliceRange.max;
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return;
    const normalized = clamp((coord - min) / (max - min), 0, 1);
    setActiveSliceNormalized(normalized);
  }, [
    activeSliceRange.max,
    activeSliceRange.min,
    brainRegions,
    focusedRegion,
    hemisphereFilter,
    invertLr,
    regionSideHints,
    setActiveSliceNormalized,
    sliceAxis,
    sliceDragActive,
  ]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetSelectedOnly', selectedOnly ? 'true' : 'false');
  }, [bridgeReady, selectedOnly, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
        'SetClinicalFocus',
        JSON.stringify({
          enabled: Boolean(clinicalFocus),
          categories: selectedFocusCategories,
          groups: selectedFocusGroups,
          keys: selectedFocusKeys,
        }),
      );
  }, [bridgeReady, clinicalFocus, selectedFocusCategories, selectedFocusGroups, selectedFocusKeys, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    const unityFilter = invertLr ? flipHemisphere(hemisphereFilter) : hemisphereFilter;
    return sendToUnityReliable('SetHemisphereFilter', unityFilter);
  }, [bridgeReady, hemisphereFilter, invertLr, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetShellVisible', showShell ? 'true' : 'false');
  }, [bridgeReady, showShell, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetAxesVisible', showAxes ? 'true' : 'false');
  }, [bridgeReady, showAxes, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetProbeGuideVisible', showProbeGuides ? 'true' : 'false');
  }, [bridgeReady, showProbeGuides, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetProbeUncertaintyVisible', showUncertainty ? 'true' : 'false');
  }, [bridgeReady, showUncertainty, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
      'SetWeightedOit',
      JSON.stringify({
        enabled: Boolean(useWeightedOit),
        weightScale: Number(oitWeight),
      }),
    );
  }, [bridgeReady, oitWeight, useWeightedOit, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
      'SetMriAppearance',
      JSON.stringify(mriAppearancePayload),
    );
  }, [bridgeReady, mriAppearancePayload, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetPostFxPreset', postFxPreset);
  }, [bridgeReady, postFxPreset, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetAntiAliasingMode', antiAliasingMode);
  }, [bridgeReady, antiAliasingMode, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetPostFxEnabled', postFxEnabled ? 'true' : 'false');
  }, [bridgeReady, postFxEnabled, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    if (sliceDragActive) {
      const cleanups = [
        sendToUnityReliable('SetInteractionQuality', 'low'),
        sendToUnityReliable('SetPostFxEnabled', 'false'),
        sendToUnityReliable(
          'SetWeightedOit',
          JSON.stringify({ enabled: false, weightScale: Number(oitWeight) }),
        ),
        sendToUnityReliable('SetAntiAliasingMode', 'none'),
      ];
      return () => cleanups.forEach((cleanup) => cleanup && cleanup());
    }
    const cleanups = [
      sendToUnityReliable('SetInteractionQuality', 'high'),
      sendToUnityReliable('SetPostFxEnabled', postFxEnabled ? 'true' : 'false'),
      sendToUnityReliable(
        'SetWeightedOit',
        JSON.stringify({ enabled: Boolean(useWeightedOit), weightScale: Number(oitWeight) }),
      ),
      sendToUnityReliable('SetAntiAliasingMode', antiAliasingMode),
    ];
    return () => cleanups.forEach((cleanup) => cleanup && cleanup());
  }, [
    antiAliasingMode,
    bridgeReady,
    oitWeight,
    postFxEnabled,
    sendToUnityReliable,
    sliceDragActive,
    useWeightedOit,
  ]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
      'SetAmbientOcclusionEnabled',
      ambientOcclusionEnabled ? 'true' : 'false',
    );
  }, [ambientOcclusionEnabled, bridgeReady, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable(
      'SetEnvironmentLighting',
      Number(clamp(environmentLighting, 0, 1)).toFixed(3),
    );
  }, [bridgeReady, environmentLighting, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    return sendToUnityReliable('SetCameraLocked', cameraLocked ? 'true' : 'false');
  }, [bridgeReady, cameraLocked, sendToUnityReliable]);

  useEffect(() => {
    if (!bridgeReady) return undefined;
    const next = new Set(selectedRegions);
    const prev = highlightStateRef.current;
    const cleanups = [];

    prev.forEach((key) => {
      if (!next.has(key)) cleanups.push(sendToUnityReliable('SetRegionHighlight', JSON.stringify({ key, highlight: false })));
    });
    next.forEach((key) => {
      if (!prev.has(key)) cleanups.push(sendToUnityReliable('SetRegionHighlight', JSON.stringify({ key, highlight: true })));
    });
    highlightStateRef.current = next;
    return () => cleanups.forEach((cleanup) => cleanup && cleanup());
  }, [bridgeReady, selectedRegions, selectedRegionsSignature, sendToUnityReliable]);

  const percent = useMemo(() => {
    const total = outlineProgress.total || 0;
    if (!total) return 0;
    return Math.round((outlineProgress.loaded / total) * 100);
  }, [outlineProgress]);

  const interactiveOutlineTarget = useMemo(() => {
    const total = Number(outlineProgress.total || 0);
    if (total <= 0) return 0;
    if (total <= 120) return total;
    return Math.max(120, Math.round(total * 0.18));
  }, [outlineProgress.total]);

  const outlinesInteractiveReady =
    interactiveOutlineTarget > 0 && (outlineProgress.loaded || 0) >= interactiveOutlineTarget;
  const allOutlinesLoaded = outlineProgress.total > 0 && outlineProgress.loaded >= outlineProgress.total;
  const slicePercent = Math.round(clamp(coronalSlice, 0, 1) * 100);
  const sliceThumbTop = `${(1 - clamp(coronalSlice, 0, 1)) * 100}%`;
  const catalogReady = bridgeReady && catalogSource !== 'unknown';
  const interactReady = bridgeReady && catalogReady && outlinesInteractiveReady && bridgeSyncState !== 'stale';
  const bridgeStageFlow = useMemo(
    () => ([
      { key: 'bridge', label: tr('brain3dStageBridge'), ready: bridgeReady },
      { key: 'catalog', label: tr('brain3dStageCatalog'), ready: catalogReady },
      { key: 'mesh', label: tr('brain3dStageMesh'), ready: outlinesInteractiveReady },
      { key: 'interact', label: tr('brain3dStageInteract'), ready: interactReady },
    ]),
    [bridgeReady, catalogReady, interactReady, outlinesInteractiveReady, tr],
  );
  const sliceSnapHintText = useMemo(() => {
    if (!sliceSnapEnabled) return tr('brain3dSliceSnapHintOff');
    if (sliceSnapHint?.active) {
      const key = sliceSnapHint.key || '--';
      const dist = Number.isFinite(sliceSnapHint.distanceMm) ? formatMm(sliceSnapHint.distanceMm) : '--';
      return `${tr('brain3dSliceSnapHintActive')}: ${key}  |  Δ${dist} mm`;
    }
    if (sliceDragActive && Number.isFinite(sliceSnapHint?.distanceMm)) {
      return `${tr('brain3dSliceSnapHintReady')}  |  Δ${formatMm(sliceSnapHint.distanceMm)} mm`;
    }
    return tr('brain3dSliceSnapHintReady');
  }, [sliceDragActive, sliceSnapEnabled, sliceSnapHint, tr]);

  const selectedAnchors = useMemo(() => {
    const selectedKeys = parseJSONSafely(selectedRegionsSignature, selectedRegions);
    if (!Array.isArray(selectedKeys) || !selectedKeys.length || !viewportSize.width || !viewportSize.height) return [];
    const basis = getCameraBasis(cameraState);
    const out = [];

    selectedKeys.forEach((key) => {
      const reg = brainRegions?.[key];
      if (!reg || !Number.isFinite(reg.ap) || !Number.isFinite(reg.dv) || !Number.isFinite(reg.ml)) return;
      const candidateHemispheres = hemisphereFilter === 'all' ? ['left', 'right'] : [hemisphereFilter];
      let best = null;

      candidateHemispheres.forEach((hemi) => {
        const world = regionToWorld(reg, hemi, invertLr);
        if (
          sliceEnabled &&
          !isPointVisibleInSlice(
            world,
            sliceAxis,
            activeSliceCoord,
            sliceMode,
            sliceMode === 'slab' ? sliceThicknessMm : 0,
          )
        ) {
          return;
        }
        const projected = projectPoint(world, basis, viewportSize.width, viewportSize.height);
        if (!projected) return;
        const hint = regionSideHints[key];
        let score = projected.depth;
        if (hint && hint === hemi) score -= 0.3;
        if (!best || score < best.score) {
          best = {
            key,
            hemisphere: hemi,
            x: projected.x,
            y: projected.y,
            depth: projected.depth,
            radiusPx: getAnchorRadius(projected.depth),
            score,
          };
        }
      });

      if (best) out.push(best);
    });

    return declutterAnchors(out, viewportSize.width, viewportSize.height);
  }, [
    activeSliceCoord,
    brainRegions,
    cameraState,
    hemisphereFilter,
    invertLr,
    regionSideHints,
    selectedRegions,
    selectedRegionsSignature,
    sliceAxis,
    sliceEnabled,
    sliceMode,
    sliceThicknessMm,
    viewportSize,
  ]);

  const sideCoverageBadges = useMemo(() => {
    if (!sideCoverage || typeof sideCoverage !== 'object') return [];
    return Object.entries(sideCoverage)
      .map(([key, value]) => {
        const left = Boolean(value?.left);
        const right = Boolean(value?.right);
        if (!left && !right) return null;
        return {
          key,
          label: left && right ? 'L/R' : left ? 'L' : 'R',
          bilateral: left && right,
        };
      })
      .filter(Boolean)
      .slice(0, 8);
  }, [sideCoverage]);

  const handleViewPreset = useCallback((view) => {
    const preset = CAMERA_PRESETS[view] || CAMERA_PRESETS.iso;
    setCameraState((prev) => ({ ...prev, ...preset, view }));
    suppressPoseUntilRef.current = Date.now() + CAMERA_POSE_SUPPRESS_MS;
    // Multi-method dispatch for compatibility with different Unity bridge builds.
    sendToUnityReliable('SetCameraView', view);
    sendToUnityReliable('SetView', view);
    sendToUnityReliable('SetCameraPreset', view);
    sendToUnityReliable(
      'SetCameraAngle',
      JSON.stringify({
        theta: normalizeTheta(preset.theta),
        phi: clamp(preset.phi, CAMERA_LIMITS.minPhi, CAMERA_LIMITS.maxPhi),
        distance: clamp(preset.distance, CAMERA_LIMITS.minDistance, CAMERA_LIMITS.maxDistance),
      }),
    );
  }, [sendToUnityReliable]);

  const applyViewTemplate = useCallback((template) => {
    const allClasses = CLINICAL_CLASS_ORDER.reduce((acc, key) => {
      acc[key] = true;
      return acc;
    }, {});
    const targetClasses = {
      ...allClasses,
      pathway: false,
      nerve_fissure: false,
    };
    if (template === 'overview') {
      setSelectedOnly(false);
      setClinicalFocus(true);
      setLayerVisibility({ L1: true, L2: false, L3: false, L4: false });
      setClassVisibility(targetClasses);
      setShowShell(true);
      setShowAxes(false);
      setShowProbeGuides(false);
      setShowUncertainty(false);
      setUseWeightedOit(true);
      setPostFxPreset('pinpoint');
      setAntiAliasingMode('taa');
      setPostFxEnabled(true);
      setAmbientOcclusionEnabled(true);
      setEnvironmentLighting(0.66);
      setActiveTemplate('overview');
      handleViewPreset('iso');
      return;
    }
    if (template === 'target') {
      setSelectedOnly(false);
      setClinicalFocus(true);
      setLayerVisibility({ ...TARGET_TEMPLATE_LAYER_VISIBILITY });
      setClassVisibility({ ...TARGET_TEMPLATE_CLASS_VISIBILITY });
      setShowShell(false);
      setShowAxes(false);
      setShowProbeGuides(true);
      setShowUncertainty(false);
      setUseWeightedOit(true);
      setPostFxPreset('pinpoint');
      setAntiAliasingMode('taa');
      setPostFxEnabled(true);
      setAmbientOcclusionEnabled(true);
      setEnvironmentLighting(TARGET_TEMPLATE_ENV_LIGHT);
      setActiveTemplate('target');
      handleViewPreset('side');
      return;
    }
    if (template === 'risk') {
      setSelectedOnly(false);
      setClinicalFocus(true);
      setLayerVisibility({ L1: true, L2: true, L3: false, L4: false });
      setClassVisibility(targetClasses);
      setShowShell(true);
      setShowAxes(false);
      setShowProbeGuides(true);
      setShowUncertainty(false);
      setUseWeightedOit(true);
      setPostFxPreset('publication');
      setAntiAliasingMode('smaa');
      setPostFxEnabled(true);
      setAmbientOcclusionEnabled(true);
      setEnvironmentLighting(0.68);
      setContactDisplayMode('slice_contact_persist');
      setContactDisplayStyle('hybrid');
      setSliceFilterProbes(true);
      setActiveTemplate('risk');
      handleViewPreset('side');
      return;
    }
    if (template === 'fine') {
      setSelectedOnly(false);
      setClinicalFocus(false);
      setLayerVisibility({ L1: true, L2: true, L3: true, L4: true });
      setClassVisibility(allClasses);
      setShowShell(true);
      setShowAxes(true);
      setShowProbeGuides(true);
      setShowUncertainty(false);
      setUseWeightedOit(true);
      setPostFxPreset('publication');
      setAntiAliasingMode('smaa');
      setPostFxEnabled(true);
      setAmbientOcclusionEnabled(true);
      setEnvironmentLighting(0.58);
      setActiveTemplate('fine');
      handleViewPreset('top');
      return;
    }
    if (template === 'mri') {
      setSelectedOnly(false);
      setClinicalFocus(true);
      setLayerVisibility({ L1: true, L2: true, L3: false, L4: false });
      setClassVisibility(targetClasses);
      setShowShell(true);
      setShowAxes(false);
      setShowProbeGuides(true);
      setShowUncertainty(false);
      setUseWeightedOit(true);
      setOitWeight(0.85);
      setPostFxPreset('mri');
      setAntiAliasingMode('taa');
      setPostFxEnabled(true);
      setAmbientOcclusionEnabled(true);
      setEnvironmentLighting(0.82);
      setMriAppearanceLayers({
        outerBoundary: true,
        skull: true,
        brainSurface: true,
        ccfLabels: true,
        overlay: true,
      });
      if (isMriHybridMode) setUseMriAppearance(true);
      setActiveTemplate('mri');
      handleViewPreset('iso');
    }
  }, [handleViewPreset, isMriHybridMode]);

  useEffect(() => {
    if (templateBootstrapRef.current) return;
    templateBootstrapRef.current = true;
    applyViewTemplate('target');
  }, [applyViewTemplate]);

  const handleCameraAngle = useCallback((nextCamera) => {
    const payload = {
      theta: normalizeTheta(nextCamera.theta),
      phi: clamp(nextCamera.phi, CAMERA_LIMITS.minPhi, CAMERA_LIMITS.maxPhi),
      distance: clamp(nextCamera.distance, CAMERA_LIMITS.minDistance, CAMERA_LIMITS.maxDistance),
    };
    setCameraState((prev) => ({
      ...prev,
      theta: payload.theta,
      phi: payload.phi,
      distance: payload.distance,
      view: 'custom',
    }));
    suppressPoseUntilRef.current = Date.now() + CAMERA_POSE_SUPPRESS_MS;
    sendToUnityReliable('SetCameraAngle', JSON.stringify(payload));
  }, [sendToUnityReliable]);

  const toggleFullscreen = useCallback(async () => {
    const target = viewportRef.current;
    if (!target) return;
    try {
      if (getFullscreenElement() === target) {
        await exitFullscreenSafe();
      } else {
        await requestFullscreenSafe(target);
      }
    } catch {
      // Ignore fullscreen API errors (permissions/gesture constraints).
    }
  }, []);

  const handleBlankAction = useCallback((action) => {
    if (action === 'reload_unity') {
      const iframe = iframeRef.current;
      try {
        if (iframe?.contentWindow?.location) {
          iframe.contentWindow.location.reload();
          return;
        }
      } catch {
        // no-op
      }
      setSnapshotSeq((v) => v + 1);
      return;
    }
    if (action === 'resync' || action === 'retry_mesh') {
      requestFullSnapshot('manual_resync', { withCamera: true });
      return;
    }
    if (action === 'connect_backend') {
      window.dispatchEvent(new CustomEvent('probeplanner:focus-backend-panel'));
    }
  }, [requestFullSnapshot]);
  const handleFlyToRecentPick = useCallback(() => {
    if (!recentPick?.key || !bridgeReady) return;
    sendToUnityReliable(
      'FocusRegion',
      JSON.stringify({
        key: recentPick.key,
        distance: Number.isFinite(cameraState?.distance) ? Math.min(22, Math.max(9, cameraState.distance)) : 12,
      }),
    );
  }, [bridgeReady, cameraState?.distance, recentPick?.key, sendToUnityReliable]);

  const bannerCount = (runtimeError ? 1 : 0) + (contractError ? 1 : 0) + (fillNotice ? 1 : 0);
  const topOffset = bannerCount * 26;
  const probeColor = PROBE_COLORS[probePreset] || '#00d4ff';
  const blankState = useMemo(() => {
    if (runtimeError || contractError) {
      return {
        key: 'error',
        progress: 0,
        title: tr('brain3dEmptyError'),
        detail: runtimeError || contractError,
        actions: ['reload_unity', 'resync'],
      };
    }
    if (backendStatus !== 'online') {
      return {
        key: 'backend_offline',
        progress: 0,
        title: tr('brain3dEmptyBackendOffline'),
        detail: tr('brain3dEmptyBackendOfflineHint'),
        actions: ['connect_backend'],
      };
    }
    if (!instanceReady) {
      return {
        key: 'waiting_unity',
        progress: 20,
        title: tr('brain3dEmptyWaitingUnity'),
        detail: tr('brain3dEmptyWaitingUnityHint'),
        actions: ['reload_unity'],
      };
    }
    if (!bridgeReady) {
      return {
        key: 'waiting_bridge',
        progress: 40,
        title: tr('brain3dEmptyWaitingBridge'),
        detail: tr('brain3dEmptyWaitingBridgeHint'),
        actions: ['resync'],
      };
    }
    if (!allOutlinesLoaded && !outlinesInteractiveReady) {
      const stageDetail = streamStage.detail || streamStage.stage;
      return {
        key: 'waiting_mesh',
        progress: Math.max(45, Math.min(99, 45 + Math.round((streamStage.progress > 0 ? streamStage.progress : percent) * 0.55))),
        title: tr('brain3dEmptyWaitingMesh'),
        detail: stageDetail
          ? `${tr('brain3dLoading')} ${outlineProgress.loaded}/${outlineProgress.total} · ${stageDetail}`
          : `${tr('brain3dLoading')} ${outlineProgress.loaded}/${outlineProgress.total}`,
        actions: ['retry_mesh'],
      };
    }
    if (!allOutlinesLoaded) {
      const stageDetail = streamStage.detail || streamStage.stage;
      return {
        key: 'streaming_mesh',
        progress: Math.max(85, Math.min(99, streamStage.progress > 0 ? streamStage.progress : percent)),
        title: tr('brain3dReady'),
        detail: stageDetail
          ? `${tr('brain3dLoading')} ${outlineProgress.loaded}/${outlineProgress.total} · ${stageDetail}`
          : `${tr('brain3dLoading')} ${outlineProgress.loaded}/${outlineProgress.total}`,
        actions: [],
      };
    }
    return {
      key: 'ready',
      progress: 100,
      title: tr('brain3dEmptyReady'),
      detail: tr('brain3dReady'),
      actions: [],
    };
  }, [
    allOutlinesLoaded,
    backendStatus,
    bridgeReady,
    contractError,
    instanceReady,
    outlineProgress.loaded,
    outlineProgress.total,
    outlinesInteractiveReady,
    percent,
    runtimeError,
    streamStage.detail,
    streamStage.progress,
    streamStage.stage,
    tr,
  ]);

  useEffect(() => {
    if (typeof onBridgeStageChange !== 'function') return;
    onBridgeStageChange({
      stage: blankState.key,
      progress: blankState.progress,
      title: blankState.title,
      detail: blankState.detail,
      loaded: outlineProgress.loaded,
      total: outlineProgress.total,
      bridgeReady,
      instanceReady,
      error: runtimeError || contractError || '',
      unityBridge: unityBridgeInfo,
    });
  }, [
    blankState.detail,
    blankState.key,
    blankState.progress,
    blankState.title,
    bridgeReady,
    contractError,
    instanceReady,
    onBridgeStageChange,
    outlineProgress.loaded,
    outlineProgress.total,
    runtimeError,
    unityBridgeInfo,
  ]);

  const viewLabel = (view) => {
    if (view === 'iso') return tr('brain3dIso');
    if (view === 'top') return tr('brain3dTop');
    if (view === 'side') return tr('brain3dSide');
    if (view === 'front') return tr('brain3dFront');
    return view;
  };
  const axisCoordLabelMap = {
    ap: tr('brain3dSliceCoordAp'),
    dv: tr('brain3dSliceCoordDv'),
    ml: tr('brain3dSliceCoordMl'),
  };
  const coordLabel = axisCoordLabelMap[sliceAxisCoordKey] || sliceAxisCoordKey.toUpperCase();
  const sliceCoordDisplay = `${tr('brain3dSliceBregmaCoord')}: ${coordLabel} ${formatMm(sliceAxisCoord)} mm`;
  const orthogonalSliceRows = SLICE_AXES.map((axis) => {
    const range = sliceRanges[axis] || sliceRanges.y;
    const normalized = axis === sliceAxis
      ? clamp(coronalSlice, 0, 1)
      : clamp(Number(sliceAxisValues?.[axis] ?? 1), 0, 1);
    const worldCoord = range.min + normalized * (range.max - range.min);
    const coordKey = axisCoordKey(axis);
    const displayCoord = axisCoordValue(axis, worldCoord);
    return {
      axis,
      normalized,
      displayCoord,
      label: tr(SLICE_AXIS_LABEL_KEY[axis]),
      coordLabel: axisCoordLabelMap[coordKey] || coordKey.toUpperCase(),
      active: axis === sliceAxis,
    };
  });
  const slabUm = Math.max(1, Math.round(sliceThicknessMm * 1000));
  const blankStateTall = blankState.key !== 'ready' && blankState.key !== 'streaming_mesh';
  const slicePanelTop = topOffset + (blankStateTall ? 430 : 112);
  const mriSurfaceSlice = useMemo(() => ({
    axis: sliceAxis,
    enabled: sliceEnabled,
    coord: activeSliceCoord,
    mode: sliceMode,
    thicknessMm: sliceThicknessMm,
  }), [activeSliceCoord, sliceAxis, sliceEnabled, sliceMode, sliceThicknessMm]);

  return (
    <div
      ref={viewportRef}
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        background: '#000',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <iframe
        ref={iframeRef}
        src={unitySource}
        title="Brain Planner Unity"
        allow="autoplay"
        onLoad={() => {
          if (commandFlushTimerRef.current) {
            window.clearTimeout(commandFlushTimerRef.current);
            commandFlushTimerRef.current = null;
          }
          commandQueueRef.current.forEach((entry) => {
            if (entry?.timers) entry.timers.forEach((tid) => window.clearTimeout(tid));
          });
          commandQueueRef.current.clear();
          ackedVersionByMethodRef.current.clear();
          unityCapabilitiesRef.current = normalizeUnityCapabilities(null);
          setUnityBridgeInfo(summarizeUnityCapabilities(unityCapabilitiesRef.current));
          bridgeMetricsRef.current = {
            startedAt: Date.now(),
            queued: 0,
            sent: 0,
            dropped: 0,
            skipped: 0,
            acked: 0,
            bytesSent: 0,
            largePayloads: 0,
            flushes: 0,
            lastFlushAt: 0,
            lastPublishedAt: 0,
            maxPayloadBytes: 0,
            lastAckLatencyMs: null,
          };
          setInstanceReady(false);
          setBridgeReady(false);
          setBridgeSyncState('ok');
          setRuntimeError('');
          setContractError('');
          setMissingBuild(false);
          setOutlineProgress({ loaded: 0, total: 0 });
          setStreamStage({ stage: '', progress: 0, detail: '', loaded: 0, total: 0 });
          setCatalogSource('unknown');
          instanceReadyAtRef.current = 0;
          bridgeFallbackPromotedRef.current = false;
          heartbeatAckAtRef.current = Date.now();
          bridgeStaleRef.current = false;
          if (bridgeReadyFallbackTimerRef.current) {
            window.clearTimeout(bridgeReadyFallbackTimerRef.current);
            bridgeReadyFallbackTimerRef.current = null;
          }
        }}
        style={{ flex: 1, border: 'none', background: '#000' }}
      />

      <MriSurfaceOverlay
        active={mriAppearanceEnabled}
        layers={mriAppearanceLayers}
        backendBaseUrl={cleanBackendBase}
        authToken={authToken}
        cameraState={cameraState}
        viewportSize={viewportSize}
        interactive={sliceDragActive}
        slice={mriSurfaceSlice}
      />

      <OrientationGizmo
        camera={cameraState}
        locked={cameraLocked}
        onToggleLock={() => setCameraLocked((v) => !v)}
        onSetView={handleViewPreset}
        onSetCameraAngle={handleCameraAngle}
        dragOffset={gizmoDrag.offset}
        onDragHandlePointerDown={gizmoDrag.onHandlePointerDown}
        onOrbitStart={() => {
          cameraDragActiveRef.current = true;
          sendToUnityReliable('SetInteractionQuality', 'low');
        }}
        onOrbitEnd={() => {
          cameraDragActiveRef.current = false;
          sendToUnityReliable('SetInteractionQuality', 'high');
          suppressPoseUntilRef.current = Date.now() + 80;
        }}
        i18n={tr}
      />

      <div
        style={{
          position: 'absolute',
          left: 10,
          top: slicePanelTop,
          bottom: 88,
          width: 46,
          zIndex: 8,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          pointerEvents: 'none',
        }}
      >
        <div
          style={{
            writingMode: 'vertical-rl',
            textOrientation: 'mixed',
            fontSize: 10,
            color: '#7fd9ff',
            letterSpacing: '0.08em',
            marginBottom: 8,
            opacity: 0.92,
            userSelect: 'none',
          }}
        >
          {tr('brain3dSliceTitle')}
        </div>
        <div
          style={{
            fontSize: 9,
            color: '#ff9fa8',
            marginBottom: 4,
            userSelect: 'none',
          }}
        >
          {tr('brain3dSliceRulerTop')}
        </div>
        <div
          ref={sliceTrackRef}
          onPointerDown={onCoronalSlicePointerDown}
          role="slider"
          aria-label={tr('brain3dSliceTitle')}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={slicePercent}
          title={`${tr('brain3dSliceTitle')} ${slicePercent}%`}
          style={{
            pointerEvents: bridgeReady ? 'auto' : 'none',
            position: 'relative',
            width: 16,
            height: 'min(52vh, 360px)',
            minHeight: 180,
            borderRadius: 999,
            border: '1px solid #1f5b7e',
            background: 'linear-gradient(180deg, #ff4d5a 0%, #f59e0b 35%, #1dd3ff 70%, #2a59ff 100%)',
            boxShadow: 'inset 0 0 16px #001428aa, 0 0 10px #001228aa',
            cursor: bridgeReady ? 'ns-resize' : 'not-allowed',
            touchAction: 'none',
            opacity: bridgeReady ? 1 : 0.42,
          }}
        >
          <div
            style={{
              position: 'absolute',
              left: '50%',
              top: sliceThumbTop,
              transform: 'translate(-50%, -50%)',
              width: 22,
              height: 10,
              borderRadius: 999,
              border: '1px solid #7ce4ff',
              background: '#022d47',
              boxShadow: '0 0 10px #21d4ff88',
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: '100%',
              top: sliceThumbTop,
              transform: 'translate(6px, -50%)',
              padding: '1px 4px',
              borderRadius: 4,
              border: '1px solid #1f5f86',
              background: '#042136dd',
              color: '#8de6ff',
              fontSize: 9,
              fontFamily: 'monospace',
              whiteSpace: 'nowrap',
              pointerEvents: 'none',
            }}
          >
            {coordLabel} {formatMm(sliceAxisCoord)}
          </div>
        </div>
        <div
          style={{
            fontSize: 9,
            color: '#87c8ff',
            marginTop: 4,
            userSelect: 'none',
          }}
        >
          {tr('brain3dSliceRulerBottom')}
        </div>
        <button
          onClick={() => setActiveSliceNormalized(1)}
          title={tr('brain3dSliceReset')}
          disabled={!bridgeReady}
          style={{
            marginTop: 8,
            pointerEvents: 'auto',
            border: '1px solid #1c6289',
            background: '#05263fdd',
            color: bridgeReady ? '#7de6ff' : '#5a7a8f',
            borderRadius: 6,
            height: 20,
            minWidth: 28,
            fontSize: 10,
            cursor: bridgeReady ? 'pointer' : 'not-allowed',
            padding: '0 6px',
            opacity: bridgeReady ? 1 : 0.55,
          }}
        >
          {tr('brain3dSliceReset')}
        </button>
        <div
          style={{
            marginTop: 5,
            maxWidth: 44,
            color: '#5aa7c8',
            fontSize: 8.5,
            lineHeight: 1.2,
            textAlign: 'center',
            userSelect: 'none',
          }}
        >
          {tr('brain3dSliceLeftDragOnly')}
        </div>
      </div>

      <div
        style={{
          position: 'absolute',
          left: 62,
          top: slicePanelTop,
          width: 'min(330px, calc(100% - 80px))',
          borderRadius: 8,
          border: '1px solid #1f4f6e',
          background: 'linear-gradient(180deg, #041424e6 0%, #021221f2 100%)',
          boxShadow: '0 10px 24px #00102099',
          color: '#bce8ff',
          zIndex: 8,
          padding: '8px 9px',
          display: 'grid',
          gap: 7,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: '#76c9e8' }}>{tr('brain3dSliceAxis')}:</span>
          {SLICE_AXES.map((axis) => (
            <button
              key={axis}
              onClick={() => handleSelectSliceAxis(axis)}
              style={smallToggle(sliceAxis === axis)}
            >
              {tr(SLICE_AXIS_LABEL_KEY[axis])}
            </button>
          ))}
          <span style={{ marginLeft: 'auto', fontSize: 10, color: '#6fd3ff', fontFamily: 'monospace' }}>
            {slicePercent}%
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: '#76c9e8' }}>{tr('brain3dSliceMode')}:</span>
          {SLICE_MODES.map((mode) => (
            <button
              key={mode}
              onClick={() => setSliceMode(mode)}
              style={smallToggle(sliceMode === mode)}
            >
              {mode === 'slab' ? tr('brain3dSliceModeSlab') : tr('brain3dSliceModeBelow')}
            </button>
          ))}
          <span style={{ marginLeft: 'auto', fontSize: 10, color: '#8fe9ff' }}>{sliceModeLabel}</span>
        </div>

        <div style={{ fontSize: 10, color: '#93d9f7', fontFamily: 'monospace' }}>
          {sliceCoordDisplay}
        </div>

        <div
          style={{
            display: 'grid',
            gap: 4,
            border: '1px solid #123a55',
            borderRadius: 7,
            background: '#031727cc',
            padding: '5px 6px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 9.5, color: '#76c9e8', fontWeight: 700 }}>
              {tr('brain3dOrthogonalWorkflow')}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: 8.5, color: '#5aa7c8' }}>
              {tr('brain3dSliceProbeIntersection')}
            </span>
          </div>
          {orthogonalSliceRows.map((row) => (
            <div
              key={row.axis}
              style={{
                display: 'grid',
                gridTemplateColumns: '64px 1fr 70px',
                alignItems: 'center',
                gap: 6,
                fontSize: 9.5,
              }}
            >
              <button
                type="button"
                onClick={() => handleSelectSliceAxis(row.axis)}
                style={{
                  ...smallToggle(row.active),
                  justifyContent: 'center',
                  height: 22,
                  borderColor: row.active ? '#22d3ee' : '#1a4f72',
                }}
                title={tr('brain3dSliceSetAxis')}
              >
                {row.active ? `${tr('brain3dSliceActive')} ` : ''}
                {row.label}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.002}
                value={row.normalized}
                onMouseDown={() => handleSelectSliceAxis(row.axis)}
                onChange={(e) => {
                  handleSelectSliceAxis(row.axis);
                  setActiveSliceNormalized(Number(e.target.value), row.axis);
                }}
                disabled={!bridgeReady}
                style={{
                  width: '100%',
                  accentColor: row.active ? '#22d3ee' : '#1f789d',
                  opacity: bridgeReady ? 1 : 0.45,
                }}
              />
              <span style={{ color: '#8fe9ff', fontFamily: 'monospace', textAlign: 'right' }}>
                {row.coordLabel} {formatMm(row.displayCoord)}
              </span>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, color: '#76c9e8', minWidth: 72 }}>{tr('brain3dSliceThickness')}</span>
          <input
            type="range"
            min={0.1}
            max={0.8}
            step={0.01}
            value={sliceThicknessMm}
            onChange={(e) => setSliceThicknessMm(Number(e.target.value))}
            disabled={sliceMode !== 'slab'}
            style={{ flex: 1, accentColor: '#34d399', opacity: sliceMode === 'slab' ? 1 : 0.45 }}
          />
          <span style={{ fontSize: 10, color: '#7ee3c5', fontFamily: 'monospace', minWidth: 48 }}>
            {slabUm} um
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', opacity: sliceMode === 'slab' ? 1 : 0.45 }}>
          {SLICE_THICKNESS_PRESETS_UM.map((presetUm) => {
            const mm = Number((presetUm / 1000).toFixed(3));
            const active = Math.abs(sliceThicknessMm - mm) < 1e-4;
            return (
              <button
                key={presetUm}
                onClick={() => setSliceThicknessMm(mm)}
                disabled={sliceMode !== 'slab'}
                style={smallToggle(active)}
                title={`${presetUm} um`}
              >
                {presetUm}um
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <button onClick={() => setSliceSnapEnabled((v) => !v)} style={toggleStyle(sliceSnapEnabled)}>
            {tr('brain3dSliceSnap')}
          </button>
          <span style={{ fontSize: 9.5, color: '#5fa8cc' }}>{tr('brain3dSliceSnapThreshold')}</span>
          <input
            type="range"
            min={0.05}
            max={0.6}
            step={0.01}
            value={sliceSnapThresholdMm}
            onChange={(e) => setSliceSnapThresholdMm(Number(e.target.value))}
            disabled={!sliceSnapEnabled}
            style={{ width: 80, accentColor: '#67e8f9', opacity: sliceSnapEnabled ? 1 : 0.45 }}
          />
          <span style={{ fontSize: 10, color: '#86dbff', fontFamily: 'monospace' }}>
            {formatMm(sliceSnapThresholdMm)} mm
          </span>
        </div>
        <div
          style={{
            border: `1px solid ${sliceSnapHint?.active ? '#1b8a83' : '#1e4d6d'}`,
            borderRadius: 6,
            background: sliceSnapHint?.active ? '#063232cc' : '#071b2ecc',
            color: sliceSnapHint?.active ? '#7ef8e5' : '#7bc9e8',
            fontSize: 9.5,
            fontFamily: 'monospace',
            padding: '4px 6px',
            lineHeight: 1.35,
            boxShadow: sliceSnapHint?.active ? '0 0 10px #14b8a655' : 'none',
          }}
          title={tr('brain3dSliceSnap')}
        >
          {sliceSnapHintText}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <button
            onClick={() => setSliceFilterProbes((v) => !v)}
            style={toggleStyle(sliceFilterProbes)}
            title={tr('brain3dSliceProbeFilter')}
          >
            {tr('brain3dSliceProbeFilter')}
          </button>
          <span style={{ fontSize: 10, color: '#7fd8f6', fontFamily: 'monospace' }}>
            {visibleProbeCount}/{totalProbeCount}
          </span>
          <button onClick={handleSaveSlicePreset} style={smallToggle(false)}>
            {tr('brain3dSliceSavePreset')}
          </button>
          <button onClick={handleExportSliceImage} style={smallToggle(false)}>
            {tr('brain3dSliceExport')}
          </button>
          <button
            onClick={handleExportClinicalBundle}
            style={smallToggle(false)}
            title={tr('brain3dExportClinicalBundleHint')}
            data-testid="brain3d-export-bundle"
          >
            {tr('brain3dExportClinicalBundle')}
          </button>
        </div>

        <div
          style={{
            display: 'grid',
            gap: 5,
            borderTop: '1px solid #14334c',
            paddingTop: 7,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 10, color: '#76c9e8' }}>{tr('brain3dContactTitle')}</span>
            <span style={{ marginLeft: 'auto', fontSize: 10, color: '#93d9f7', fontFamily: 'monospace' }}>
              {contactRegionSet.length}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 9.5, color: '#5fa8cc' }}>{tr('brain3dContactMode')}</span>
            {CONTACT_DISPLAY_MODES.map((mode) => {
              const labelKey =
                mode === 'contact_only'
                  ? 'brain3dContactModeOnly'
                  : mode === 'slice_contact_persist'
                    ? 'brain3dContactModePersist'
                    : 'brain3dContactModeStandard';
              return (
                <button
                  key={mode}
                  data-testid={`brain3d-contact-mode-${mode}`}
                  onClick={() => setContactDisplayMode(mode)}
                  style={smallToggle(contactDisplayMode === mode)}
                >
                  {tr(labelKey)}
                </button>
              );
            })}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 9.5, color: '#5fa8cc' }}>{tr('brain3dContactStyle')}</span>
            {CONTACT_DISPLAY_STYLES.map((style) => {
              const labelKey =
                style === 'outline'
                  ? 'brain3dContactStyleOutline'
                  : style === 'fill'
                    ? 'brain3dContactStyleFill'
                    : 'brain3dContactStyleHybrid';
              return (
                <button
                  key={style}
                  data-testid={`brain3d-contact-style-${style}`}
                  onClick={() => setContactDisplayStyle(style)}
                  style={smallToggle(contactDisplayStyle === style)}
                >
                  {tr(labelKey)}
                </button>
              );
            })}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 9.5, color: '#5fa8cc', minWidth: 86 }}>{tr('brain3dContactMinLength')}</span>
            <input
              type="range"
              data-testid="brain3d-contact-min-length"
              min={0}
              max={1200}
              step={20}
              value={contactMinLengthUm}
              onChange={(e) => setContactMinLengthUm(Number(e.target.value))}
              style={{ flex: 1, accentColor: '#67e8f9' }}
            />
            <span style={{ fontSize: 10, color: '#86dbff', fontFamily: 'monospace', minWidth: 62 }}>
              {Math.round(contactMinLengthUm)} um
            </span>
          </div>

          {contactProfileLoading && (
            <div style={{ fontSize: 9.5, color: '#7bd7ff' }}>{tr('brain3dContactLoading')}</div>
          )}
          {!contactProfileLoading && contactProfileError && (
            <div style={{ fontSize: 9.5, color: '#fda4af' }}>
              {tr('brain3dContactError')}: {contactProfileError}
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ fontSize: 9.5, color: '#5fa8cc' }}>{tr('brain3dContactDepthTable')}</div>
            <button
              onClick={handleExportContactCsv}
              disabled={contactIntervals.length <= 0}
              style={{ ...smallToggle(false), marginLeft: 'auto', opacity: contactIntervals.length > 0 ? 1 : 0.45 }}
              title={tr('brain3dContactExportCsv')}
            >
              {tr('brain3dContactExportCsv')}
            </button>
          </div>
          <div
            data-testid="brain3d-contact-depth-table"
            tabIndex={0}
            onKeyDown={handleContactDepthTableKeyDown}
            title={tr('brain3dContactShortcutHint')}
            style={{
              maxHeight: 120,
              overflow: 'auto',
              border: '1px solid #1b4564',
              borderRadius: 6,
              background: '#041a2ddd',
              display: 'grid',
              gap: 2,
              padding: 3,
            }}
            className="bp-scrollbar bp-scrollbar--thin"
          >
            {visibleContactIntervals.map((row, rowIndex) => {
              const lenRatio = clamp(Number(row.lengthUm || 0) / Math.max(1, contactHeatRange.maxLen), 0, 1);
              const strengthRatio = clamp(
                Number(row.contactStrength || 0) / Math.max(0.01, contactHeatRange.maxStrength),
                0,
                1,
              );
              const heat = clamp(lenRatio * 0.7 + strengthRatio * 0.3, 0, 1);
              const active = activeContactIntervalId && String(row.id) === String(activeContactIntervalId);
              return (
                <div
                  key={row.id}
                  onClick={() => handleContactIntervalAction(row, 'fly')}
                  onMouseEnter={() => setActiveContactIntervalId(String(row.id || ''))}
                  title={tr('brain3dContactJump')}
                  role="button"
                  style={{
                    border: `1px solid ${active ? '#22d3ee' : heat > 0.72 ? '#0ea5a0' : '#174661'}`,
                    borderRadius: 5,
                    background: `linear-gradient(90deg, rgba(8,35,56,0.95) 0%, rgba(15,118,110,${(0.12 + heat * 0.28).toFixed(3)}) 100%)`,
                    color: '#a7e3ff',
                    fontSize: 9,
                    fontFamily: 'monospace',
                    padding: '3px 5px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    boxShadow: active ? '0 0 12px #22d3ee66' : heat > 0.82 ? '0 0 10px #0ea5a066' : 'none',
                    display: 'grid',
                    gap: 4,
                  }}
                >
                  <div>
                    {`${row.probeId || `P${row.probeIndex + 1}`} ${row.acronym} ${Math.round(row.dStartUm)}-${Math.round(row.dEndUm)}um | L=${Math.round(row.lengthUm)}um`}
                  </div>
                  <div style={{ color: '#7dd3fc' }}>
                    {[
                      row.hemisphere || '--',
                      `S=${Number(row.contactStrength || 0).toFixed(2)}`,
                      row.riskClass ? `risk=${row.riskClass}` : '',
                      Number.isFinite(row.minDistanceMm) ? `dmin=${row.minDistanceMm.toFixed(3)}mm` : '',
                      Array.isArray(row.shankIndices) && row.shankIndices.length ? `shank=${row.shankIndices.join('|')}` : '',
                    ].filter(Boolean).join(' | ')}
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    <button
                      onClick={(evt) => handleContactIntervalAction(row, 'fly', evt)}
                      style={smallToggle(false)}
                      title={tr('brain3dContactJump')}
                    >
                      {tr('brain3dContactActionFly')}
                    </button>
                    <button
                      onClick={(evt) => handleContactIntervalAction(row, 'snap', evt)}
                      style={smallToggle(false)}
                      title={tr('brain3dSliceSnap')}
                    >
                      {tr('brain3dContactActionSnap')}
                    </button>
                    <button
                      onClick={(evt) => handleContactIntervalAction(row, 'risk', evt)}
                      style={smallToggle(false)}
                      title={tr('brain3dContactStyle')}
                    >
                      {tr('brain3dContactActionRisk')}
                    </button>
                    <button
                      onClick={(evt) => handleContactIntervalAction(row, 'export', evt)}
                      style={smallToggle(false)}
                      title={tr('brain3dSliceExport')}
                    >
                      {tr('brain3dContactActionExport')}
                    </button>
                  </div>
                </div>
              );
            })}
            {contactIntervals.length <= 0 && !contactProfileLoading && (
              <div style={{ fontSize: 9, color: '#5a9ab8', padding: '2px 4px' }}>
                {tr('brain3dContactEmpty')}
              </div>
            )}
          </div>
          <div style={{ fontSize: 9, color: '#63b7da' }}>
            {tr('brain3dContactShortcutHint')}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 4 }}>
          <div style={{ fontSize: 10, color: '#76c9e8' }}>{tr('brain3dSlicePresets')}</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', maxHeight: 60, overflow: 'auto' }} className="bp-scrollbar bp-scrollbar--thin">
            {[...slicePresets, ...DEFAULT_SLICE_PRESETS.filter((p) => !slicePresets.some((x) => x.id === p.id))]
              .slice(0, 12)
              .map((preset) => (
                <div
                  key={preset.id}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 2,
                    border: '1px solid #1e4668',
                    background: '#082039',
                    borderRadius: 999,
                    padding: '2px 5px',
                  }}
                >
                  <button
                    onClick={() => applySlicePreset(preset)}
                    style={{
                      border: 'none',
                      background: 'transparent',
                      color: '#99ddff',
                      fontSize: 9,
                      cursor: 'pointer',
                    }}
                    title={tr('brain3dSlicePresetApply')}
                  >
                    {preset.label || preset.region || preset.id}
                  </button>
                  {String(preset.id).length > 10 && (
                    <button
                      onClick={() => handleDeleteSlicePreset(preset.id)}
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: '#fca5a5',
                        fontSize: 9,
                        cursor: 'pointer',
                        lineHeight: 1,
                      }}
                      title={tr('brain3dSlicePresetDelete')}
                    >
                      x
                    </button>
                  )}
                </div>
              ))}
            {slicePresets.length <= 0 && (
              <span style={{ fontSize: 9.5, color: '#5a9ab8' }}>{tr('brain3dSliceNoPreset')}</span>
            )}
          </div>
        </div>
      </div>

      {selectedAnchors.map((anchor) => (
        <div
          key={`${anchor.key}-${anchor.hemisphere}`}
          style={{
            position: 'absolute',
            left: anchor.displayX ?? anchor.x,
            top: anchor.displayY ?? anchor.y,
            transform: 'translate(-50%, -50%)',
            pointerEvents: 'none',
            zIndex: 3,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 3,
          }}
        >
          <div
            style={{
              width: clamp(anchor.radiusPx * 0.9, 10, 20),
              height: clamp(anchor.radiusPx * 0.9, 10, 20),
              borderRadius: '50%',
              border: '2px solid #67e8f9',
              background: '#0e749055',
              boxShadow: '0 0 18px #22d3eecc',
            }}
          />
          <div
            style={{
              padding: '1px 5px',
              borderRadius: 3,
              background: '#042033dd',
              border: '1px solid #175175',
              fontSize: 9,
              color: '#8adfff',
              fontFamily: 'monospace',
              letterSpacing: '0.02em',
            }}
          >
            {anchor.key} {anchor.hemisphere === 'left' ? 'L' : 'R'}
          </div>
        </div>
      ))}

      {missingBuild && !bridgeReady && !runtimeError && (
        <div style={{ position: 'absolute', inset: 12, border: '1px solid #5b1f1f', background: '#240d0dcc', color: '#ffcdcd', fontSize: 12, padding: 12, lineHeight: 1.5, zIndex: 5 }}>
          {tr('brain3dUnityMissing')} <code>{unitySource}</code>.
          <br />
          {tr('brain3dBuildHint')} <code>probe-planner/public/unity/</code>.
        </div>
      )}

      {blankState.key !== 'ready' && (
        <div
          style={{
            position: 'absolute',
            left: 14,
            top: topOffset + 14,
            width: 'min(420px, calc(100% - 28px))',
            borderRadius: 10,
            border: '1px solid #1d4f6d',
            background: 'linear-gradient(180deg, #051a2acc 0%, #041425dd 100%)',
            color: '#d4ebf9',
            boxShadow: '0 14px 36px #000f22aa',
            padding: '10px 12px',
            zIndex: 8,
            display: 'grid',
            gap: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>{blankState.title}</span>
            <span style={{ marginLeft: 'auto', fontSize: 10, fontFamily: 'monospace', color: '#67e8f9' }}>
              {blankState.progress}%
            </span>
          </div>
          <div style={{ fontSize: 10.5, color: '#8ec9e9', lineHeight: 1.5 }}>{blankState.detail}</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {blankState.actions.map((action) => (
              <button
                key={action}
                onClick={() => handleBlankAction(action)}
                style={{
                  border: '1px solid #2e7399',
                  background: '#072742',
                  color: '#8fdfff',
                  borderRadius: 6,
                  padding: '4px 8px',
                  fontSize: 10,
                  cursor: 'pointer',
                }}
              >
                {action === 'reload_unity'
                  ? tr('brain3dActionReloadUnity')
                  : action === 'resync'
                    ? tr('brain3dActionResync')
                    : action === 'retry_mesh'
                      ? tr('brain3dActionRetryMesh')
                      : tr('brain3dActionReconnect')}
              </button>
            ))}
          </div>
        </div>
      )}

      {runtimeError && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, background: '#7f1d1d', color: '#fecaca', borderBottom: '1px solid #991b1b', fontSize: 11, textAlign: 'center', padding: '4px 8px', zIndex: 6 }}>
          {tr('unityErrorLabel')}: {runtimeError}
        </div>
      )}

      {contractError && (
        <div style={{ position: 'absolute', top: runtimeError ? 26 : 0, left: 0, right: 0, background: '#78350f', color: '#fde68a', borderBottom: '1px solid #92400e', fontSize: 11, textAlign: 'center', padding: '4px 8px', zIndex: 6 }}>
          {contractError}
        </div>
      )}

      {fillNotice && (
        <div
          style={{
            position: 'absolute',
            top: (runtimeError ? 26 : 0) + (contractError ? 26 : 0),
            left: 0,
            right: 0,
            background: '#0f3b57',
            color: '#bde8ff',
            borderBottom: '1px solid #1d5f88',
            fontSize: 11,
            textAlign: 'center',
            padding: '4px 8px',
            zIndex: 6,
          }}
        >
          {fillNotice}
        </div>
      )}

      {bridgeReady && !allOutlinesLoaded && (
        <div style={{ position: 'absolute', top: topOffset, left: 0, right: 0, height: 2, background: '#0a1a2a', zIndex: 6 }}>
          <div style={{ width: `${percent}%`, height: '100%', background: 'linear-gradient(90deg, #00d4ff, #34d399)', transition: 'width 0.3s' }} />
        </div>
      )}

      {recentPick && (
        <div
          style={{
            position: 'absolute',
            top: topOffset + 8,
            right: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '4px 8px',
            borderRadius: 5,
            background: '#041d15d6',
            border: '1px solid #14532d',
            color: '#6ee7b7',
            fontSize: 10,
            fontFamily: 'monospace',
            zIndex: 8,
            maxWidth: 520,
          }}
        >
          <span style={{ whiteSpace: 'nowrap' }}>
            {recentPick.key}
            {recentPick.hemisphere ? ` / ${recentPick.hemisphere === 'left' ? 'L' : 'R'}` : ''}
          </span>
          <span style={{ color: '#9cead0', whiteSpace: 'nowrap' }}>
            AP {Number.isFinite(recentPick.coords?.ap) ? formatMm(recentPick.coords.ap) : '--'}
          </span>
          <span style={{ color: '#9cead0', whiteSpace: 'nowrap' }}>
            DV {Number.isFinite(recentPick.coords?.dv) ? formatMm(recentPick.coords.dv) : '--'}
          </span>
          <span style={{ color: '#9cead0', whiteSpace: 'nowrap' }}>
            ML {Number.isFinite(recentPick.coords?.ml) ? formatMm(recentPick.coords.ml) : '--'}
          </span>
          <span style={{ color: '#93c5fd', whiteSpace: 'nowrap' }}>
            {sliceAxisLabel} {Number.isFinite(sliceAxisCoord) ? formatMm(sliceAxisCoord) : '--'}
          </span>
          <button
            onClick={handleFlyToRecentPick}
            style={{
              border: '1px solid #256d58',
              background: '#063228',
              color: '#6ee7b7',
              borderRadius: 4,
              padding: '2px 6px',
              cursor: 'pointer',
              fontSize: 9.5,
            }}
            title={tr('brain3dFlyToRegion')}
          >
            {tr('brain3dFlyToRegion')}
          </button>
        </div>
      )}

      {sideCoverageBadges.length > 0 && (
        <div
          style={{
            position: 'absolute',
            top: topOffset + 36,
            left: 10,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 4,
            maxWidth: 260,
            zIndex: 6,
          }}
        >
          {sideCoverageBadges.map((item) => (
            <div
              key={item.key}
              style={{
                padding: '2px 6px',
                borderRadius: 4,
                border: `1px solid ${item.bilateral ? '#34d39999' : '#2a6a9a'}`,
                background: item.bilateral ? '#052e1cdd' : '#052033dd',
                color: item.bilateral ? '#86efac' : '#84dfff',
                fontSize: 9.5,
                fontFamily: 'monospace',
              }}
            >
              {item.key} {item.label}
            </div>
          ))}
        </div>
      )}

      {selectedRegions.length > 0 && (
        <div style={{ position: 'absolute', top: topOffset + 34, right: 10, transform: `translate(${selectedPanelDrag.offset.x}px, ${selectedPanelDrag.offset.y}px)`, maxWidth: 280, padding: '6px 8px', borderRadius: 5, background: '#021726dd', border: '1px solid #17425f', color: '#7ec8ea', fontSize: 10, zIndex: 6, display: 'grid', gap: 5 }}>
          <div
            onPointerDown={selectedPanelDrag.onHandlePointerDown}
            style={{
              height: 12,
              borderRadius: 4,
              border: '1px dashed #2b6a95',
              color: '#6db8de',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 9,
              letterSpacing: '0.08em',
              cursor: 'grab',
              userSelect: 'none',
            }}
            title={tr('brain3dDragPanel')}
          >
            {tr('brain3dDrag')}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {selectedRegions
              .filter((key) => {
                if (hemisphereFilter === 'all') return true;
                const side = regionSideHints[key];
                return !side || side === hemisphereFilter;
              })
              .map((key) => {
            const side = regionSideHints[key];
            return (
              <button
                key={key}
                onMouseEnter={() => sendToUnity('SetRegionHighlight', JSON.stringify({ key, highlight: true }))}
                onMouseLeave={() => sendToUnity('SetRegionHighlight', JSON.stringify({ key, highlight: false }))}
                style={{ padding: '1px 5px', borderRadius: 4, border: '1px solid #255574', background: '#06304a', color: '#9fe3ff', fontSize: 9.5, cursor: 'pointer' }}
              >
                {key}{side ? ` ${side === 'left' ? 'L' : 'R'}` : ''}
              </button>
            );
          })}
          </div>
        </div>
      )}

      <button
        onClick={toggleFullscreen}
        title={isFullscreen ? tr('brain3dExitFullscreen') : tr('brain3dFullscreen')}
        data-testid="brain3d-fullscreen"
        style={{
          position: 'absolute',
          right: 10,
          bottom: 56,
          zIndex: 9,
          width: 34,
          height: 34,
          borderRadius: 7,
          border: '1px solid #1f6f9f',
          background: '#042138dd',
          color: '#8fe8ff',
          cursor: 'pointer',
          display: 'grid',
          placeItems: 'center',
          boxShadow: '0 4px 14px #00122288',
        }}
      >
        <FullscreenIcon active={isFullscreen} />
      </button>

      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, background: 'linear-gradient(transparent, #000000dd 55%)', padding: '10px 14px 12px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', zIndex: 7 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 10, color: '#5a9ab8' }}>{tr('brain3dOpacity')}</span>
          <input
            type="range"
            min={0}
            max={100}
            value={opacitySlider}
            onChange={(e) => setOpacitySlider(Number(e.target.value))}
            data-testid="brain3d-opacity-slider"
            style={{ width: 90, accentColor: '#00d4ff', cursor: 'pointer', height: 3 }}
          />
          <span style={{ fontSize: 10, color: '#00d4ff', fontFamily: 'monospace', minWidth: 28 }}>
            {Math.round(mapOpacity(opacitySlider) * 100)}%
          </span>
        </div>

        <button
          onClick={() => setSelectedOnly((v) => !v)}
          style={toggleStyle(selectedOnly)}
          data-testid="brain3d-selected-only"
        >
          {tr('brain3dSelectedOnly')}
        </button>
        <button
          onClick={() => setClinicalFocus((v) => !v)}
          style={toggleStyle(clinicalFocus)}
          data-testid="brain3d-clinical-focus"
          title={tr('brain3dClinicalFocusHint')}
        >
          {tr('brain3dClinicalFocus')}
        </button>
        {isMriHybridMode && (
          <button
            onClick={handleToggleMriAppearance}
            style={mriPrimaryToggleStyle(mriAppearanceEnabled)}
            data-testid="brain3d-mri-appearance"
            title={tr('brain3dUseMriAppearanceHint')}
          >
            <span style={{ fontSize: 9, fontFamily: 'monospace', opacity: 0.95 }}>MRI</span>
            <span>{tr('brain3dUseMriAppearance')}</span>
            <span
              style={{
                marginLeft: 2,
                borderRadius: 999,
                padding: '1px 6px',
                fontSize: 9,
                fontWeight: 700,
                fontFamily: 'monospace',
                background: mriAppearanceEnabled ? '#043625' : '#2a120f',
                color: mriAppearanceEnabled ? '#9ff7cf' : '#fbb0a4',
                border: `1px solid ${mriAppearanceEnabled ? '#138a61' : '#8a3f32'}`,
              }}
            >
              {mriAppearanceEnabled ? tr('on') : tr('off')}
            </span>
          </button>
        )}
        {isMriHybridMode && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              maxWidth: 620,
              overflowX: 'auto',
              padding: '2px 4px',
              borderRadius: 999,
              background: mriAppearanceEnabled ? '#042f2e80' : '#07162480',
              border: `1px solid ${mriAppearanceEnabled ? '#0f766e' : '#164766'}`,
              opacity: mriAppearanceEnabled ? 1 : 0.58,
            }}
            className="bp-scrollbar bp-scrollbar--thin"
            title={tr('brain3dMriLayersHint')}
          >
            <span style={{ fontSize: 9, color: '#8fd7ff', flexShrink: 0 }}>
              {tr('brain3dMriLayers')}
            </span>
            {MRI_APPEARANCE_LAYER_ITEMS.map(([layerKey, labelKey, testId]) => {
              const surfaceUnavailable = Boolean(
                MRI_SURFACE_LAYER_BY_UI_KEY[layerKey] && !mriSurfaceLayerReady?.[layerKey],
              );
              const ccfLabelsBlocked = Boolean(layerKey === 'ccfLabels' && !mriAppearanceLayers?.overlay);
              const disabled = Boolean(!mriAppearanceEnabled || ccfLabelsBlocked || surfaceUnavailable);
              const active = Boolean(
                mriAppearanceEnabled
                && mriAppearanceLayers?.[layerKey]
                && (layerKey !== 'ccfLabels' || mriAppearanceLayers?.overlay)
                && !surfaceUnavailable
              );
              const title = surfaceUnavailable
                ? tr('brain3dMriSurfaceUnavailable')
                : ccfLabelsBlocked
                  ? tr('brain3dMriCcfLabelsNeedOverlay')
                  : tr('brain3dMriLayersHint');
              return (
                <button
                  key={layerKey}
                  onClick={() => handleToggleMriAppearanceLayer(layerKey)}
                  style={{
                    ...smallToggle(active),
                    flexShrink: 0,
                    opacity: disabled ? 0.52 : 1,
                    cursor: disabled ? 'not-allowed' : 'pointer',
                  }}
                  disabled={disabled}
                  data-testid={testId}
                  title={title}
                >
                  <span style={mriToggleIndicatorStyle(active, disabled)} />
                  <span>{tr(labelKey)}</span>
                </button>
              );
            })}
          </div>
        )}
        {isMriHybridMode && mriAppearanceEnabled && (
          <span
            style={{
              fontSize: 10,
              color: '#9ff7cf',
              background: '#043625aa',
              border: '1px solid #138a61',
              borderRadius: 999,
              padding: '2px 8px',
              fontFamily: 'monospace',
            }}
            title={tr('brain3dUseMriAppearanceHint')}
          >
            {tr('brain3dMriProfileActive')}
          </span>
        )}
        {isMriHybridMode && mriLayerExplain && (
          <span
            style={{
              fontSize: 10,
              color: '#93c5fd',
              background: '#08213b',
              border: '1px solid #1b4f7d',
              borderRadius: 999,
              padding: '2px 8px',
              fontFamily: 'monospace',
            }}
            title={`${tr('mriAppearanceActiveLabel')}: ${mriLayerExplain.appearanceActive ? tr('on') : tr('off')} | ${tr('mriRiskLayerDistanceModelLabel')}: ${mriLayerExplain.distanceModel}`}
          >
            {tr('brain3dMriLayerStatus')}: {mriLayerExplain.appearanceActive ? tr('on') : tr('off')} | {tr('mriRiskLayerStructuredLabel')}: {mriLayerExplain.structured ? tr('on') : tr('off')} | {tr('mriRiskLayerSdfLabel')}: {mriLayerExplain.sdf ? tr('on') : tr('off')}
            {mriLayerExplain.surfaceReady !== null
              ? ` | ${tr('brain3dMriSurfaceMeshes')}: ${mriLayerExplain.surfaceReady ? tr('on') : tr('off')}`
              : ''}
          </span>
        )}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <span style={{ fontSize: 9, color: '#5a9ab8' }}>{tr('brain3dDisplayLayers')}</span>
          {DISPLAY_LAYER_ORDER.map((layerKey) => (
            <button
              key={layerKey}
              onClick={() => toggleLayerVisibility(layerKey)}
              style={smallToggle(Boolean(layerVisibility?.[layerKey]))}
              data-testid={`brain3d-layer-${layerKey.toLowerCase()}`}
              title={`${tr(DISPLAY_LAYER_LABEL_KEY[layerKey])} (${layerCounts[layerKey] || 0})`}
            >
              {tr(DISPLAY_LAYER_LABEL_KEY[layerKey])} {layerCounts[layerKey] || 0}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', overflowX: 'auto', maxWidth: 680 }} className="bp-scrollbar bp-scrollbar--thin">
          <span style={{ fontSize: 9, color: '#5a9ab8', flexShrink: 0 }}>{tr('brain3dDisplayTypes')}</span>
          {CLINICAL_CLASS_ORDER.map((classKey) => (
            <button
              key={classKey}
              onClick={() => toggleClassVisibility(classKey)}
              style={{ ...smallToggle(Boolean(classVisibility?.[classKey])), flexShrink: 0 }}
              data-testid={`brain3d-class-${classKey}`}
            >
              {tr(CLINICAL_CLASS_LABEL_KEY[classKey])} {clinicalClassCounts[classKey] || 0}
            </button>
          ))}
        </div>
        {focusAutoLimited && (
          <span
            style={{
              fontSize: 9,
              color: '#fcd34d',
              fontFamily: 'monospace',
            }}
            title={tr('brain3dAutoDegradeHint')}
          >
            {tr('brain3dAutoDegradeLabel')}: {selectedFocusKeys.length}/{visiblePolicyCount}
          </span>
        )}

        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <span style={{ fontSize: 9, color: '#5a9ab8' }}>{tr('brain3dHemisphere')}</span>
          {['all', 'left', 'right'].map((hemi) => (
            <button
              key={hemi}
              onClick={() => setHemisphereFilter(hemi)}
              style={smallToggle(hemisphereFilter === hemi)}
              data-testid={`brain3d-hemi-${hemi}`}
            >
              {hemi === 'all' ? tr('brain3dAll') : hemi === 'left' ? 'L' : 'R'}
            </button>
          ))}
          <button onClick={() => setInvertLr((v) => !v)} style={smallToggle(invertLr)} data-testid="brain3d-hemi-lrfix">
            {tr('brain3dLrFix')}
          </button>
        </div>

        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <button onClick={() => setShowShell((v) => !v)} style={toggleStyle(showShell)} data-testid="brain3d-shell">{tr('brain3dShell')}</button>
          <button onClick={() => setShowAxes((v) => !v)} style={toggleStyle(showAxes)} data-testid="brain3d-axes">{tr('brain3dAxes')}</button>
          <button onClick={() => setShowProbeGuides((v) => !v)} style={toggleStyle(showProbeGuides)} data-testid="brain3d-probe-guide">{tr('brain3dProbeGuide')}</button>
          <button onClick={() => setShowUncertainty((v) => !v)} style={toggleStyle(showUncertainty)} data-testid="brain3d-uncertainty">{tr('brain3dUncertainty')}</button>
          <button onClick={() => setUseWeightedOit((v) => !v)} style={toggleStyle(useWeightedOit)} data-testid="brain3d-weighted-oit">{tr('brain3dWeightedOit')}</button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, opacity: useWeightedOit ? 1 : 0.45 }}>
            <span style={{ fontSize: 9, color: '#5a9ab8' }}>{tr('brain3dOitWeight')}</span>
            <input
              type="range"
              min={0.2}
              max={2}
              step={0.05}
              value={oitWeight}
              disabled={!useWeightedOit}
              onChange={(e) => setOitWeight(Number(e.target.value))}
              data-testid="brain3d-oit-weight"
              style={{ width: 76, accentColor: '#34d399', cursor: useWeightedOit ? 'pointer' : 'not-allowed', height: 3 }}
            />
            <span style={{ fontSize: 9, color: '#7ee3c5', fontFamily: 'monospace', minWidth: 24 }}>
              {oitWeight.toFixed(2)}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: 9, color: '#5a9ab8' }}>{tr('brain3dEnvironmentLight')}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={Math.round(clamp(environmentLighting, 0, 1) * 100)}
              onChange={(e) => setEnvironmentLighting(clamp(Number(e.target.value) / 100, 0, 1))}
              data-testid="brain3d-environment-lighting"
              style={{ width: 78, accentColor: '#facc15', cursor: 'pointer', height: 3 }}
            />
            <span style={{ fontSize: 9, color: '#fde68a', fontFamily: 'monospace', minWidth: 28 }}>
              {Math.round(clamp(environmentLighting, 0, 1) * 100)}%
            </span>
          </div>
        </div>

        <span style={{ fontSize: 10, fontFamily: 'monospace', color: allOutlinesLoaded ? '#34d399' : '#a78bfa' }}>
          {bridgeReady
            ? allOutlinesLoaded
              ? `${tr('brain3dReady')} ${outlineProgress.loaded}/${outlineProgress.total}`
              : outlinesInteractiveReady
                ? `${tr('brain3dReady')}* ${outlineProgress.loaded}/${outlineProgress.total}`
                : `${tr('brain3dLoading')} ${outlineProgress.loaded}/${outlineProgress.total}`
            : instanceReady
              ? tr('brain3dBridgeInit')
              : tr('brain3dUnityLoading')}
        </span>
        {bridgeReady && !allOutlinesLoaded && (streamStage.detail || streamStage.stage) && (
          <span
            style={{
              fontSize: 10,
              fontFamily: 'monospace',
              color: '#7dd3fc',
              maxWidth: 260,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
            title={streamStage.detail || streamStage.stage}
          >
            {streamStage.detail || streamStage.stage}
          </span>
        )}
        {bridgeReady && (
          <span
            style={{
              fontSize: 10,
              fontFamily: 'monospace',
              color:
                catalogSource === 'backend_unity_all' ||
                catalogSource === 'backend_unity_all_refresh' ||
                catalogSource === 'backend_all'
                  ? '#6ee7b7'
                  : '#fcd34d',
            }}
          >
            {catalogDisplay}
          </span>
        )}
        {bridgeReady && (
          <span
            data-testid="brain3d-sync-state"
            style={{
              fontSize: 10,
              fontFamily: 'monospace',
              color:
                bridgeSyncState === 'stale'
                  ? '#fca5a5'
                  : bridgeSyncState === 'resync'
                    ? '#fcd34d'
                    : '#67e8f9',
            }}
          >
            {bridgeSyncState === 'stale'
              ? tr('brain3dSyncStale')
              : bridgeSyncState === 'resync'
                ? tr('brain3dSyncResync')
                : tr('brain3dSyncOk')}
          </span>
        )}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            border: '1px solid #1d4968',
            background: '#041828cc',
            borderRadius: 999,
            padding: '1px 6px',
            minHeight: 18,
          }}
        >
          {bridgeStageFlow.map((stage, idx) => (
            <React.Fragment key={stage.key}>
              <span
                title={`${stage.label}: ${stage.ready ? tr('globalStageReady') : tr('globalStageWaiting')}`}
                style={{
                  fontSize: 9,
                  fontFamily: 'monospace',
                  color: stage.ready ? '#6ee7b7' : '#5b8ba8',
                  whiteSpace: 'nowrap',
                }}
              >
                {stage.label}:{stage.ready ? '1' : '0'}
              </span>
              {idx < bridgeStageFlow.length - 1 && (
                <span style={{ fontSize: 8, color: '#2f647f' }}>{'>'}</span>
              )}
            </React.Fragment>
          ))}
        </div>
        <span
          style={{
            fontSize: 10,
            fontFamily: 'monospace',
            color: sliceEnabled ? '#7dd3fc' : '#64748b',
          }}
        >
          {sliceEnabled
            ? `${sliceAxisLabel} ${formatMm(sliceAxisCoord)}mm`
            : tr('brain3dSliceDisabled')}
        </span>
        <span
          style={{
            fontSize: 10,
            fontFamily: 'monospace',
            color: sliceFilterProbes ? '#6ee7b7' : '#94a3b8',
          }}
        >
          {tr('brain3dSliceProbeFilter')}: {visibleProbeCount}/{totalProbeCount}
        </span>

        <div style={{ display: 'flex', gap: 5 }}>
          {CAMERA_VIEWS.map((view) => (
            <button
              key={view}
              onClick={() => handleViewPreset(view)}
              data-testid={`brain3d-view-${view}`}
              style={smallToggle(cameraState.view === view)}
            >
              {viewLabel(view)}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          {VIEW_TEMPLATES.map((template) => {
            const disabled = template === 'mri' && !isMriHybridMode;
            return (
              <button
                key={template}
                onClick={() => applyViewTemplate(template)}
                style={{ ...smallToggle(activeTemplate === template), opacity: disabled ? 0.45 : smallToggle(activeTemplate === template).opacity }}
                data-testid={`brain3d-template-${template}`}
                disabled={disabled}
                title={disabled ? tr('brain3dTemplateMriDisabledHint') : tr(VIEW_TEMPLATE_LABEL_KEY[template])}
              >
                {tr(VIEW_TEMPLATE_LABEL_KEY[template])}
              </button>
            );
          })}
        </div>

        <span
          style={{
            fontSize: 10,
            color: cameraLocked ? '#fca5a5' : '#93c5fd',
            border: `1px solid ${cameraLocked ? '#7f1d1d' : '#1f5a88'}`,
            borderRadius: 999,
            padding: '1px 8px',
            background: cameraLocked ? '#3f1017' : '#08223a',
            whiteSpace: 'nowrap',
          }}
          title={cameraLocked ? tr('brain3dViewLocked') : tr('brain3dViewUnlocked')}
        >
          {cameraLocked ? `L ${tr('brain3dViewLocked')}` : `U ${tr('brain3dViewUnlocked')}`}
        </span>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          <LegendDot color="#00d4ff" label={`${tr('brain3dSelected')} ${selectedRegions.length}`} />
          <LegendDot color="#ffd700" label={tr('brain3dBregma')} />
          <LegendDot color="#ff8800" label={tr('brain3dLambda')} />
          {probes.length > 0 && <LegendDot color={probeColor} label={`${probePreset} x${probes.length}`} />}
        </div>
      </div>
    </div>
  );
}

function OrientationGizmo({
  camera,
  onSetView,
  onSetCameraAngle,
  locked,
  onToggleLock,
  dragOffset,
  onDragHandlePointerDown,
  onOrbitStart,
  onOrbitEnd,
  i18n,
}) {
  const basis = getCameraBasis(camera);
  const orbitDragRef = useRef(null);
  const size = 96;
  const c = size / 2;
  const scale = 27;

  const axisDots = [
    { key: 'x', vec: { x: 1, y: 0, z: 0 }, color: '#ff7568', view: 'side' },
    { key: 'y', vec: { x: 0, y: 1, z: 0 }, color: '#98e84d', view: 'top' },
    { key: 'z', vec: { x: 0, y: 0, z: 1 }, color: '#7da7ff', view: 'front' },
  ].map((a) => {
    const x = c + vecDot(a.vec, basis.right) * scale;
    const y = c - vecDot(a.vec, basis.up) * scale;
    return { ...a, x, y };
  });

  const onOrbitPointerDown = useCallback((e) => {
    if (locked) return;
    const target = e.target;
    if (target && typeof target.closest === 'function' && target.closest('[data-no-orbit="1"]')) return;
    if (e.button !== 0 && e.pointerType !== 'touch') return;
    e.preventDefault();
    e.stopPropagation();
    if (typeof onOrbitStart === 'function') onOrbitStart();
    orbitDragRef.current = {
      x: e.clientX,
      y: e.clientY,
      theta: camera.theta,
      phi: camera.phi,
      distance: camera.distance,
    };

    const onMove = (moveEvent) => {
      if (!orbitDragRef.current) return;
      const dx = moveEvent.clientX - orbitDragRef.current.x;
      const dy = moveEvent.clientY - orbitDragRef.current.y;
      onSetCameraAngle({
        theta: orbitDragRef.current.theta - dx * 0.012,
        phi: orbitDragRef.current.phi + dy * 0.012,
        distance: orbitDragRef.current.distance,
      });
    };

    const onUp = () => {
      orbitDragRef.current = null;
      if (typeof onOrbitEnd === 'function') onOrbitEnd();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerup', onUp, { once: true });
    window.addEventListener('pointercancel', onUp, { once: true });
  }, [camera.distance, camera.phi, camera.theta, locked, onOrbitEnd, onOrbitStart, onSetCameraAngle]);

  return (
    <div
      style={{
        position: 'absolute',
        top: 8,
        right: 8,
        zIndex: 8,
        width: 114,
        borderRadius: 10,
        border: '1px solid #4b7096',
        background: 'linear-gradient(180deg, #7b95b8 0%, #6d89ae 100%)',
        boxShadow: '0 10px 26px #00102488',
        transform: `translate(${dragOffset.x}px, ${dragOffset.y}px)`,
        overflow: 'hidden',
      }}
    >
      <div
        onPointerDown={onDragHandlePointerDown}
        title={i18n('brain3dDragGizmo')}
        style={{
          height: 16,
          cursor: 'grab',
          borderBottom: '1px solid #5d7ba1',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          color: '#d8e6fa',
          fontSize: 10,
          padding: '0 5px',
          userSelect: 'none',
        }}
      >
        <span style={{ opacity: 0.82, letterSpacing: '0.08em' }}>III</span>
        <button
          data-no-orbit="1"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onToggleLock}
          style={{
            border: 'none',
            background: 'transparent',
            color: locked ? '#f8fafc' : '#d7e4f8',
            cursor: 'pointer',
            fontSize: 11,
            lineHeight: 1,
            padding: 0,
          }}
          title={locked ? i18n('brain3dLock') : i18n('brain3dUnlock')}
        >
          {locked ? 'LCK' : 'ULK'}
        </button>
      </div>

      <div
        onPointerDown={onOrbitPointerDown}
        title={i18n('brain3dDragOrbit')}
        style={{
          position: 'relative',
          display: 'grid',
          placeItems: 'center',
          padding: '6px 0 4px',
          cursor: locked ? 'not-allowed' : 'grab',
        }}
      >
        <svg width={size} height={size} style={{ display: 'block' }}>
          <circle cx={c} cy={c} r={33} fill="#89a0c155" stroke="#6a84a8" strokeWidth="1" />
          <rect x={c - 8} y={c - 8} width={16} height={16} rx={2} fill="#f0f4fb" stroke="#ffffff" strokeWidth="0.8" />
          {axisDots.map((a) => (
            <g
              key={a.key}
              data-no-orbit="1"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onSetView(a.view)}
              style={{ cursor: 'pointer' }}
            >
              <line x1={c} y1={c} x2={a.x} y2={a.y} stroke={a.color} strokeWidth="2.2" strokeLinecap="round" />
              <polygon
                points={`${a.x},${a.y - 5} ${a.x + 5},${a.y + 5} ${a.x - 5},${a.y + 5}`}
                fill={a.color}
                transform={`rotate(${a.key === 'x' ? 90 : a.key === 'z' ? -90 : 180} ${a.x} ${a.y})`}
              />
              <text x={a.x + 8} y={a.y + 4} fill={a.color} fontSize="11" fontWeight="700" fontFamily="monospace">
                {a.key.toUpperCase()}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}

function FullscreenIcon({ active }) {
  if (active) {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="6" y="6" width="4" height="4" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function toggleStyle(active) {
  return {
    padding: '2px 8px',
    fontSize: 9,
    borderRadius: 3,
    cursor: 'pointer',
    background: active ? '#0f3a2d' : '#0a1a2a',
    color: active ? '#6ee7b7' : '#5a9ab8',
    border: `1px solid ${active ? '#166534' : '#1a3a5a'}`,
  };
}

function smallToggle(active) {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    padding: '2px 7px',
    minHeight: 22,
    fontSize: 9,
    lineHeight: 1.2,
    whiteSpace: 'nowrap',
    borderRadius: 3,
    cursor: 'pointer',
    background: active ? '#0d2b42' : '#0a1a2a',
    color: active ? '#84dfff' : '#5a9ab8',
    border: `1px solid ${active ? '#2a6a9a' : '#1a3a5a'}`,
  };
}

function mriToggleIndicatorStyle(active, disabled) {
  return {
    width: 5,
    height: 5,
    flexShrink: 0,
    borderRadius: 999,
    background: disabled ? '#31546b' : active ? '#67e8f9' : '#12344d',
    boxShadow: !disabled && active ? '0 0 8px #22d3eeaa' : 'none',
    border: `1px solid ${disabled ? '#406077' : active ? '#a5f3fc' : '#2a5a78'}`,
  };
}

function mriPrimaryToggleStyle(active) {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '4px 12px',
    minHeight: 24,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.01em',
    borderRadius: 999,
    cursor: 'pointer',
    background: active
      ? 'linear-gradient(90deg, #0b3f33 0%, #0f5342 100%)'
      : 'linear-gradient(90deg, #2a1212 0%, #3a1d1d 100%)',
    color: active ? '#ccffe9' : '#ffd5d0',
    border: `1px solid ${active ? '#1e8b6a' : '#9f4b40'}`,
    boxShadow: active
      ? '0 0 0 1px #0e2c24 inset, 0 0 14px #2dd4bf55'
      : '0 0 0 1px #2b1515 inset, 0 0 10px #ef444433',
  };
}

function LegendDot({ color, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
      <span style={{ fontSize: 9, color: '#5a9ab8' }}>{label}</span>
    </div>
  );
}

