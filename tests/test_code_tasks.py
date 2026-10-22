import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import patch

from tracevolve.code_corpus import ISSUES,fixture
from tracevolve.code_runner import available,check,execute
from tracevolve.code_tasks import BASE_PROMPT,CodeEnvironment,acceptance,contract,generate,plan,rename,rename_files,tool_specs
from tracevolve.config import Config
from tracevolve.contracts import Bundle,frozen_harness
from tracevolve.memory import Registry,selected_bundle
from tracevolve.store import Store
from tracevolve.study import Study,judgment_settings
from tracevolve.util import digest,write_json
from test_core import SyntheticAdapter


class CodeContractTests(unittest.TestCase):
    def test_distinct_splits_and_finite_exhaustion(self):
        tasks=plan(0,6,1,3)
        ids=[t['template_id'] for rows in tasks.values() for t in rows]
        self.assertEqual(len(ids),11)
        self.assertEqual(len(set(ids)),11)
        with self.assertRaisesRegex(ValueError,'EXHAUSTED'): plan(0,6,5,3)
        with self.assertRaisesRegex(ValueError,'EXHAUSTED'): plan(0,6,1,3,ids)

    def test_gateway_paths_counts_submission_and_cas(self):
        env=CodeEnvironment(generate(0,'x','development')['content'],contract('code-maintenance-v2'))
        bundle=asdict(Bundle(prompt=BASE_PROMPT))
        self.assertIn('error',env.call('read_file',{'path':'core.py'},bundle=bundle))
        env.call('load_bundle',{},bundle=bundle)
        env.call('load_bundle',{},bundle=bundle)
        self.assertIn('error',env.call('read_file',{'path':'../auth.json'},bundle=bundle))
        before=env.files.copy()
        self.assertEqual(env.call('write_file',{'path':'core.py','content':'x=1','expected_sha256':'bad'})['error'],'STALE_WRITE')
        self.assertEqual(env.files,before)
        sha=env.call('read_file',{'path':'core.py'})['sha256']
        env.call('write_file',{'path':'core.py','content':'x=1','expected_sha256':sha})
        env.call('submit',{})
        self.assertEqual(env.call('write_file',{'path':'core.py','content':'x=2','expected_sha256':sha})['error'],'ALREADY_SUBMITTED')
        self.assertEqual(env.calls,9); self.assertEqual(env.errors,4)
        self.assertFalse(env.evaluate()['success'])

    def test_public_tests_are_not_agent_errors(self):
        env=CodeEnvironment(generate(0,'x','development')['content'],contract())
        with patch('tracevolve.code_runner.check',return_value={'passed':0,'total':1}):
            self.assertEqual(env.call('run_tests',{}),{'passed':0,'total':1})
        self.assertEqual(env.errors,0)
        text=json.dumps(env.call('list_files',{}))
        self.assertNotIn('acceptance',text); self.assertNotIn('reference_files',text)
        original=copy.deepcopy(env.task['public_tests'])
        with patch('tracevolve.code_runner.check',return_value={'passed':1,'total':1}) as checker:
            cases=[{'input':[],'expected':0}]
            self.assertEqual(env.call('check_examples',{'cases':cases})['passed'],1)
            self.assertEqual(checker.call_args.args[-1],cases)
        self.assertEqual(env.task['public_tests'],original)
        self.assertIn('error',env.call('check_examples',{'cases':[{'input':1}]}))
        self.assertIn('error',env.call('check_examples',{'cases':[{'input':float('inf'),'expected':0}]}))

    def test_schema_and_platform_fail_closed(self):
        old={s['name']:s for s in tool_specs(contract())}
        new={s['name']:s for s in tool_specs(contract('code-maintenance-v2'))}
        self.assertEqual(set(new['write_file']['inputSchema']['required'])-set(old['write_file']['inputSchema']['required']),{'expected_sha256'})
        with patch('tracevolve.code_runner.available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'UNAVAILABLE'): execute({'api.py':'x=1'},'api',[])

    def test_renaming_changes_imports_only(self):
        files={'core.py':'def solve(data):\n    return "core"\n','api.py':'from core import solve\n\ndef handle(data):\n    core = "core"\n    return solve(data)\n'}
        result=rename_files(files,{'core':'other','api':'entry'})
        self.assertIn('from other import solve',result['entry.py'])
        self.assertIn('core = "core"',result['entry.py'])
        self.assertIn('return "core"',result['other.py'])


@unittest.skipUnless(available(),'code execution requires macOS Seatbelt')
class CodeExecutionTests(unittest.TestCase):
    def test_certificate_precedes_calls_and_recovers_materialization(self):
        class OfflinePreflight(SyntheticAdapter):
            synthetic=False
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            try:
                study=Study(store,OfflinePreflight(),Config(task_profile='code-maintenance-v1',seed=12))
                study.freeze()
                self.assertEqual(store.calls(),[])
                certificate=store.get(store.state('reference_certificate'))
                self.assertEqual(len(certificate['tasks']),11)
                (store.root/'reference.json').unlink()
                study.freeze()
                self.assertTrue((store.root/'reference.json').is_file())
                write_json(store.root/'reference.json',{})
                with self.assertRaisesRegex(ValueError,'reference file mismatch'): study.freeze()
            finally: store.close()

    def test_all_reference_repairs_and_renames(self):
        for i,row in enumerate(ISSUES):
            with self.subTest(issue=row[0]):
                f=fixture(i); task=generate(i,str(i),'development')
                bad=acceptance(task['content'],f['files'])
                good=acceptance(task['content'],f['reference_files'])
                renamed,mapping=rename(task,42)
                other=acceptance(renamed['content'],rename_files(f['reference_files'],mapping))
                self.assertLess(bad['passed'],bad['total'])
                self.assertEqual(good['passed'],good['total'])
                self.assertEqual(other['passed'],other['total'])
                self.assertNotIn('reference_files',task['content'])

    def test_sandbox_denies_host_reads_network_and_children(self):
        with tempfile.TemporaryDirectory() as directory:
            secret=Path(directory)/'sentinel'; secret.write_text('DO NOT EXPOSE')
            probes=[f'open({str(secret)!r}).read()', "__import__('socket').socket().connect(('127.0.0.1',9))",'__import__("os").fork()']
            for probe in probes:
                source='def handle(data):\n    try:\n        '+probe+'\n        return "ESCAPED"\n    except PermissionError:\n        return "DENIED"\n'
                result=execute({'api.py':source},'api',[None])
                self.assertEqual(result['answers'][0]['value'],'DENIED',result)
            self.assertEqual(secret.read_text(),'DO NOT EXPOSE')

    def test_task_failure_is_deterministic_and_input_mutation_rejected(self):
        malformed=check({'api.py':'not valid python!'},'api',[{'input':None,'expected':None}])
        self.assertEqual(malformed['passed'],0)
        source='def handle(data):\n    data.append(1)\n    return 7\n'
        self.assertEqual(check({'api.py':source},'api',[{'input':[],'expected':7}])['passed'],0)
        self.assertEqual(check({'api.py':'def handle(data):\n    while True: pass\n'},'api',[{'input':0,'expected':0}])['passed'],0)


class CodeSyntheticAdapter(SyntheticAdapter):
    def execute(self,*,store,logical_id,bundle,task,contract,config,phase):
        request={'bundle':asdict(bundle),'task':task,'contract':contract}
        cached=store.reserve(logical_id,phase,request,config.max_sessions)
        if cached: return cached
        self.order.append(phase)
        snapshot=self.snapshot(bundle,contract,config)
        success=bundle.skill is not None
        result={'run_id':logical_id,'status':'completed','synthetic':True,'execution_origin':'synthetic_test',
                'cost_usd':None,'elapsed_seconds':0,'harness':frozen_harness(bundle,snapshot),
                'task_sha256':task['sha256'],'evaluation':{'success':success,'utility':float(success),'tool_errors':0},
                'tool_events':[],'snapshot_hash':snapshot.hash,'execution_snapshot':asdict(snapshot)}
        return store.finish(logical_id,result)


class CodeLifecycleTests(unittest.TestCase):
    def test_publish_followup_pause_revalidate_rollback_and_bindings(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            store=Store(directory)
            config=Config(task_profile='code-maintenance-v1',phase='p1a',seed=12)
            study=Study(store,CodeSyntheticAdapter(),config)
            try:
                report=study.run()
                self.assertEqual(report['phase'],'AWAITING_MAINTAINER')
                candidate=store.state('candidates')[0]
                self.assertEqual(candidate['candidate']['prompt'],BASE_PROMPT)
                self.assertEqual(store.state('permit')['task_family'],'python-code-maintenance')
                data=study.data(store.state('units'))
                broken=copy.deepcopy(data)
                broken['units'][0]['extra_C'][0]['task']['content']['issue']='changed'
                with self.assertRaises(ValueError): judgment_settings(broken,synthetic=True)
                with self.assertRaisesRegex(ValueError,'synthetic'): judgment_settings(data)
                registry=Registry(store)
                registry.publish(candidate,expected_generation=0,maintainer='offline-test')
                report=study.run()
                self.assertTrue(report['data_complete'])
                self.assertTrue(report['lifecycle']['was_published_before_change'])
                self.assertTrue(report['lifecycle']['selection']['skill_enabled'])
                self.assertTrue(report['lifecycle']['skill_paused_for_new_contract'])
                self.assertEqual(registry.pointer()['scope'],'python-code-maintenance')
                self.assertFalse(selected_bundle(store,candidate,study.contract)[1]['skill_enabled'])
                registry.rollback(expected_generation=1,reason='offline-test')
                self.assertEqual(registry.pointer()['bundle'],study.base.hash)
                self.assertTrue(store.verify()['verified'])
                with self.assertRaisesRegex(ValueError,'separate protocol'): study.export()
            finally: store.close()
