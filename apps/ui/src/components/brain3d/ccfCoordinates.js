import { BREGMA_UM } from '../../constants/anatomy';

// CCF micrometres (posterior, ventral, right) -> viewer mm (right, dorsal, anterior).
export function ccfUmToViewer(point) {
  return { x: (point[2] - BREGMA_UM[2]) / 1000,
    y: -(point[1] - BREGMA_UM[1]) / 1000,
    z: -(point[0] - BREGMA_UM[0]) / 1000 };
}

export function regionToWorld(reg, hemisphere, invertLr = false) {
  const mlSign = hemisphere === 'right' ? 1 : -1;
  return { x: Math.abs(reg.ml ?? 0) * mlSign * (invertLr ? -1 : 1),
    y: -(reg.dv ?? 0), z: reg.ap ?? 0 };
}

export function probePointAtDepth(probe, depthUm, invertLr = false) {
  if (!probe || !Number.isFinite(depthUm)) return null;
  const theta = Number(probe.theta_deg) * Math.PI / 180;
  const phi = Number(probe.phi_deg) * Math.PI / 180;
  if (!Number.isFinite(theta) || !Number.isFinite(phi)) return null;
  const depth = depthUm / 1000;
  return {
    x: (Number(probe.ml_mm_corrected ?? probe.ml_mm ?? 0) + Math.sin(theta) * Math.sin(phi) * depth) * (invertLr ? -1 : 1),
    y: -Number(probe.entryDvCorr_mm_corrected ?? probe.entryDvCorr_mm ?? 0) - Math.cos(theta) * depth,
    z: Number(probe.ap_mm_corrected ?? probe.ap_mm ?? 0) - Math.sin(theta) * Math.cos(phi) * depth,
  };
}

export function normalizeProbeSegment(probe) {
  return { entry: probePointAtDepth(probe, 0), tip: probePointAtDepth(probe, Number(probe?.depthUm_corrected ?? probe?.depthUm ?? 0)) };
}

export function axisCoordValue(axis, worldCoord) {
  return axis === 'y' ? -worldCoord : worldCoord;
}

export function resolvedSliceCoordinate(requested, section, requestKey, enabled) {
  return enabled && section?.requestKey === requestKey && Number.isFinite(section.center) &&
    ['loading', 'loaded', 'failed'].includes(section.status) ? section.center : requested;
}
