"""Independent exact packing reference, never available to model sessions."""


def _backtracking(task):
    jobs = sorted([j for j in task['jobs'] if j['payload']['units']],
                  key=lambda j: -j['payload']['units'])
    pools = list(task['pools'])
    remaining = [task['pools'][p] for p in pools]
    chosen, failed = {}, set()
    states = 0

    def search(index):
        nonlocal states
        states += 1
        if index == len(jobs):
            return True
        key = (index, tuple(sorted(remaining)))
        if key in failed:
            return False
        job = jobs[index]
        units = job['payload']['units']
        seen = set()
        for i in sorted(range(len(pools)), key=lambda i:remaining[i]):
            space = remaining[i]
            if space < units or space in seen:
                continue
            seen.add(space)
            remaining[i] -= units
            chosen[job['id']] = pools[i]
            if search(index + 1):
                return True
            remaining[i] += units
            del chosen[job['id']]
        failed.add(key)
        return False

    if not search(0):
        return {'assignments':None, 'states_searched':states}
    order, done = [], set()
    while len(done) < len(task['jobs']):
        ready = [j for j in task['jobs'] if j['id'] not in done and set(j['requires']) <= done]
        if not ready:
            raise ValueError('cyclic dependencies')
        for job in ready:
            order.append({'job_id':job['id'], 'pool':chosen.get(job['id'],'')})
            done.add(job['id'])
    return {'assignments':order, 'states_searched':states}


def solve(task):
    """For equal full pools, enumerate exact-fill subsets then solve exact cover.

    Meet-in-the-middle enumeration avoids a potentially huge assignment tree
    when quantities have more integer precision. The generated partition is
    never consulted. Other pool layouts retain the general backtracking solver.
    """
    from collections import defaultdict
    from functools import lru_cache
    jobs = [j for j in task['jobs'] if j['payload']['units']]
    capacities = list(task['pools'].values())
    if len(set(capacities)) != 1 or sum(j['payload']['units'] for j in jobs) != sum(capacities):
        return _backtracking(task)
    n, capacity = len(jobs), capacities[0]
    def subsets(start, end):
        result = [(0, 0)]
        for i in range(start, end):
            units = jobs[i]['payload']['units']
            result += [(total + units, mask | (1 << i)) for total, mask in result[:]
                       if total + units <= capacity]
        return result
    right = defaultdict(list)
    for total, mask in subsets(n // 2, n):
        right[total].append(mask)
    groups = []
    for total, mask in subsets(0, n // 2):
        groups.extend(mask | other for other in right[capacity - total])
    containing = [[] for _ in jobs]
    for group in groups:
        for i in range(n):
            if group & (1 << i):
                containing[i].append(group)
    states = 0
    @lru_cache(None)
    def cover(mask):
        nonlocal states
        states += 1
        if not mask:
            return ()
        choices = None
        for i in range(n):
            if mask & (1 << i):
                options = [g for g in containing[i] if g & mask == g]
                if not options:
                    return None
                if choices is None or len(options) < len(choices):
                    choices = options
        for group in choices:
            tail = cover(mask ^ group)
            if tail is not None:
                return (group,) + tail
        return None
    partition = cover((1 << n) - 1)
    if partition is None:
        return {'assignments':None, 'states_searched':states, 'exact_fill_subsets':len(groups)}
    assignment = {}
    for pool, group in zip(task['pools'], partition):
        for i, job in enumerate(jobs):
            if group & (1 << i):
                assignment[job['id']] = pool
    order, done = [], set()
    while len(done) < len(task['jobs']):
        ready = [j for j in task['jobs'] if j['id'] not in done and set(j['requires']) <= done]
        if not ready:
            raise ValueError('cyclic dependencies')
        for job in ready:
            order.append({'job_id':job['id'], 'pool':assignment.get(job['id'], '')})
            done.add(job['id'])
    return {'assignments':order, 'states_searched':states, 'exact_fill_subsets':len(groups)}
