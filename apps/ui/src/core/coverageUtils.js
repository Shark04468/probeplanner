/**
 * coverageUtils.js  —  覆盖率计算工具
 *
 * 修复点（相对原版）：
 *  1. trajectoryCoversRegion: 字段名全部改为 brainRegions.js 实际字段
 *       r.ap_mm  → -r.ap  （region.ap 是后方为正，trajectory 是前方为正，需取反）
 *       r.ml_mm  → r.ml
 *       r.dv_mm  → r.dv
 *       r.radiusUm → REGION_RADII_UM[regionKey]（需额外 import）
 *  2. computeCoverage: traj.covered_regions → traj.coveredRegions
 */

import { BRAIN_REGIONS, REGION_RADII_UM } from '../constants/brainRegions';

const DEG2RAD = Math.PI / 180;

// ─── 内部几何工具 ──────────────────────────────────────────────────────────────

/** 点 P 到线段 (A→B) 的最近点 */
function closestPointOnSegment(px, py, pz, ax, ay, az, bx, by, bz) {
  const dx = bx - ax, dy = by - ay, dz = bz - az;
  const lenSq = dx * dx + dy * dy + dz * dz;
  if (lenSq < 1e-10) return { x: ax, y: ay, z: az };
  const t = Math.max(0, Math.min(1,
      ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / lenSq
  ));
  return { x: ax + t * dx, y: ay + t * dy, z: az + t * dz };
}

/** 两点欧氏距离（mm） */
function dist3(x1, y1, z1, x2, y2, z2) {
  return Math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2);
}

// ─── 导出工具函数 ─────────────────────────────────────────────────────────────

/**
 * 从 trajectory 对象计算入颅点和尖端坐标（均为前方正坐标系，单位 mm）
 */
export function getTrajectoryEndpoints(traj) {
  const ap     = traj.ap_mm || 0;
  const ml     = traj.ml_mm || 0;
  const dvE    = traj.entryDvCorr_mm || traj.dvEntry || 0;
  const depthMm = (traj.depthUm || (traj.depth || 4) * 1000) / 1000;

  // theta_deg 优先；若只有 elevation（仰角），则 theta = 90 - elevation
  const thetaDeg = traj.theta_deg != null
      ? traj.theta_deg
      : (traj.elevation != null ? 90 - traj.elevation : 0);
  const phiDeg = traj.phi_deg ?? traj.azimuth ?? 0;

  const thetaRad = thetaDeg * DEG2RAD;
  const phiRad   = phiDeg   * DEG2RAD;

  const dAP = Math.sin(thetaRad) * Math.cos(phiRad);
  const dML = Math.sin(thetaRad) * Math.sin(phiRad);
  const dDV = Math.cos(thetaRad);   // 腹侧为正

  const entryAP = ap;
  const entryML = ml;
  const entryDV = dvE;

  const tipAP = entryAP + dAP * depthMm;
  const tipML = entryML + dML * depthMm;
  const tipDV = entryDV + dDV * depthMm;

  return { entryAP, entryML, entryDV, tipAP, tipML, tipDV, depthMm };
}

/**
 * 判断单根探针是否覆盖某脑区（几何方法）
 *
 * 坐标系说明：
 *   brainRegions.js 中 reg.ap 是后方为正（posterior-positive）
 *   trajectory 中 ap_mm 是前方为正（anterior-positive）
 *   因此脑区坐标转换：rAP = -reg.ap
 */
export function trajectoryCoversRegion(traj, regionKey) {
  const reg = BRAIN_REGIONS[regionKey];
  if (!reg) return false;

  // ✅ 使用 brainRegions.js 实际字段名，并修正 AP 方向
  const rAP      = -reg.ap;                                    // posterior→anterior 取反
  const rML      =  reg.ml;
  const rDV      =  reg.dv;
  const radiusMm = (REGION_RADII_UM[regionKey] ?? 500) / 1000; // ✅ 从 REGION_RADII_UM 查

  const { entryAP, entryML, entryDV, tipAP, tipML, tipDV } = getTrajectoryEndpoints(traj);

  // A: 轨迹线段最近点距脑区中心
  const cp = closestPointOnSegment(
      rAP, rML, rDV,
      entryAP, entryML, entryDV,
      tipAP,   tipML,   tipDV,
  );
  const distToLine = dist3(rAP, rML, rDV, cp.x, cp.y, cp.z);

  // B: 尖端距脑区中心（适用于深部核团）
  const distToTip = dist3(rAP, rML, rDV, tipAP, tipML, tipDV);

  return distToLine < radiusMm * 1.3 || distToTip < radiusMm * 0.9;
}

/**
 * 计算多探针覆盖率
 *
 * 优先使用 trajectory.coveredRegions（来自优化器），
 * 若为空则以 trajectoryCoversRegion 几何回退计算。
 *
 * @param {Array} trajectories  - results.selected 探针数组
 * @param {Set}   targetRegions - 用户选择的目标脑区 Set<string>
 * @returns {{ coveredSet: Set, coverage: number, probesWithCoverage: Array }}
 */
export function computeCoverage(trajectories, targetRegions) {
  if (!trajectories?.length || !targetRegions?.size) {
    return { coveredSet: new Set(), coverage: 0, probesWithCoverage: [] };
  }

  const probesWithCoverage = trajectories.map(traj => {
    // ✅ 只用 coveredRegions（已移除 traj.covered_regions 的引用）
    let covered = traj.coveredRegions ?? [];

    // 优化器未填充时，几何回退
    if (!covered.length) {
      covered = [...targetRegions].filter(k => trajectoryCoversRegion(traj, k));
    }

    // 过滤：只保留目标集内的脑区
    covered = covered.filter(k => targetRegions.has(k));

    return { ...traj, coveredRegions: covered };
  });

  const coveredSet = new Set(probesWithCoverage.flatMap(p => p.coveredRegions));
  const coverage   = coveredSet.size / targetRegions.size;

  return { coveredSet, coverage, probesWithCoverage };
}