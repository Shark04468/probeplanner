# Frontiers Technology & Code Release Package

This checklist is the paper-facing release package for the ProbePlanner native-engine Technology & Code submission.

## Release Identity

- Proposed release tag: `v0.1.0-frontiers`
- Package name: `probeplanner-native-upgrade`
- Repository: `https://github.com/Shark04467/probeplanner`
- License: MIT
- Citation metadata: `CITATION.cff`
- Zenodo metadata: `.zenodo.json`
- Primary tested OS: Windows x64
- Languages/runtime: Rust native engine, TypeScript/React UI, Flask/Python compatibility backend, Unity WebGL viewer

## Publish Status

- Local release metadata exists: yes
- Local source tree has release notes: yes
- Local git remote configured: no
- GitHub remote release: pending repository remote configuration and user publication
- Zenodo DOI: pending user-authorized GitHub-Zenodo archive

The repository cannot honestly claim a Zenodo DOI until the GitHub release is published and archived by Zenodo. Do not invent a DOI in the manuscript. Use `DOI: pending archive` until Zenodo returns a real DOI.

## Evidence Snapshot

- Native replay110 3-run stability: 330/330 backend runs succeeded; backend p50 median 580.62 ms; backend p95 median 609.87 ms.
- Supported target set: supported main replay targets DG, RSP, TH, VPM; partial targets CA1, CA3, LGd, LP, VISp, VISpm, VISrl; boundary targets ACB and MOs.
- Digital phantom: 16/16 targets covered, target-distance p95 0.29 mm, selected boundary violation 0.0%, selected obstacle violation 0.0%.
- Versioned evidence snapshot: `docs/evidence/2026-07-08-frontiers-readiness-experiments.md`.

## Required Evidence Reports

- `reports/paper-native-replay110-3run`
- `reports/paper-native-replay110-stability`
- `reports/paper-supported-target-set`
- `reports/paper-phantom-accuracy`
- `reports/paper-legacy-native-comparison110`
- `reports/paper-failure-analysis`
- `reports/paper-trajectory-audit`
- `reports/paper-reproducibility-pack`

## One-Command Local Reproduction Sequence

```powershell
npm install
npm --prefix apps/ui install
npm run prepare:cases -- --artifact-root <ARTIFACT_ROOT>
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_dev_stack.ps1 -ArtifactRoot <ARTIFACT_ROOT> -UseEngineService -UiPort 3100
npm run perf:paper:replay110 -- --backend-url http://127.0.0.1:5000 --runs 3 --out reports/paper-native-replay110-3run
npm run perf:paper:stability -- --results reports/paper-native-replay110-3run/run-results.jsonl --out reports/paper-native-replay110-stability
npm run perf:paper:targets
npm run perf:paper:phantom
npm run perf:paper:repro
```

## Release Checklist

- [x] MIT license present.
- [x] `CITATION.cff` present.
- [x] `.zenodo.json` present.
- [x] Artifact schema documented in `docs/artifact-packaging.md`.
- [x] Case manifest generated in `cases/manifest.json`.
- [x] Summary JSON reports generated for main experiments.
- [x] OS/language/restriction information documented.
- [ ] Create a clean commit containing release files and reports.
- [ ] Create local tag `v0.1.0-frontiers` on the clean release commit.
- [ ] Push tag to GitHub.
- [ ] Create GitHub release from tag.
- [ ] Archive release with Zenodo and replace `DOI: pending archive` with the assigned DOI.

## Manuscript Code Availability Wording

The source code is available at `https://github.com/Shark04467/probeplanner` under the MIT license. The manuscript release is intended to be archived as `v0.1.0-frontiers`; the Zenodo DOI should be inserted after the GitHub release is archived. Large MRI/CCF artifacts are not stored in git and must be provided through the documented artifact package layout.
