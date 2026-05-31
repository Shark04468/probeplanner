/**
 * Smart Candidate Generator
 * ==========================
 *
 * FIX: constraintModes?.lambda → constraintModes?.lambdaBoundary  (2 locations)
 *      to match the key used in ConstraintsPanel.jsx / defaultConstraints.
 */

import { BRAIN_REGIONS, regionToXYZ } from '../constants/brainRegions.js';
import { ANATOMY, BREGMA_UM } from '../constants/anatomy.js';
import { v3 } from './vectorMath.js';
import { makeTrajectory, computeChannelCoverage, computeTrajectoryScore } from './trajectory.js';

// ── Strategy 1: Centroid seeds ────────────────────────────────────────────────
function generateCentroidSeeds(targetKeys, params, probeConfig, constraintModes) {
  const {
    maxAngle    = 15,
    depthUm     = 10000,
    skullTiltDeg = 0,
  } = params;

  const candidates = [];

  const angles = [[0, 0]];
  for (let th = 5; th <= maxAngle; th += 5)
    for (let ph = 0; ph < 360; ph += 45)
      angles.push([th, ph]);

  const offsets = [
    [0, 0],
    [0.3, 0], [-0.3, 0], [0, 0.3], [0, -0.3],
    [0.3, 0.3], [-0.3, -0.3], [0.15, 0.15], [-0.15, 0.15],
  ];

  for (const key of targetKeys) {
    const reg = BRAIN_REGIONS[key];
    if (!reg) continue;

    const idealAP = -reg.ap;
    const idealML =  reg.ml;
    const recordingLen  = probeConfig.recordingLengthUm || 720;
    const idealDepth    = reg.dv * 1000 + recordingLen / 2;
    const clampedDepth  = Math.min(idealDepth, depthUm);

    for (const [dap, dml] of offsets) {
      const ap = idealAP + dap;
      const ml = idealML + dml;

      // FIX: was constraintModes?.lambda
      if (constraintModes?.lambdaBoundary === 'enforce' && ap < ANATOMY.lambda_ap_mm) continue;

      for (const [theta, phi] of angles) {
        if (theta > maxAngle) continue;

        const traj = makeTrajectory(ap, ml, theta, phi, clampedDepth, skullTiltDeg);
        traj.id       = candidates.length;
        traj._source  = `centroid_${key}`;
        traj._targetKey = key;

        if (constraintModes?.brainBounds === 'enforce') {
          const tipDV = traj.dir[1] * clampedDepth / 1000;
          const tipAP = ap + traj.dir[0] * clampedDepth / 1000;
          if (tipDV > ANATOMY.max_dv_mm || tipAP < ANATOMY.tip_post_limit_mm) continue;
        }

        candidates.push(traj);
      }
    }
  }

  return candidates;
}

