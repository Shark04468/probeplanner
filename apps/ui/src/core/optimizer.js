/**
 * Probe Trajectory Optimizer
 *
 * FIX: constraintModes?.lambda → constraintModes?.lambdaBoundary in coverageBoost()
 */

import { makeTrajectory, computeChannelCoverage } from './trajectory.js';
import { BRAIN_REGIONS } from '../constants/brainRegions.js';
import { ANATOMY } from '../constants/anatomy.js';
import { v3, segSegDist } from './vectorMath.js';

// ── Collision check ───────────────────────────────────────────────────────────
function trajsCollide(a, b, minClearance, minEntry = 3500) {
  const entryDist = v3.norm(v3.sub(a.entry, b.entry));
  if (entryDist < minEntry) return true;
  return segSegDist(a.entry, a.tip, b.entry, b.tip) < minClearance;
}

// ── Greedy optimizer ──────────────────────────────────────────────────────────
export function greedyOptimize(targetKeys, candidates, minClearance, minEntry, regionWeights = {}) {
  const uncovered  = new Set(targetKeys);
  const selected   = [];
  const selectedSet = new Set();

  while (uncovered.size > 0) {
    let best = null, bestNewCount = 0, bestScore = -Infinity;

    for (const c of candidates) {
      if (selectedSet.has(c)) continue;
      if (selected.some(s => trajsCollide(c, s, minClearance, minEntry))) continue;

      const newRegions = c.coveredRegions.filter(r => uncovered.has(r));
      const nNew = newRegions.length;
      if (nNew === 0) continue;

      let newScore = 0;
      for (const r of newRegions) {
        const cov = c.channelCoverage?.[r] || {};
        const w   = regionWeights[r] || 1.0;
        newScore += (cov.score || 0.5) * w * (cov.qualityFactor || 0.5);
      }

      if (nNew > bestNewCount || (nNew === bestNewCount && newScore > bestScore)) {
        bestNewCount = nNew; bestScore = newScore; best = c;
      }
    }

    if (!best || bestNewCount === 0) break;
    selected.push(best);
    selectedSet.add(best);
    for (const r of best.coveredRegions) uncovered.delete(r);
  }

  return { selected, uncovered: [...uncovered] };
}

