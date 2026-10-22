import copy
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict

from test_capacity import load_script
from tracevolve.config import Config
from tracevolve.contracts import Bundle
from tracevolve.tasks import create_environment, generate, rename, tool_contract, validate
from tracevolve.task_server import tool_specs
from tracevolve.util import source_manifest, write_json

solve = load_script('solve_allocation').solve


def execute_reference(env):
    result = solve(env.task)
    assert result['assignments'] is not None
    events = []
    def call(name, args):
        out = env.call(name, args, bundle=asdict(Bundle()))
        events.append({'name':name,'arguments':args,'output':out})
        assert 'error' not in out, out
        return out
    call('load_bundle', {})
    call('list_jobs', {})
    for assignment in result['assignments']:
        job = call('inspect_job', {'job_id':assignment['job_id']})
        call('run_job', {**assignment,'mode':env.contract['checked_mode'] if job['payload']['requires_check'] else 'standard'})
    call('submit', {'goal_ids':env.task['goals']})
    return events


class AllocationTests(unittest.TestCase):
    def fixture(self):
        jobs = [{'id':str(i),'requires':[], 'payload':{'kind':'build','requires_check':False,'units':v}}
                for i,v in enumerate([6,4,4,6])]
        jobs.append({'id':'goal','requires':['0','1','2','3'], 'payload':{'kind':'publish','requires_check':True,'units':0}})
        return {'id':'fixture','jobs':jobs,'goals':['goal'],'pools':{'a':10,'b':10}}

    def test_reference_solves_50_seeds_both_modes_within_budget(self):
        for seed in range(50):
            for version in ('resource-allocation-v1','resource-allocation-v2'):
                with self.subTest(seed=seed, version=version):
                    task=generate(seed,str(seed),'test','allocation-v1')
                    env=create_environment(task['content'],tool_contract(version))
                    execute_reference(env)
                    self.assertTrue(env.evaluate()['success'])
                    self.assertEqual(env.calls,55)
                    self.assertEqual(env.errors,0)
                    self.assertEqual(sum(env.available.values()),0)

    def test_precision_profile_reference_and_modes(self):
        for seed in range(50):
            for version in ('resource-allocation-v1','resource-allocation-v2'):
                task=generate(seed,str(seed),'test','allocation-precision-v1')
                self.assertEqual(set(task['content']['pools'].values()), {1000})
                self.assertTrue(any(j['payload']['units'] % 10 for j in task['content']['jobs']))
                env=create_environment(task['content'],tool_contract(version))
                execute_reference(env)
                self.assertTrue(env.evaluate()['success'])
                self.assertEqual(env.calls,55)
                self.assertEqual(sum(env.available.values()),0)

    def test_exact_cover_rejects_unsolvable_packing(self):
        task=self.fixture()
        for job,units in zip(task['jobs'],[6,6,6,2,0]):job['payload']['units']=units
        self.assertIsNone(solve(task)['assignments'])

    def test_reference_supports_unequal_pools_with_slack(self):
        task=self.fixture();task['pools']={'a':11,'b':12}
        env=create_environment(task,tool_contract('resource-allocation-v1'))
        execute_reference(env)
        self.assertTrue(env.evaluate()['success'])
        self.assertEqual(sum(env.available.values()),3)

    def test_locally_valid_allocation_can_strand_remaining_jobs(self):
        env=create_environment(self.fixture(),tool_contract('resource-allocation-v1'))
        for job,pool in [('1','a'),('2','a')]:
            self.assertEqual(env.call('run_job',{'job_id':job,'pool':pool,'mode':'standard'})['status'],'done')
        self.assertEqual(env.available,{'a':2,'b':10})
        env.call('run_job',{'job_id':'0','pool':'b','mode':'standard'})
        before=(env.done.copy(),dict(env.available),dict(env.assignments))
        self.assertEqual(env.call('run_job',{'job_id':'3','pool':'b','mode':'standard'})['error'],'INSUFFICIENT_POOL_CAPACITY')
        self.assertEqual((env.done,env.available,env.assignments),before)
        self.assertEqual(env.evaluate()['agent_errors'],1)

    def test_invalid_pool_and_duplicate_do_not_allocate(self):
        env=create_environment(self.fixture(),tool_contract('resource-allocation-v1'))
        for pool in ('missing','',[],None):
            self.assertIn('error',env.call('run_job',{'job_id':'0','pool':pool,'mode':'standard'}))
            self.assertFalse(env.done)
            self.assertEqual(env.available,env.task['pools'])
        args={'job_id':'0','pool':'a','mode':'standard'}
        env.call('run_job',args)
        self.assertEqual(env.call('run_job',args)['status'],'already_done')
        self.assertEqual(env.available,{'a':4,'b':10})

    def test_validation_and_contract_match(self):
        task=self.fixture()
        with self.assertRaises(ValueError):create_environment(task,tool_contract())
        for units in (-1,True,11,1.5):
            bad=copy.deepcopy(task);bad['jobs'][0]['payload']['units']=units
            with self.assertRaises(ValueError):validate(bad)
        with self.assertRaises(ValueError):Config(task_profile='allocation-v1',max_tool_calls=54)

    def test_renaming_preserves_assignments_and_replay(self):
        source=generate(1,'x','test','allocation-v1');other,mapping=rename(source,18)
        a=create_environment(source['content'],tool_contract('resource-allocation-v1'))
        b=create_environment(other['content'],tool_contract('resource-allocation-v1'))
        def transform(x):
            if isinstance(x,str):return mapping.get(x,x)
            if isinstance(x,list):return [transform(v) for v in x]
            if isinstance(x,dict):return {k:transform(v) for k,v in x.items()}
            return x
        events=execute_reference(a)
        for event in events:
            self.assertEqual(b.call(event['name'],transform(event['arguments']),bundle=asdict(Bundle())),transform(event['output']))
        self.assertEqual(b.evaluate(),transform(a.evaluate()))
        request={'implementation':source_manifest(),'server_input':{'task':a.task,'contract':a.contract,'max_tool_calls':64,'bundle':asdict(Bundle())}}
        result={'status':'completed','tool_events':events,'evaluation':a.evaluate()}
        audit=load_script('audit_resource_runs')
        self.assertTrue(audit.replay_run(request,result)['checked'])
        result['evaluation']['pool_available']['pool-0']+=1
        with self.assertRaises(ValueError):audit.replay_run(request,result)

    def test_stdio_and_v2_tool_schema(self):
        env=create_environment(self.fixture(),tool_contract('resource-allocation-v2'))
        events=execute_reference(env)
        spec=next(s for s in tool_specs(env.contract) if s['name']=='run_job')
        self.assertIn('pool',spec['inputSchema']['required'])
        self.assertIn('verified',spec['inputSchema']['properties']['mode']['enum'])
        with tempfile.TemporaryDirectory() as root:
            write_json(Path(root)/'server_input.json',{'task':env.task,'contract':env.contract,'bundle':asdict(Bundle()),'max_tool_calls':64})
            requests=[{'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':e['name'],'arguments':e['arguments']}} for i,e in enumerate(events)]
            proc=subprocess.run([sys.executable,'-m','tracevolve.task_server','--root',root],input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,check=True)
            outputs=[json.loads(json.loads(line)['result']['content'][0]['text']) for line in proc.stdout.splitlines()]
            self.assertEqual(outputs,[e['output'] for e in events])

    def test_synthetic_export_and_lifecycle_preserve_allocation_contract(self):
        # Protocol wiring only. These synthetic decisions are not live evidence;
        # inventory semantics and stdio are checked in separate tests above.
        from test_core import SyntheticAdapter
        from tracevolve.store import Store
        from tracevolve.study import Study
        from tracevolve.memory import Registry
        for profile in ('allocation-v1','allocation-precision-v1'):
            for phase in ('p0','p1a'):
                with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
                    store=Store(root)
                    try:
                        study=Study(store,SyntheticAdapter(),Config(phase=phase,task_profile=profile,max_development_tasks=1))
                        report=study.run()
                        if phase=='p0':
                            self.assertTrue(report['data_complete'])
                            self.assertTrue((Path(root)/'export/records.v2.json').is_file())
                        else:
                            self.assertEqual(report['phase'],'AWAITING_MAINTAINER')
                            Registry(store).publish(store.state('candidates')[0],expected_generation=0,maintainer='synthetic test')
                            lifecycle=study.run()['lifecycle']
                            self.assertTrue(lifecycle['revalidation_complete'])
                            self.assertTrue(lifecycle['skill_paused_for_new_contract'])
                            changed=lifecycle['contract_change']['new_contract']
                            self.assertEqual(changed['version'],'resource-allocation-v2')
                            self.assertTrue(changed['pool_assignment'])
                    finally:
                        store.close()


if __name__=='__main__':unittest.main()
