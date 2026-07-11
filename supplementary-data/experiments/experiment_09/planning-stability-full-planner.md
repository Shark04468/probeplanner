# Automatic Planning Result Stability - 2026-07-09

This experiment repeats automatic ProbePlanner selection for target-set cases to test whether the selected probe count, trajectory coordinates, coverage assignment, and runtime remain stable for the same planning input.

## Summary

- Target-set cases: 30
- Repeats per case: 10
- Total repeated selection runs: 300
- Candidate-pool signature consistency: 100.0% mean; all cases identical = True
- Selected probe count consistency: 100.0% mean; all cases identical = True
- Coverage assignment consistency: 100.0% mean; all cases identical = True
- Trajectory signature consistency: 100.0% mean; all cases identical = True
- Maximum entry-coordinate axis std: 0.000000000000 mm
- Maximum tip-coordinate axis std: 0.000000000000 mm
- Median runtime coefficient of variation: 0.195186 all repeats; 0.046233 after first-repeat cache warmup
- Full-coverage cases: 15/30

## Per-case Stability

| case | targets | mode | candidates | repeats | candidate signature consistency | probe-count consistency | entry max std mm | tip max std mm | coverage-assignment consistency | runtime mean ms | runtime CV | warm runtime CV | status |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stab_001_single_vpm | VPM | single_hemisphere | 1 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13295.728 | 0.250 | 0.124 | ok |
| stab_002_single_ca1 | CA1 | single_hemisphere | 0 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 1332.988 | 0.734 | 0.090 | fail_closed |
| stab_003_single_ca3 | CA3 | single_hemisphere | 0 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 1698.874 | 1.326 | 0.085 | fail_closed |
| stab_004_single_dg | DG | single_hemisphere | 0 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 2322.761 | 0.828 | 0.103 | fail_closed |
| stab_005_single_visp | VISp | single_hemisphere | 0 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 992.745 | 0.261 | 0.112 | fail_closed |
| stab_006_single_mos | MOs | single_hemisphere | 19 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 10698.075 | 0.086 | 0.047 | ok |
| stab_007_single_ssp | SSp | single_hemisphere | 0 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 4744.540 | 0.187 | 0.076 | fail_closed |
| stab_008_pair_ca1_ca3 | CA1, CA3 | single_hemisphere | 3 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13569.093 | 0.542 | 0.061 | ok |
| stab_009_pair_ca1_dg | CA1, DG | single_hemisphere | 4 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13070.534 | 0.306 | 0.038 | ok |
| stab_010_pair_ca1_vpm | CA1, VPM | single_hemisphere | 5 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13305.868 | 0.202 | 0.090 | ok |
| stab_011_pair_ca1_visp | CA1, VISp | single_hemisphere | 2 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 11485.703 | 0.187 | 0.045 | ok |
| stab_012_pair_vpm_visp | VPM, VISp | single_hemisphere | 3 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 12498.997 | 0.100 | 0.037 | ok |
| stab_013_pair_vpm_mos | VPM, MOs | single_hemisphere | 25 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 16016.300 | 0.081 | 0.037 | ok |
| stab_014_pair_visp_mos | VISp, MOs | single_hemisphere | 38 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 11639.663 | 0.088 | 0.059 | ok |
| stab_015_pair_ssp_mos | SSp, MOs | single_hemisphere | 49 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 16536.330 | 0.188 | 0.045 | ok |
| stab_016_pair_dg_vpm | DG, VPM | single_hemisphere | 12 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13267.846 | 0.242 | 0.049 | ok |
| stab_017_pair_ca3_visp | CA3, VISp | single_hemisphere | 3 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 12181.056 | 0.248 | 0.066 | ok |
| stab_018_triple_hippocampal | CA1, CA3, DG | single_hemisphere | 9 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 16127.561 | 0.436 | 0.046 | ok |
| stab_019_triple_ca1_vpm_visp | CA1, VPM, VISp | single_hemisphere | 5 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 13935.588 | 0.248 | 0.033 | ok |
| stab_020_triple_ca1_vpm_mos | CA1, VPM, MOs | single_hemisphere | 27 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 17707.426 | 0.310 | 0.041 | ok |
| stab_021_triple_vpm_visp_mos | VPM, VISp, MOs | single_hemisphere | 21 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 15437.953 | 0.064 | 0.068 | ok |
| stab_022_triple_ca1_visp_ssp | CA1, VISp, SSp | single_hemisphere | 4 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 15103.610 | 0.110 | 0.036 | ok |
| stab_023_triple_dg_vpm_ssp | DG, VPM, SSp | single_hemisphere | 21 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 18393.180 | 0.341 | 0.038 | ok |
| stab_024_triple_ca3_dg_vpm | CA3, DG, VPM | single_hemisphere | 16 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 14562.469 | 0.370 | 0.069 | ok |
| stab_025_triple_ca1_mos_ssp | CA1, MOs, SSp | single_hemisphere | 29 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 18684.723 | 0.145 | 0.043 | ok |
| stab_026_quad_ca1_vpm_visp_mos | CA1, VPM, VISp, MOs | single_hemisphere | 10 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 15963.307 | 0.082 | 0.029 | ok |
| stab_027_quad_ca1_vpm_mos_ssp | CA1, VPM, MOs, SSp | single_hemisphere | 41 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 21731.246 | 0.146 | 0.047 | ok |
| stab_028_quad_hippo_vpm | CA1, CA3, DG, VPM | single_hemisphere | 22 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 14211.775 | 0.134 | 0.039 | ok |
| stab_029_bilateral_ca1_vpm | CA1, VPM | bilateral_symmetric | 5 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 14410.683 | 0.060 | 0.031 | ok |
| stab_030_bilateral_distributed | CA1, VPM, VISp, MOs | bilateral_symmetric | 10 | 10 | 100.0% | 100.0% | 0.000000000000 | 0.000000000000 | 100.0% | 17965.302 | 0.033 | 0.034 | ok |

## Interpretation

In this backend configuration, candidate generation and automatic selection are deterministic and no random candidate sampling seed is used. The default run repeats the full planning path for each target set: candidate generation, automatic selection, and selected-probe risk annotation. A consistency value of 100% means all repeats returned the same candidate-pool signature, selected probe count, selected trajectory signature, and target-coverage assignment.

## Metric Definitions

- `candidate signature consistency`: fraction of repeats matching the modal SHA-256 signature of the generated candidate pool.
- `selected probe count consistency`: fraction of repeats matching the modal selected probe count for the case.
- `entry/tip coordinate variance`: population variance and standard deviation of selected entry/tip planner-coordinate axes after canonical sorting of selected probes.
- `coverage assignment consistency`: fraction of repeats matching the modal per-probe covered-target assignment.
- `trajectory signature consistency`: fraction of repeats matching the modal selected trajectory coordinates, angle/depth, and coverage signature.
- `runtime CV`: standard deviation divided by mean wall-clock runtime for the selector plus selected-probe risk annotation; `warm runtime CV` excludes the first repeat, which includes risk-annotation cache warmup.
- `seed sensitivity`: not applicable for this deterministic configuration because random candidate sampling is not used.
