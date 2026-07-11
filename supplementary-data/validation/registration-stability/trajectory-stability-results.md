# Registration Perturbation Trajectory-Stability Results

- Perturbation cases: 46
- Selected stability weight: 0.1
- Control coverage-changed cases: 0
- Stabilized coverage-changed cases: 0
- Control strict-audit failures: 0
- Stabilized strict-audit failures: 0

## Spatial Stability

| metric | control | stabilized |
|---|---:|---:|
| max entry displacement mm | 4.79894 | 0.1 |
| p95 entry displacement mm | 4.660801 | 0.0 |
| median entry displacement mm | 0.160016 | 0.0 |
| max tip displacement mm | 3.61987 | 0.120345 |
| p95 tip displacement mm | 3.549381 | 0.045617 |
| median tip displacement mm | 0.298665 | 0.0 |
| max angular change deg | 17.299275 | 1.735232 |
| p95 angular change deg | 17.298272 | 0.000431 |
| probe-count changed cases | 1 | 0 |
| entry jumps >1 mm | 6 | 0 |
| tip jumps >1 mm | 6 | 0 |

## Lambda Sweep

| lambda | coverage changes | failures | max entry mm | p95 entry mm | max tip mm | p95 tip mm |
|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 0 | 0 | 0.468108 | 0.216509 | 0.413664 | 0.187706 |
| 0.02 | 0 | 0 | 0.468108 | 0.216509 | 0.413664 | 0.187706 |
| 0.05 | 0 | 0 | 0.468108 | 0.1 | 0.279883 | 0.089322 |
| 0.1 | 0 | 0 | 0.1 | 0.0 | 0.120345 | 0.045617 |
| 0.2 | 0 | 0 | 0.1 | 0.0 | 0.120345 | 0.045617 |

## Interpretation

Coverage and trajectory stability are reported separately. The reference term is applied only within the already strict feasible candidate pool; it cannot make an unsafe candidate feasible. Remaining probe-count changes and spatial jumps are retained as sensitivity evidence rather than hidden.
