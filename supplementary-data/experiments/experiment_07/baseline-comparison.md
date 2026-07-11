# Baseline Comparison for Automatic Probe Planning - 2026-07-09

This experiment compares ProbePlanner against three simple automatic baselines on the same target-combination cases, candidate pools, coverage-token definitions, and inter-probe collision constraints used in the automatic probe-count benchmark.

## Summary

- Cases run: 6
- Strictly feasible target-set cases: 2
- Unsupported or fail-closed cases retained outside selector aggregates: 4
- ProbePlanner exact-min matches on feasible cases: 2/2
- ProbePlanner total probes on feasible cases: 2.00
- one-target-one-probe total probes on feasible cases: 4.00
- greedy nearest-target grouping total probes on feasible cases: 2.00
- random feasible mean total probes on feasible cases: 2.00

## Method Summary

| method | total probes | mean probes/case | mean coverage | full coverage rate | uncovered units | hard violation rate | constraint-valid rate | worst risk margin mm | median runtime ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ProbePlanner automatic planner | 2.00 | 1.00 | 100.0% | 100.0% | 0.00 | 0.0% | 100.0% | 0.060 | 33863.926 |
| one-target-one-probe | 4.00 | 2.00 | 100.0% | 100.0% | 0.00 | 50.0% | 50.0% | 0.060 | 340.838 |
| greedy nearest-target grouping | 2.00 | 1.00 | 100.0% | 100.0% | 0.00 | 0.0% | 100.0% | 0.075 | 0.243 |
| random feasible candidate selection | 2.00 | 1.00 | 100.0% | 100.0% | 0.00 | 0.0% | 100.0% | 0.076 | 21.463 |

## Per-case Results

| case | method | probes | coverage | uncovered units | hard violation rate | risk margin mm | runtime ms | status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| case_01_single_vpm | ProbePlanner automatic planner | 1 | 100.0% | 0 | 0.0% | 0.076 | 16189.074 | ok |
| case_01_single_vpm | one-target-one-probe | 1 | 100.0% | 0 | 0.0% | 0.076 | 0.215 | ok |
| case_01_single_vpm | greedy nearest-target grouping | 1 | 100.0% | 0 | 0.0% | 0.076 | 0.187 | ok |
| case_01_single_vpm | random feasible candidate selection | 1.00 | 100.0% | 0.00 | 0.0% | 0.076 | 0.192 | aggregate |
| case_02_nearby_hippocampal | ProbePlanner automatic planner | 1 | 100.0% | 0 | 0.0% | 0.060 | 51538.777 | ok |
| case_02_nearby_hippocampal | one-target-one-probe | 3 | 100.0% | 0 | 100.0% | 0.060 | 681.461 | constraint_violation |
| case_02_nearby_hippocampal | greedy nearest-target grouping | 1 | 100.0% | 0 | 0.0% | 0.075 | 0.299 | ok |
| case_02_nearby_hippocampal | random feasible candidate selection | 1.00 | 100.0% | 0.00 | 0.0% | 0.087 | 42.733 | aggregate |
| case_03_separated_two_probe | ProbePlanner automatic planner | 2 | 75.0% | 1 | 0.0% | -0.075 | 51896.667 | partial |
| case_03_separated_two_probe | one-target-one-probe | 3 | 75.0% | 1 | 33.3% | -0.075 | 501.397 | partial_with_constraint_violation |
| case_03_separated_two_probe | greedy nearest-target grouping | 2 | 75.0% | 1 | 0.0% | -0.075 | 1.986 | partial |
| case_03_separated_two_probe | random feasible candidate selection | 2.35 | 75.0% | 1.00 | 0.0% | -0.057 | 71.664 | aggregate |
| case_04_distributed_three_probe | ProbePlanner automatic planner | 2 | 80.0% | 1 | 0.0% | -0.075 | 74282.021 | partial |
| case_04_distributed_three_probe | one-target-one-probe | 4 | 80.0% | 1 | 50.0% | -0.138 | 1223.050 | partial_with_constraint_violation |
| case_04_distributed_three_probe | greedy nearest-target grouping | 2 | 80.0% | 1 | 0.0% | -0.075 | 1.840 | partial |
| case_04_distributed_three_probe | random feasible candidate selection | 2.60 | 80.0% | 1.00 | 0.0% | -0.060 | 84.816 | aggregate |
| case_05_bilateral_distributed_four_probe | ProbePlanner automatic planner | 3 | 50.0% | 4 | 0.0% | -0.075 | 23629.468 | partial |
| case_05_bilateral_distributed_four_probe | one-target-one-probe | 5 | 62.5% | 3 | 10.0% | -0.075 | 1.088 | partial_with_constraint_violation |
| case_05_bilateral_distributed_four_probe | greedy nearest-target grouping | 1 | 25.0% | 6 | 0.0% | 0.076 | 0.243 | partial |
| case_05_bilateral_distributed_four_probe | random feasible candidate selection | 3.80 | 60.0% | 3.20 | 0.0% | -0.075 | 4.007 | aggregate |
| case_06_depth_limited_fail_closed | ProbePlanner automatic planner | 0 | 0.0% | NA | NA | NA | NA | fail_closed: ContractError: No feasible candidate trajectories generated under current settings |

## Interpretation

Selector aggregates include only requests for which the shared strict candidate pool has a full-coverage solution. Unsupported or depth-limited requests are retained above as partial or fail-closed evidence and are not used to score any selector. On the strictly feasible subset, ProbePlanner is compared with the exact candidate-pool minimum and with the three baselines under identical candidate and hard-constraint contracts.

## Metric Definitions

- `probe count`: number of selected trajectories; random feasible reports the mean over deterministic seeds.
- `coverage`: covered required coverage units divided by required coverage units; bilateral targets are counted as target:left and target:right units.
- `uncovered units`: number of uncovered required coverage units.
- `risk margin`: worst class-specific risk margin among selected probes, computed as distance to risk layer minus class threshold.
- `hard violation rate`: pairwise inter-probe collision rate under the same minimum entry spacing and minimum clearance contract; single-candidate boundary/obstacle hard filters are already applied in candidate generation.
- `runtime`: selector plus selected-probe risk annotation time; candidate generation is shared across methods and reported in JSON.
