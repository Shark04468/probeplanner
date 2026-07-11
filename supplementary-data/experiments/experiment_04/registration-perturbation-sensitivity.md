# Registration Perturbation Sensitivity - 2026-07-09

ProbePlanner was re-run after small affine perturbations were applied to the MRI-to-CCF registration query space. For each perturbation, target/risk lookups used the inverse perturbed transform and the backend candidate generator plus greedy set-cover solver selected probes from scratch.

## Registration baseline

- Registration artifact: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm`
- Dice: 0.936457
- HD95 mm: 0.406202
- Jacobian anomaly ratio: 0.000000
- Jacobian std: 0.094572
- Structure-tree descendant fallback: True (`${LOCAL_DATA_ROOT}/structures.json`)

## Target regions

| acronym | region ID | AP mm | DV mm | ML mm | voxels |
|---|---:|---:|---:|---:|---:|
| CA1 | 382 | -2.628000 | 2.695000 | -0.007000 | 653391 |
| VPM | 733 | -1.663000 | 3.707000 | -0.007000 | 105262 |
| MOs | 993 | 1.796000 | 1.664000 | -0.008000 | 838678 |

## Summary

- Perturbation cases: 46
- Planning failure rate: 0.000000 (0/46)
- Max selected probe count change: 1
- Coverage-changed cases: 0
- Cases with new uncovered targets: 0
- Max entry displacement: 2.726055 mm
- Max tip displacement: 3.755636 mm
- Worst risk-margin drop: -0.114716 mm
- Max risk-shortfall increase: 0.189716 mm

## Interpretation

The perturbation audit shows stable target coverage under the tested registration errors: no perturbation failed planning, changed the covered target set, or introduced a newly uncovered target. However, several small perturbations switched the selected trajectory to a different feasible solution, producing entry/tip coordinate jumps even when probe count and target coverage stayed unchanged. This should be reported as output-solution sensitivity rather than target-coverage instability; a future production planner can reduce this by adding an explicit trajectory-continuity or baseline-proximity tie-breaker.

## Perturbation-type summary

| perturbation type | cases | failures | coverage-changed cases | max entry displacement mm | max tip displacement mm | worst risk-margin change mm |
|---|---:|---:|---:|---:|---:|---:|
| translation | 18 | 0 | 0 | 2.726055 | 3.755636 | -0.114716 |
| rotation | 18 | 0 | 0 | 0.378000 | 0.399067 | 0.000000 |
| scaling | 4 | 0 | 0 | 0.053000 | 0.258035 | 0.000000 |
| combined | 6 | 0 | 0 | 2.244758 | 3.309062 | 0.000000 |

## Case table

| case | selected probe count change | entry displacement mm | tip displacement mm | coverage changed | new uncovered targets | risk margin change mm |
|---|---:|---:|---:|---|---|---:|
| translation_AP_-1_0.1mm | 0 | 0.100000 | 0.199152 | False | none | 0.000000 |
| translation_AP_+1_0.1mm | 0 | 0.100000 | 0.272485 | False | none | 0.000000 |
| translation_DV_-1_0.1mm | 0 | 0.000000 | 0.000000 | False | none | 0.035910 |
| translation_DV_+1_0.1mm | 0 | 0.000000 | 0.000000 | False | none | 0.015137 |
| translation_ML_-1_0.1mm | 0 | 0.100000 | 0.135019 | False | none | 0.000000 |
| translation_ML_+1_0.1mm | 0 | 0.100000 | 0.367308 | False | none | 0.000000 |
| translation_AP_-1_0.2mm | 0 | 0.200000 | 0.207440 | False | none | 0.000000 |
| translation_AP_+1_0.2mm | 0 | 0.435001 | 0.281365 | False | none | 0.000000 |
| translation_DV_-1_0.2mm | 0 | 0.235002 | 0.160183 | False | none | 0.000000 |
| translation_DV_+1_0.2mm | 0 | 0.000000 | 0.000000 | False | none | -0.015023 |
| translation_ML_-1_0.2mm | 0 | 0.309235 | 0.524617 | False | none | 0.000000 |
| translation_ML_+1_0.2mm | 0 | 0.200000 | 0.441558 | False | none | 0.000000 |
| translation_AP_-1_0.3mm | 0 | 0.300000 | 0.376192 | False | none | 0.000000 |
| translation_AP_+1_0.3mm | 0 | 0.300000 | 0.325006 | False | none | 0.000000 |
| translation_DV_-1_0.3mm | 0 | 2.410483 | 3.755636 | False | none | 0.015137 |
| translation_DV_+1_0.3mm | 0 | 0.000000 | 0.000000 | False | none | -0.114716 |
| translation_ML_-1_0.3mm | 0 | 0.381872 | 0.492244 | False | none | 0.000000 |
| translation_ML_+1_0.3mm | 0 | 2.726055 | 3.710519 | False | none | 0.034433 |
| rotation_AP_-1_1deg | 0 | 0.235000 | 0.162068 | False | none | 0.035910 |
| rotation_AP_+1_1deg | 0 | 0.018000 | 0.050491 | False | none | 0.000000 |
| rotation_DV_-1_1deg | 0 | 0.046000 | 0.056083 | False | none | 0.000000 |
| rotation_DV_+1_1deg | 0 | 0.046000 | 0.055993 | False | none | 0.000000 |
| rotation_ML_-1_1deg | 0 | 0.018000 | 0.030798 | False | none | 0.000000 |
| rotation_ML_+1_1deg | 0 | 0.018000 | 0.029483 | False | none | 0.035910 |
| rotation_AP_-1_2deg | 0 | 0.036000 | 0.057021 | False | none | 0.015137 |
| rotation_AP_+1_2deg | 0 | 0.036000 | 0.057021 | False | none | 0.015137 |
| rotation_DV_-1_2deg | 0 | 0.244264 | 0.361626 | False | none | 0.000000 |
| rotation_DV_+1_2deg | 0 | 0.092022 | 0.128414 | False | none | 0.000000 |
| rotation_ML_-1_2deg | 0 | 0.037000 | 0.271068 | False | none | 0.000000 |
| rotation_ML_+1_2deg | 0 | 0.035000 | 0.041176 | False | none | 0.000000 |
| rotation_AP_-1_3deg | 0 | 0.054000 | 0.067152 | False | none | 0.000000 |
| rotation_AP_+1_3deg | 0 | 0.054000 | 0.105898 | False | none | 0.000000 |
| rotation_DV_-1_3deg | 0 | 0.254961 | 0.399067 | False | none | 0.000000 |
| rotation_DV_+1_3deg | 0 | 0.138058 | 0.183947 | False | none | 0.000000 |
| rotation_ML_-1_3deg | 0 | 0.057000 | 0.315775 | False | none | 0.000000 |
| rotation_ML_+1_3deg | 0 | 0.378000 | 0.159112 | False | none | 0.000000 |
| scaling_-1_1pct | 0 | 0.026000 | 0.036063 | False | none | 0.015137 |
| scaling_+1_1pct | 0 | 0.026000 | 0.026000 | False | none | 0.015137 |
| scaling_-1_2pct | 0 | 0.053000 | 0.258035 | False | none | 0.000000 |
| scaling_+1_2pct | 0 | 0.053000 | 0.058596 | False | none | 0.000000 |
| combined_APMLrot_-1_0.1mm_1deg | 0 | 0.305084 | 0.257801 | False | none | 0.015137 |
| combined_APMLrot_+1_0.1mm_1deg | 0 | 0.152398 | 0.195899 | False | none | 0.000000 |
| combined_APMLrot_-1_0.2mm_2deg | 0 | 0.304041 | 0.085079 | False | none | 0.000000 |
| combined_APMLrot_+1_0.2mm_2deg | 0 | 0.304700 | 0.268435 | False | none | 0.000000 |
| combined_APMLrot_-1_0.3mm_3deg | -1 | 2.244758 | 3.309062 | False | none | 0.000000 |
| combined_APMLrot_+1_0.3mm_3deg | 0 | 0.457096 | 0.406344 | False | none | 0.000000 |

## Metric definitions

- `selected probe count change`: selected probes after perturbation minus baseline selected probes.
- `entry displacement` and `tip displacement`: nearest-neighbor Euclidean displacement of selected entry/tip coordinates relative to the baseline selected probes.
- `coverage changed`: whether the set of covered target acronyms changed relative to baseline.
- `new uncovered targets`: targets uncovered after perturbation that were not uncovered at baseline.
- `risk margin change`: change in the worst class-specific risk margin, computed as distance-to-risk-layer minus class threshold; negative values mean the perturbed plan moved closer to a risk layer.
- `planning failure rate`: fraction of perturbation cases that returned an error or zero selected probes.
