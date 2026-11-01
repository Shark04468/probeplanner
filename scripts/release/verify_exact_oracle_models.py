#!/usr/bin/env python3
"""Independently re-solve the archived v5 and historical exact MILP models."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix


COHORTS = (
    {
        "name": "v5",
        "results": "paper_final_validation/07_v021_revision/v021_v5_exact_oracle_results.json",
        "models": "paper_final_validation/07_v021_revision/v021_v5_exact_models",
        "expectedCases": 19,
    },
    {
        "name": "historical",
        "results": "paper_final_validation/07_v021_revision/final_source_exact_oracle_results.json",
        "models": "paper_final_validation/07_v021_revision/final_source_exact_models",
        "expectedCases": 48,
    },
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def solve_model(model: dict, time_limit_sec: float) -> tuple[str, int | None, float, str]:
    candidates = model["candidates"]
    required_tokens = [str(value) for value in model["requiredTokens"]]
    conflicts = [(int(left), int(right)) for left, right in model.get("conflicts", [])]
    n_candidates = len(candidates)
    if n_candidates != int(model["candidateCount"]):
        return "invalid_model", None, 0.0, "candidate count mismatch"

    token_rows = []
    for token in required_tokens:
        indices = [
            index
            for index, candidate in enumerate(candidates)
            if token in {str(value) for value in candidate.get("coverageTokens", [])}
        ]
        if not indices:
            return "infeasible", None, 0.0, f"required token has no candidate: {token}"
        token_rows.append(indices)

    matrix = lil_matrix((len(token_rows) + len(conflicts), n_candidates), dtype=np.float64)
    lower = np.full(matrix.shape[0], -np.inf, dtype=np.float64)
    upper = np.full(matrix.shape[0], np.inf, dtype=np.float64)
    for row, indices in enumerate(token_rows):
        matrix[row, indices] = 1.0
        lower[row] = 1.0
    for offset, (left, right) in enumerate(conflicts, start=len(token_rows)):
        matrix[offset, [left, right]] = 1.0
        upper[offset] = 1.0

    started = time.perf_counter()
    result = milp(
        c=np.ones(n_candidates, dtype=np.float64),
        integrality=np.ones(n_candidates, dtype=np.int8),
        bounds=Bounds(np.zeros(n_candidates), np.ones(n_candidates)),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"time_limit": float(time_limit_sec), "mip_rel_gap": 0.0},
    )
    runtime = time.perf_counter() - started
    if int(result.status) == 0 and result.fun is not None:
        return "proven_optimal", int(round(float(result.fun))), runtime, str(result.message)
    if int(result.status) == 1:
        return "timeout", None, runtime, str(result.message)
    if int(result.status) == 2:
        return "infeasible", None, runtime, str(result.message)
    return "error", None, runtime, str(result.message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-limit-sec", type=float, default=120.0)
    args = parser.parse_args()

    started = time.perf_counter()
    rows = []
    with zipfile.ZipFile(args.evidence_zip) as archive:
        for cohort in COHORTS:
            results = json.loads(archive.read(cohort["results"]))
            cases = results.get("cases", [])
            if len(cases) != cohort["expectedCases"]:
                raise RuntimeError(
                    f"{cohort['name']} case count changed: {len(cases)} != {cohort['expectedCases']}"
                )
            for case in cases:
                case_id = str(case["caseId"])
                member = f"{cohort['models']}/{case_id}.exact_model.json.gz"
                model_bytes = archive.read(member)
                model_payload = gzip.decompress(model_bytes)
                if sha256(model_payload) != str(case["modelSha256"]):
                    raise RuntimeError(f"Archived model hash mismatch: {case_id}")
                model = json.loads(model_payload)
                status, optimum, runtime, message = solve_model(model, args.time_limit_sec)
                expected = int(case["exactOracleMinimumCount"])
                planner = int(case["probePlannerSelectedCount"])
                rows.append(
                    {
                        "cohort": cohort["name"],
                        "caseId": case_id,
                        "modelContainerSha256": sha256(model_bytes),
                        "modelPayloadSha256": sha256(model_payload),
                        "solverStatus": status,
                        "expectedMinimum": expected,
                        "reverifiedMinimum": optimum,
                        "plannerCount": planner,
                        "minimumMatchesFrozenResult": optimum == expected,
                        "plannerMatchesReverifiedMinimum": optimum == planner,
                        "runtimeSec": round(runtime, 6),
                        "solverMessage": message,
                    }
                )

    failures = [
        row
        for row in rows
        if row["solverStatus"] != "proven_optimal"
        or not row["minimumMatchesFrozenResult"]
        or not row["plannerMatchesReverifiedMinimum"]
    ]
    payload = {
        "schemaVersion": "probeplanner.paper_exact_oracle_reverification.v1",
        "claimScope": "minimum cardinality within each archived regenerated strictly filtered candidate pool",
        "solver": "SciPy milp / HiGHS binary MILP",
        "evidenceArchiveSha256": sha256(args.evidence_zip.read_bytes()),
        "summary": {
            "totalCaseCount": len(rows),
            "v5CaseCount": sum(row["cohort"] == "v5" for row in rows),
            "historicalCaseCount": sum(row["cohort"] == "historical" for row in rows),
            "solverStatusCounts": dict(Counter(row["solverStatus"] for row in rows)),
            "minimumMatchCount": sum(row["minimumMatchesFrozenResult"] for row in rows),
            "plannerExactMatchCount": sum(row["plannerMatchesReverifiedMinimum"] for row in rows),
            "timeoutCount": sum(row["solverStatus"] == "timeout" for row in rows),
            "failureCount": len(failures),
            "runtimeSec": round(time.perf_counter() - started, 6),
            "acceptanceGate": "PASS" if not failures and len(rows) == 67 else "FAIL",
        },
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(payload["summary"], indent=2))
    return 0 if payload["summary"]["acceptanceGate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