// ── Exact / MILP optimizer ────────────────────────────────────────────────────
export function exactOptimize(targetKeys, candidates, minClearance, minEntry, timeLimitMs = 12000, onProgress) {
  const n   = candidates.length;
  const m   = targetKeys.length;
  const BigM = n + 1;

  if (n === 0) return { selected: [], uncovered: [...targetKeys], milpStatus: 'Optimal', milpNodes: 0 };

  const covIdx = candidates.map(c =>
    new Set(targetKeys.map((k, i) => c.coveredRegions.includes(k) ? i : -1).filter(i => i >= 0))
  );
  const covBy = targetKeys.map((_, i) =>
    candidates.map((_, j) => covIdx[j].has(i) ? j : -1).filter(j => j >= 0)
  );

  const conflicts = Array.from({ length: n }, () => new Set());
  for (let a = 0; a < n; a++) {
    for (let b = a + 1; b < n; b++) {
      if (trajsCollide(candidates[a], candidates[b], minClearance, minEntry)) {
        conflicts[a].add(b); conflicts[b].add(a);
      }
    }
  }

  if (onProgress) onProgress(65, 'Branch & Bound searching...');

  function lpBound(nSelected, excluded, covered) {
    let lb = nSelected;
    for (let i = 0; i < m; i++) {
      if (covered.has(i)) continue;
      let k = 0;
      for (const j of covBy[i]) if (!excluded.has(j)) k++;
      lb += k === 0 ? BigM : 1 / k;
    }
    return lb;
  }

  const order = Array.from({ length: n }, (_, i) => i)
    .sort((a, b) =>
      (covIdx[b].size / (1 + conflicts[b].size)) -
      (covIdx[a].size / (1 + conflicts[a].size))
    );

  const tStart = Date.now();
  let bestObj = Infinity, bestSelected = null, nodes = 0;

  function bb(selected, selSet, excluded, covered, orderPos) {
    if (Date.now() - tStart > timeLimitMs) return;
    nodes++;

    const uncovN  = m - covered.size;
    const currObj = selected.length + BigM * uncovN;

    if (uncovN === 0) {
      if (currObj < bestObj) { bestObj = currObj; bestSelected = [...selected]; }
      return;
    }

    if (lpBound(selected.length, excluded, covered) >= bestObj) return;

    let branchJ = -1, pos = orderPos;
    while (pos < n) {
      const j = order[pos];
      if (!excluded.has(j) && !selSet.has(j)) { branchJ = j; break; }
      pos++;
    }

    if (branchJ === -1) {
      if (currObj < bestObj) { bestObj = currObj; bestSelected = [...selected]; }
      return;
    }

    const newExcl1 = new Set([...excluded, ...conflicts[branchJ]]);
    const newCov1  = new Set(covered);
    covIdx[branchJ].forEach(i => newCov1.add(i));
    selected.push(branchJ); selSet.add(branchJ);
    bb(selected, selSet, newExcl1, newCov1, pos + 1);
    selected.pop(); selSet.delete(branchJ);

    if (Date.now() - tStart > timeLimitMs) return;

    excluded.add(branchJ);
    bb(selected, selSet, excluded, covered, pos + 1);
    excluded.delete(branchJ);
  }

  bb([], new Set(), new Set(), new Set(), 0);

  const timedOut = Date.now() - tStart >= timeLimitMs;
  const status   = !timedOut ? 'Optimal' : (bestSelected ? 'Feasible' : 'FallbackGreedy');

  if (!bestSelected) {
    const g = greedyOptimize(targetKeys, candidates, minClearance, minEntry);
    g.milpStatus = 'FallbackGreedy';
    g.milpNodes  = nodes;
    return g;
  }

  const selected   = bestSelected.map(j => candidates[j]);
  const coveredSet = new Set(selected.flatMap(c => c.coveredRegions));
  const uncovered  = targetKeys.filter(k => !coveredSet.has(k));
  return { selected, uncovered, milpStatus: status, milpNodes: nodes };
}

// ── Coverage boost ────────────────────────────────────────────────────────────
export function coverageBoost(selected, uncoveredKeys, targetKeys, params, probeConfig, constraintModes) {
  if (!uncoveredKeys.length) return { selected, uncovered: [] };

  const boosted    = [...selected];
  let remaining    = [...uncoveredKeys];
  const minClearance = params.minClearance  || 1000;
  const minEntry     = probeConfig.entrySpacingUm || 3500;

  const angles = [];
  for (let th = 0; th <= (params.maxAngle || 15); th += 3) {
    if (th === 0) { angles.push([0, 0]); continue; }
    for (let ph = 0; ph < 360; ph += 30) angles.push([th, ph]);
  }

  const offsets = [
    [0, 0], [0.2, 0], [-0.2, 0], [0, 0.2], [0, -0.2],
    [0.4, 0], [-0.4, 0], [0, 0.4], [0, -0.4],
    [0.3, 0.3], [-0.3, -0.3], [0.5, 0], [0, 0.5],
  ];

  for (const rk of uncoveredKeys) {
    if (!remaining.includes(rk)) continue;
    const reg = BRAIN_REGIONS[rk];
    if (!reg) continue;

    const apT = -reg.ap, mlT = reg.ml;
    outer:
    for (const [dap, dml] of offsets) {
      for (const [theta, phi] of angles) {
        const ap = apT + dap, ml = mlT + dml;

        // FIX: was constraintModes?.lambda
        if (constraintModes?.lambdaBoundary === 'enforce' && ap < ANATOMY.lambda_ap_mm) continue;

        const traj = makeTrajectory(
          ap, ml, theta, phi,
          params.depthUm || probeConfig.depthUm,
          params.skullTiltDeg || 0
        );
        const coverage = computeChannelCoverage(traj, targetKeys, probeConfig, { minChannels: 3 });
        traj.coveredRegions  = coverage.coveredKeys;
        traj.channelCoverage = coverage.perRegion;
        traj.overlapVoxels   = {};
        for (const k of coverage.coveredKeys)
          traj.overlapVoxels[k] = coverage.perRegion[k].channelCount;

        if (!traj.coveredRegions.includes(rk)) continue;
        if (boosted.some(s => trajsCollide(traj, s, minClearance, minEntry))) continue;

        if (constraintModes?.hemisphere === 'enforce') {
          let bad = false;
          for (const k of traj.coveredRegions) {
            const r2 = BRAIN_REGIONS[k];
            if (r2 && Math.abs(r2.ml) >= 0.5 && Math.abs(traj.ml_mm) >= 0.5
              && Math.sign(traj.ml_mm) !== Math.sign(r2.ml)) { bad = true; break; }
          }
          if (bad) continue;
        }

        traj.id = Date.now() + Math.random();
        boosted.push(traj);
        remaining = remaining.filter(k => !traj.coveredRegions.includes(k));
        break outer;
      }
    }
  }

  return { selected: boosted, uncovered: remaining };
}

