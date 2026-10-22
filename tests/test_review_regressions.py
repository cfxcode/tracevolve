"""Offline regressions for the October review. No real model is called."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import patch

from test_core import SyntheticAdapter, solve
from tracevolve.auth import isolated_auth
from tracevolve.config import Config
from tracevolve.contracts import Bundle
from tracevolve.memory import Registry
from tracevolve import memory, study as study_module
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve.tasks import ResourceEnvironment, generate, plan_tasks, tool_contract
from tracevolve.util import canonical, read_json, text_hash, write_json


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'auth.json'
        self.original = {'auth_mode': 'chatgpt', 'tokens': {'refresh_token': 'original-fixture'}}
        self.rotated = {'auth_mode': 'chatgpt', 'tokens': {'refresh_token': 'rotated-fixture'}}
        write_json(self.source, self.original)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rotation_survives_exception_and_next_lease(self):
        with self.assertRaises(KeyboardInterrupt):
            with isolated_auth(self.root) as isolated:
                write_json(isolated / 'auth.json', self.rotated)
                raise KeyboardInterrupt()
        self.assertEqual(read_json(self.source), self.rotated)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o600)
        with isolated_auth(self.root) as isolated:
            self.assertEqual(read_json(isolated / 'auth.json'), self.rotated)
        self.assertFalse((self.root / '.tracevolve-auth/active').exists())

    def test_concurrent_login_change_preserves_both_and_blocks_retry(self):
        external = {**self.original, 'tokens': {'refresh_token': 'other-login-fixture'}}
        with self.assertRaisesRegex(ValueError, 'AUTH_REFRESH_CONFLICT'):
            with isolated_auth(self.root) as isolated:
                write_json(isolated / 'auth.json', self.rotated)
                write_json(self.source, external)
        self.assertEqual(read_json(self.source), external)
        self.assertEqual(read_json(isolated / 'auth.json'), self.rotated)
        with self.assertRaisesRegex(ValueError, 'AUTH_REFRESH_CONFLICT'):
            with isolated_auth(self.root):
                self.fail('must not start with an unresolved rotation')

    def test_killed_process_rotation_recovered_before_next_session(self):
        driver = """
import os, sys
from tracevolve.auth import isolated_auth
from tracevolve.util import write_json
with isolated_auth(sys.argv[1]) as isolated:
    write_json(isolated / 'auth.json', {'auth_mode':'chatgpt','tokens':{'refresh_token':'rotated-fixture'}})
    os._exit(9)
