export function formatProbeModels(probes, unspecifiedLabel) {
  const counts = new Map();
  for (const probe of probes || []) {
    const model = typeof probe?.probeType === 'string' ? probe.probeType.trim() : '';
    const label = model || unspecifiedLabel;
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts].map(([label, count]) => `${label} x${count}`).join('; ');
}
