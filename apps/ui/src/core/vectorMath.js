/**
 * 3D Vector math utilities for trajectory computation.
 * All functions work with [x, y, z] arrays.
 */

export const v3 = {
  sub:   (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]],
  add:   (a, b) => [a[0]+b[0], a[1]+b[1], a[2]+b[2]],
  dot:   (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2],
  scale: (a, s) => [a[0]*s, a[1]*s, a[2]*s],
  norm:  (a)    => Math.sqrt(a[0]*a[0] + a[1]*a[1] + a[2]*a[2]),
  cross: (a, b) => [
    a[1]*b[2] - a[2]*b[1],
    a[2]*b[0] - a[0]*b[2],
    a[0]*b[1] - a[1]*b[0],
  ],
  normalize: (a) => {
    const n = Math.sqrt(a[0]*a[0] + a[1]*a[1] + a[2]*a[2]);
    return n < 1e-12 ? [0,0,0] : [a[0]/n, a[1]/n, a[2]/n];
  },
  lerp: (a, b, t) => [
    a[0] + (b[0]-a[0]) * t,
    a[1] + (b[1]-a[1]) * t,
    a[2] + (b[2]-a[2]) * t,
  ],
};

/**
 * Minimum distance between two 3D line segments (Eberly algorithm).
 * Used for probe collision detection.
 * 
 * @param {number[]} p0 - Segment A start
 * @param {number[]} p1 - Segment A end
 * @param {number[]} q0 - Segment B start
 * @param {number[]} q1 - Segment B end
 * @returns {number} Minimum distance in same units as input
 */
export function segSegDist(p0, p1, q0, q1) {
  const u = v3.sub(p1, p0), vv = v3.sub(q1, q0), w0 = v3.sub(p0, q0);
  const a = v3.dot(u, u), b = v3.dot(u, vv), c = v3.dot(vv, vv);
  const d = v3.dot(u, w0), e = v3.dot(vv, w0);
  const D = a * c - b * b, EPS = 1e-12;
  let sN, sD = D, tN, tD = D;
  if (D < EPS) { sN = 0; sD = 1; tN = e; tD = c; }
  else {
    sN = b * e - c * d; tN = a * e - b * d;
    if (sN < 0)      { sN = 0; tN = e; tD = c; }
    else if (sN > sD) { sN = sD; tN = e + b; tD = c; }
  }
  if (tN < 0) {
    tN = 0;
    if (-d < 0) sN = 0;
    else if (-d > a) sN = sD;
    else { sN = -d; sD = a; }
  } else if (tN > tD) {
    tN = tD;
    const tmp = -d + b;
    if (tmp < 0) sN = 0;
    else if (tmp > a) sN = sD;
    else { sN = tmp; sD = a; }
  }
  const s = Math.abs(sN) < EPS ? 0 : sN / sD;
  const t = Math.abs(tN) < EPS ? 0 : tN / tD;
  return v3.norm(v3.sub(v3.add(w0, v3.scale(u, s)), v3.scale(vv, t)));
}

/**
 * Distance from point P to line segment [A, B].
 * @param {number[]} P - Point
 * @param {number[]} A - Segment start
 * @param {number[]} B - Segment end
 * @returns {number} Distance
 */
export function ptSegDist(P, A, B) {
  const u = v3.sub(B, A), w = v3.sub(P, A);
  const lsq = v3.dot(u, u);
  if (lsq < 1e-12) return v3.norm(w);
  const t = Math.max(0, Math.min(1, v3.dot(w, u) / lsq));
  return v3.norm(v3.sub(P, v3.add(A, v3.scale(u, t))));
}

/**
 * Closest point on segment [A, B] to point P.
 * Returns { point, t } where t ∈ [0,1] is the parametric position.
 */
export function closestPointOnSeg(P, A, B) {
  const u = v3.sub(B, A), w = v3.sub(P, A);
  const lsq = v3.dot(u, u);
  if (lsq < 1e-12) return { point: [...A], t: 0 };
  const t = Math.max(0, Math.min(1, v3.dot(w, u) / lsq));
  return {
    point: v3.add(A, v3.scale(u, t)),
    t,
  };
}
