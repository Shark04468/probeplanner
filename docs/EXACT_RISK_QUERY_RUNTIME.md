# Exact risk-query runtime configuration

This post-hoc branch adds a deployment option, not a new anatomical model or
newly published paper release. Frozen historical outcomes remain unchanged.

The paper launcher accepts `--risk-query-workers 4` (integer1..8, default1).
It clears inherited ProbePlanner environment settings and explicitly passes
this option. Other launchers can set `PROBEPLANNER_RISK_QUERY_WORKERS` before
import. Invalid values fail at startup instead of enabling unlimited workers.

Effective query workers are capped by CPU count, affinity, detected cgroup CPU
quota and the startup runtime-thread budget. Exact k=1 Euclidean nearest-point
queries retain eps=0; no approximate search, threshold or mask changes occur.
Parallel risk queries use one shared slot per backend process, preventing
concurrent requests or outer worker pools from multiplying query workers.
This is a risk-query bound, not a bound on all process-wide compute threads.
Multiple server processes each have their own slot and must be provisioned
within the host's total CPU budget. Default1 preserves the legacy query call.

`/api/paper/runtime`, exported runtime identity and successful planning
`computeProfile.riskQueryPolicy` report the configuration. Runtime identity
also binds the policy-module SHA-256. A faster component measurement alone
does not establish end-to-end planning latency or anatomical safety.
