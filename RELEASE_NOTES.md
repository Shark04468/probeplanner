# Release notes

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

Known limitations:

- The manuscript locked MRI/CCF profile passes manifest/hash validation but fails high-precision Dice, HD95, and TRE thresholds.
- Large MRI/CCF artifacts are not stored in git.
- Prospective histology and probe-track validation are not included in this software release.
- Windows is the primary tested platform; Linux/macOS support is experimental for non-MRI artifact workflows.
