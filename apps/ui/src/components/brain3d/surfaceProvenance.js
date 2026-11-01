export function surfaceLabelKey(layerKey, fallbackKey, surfaceStatus) {
  if (layerKey !== 'brainSurface') return fallbackKey;
  const info = surfaceStatus?.layers?.brain_surface;
  if (info?.provenanceType === 'atlas_annotation') return 'brain3dAtlasBrainSurface';
  if (info?.provenanceType === 'declared_mri_mask') return 'brain3dMriMaskBrainSurface';
  return 'brain3dBrainSurfaceUnverified';
}
