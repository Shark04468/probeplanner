export function measurementValue(report, key) {
  if (report?.metricAssessment?.[key]?.usableAsMeasurement !== true) return null;
  const value = report?.measurementMetrics?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function measurementView(report) {
  const result = {};
  for (const key of ['dice', 'hd95Mm', 'treMm', 'jacobianAnomalyRatio']) {
    result[key] = measurementValue(report, key);
  }
  result.treMeanMm = result.treMm;
  result.treMedianMm = null;
  result.treMaxMm = null;
  result.treAxisMm = {};
  result.treCi95Mm = result.treMm === null ? null : report?.measurementCi95?.treMm || report?.metrics?.treCi95Mm || null;
  for (const key of ['jacobianMean', 'jacobianStd', 'jacobianMin', 'jacobianMax']) {
    const value = report?.metrics?.[key];
    result[key] = result.jacobianAnomalyRatio !== null && typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
  return result;
}

export function registrationGateState(gate) {
  if (String(gate?.status || '').startsWith('INCOMPLETE')) return 'incomplete';
  if (gate?.passed === false) return 'failed';
  if (gate?.passed === true && gate?.status === 'PASS') return 'passed';
  return 'incomplete';
}
