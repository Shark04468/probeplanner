# ProbePlanner Native Artifact Package

The native upgrade is fail-closed: planner-engine expects an explicit artifact
root and CI gates must not depend on a developer-local drive.

## Required Layout

Set `PROBEPLANNER_ARTIFACT_ROOT` to a directory with this minimum layout:

```text
artifact-root/
  artifact_manifest.json
  annotation.npy
  region_map_v2.json  # preferred
  region_map.json     # compatibility fallback when v2 is unavailable
  stats.json
  layered_sdf.npz
  signed_sdf_mm.npy
  skull_sdf_mm.npy
  brain_surface_sdf_mm.npy
  ventricle_sdf_mm.npy
  vessel_sdf_mm.npy
  sulcus_sdf_mm.npy
  outer_tissue_sdf_mm.npy  # or cortex_outside_sdf_mm.npy
  drilling_surface_sdf_mm.npy
  registration/
    registration_summary.json
```

`region_map_v2.json` is preferred because it preserves aliases and canonical
labels. `region_map.json` is accepted by the launcher and native engine as a
compatibility fallback. New packages should include both files when possible.

`layered_sdf.npz` must contain `signed_sdf_mm` and every risk channel used by
the hard constraints:

- `skull_sdf_mm`
- `brain_surface_sdf_mm`
- `ventricle_sdf_mm`
- `vessel_sdf_mm`
- `sulcus_sdf_mm`
- `outer_tissue_sdf_mm` or `cortex_outside_sdf_mm`
- `drilling_surface_sdf_mm`

For native planning, export mmap-ready `.npy` files next to the package or under
`.cache/native-artifacts` before delivery:

```powershell
scripts/prepare_native_artifacts.ps1 -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
```

The launcher can generate these files from `layered_sdf.npz`, but the engine
health gate is fail-closed when any required class is missing. The engine will
prefer these files when present:

```text
artifact-root/
  signed_sdf_mm.npy
  skull_sdf_mm.npy
  brain_surface_sdf_mm.npy
  ventricle_sdf_mm.npy
  vessel_sdf_mm.npy
  sulcus_sdf_mm.npy
  outer_tissue_sdf_mm.npy
  drilling_surface_sdf_mm.npy
```

Optional MRI/QC assets should stay under the same root and be referenced by
`artifact_manifest.json`: `mri_surfaces/`, `overlays/`, `qc/`, `jacobian.npy`,
`skull_mask.npy`, `outer_tissue_mask.npy`, and `warped_mri` volumes. Missing
`warped_mri` is reported as optional because native planning, surface
visualization, and registration QC can still run from the other artifacts.

## CI Runner Variables

Configure these repository variables on the self-hosted Windows runner:

- `PROBEPLANNER_ARTIFACT_ROOT`: absolute path to the artifact package.
- `PERF_BASELINE_SUMMARY`: baseline summary JSON for the 10-case legacy gate.
- `BROWSER3D_BASELINE_SUMMARY`: baseline summary JSON for the production 3D gate.

The native workflow runs static checks on GitHub-hosted Windows and runs
backend/SDF/3D/MRI gates on a self-hosted Windows runner for pull requests and
pushes. It fails if required artifact or baseline paths are missing. MRI
registration QC is a separate gate and fails when `/api/mri/registration/qc`
reports `qualityGate.passed != true`.

The self-hosted jobs run `scripts/verify_ci_environment.ps1` before starting
services. The script fails closed when artifact roots, baseline summaries, or
the headed GPU/display environment are not configured.

## Acceptance Commands

```powershell
npm run prepare:cases
npm run perf:gate:backend -- --baseline reports/baseline-legacy-10/summary.json --out reports/native-backend --case-group replay10 --case-limit 10 --runs 3
npm run perf:gate:sdf -- --baseline reports/baseline-legacy-10/summary.json --out reports/sdf-adversarial --runs 1
npm run sdf:boundary
npm run perf:gate:3d -- --baseline reports/browser3d-profile-current-dpr1-v3/summary.json --out reports/browser3d --frontend-url http://127.0.0.1:3100 --backend-url http://127.0.0.1:5000 --runs 0
npm run mri:qc
```

The 3D gate is strict by default: it must satisfy absolute FPS/frame/load
thresholds and must not regress beyond the configured relative baseline limits.
High absolute FPS does not override a relative regression.

`npm run sdf:boundary` directly samples the real layered SDF channels and
requires each safety layer to have finite samples immediately below and above
its configured threshold. This complements replay-based reject/pass controls and
guards against a gate that only proves the class name was wired through.

The default validated profile is `ambmc_true_outer_full`. The older
`mri_allen_ants_shape_head_baseline` profile remains a legacy build artifact and
must not be used as the acceptance default unless its registration QC gate is
rerun and passes.

## Target Semantics

Default native planning requires atlas-backed target keys. Known non-atlas
replay targets are excluded from perf-gate cases and recorded as
`nativeExcludedFallbackTargetKeys`. Runtime `fallbackTargetKeys` in a native
gate is a failure.

Regenerate replay cases against the delivered native region map, not a
hard-coded target list:

```powershell
$env:PROBEPLANNER_ARTIFACT_ROOT = "C:\path\to\planner_artifacts"
npm run prepare:cases -- --artifact-root "$env:PROBEPLANNER_ARTIFACT_ROOT"
```

For demo-only behavior, a request may set both:

```json
{
  "strictAtlasTargets": false,
  "allowFallbackTargetCoverage": true
}
```

Do not use this mode for medical planning acceptance.

## DRCC Solver Semantics

Native DRCC supports `exact_pool`, `hybrid`, `heuristic`, and `cvar_relax`.
Legacy Python MISOCP/CVX semantics are not claimed as native equivalent. When
the native engine service is enabled, `drccSolver=misocp` fails closed with
`unsupported_native_drcc_solver`; use the legacy Python backend only for
historical MISOCP comparison runs.
