"""Recovery contracts and provenance tests; these never call a real model."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict

from test_core import SyntheticAdapter
from tracevolve.config import Config
from tracevolve.contracts import Bundle
from tracevolve.memory import Registry, applicability
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve.tasks import create_environment, generate, rename, tool_contract
from tracevolve.task_server import tool_specs
from tracevolve.util import canonical, read_json, source_manifest, write_json


def solve_recovery(env):
    """TEST ONLY reference traversal, including status resolution after lost ACK."""
    events, visited = [], set()
    def call(name, args):
        result = env.call(name, args, bundle=asdict(Bundle()))
        events.append({'name': name, 'arguments': args, 'output': result})
        return result
    call('load_bundle', {})
    def visit(job_id):
        if job_id in visited:
            return
        job = call('inspect_job', {'job_id': job_id})
        for dep in job['requires']:
            visit(dep)
        while True:
            checked = job['payload']['requires_check']
            receipt = call('check_job', {'job_id': job_id})['receipt'] if checked else ''
            result = call('run_job', {'job_id': job_id, 'mode': env.contract['checked_mode'] if checked else 'standard', 'receipt': receipt})
            if result.get('error') == 'RESOURCE_BUSY':
                continue
            if result.get('error') == 'OUTCOME_UNKNOWN':
                result = call('inspect_job', {'job_id': job_id})
            assert result.get('status') == 'done', result
            break
        visited.add(job_id)
    for goal in env.task['goals']:
        visit(goal)
    call('submit', {'goal_ids': env.task['goals']})
    return events


class RecoveryTests(unittest.TestCase):
    def make_env(self, seed=1, version='resource-recovery-v1'):
        task = generate(seed, str(seed), 'test', 'recovery-v1')
        return create_environment(task['content'], tool_contract(version))

    def test_reference_solvable_within_budget_for_both_contracts(self):
        for version in ('resource-recovery-v1', 'resource-recovery-v2'):
            for seed in range(50):
                with self.subTest(version=version, seed=seed):
                    env = self.make_env(seed, version)
                    solve_recovery(env)
                    result = env.evaluate()
                    self.assertTrue(result['success'])
                    self.assertEqual(result['environment_faults'], 2)
                    self.assertEqual(result['tool_errors'], 2)
                    self.assertEqual(result['agent_errors'], 0)
                    self.assertLessEqual(result['tool_calls'], 51)

    def test_receipt_expiry_and_illegal_actions_do_not_commit(self):
        env = self.make_env()
        job = next(j for j in env.task['jobs'] if not j['requires'])
        job_id = job['id']
        # Select the root deterministically and make it checked only in this fixture.
        env.jobs[job_id]['payload']['requires_check'] = True
        receipt = env.call('check_job', {'job_id':job_id})['receipt']
        for _ in range(3):
            env.call('list_jobs', {})
        args = {'job_id':job_id, 'mode':'checked', 'receipt':receipt}
        self.assertEqual(env.call('run_job', args)['error'], 'EXPIRED_RECEIPT')
        self.assertFalse(env.done)
        self.assertEqual(env.evaluate()['agent_errors'], 1)
        args['receipt'] = env.call('check_job', {'job_id':job_id})['receipt']
        self.assertEqual(env.call('run_job', args)['status'], 'done')
        self.assertEqual(env.call('run_job', args)['error'], 'ALREADY_COMMITTED')

    def test_environment_faults_are_separate_from_agent_errors(self):
        env = self.make_env()
        solve_recovery(env)
        env.call('run_job', {'job_id':env.task['goals'][0], 'mode':'checked', 'receipt':'old'})
        result = env.evaluate()
        self.assertEqual(result['tool_errors'], 3)
        self.assertEqual(result['environment_faults'], 2)
        self.assertEqual(result['agent_errors'], 1)

    def test_environment_faults_alone_do_not_trigger_proposal(self):
        class FaultsHandled(SyntheticAdapter):
            def execute(self, **kwargs):
                result = super().execute(**kwargs)
                result['evaluation'].update(success=True, utility=1., tool_errors=2, environment_faults=2, agent_errors=0)
                return result
            def infer(self, **kwargs):
                raise AssertionError('a handled environment fault is not an agent defect')
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            store = Store(root)
            try:
                result = Study(store, FaultsHandled(), Config(task_profile='recovery-v1', max_development_tasks=2)).run()
                self.assertEqual(result['actual_candidates'], 0)
                self.assertEqual(result['stop_reason'], 'NO_ELIGIBLE_CANDIDATE')
            finally:
                store.close()

    def test_recovery_profile_requires_matching_contract(self):
        task = generate(1, 'test', 'dev', 'recovery-v1')['content']
        with self.assertRaisesRegex(ValueError, 'requires recovery contract'):
            create_environment(task, tool_contract())
        with self.assertRaises(ValueError):
            Config(task_profile='recovery-v1', max_tool_calls=50)

    def test_renaming_preserves_recovery_actions_and_responses(self):
        source = generate(2, 'test', 'dev', 'recovery-v1')
        other, mapping = rename(source, 33)
        a = create_environment(source['content'], tool_contract('resource-recovery-v1'))
        b = create_environment(other['content'], tool_contract('resource-recovery-v1'))
        def transform(value):
            if isinstance(value, str):
                return mapping.get(value, value)
            if isinstance(value, list):
                return [transform(x) for x in value]
            if isinstance(value, dict):
                return {k:transform(v) for k,v in value.items()}
            return value
        events = solve_recovery(a)
        for event in events:
            result = b.call(event['name'], transform(event['arguments']), bundle=asdict(Bundle()))
            self.assertEqual(result, transform(event['output']))
        self.assertEqual(b.evaluate(), transform(a.evaluate()))

    def test_contract_change_changes_modes_and_invalidates_permit(self):
        first, second = tool_contract('resource-recovery-v1'), tool_contract('resource-recovery-v2')
        permit = {'status':'active','task_family':first['family'],'bundle_hash':Bundle().hash,'dependencies':first}
        self.assertEqual(applicability(permit, task_family=first['family'], contract=second,
                                     bundle_hash=Bundle().hash)['result'], 'inapplicable')
        spec = next(s for s in tool_specs(second) if s['name'] == 'run_job')
        self.assertEqual(spec['inputSchema']['properties']['mode']['enum'], ['verified','standard'])
        self.assertIn('receipt', spec['inputSchema']['required'])
        self.assertIn('check_job', [s['name'] for s in tool_specs(second)])

    def test_followup_preserves_recovery_contract_when_mode_changes(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            store = Store(root)
            try:
                # Synthetic control-flow fixture; environment semantics are tested separately.
                study = Study(store, SyntheticAdapter(), Config(phase='p1a', task_profile='recovery-v1', max_development_tasks=1))
                self.assertEqual(study.run()['phase'], 'AWAITING_MAINTAINER')
                self.assertEqual(store.state('permit')['dependencies']['receipt_ttl_calls'], 3)
                Registry(store).publish(store.state('candidates')[0], expected_generation=0, maintainer='offline test')
                result = study.run()
                self.assertTrue(result['lifecycle']['skill_paused_for_new_contract'])
                self.assertTrue(result['lifecycle']['revalidation_complete'])
                for call in store.calls():
                    if call['phase'] == 'revalidation':
                        contract = store.get(call['request_hash'])['contract']
                        self.assertEqual(contract['version'], 'resource-recovery-v2')
                        self.assertEqual(contract['checked_mode'], 'verified')
                        self.assertTrue(contract['check_receipt_required'])
            finally:
                store.close()

    def test_recovery_traces_replay_and_fabricated_fault_count_is_rejected(self):
        spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'scripts/audit_resource_runs.py')
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        env = self.make_env()
        events = solve_recovery(env)
        request = {'implementation':source_manifest(),'server_input':{'task':env.task,'contract':env.contract,
                   'max_tool_calls':64,'bundle':asdict(Bundle())}}
        result = {'status':'completed','tool_events':events,'evaluation':env.evaluate()}
        self.assertTrue(audit.replay_run(request, result)['checked'])
        changed = copy.deepcopy(result)
        changed['evaluation']['agent_errors'] = 2
        with self.assertRaisesRegex(ValueError, 'final outcome'):
            audit.replay_run(request, changed)

    def test_real_stdio_matches_reference_recovery_transcript(self):
        env = self.make_env()
        events = solve_recovery(env)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_json(root / 'server_input.json', {'task':env.task,'contract':env.contract,'max_tool_calls':64,'bundle':asdict(Bundle())})
            requests = [{'jsonrpc':'2.0','id':i,'method':'tools/call',
                         'params':{'name':e['name'],'arguments':e['arguments']}} for i,e in enumerate(events)]
            proc = subprocess.run([sys.executable,'-m','tracevolve.task_server','--root',d],
                input='\n'.join(map(canonical,requests)),capture_output=True,text=True,check=True)
            responses = [json.loads(line) for line in proc.stdout.splitlines()]
            for response, event in zip(responses, events):
                self.assertEqual(json.loads(response['result']['content'][0]['text']), event['output'])
            actual = read_json(root / 'environment_result.json')
            for key, value in env.evaluate().items():
                self.assertEqual(actual[key], value)
