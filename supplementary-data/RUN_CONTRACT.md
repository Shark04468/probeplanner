# Full Rerun Contract: Experiments 1-10

## Purpose

Regenerate every paper experiment after the target-safe planning remediation. Legacy evidence remains preserved but is not merged with this run.

## Runtime and Atlas Contract

- Python: `${LOCAL_DATA_ROOT}/python.exe`
- AllenSDK: `2.16.2`
- Planning CCF profile: `${PROBEPLANNER_PROFILE_ROOT}`
- Registration transform candidate for Experiments 2-3: `${PROBEPLANNER_REGISTRATION_ROOT}\ants_native_safe_062_cached_syn_grad070_long_s090_axis120_010mm`
- Annotation brain mask: CCF annotation support (`annotation > 0`)
- Structure tree: AllenSDK cache `structures.json`

## Planning Contract

All atlas planning experiments use the production optimization pipeline with:

- `lambdaBoundary=enforce`
- `brainBounds=enforce`
- `continuousRiskDistance=enforce`
- `targetEndpointPolicy=tip_in_target`
- `autoAvoidEnabled=true`
- explicit fail-closed reporting for an empty safe candidate pool
- 25 um physical path sampling, including the exact tip

## Experiment Scope

| experiment | rerun action |
|---|---|
| 1 AllenSDK atlas resources | full re-audit with AllenSDK 2.16.2 |
| 2 MRI-to-CCF QC | use one ANTs candidate for MRI, masks, Jacobian, and transforms; use the planning CCF profile only for the fixed annotation grid |
| 3 coordinate round trip | re-audit the same ANTs transform chain used by Experiment 2 |
| 4 registration perturbation sensitivity | replan every perturbation under the planning contract |
| 5 automatic probe-count benchmark | replan all benchmark cases under the planning contract |
| 6 known-optimal phantom | rerun deterministic TypeScript phantom benchmark |
| 7 baseline comparison | compare methods using the same strictly screened candidate pool |
| 8 objective/constraint ablation | retain ablation semantics but annotate non-enforced rows as unsafe controls |
| 9 planning stability | rerun full-planner repeats with the strict production path |
| 10 real-atlas case study | rerun, independently audit, and inspect figures |

## Acceptance Rule

No selected atlas trajectory may be reported as a positive result unless it satisfies the target-safe containment audit. A case with no safe candidate is `fail_closed`, not a zero-coverage success.

For registration, mask overlap and Jacobian QC do not substitute for anatomical TRE. A candidate with automatic control points but no manually curated anatomical correspondences is reported as `INCOMPLETE_ANATOMICAL_TRE`, not as a full registration pass.
