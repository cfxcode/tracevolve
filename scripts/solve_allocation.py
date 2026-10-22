"""Independent exact packing reference, never available to model sessions."""


def solve(task):
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
