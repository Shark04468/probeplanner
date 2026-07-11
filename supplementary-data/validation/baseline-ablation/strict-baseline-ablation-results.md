# Strict Full-Benchmark Baseline and Ablation Results

- Full-coverage reference cases: 27
- Random seeds per case: 20
- Atlas profile: `${PROBEPLANNER_PROFILE_ROOT}`

## Baselines

| method | mean probes [95% CI] | full coverage | uncovered units | hard violation rate | internal margin mm | runtime s |
|---|---:|---:|---:|---:|---:|---:|
| one-target-one-probe | 2.889 [2.333333, 3.444444] | 100.0% | 0.000 | 4.81% | NA | 25.021 |
| greedy grouping | 2.296 [1.814815, 2.777778] | 100.0% | 0.000 | 0.00% | NA | 25.029 |
| random feasible selection | 2.337 [1.901852, 2.814815] | 100.0% | 0.000 | 0.15% | NA | 25.030 |
| coverage-first greedy | 2.111 [1.740741, 2.518519] | 100.0% | 0.000 | 0.00% | NA | 25.029 |
| ProbePlanner | 2.111 [1.703704, 2.518519] | 100.0% | 0.000 | 0.00% | NA | 25.021 |

## Ablations

| method | mean probes [95% CI] | full coverage | uncovered units | hard violation rate | internal margin mm | runtime s |
|---|---:|---:|---:|---:|---:|---:|
| full ProbePlanner | 2.111 [1.703704, 2.518519] | 100.0% | 0.000 | 0.00% | NA | 25.021 |
| no probe-count penalty | 4.630 [3.703704, 5.555556] | 100.0% | 0.000 | 1.32% | NA | 25.033 |
| no coverage priority | 2.111 [1.740741, 2.518519] | 66.7% | 12.000 | 0.37% | NA | 25.022 |
| no multi-target grouping | 2.889 [2.333333, 3.444444] | 100.0% | 0.000 | 4.81% | NA | 25.021 |
| no depth optimization | 0.000 [0.0, 0.0] | 0.0% | 78.000 | 0.00% | NA | 1.797 |
| no tip-in-target policy | 2.111 [1.740741, 2.518519] | 100.0% | 0.000 | 44.69% | NA | 48.798 |
| no internal-risk enforcement | 2.111 [1.703704, 2.518519] | 100.0% | 0.000 | 0.00% | NA | 16.571 |

## Results Interpretation

ProbePlanner used 0.778 fewer probes per case than one-target-one-probe, 0.185 fewer than greedy grouping, and 0.226 fewer than random feasible selection when the deltas are expressed as baseline minus ProbePlanner. Coverage-first greedy exactly matched ProbePlanner on this benchmark (0.000 probe-count delta and identical full coverage), so these data do not establish superiority over that baseline.

Removing the probe-count penalty increased the mean count to 4.630. Removing coverage priority reduced full-coverage performance to 66.7% and left 12 coverage units uncovered. With the fixed 7.6 mm request depth and tip-in-target still enforced, disabling depth optimization yielded 0.0% full coverage. Removing tip-in-target preserved intersection coverage but produced a 44.7% mean hard-violation rate; 20 of 27 cases contained at least one independently detected endpoint violation.

## Safety Interpretation

Hard violations are recomputed independently from brain containment, tip-in-target, distal overshoot, internal-risk diagnostics, and inter-probe collisions. The active profile has no usable internal vessel, ventricle, sulcus, or user-exclusion layer; internal-risk margins are therefore `NA`, and the no-internal-risk-enforcement result is not evidence that those constraints are unnecessary.
