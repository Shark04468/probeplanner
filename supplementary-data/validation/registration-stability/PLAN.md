# Registration Perturbation Trajectory-Stability Plan

## Claim Under Test

A reference-trajectory penalty can reduce entry, tip, and angular jumps under small MRI-to-CCF registration perturbations without reducing target coverage or violating strict candidate constraints.

## Design

- Targets: CA1, VPM, LP, VISp, matching the prior perturbation experiment.
- Perturbations: 46 translation, rotation, scaling, and combined cases plus the identity baseline.
- Generate one strict feasible candidate pool per perturbation.
- Compare the original production ranking with reference-stabilized ranking on the identical candidate pool.
- Sweep lambda values and select the smallest value on the best stability frontier with no coverage or failure regression.

## Stability Distance

`D_reference = w_entry * entry_mm + w_tip * tip_mm + w_angle * angular_deg / 10`.

The final candidate score subtracts `lambda * D_reference` after coverage, risk, and angle scoring. Probe-count change remains a reported set-level stability metric.

## Metrics

- target coverage rate and coverage-changed cases
- planning failure rate
- probe-count changed cases
- maximum, p95, median, and mean entry displacement
- maximum, p95, median, and mean tip displacement
- maximum, p95, median, and mean angular change
- cases exceeding 1 mm entry or tip displacement

## Acceptance

- No coverage or planning-failure regression against the unstabilized control.
- Reduced maximum and p95 spatial displacement.
- Full reporting of any remaining trajectory switches.
