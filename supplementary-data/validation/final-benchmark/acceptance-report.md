# Strict Final Benchmark Acceptance Report

- Manifest gate: PASS
- Production target sets searched: 49
- Independently audited feasible cases: 46
- Proposed manifest size: 32

## Single-Target Diagnostics

| target | raw | brain mask pass | tip-in-target pass | risk-enforced pass | selected | uncovered | status | fail reason |
|---|---:|---:|---:|---:|---:|---|---|---|
| CA1 | 1596 | 1275 | 15 | 15 | 1 | none | ok | none |
| CA3 | 1596 | 1297 | 25 | 25 | 1 | none | ok | none |
| DG | 1596 | 1276 | 37 | 37 | 1 | none | ok | none |
| VISp | 1206 | 970 | 18 | 18 | 1 | none | ok | none |
| SSp | 1986 | 1611 | 26 | 26 | 1 | none | ok | none |
| VPM | 1596 | 1231 | 12 | 12 | 1 | none | ok | none |
| MOs | 1950 | 1413 | 30 | 30 | 1 | none | ok | none |

## Probe-Count Search Coverage

| probes | required | available | gate |
|---:|---:|---:|---|
| 1 | 10 | 10 | PASS |
| 2 | 8 | 27 | PASS |
| 3 | 5 | 5 | PASS |
| 4 | 4 | 4 | PASS |

## Interpretation

The manifest is written only when all count buckets, independent trajectory audits, fail-closed controls, and the 30-50 case-size gate pass. Missing internal risk layers remain explicitly unavailable and are not included in the safety claim.
