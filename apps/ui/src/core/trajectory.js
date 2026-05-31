/**
 * Channel-Aware Trajectory Model
 */

import { BRAIN_REGIONS, regionToXYZ, REGION_RADII_UM, REGION_DV_SPAN_MM } from '../constants/brainRegions.js';
import { BREGMA_UM } from '../constants/anatomy.js';
// FIX: added segSegDist to the import (was missing, causing require() fallback bug)
import { v3, ptSegDist, segSegDist } from './vectorMath.js';

export function makeTrajectory(ap_mm, ml_mm, theta_deg, phi_deg, depthUm = 10000, skullTiltDeg = 0) {
  const tiltRad = skullTiltDeg * Math.PI / 180;
  const dvCorrUm = -ap_mm * Math.tan(tiltRad) * 1000;

  const entry = [
    BREGMA_UM[0] - ap_mm * 1000,
    BREGMA_UM[1] + dvCorrUm,
    BREGMA_UM[2] + ml_mm * 1000,
  ];

  const th = theta_deg * Math.PI / 180;
  const ph = phi_deg * Math.PI / 180;

  const dir = [
    Math.sin(th) * Math.cos(ph),
    Math.cos(th),
    Math.sin(th) * Math.sin(ph),
  ];

  const tip = [
    entry[0] + dir[0] * depthUm,
    entry[1] + dir[1] * depthUm,
    entry[2] + dir[2] * depthUm,
  ];

  return {
    entry, tip, dir,
    theta_deg, phi_deg, ap_mm, ml_mm, depthUm, skullTiltDeg,
    entryDvCorr_mm: -dvCorrUm / 1000,
  };
}

export function generateElectrodePositions(traj, probeConfig) {
  const {
    shanks = 1,
    channelsPerShank = 384,
    electrodePitchUm = 15,
    electrodeColumns = 2,
    shankPitchUm = 250,
  } = probeConfig;

  const dirN = v3.normalize(traj.dir);

  let perpDir = [1, 0, 0];
  if (Math.abs(v3.dot(dirN, perpDir)) > 0.9) perpDir = [0, 0, 1];
  const proj = v3.scale(dirN, v3.dot(perpDir, dirN));
  perpDir = v3.normalize(v3.sub(perpDir, proj));

  const halfSpan = (shanks - 1) * shankPitchUm / 2;
  const electrodesPerColumn = Math.ceil(channelsPerShank / electrodeColumns);
  const electrodes = [];

  for (let si = 0; si < shanks; si++) {
    const shankOffset = shanks > 1
      ? v3.scale(perpDir, -halfSpan + si * shankPitchUm)
      : [0, 0, 0];
    const shankTip = v3.add(traj.tip, shankOffset);

    for (let ei = 0; ei < electrodesPerColumn; ei++) {
      const distFromTip = ei * electrodePitchUm;
      const pos = v3.sub(shankTip, v3.scale(dirN, distFromTip));
      electrodes.push({
        pos, shank: si,
        channelIdx: si * channelsPerShank + ei,
        depthUm: traj.depthUm - distFromTip,
      });
    }
  }

  return electrodes;
}

