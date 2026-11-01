export const DEFAULT_REPO_REPLAY_IDS = [
  "2f6de98fc6b14259b50625db53a2ff3f",
  "8b83d8d249834bcba970b8e463ff6a9d",
  "a4650be179fe4325acdc2f72ee022d87",
  "d1c472882d954170a62df28a11ec3aa6",
  "dee9b5b717b349bbb45acd8f6f56dfe1",
  "ea412cef1e0a47aeb70e32b8d045796f",
] as const;

export const DEFAULT_LOCAL_REPLAY_IDS = [
  "325c3ba8f9de4e4d8c40d33ced16a43d",
  "5360cd81765a40f2a73a9ca11f7f66a4",
  "13a2046cc6594fb792bc3025f36393d8",
  "15089cf26d0a4299971ab5e2a240e5f5",
] as const;

export const DEFAULT_GATE_CONFIG = {
  backendAbsoluteP95Ms: 1_000,
  scoringAbsoluteP95Ms: 250,
  solverAbsoluteP95Ms: 50,
  backendRelativeImprovement: 0.4,
  maxPerCaseRegressionRatio: 0.1,
  browserInteractiveReadyMs: 30_000,
  browserFpsMedianMin: 45,
  browserFrameP95MaxMs: 45,
  browserMaxLoadRegressionRatio: 0.1,
  browserMaxFpsRegressionRatio: 0.05,
} as const;

export const DEFAULT_STAGE_ORDER = [
  "received",
  "pre_validation",
  "payload_normalized",
  "quality_gate_check",
  "candidate_generating",
  "candidate_generated",
  "candidate_prefiltered",
  "candidate_enforced_filtered",
  "candidate_scoring",
  "candidate_scored",
  "pareto_ranked",
  "solver_completed",
  "coverage_boosted",
  "targeting_mode_augmented",
  "per_probe_metrics_ready",
  "safety_contract_checked",
  "completed",
] as const;
