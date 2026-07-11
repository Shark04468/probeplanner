# Strict Baseline and Ablation Plan

## Claim Under Test

ProbePlanner selects a compact, full-coverage, hard-constraint-valid probe set, and its count, coverage, endpoint, depth, and risk behaviour are attributable to explicit objective and constraint components.

## Benchmark Population

- Use all 27 feasible cases from the locked strict final benchmark manifest.
- Preserve each case's targets, targeting mode, atlas profile, geometry contract, and strict production request.
- Exclude the five deliberately infeasible controls from aggregate method comparisons.

## Baselines

- one-target-one-probe
- greedy grouping
- random feasible selection with deterministic seeds
- coverage-first greedy
- ProbePlanner production selection

## Ablations

- no probe-count penalty
- no coverage priority
- no multi-target grouping
- no depth optimization
- no tip-in-target policy
- no internal-risk enforcement

## Metrics And Gates

- probe count
- full-coverage case rate
- uncovered coverage units and targets
- hard-violation rate under independent audit
- minimum internal-risk margin when an internal layer is available
- candidate-generation, selector, and total runtime
- Missing internal layers must be reported as unavailable and excluded from safety claims.
- Every run is checkpointed; failed or partial methods remain in the final result.

## Revision Log

- 2026-07-11: Campaign initialized from the locked 32-case manifest and restricted to its 27 feasible cases.
