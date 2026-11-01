# Release notes

## v0.2.1-paper

This is the corrected final-source paper release after `v0.2.0-paper`. The old
tag and its evidence are unchanged.

- The frozen v4 first execution is preserved as a failed gate: 16 positive
  full, 3 partial, and 1 expected fail-closed case.
- The v4 diagnosis and repair are explicitly post-hoc. The repair corrected
  stale exact-acronym ontology resolution, incomplete token-cover completion,
  and missing bilateral mirror closure.
- A newly frozen v5 untouched confirmation produced 19/19 positive full and
  strict-pass cases, 1/1 expected negative fail-closed case, 0 partial cases,
  and 0 errors.
- The v5 exact oracle proved the candidate-pool minimum in 19/19 cases and
  matched ProbePlanner in 19/19, with 0 timeouts.
- The final-source historical exact regression proved 48/48 candidate-pool
  optima and matched ProbePlanner in 48/48, with 0 timeouts.
- The release gate requires exactly 112 backend and validation tests to pass.
- Frozen raw validation records are distributed as a byte-preserving release
  asset with per-member SHA-256 hashes. Historical machine paths inside that
  archive are provenance fields, not executable configuration.

Supported claims are limited to the locked cohort results, fail-closed
behavior, strict constraint auditing, and minimum probe count within each
regenerated strictly filtered candidate pool.

This release does not support claims of continuous-space global optimality,
subject-specific landmark TRE, bregma/lambda or skull localization accuracy,
individual-animal native-space surgical safety, vascular or ventricular safety
when masks are unavailable, or histological implantation accuracy.

## v0.2.0-paper

This is the locked manuscript release for the final strict ProbePlanner experiments.

- Unified brain-containment, tip-in-target, distal-overshoot, internal-risk, and entry-surface audit fields.
- Strict single-target diagnostics for CA1, CA3, DG, VISp, SSp, VPM, and MOs.
- A 32-case final benchmark containing independently audited 1-, 2-, 3-, and 4-probe solutions and fail-closed controls.
- Full baseline and six-condition ablation evaluation on 27 full-coverage cases.
- Registration-perturbation stability tie-breaker and lambda sweep.
- Path-sanitized experiments 1-10 Supplementary Data with SHA-256 checksums.
- Exact Node, Rust, and Python environment locks and an isolated clean-cache reproduction workflow.

Safety claims remain limited to available masks. Missing internal vessel, ventricle, sulcus, or user-exclusion evidence is reported as unavailable rather than passed.

## v0.1.0-frontiers

This release supports the Frontiers in Neuroinformatics Technology & Code manuscript draft for ProbePlanner.

Included:

- React planning interface and Unity WebGL host.
- Flask compatibility API.
- Rust native planning engine.
- MRI/CCF artifact validation scripts.
- Replay and performance gate tooling.
- Minimal native-engine example.
- Locked MRI/CCF profile resolution for native engine startup.
- MIT license, CITATION.cff, and Zenodo metadata.
- Replay110 stability, supported-target-set, digital phantom, and reproducibility-pack reports for the manuscript evidence package.

Known limitations:

- The promoted MRI/CCF profile used for the current evidence package passes the configured Dice, HD95, TRE, and Jacobian QC thresholds.
- Large MRI/CCF artifacts are not stored in git.
- Prospective histology and probe-track validation are not included in this software release.
- Vessel and sulcus structured masks remain incomplete in the current artifact.
- Native DRCC/MISOCP support should not be described as full semantic equivalence with every legacy Python solver mode.
- MOs and ACB remain fail-closed supported-target-set boundary cases.
- Windows is the primary tested platform; Linux/macOS support is experimental for non-MRI artifact workflows.
