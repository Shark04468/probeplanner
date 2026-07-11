# Digital Phantom Accuracy Validation

Generated: 2026-07-10T06:28:26.626Z

This analytic phantom uses a fixed ellipsoid brain, known spherical obstacles, known target centers, and a deterministic entry/angle/depth candidate grid. It validates planner geometry and constraint behavior; it is not animal or histology validation.

## Metrics

- Candidate count: 46565
- Targets: 16
- Coverage recall: 100.0%
- Target distance median: 0.11 mm
- Target distance p95: 0.29 mm
- Target distance max: 0.29 mm
- Selected boundary violation rate: 0.0%
- Selected obstacle violation rate: 0.0%
- Minimum boundary margin: 0.01 mm
- Minimum obstacle margin: 0.04 mm

## Targets

| Target | Covered | Target distance mm | Min boundary margin mm | Min obstacle margin mm | Theta | Phi | Depth mm |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T01_CA1_like | true | 0.11 | 0.08 | 0.57 | 15 | 180 | 4.25 |
| T02_CA3_like | true | 0.10 | 0.24 | 0.61 | 25 | 180 | 4.25 |
| T03_DG_like | true | 0.09 | 0.01 | 0.12 | 5 | 270 | 4.75 |
| T04_VPM_like | true | 0.12 | 0.11 | 0.28 | 5 | 315 | 4.75 |
| T05_LGd_like | true | 0.12 | 0.24 | 0.16 | 25 | 45 | 5.00 |
| T06_VISp_like | true | 0.29 | 0.06 | 0.04 | 30 | 45 | 3.25 |
| T07_VISrl_like | true | 0.11 | 0.06 | 1.25 | 25 | 315 | 3.50 |
| T08_LP_like | true | 0.12 | 0.01 | 0.19 | 5 | 135 | 5.25 |
| T09_TH_like | true | 0.08 | 0.01 | 0.74 | 5 | 315 | 5.50 |
| T10_RSP_like | true | 0.11 | 0.01 | 0.77 | 25 | 135 | 3.00 |
| T11_ACA_like | true | 0.04 | 0.24 | 1.58 | 30 | 270 | 2.75 |
| T12_CP_like | true | 0.11 | 0.31 | 0.32 | 30 | 0 | 5.50 |
| T13_MOs_boundary_like | true | 0.24 | 0.06 | 1.67 | 30 | 180 | 2.50 |
| T14_ACB_boundary_like | true | 0.13 | 0.14 | 0.10 | 5 | 90 | 6.50 |
| T15_deep_safe | true | 0.07 | 0.01 | 0.29 | 20 | 90 | 6.25 |
| T16_lateral_safe | true | 0.11 | 0.01 | 1.13 | 20 | 0 | 4.00 |