"""
        run = subprocess.run([sys.executable, '-c', driver, str(self.root)])
        self.assertEqual(run.returncode, 9)
        with isolated_auth(self.root) as isolated:
            self.assertEqual(read_json(isolated / 'auth.json'), self.rotated)
        self.assertEqual(read_json(self.source), self.rotated)

    def test_concurrent_tracevolve_lease_fails_before_copy(self):
        with isolated_auth(self.root):
            with self.assertRaisesRegex(ValueError, 'AUTH_IN_USE'):
                with isolated_auth(self.root):
                    self.fail('second lease must not launch')


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.store.close()
        self.tmp.cleanup()

    def p1a(self):
        study = Study(self.store, SyntheticAdapter(), Config(phase='p1a', max_development_tasks=1))
        study.run()
        return study, self.store.state('candidates')[0]

    def test_resume_after_contract_change_reuses_original_followup_selection(self):
        study, candidate = self.p1a()
        Registry(self.store).publish(candidate, expected_generation=0, maintainer='test')
        original = memory.contract_changed
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(memory, 'contract_changed', interrupted), self.assertRaises(KeyboardInterrupt):
            study.run()
        calls_before = self.store.calls()
        result = study.run()
        lifecycle = result['lifecycle']
        self.assertTrue(lifecycle['selection']['skill_enabled'])
        self.assertTrue(lifecycle['was_published_before_change'])
        self.assertTrue(lifecycle['skill_paused_for_new_contract'])
        self.assertTrue(lifecycle['revalidation_complete'])
        self.assertEqual(sum(c['id'] == 'p1a-followup' for c in self.store.calls()), 1)
        self.assertEqual(len(self.store.calls()), len(calls_before) + 2)
        self.assertTrue(self.store.verify()['verified'])

    def test_resume_repairs_missing_sealed_file_without_model_calls(self):
        study = Study(self.store, SyntheticAdapter(), Config(max_development_tasks=1))
        original = study_module.write_json
        def interrupted(path, value):
            if Path(path).name == 'sealed.json':
                raise KeyboardInterrupt()
            return original(path, value)
        with patch.object(study_module, 'write_json', interrupted), self.assertRaises(KeyboardInterrupt):
            study.run()
        before = len([c for c in self.store.calls() if c['phase'] == 'judgment'])
        self.assertTrue(self.store.state('judgments_sealed'))
        self.assertFalse((self.store.root / 'judgments/sealed.json').exists())
        report = study.run()
        self.assertTrue(report['data_complete'])
        self.assertEqual(before, len([c for c in self.store.calls() if c['phase'] == 'judgment']))
        study.replay()

    def test_one_failed_judge_does_not_block_either_candidates_heldout(self):
        class OneFailedJudge(SyntheticAdapter):
            def infer(self, **kwargs):
                self.failure_phase = 'judgment' if kwargs['logical_id'] == 'unit-0:judge:B' else None
                return super().infer(**kwargs)
        study = Study(self.store, OneFailedJudge(), Config(target_modifications=2, max_development_tasks=2, max_sessions=50))
        report = study.run()
        self.assertEqual(report['actual_candidates'], 2)
        self.assertFalse(report['data_complete'])
        bank = self.store.get(self.store.state('judgments_sealed'))
        self.assertEqual(sum(e['response'] is None for e in bank.values()), 1)
        self.assertEqual(len(self.store.state('heldout_attempts')), 6)
        self.assertTrue(all(len(u['heldout']) == 3 for u in self.store.state('units')))
        replay = study.replay()
        self.assertEqual(len(replay['heldout_references']), 2)
        replay_rows = [json.loads(line) for line in (self.store.root / 'replay/traces.jsonl').read_text().splitlines()]
        self.assertEqual(sum(row['judge_error'] is not None for row in replay_rows), 1)
        before = len(self.store.calls())
        study.run()
        self.assertEqual(len(self.store.calls()), before)

    def test_resume_after_failed_judge_is_recorded_before_bank_seal(self):
        class InterruptedJudge(SyntheticAdapter):
            interrupted = False
            def infer(self, **kwargs):
                failing = kwargs['logical_id'] == 'unit-0:judge:B'
                self.failure_phase = 'judgment' if failing else None
                result = super().infer(**kwargs)
                if failing and not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt()
                return result
        study = Study(self.store, InterruptedJudge(), Config(max_development_tasks=1))
        with self.assertRaises(KeyboardInterrupt):
            study.run()
        self.assertIsNone(self.store.state('judgments_sealed'))
        report = study.run()
        self.assertEqual(report['stop_reason'], 'JUDGE_FAILED')
        self.assertEqual(len(self.store.state('heldout_attempts')), 3)
        self.assertEqual(len([c for c in self.store.calls() if c['phase'] == 'judgment']), 3)

    def test_call_deletion_is_detected_even_when_objects_remain(self):
        s = self.store
        s.reserve('a', 'test', {}, 1)
        s.finish('a', {'status': 'completed'})
        s.db.execute('DELETE FROM calls')
        s.db.commit()
        with self.assertRaisesRegex(ValueError, 'ledger differs'):
            s.verify()

    def test_call_status_and_state_mutations_are_detected(self):
        s = self.store
        s.reserve('a', 'test', {}, 1)
        s.db.execute("UPDATE calls SET status='completed'")
        s.db.commit()
        with self.assertRaisesRegex(ValueError, 'ledger differs'):
            s.verify()
        s.db.execute("UPDATE calls SET status='started'")
        s.set('example', 1)
        s.db.execute("UPDATE state SET value='2' WHERE key='example'")
        s.db.commit()
        with self.assertRaisesRegex(ValueError, 'state differs'):
            s.verify()

    def test_publish_refuses_corrupted_raw_log(self):
        study, candidate = self.p1a()
        s = self.store
        path = s.root / 'raw.txt'
        path.write_text('original')
        s.reserve('raw-test', 'test', {}, 100)
        s.finish('raw-test', {'status': 'completed', 'raw_files': {'raw.txt': text_hash('original')}})
        path.write_text('damaged')
        with self.assertRaisesRegex(ValueError, 'raw evidence'):
            Registry(s).publish(candidate, expected_generation=0, maintainer='test')
        self.assertEqual(Registry(s).pointer()['generation'], 0)

    def test_publish_refuses_changed_candidate_content(self):
        study, candidate = self.p1a()
        changed = {**candidate, 'candidate': asdict(Bundle(skill='different content'))}
        with self.assertRaisesRegex(ValueError, 'frozen candidate'):
            Registry(self.store).publish(changed, expected_generation=0, maintainer='test')

    def test_publish_refuses_missing_execution_ledger_row(self):
        study, candidate = self.p1a()
        self.store.db.execute("DELETE FROM calls WHERE id='unit-0:initial:candidate'")
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, 'ledger differs'):
            Registry(self.store).publish(candidate, expected_generation=0, maintainer='test')


class GatewayTests(unittest.TestCase):
    def test_loading_and_gate_errors_count_but_transport_retry_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_json(root / 'server_input.json', {'task': generate(1, 'test', 'dev')['content'],
                'contract': tool_contract(), 'bundle': asdict(Bundle()), 'max_tool_calls': 4})
            def call(i, name, arguments=None):
                return {'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':name,'arguments':arguments or {}}}
            requests = [call(1, 'list_jobs'), call(2, 'load_bundle'), call(2, 'load_bundle'),
                        call(3, 'load_bundle', {'bad': True}), call(4, 'load_bundle'), call(5, 'list_jobs')]
            run = subprocess.run([sys.executable, '-m', 'tracevolve.task_server', '--root', d],
                input='\n'.join(map(canonical, requests)), capture_output=True, text=True, check=True)
            replies = [json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual(len(replies), 5)
            self.assertEqual(replies[1], replies[2])
            result = read_json(root / 'environment_result.json')
            self.assertEqual(result['tool_calls'], 4)
            self.assertEqual(result['tool_errors'], 2)
            self.assertTrue(result['loaded'])
            self.assertEqual(len((root / 'tool_events.jsonl').read_text().splitlines()), 4)


class CalibrationTests(unittest.TestCase):
    def test_planning_profile_has_solvable_shared_goals_with_full_gateway_budget(self):
        counts = []
        for seed in range(50):
            task = generate(seed, str(seed), 'calibration', 'planning-v2')
            env = ResourceEnvironment(task['content'], max_calls=64)
            env.call('load_bundle', {}, bundle=asdict(Bundle()))
            solve(env)
            self.assertTrue(env.evaluate()['success'])
            self.assertEqual(env.errors, 0)
            self.assertEqual(len(task['content']['goals']), 3)
            counts.append(env.calls)
        self.assertGreaterEqual(min(counts), 52)
        self.assertLessEqual(max(counts), 60)

    def test_calibration_templates_excluded_from_formal_splits(self):
        calibration = plan_tasks(404501, 3, 1, 3, profile='planning-v2')
        excluded = {t['template_id'] for tasks in calibration.values() for t in tasks}
        formal = plan_tasks(504501, 4, 1, 3, excluded, 'planning-v2')
        self.assertFalse(excluded & {t['template_id'] for tasks in formal.values() for t in tasks})
