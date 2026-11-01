# Native Engine Status

Last source audit: 2026-07-06
Latest versioned local acceptance evidence: 2026-05-31

## Implemented

- `planner-engine` has an active Rust optimization pipeline instead of a service shell.
- `planner-engine` loads resident `annotation.npy` through mmap, prefers
  `region_map_v2.json`, accepts legacy `region_map.json`, and uses mmap-ready
  SDF `.npy` channels or `layered_sdf.npz` prepared into mmap channels.
- Candidate generation, trajectory voxel sampling, SDF sampling, coverage
  bitsets, conflict checks, and solver kernels run in Rust.
- Flask `/api/optimize` is now a compatibility API. The default launcher path
  enables the local engine service; pass `-LegacyPythonBackend` only for
  historical legacy comparison or unsupported MISOCP/CVX runs.
- Native DRCC supports `exact_pool`, `hybrid`, `heuristic`, and `cvar_relax`.
  Native MISOCP/CVX equivalence is not claimed and native MISOCP requests fail
  closed.

## Local Acceptance Evidence

Versioned summary: `docs/evidence/2026-05-31-summary.json`

- 10-case native backend gate: PASS, 30 runs, 0 failures, backend p95 617 ms,
  scoring p95 116 ms, solver p95 6 ms.
- SDF gate: PASS for skull, brain surface, ventricle, vessel, and sulcus, 10
  runs, 0 failures.
- Browser 3D strict gate: PASS, 3 runs, FPS median 238.1, frame p95 median 8 ms.
- MRI QC profile `ambmc_true_outer_full`: PASS, Dice 0.841848, HD95 0.365718,
  TRE 0.267084.
- Native MRI-profile replay: 20/20 OK, strict atlas lookup failures 0.

## Current Release Boundary

- GitHub/self-hosted Actions proof is still required for release acceptance.
  The workflow expects `PROBEPLANNER_ARTIFACT_ROOT`,
  `PERF_BASELINE_SUMMARY`, `BROWSER3D_BASELINE_SUMMARY`, and a headed GPU
  environment.
- The Tauri app is still a thin host. Installer packaging, sidecar-managed
  engine/backend startup, port conflict handling, and shutdown cleanup remain
  release tasks.
- Real SDF boundary coverage has a boundary-layer check and replay SDF gate.
  Full geometry adversarial trajectories constructed epsilon-inside/outside
  each real risk surface remain the next scientific validation step.
