# ProbePlanner

[![Concept DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21307546.svg)](https://doi.org/10.5281/zenodo.21307546)

ProbePlanner is an auditable planning workflow for mouse electrophysiology probe trajectories. It combines an Allen CCF/MRI-derived artifact profile, a React planning interface, a Flask compatibility API, a Rust native planning engine, replay/performance tooling, and paper-facing evidence exports.

This source targets the **`v0.3.0-paper` paper release**. Its supported paper
runtime is the Python optimizer, explicitly initialized from a verified
deployment profile. Publication status, the fixed commit, validation reports
and Version DOI are recorded on the release page; a candidate branch or package
version string alone is not a published release.

The preceding `v0.2.1-paper` release froze the corrected source that produced
the historical v5 validation. Neither that tag nor `v0.2.0-paper` is replaced.
Revised-source candidate regeneration and exact models are separately validated;
old certificates are not assigned to new pools. The v4 failures, post-hoc repair
records and old frozen v5 confirmation remain unchanged.

## Repository

- Project link: https://github.com/Shark04468/probeplanner
- License: MIT
- Paper source version: `v0.3.0-paper`; [release and publication status](https://github.com/Shark04468/probeplanner/releases/tag/v0.3.0-paper)
- Preceding paper release: `v0.2.1-paper` at commit `f259a72ecbfdb7dd898c17c4fb3755198f8dfb64`
- Preceding Version DOI: `10.5281/zenodo.21974187` (not the DOI of this source)
- Earlier paper release: `v0.2.0-paper` at commit `0d5748e05c05e54d1330d05009747bcb4fa915a5`, Version DOI `10.5281/zenodo.21307547`
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

- Node.js 20.19+ or 22.12+ (the installed Vite engine constraint); Node 24.14.0 was used for the current revision checks
- Python 3.10 for the paper environment and its committed dependency lock
- Rust stable toolchain only for independent native-engine tooling
- PowerShell for the Windows examples and optional legacy tooling

For the separate maintained runtime/test environment, use Python 3.11 or later
and `apps/backend-compat/requirements.txt`. Update its installer first with
`python -m pip install --upgrade pip==26.2`; its Pillow 13 dependency does not
support Python 3.10. The historical paper lock below remains unchanged for
offline reproduction and is not the maintained network-service environment.
The maintained Rust dependency set requires Rust 1.88 or later (ZIP 9).
These maintenance dependencies do not revalidate the frozen paper experiments.

Install JavaScript dependencies:

```powershell
npm ci
npm --prefix apps/ui ci
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r apps/backend-compat/requirements-lock-py310.txt
python -m pip check
```

Check TypeScript, unit tests, UI build, backend syntax, and Rust compilation:

```powershell
npm run typecheck
npm test
npm run backend:test
npm run ui:build
npm run backend:smoke
npm run rust:check
```

## Supported paper startup

For the prepared public profile, candidate/exact replay and version-specific
release checks, see the [v0.3.0 reproduction route](docs/release/v0.3.0-reproduction.md).
Its data and evidence have separate hashes and licenses. Obtain assets only from
the matching published release and verify the listed hashes; do not substitute a
different atlas profile or infer publication from a version string.

Prepare a separately bound deployment profile with
`apps/backend-compat/scripts/prepare_paper_profile.py`; consult
[the runtime/evidence contract](docs/PAPER_RUNTIME_AND_MRI_EVIDENCE.md) for its input
requirements and scientific limitations. Do not replace missing inputs with an
unrelated profile or edit frozen manifests to force a pass.

After building the UI, run from the root in the activated Python environment:

```powershell
npm run paper:start -- --profile "<prepared-profile>" --run-dir "<new-run-directory>" --static-dir apps/ui/build --port 5039
```

`backend:start` is an explicit alias of this paper Python CLI. It does not start
the native Rust engine. Open `http://127.0.0.1:5039`; `/api/paper/runtime` gives the
actual source/profile identity. Existing output directories, occupied ports and
missing static builds are rejected; no existing listener is terminated. Use
`--check-only` for initialization/QC without an HTTP listener.

The former `start_backend_compat.ps1` is absent after antivirus deletion. Its
classification is unresolved; it has not been restored or whitelisted. The old
`dev:stack`/`desktop:stack` combined launchers are unavailable with that dependency
missing and now fail before process-stop/start operations. Their old PowerShell
flags are not arguments to the paper CLI. Independent native commands below are
not substitutes for version-matched paper validation.

## Legacy native artifact setup

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

Check manifest/hash, overlap, boundary-distance and Jacobian results independently;
do not assume a default profile supplies every required measurement. Anatomical landmark TRE is
unavailable in the public population-atlas profile and must not be inferred
from automatic control points. Failed QC remains an artifact blocker.

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

In the archived native locked-profile demonstration, the CA1 medium case reported
coverage 1.0, a legacy minimum-distance diagnostic of 0.491799 mm, a model probability
of 0.75, one accepted selected trajectory and zero rejected selected trajectories.
These are historical native/software diagnostics, not new Python confirmation or
measured surgical safety.

## Replay command

Replay tooling is separate from the final scientific campaign. Start the explicitly
bound paper runtime above for Python replay and record its exact identity. Do not
use the unavailable legacy combined launchers, and do not interpret a Python/native
backend swap as the same experiment. Fresh scientific runs require a frozen input,
candidate-generation and solver-budget contract.

Run a 30-case or full-manifest backend replay:

```powershell
npm run perf:gate:backend -- --backend-url http://127.0.0.1:5039 --runs 1 --backend-warmup-runs 0 --case-limit 30 --out reports\mri-profile-replay-30
npm run perf:gate:backend -- --backend-url http://127.0.0.1:5039 --runs 1 --backend-warmup-runs 0 --case-limit 129 --out reports\mri-profile-replay-129
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

## Revised-Source Validation

The retained full retrospective regeneration contains 89 positive full/strict
outcomes and nine expected negative fail-closed outcomes. Subsequent separately
frozen request confirmation contains 12 positive full/strict outcomes and one
reused negative control. Exact minima were proved and planner counts matched
for all 89 and all 12 regenerated strict finite pools, with zero timeouts.
The cohorts remain separate and are not biological replicates.

The release checks retain the unchanged original 112-test subset, the complete
329-test backend suite (including that subset), 152 frontend tests, optional
surface-builder tests and separate packaging checks. See the final release's
test report for exact counts, any platform-specific skips and the bound commit.
The [public evidence guide](docs/release/v0.3.0-public-evidence.md) explains the
path-only projection and original-versus-public hash identities.

## Archived Paper Validation

The release preserves three distinct evidence phases:

- **v4 first execution:** 16 positive full, 3 partial, and 1 expected
  fail-closed case. Its failed acceptance gate is preserved unchanged.
- **Post-hoc remediation:** the three v4 partial cases became full and strict
  pass after ontology, selector-completion, and bilateral candidate-generation
  fixes. This is repair evidence, not untouched evidence.
- **v5 frozen confirmation:** 19/19 positive cases were full and strict pass;
  the single negative control failed closed; there were no partial cases or
  errors.

The v5 candidate-pool oracle proved 19/19 minima with 19/19 ProbePlanner
matches and no timeout. The historical exact regression proved 48/48 minima
with 48/48 matches and no timeout. Exactness means minimum cardinality within
the regenerated strictly filtered candidate pool, not a continuous-space
global optimum.

The GitHub release assets contain the byte-preserved v4/v5 evidence, per-file
hash manifest, test report, release provenance, and manuscript update values.

## Claim boundary

Supported claims are software behavior on the locked cohorts, strict
constraint auditing, fail-closed behavior, and candidate-pool minimum probe
count.

This release does not establish subject-specific anatomical TRE,
bregma/lambda or skull-landmark accuracy, native-space surgical safety for an
individual animal, vascular or ventricular avoidance where masks are absent,
histological implantation accuracy, or continuous-space global optimality.
