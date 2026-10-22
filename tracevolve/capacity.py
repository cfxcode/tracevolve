"""Finite shared inventory: legal local actions need not form a feasible plan."""
import copy
import random

from .tasks import ResourceEnvironment, envelope, semantic_identity, validate


def validate_resources(task):
    resources = task['resources']
    if not isinstance(resources, dict) or set(resources) != {'capacity', 'initial'}:
        raise ValueError('invalid inventory schema')
    cap, initial = resources['capacity'], resources['initial']
    if (not isinstance(cap, list) or len(cap) != 4 or not isinstance(initial, list)
            or len(initial) != 4 or any(type(x) is not int or x < 1 for x in cap)
            or any(type(x) is not int or not 0 <= x <= c for x, c in zip(initial, cap))):
        raise ValueError('invalid inventory quantities')
    for job in task['jobs']:
        for field in ('consumes', 'produces'):
            values = job['payload'].get(field)
            if (not isinstance(values, list) or len(values) != 4
                    or any(type(x) is not int or not 0 <= x <= c for x, c in zip(values, cap))):
                raise ValueError('invalid job inventory effect')


class CapacityEnvironment(ResourceEnvironment):
    def __init__(self, task, contract, max_calls=64):
        if 'resources' not in task:
            raise ValueError('capacity contract requires inventory task')
        super().__init__(task, contract, max_calls)
        self.capacity = list(task['resources']['capacity'])
        self.stock = list(task['resources']['initial'])

    def _dispatch(self, name, args):
        # Preserve all base validation before changing either state component.
        if name != 'run_job':
            result = super()._dispatch(name, args)
            if name == 'list_jobs' and 'error' not in result:
                result['resources'] = {**copy.deepcopy(self.task['resources']), 'available': list(self.stock)}
            return result
        old_done = self.done.copy()
        result = super()._dispatch(name, args)
        if result.get('status') != 'done':
            return result
        job = self.jobs[args['job_id']]
        p = job['payload']
        after = [s - take + give for s, take, give in zip(self.stock, p['consumes'], p['produces'])]
        if any(s < take for s, take in zip(self.stock, p['consumes'])):
            self.done = old_done
            return {'error': 'INSUFFICIENT_INVENTORY', 'available': list(self.stock), 'consumes': p['consumes']}
        if any(a > c for a, c in zip(after, self.capacity)):
            self.done = old_done
            return {'error': 'INVENTORY_CAPACITY', 'available': list(self.stock), 'would_produce': after}
        self.stock = after
        return {**result, 'available': list(self.stock)}

    def evaluate(self):
        return {**super().evaluate(), 'available': list(self.stock), 'agent_errors': self.errors,
                'environment_faults': 0, 'task_origin': 'procedural_capacity_scenario'}


def generate_capacity(seed, source_id, split):
    rng = random.Random(seed)
    n = rng.randint(20, 24)
    capacity = [rng.randint(6, 10) for _ in range(4)]
    initial = [rng.randint(2, c - 2) for c in capacity]
    stock = initial[:]
    ids_rng = random.Random(seed ^ 0xA57C9)
    ids = []
    while len(ids) < n + 1:
        value = 'r_' + f'{ids_rng.getrandbits(32):08x}'
        if value not in ids:
            ids.append(value)
    jobs = []
    for i in range(n):
        consumes = [rng.randint(0, min(s, 4)) for s in stock]
        produces = [rng.randint(0, min(c - s + t, 4)) for c, s, t in zip(capacity, stock, consumes)]
        stock = [s - t + g for s, t, g in zip(stock, consumes, produces)]
        parents = rng.sample(range(i), rng.randint(0, min(i, 2)))
        jobs.append({'id': ids[i], 'requires': [ids[p] for p in sorted(parents)],
                     'payload': {'kind': rng.choice(['prepare', 'build', 'verify']),
                                 'requires_check': bool(rng.getrandbits(1)),
                                 'consumes': consumes, 'produces': produces}})
    parents_used = {p for j in jobs for p in j['requires']}
    jobs.append({'id': ids[n], 'requires': [i for i in ids[:n] if i not in parents_used],
                 'payload': {'kind': 'publish', 'requires_check': True,
                             'consumes': [0] * 4, 'produces': [0] * 4}})
    rng.shuffle(jobs)
    task = {'id': source_id, 'jobs': jobs, 'goals': [ids[n]],
            'resources': {'capacity': capacity, 'initial': initial}}
    validate(task)
    return envelope(task, source_id, split=split, seed=seed,
                    template_id=semantic_identity(task, False), semantic_hash=semantic_identity(task))