// ── Strategy 2: Multi-region lines ───────────────────────────────────────────
function generateMultiRegionLines(targetKeys, params, constraintModes) {
  const {
    maxAngle    = 15,
    depthUm     = 10000,
    skullTiltDeg = 0,
  } = params;

  const candidates = [];

  const centroids = {};
  for (const key of targetKeys) {
    const reg = BRAIN_REGIONS[key];
    if (reg) centroids[key] = regionToXYZ(reg);
  }

  const keys = Object.keys(centroids);

  for (let i = 0; i < keys.length; i++) {
    for (let j = i + 1; j < keys.length; j++) {
      const ka = keys[i], kb = keys[j];
      const ca = centroids[ka], cb = centroids[kb];

      let dir, shallow, deep;
      if (ca[1] < cb[1]) {
        dir = v3.sub(cb, ca); shallow = ca; deep = cb;
      } else {
        dir = v3.sub(ca, cb); shallow = cb; deep = ca;
      }

      if (Math.abs(dir[1]) < 200) continue;

      const dirN  = v3.normalize(dir);
      const theta = Math.acos(Math.min(1, Math.abs(dirN[1]))) * 180 / Math.PI;
      if (theta > maxAngle) continue;

      if (Math.abs(dirN[1]) < 0.1) continue;
      const tToSkull = (BREGMA_UM[1] - shallow[1]) / dirN[1];
      const entryUm  = v3.add(shallow, v3.scale(dirN, tToSkull));

      const ap_mm = (BREGMA_UM[0] - entryUm[0]) / 1000;
      const ml_mm = (entryUm[2]   - BREGMA_UM[2]) / 1000;

      // FIX: was constraintModes?.lambda
      if (constraintModes?.lambdaBoundary === 'enforce' && ap_mm < ANATOMY.lambda_ap_mm) continue;

      const phi        = (Math.atan2(dirN[2], dirN[0]) * 180 / Math.PI + 360) % 360;
      const depthToDeep = v3.norm(v3.sub(deep, entryUm));
      const depth      = Math.min(depthToDeep + 1000, depthUm);

      const traj = makeTrajectory(ap_mm, ml_mm, theta, phi, depth, skullTiltDeg);
      traj.id       = -1;
      traj._source  = `pair_${ka}_${kb}`;
      traj._priority = 'high';
      candidates.push(traj);

      for (const dth of [-3, 3]) {
        for (const dph of [-15, 15]) {
          const th2 = Math.max(0, Math.min(maxAngle, theta + dth));
          const ph2 = (phi + dph + 360) % 360;
          const t2  = makeTrajectory(ap_mm, ml_mm, th2, ph2, depth, skullTiltDeg);
          t2.id     = -1;
          t2._source = `pair_${ka}_${kb}_perturb`;
          candidates.push(t2);
        }
      }
    }
  }

  return candidates;
}

// ── Strategy 3: Sparse grid ───────────────────────────────────────────────────
function generateSparseGrid(targetKeys, params, constraintModes) {
  const {
    gridSpacing  = 600,
    maxAngle     = 15,
    depthUm      = 10000,
    bilateral    = false,
    skullTiltDeg = 0,
  } = params;

  const gridMm = Math.max(gridSpacing / 1000, 0.8);

  const regions = targetKeys.map(k => BRAIN_REGIONS[k]).filter(Boolean);
  let apVals = regions.map(r => -r.ap);
  let mlVals = regions.map(r => r.ml);
  if (bilateral) mlVals = [...mlVals, ...regions.map(r => -r.ml)];

  const margin = 1.5;
  let apMin = Math.min(...apVals) - margin;
  let apMax = Math.max(...apVals) + margin;
  let mlMin = Math.min(...mlVals) - margin;
  let mlMax = Math.max(...mlVals) + margin;

  // FIX: was constraintModes?.lambda
  if (constraintModes?.lambdaBoundary === 'enforce')
    apMin = Math.max(apMin, ANATOMY.lambda_ap_mm);

  const angles = [[0, 0]];
  for (let th = 5; th <= maxAngle; th += 5)
    for (let ph = 0; ph < 360; ph += 60)
      angles.push([th, ph]);

  const candidates = [];
  for (let ap = apMin; ap <= apMax + 1e-9; ap += gridMm) {
    // FIX: was constraintModes?.lambda
    if (constraintModes?.lambdaBoundary === 'enforce' && ap < ANATOMY.lambda_ap_mm) continue;
    for (let ml = mlMin; ml <= mlMax + 1e-9; ml += gridMm) {
      for (const [theta, phi] of angles) {
        if (theta > maxAngle) continue;
        const traj = makeTrajectory(
          Math.round(ap * 1000) / 1000,
          Math.round(ml * 1000) / 1000,
          theta, phi, depthUm, skullTiltDeg
        );
        traj.id     = -1;
        traj._source = 'grid';

        if (constraintModes?.brainBounds === 'enforce') {
          const tipDV = traj.dir[1] * depthUm / 1000;
          const tipAP = ap + traj.dir[0] * depthUm / 1000;
          if (tipDV > ANATOMY.max_dv_mm || tipAP < ANATOMY.tip_post_limit_mm) continue;
        }

        candidates.push(traj);
      }
    }
  }

  return candidates;
}

