# Coordinate Transform Round-Trip QC - 2026-07-09

- Status: PASS.
- Sample count: 1000 foreground voxels from the prepared MRI brain mask.
- Artifact root: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm`.
- Acceptance threshold: one native MRI voxel = 0.015000000 mm; thresholds are derived from image spacing, not hard-coded.

## Tested Chain

MRI voxel coordinate -> MRI physical coordinate (mm) -> CCF physical coordinate (mm, ANTs point transform) -> planner coordinate ([AP,DV,ML] um) -> trajectory output coordinate ([AP,DV,ML] um, f32 output simulation) -> CCF physical coordinate (mm) -> MRI physical coordinate (mm, ANTs inverse point transform) -> MRI continuous voxel coordinate.

## Results

| Check | Result | Detail |
| --- | ---: | --- |
| Median round-trip error | 0.000031771 mm | 0.002118081 vox |
| P95 round-trip error | 0.000376899 mm | 0.025126580 vox |
| Max round-trip error | 0.001555005 mm | 0.103667024 vox |
| Planner f32 quantization error | 0.000000815653 mm | CCF mm -> planner um -> output um -> CCF mm |
| Left-right flip check | PASS | +1 ML voxel must increase planner ML um and bregma ML mm |
| AP/ML/DV axis consistency | PASS | +AP index is posterior in bregma display, so displayed AP mm decreases; +DV and +ML increase. |
| Unit consistency | PASS | CCF spacing mm x 1000 equals planner spacing um |

## Axis Convention Checks

| Axis | Voxel axis index | Physical delta mm | Planner delta um | Bregma-display delta mm |
| --- | ---: | --- | --- | --- |
| AP | 0 | [0.025, 0.0, 0.0] | [25.0, 0.0, 0.0] | [-0.025, 0.0, 0.0] |
| DV | 1 | [0.0, 0.025, 0.0] | [0.0, 25.0, 0.0] | [0.0, 0.025, 0.0] |
| ML | 2 | [0.0, 0.0, 0.025] | [0.0, 0.0, 25.0] | [0.0, 0.0, 0.025] |

## Data Inputs

- Moving MRI brain mask: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm\prepared\moving_brain_mask_full.nii.gz`
- Fixed CCF template: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm\prepared\fixed_ccf_template_full_mm.nii.gz`
- ANTs affine: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm\registration\ambmc_to_ccf_0GenericAffine.mat`
- ANTs warp: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm\registration\ambmc_to_ccf_1Warp.nii.gz`
- ANTs inverse warp: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm\registration\ambmc_to_ccf_1InverseWarp.nii.gz`
- Sampled MRI points: `${LOCAL_DATA_ROOT}/sampled_mri_points_mm.csv`
- CCF points after forward transform: `${LOCAL_DATA_ROOT}/sampled_points_in_ccf_mm.csv`
- CCF points after planner/output unit loop: `${LOCAL_DATA_ROOT}/sampled_points_after_planner_loop_ccf_mm.csv`
- Back-projected MRI points: `${LOCAL_DATA_ROOT}/sampled_points_back_mri_mm.csv`
- ANTs point-transform log: `${LOCAL_DATA_ROOT}/ants_points_roundtrip.log`

## Interpretation

The sampled MRI brain voxels returned to their original MRI physical/voxel coordinates within the derived one-voxel tolerance after the MRI->CCF ANTs point transform, planner-unit conversion, f32 trajectory-output simulation, and inverse CCF->MRI point transform. The end-to-end residual is dominated by the ANTs forward/inverse point-transform consistency; the planner mm-to-um-to-f32-output loop contributes only sub-micrometre numerical error. The AP/DV/ML checks show that the internal planner frame uses [AP,DV,ML] in micrometres; bregma-display AP has the expected opposite sign because increasing AP voxel index moves posteriorly. No left-right flip was detected because increasing ML voxel index increases both planner ML and bregma ML.