// ── Refinement ────────────────────────────────────────────────────────────────
export function refineSelection(selected, allCandidates, targetKeys, minClearance, minEntry) {
  let improved = true, current = [...selected], iterations = 0;
  const MAX_ITER = 5;

  while (improved && iterations < MAX_ITER) {
    improved = false; iterations++;
    for (let i = 0; i < current.length; i++) {
      const others       = current.filter((_, j) => j !== i);
      const otherCoverage = new Set(others.flatMap(s => s.coveredRegions));
      const mustCover    = current[i].coveredRegions.filter(r => !otherCoverage.has(r));

      let bestReplacement = null, bestScore = current[i].compositeScore || 0;
      for (const c of allCandidates) {
        if (current.includes(c)) continue;
        if (others.some(s => trajsCollide(c, s, minClearance, minEntry))) continue;
        if (!mustCover.every(r => c.coveredRegions.includes(r))) continue;
        if ((c.compositeScore || 0) > bestScore) {
          bestScore = c.compositeScore; bestReplacement = c;
        }
      }
      if (bestReplacement && bestReplacement !== current[i]) {
        current[i] = bestReplacement; improved = true;
      }
    }
  }
  return current;
}

// ── Main entry ────────────────────────────────────────────────────────────────
export function optimize(targetKeys, candidates, params, probeConfig, constraintModes, onProgress) {
  const algorithm    = params.algorithm || 'greedy';
  const minClearance = params.minClearance || 1000;
  const minEntry     = probeConfig.entrySpacingUm || 3500;

  let result;
  if (algorithm === 'milp' || algorithm === 'exact') {
    result = exactOptimize(targetKeys, candidates, minClearance, minEntry, 12000, onProgress);
  } else {
    result = greedyOptimize(targetKeys, candidates, minClearance, minEntry);
    result.milpStatus = null;
    result.milpNodes  = null;
  }

  if (onProgress) onProgress(80, 'Refining selection...');

  if (result.selected.length > 0) {
    result.selected = refineSelection(result.selected, candidates, targetKeys, minClearance, minEntry);
  }

  if (params.coverageBoost && result.uncovered.length > 0) {
    if (onProgress) onProgress(85, 'Coverage boost...');
    const boosted = coverageBoost(
      result.selected, result.uncovered, targetKeys,
      { ...params, depthUm: probeConfig.depthUm },
      probeConfig, constraintModes
    );
    result.selected  = boosted.selected;
    result.uncovered = boosted.uncovered;
  }

  result.selected = result.selected.map((t, i) => ({ ...t, probeIdx: i }));
  return result;
}
