# Reproducibility

## Current paper route

The revised paper uses `v0.3.0-paper`, commit
`56868d57c23cbbb1207666f5e420bf47019591c6`. Use the
[version-specific reproduction route](release/v0.3.0-reproduction.md) for its
Python environment, public profile, launcher, evidence and candidate replay.
Use the [UI walkthrough](PAPER_UI_WALKTHROUGH.md) to configure and inspect a plan.

This later documentation correction does not move the published tag or replace
its archived source. The older commands below are retained only for reproducing
the older release; they are not current startup instructions.

## Historical locked release: v0.2.1-paper

The preceding manuscript software version is tag `v0.2.1-paper`.
Both `v0.2.1-paper` and `v0.2.0-paper` remain immutable. When reproducing a historical
release, its tag, GitHub Release, Zenodo version and provenance must resolve to
the same historical source commit.

Runtime versions are locked by:

- `.nvmrc` and both `package-lock.json` files for Node.js;
- `rust-toolchain.toml` and `Cargo.lock` for Rust;
- `.python-version`, `environment.yml`, and `apps/backend-compat/requirements-lock-py310.txt` for Python 3.10;
- `release-evidence/v0.2.1-paper/IMMUTABLE_EVIDENCE_MANIFEST.json` for frozen
  v4/v5 validation records;
- the historical `supplementary-data/manifest.json` and `SHA256SUMS` remain
  bound to `v0.2.0-paper`.

## External data

Raw MRI and Allen CCF volumes are not redistributed. Configure these roots without editing source files:

```powershell
$env:PROBEPLANNER_ARTIFACT_ROOT = "<artifact-root>"
$env:PROBEPLANNER_PROFILE_ROOT = "<artifact-root>\ambmc_true_outer_full"
$env:PROBEPLANNER_REGISTRATION_ROOT = "<registration-root>"
$env:PROBEPLANNER_MRI_CCF_PROFILE_NAME = "ambmc_true_outer_full"
```

The public Supplementary Data uses `${PROBEPLANNER_PROFILE_ROOT}`, `${PROBEPLANNER_REGISTRATION_ROOT}`, `${ALLEN_CCF_ROOT}`, and `${AMBMC_DATA_ROOT}` placeholders instead of workstation paths.

## Environment creation

```powershell
conda env create -f environment.yml
conda activate probeplanner-paper
npm ci
npm --prefix apps/ui ci
```

## Empty-cache verification

The verifier exports the tagged repository with `git archive`, creates isolated npm, application-state, Python virtual-environment, and Cargo target directories, installs from lock files, then runs frontend, backend, and native-engine tests:

```powershell
npm run release:reproduce -- -SourceRef v0.2.1-paper -PythonExe python
```

Raw MRI/CCF experiments additionally require the external data roots above. Their source checksums and atlas checksums are recorded in Supplementary Data; absence of nonredistributable inputs must fail closed rather than substitute synthetic data.

## Integrity check

```powershell
npm run release:verify
python scripts/release/verify_release.py --paper-release-dir release-evidence\v0.2.1-paper
Get-Content release-evidence\v0.2.1-paper\IMMUTABLE_EVIDENCE_MANIFEST.json
```

The release verifier rejects absolute Windows paths in active source, missing
manifest cases, generated cache directories, version mismatch, historical
Supplementary Data checksum changes, frozen source-hash changes, and any
v4/v5 evidence-member mismatch.
