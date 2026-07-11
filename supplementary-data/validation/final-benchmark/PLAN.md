# Strict Final Benchmark Plan

## Claim Under Test

ProbePlanner uses one production planning path for single- and multi-target requests, emits internally consistent safety evidence, and selects an audited minimum-size probe set without fabricating unavailable 1/2/3/4-probe cases.

## Acceptance Contract

- Single-target diagnostics cover CA1, CA3, DG, VISp, SSp, VPM, and MOs with stage-specific candidate counts and explicit fail-closed reasons.
- Every selected probe emits `brainContainmentPassed`, `tipInTarget`, `distalOvershootMm`, `internalRiskViolationCount`, `minimumInternalRiskMarginMm`, and `entrySurfaceCrossingStatus`.
- A successful probe has continuous post-entry brain containment, a tip in a requested target, distal overshoot at or below 0.025 mm, and zero violations for every available enforced internal-risk layer.
- Skull, drilling surface, and brain surface are reported as entry surfaces, not internal avoidance structures.
- Empty or missing vessel, ventricle, sulcus, or user exclusion layers are reported as unavailable and are excluded from safety claims.
- Target-set cases are assigned to 1/2/3/4-probe buckets only from actual production output after independent audit.
- The final 30-50-case manifest is locked only if it contains at least 20 full-coverage feasible cases, audited 1/2/3/4-probe cases, and correct fail-closed cases.

## Campaign Slices

| slice | research question | output |
|---|---|---|
| S1 | Does strict single-target search use the same candidate families and masks as multi-target search? | single-target diagnostics |
| S2 | Are containment, endpoint, entry-surface, and internal-risk semantics consistent? | unified probe audit payload and tests |
| S3 | Which actual safe target sets populate the 1/2/3/4-probe buckets? | bounded production search ledger |
| S4 | Do independently recomputed audits confirm every proposed manifest case? | locked manifest or explicit gate failure |

## Assets And Comparability

- Production planner: `apps/backend-compat/src/optimizer_server.py`
- Allen CCF annotation and structure tree used by the current planner profile
- Existing strict request contract and current MRI/CCF profile artifacts
- Same candidate-generation, constraint, and optimization code path for all cases

## Execution Strategy

1. Add central audit construction and regression tests.
2. Smoke-test VPM and CA1, then run all seven single targets.
3. Enumerate a bounded, deterministic target-set registry with checkpointed results.
4. Assign only independently audited full-coverage outputs to probe-count buckets.
5. Write acceptance and coverage reports; lock the manifest only when every gate passes.

## Revision Log

- 2026-07-11: Initial strict benchmark charter created from the user-specified acceptance criteria.