export function computeChannelCoverage(traj, targetKeys, probeConfig, opts = {}) {
  const { minChannels = 5 } = opts;
  const electrodes = generateElectrodePositions(traj, probeConfig);

  const result = {};
  const coveredKeys = [];

  for (const key of targetKeys) {
    const reg = BRAIN_REGIONS[key];
    if (!reg) { result[key] = { covered: false, channelCount: 0, score: 0 }; continue; }

    const centroid = regionToXYZ(reg);
    const radiusUm = REGION_RADII_UM[key] || 500;
    const dvSpanMm = REGION_DV_SPAN_MM[key] || 0.6;

    const semiDV = dvSpanMm * 1000 / 2;
    const semiAP = radiusUm;
    const semiML = radiusUm;

    let channelCount = 0;
    let minDepth = Infinity, maxDepth = -Infinity;
    const channelsInRegion = [];

    for (const elec of electrodes) {
      const dx = (elec.pos[0] - centroid[0]) / semiAP;
      const dy = (elec.pos[1] - centroid[1]) / semiDV;
      const dz = (elec.pos[2] - centroid[2]) / semiML;
      const ellipDist = dx * dx + dy * dy + dz * dz;

      if (ellipDist <= 1.0) {
        channelCount++;
        minDepth = Math.min(minDepth, elec.depthUm);
        maxDepth = Math.max(maxDepth, elec.depthUm);
        channelsInRegion.push(elec);
      }
    }

    const centroidDist = ptSegDist(centroid, traj.entry, traj.tip);
    const centroidWithin = centroidDist <= radiusUm * 1.2;
    const covered = channelCount >= minChannels || (channelCount > 0 && centroidWithin);

    const targetChannels = Math.max(10, Math.round(dvSpanMm * 1000 / (probeConfig.electrodePitchUm || 15)));
    const score = Math.min(1.0, channelCount / targetChannels);

    let qualityFactor = 0;
    if (channelCount > 0) {
      let sumWeight = 0;
      for (const elec of channelsInRegion) {
        const d = v3.norm(v3.sub(elec.pos, centroid)) / radiusUm;
        sumWeight += Math.max(0, 1 - d * 0.7);
      }
      qualityFactor = sumWeight / channelCount;
    }

    result[key] = {
      covered, channelCount,
      score: Math.round(score * 1000) / 1000,
      qualityFactor: Math.round(qualityFactor * 1000) / 1000,
      depthRange: channelCount > 0 ? [minDepth, maxDepth] : null,
      channelSpanUm: channelCount > 0 ? maxDepth - minDepth : 0,
      centroidDistUm: Math.round(centroidDist),
    };

    if (covered) coveredKeys.push(key);
  }

  return { perRegion: result, coveredKeys };
}

export function computeTrajectoryScore(coverageResult, weights = {}) {
  let totalScore = 0, nCovered = 0, sumQuality = 0;
  for (const [key, cov] of Object.entries(coverageResult.perRegion)) {
    if (!cov.covered) continue;
    const w = weights[key] || 1.0;
    totalScore += cov.score * w * cov.qualityFactor;
    sumQuality += cov.qualityFactor;
    nCovered++;
  }
  return {
    totalScore: Math.round(totalScore * 1000) / 1000,
    nCovered,
    avgQuality: nCovered > 0 ? Math.round(sumQuality / nCovered * 1000) / 1000 : 0,
  };
}

// FIX: removed require('./vectorMath.js') — segSegDist is now a top-level import
export function trajsCollide(a, b, minClearanceUm, minEntryUm = 3500) {
  const entryDist = v3.norm(v3.sub(a.entry, b.entry));
  if (entryDist < minEntryUm) return true;
  const shaftDist = segSegDist(a.entry, a.tip, b.entry, b.tip);
  return shaftDist < minClearanceUm;
}

export function trajsCollideInline(a, b, minClearanceUm, minEntryUm = 3500) {
  const entryDist = v3.norm(v3.sub(a.entry, b.entry));
  if (entryDist < minEntryUm) return true;

  const u = v3.sub(a.tip, a.entry), vv = v3.sub(b.tip, b.entry);
  const w0 = v3.sub(a.entry, b.entry);
  const aa = v3.dot(u, u), bb = v3.dot(u, vv), c = v3.dot(vv, vv);
  const d = v3.dot(u, w0), e = v3.dot(vv, w0);
  const D = aa * c - bb * bb, EPS = 1e-12;
  let sN, sD = D, tN, tD = D;
  if (D < EPS) { sN = 0; sD = 1; tN = e; tD = c; }
  else {
    sN = bb * e - c * d; tN = aa * e - bb * d;
    if (sN < 0)       { sN = 0; tN = e; tD = c; }
    else if (sN > sD) { sN = sD; tN = e + bb; tD = c; }
  }
  if (tN < 0) {
    tN = 0;
    if (-d < 0) sN = 0;
    else if (-d > aa) sN = sD;
    else { sN = -d; sD = aa; }
  } else if (tN > tD) {
    tN = tD;
    const tmp = -d + bb;
    if (tmp < 0) sN = 0;
    else if (tmp > aa) sN = sD;
    else { sN = tmp; sD = aa; }
  }
  const s = Math.abs(sN) < EPS ? 0 : sN / sD;
  const t = Math.abs(tN) < EPS ? 0 : tN / tD;
  const dist = v3.norm(v3.sub(v3.add(w0, v3.scale(u, s)), v3.scale(vv, t)));
  return dist < minClearanceUm;
}
