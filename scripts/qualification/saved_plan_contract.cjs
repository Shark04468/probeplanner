const { same, sha, requireThat } = require('./ui_contract.cjs');
function validateSavedInput(bytes, origin, runtime) {
  requireThat(origin.role === 'actual_saved_plan_scene_inspection_only' &&
    origin.allowed_optimization_requests === 0 && origin.anatomy_accepted === false &&
    origin.full_first_request_workflow_accepted === false && origin.new_untouched_evidence === false,
  'Saved-plan scope must remain limited');
  requireThat(sha(bytes) === origin.sha256, 'Saved-plan bytes differ');
  const plan = JSON.parse(bytes.toString('utf8'));
  requireThat(plan._coordinateSystem === 'pinpoint_ap_posterior_positive_mm', 'Coordinate convention differs');
  requireThat(plan._coordinateCorrectionApplied === false, 'Unexpected coordinate correction');
  requireThat(plan._sourcePlan?.status === 'linked_to_saved_planner_result', 'Original export is not linked');
  for (const k of ['requestId', 'requestHash', 'responseHash'])
    requireThat(plan._sourcePlan[k] === origin.source_plan[k], 'Original identity differs: ' + k);
  for (const [key, expected] of [['optimizerSha256', origin.optimizer_sha256],
    ['profileContractSha256', origin.profile_sha256]])
    requireThat(plan._exportRuntime[key] === expected && runtime[key] === expected, 'Runtime differs: ' + key);
  requireThat(plan.probes.length === 1 && same(plan.probes[0].coveredRegions, ['CA1', 'CA3']) &&
    plan.probes[0].probeType === 'NP2.0-1sh', 'Saved case differs');
  return plan;
}
function validateImportedExport(original, received, downloaded) {
  requireThat(received.ok && same(received.pinpointData, downloaded), 'Actual imported export response differs');
  requireThat(downloaded._sourcePlan?.status === 'unverified_geometry', 'Imported geometry improperly recertified');
  requireThat(original._coordinateSystem === downloaded._coordinateSystem, 'Round-trip convention differs');
  requireThat(downloaded.probes?.length === original.probes.length, 'Round-trip probe count differs');
  for (let i = 0; i < original.probes.length; i++) {
    for (const k of ['ap', 'ml', 'dv', 'phi', 'theta', 'spin', 'depth', 'probeType',
      'shankCount', 'shankPitchUm', 'radiusUm', 'coveredRegions'])
      requireThat(same(original.probes[i][k], downloaded.probes[i][k]), 'Round-trip geometry differs: ' + k);
  }
  return { geometryEqual: true, importedStatus: 'unverified_geometry', revalidated: false };
}
module.exports = { validateSavedInput, validateImportedExport };
