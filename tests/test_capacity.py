"""Offline capacity-contract checks; no model calls or fabricated live evidence."""
import copy
import importlib.util
from pathlib import Path
from dataclasses import asdict
import unittest
import contextlib
import io
import json
import subprocess
import sys
import tempfile

from tracevolve.config import Config
from tracevolve.contracts import Bundle
from tracevolve.tasks import create_environment, generate, rename, tool_contract, validate
from tracevolve.task_server import tool_specs
from tracevolve.util import source_manifest
from tracevolve.util import write_json


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


solve = load_script('solve_capacity').solve


def execute_reference(env):
    result = solve(env.task)
    assert result['order'] is not None
    events = []
    def call(name, args):
        out = env.call(name, args, bundle=asdict(Bundle()))
        events.append({'name': name, 'arguments': args, 'output': out})
        assert 'error' not in out, out
        return out
    call('load_bundle', {})
    call('list_jobs', {})
    for job_id in result['order']:
        job = call('inspect_job', {'job_id': job_id})
        call('run_job', {'job_id': job_id, 'mode': env.contract['checked_mode'] if job['payload']['requires_check'] else 'standard'})
    call('submit', {'goal_ids': env.task['goals']})
    return events


class CapacityTests(unittest.TestCase):
    def test_independent_reference_solves_50_seeds_both_contracts(self):
        for seed in range(50):
            for version in ('resource-capacity-v1', 'resource-capacity-v2'):
                with self.subTest(seed=seed, version=version):
                    task = generate(seed, str(seed), 'test', 'capacity-v1')
                    env = create_environment(task['content'], tool_contract(version))
                    execute_reference(env)
                    self.assertTrue(env.evaluate()['success'])
                    self.assertEqual(env.errors, 0)
                    self.assertLessEqual(env.calls, 53)

    def fixture(self):
        return {'id':'fixture','resources':{'initial':[2,0,0,0],'capacity':[3,3,3,3]},
                'jobs':[{'id':'a','requires':[], 'payload':{'kind':'build','requires_check':False,'consumes':[2,0,0,0],'produces':[0,2,0,0]}},
                        {'id':'b','requires':[], 'payload':{'kind':'build','requires_check':False,'consumes':[2,0,0,0],'produces':[3,0,0,0]}},
                        {'id':'c','requires':['a','b'], 'payload':{'kind':'publish','requires_check':True,'consumes':[0,0,0,0],'produces':[0,0,0,0]}}],
                'goals':['c']}

    def test_locally_legal_order_can_deadlock_and_error_is_atomic(self):
        task = self.fixture()
        self.assertEqual(solve(task)['order'], ['b','a','c'])
        env = create_environment(task, tool_contract('resource-capacity-v1'))
        env.call('run_job', {'job_id':'a','mode':'standard'})
        before = (env.done.copy(), env.stock[:])
        self.assertEqual(env.call('run_job', {'job_id':'b','mode':'standard'})['error'], 'INSUFFICIENT_INVENTORY')
        self.assertEqual((env.done, env.stock), before)
        self.assertEqual(env.evaluate()['agent_errors'], 1)

    def test_capacity_error_and_idempotence_preserve_stock(self):
        task = self.fixture()
        task['jobs'][0]['payload']['produces'] = [3,2,0,0]
        env = create_environment(task, tool_contract('resource-capacity-v1'))
        env.call('run_job', {'job_id':'b','mode':'standard'})
        self.assertEqual(env.stock, [3,0,0,0])
        self.assertEqual(env.call('run_job', {'job_id':'b','mode':'standard'})['status'], 'already_done')
        self.assertEqual(env.stock, [3,0,0,0])
        self.assertEqual(env.call('run_job', {'job_id':'a','mode':'standard'})['error'], 'INVENTORY_CAPACITY')
        self.assertEqual(env.done, {'b'})
        self.assertEqual(env.stock, [3,0,0,0])

    def test_payload_validation_and_contract_mismatch(self):
        task = self.fixture()
        with self.assertRaisesRegex(ValueError, 'requires capacity contract'):
            create_environment(task, tool_contract())
        for value in (True, -1, 4, 0.5):
            bad = copy.deepcopy(task)
            bad['jobs'][0]['payload']['consumes'][0] = value
            with self.assertRaises(ValueError):
                validate(bad)
        with self.assertRaises(ValueError):
            Config(task_profile='capacity-v1', max_tool_calls=52)

    def test_renaming_preserves_transitions_and_resources(self):
        source = generate(1, 'x', 'test', 'capacity-v1')
        other, mapping = rename(source, 17)
        a = create_environment(source['content'], tool_contract('resource-capacity-v1'))
        b = create_environment(other['content'], tool_contract('resource-capacity-v1'))
        def transform(x):
            if isinstance(x, str): return mapping.get(x,x)
            if isinstance(x, list): return [transform(v) for v in x]
            if isinstance(x, dict): return {k:transform(v) for k,v in x.items()}
            return x
        for event in execute_reference(a):
            self.assertEqual(b.call(event['name'], transform(event['arguments']), bundle=asdict(Bundle())), transform(event['output']))
        self.assertEqual(b.evaluate(), transform(a.evaluate()))

    def test_replay_checks_inventory_and_implementation(self):
        env = create_environment(self.fixture(), tool_contract('resource-capacity-v1'))
        events = execute_reference(env)
        request = {'implementation':source_manifest(), 'server_input':{'task':env.task,'contract':env.contract,
                   'max_tool_calls':64,'bundle':asdict(Bundle())}}
        result = {'status':'completed','tool_events':events,'evaluation':env.evaluate()}
        audit = load_script('audit_resource_runs')
        self.assertTrue(audit.replay_run(request,result)['checked'])
        result['evaluation']['available'][0] += 1
        with self.assertRaises(ValueError): audit.replay_run(request,result)

    def test_v2_contract_preserves_inventory_and_changes_mode(self):
        contract = tool_contract('resource-capacity-v2')
        spec = next(s for s in tool_specs(contract) if s['name']=='run_job')
        self.assertIn('verified', spec['inputSchema']['properties']['mode']['enum'])
        self.assertIn('irreversible', spec['description'])
        self.assertTrue(contract['shared_inventory'])

    def test_stdio_exposes_inventory_and_replays_reference(self):
        env = create_environment(self.fixture(), tool_contract('resource-capacity-v1'))
        events = execute_reference(env)
        with tempfile.TemporaryDirectory() as root:
            write_json(Path(root) / 'server_input.json', {'task':env.task, 'contract':env.contract,
                       'bundle':asdict(Bundle()), 'max_tool_calls':64})
            requests = [{'jsonrpc':'2.0','id':i,'method':'tools/call',
                         'params':{'name':e['name'],'arguments':e['arguments']}} for i,e in enumerate(events)]
            proc = subprocess.run([sys.executable,'-m','tracevolve.task_server','--root',root],
                                  input=''.join(json.dumps(r)+'\n' for r in requests), text=True, capture_output=True, check=True)
            outputs = [json.loads(json.loads(line)['result']['content'][0]['text']) for line in proc.stdout.splitlines()]
            self.assertEqual(outputs, [e['output'] for e in events])

    def test_synthetic_protocol_export_and_lifecycle_wiring(self):
        # Control-flow fixture only; inventory semantics are checked above.
        from test_core import SyntheticAdapter
        from tracevolve.store import Store
        from tracevolve.study import Study
        from tracevolve.memory import Registry
        for phase in ('p0','p1a'):
            with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
                store = Store(root)
                try:
                    study = Study(store, SyntheticAdapter(), Config(phase=phase, task_profile='capacity-v1', max_development_tasks=1))
                    report = study.run()
                    if phase == 'p0':
                        self.assertTrue(report['data_complete'])
                        self.assertTrue((Path(root)/'export/records.v2.json').is_file())
                    else:
                        self.assertEqual(report['phase'], 'AWAITING_MAINTAINER')
                        Registry(store).publish(store.state('candidates')[0], expected_generation=0, maintainer='synthetic test')
                        report = study.run()
                        self.assertTrue(report['lifecycle']['revalidation_complete'])
                        changed = report['lifecycle']['contract_change']['new_contract']
                        self.assertEqual(changed['version'], 'resource-capacity-v2')
                        self.assertTrue(changed['shared_inventory'])
                finally:
                    store.close()


if __name__ == '__main__':
    unittest.main()
