# Reproducibility

## Locked release

The manuscript software version is tag `v0.2.0-paper`. The tag, GitHub Release, Zenodo software record, and Supplementary Data `SOURCE_COMMIT.txt` must resolve to the same Git commit.

Runtime versions are locked by:

- `.nvmrc` and both `package-lock.json` files for Node.js;
- `rust-toolchain.toml` and `Cargo.lock` for Rust;
- `.python-version`, `environment.yml`, and `apps/backend-compat/requirements-lock-py310.txt` for Python 3.10;
- `supplementary-data/manifest.json` and `SHA256SUMS` for paper-facing results.

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
npm run release:reproduce -- -SourceRef v0.2.0-paper -PythonExe python
```

Raw MRI/CCF experiments additionally require the external data roots above. Their source checksums and atlas checksums are recorded in Supplementary Data; absence of nonredistributable inputs must fail closed rather than substitute synthetic data.

## Integrity check

```powershell
npm run release:verify
Get-Content supplementary-data\SHA256SUMS
```

The release verifier rejects absolute Windows user/data paths, missing manifest cases, generated cache directories, version mismatch, and Supplementary Data checksum mismatch.
