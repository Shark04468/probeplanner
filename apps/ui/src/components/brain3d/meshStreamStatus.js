export function meshStreamStatus(progress, selectedKeys = [], fills = {}, requiredSurfaces = [], surfaces = {}, sectionRequired = false, section = {}, sectionRequestKey = null) {
  const loaded = Math.max(0, Number(progress?.loaded) || 0);
  const failed = Math.max(0, Number(progress?.failed) || 0);
  const total = Math.max(0, Number(progress?.total) || 0);
  const failedTargets = selectedKeys.filter(key => ['failed', 'suppressed'].includes(fills[key]));
  const pendingTargets = selectedKeys.filter(key => !fills[key]);
  const failedSurfaces = requiredSurfaces.filter(key => surfaces[key]?.status === 'failed');
  const pendingSurfaces = requiredSurfaces.filter(key => !failedSurfaces.includes(key) &&
    (surfaces[key]?.status !== 'loaded' || surfaces[key]?.visible !== true ||
      !Number.isInteger(surfaces[key]?.resolvedLod) || surfaces[key].resolvedLod < 0));
  const sectionMatches = sectionRequestKey === null || section.requestKey === sectionRequestKey;
  const failedSections = sectionRequired && sectionMatches && section.status === 'failed' ? ['mri_section'] : [];
  const sectionReady = sectionMatches && section.status === 'loaded' && section.visible === true &&
    [1, 2].includes(section.requestedPlanes) && section.loadedPlanes === section.requestedPlanes &&
    /^[a-f0-9]{64}$/.test(section.manifestSha256 || '');
  const pendingSections = sectionRequired && !sectionReady && !failedSections.length ? ['mri_section'] : [];
  return {
    finished: total > 0 && loaded + failed >= total,
    ready: total > 0 && loaded === total && failed === 0 &&
      failedTargets.length === 0 && pendingTargets.length === 0 &&
      failedSurfaces.length === 0 && pendingSurfaces.length === 0 &&
      failedSections.length === 0 && pendingSections.length === 0,
    failedTargets,
    pendingTargets,
    failedSurfaces,
    pendingSurfaces,
    failedSections,
    pendingSections,
  };
}