// ── Deduplication ─────────────────────────────────────────────────────────────
function deduplicateCandidates(candidates) {
  const seen   = new Set();
  const unique = [];
  for (const c of candidates) {
    const key = `${Math.round(c.ap_mm * 10)}_${Math.round(c.ml_mm * 10)}_${Math.round(c.theta_deg)}_${Math.round(c.phi_deg / 5)}`;
    if (!seen.has(key)) {
      seen.add(key);
      c.id = unique.length;
      unique.push(c);
    }
  }
  return unique;
}

// ── Scoring ───────────────────────────────────────────────────────────────────
function scoreCandidates(candidates, targetKeys, probeConfig, constraintModes) {
  const scored = [];
  for (const traj of candidates) {
    const coverage    = computeChannelCoverage(traj, targetKeys, probeConfig, { minChannels: 3 });
    const { coveredKeys } = coverage;
    if (coveredKeys.length === 0) continue;

    if (constraintModes?.hemisphere === 'enforce') {
      let bad = false;
      for (const k of coveredKeys) {
        const reg = BRAIN_REGIONS[k];
        if (reg && Math.abs(reg.ml) >= 0.5 && Math.abs(traj.ml_mm) >= 0.5)
          if (Math.sign(traj.ml_mm) !== Math.sign(reg.ml)) { bad = true; break; }
      }
      if (bad) continue;
    }

    const scoreResult = computeTrajectoryScore(coverage);

    traj.coveredRegions   = coveredKeys;
    traj.channelCoverage  = coverage.perRegion;
    traj.compositeScore   = scoreResult.totalScore;
    traj.nCoveredRegions  = scoreResult.nCovered;
    traj.avgQuality       = scoreResult.avgQuality;
    traj.overlapVoxels    = {};
    for (const k of coveredKeys)
      traj.overlapVoxels[k] = coverage.perRegion[k].channelCount;

    scored.push(traj);
  }
  return scored;
}

// ── Diversity reduction ───────────────────────────────────────────────────────
function diversityReduce(candidates, topK = 6) {
  const JACCARD_THRESH = 0.6;
  const groups = {};

  for (const c of candidates) {
    const key = `${Math.round(c.ap_mm * 5)}_${Math.round(c.ml_mm * 5)}`;
    if (!groups[key]) groups[key] = [];
    groups[key].push(c);
  }

  const reduced = [];
  for (const group of Object.values(groups)) {
    group.sort((a, b) =>
      b.compositeScore - a.compositeScore ||
      b.nCoveredRegions - a.nCoveredRegions
    );

    const selected = [group[0]];
    for (let i = 1; i < group.length && selected.length < topK; i++) {
      const cov  = new Set(group[i].coveredRegions);
      const maxJ = Math.max(...selected.map(s => {
        const sc    = new Set(s.coveredRegions);
        const inter = [...cov].filter(x => sc.has(x)).length;
        const union = new Set([...cov, ...sc]).size;
        return union === 0 ? 1 : inter / union;
      }));
      if (maxJ < JACCARD_THRESH) selected.push(group[i]);
    }
    reduced.push(...selected);
  }

  return reduced;
}

// ── Main export ───────────────────────────────────────────────────────────────
export function generateCandidates(targetKeys, params, probeConfig, constraintModes, onProgress) {
  if (onProgress) onProgress(5,  'Generating centroid-seeded candidates...');
  const centroidCands = generateCentroidSeeds(targetKeys, params, probeConfig, constraintModes);

  if (onProgress) onProgress(15, 'Finding multi-region trajectories...');
  const multiCands    = generateMultiRegionLines(targetKeys, params, constraintModes);

  if (onProgress) onProgress(25, 'Sparse grid scan...');
  const gridCands     = generateSparseGrid(targetKeys, params, constraintModes);

  if (onProgress) onProgress(35, 'Deduplicating...');
  const all    = [...centroidCands, ...multiCands, ...gridCands];
  const unique = deduplicateCandidates(all);

  if (onProgress) onProgress(40, `Scoring ${unique.length} candidates...`);
  const scored = scoreCandidates(unique, targetKeys, probeConfig, constraintModes);

  if (onProgress) onProgress(55, `Reducing ${scored.length} → diversity selection...`);
  const reduced = diversityReduce(scored, 8);

  if (onProgress) onProgress(60, `${reduced.length} candidates ready for optimization`);
  return reduced;
}
