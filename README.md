# ProbePlanner

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21307547.svg)](https://doi.org/10.5281/zenodo.21307547)

ProbePlanner is an auditable planning workflow for mouse electrophysiology probe trajectories. It combines an Allen CCF/MRI-derived artifact profile, a React planning interface, a Flask compatibility API, a Rust native planning engine, replay/performance tooling, and paper-facing evidence exports.

This repository is the source-code release used for the Frontiers in Neuroinformatics Technology & Code manuscript draft.

## Repository

- Project link: https://github.com/Shark04468/probeplanner
- License: MIT
- Release tag used for the manuscript: `v0.2.0-paper`
- Archived release commit: `0d5748e05c05e54d1330d05009747bcb4fa915a5`
- Version DOI: `10.5281/zenodo.21307547`
- Concept DOI: `10.5281/zenodo.21307546`
- Primary tested operating system: Windows 11 with PowerShell 7/Windows PowerShell 5.1
- Experimental support: Linux/macOS for TypeScript and Rust checks; MRI artifact scripts and launcher commands are Windows-first

## Layout

- `apps/ui`: React UI and Unity WebGL host.
- `apps/backend-compat`: Flask compatibility API and MRI/CCF artifact scripts.
- `apps/perf-gate`: replay benchmark, performance gates, and browser 3D checks.
- `planner-engine`: Rust native planning engine.
- `bindings/python`: PyO3 native extension entrypoint.
- `compat/python`: Flask-side native-engine client examples.
- `contracts`: request/response contracts shared by the compatibility layer and engine.
- `cases`: replay manifest and request fixtures.
- `docs`: architecture and artifact packaging notes.

Large MRI/CCF artifacts are not stored in git. The default native delivery profile is `ambmc_true_outer_full`; public releases should archive redistributable artifacts separately when source dataset licenses allow it.

## Installation

Prerequisites:

- Node.js 20 or later
- Rust stable toolchain
- Python 3.10 or later
- PowerShell

Install JavaScript dependencies:

```powershell
npm install
npm run ui:install
```

Check TypeScript, unit tests, UI build, backend syntax, and Rust compilation:

```powershell
npm run typecheck
npm test
npm run ui:build
npm run backend:smoke
npm run rust:check
```

## Artifact setup

Set the artifact root to the directory that contains the MRI/CCF profile directory:

```powershell
$env:PROBEPLANNER_ARTIFACT_ROOT = "<artifact-root-containing-profiles>"
$env:PROBEPLANNER_MRI_CCF_PROFILE_NAME = "ambmc_true_outer_full"
```

The native launcher resolves the profile directory and expects at least:

- `annotation.npy`
- `region_map_v2.json` preferred, or `region_map.json` as the compatibility fallback
- `artifact_manifest.json`
- `layered_sdf.npz` or channel-level SDF arrays when available

Validate the profile:

```powershell
npm run mri:qc -- --manifest "$env:PROBEPLANNER_ARTIFACT_ROOT\$env:PROBEPLANNER_MRI_CCF_PROFILE_NAME\artifact_manifest.json" --expected-shape 528,320,456
```

Expected result for the default delivery profile: manifest/hash validation passes and registration QC passes the configured Dice, HD95, and TRE thresholds. Failed QC must be treated as an artifact blocker, not as a UI/backend failure.

## Minimal native-engine example

Start the native engine on the locked MRI/CCF profile:

```powershell
$env:PROBEPLANNER_ARTIFACT_ROOT = "<artifact-root-containing-profiles>"
$env:PROBEPLANNER_MRI_CCF_PROFILE_NAME = "ambmc_true_outer_full"
npm run engine:start -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT -Port 7878 -KillExisting
```

In another terminal, run the accepted CA1 example:

```powershell
node scripts/run_minimal_example.mjs --case cases\requests\8b83d8d249834bcba970b8e463ff6a9d.json --engine-url http://127.0.0.1:7878 --out output\minimal_example_accepted.json
```

Expected output fields:

- `ok: true`
- `code: null`
- `selected.length >= 1`
- `uncovered.length == 0`
- `coveredCoverageUnits == requiredCoverageUnits`
- no `strict_atlas_target_lookup_failed`

For the manuscript locked-profile workflow demonstration, the accepted CA1 medium case produced coverage 1.0, minimum safety distance 0.491799 mm, safety pass probability 0.75, one accepted selected trajectory, and zero rejected selected trajectories.

## Replay command

Start the native engine plus compatibility backend with the locked profile,
then run replay gates:

```powershell
$env:PROBEPLANNER_ARTIFACT_ROOT = "<artifact-root-containing-profiles>"
$env:PROBEPLANNER_MRI_CCF_PROFILE_NAME = "ambmc_true_outer_full"
$env:PROBEPLANNER_STATE_DIR = "$PWD\.probeplanner\state"
$env:PROBEPLANNER_REPLAY_DIR = "$env:PROBEPLANNER_STATE_DIR\replay"
$env:PROBEPLANNER_AUDIT_LOG = "$env:PROBEPLANNER_STATE_DIR\audit.log"
npm run dev:stack -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT
```

Use `-LegacyPythonBackend` only when intentionally regenerating a historical
Python baseline:

```powershell
npm run dev:stack -- -ArtifactRoot $env:PROBEPLANNER_ARTIFACT_ROOT -LegacyPythonBackend
```

Run a 30-case or full-manifest backend replay:

```powershell
npm run perf:gate:backend -- --backend-url http://127.0.0.1:5000 --runs 1 --backend-warmup-runs 0 --case-limit 30 --out reports\mri-profile-replay-30
npm run perf:gate:backend -- --backend-url http://127.0.0.1:5000 --runs 1 --backend-warmup-runs 0 --case-limit 129 --out reports\mri-profile-replay-129
```

Run the native MRI-profile replay contract directly against the Rust engine:

```powershell
npm run replay:native -- --engine-url http://127.0.0.1:7878 --manifest cases\manifest.json --limit 20 --out reports\native-mri-profile-replay
```

Run the real SDF boundary-layer check on the active artifact profile:

```powershell
npm run sdf:boundary
```

Expected report files:

- `report.md`
- `summary.json`
- `run-result.json`
- `backend-results.csv`
- native replay `cases.csv` and `summary.json` when using `npm run replay:native`

Expected interpretation:

- Request completion is a software execution check.
- Failed registration QC, failed safety gates, and selected-but-rejected trajectories remain valid evidence boundaries.
- Native MRI-profile replay should report `strictAtlasLookupFailureCount: 0` for the locked profile.
- Native DRCC supports `exact_pool`, `hybrid`, `heuristic`, and `cvar_relax`; MISOCP is legacy Python only and fails closed when the native engine service is enabled.
- Replay metrics should not be interpreted as prospective surgical accuracy.

## Frontiers manuscript evidence

The manuscript evidence package generated during local preparation includes:

- locked MRI/CCF profile validation summary,
- representative rejected CA1 example,
- representative accepted CA1 example,
- replay and ablation summaries,
- figure generation scripts,
- Word manuscript and supplementary material generated from the Frontiers template.

The public repository DOI should be created from the tagged GitHub release through Zenodo or another persistent archive.
