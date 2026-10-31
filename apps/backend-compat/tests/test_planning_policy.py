from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from backend.planning_policy import density_schedule, run_density_policy


class EmptyPool(Exception):
    code = 'candidate_generation_empty'
    details = ['no candidates']
    extra = {}


def response(covered, count=1):
    return {'selected': [{'id': i} for i in range(count)], 'requiredCoverageUnits': 3,
            'coveredCoverageUnits': covered, 'uncovered': [] if covered == 3 else ['missing'],
            'candidateCount': 10, 'candidatePreview': [{'id': 0}]}


def test_schedule_is_bounded_and_never_coarsens_finer_inputs():
    assert density_schedule(1200, 10) == [(1200, 10), (600, 5), (300, 5)]
    assert density_schedule(200, 2) == [(200, 2)]
    assert density_schedule(5000, 30) == [(5000, 30), (2500, 15), (1250, 7)]
    assert density_schedule(1200, 10, False) == [(1200, 10)]


def test_retry_changes_only_sampling_and_stops_at_full():
    request = {'targetKeys': ['A'], 'params': {'maxAngle': 10, 'minClearance': 1000},
               'probeParams': {'depthUm': 7600, 'entrySpacingUm': 1200},
               'constraintModes': {'brainBounds': 'enforce'}}
    original = deepcopy(request)
    calls = []
    def once(body):
        calls.append(body)
        return response(2 if len(calls) == 1 else 3)
    result = run_density_policy(request, 1200, 10, True, once)
    assert request == original and len(calls) == 2
    for body in calls:
        body['params'].pop('gridSpacing')
        body['params'].pop('angleStep')
        assert body == original
    assert result['candidateExpansion']['chosenRound'] == 1
    assert [r['status'] for r in result['candidateExpansion']['attempts']] == ['partial', 'full']


def test_best_partial_is_preserved_with_its_pool():
    count = 0
    def once(body):
        nonlocal count
        count += 1
        r = response(2 if count == 1 else 1)
        r['candidatePreview'] = [{'round': count}]
        return r
    result = run_density_policy({}, 1200, 10, True, once)
    assert result['candidateExpansion']['chosenRound'] == 0
    assert len(result['candidateExpansion']['attempts']) == 3
    assert result['candidatePreview'] == [{'round': 1}]


def test_empty_pool_retry_can_recover():
    calls = []
    def once(body):
        calls.append(body)
        if len(calls) == 1:
            raise EmptyPool()
        return response(3)
    result = run_density_policy({}, 1200, 10, True, once)
    assert len(calls) == 2
    assert result['candidateExpansion']['attempts'][0]['status'] == 'empty_strict_pool'


def test_all_empty_pools_remain_error_with_history():
    def once(body):
        raise EmptyPool()
    with pytest.raises(EmptyPool) as caught:
        run_density_policy({}, 1200, 10, True, once)
    assert len(caught.value.extra['candidateExpansion']['attempts']) == 3


def test_validation_and_safety_errors_are_not_retried():
    calls = []
    def once(body):
        calls.append(body)
        raise ValueError('invalid request')
    with pytest.raises(ValueError):
        run_density_policy({}, 1200, 10, True, once)
    assert len(calls) == 1


def test_full_first_plan_is_not_replaced_by_finer_pool():
    calls = []
    def once(body):
        calls.append(body)
        return response(3)
    result = run_density_policy({}, 1200, 10, True, once)
    assert len(calls) == 1 and result['candidateExpansion']['chosenRound'] == 0


def test_cancellation_prevents_next_attempt():
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        run_density_policy({}, 1200, 10, True, lambda body: response(3), cancel)
