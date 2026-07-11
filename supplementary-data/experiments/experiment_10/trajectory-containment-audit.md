# Real-Atlas Trajectory Containment and Endpoint Audit

Each selected trajectory is sampled at 25 um, including its physical tip. The brain mask is the CCF annotation support (`annotation > 0`).

| case | probe | tip inside brain | post-entry inside ratio | max outside gap mm | tip region | tip in target | overshoot mm | strict pass |
|---|---:|---|---:|---:|---|---|---:|---|
| case_01_single_vpm | 1 | True | 1.000000 | 0.000000 | VPM | True | 0.0 | True |
| case_02_merged_hippocampal | 1 | True | 1.000000 | 0.000000 | CA3 | True | 0.0 | True |
| case_03_distributed_multitarget | 1 | True | 1.000000 | 0.000000 | VPM | True | 0.0 | True |
| case_03_distributed_multitarget | 2 | True | 1.000000 | 0.000000 | MOs6b | True | 0.0 | True |

## Acceptance Contract

`tipInside=true`, `insideRatioAfterEntry=1.0`, `outsideSampleCount=0`, `maxOutsideGapMm=0`, `tipInRequestedTarget=true`, and `overshootAfterLastTargetMm=0` within sampling precision.

## Summary

- Selected probes audited: 4
- Strict passes: 4
- Strict failures: 0
