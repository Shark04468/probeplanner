"""Budgeted compatible set cover on an explicit finite candidate pool.

Geometry and individual admissibility belong to the caller. A complete search
certifies cardinality in this pool only; a budget exit never proves infeasibility.
"""
import math
import time


class _BudgetReached(Exception):
    pass


def bounded_token_cover(token_sets, required, conflicts, incumbent=(), order_keys=None,
                        time_limit_s=5.0, node_limit=100000, cancel=None):
    """Return candidate indices and auditable search status, preserving a valid incumbent."""
    if not math.isfinite(time_limit_s) or time_limit_s < 0 or node_limit < 0:
        raise ValueError("Search budgets must be finite and nonnegative")
    started = time.perf_counter()
    deadline = started + time_limit_s
    tokens = sorted(set(required))
    bits = {token: 1 << i for i, token in enumerate(tokens)}
    masks = [sum(bits[t] for t in set(values) if t in bits) for values in token_sets]
    full = (1 << len(tokens)) - 1
    order_keys = list(order_keys) if order_keys is not None else list(range(len(masks)))
    if len(order_keys) != len(masks):
        raise ValueError("One deterministic ordering key is required per candidate")
    incumbent = list(incumbent)
    if len(set(incumbent)) != len(incumbent) or any(i < 0 or i >= len(masks) for i in incumbent):
        raise ValueError("Incumbent must consist of distinct pool indices")
    pair_cache = {}
    blocked = [0] * len(masks)
    nodes = 0
    reason = None
    lower = 0

    def check_budget():
        nonlocal reason
        if cancel is not None:
            cancel()
        if time.perf_counter() >= deadline:
            reason = "time_limit"
            raise _BudgetReached
        if nodes >= node_limit:
            reason = "node_limit"
            raise _BudgetReached

    def incompatible(a, b):
        pair = (min(a, b), max(a, b))
        if pair not in pair_cache:
            bad = bool(conflicts(*pair))
            pair_cache[pair] = bad
            if bad:
                blocked[a] |= 1 << b
                blocked[b] |= 1 << a
        return pair_cache[pair]

    # Validate before preservation: an invalid caller incumbent cannot be a fallback.
    for k, a in enumerate(incumbent):
        if any(incompatible(a, b) for b in incumbent[:k]):
            raise ValueError("Incumbent violates pair compatibility")
    incumbent_mask = 0
    for i in incumbent:
        incumbent_mask |= masks[i]
    best = incumbent
    best_full = incumbent_mask == full
    support = [0] * len(tokens)
    available = 0
    for i, mask in enumerate(masks):
        if mask:
            available |= 1 << i
        for t in range(len(tokens)):
            if mask & (1 << t):
                support[t] |= 1 << i

    def indices(value):
        while value:
            bit = value & -value
            yield bit.bit_length() - 1
            value ^= bit

    def search(chosen, covered, remaining, limit):
        nonlocal nodes
        check_budget()
        nodes += 1
        if covered == full:
            return list(chosen)
        slots = limit - len(chosen)
        if slots <= 0:
            return None
        for i in chosen:
            remaining &= ~blocked[i]
        missing = full & ~covered
        active = []
        union = 0
        max_gain = 0
        for i in indices(remaining):
            gain = masks[i] & missing
            if gain:
                active.append(i)
                union |= gain
                max_gain = max(max_gain, gain.bit_count())
        if union != missing or missing.bit_count() > slots * max_gain:
            return None
        branch = min(indices(missing), key=lambda t: ((support[t] & remaining).bit_count(), t))
        options = sorted(indices(support[branch] & remaining),
                         key=lambda i: (-(masks[i] & missing).bit_count(), order_keys[i], i))
        for i in options:
            check_budget()
            # Excluding earlier options partitions the cover search without losing solutions.
            remaining &= ~(1 << i)
            if any(incompatible(i, j) for j in chosen):
                continue
            found = search((*chosen, i), covered | masks[i], remaining & ~blocked[i], limit)
            if found is not None:
                return found
        return None

    status = "unknown"
    try:
        if not full:
            best, best_full, status = [], True, "proven_optimal"
        elif any(not candidates for candidates in support):
            status = "infeasible"
        else:
            lower = math.ceil(len(tokens) / max(mask.bit_count() for mask in masks))
            upper = len(best) - 1 if best_full else len(tokens)
            for size in range(lower, upper + 1):
                found = search((), 0, available, size)
                if found is not None:
                    best, best_full, lower = found, True, size
                    status = "proven_optimal"
                    break
                lower = size + 1
            else:
                status = "proven_optimal" if best_full else "infeasible"
    except _BudgetReached:
        status = "feasible_budget_limit" if best_full else "unknown_budget_limit"
    return best, {
        "status": status, "complete": best_full, "selectedCount": len(best),
        "minimumCount": len(best) if status == "proven_optimal" else None,
        "provenLowerBound": lower if full else 0, "budgetReason": reason,
        "nodes": nodes, "pairChecks": len(pair_cache), "candidateCount": len(masks),
        "requiredTokenCount": len(tokens), "timeLimitS": time_limit_s, "nodeLimit": node_limit,
        "elapsedMs": (time.perf_counter() - started) * 1000,
        "domain": "supplied finite candidate pool; caller-enforced individual admissibility",
    }
