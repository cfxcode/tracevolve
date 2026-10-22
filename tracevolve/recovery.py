"""Deterministic resource recovery scenarios, with explicit fault provenance."""
from .tasks import ResourceEnvironment


class RecoveryEnvironment(ResourceEnvironment):
    def __init__(self, task, contract, max_calls=64):
        super().__init__(task, contract, max_calls)
        if any('fault' not in j['payload'] for j in task['jobs']):
            raise ValueError('recovery tasks require a fault category for every job')
        self.receipts = {}
        self.receipt_sequence = 0
        self.faulted = set()
        self.environment_faults = 0

    def _dispatch(self, name, args):
        if name not in ('check_job', 'run_job'):
            return super()._dispatch(name, args)
        if self.submitted:
            return {'error': 'ALREADY_SUBMITTED'}
        expected = {'job_id'} if name == 'check_job' else {'job_id', 'mode', 'receipt'}
        if not isinstance(args, dict) or set(args) != expected or not isinstance(args['job_id'], str):
            return {'error': 'INVALID_ARGUMENT'}
        job_id = args['job_id']
        if job_id not in self.jobs:
            return {'error': 'UNKNOWN_JOB'}
        job = self.jobs[job_id]
        if job_id in self.done:
            return {'error': 'ALREADY_COMMITTED', 'recovery': 'Use inspect_job to confirm status; do not rerun completed jobs.'}
        if not set(job['requires']) <= self.done:
            return {'error': 'DEPENDENCIES_NOT_DONE'}
        if name == 'check_job':
            self.receipt_sequence += 1
            receipt = {'receipt': 'check-' + str(self.receipt_sequence),
                       'valid_through_call': self.calls + self.contract['receipt_ttl_calls']}
            self.receipts[job_id] = receipt
            return {**receipt, 'job_id': job_id, 'current_call': self.calls}
        required = self.contract['checked_mode'] if job['payload']['requires_check'] else 'standard'
        if args['mode'] != required:
            return {'error': 'WRONG_MODE'}
        if not isinstance(args['receipt'], str):
            return {'error': 'INVALID_ARGUMENT'}
        if job['payload']['requires_check']:
            receipt = self.receipts.get(job_id)
            if not receipt or args['receipt'] != receipt['receipt']:
                return {'error': 'INVALID_RECEIPT', 'recovery': 'Call check_job after prerequisites are done, then run with the new receipt.'}
            if self.calls > receipt['valid_through_call']:
                return {'error': 'EXPIRED_RECEIPT', 'recovery': 'Call check_job again immediately before execution.'}
            del self.receipts[job_id]
        elif args['receipt']:
            return {'error': 'UNEXPECTED_RECEIPT'}
        fault = job['payload']['fault']
        if fault != 'stable' and job_id not in self.faulted:
            self.faulted.add(job_id)
            self.environment_faults += 1
            if fault == 'busy_once':
                return {'error': 'RESOURCE_BUSY', 'origin': 'environment', 'committed': False,
                        'recovery': 'Transient busy condition cleared. Receipt consumed; check again if required, then retry.'}
            self.done.add(job_id)
            return {'error': 'OUTCOME_UNKNOWN', 'origin': 'environment',
                    'recovery': 'Acknowledgment lost. Inspect this job before deciding whether to retry; a completed job must not be run again.'}
        self.done.add(job_id)
        return {'status': 'done', 'job_id': job_id}

    def evaluate(self):
        return {**super().evaluate(), 'environment_faults': self.environment_faults,
                'agent_errors': self.errors - self.environment_faults,
                'task_origin': 'procedural_recovery_scenario'}
