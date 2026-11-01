export function regionMeasurements(meta) {
  const voxelCount = Number(meta.voxelCount ?? meta.voxel_count ?? 0) || 0;
  const volume = Number(meta.volumeMm3 ?? meta.volume_mm3);
  return {
    voxelCount,
    volumeScore: Number.isFinite(volume) && volume > 0 ? volume : voxelCount,
    nSubregions: Number(meta.nSubregions ?? meta.n_subregions ?? 0) || 0,
    structureId: Number(meta.structureId),
    structureIdPath: Array.isArray(meta.structureIdPath) ? meta.structureIdPath.map(Number) : [],
    presentInVolume: meta.presentInVolume,
  };
}

function related(a, b) {
  if (!Number.isFinite(a?.structureId) || !Number.isFinite(b?.structureId)) return false;
  return a.structureId === b.structureId
    || a.structureIdPath?.includes(b.structureId)
    || b.structureIdPath?.includes(a.structureId);
}

export function selectContextKeys(metadata, explicitKeys, contextKeys, limit) {
  // Explicit targets/contacts are never displaced by a background-fill budget.
  const chosen = [...new Set(explicitKeys)].filter(key => metadata.has(key));
  const context = [...new Set(contextKeys)].filter(key => metadata.has(key) && !chosen.includes(key));
  context.sort((a, b) => (metadata.get(b).volumeScore - metadata.get(a).volumeScore)
    || a.localeCompare(b, 'en'));
  for (const key of context) {
    if (chosen.length >= limit) break;
    const meta = metadata.get(key);
    if (meta.aggregateParent || meta.presentInVolume === false) continue;
    if (chosen.some(other => related(meta, metadata.get(other)))) continue;
    chosen.push(key);
  }
  return chosen;
}
