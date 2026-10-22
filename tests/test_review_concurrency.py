"""Offline regression tests for review of 7229a9b; no real auth or model IO."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_core import SyntheticAdapter
import test_codex_adapter as adapter_fixtures
from tracevolve.auth import isolated_auth
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.memory import Registry, contract_changed
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve import study as study_module
from tracevolve.tasks import tool_contract
from tracevolve.util import read_json, write_json


class BeforeSQL:
    """Deterministically interleave another connection at a SQL boundary."""
    def __init__(self, db, statement, hook):
        self.db, self.statement, self.hook = db, statement, hook

    def __getattr__(self, name):
        return getattr(self.db, name)

    def execute(self, sql, *args):
        if sql == self.statement and self.hook:
            hook, self.hook = self.hook, None
            hook()
        return self.db.execute(sql, *args)

    def __enter__(self):
        return self.db.__enter__()

    def __exit__(self, *args):
        return self.db.__exit__(*args)


def wait_file(path):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(.01)
    raise AssertionError('subprocess did not reach checkpoint: ' + str(path))


class AuthAndEventTests(unittest.TestCase):
    def setUp(self):
        # Reuse only fixture setup/helpers, not inherited test methods.
        self.fixture = adapter_fixtures.CodexTransportTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def conflict_body(self):
        return f"""
with open(os.path.join(os.environ['CODEX_HOME'], 'auth.json'), 'w') as f:
    json.dump({{'auth_mode':'chatgpt','tokens':{{'test_fixture':'rotated'}}}}, f)
with open({str(self.fixture.auth / 'auth.json')!r}, 'w') as f:
    json.dump({{'auth_mode':'chatgpt','tokens':{{'test_fixture':'external'}}}}, f)
"""

    def test_timeout_and_auth_conflict_preserve_uncertain_ledger(self):
        f = self.fixture
        adapter = f.adapter(self.conflict_body() + "print('malformed fixture line', flush=True)\ntime.sleep(10)\n")
        result = adapter.infer(store=f.store, logical_id='timeout-conflict', prompt='fixture', schema=None,
                               config=replace(f.config, timeout_seconds=1), phase='test')
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(result['error'], 'EXECUTION_TIMEOUT')
        self.assertIn('AUTH_REFRESH_CONFLICT', result['auth_cleanup_errors'][0])
        self.assertTrue(result['malformed_lines'])
        self.assertEqual(f.store.accounting()['uncertain_attempts'], ['timeout-conflict'])
        with self.assertRaisesRegex(RuntimeError, 'UNRECONCILED_EXECUTION'):
            f.store.reserve('later', 'test', {}, 32)
        self.assertEqual(read_json(f.auth / 'auth.json')['tokens']['test_fixture'], 'external')
        self.assertTrue((f.auth / '.tracevolve-auth/active/auth.json').exists())
        self.assertTrue(f.store.verify()['verified'])

    def test_cancel_and_auth_conflict_preserve_cancelled_status(self):
        f = self.fixture
        ready = f.root / 'ready'
        adapter = f.adapter(self.conflict_body() + f"open({str(ready)!r}, 'w').close()\ntime.sleep(20)\n")
        driver = f"""
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.store import Store
s=Store({str(f.store.root)!r})
try:
    CodexAdapter({adapter.executable!r}).infer(store=s, logical_id='cancel-conflict', prompt='fixture', schema=None,
        config=Config(auth_home={str(f.auth)!r}), phase='test')
except KeyboardInterrupt:
    raise SystemExit(130)
finally:
    s.close()
"""
        proc = subprocess.Popen([sys.executable, '-c', driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_file(ready)
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 130)
            call = f.store.calls()[0]
            result = f.store.get(call['result_hash'])
            self.assertEqual(call['status'], 'uncertain')
            self.assertEqual(result['error'], 'CANCELLED')
            self.assertIn('AUTH_REFRESH_CONFLICT', result['auth_cleanup_errors'][0])
            with self.assertRaisesRegex(RuntimeError, 'UNRECONCILED_EXECUTION'):
                f.store.reserve('later', 'test', {}, 32)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_collaboration_event_variants_cannot_be_success(self):
        f = self.fixture
        for index, kind in enumerate(('collab_tool_call', 'collab_agent_tool_call')):
            with self.subTest(kind=kind):
                adapter = f.adapter(f"""
