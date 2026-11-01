# Manuscript Limitations

These limitations must be stated in the manuscript, not hidden in supplementary material only.

## Biological Validation

- No animal experiments were performed for this software-evidence package.
- No histological track reconstruction was performed.
- No measured in vivo insertion error, targeting error, or tissue deformation error is reported.
- Therefore, the manuscript must not claim validated biological insertion accuracy.

## Anatomical Risk Models

- Brain surface, skull and ventricle risk layers are exercised in the current artifact.
- Vessel and sulcus structured masks are incomplete in the current artifact.
- Therefore, the manuscript must not claim complete vascular or sulcal avoidance.

## Optimization Semantics

- The native engine supports the current bitset, exact-pool, heuristic, hybrid and CVaR-relax execution modes used in the replay evidence.
- It is not a full native semantic equivalent of every legacy Python MISOCP / hybrid / CVaR mode.
- Therefore, MISOCP-equivalence language should be avoided unless a separate solver-equivalence validation is added.

## Supported Target Set

- MOs and ACB are fail-closed boundary cases in the current evidence package.
- These targets should be described as boundary cases requiring target-specific candidate generation and entry/trajectory modeling improvements.
- The manuscript should distinguish supported, partial and boundary targets using `reports/paper-supported-target-set`.

## Evidence Scope

- The strongest supported claim is software-system performance, reproducibility, native-engine acceleration, fail-closed behavior and display-payload consistency.
- The evidence does not support claims of general-purpose all-target stereotaxic accuracy.
