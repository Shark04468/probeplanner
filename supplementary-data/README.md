# ProbePlanner Supplementary Data

This directory contains the path-sanitized results, tables, and figures for experiments 1-10 and the final strict validation runs associated with release `v0.2.0-paper`.

## Contents

- `experiments/experiment_01` to `experiment_10`: atlas provenance, registration QC, coordinate round trip, perturbation sensitivity, automatic probe-count planning, digital phantom, baselines, ablations, repeated-run stability, and real-atlas case studies.
- `validation/final-benchmark`: locked 32-case strict benchmark and single-target diagnostics.
- `validation/baseline-ablation`: baselines and six objective/constraint ablations on the full-coverage benchmark.
- `validation/registration-stability`: registration-perturbation spatial-stability analysis and lambda sweep.
- `RUN_CONTRACT.md`: experiment execution contract.
- `manifest.json` and `SHA256SUMS`: provenance and integrity records.

Absolute workstation paths were replaced with environment placeholders. Raw Allen CCF and MRI volumes are not redistributed; obtain them from their cited sources and set the roots documented in `docs/reproducibility.md`.

## Claim Boundary

These files support computational reproducibility and atlas-defined trajectory audits. They do not constitute prospective surgical or histological validation. Internal vessel, ventricle, sulcus, or user-exclusion safety claims are unavailable where the corresponding masks were absent.
