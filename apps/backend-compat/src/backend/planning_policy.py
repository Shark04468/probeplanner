"""Uniform, finite density retries without changing anatomical or safety limits."""
from copy import deepcopy
import time


EMPTY_POOL_CODES = frozenset({'candidate_generation_empty', 'candidate_enforced_filter_empty'})


def density_schedule(grid_spacing, angle_step, enabled=True):
    schedule = [(float(grid_spacing), int(angle_step))]
    if enabled:
        for _ in range(2):
            grid, angle = schedule[-1]
            nxt = (min(grid, max(300.0, grid / 2.0)), min(angle, max(5, angle // 2)))
            if nxt == schedule[-1]:
                break
            schedule.append(nxt)
    return schedule


def run_density_policy(request, grid_spacing, angle_step, enabled, run_once, cancel=None):
    """Retry only incomplete/empty-pool outcomes; keep every attempt and the best plan."""
    start = time.perf_counter()
    attempts = []
    best = None
    best_key = None
    best_round = None
    last_error = None
    for round_index, (grid, angle) in enumerate(density_schedule(grid_spacing, angle_step, enabled)):
        if cancel is not None:
            cancel()
        body = deepcopy(request)
        body.setdefault('params', {}).update(gridSpacing=grid, angleStep=angle)
        record = {'round': round_index, 'gridSpacingUm': grid, 'angleStepDeg': angle,
                  'effectiveRequest': deepcopy(body)}
        try:
            result = run_once(body)
        except Exception as error:
            if getattr(error, 'code', None) not in EMPTY_POOL_CODES:
                raise
            last_error = error
            record.update(status='empty_strict_pool', code=error.code,
                          details=deepcopy(getattr(error, 'details', [])),
                          diagnostics=deepcopy(getattr(error, 'extra', {})))
            attempts.append(record)
            continue
        missing = len(result.get('uncoveredSides') or result.get('uncovered') or [])
        full = missing == 0 and result.get('coveredCoverageUnits', 0) >= result.get('requiredCoverageUnits', 1)
        record.update(status='full' if full else 'partial', candidateCount=result.get('candidateCount'),
                      selected=deepcopy(result.get('selected', [])), uncovered=deepcopy(result.get('uncovered', [])),
                      uncoveredSides=deepcopy(result.get('uncoveredSides', [])),
                      requiredCoverageUnits=result.get('requiredCoverageUnits'),
                      coveredCoverageUnits=result.get('coveredCoverageUnits'),
                      tokenCoverSearch=deepcopy(result.get('tokenCoverSearch')))
        if result.get('candidatePreview') is not None:
            record['candidatePreview'] = deepcopy(result['candidatePreview'])
        attempts.append(record)
        key = (int(full), int(result.get('coveredCoverageUnits', 0)), -len(result.get('selected', [])))
        if best_key is None or key > best_key:
            best, best_key, best_round = result, key, round_index
        if full:
            break
    audit = {'policy': 'bounded_density_v1', 'enabled': bool(enabled), 'maximumAdditionalRounds': 2,
             'minimumGridSpacingUm': 300.0, 'minimumAngleStepDeg': 5,
             'chosenRound': best_round, 'attempts': attempts,
             'stopReason': 'full_coverage' if best_key and best_key[0] else 'schedule_exhausted',
             'poolScope': 'chosen round only; no union-pool optimum claim',
             'elapsedMs': (time.perf_counter() - start) * 1000}
    if best is None:
        last_error.extra = dict(getattr(last_error, 'extra', {}) or {})
        last_error.extra['candidateExpansion'] = audit
        raise last_error
    best['candidateExpansion'] = audit
    best['timeMs'] = round(audit['elapsedMs'], 3)
    return best
