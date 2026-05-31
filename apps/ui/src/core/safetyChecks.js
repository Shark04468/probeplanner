/**
 * Safety validation checks for probe trajectories.
 *
 * FIX: constraint key 'lambda' → 'lambdaBoundary' to match ConstraintsPanel.jsx
 */

import { ANATOMY } from '../constants/anatomy.js';
import { BRAIN_REGIONS } from '../constants/brainRegions.js';

/**
 * Check trajectory against anatomical safety constraints.
 * @param {Object} traj            - Trajectory with ap_mm, dir, depthUm, probeIdx
 * @param {Object} constraintModes - Current constraint mode settings
 * @returns {Array<{key, mode, probeIdx, msgCn, msgEn}>}
 */
export function checkTrajSafety(traj, constraintModes) {
  const issues = [];
  const ap    = traj.ap_mm ?? 0;
  const idx   = traj.probeIdx ?? null;
  const label = `P${idx != null ? idx + 1 : '?'}`;

  // ── Lambda boundary ───────────────────────────────────────────────────────
  if (ap < ANATOMY.lambda_ap_mm) {
    // FIX: was constraintModes?.lambda — key must match ConstraintsPanel
    const mode = constraintModes?.lambdaBoundary ?? 'warn';
    if (mode !== 'off') {
      issues.push({
        key: 'lambdaBoundary', mode, probeIdx: idx,
        msgCn: `${label} 入颅点 AP=${ap.toFixed(2)}mm 超出Lambda边界(${ANATOMY.lambda_ap_mm}mm)`,
        msgEn: `${label} Entry AP=${ap.toFixed(2)}mm exceeds Lambda boundary (${ANATOMY.lambda_ap_mm}mm)`,
      });
    }
  }

  // ── Brain bounds — tip DV depth ───────────────────────────────────────────
  const tipDV = (traj.dir?.[1] ?? 1) * (traj.depthUm ?? 0) / 1000;
  if (tipDV > ANATOMY.max_dv_mm) {
    const mode = constraintModes?.brainBounds ?? 'warn';
    if (mode !== 'off') {
      issues.push({
        key: 'brainBounds', mode, probeIdx: idx,
        msgCn: `${label} 尖端深度${tipDV.toFixed(1)}mm超出脑组织范围(${ANATOMY.max_dv_mm}mm)`,
        msgEn: `${label} Tip depth ${tipDV.toFixed(1)}mm exceeds brain bounds (${ANATOMY.max_dv_mm}mm)`,
      });
    }
  }

  // ── Brain bounds — tip AP posterior limit ─────────────────────────────────
  const tipAP = ap + (traj.dir?.[0] ?? 0) * (traj.depthUm ?? 0) / 1000;
  if (tipAP < ANATOMY.tip_post_limit_mm) {
    const mode = constraintModes?.brainBounds ?? 'warn';
    if (mode !== 'off') {
      issues.push({
        key: 'brainBounds', mode, probeIdx: idx,
        msgCn: `${label} 尖端AP=${tipAP.toFixed(2)}mm超出脑后缘`,
        msgEn: `${label} Tip AP=${tipAP.toFixed(2)}mm exceeds posterior brain limit`,
      });
    }
  }

  return issues;
}

/**
 * Check hemisphere consistency between probe entry and its covered regions.
 */
export function checkHemisphereIssues(traj, constraintModes) {
  const issues = [];
  const mode = constraintModes?.hemisphere ?? 'warn';
  if (mode === 'off') return issues;

  const idx   = traj.probeIdx ?? null;
  const label = `P${idx != null ? idx + 1 : '?'}`;

  for (const key of (traj.coveredRegions ?? traj.regionsHit ?? [])) {
    const reg = BRAIN_REGIONS[key];
    if (!reg) continue;
    if (Math.abs(reg.ml) < 0.5 || Math.abs(traj.ml_mm ?? 0) < 0.5) continue;
    if (Math.sign(traj.ml_mm) !== Math.sign(reg.ml)) {
      issues.push({
        key: 'hemisphere', mode, probeIdx: idx,
        msgCn: `${label} 入颅在${(traj.ml_mm ?? 0) > 0 ? '右' : '左'}半球，目标${key}在${reg.ml > 0 ? '右' : '左'}半球`,
        msgEn: `${label} Entry in ${(traj.ml_mm ?? 0) > 0 ? 'R' : 'L'} hemisphere, target ${key} in ${reg.ml > 0 ? 'R' : 'L'}`,
      });
      break;   // one warning per probe is enough
    }
  }
  return issues;
}

/**
 * Run all safety checks on a full set of trajectories.
 */
export function validateAllTrajectories(selected, constraintModes) {
  const issues = [];
  for (const traj of selected) {
    issues.push(...checkTrajSafety(traj, constraintModes));
    issues.push(...checkHemisphereIssues(traj, constraintModes));
  }
  return issues;
}
