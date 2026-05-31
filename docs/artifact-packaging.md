# ProbePlanner Native Artifact Package

The native upgrade is fail-closed: planner-engine expects an explicit artifact
root and CI gates must not depend on a developer-local drive.

## Required Layout

Set `PROBEPLANNER_ARTIFACT_ROOT` to a directory with this minimum layout:

```text
artifact-root/
  artifact_manifest.json
  annotation.npy
  region_map_v2.json
  stats.json
  layered_sdf.npz
  registration/
    registration_summary.json
```

`region_map.json` is accepted as a compatibility fallback when
`region_map_v2.json` is not available, but new artifact packages should include
`region_map_v2.json`.

`layered_sdf.npz` must contain `signed_sdf_mm` and any available risk channels
used by the hard constraints:

- `skull_sdf_mm`
- `brain_surface_sdf_mm`
- `ventricle_sdf_mm`
- `vessel_sdf_mm`
- `sulcus_sdf_mm`
- `outer_tissue_sdf_mm`
- `drilling_surface_sdf_mm`

For faster startup, export mmap-ready `.npy` files next to the package or under
`.cache/native-artifacts`:

```powershell
scripts/prepare_native_artifacts.ps1 -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
```

The engine will prefer these files when present:

```text
artifact-root/
  signed_sdf_mm.npy
  skull_sdf_mm.npy
  brain_surface_sdf_mm.npy
  ventricle_sdf_mm.npy
  vessel_sdf_mm.npy
  sulcus_sdf_mm.npy
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

## Acceptance Commands

```powershell
npm run prepare:cases
npm run perf:gate:backend -- --baseline reports/baseline-legacy-10/summary.json --out reports/native-backend --case-group replay10 --case-limit 10 --runs 3
npm run perf:gate:sdf -- --baseline reports/baseline-legacy-10/summary.json --out reports/sdf-adversarial --runs 1
npm run perf:gate:3d -- --baseline reports/browser3d-profile-current-dpr1-v3/summary.json --out reports/browser3d --frontend-url http://127.0.0.1:3100 --backend-url http://127.0.0.1:5000 --runs 0
npm run mri:qc
```

The default validated profile is `ambmc_true_outer_full`. The older
`mri_allen_ants_shape_head_baseline` profile remains a legacy build artifact and
must not be used as the acceptance default unless its registration QC gate is
rerun and passes.

## Target Semantics

Default native planning requires atlas-backed target keys. Known non-atlas
replay targets are excluded from perf-gate cases and recorded as
`nativeExcludedFallbackTargetKeys`. Runtime `fallbackTargetKeys` in a native
gate is a failure.

For demo-only behavior, a request may set both:

```json
{
  "strictAtlasTargets": false,
  "allowFallbackTargetCoverage": true
}
```

Do not use this mode for medical planning acceptance.
