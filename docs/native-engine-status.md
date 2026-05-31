# Native Engine Status

Last verified: 2026-05-28

## Implemented

- `planner-engine` now has an active Rust optimization pipeline instead of a service shell.
- `planner-engine` loads resident `annotation.npy` through mmap, `region_map_v2.json`,
  and mmap `signed_sdf_mm.npy` SDF from `.cache/native-artifacts` when available.
- Candidate generation runs in Rust from request parameters and atlas-backed target centroids.
- Trajectory sampling uses native arrays at atlas voxel resolution for atlas overlap.
- Atlas overlap scoring uses target-index counters instead of per-voxel string/map updates.
- Coverage and collision data are represented as `BitSet`; greedy selection now avoids
  full N-by-N conflict matrix construction and uses a coverage-recovery pass.
- SDF risk diagnostics are sampled in Rust from mmap SDF with strided voxel lookup.
- Flask `/api/optimize` now defaults to the local engine compatibility route; use
  `-LegacyPythonBackend` to force the old Python path.
- `scripts/run_engine.ps1` starts the native engine in release mode for perf gates.
- Native engine stages are exposed as perf-gate-compatible task stages.
- Perf gate falls back to `computeProfile.nativeTimingsMs` if a fast engine run completes before polling observes every stage.
- Perf gate now enforces backend p95 <= 1000 ms, scoring p95 <= 250 ms,
  and solver p95 <= 50 ms.

## Backend Replay Gate

Reports:

- 10-case legacy baseline: `reports/baseline-legacy-10`
- 10-case strict native gate: `reports/native-atlas-gate-10-mmap-sdf-strict`

Scope:

- 10 fixed replay cases.
- Legacy baseline: 1 run per case.
- Native current: 3 runs per case, 30 backend runs total.
- 3D checks skipped for this backend-only gate.

Result:

- Overall strict backend gate: PASS.
- Legacy backend median: 17061 ms, p95: 109112 ms, failures: 3/10.
- Native backend median: 332 ms, p95: 613 ms, failures: 0/30.
- Generation median: 1 ms.
- Scoring median: 3 ms, p95: 40 ms.
- Solver median: 1 ms.
- Solver p95: 4 ms.
- Coverage not worse: uncovered median stayed 0 for all 10 gate cases.
- Native current succeeds on the 3 replay cases that failed under the legacy baseline.

## 3D Metric Snapshot

Reports:

- Headless trace snapshot: `reports/browser3d-profile-current`
- Headed hardware snapshot: `reports/browser3d-profile-current-headed-notrace`
- Headed hardware 3-run snapshot: `reports/browser3d-profile-current-headed-3run`

Best recent headed hardware single-run snapshot:

- Unity instance/interactive ready: 9130 ms.
- Total 3D profile load: 14180 ms.
- FPS median sample: 238.1.
- Frame p95 sample: 4.3 ms.
- Unity Build resources: 7.3 MiB.
- Max Unity resource load: 62 ms.
- JS heap used: 154.5 MiB.
- WASM heap used: 98.2 MiB.
- Renderer: Intel UHD Graphics via D3D11.

Current headed hardware 3-run snapshot:

- 3D failures: 0/3.
- Interactive ready median: 9636 ms, p95: 10188 ms.
- Total load median: 14927 ms, p95: 15798 ms.
- FPS median across runs: 26.6, with high variance from 1.0 to 238.1.
- Frame p95 median: 1004 ms.
- Unity Build resources: 7.3 MiB.
- Max Unity resource load p95: 63 ms.

Headless Playwright can fall back to SwiftShader and report misleadingly low FPS;
3D acceptance should use headed hardware runs or a browser trace captured on real GPU.
Draw-call and triangle counters are still not available from the current WebGL build
because Unity appears to cache WebGL entry points before the runtime patch is applied.
The 3-run headed profile does not satisfy the 45 FPS / frame p95 <= 45 ms target,
so full architecture success is backend-complete but 3D-incomplete.

## Remaining Before Full Architecture Success

- Keep the 10-case strict replay gate as the required backend regression gate.
- Add Unity-side or Chrome DevTools Protocol counters for draw calls, triangles,
  materials, and texture memory; the current JS monkey-patch is insufficient.
- Start the next phase with Unity WebGL frame-time profiling: disable readback-heavy
  screenshot paths, reduce duplicate controllers/components, and test LOD/batching.
  If repeated headed profiles remain below target, switch 3D to Unity Native desktop
  while keeping `planner-engine` as the local HTTP/gRPC compute service.
