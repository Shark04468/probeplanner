# Release notes

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
