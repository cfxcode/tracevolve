"""Irreversible job allocation across finite shared resource pools."""
import random

from .tasks import ResourceEnvironment, envelope, semantic_identity, validate


def validate_pools(task):
    pools = task['pools']
    if (not isinstance(pools, dict) or not pools
            or any(not isinstance(k, str) or not k or type(v) is not int or v < 1 for k,v in pools.items())):
        raise ValueError('invalid pool capacities')
    for job in task['jobs']:
        units = job['payload'].get('units')
        if type(units) is not int or units < 0 or units > max(pools.values()):
            raise ValueError('invalid allocation units')


class AllocationEnvironment(ResourceEnvironment):
    def __init__(self, task, contract, max_calls=64):
        if 'pools' not in task:
            raise ValueError('allocation contract requires pool task')
        super().__init__(task, contract, max_calls)
        self.available = dict(task['pools'])
        self.assignments = {}

    def _dispatch(self, name, args):
        if name != 'run_job':
            result = super()._dispatch(name, args)
            if name == 'list_jobs' and 'error' not in result:
                result.update(pool_capacity=dict(self.task['pools']), pool_available=dict(self.available))
            if name == 'inspect_job' and 'error' not in result:
                result['assigned_pool'] = self.assignments.get(args['job_id'])
            return result
        if (not isinstance(args, dict) or set(args) != {'job_id','mode','pool'}
                or not isinstance(args['pool'], str)):
            return {'error':'INVALID_ARGUMENT'}
        previous = self.done.copy()
        result = super()._dispatch(name, {'job_id':args['job_id'], 'mode':args['mode']})
        if result.get('status') != 'done':
            return result
        units = self.jobs[args['job_id']]['payload']['units']
        pool = args['pool']
        if (units == 0 and pool != '') or (units > 0 and pool not in self.available):
            self.done = previous
            return {'error':'INVALID_POOL'}
        if units and self.available[pool] < units:
            self.done = previous
            return {'error':'INSUFFICIENT_POOL_CAPACITY', 'pool_available':dict(self.available), 'units':units}
        if units:
            self.available[pool] -= units
        self.assignments[args['job_id']] = pool
        return {**result, 'pool':pool, 'pool_available':dict(self.available)}

    def evaluate(self):
        # Store assignments as ordered records so identifier renaming remains
        # explicit in values, not implicit in dictionary keys.
        return {**super().evaluate(), 'pool_available':dict(self.available),
                'assignments':[{'job_id':j['id'],'pool':self.assignments[j['id']]}
                               for j in self.task['jobs'] if j['id'] in self.assignments],
                'agent_errors':self.errors, 'environment_faults':0,
                'task_origin':'procedural_allocation_scenario'}


def generate_allocation(seed, source_id, split, precision=1):
    rng = random.Random(seed)
    weights = []
    for _ in range(5):
        while True:
            group = [rng.randint(10 * precision, 30 * precision) for _ in range(4)]
            remaining = 100 * precision - sum(group)
            if 10 * precision <= remaining <= 30 * precision:
                weights.extend(group + [remaining])
                break
    rng.shuffle(weights)
    ids_rng = random.Random(seed ^ 0xA57C9)
    ids = []
    while len(ids) < 26:
        value = 'r_' + f'{ids_rng.getrandbits(32):08x}'
        if value not in ids:
            ids.append(value)
    jobs = []
    for i, units in enumerate(weights):
        parents = rng.sample(range(i), rng.randint(0, min(i, 2)))
        jobs.append({'id':ids[i], 'requires':[ids[p] for p in sorted(parents)],
                     'payload':{'kind':rng.choice(['prepare','build','verify']),
                                'requires_check':bool(rng.getrandbits(1)), 'units':units}})
    jobs.append({'id':ids[-1], 'requires':ids[:-1],
                 'payload':{'kind':'publish','requires_check':True,'units':0}})
    rng.shuffle(jobs)
    task = {'id':source_id, 'jobs':jobs, 'goals':[ids[-1]], 'pools':{f'pool-{i}':100 * precision for i in range(5)}}
    validate(task)
    return envelope(task, source_id, split=split, seed=seed,
                    template_id=semantic_identity(task, False), semantic_hash=semantic_identity(task))
