"""Offline reference solver. Never supplied to the model or used as a candidate."""
from functools import lru_cache


def solve(task):
    """Exhaustive subset search; inventories depend only on the completed subset.

    Independent of generation order and Environment._dispatch; uses public task
    attributes only. Returns a complete feasible schedule or None.
    """
    jobs = task['jobs']
    indexes = {job['id']: i for i, job in enumerate(jobs)}
    required = [sum(1 << indexes[d] for d in j['requires']) for j in jobs]
    goals = sum(1 << indexes[g] for g in task['goals'])
    capacity = task['resources']['capacity']

    @lru_cache(None)
    def search(mask, stock):
        if mask & goals == goals:
            return ()
        for i, job in enumerate(jobs):
            if mask & (1 << i) or mask & required[i] != required[i]:
                continue
            take, give = job['payload']['consumes'], job['payload']['produces']
            if any(s < t for s, t in zip(stock, take)):
                continue
            after = tuple(s - t + g for s, t, g in zip(stock, take, give))
            if any(s > c for s, c in zip(after, capacity)):
                continue
            tail = search(mask | (1 << i), after)
            if tail is not None:
                return (job['id'],) + tail
        return None

    path = search(0, tuple(task['resources']['initial']))
    return {'order': list(path) if path is not None else None, 'states_searched': search.cache_info().misses}
