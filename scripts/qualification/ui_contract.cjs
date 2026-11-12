const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const canonical = v => Array.isArray(v) ? v.map(canonical) :
  v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function requireThat(ok, message) { if (!ok) throw Error(message); }
function literal(n) {
  if (['StringLiteral', 'NumericLiteral', 'BooleanLiteral'].includes(n.type)) return n.value;
  if (n.type === 'NullLiteral') return null;
  if (n.type === 'ArrayExpression') return n.elements.map(literal);
  if (n.type === 'ObjectExpression') return Object.fromEntries(n.properties.map(p => {
    requireThat(p.type === 'ObjectProperty' && !p.computed && !p.method, 'Nonliteral property');
    return [p.key.name ?? p.key.value, literal(p.value)];
  }));
  throw Error('Unsupported literal: ' + n.type);
}
function derive(source) {
  const ui = path.join(source, 'apps/ui');
  const parser = createRequire(path.join(ui, 'package.json'))('@babel/parser');
  const bindings = {};
  function constant(file, name) {
    const bytes = fs.readFileSync(path.join(ui, file));
    bindings[file] = sha(bytes);
    const ast = parser.parse(bytes.toString(), { sourceType: 'module', plugins: ['jsx'] });
    for (const item of ast.program.body) {
      const dec = item.type === 'ExportNamedDeclaration' ? item.declaration : item;
      if (dec?.type !== 'VariableDeclaration') continue;
      const match = dec.declarations.find(x => x.id.name === name);
      if (match) return literal(match.init);
    }
    throw Error('Missing constant ' + name);
  }
  const params = constant('src/ProbeplannerGUI.jsx', 'DEFAULT_PARAMS');
  const probe = constant('src/constants/anatomy.js', 'PROBE_PRESETS')['NP2.0-1sh'];
  Object.assign(params, { stabilityTieBreakerEnabled: false, depthUm: probe.depthUm,
    stabilityReferenceTrajectories: [], stabilityReferenceProbeCount: 0 });
  const expectedRequest = { targetKeys: ['CA1', 'CA3'], params,
    probeParams: { ...probe, probeType: 'NP2.0-1sh' },
    constraintModes: constant('src/constants/constraintSchema.js', 'DEFAULT_CONSTRAINTS'),
    correctionFactors: null, correctionUncertainty: null,
    clinicalContext: { species: 'mouse', intendedUse: 'automatic_decision' } };
  return { createdUtc: new Date().toISOString(), expectedRequest, sourceBindings: bindings,
    requestSettingsSha256: sha(JSON.stringify(canonical(expectedRequest))),
    role: 'observed_development_case_portability_not_untouched',
    excludedDynamicField: 'taskId', externalSubmissionsAllowed: 1, noPriorPlan: true };
}
function validateExports(result, runtime, replay, downloads) {
  requireThat(result.ok === true && Array.isArray(result.uncovered) && !result.uncovered.length,
    'First plan is not full');
  requireThat(Array.isArray(result.selected) && result.selected.length > 0, 'No selected plan');
  requireThat(replay.ok && replay.record.responseHash === result.responseHash, 'Replay identity differs');
  requireThat(same(result.runtimeIdentity, runtime), 'Runtime identity differs');
  requireThat(downloads.length === 2, 'Both actual downloads required');
  for (const d of downloads) {
    const s = d.received.pinpointData?._sourcePlan;
    requireThat(d.submitted.planRequestId === result.requestId &&
      d.submitted.planResponseHash === result.responseHash, 'Export request differs');
    requireThat(d.received.ok && s?.status === 'linked_to_saved_planner_result', 'Export not linked');
    for (const k of ['requestId', 'requestHash', 'responseHash', 'runtimeIdentity'])
      requireThat(same(s[k], result[k]), 'Export provenance differs: ' + k);
  }
  const p = downloads.find(d => d.format === 'pplan');
  const t = downloads.find(d => d.format === 'txt');
  const data = JSON.parse(fs.readFileSync(p.path, 'utf8'));
  requireThat(same(data, p.received.pinpointData), 'Downloaded JSON differs from real response');
  requireThat(same(data.probes, t.received.pinpointData.probes), 'Export geometries differ');
  const text = fs.readFileSync(t.path, 'utf8');
  requireThat(text.startsWith('# coordinate_system: pinpoint_ap_posterior_positive_mm'), 'Text coordinate header differs');
  const rows = text.split(/\r?\n/).filter(x => x.trim() && !x.startsWith('#')).map(x => x.split(/\s+/));
  requireThat(rows.length === data.probes.length, 'Text probe count differs');
  const fields = ['ap', 'ml', 'dv', 'phi', 'theta', 'spin', 'depth'];
  rows.forEach((row, i) => {
    requireThat(row.length === 8 && row[0] === 'Probe' + i, 'Text row identity differs');
    fields.forEach((k, j) => requireThat(Number.isFinite(Number(row[j + 1])) &&
      Math.abs(Number(row[j + 1]) - Number(data.probes[i][k] ?? 0)) < 1e-10, 'Text geometry differs: ' + k));
  });
  const metadata = text.split(/\r?\n/).filter(x => x.startsWith('# probe_metadata: '))
    .map(x => JSON.parse(x.slice('# probe_metadata: '.length)));
  requireThat(metadata.length === data.probes.length, 'Text hardware metadata absent');
  metadata.forEach((m, i) => {
    requireThat(m.id === 'Probe' + i, 'Text metadata id differs');
    for (const k of ['shankCount', 'shankPitchUm', 'radiusUm', 'shankWidthUm', 'shankThicknessUm', 'coveredRegions'])
      if (data.probes[i][k] !== undefined) requireThat(same(m[k], data.probes[i][k]), 'Text metadata differs: ' + k);
    requireThat(m.probeType === (data.probes[i].probeType ?? data.probes[i].type ?? null), 'Text probe type differs');
  });
  return { passed: true, selected: result.selected.length, textRows: rows.length,
    textProvenance: 'captured_export_response_and_file_hash_not_embedded_full_identity' };
}
module.exports = { derive, same, sha, literal, requireThat, validateExports };
if (require.main === module) {
  const record = derive(path.resolve(process.argv[2]));
  fs.writeFileSync(process.argv[3], JSON.stringify(record, null, 2) + '\n', { flag: 'wx' });
  console.log(record.requestSettingsSha256);
}
