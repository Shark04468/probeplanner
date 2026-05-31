# Native Upgrade Evidence Snapshot

Date: 2026-05-31

This snapshot records the current local acceptance evidence. Raw reports remain
under `reports/` and CI uploads fresh `reports/ci-*` artifacts on every
self-hosted gate run.

Versioned concise summary: `docs/evidence/2026-05-31-summary.json`

## CI Proof Status

The workflow is configured as a PR/push gate, but this local repository has no
Git remote configured at the time of this snapshot. A real GitHub Actions run
on a configured self-hosted Windows runner is still required before release.

Required runner inputs:

- `PROBEPLANNER_ARTIFACT_ROOT`
- `PROBEPLANNER_MRI_CCF_PROFILE_NAME=ambmc_true_outer_full`
- `PERF_BASELINE_SUMMARY`
- `BROWSER3D_BASELINE_SUMMARY`
- GPU/display environment for the headed browser 3D gate

## Backend Native Gate

Latest report: `reports/native-backend-p1p2-fix/report.md`

- Status: PASS
- Cases: 10 fixed replay cases, 3 runs each
- Backend median: 572 ms
- Backend p95: 617 ms
- Failures: 0 / 30
- Scoring p95: 116 ms
- Solver p95: 6 ms
- Runtime fallback targets: none
- Non-atlas replay targets: explicitly excluded from real coverage gate
- Multi-shank runtime and selected payload are checked in the gate report.

## SDF Gate

Latest report: `reports/native-sdf-p1p2-fix/report.md`

- Status: PASS
- Classes covered: skull, brain_surface, ventricle, vessel, sulcus
- Behavior covered: hard reject for unsafe layered SDF constraints plus
  permissive pass-control cases for the same classes
- Backend median: 577 ms
- Backend p95: 602 ms
- Failures: 0 / 10
- Rust unit coverage includes strict near-threshold behavior at the SDF class
  threshold boundary.

Real SDF boundary-layer validation has also passed on the locked
`ambmc_true_outer_full` profile. The gate samples skull, brain surface,
ventricle, vessel, and sulcus channels on both sides of their thresholds.

## 3D Gate

Latest report: `reports/browser3d-strict-v3/report.md`

- Status under strict absolute and relative stability gates: PASS
- Runs: 3 headed production WebGL runs
- Interactive median: 10841 ms
- Total load median: 20926 ms
- FPS median: 238.1
- Frame p95 median: 8 ms
- Relative load drift: 5% against baseline
- Relative FPS drift: 0% against baseline
- Failures: 0 / 3

## MRI QC

Validated profile: `ambmc_true_outer_full`

- Status contract: `artifactReady=true`, `missingArtifacts=[]`
- Dice: 0.841848
- HD95: 0.365718
- TRE: 0.267084
- Gate: PASS

The older `mri_allen_ants_shape_head_baseline` profile is not an acceptance
default because its prior QC metrics failed the gate.

## DRCC Solver Scope

Native DRCC acceptance covers `exact_pool`, `hybrid`, `heuristic`, and
`cvar_relax`. Native MISOCP/CVX equivalence is not claimed. Requests that ask
the native path for MISOCP fail closed instead of silently downgrading to a
greedy or approximate solver.
