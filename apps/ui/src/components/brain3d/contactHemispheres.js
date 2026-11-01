const SAMPLE_SIDES = ['left', 'right', 'midline'];

export function sampledContactHemispheres(interval) {
  // The legacy singular field is a majority label, not complete contact evidence.
  const sides = Array.isArray(interval?.hemispheres) ? interval.hemispheres : [];
  return SAMPLE_SIDES.filter(side => sides.includes(side));
}

export function contactRegionHemispheres(intervals, regions) {
  const sidesByKey = new Map(regions.map(key => [key, new Set()]));
  for (const interval of intervals) {
    const sides = sidesByKey.get(interval.acronym);
    if (!sides) continue;
    for (const side of sampledContactHemispheres(interval)) sides.add(side);
  }
  // Mesh and probe coordinates both use positive CCF ML for viewer x > 0.
  // The optional UI label LR correction must not mirror sampled geometry.
  return regions.map(key => ({ key, hemispheres: SAMPLE_SIDES.filter(side => sidesByKey.get(key).has(side)) }));
}
