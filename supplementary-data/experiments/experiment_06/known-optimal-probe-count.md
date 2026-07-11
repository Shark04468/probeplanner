# Digital Phantom Known-Optimal Probe Count Validation

Generated: 2026-07-10T06:28:32.480Z

This experiment adds a digital phantom with a known optimal answer for automatic probe-count planning. Sixteen targets are placed into four separated spatial groups. Every valid candidate trajectory covers targets from at most one group, while four designed trajectories cover all four groups. Therefore the lower bound and feasible solution match: the ground-truth optimal probe count is exactly 4.

## Ground-truth proof

- Target count: 16
- Target groups: 4
- Targets per group: 4
- Candidate count: 52
- Valid covering candidates: 36
- Max target groups covered by any valid candidate: 1
- Group lower-bound probe count: 4
- Designed feasible solution probe count: 4
- Best coverage using at most 3 probes: 12/16
- Full coverage using at most 3 probes possible: false
- Full coverage using at most 5 probes possible: true
- Full coverage using exactly 5 probes possible: true

## Metrics

| Metric | Value |
| --- | ---: |
| optimal probe count recovery | 1 |
| ground-truth optimal probe count | 4 |
| selected probe count | 4 |
| target coverage recall | 100.0% |
| over-selection rate | 0.0% |
| under-selection rate | 0.0% |
| obstacle violation rate | 0.0% |
| boundary violation rate | 0.0% |
| min selected obstacle margin | 2.03 mm |
| min selected boundary margin | 0.39 mm |
| runtime | 26.4 ms |

## Selected probes

| Candidate | Family | Covered targets | Entry mm | Tip mm | Theta deg | Phi deg | Depth mm | Boundary margin mm | Obstacle margin mm |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| P_G1_left_posterior_designed | designed_optimal | 4 | (-2.40, 1.50, -1.30) | (-2.40, 6.90, -1.30) | 0.0 | 0.0 | 5.40 | 0.39 | 2.03 |
| P_G2_right_posterior_designed | designed_optimal | 4 | (2.40, 1.50, -1.30) | (2.40, 6.90, -1.30) | 0.0 | 0.0 | 5.40 | 0.39 | 2.03 |
| P_G3_left_anterior_designed | designed_optimal | 4 | (-2.40, 1.50, 1.30) | (-2.40, 6.90, 1.30) | 0.0 | 0.0 | 5.40 | 0.39 | 2.03 |
| P_G4_right_anterior_designed | designed_optimal | 4 | (2.40, 1.50, 1.30) | (2.40, 6.90, 1.30) | 0.0 | 0.0 | 5.40 | 0.39 | 2.03 |