print(json.dumps({{'type':'item.completed','item':{{'type':{kind!r},'tool':'spawn_agent','status':'completed'}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
""")
                result = adapter.infer(store=f.store, logical_id='collab-' + str(index), prompt='fixture',
                                       schema=None, config=f.config, phase='test')
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error'], 'UNEXPECTED_TOOL_CAPABILITY')
                self.assertEqual(len(result['capability_violations']), 1)

    def test_killed_host_keeps_exec_child_lease_across_studies(self):
        self._orphan_case('exec')

    def test_killed_host_keeps_audit_child_lease_across_studies(self):
        self._orphan_case('debug')

    def _orphan_case(self, stage):
        f = self.fixture
        ready, release, done = [f.root / name for name in ('child.pid', 'release', 'done')]
        executable = f.root / 'orphan-codex'
        executable.write_text(f"""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('codex-cli 0.163.0'); sys.exit()
stage = 'debug' if 'debug' in sys.argv else 'exec'
if stage == 'exec':
    prompt = sys.stdin.read()
if stage == {stage!r} and not Path({str(done)!r}).exists():
    Path({str(ready)!r}).write_text(str(os.getpid()))
    while not Path({str(release)!r}).exists():
        time.sleep(.01)
    Path(os.environ['CODEX_HOME'], 'auth.json').write_text(json.dumps({{'auth_mode':'chatgpt','tokens':{{'test_fixture':'orphan-rotation'}}}}))
    Path({str(done)!r}).touch()
if stage == 'debug':
    print('[]')
else:
    print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
""")
        executable.chmod(0o700)
        driver = f"""
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.store import Store
s=Store({str(f.root / 'old-study')!r})
CodexAdapter({str(executable)!r}).infer(store=s,logical_id='old',prompt='old',schema=None,
    config=Config(auth_home={str(f.auth)!r}),phase='test')
"""
        host = subprocess.Popen([sys.executable, '-c', driver], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child = None
        try:
            wait_file(ready)
            child = int(ready.read_text())
            host.kill()
            host.wait(timeout=5)
            os.kill(child, 0)
            before = (f.auth / '.tracevolve-auth/active/auth.json').read_bytes()
            result = CodexAdapter(str(executable)).infer(store=f.store, logical_id='other-study', prompt='new',
                schema=None, config=f.config, phase='test')
            self.assertEqual(result['status'], 'failed')
            self.assertIn('AUTH_IN_USE', result['error'])
            self.assertEqual((f.auth / '.tracevolve-auth/active/auth.json').read_bytes(), before)
            self.assertFalse(list((f.store.root / 'executions').glob('*/launch.json')))
            release.touch()
            wait_file(done)
            # Acquiring the shared flock confirms every inheriting child has
            # closed its descriptor. Do not trust only a PID file or host death.
            deadline = time.monotonic() + 5
            while True:
                try:
                    with isolated_auth(f.auth) as isolated:
                        self.assertEqual(read_json(isolated / 'auth.json')['tokens']['test_fixture'], 'orphan-rotation')
                    break
                except ValueError as exc:
                    if 'AUTH_IN_USE' not in str(exc) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.01)
            completed = CodexAdapter(str(executable)).infer(store=f.store, logical_id='after-exit', prompt='new',
                schema=None, config=f.config, phase='test')
            self.assertEqual(completed['status'], 'completed')
            self.assertFalse((f.auth / '.tracevolve-auth/active').exists())
        finally:
            if host.poll() is None:
                host.kill()
                host.wait()
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass


class SnapshotAndPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'study')
        self.other = Store(self.store.root)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.other.close()
        self.store.close()
        self.tmp.cleanup()

    def test_missing_manifest_restored_and_usable_as_prior_study(self):
        study = Study(self.store, SyntheticAdapter(), Config(max_development_tasks=1))
        original = study_module.write_json
        def interrupt(path, value):
            if Path(path).name == 'manifest.json':
                raise KeyboardInterrupt()
            return original(path, value)
        with patch.object(study_module, 'write_json', interrupt), self.assertRaises(KeyboardInterrupt):
            study.run()
        self.assertTrue(self.store.state('frozen'))
        self.assertFalse((self.store.root / 'manifest.json').exists())
        report = study.run()
        self.assertTrue(report['data_complete'])
        self.assertEqual(read_json(self.store.root / 'manifest.json'), self.store.get(self.store.state('frozen')))
        next_store = Store(Path(self.tmp.name) / 'next')
        try:
            future = Study(next_store, SyntheticAdapter(), Config(max_development_tasks=1,
                prior_studies=(str(self.store.root),))).freeze()
            old = self.store.get(self.store.state('frozen'))
            seen = {task['template_id'] for tasks in old['tasks'].values() for task in tasks}
            self.assertFalse(seen & {task['template_id'] for tasks in future['tasks'].values() for task in tasks})
        finally:
            next_store.close()

    def test_existing_modified_manifest_is_rejected_not_overwritten(self):
        study = Study(self.store, SyntheticAdapter(), Config(max_development_tasks=1))
        frozen = study.freeze()
        modified = {**frozen, 'phase': 'modified'}
        write_json(self.store.root / 'manifest.json', modified)
        with self.assertRaisesRegex(ValueError, 'manifest file differs'):
            study.freeze()
        self.assertEqual(read_json(self.store.root / 'manifest.json'), modified)

    def test_concurrent_invalidation_before_publish_cannot_be_reactivated(self):
        Study(self.store, SyntheticAdapter(), Config(phase='p1a', max_development_tasks=1)).run()
        candidate = self.store.state('candidates')[0]
        self.store.db = BeforeSQL(self.store.db, 'BEGIN IMMEDIATE',
            lambda: contract_changed(self.other, tool_contract('resource-v2')))
        with self.assertRaisesRegex(ValueError, 'permit is not pending'):
            Registry(self.store).publish(candidate, expected_generation=0, maintainer='offline test')
        self.assertEqual(self.store.state('permit')['status'], 'needs_revalidation')
        self.assertTrue(self.store.state('permit')['change'])
        self.assertEqual(Registry(self.store).pointer()['generation'], 0)
        self.assertTrue(self.store.verify()['verified'])

    def test_concurrent_decision_change_before_publish_is_rechecked(self):
        Study(self.store, SyntheticAdapter(), Config(phase='p1a', max_development_tasks=1)).run()
        candidate = self.store.state('candidates')[0]
        def invalidate():
            decision = self.other.state('release_decision')
            self.other.set('release_decision', {**decision, 'publish_eligible': False})
        self.store.db = BeforeSQL(self.store.db, 'BEGIN IMMEDIATE', invalidate)
        with self.assertRaisesRegex(ValueError, 'lacks passing release evidence'):
            Registry(self.store).publish(candidate, expected_generation=0, maintainer='offline test')
        self.assertEqual(Registry(self.store).pointer()['generation'], 0)
        self.assertTrue(self.store.verify()['verified'])

    def test_verify_uses_one_snapshot_during_concurrent_state_write(self):
        self.store.set('example', 1)
        old_head = self.store.verify()['chain_head']
        self.store.db = BeforeSQL(self.store.db, 'SELECT * FROM state', lambda: self.other.set('example', 2))
        report = self.store.verify()
        self.assertTrue(report['verified'])
        self.assertEqual(report['chain_head'], old_head)
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(self.store.state('example'), 2)
        self.assertNotEqual(self.store.verify()['chain_head'], old_head)

    def test_verify_uses_one_snapshot_during_concurrent_call_finish(self):
        self.store.reserve('a', 'test', {}, 2)
        self.store.db = BeforeSQL(self.store.db, 'SELECT * FROM calls ORDER BY rowid',
            lambda: self.other.finish('a', {'status':'completed'}))
        self.assertTrue(self.store.verify()['verified'])
        self.assertEqual(self.store.calls()[0]['status'], 'completed')
        self.assertTrue(self.store.verify()['verified'])

    def test_verify_does_not_commit_or_rollback_owning_write_transaction(self):
        s = self.store
        s.db.execute('BEGIN IMMEDIATE')
        s.db.execute("INSERT INTO state VALUES('pending','1')")
        s.event('state_changed', {'key':'pending', 'value':1})
        self.assertTrue(s.verify()['verified'])
        self.assertTrue(s.db.in_transaction)
        self.assertIsNone(self.other.state('pending'))
        s.db.rollback()
        self.assertIsNone(s.state('pending'))
        s.db.execute('BEGIN IMMEDIATE')
        s.db.execute("INSERT INTO state VALUES('unaudited','1')")
        with self.assertRaisesRegex(ValueError, 'state differs'):
            s.verify()
        self.assertTrue(s.db.in_transaction)
        self.assertIsNone(self.other.state('unaudited'))
        s.db.rollback()
        self.assertTrue(s.verify()['verified'])
