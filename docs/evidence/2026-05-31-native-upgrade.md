# Native Upgrade Evidence Snapshot

Date: 2026-05-31

This snapshot records the current local acceptance evidence. Raw reports remain
under `reports/` and CI uploads fresh `reports/ci-*` artifacts on every
self-hosted gate run.

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

## 3D Gate

Latest report: `reports/browser3d-p1p2-fix-v2/report.md`

- Status: PASS
- Runs: 3 headed production WebGL runs
- Interactive median: 13402 ms
- FPS median: 119.0
- Frame p95 median: 22 ms
- Failures: 0 / 3

The gate uses hard absolute limits for interactive load, FPS, and frame p95.
Relative load/FPS drift is still reported, but it no longer fails a run when
absolute performance remains comfortably above the acceptance floor.

## MRI QC

Validated profile: `ambmc_true_outer_full`

- Status contract: `artifactReady=true`, `missingArtifacts=[]`
- Dice: 0.841848
- HD95: 0.365718
- TRE: 0.267084
- Gate: PASS

The older `mri_allen_ants_shape_head_baseline` profile is not an acceptance
default because its prior QC metrics failed the gate.
