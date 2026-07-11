# Objective and Constraint Ablation - 2026-07-09

This ablation tests whether automatic probe-count and trajectory choices are explained by explicit objective and constraint terms. All selector-level ablations reuse the same target-set cases, coverage-token definitions, inter-probe constraints, and accepted candidate pool as the full planner. The hard-boundary ablation is reported as a candidate-generation diagnostic because rejected out-of-bounds candidates are not exposed in the accepted candidate artifact.

## Summary

- Cases run: 6
- Strictly feasible target-set cases: 2
- Unsupported or fail-closed cases retained outside ablation aggregates: 4
- Full ProbePlanner exact-min matches on feasible cases: 2/2

## Ablation Summary

| ablation | removed component | total probes | mean coverage | full-coverage cases | uncovered units | worst risk margin mm | constraint violation | boundary violation |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full ProbePlanner objective | none | 2.00 | 100.0% | 2/2 | 0.00 | 0.076 | 0.0% | NA |
| no probe-count penalty | probe-count penalty | 4.00 | 100.0% | 2/2 | 0.00 | -0.045 | 0.0% | NA |
| no risk penalty | risk penalty | 2.00 | 100.0% | 2/2 | 0.00 | 0.076 | 0.0% | NA |
| no coverage priority | coverage priority | 2.00 | 100.0% | 2/2 | 0.00 | 0.087 | 0.0% | NA |
| no hard boundary exclusion | hard boundary exclusion | 2.00 | 100.0% | 2/2 | 0.00 | 0.076 | 0.0% | NA |
| no multi-target grouping | multi-target grouping | 4.00 | 100.0% | 2/2 | 0.00 | 0.014 | 16.7% | NA |

## Per-case Results

| case | ablation | candidates | probes | coverage | uncovered | risk margin mm | constraint violation | boundary violation | status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| case_01_single_vpm | full ProbePlanner objective | 6 | 1 | 100.0% | 0 | 0.076 | 0.0% | NA | ok |
| case_01_single_vpm | no probe-count penalty | 6 | 2 | 100.0% | 0 | -0.045 | 0.0% | NA | ok |
| case_01_single_vpm | no risk penalty | 6 | 1 | 100.0% | 0 | 0.076 | 0.0% | NA | ok |
| case_01_single_vpm | no coverage priority | 6 | 1 | 100.0% | 0 | 0.087 | 0.0% | NA | ok |
| case_01_single_vpm | no hard boundary exclusion | 6 | 1 | 100.0% | 0 | 0.076 | 0.0% | NA | ok |
| case_01_single_vpm | no multi-target grouping | 6 | 1 | 100.0% | 0 | 0.076 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | full ProbePlanner objective | 13 | 1 | 100.0% | 0 | 0.164 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | no probe-count penalty | 13 | 2 | 100.0% | 0 | 0.014 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | no risk penalty | 13 | 1 | 100.0% | 0 | 0.164 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | no coverage priority | 13 | 1 | 100.0% | 0 | 0.164 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | no hard boundary exclusion | 13 | 1 | 100.0% | 0 | 0.164 | 0.0% | NA | ok |
| case_02_nearby_hippocampal | no multi-target grouping | 13 | 3 | 100.0% | 0 | 0.014 | 33.3% | NA | constraint_violation |
| case_03_separated_two_probe | full ProbePlanner objective | 21 | 2 | 75.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_03_separated_two_probe | no probe-count penalty | 21 | 4 | 75.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_03_separated_two_probe | no risk penalty | 21 | 2 | 75.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_03_separated_two_probe | no coverage priority | 21 | 2 | 75.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_03_separated_two_probe | no hard boundary exclusion | 21 | 2 | 75.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_03_separated_two_probe | no multi-target grouping | 21 | 3 | 75.0% | 1 | -0.075 | 33.3% | NA | partial_with_constraint_violation |
| case_04_distributed_three_probe | full ProbePlanner objective | 28 | 2 | 80.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_04_distributed_three_probe | no probe-count penalty | 28 | 5 | 80.0% | 1 | -0.138 | 0.0% | NA | partial |
| case_04_distributed_three_probe | no risk penalty | 28 | 2 | 80.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_04_distributed_three_probe | no coverage priority | 28 | 2 | 60.0% | 2 | -0.060 | 0.0% | NA | partial |
| case_04_distributed_three_probe | no hard boundary exclusion | 28 | 2 | 80.0% | 1 | -0.075 | 0.0% | NA | partial |
| case_04_distributed_three_probe | no multi-target grouping | 28 | 4 | 80.0% | 1 | -0.138 | 33.3% | NA | partial_with_constraint_violation |
| case_05_bilateral_distributed_four_probe | full ProbePlanner objective | 21 | 4 | 62.5% | 3 | -0.075 | 0.0% | NA | partial |
| case_05_bilateral_distributed_four_probe | no probe-count penalty | 21 | 4 | 50.0% | 4 | -0.075 | 0.0% | NA | partial |
| case_05_bilateral_distributed_four_probe | no risk penalty | 21 | 4 | 62.5% | 3 | -0.075 | 0.0% | NA | partial |
| case_05_bilateral_distributed_four_probe | no coverage priority | 21 | 4 | 50.0% | 4 | -0.075 | 0.0% | NA | partial |
| case_05_bilateral_distributed_four_probe | no hard boundary exclusion | 21 | 4 | 62.5% | 3 | -0.075 | 0.0% | NA | partial |
| case_05_bilateral_distributed_four_probe | no multi-target grouping | 21 | 5 | 62.5% | 3 | -0.075 | 10.0% | NA | partial_with_constraint_violation |
| case_06_depth_limited_fail_closed | full ProbePlanner objective | 0 | 0 | 0.0% | NA | NA | NA | NA | fail_closed: ContractError: No feasible candidate trajectories generated under current settings |

## Interpretation

Ablation aggregates include only requests with a full-coverage solution in the shared strict candidate pool. Unsupported or depth-limited requests remain in the per-case table and are not used to score an objective term. The hard-boundary row is diagnostic-only because the saved candidate pool does not expose rejected boundary-invalid trajectories.

## Metric Definitions

- `selected probe count`: number of selected trajectories.
- `coverage`: covered required coverage units divided by required coverage units; bilateral targets are target:left and target:right units.
- `risk margin`: worst class-specific risk margin among selected probes, computed as distance to risk layer minus class threshold.
- `constraint violation`: inter-probe collision-pair rate under the same minimum entry spacing and minimum clearance contract.
- `boundary violation`: not replayable from the accepted candidate pool in this artifact; reported as NA.
- `no hard boundary exclusion`: reported as a candidate-generation diagnostic. The accepted candidate pool does not expose rejected out-of-bounds candidates, so this row is not interpreted as a selector-only replay.
