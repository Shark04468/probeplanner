"""Independent exhaustive graph oracle and bounded-search contract tests."""
import itertools
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from backend.token_cover import bounded_token_cover


def exhaustive(sets, required, edges):
    for count in range(len(sets) + 1):
        for choice in itertools.combinations(range(len(sets)), count):
            if any((a, b) in edges for a, b in itertools.combinations(choice, 2)):
                continue
            if set().union(*(sets[i] for i in choice)) >= required:
                return count
    return None


def test_exhaustive_random_graphs():
    rng = random.Random(2026103104)
    for _ in range(400):
        n, k = rng.randint(1, 10), rng.randint(1, 5)
        required = set(range(k))
        sets = [{t for t in required if rng.random() < .4} for _ in range(n)]
        edges = {(a, b) for a, b in itertools.combinations(range(n), 2) if rng.random() < .35}
        expected = exhaustive(sets, required, edges)
        chosen, audit = bounded_token_cover(sets, required, lambda a, b: (a, b) in edges)
        assert audit['budgetReason'] is None
        assert audit['minimumCount'] == expected
        assert audit['status'] == ('infeasible' if expected is None else 'proven_optimal')
        if expected is not None:
            assert len(chosen) == expected
            assert not any(tuple(sorted(p)) in edges for p in itertools.combinations(chosen, 2))
            assert set().union(*(sets[i] for i in chosen)) == required


def test_complete_incumbent_is_compressed():
    sets = [{'a'}, {'b'}, {'c'}, {'a', 'b'}, {'c'}]
    chosen, audit = bounded_token_cover(sets, {'a', 'b', 'c'}, lambda a, b: False, [0, 1, 2])
    assert len(chosen) == audit['minimumCount'] == 2


def test_large_pool_and_more_than_twelve_tokens():
    sets = [{i % 14} for i in range(700)]
    chosen, audit = bounded_token_cover(sets, set(range(14)), lambda a, b: False)
    assert len(chosen) == 14
    assert audit['status'] == 'proven_optimal'


def test_time_budget_keeps_valid_complete_incumbent_without_proof():
    chosen, audit = bounded_token_cover([{'a'}, {'b'}, {'a', 'b'}], {'a', 'b'},
                                       lambda a, b: False, [0, 1], time_limit_s=0)
    assert chosen == [0, 1]
    assert audit['status'] == 'feasible_budget_limit'
    assert audit['minimumCount'] is None


def test_node_budget_is_unknown_not_infeasible():
    chosen, audit = bounded_token_cover([{'a'}, {'b'}], {'a', 'b'}, lambda a, b: False,
                                       [0], node_limit=0)
    assert chosen == [0]
    assert audit['status'] == 'unknown_budget_limit'
    assert audit['budgetReason'] == 'node_limit'


def test_incompatible_incumbent_is_rejected():
    with pytest.raises(ValueError, match='compatibility'):
        bounded_token_cover([{'a'}, {'b'}], {'a', 'b'}, lambda a, b: True, [0, 1])


def test_missing_token_and_empty_request():
    assert bounded_token_cover([{'a'}], {'a', 'b'}, lambda a, b: False)[1]['status'] == 'infeasible'
    chosen, audit = bounded_token_cover([{'a'}], set(), lambda a, b: False)
    assert chosen == [] and audit['minimumCount'] == 0


def test_deterministic_order_and_no_double_pair_queries():
    calls = []
    def conflict(a, b):
        assert (a, b) not in calls
        calls.append((a, b))
        return a == 0
    sets = [{'a'}, {'a'}, {'b'}, {'b'}]
    first, audit = bounded_token_cover(sets, {'a', 'b'}, conflict, order_keys=[2, 1, 0, 3])
    second, _ = bounded_token_cover(sets, {'a', 'b'}, lambda a, b: a == 0, order_keys=[2, 1, 0, 3])
    assert first == second
    assert audit['pairChecks'] == len(calls)


def test_cancellation_propagates():
    def cancelled():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        bounded_token_cover([{'a'}], {'a'}, lambda a, b: False, cancel=cancelled)


def test_budget_values_are_validated():
    for value in (-1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            bounded_token_cover([{'a'}], {'a'}, lambda a, b: False, time_limit_s=value)
