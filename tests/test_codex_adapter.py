import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.contracts import Bundle
from tracevolve.store import Store
from tracevolve.tasks import generate, tool_contract


class CodexTransportTests(unittest.TestCase):
    """Exercise the real process adapter against an explicit CLI test double."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.auth = self.root / "auth"
        self.auth.mkdir()
        (self.auth / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"test_fixture": True}}))
        self.store = Store(self.root / "study")
        self.config = Config(auth_home=str(self.auth))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def adapter(self, body, context="[]"):
        executable = self.root / "fake-codex"
        executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f"""
import json, sys, os, time
if '--version' in sys.argv:
    print('codex-cli 0.163.0'); sys.exit()
if 'debug' in sys.argv:
    if any('mcp_servers.' in arg for arg in sys.argv):
        sys.stderr.write('instruction audit must not start the task MCP'); sys.exit(1)
    print({context!r}); sys.exit()
prompt = sys.stdin.read()
""") + textwrap.dedent(body))
        executable.chmod(0o700)
        return CodexAdapter(str(executable))

    def test_json_inference_usage_and_credentials_environment(self):
        adapter = self.adapter("""
print(json.dumps({'type':'thread.started','thread_id':'test-thread'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'api_env_present': bool(os.environ.get('OPENAI_API_KEY'))})}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':3}}))
""")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-do-not-forward"}):
            result = adapter.infer(store=self.store, logical_id="test", prompt="test", schema={"type": "object"}, config=self.config, phase="judgment")
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["response"]["api_env_present"])
        self.assertEqual(result["usage"]["input_tokens"], 10)
        self.assertIsNone(result["cost_usd"])
        self.assertTrue(self.store.verify()["verified"])

    def test_real_subprocess_rotation_survives_timeout(self):
        adapter = self.adapter("""
auth = os.path.join(os.environ['CODEX_HOME'], 'auth.json')
with open(auth, 'w') as f:
    json.dump({'auth_mode':'chatgpt','tokens':{'test_fixture':'rotated'}}, f)
time.sleep(10)
""")
        result = adapter.infer(store=self.store, logical_id="rotate-timeout", prompt="test", schema=None,
                               config=replace(self.config, timeout_seconds=1), phase="test")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(json.loads((self.auth / "auth.json").read_text())["tokens"]["test_fixture"], "rotated")
        self.assertFalse((self.auth / ".tracevolve-auth/active").exists())
        self.assertNotIn('test_fixture', json.dumps(result))

    def test_timeout_is_uncertain_and_no_automatic_retry(self):
        adapter = self.adapter("time.sleep(10)\n")
        result = adapter.infer(store=self.store, logical_id="timeout", prompt="test", schema=None,
                               config=replace(self.config, timeout_seconds=1), phase="test")
        self.assertEqual(result["status"], "uncertain")
        with self.assertRaises(RuntimeError):
            adapter.infer(store=self.store, logical_id="new", prompt="test", schema=None, config=self.config, phase="test")

    def test_sigterm_terminates_child_and_preserves_uncertain_ledger(self):
        child_pid = self.root / "child.pid"
        adapter = self.adapter(f"open({str(child_pid)!r}, 'w').write(str(os.getpid()))\ntime.sleep(20)\n")
        other_store = self.root / "signal-study"
        driver = textwrap.dedent(f"""
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.store import Store
s=Store({str(other_store)!r})
try:
    CodexAdapter({adapter.executable!r}).infer(store=s, logical_id='signal-test', prompt='test', schema=None,
        config=Config(auth_home={str(self.auth)!r}), phase='test')
except KeyboardInterrupt:
    raise SystemExit(130)
finally:
    s.close()
""")
        proc = subprocess.Popen([sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(child_pid.exists())
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 130)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
            saved = Store(other_store)
            try:
                self.assertEqual(saved.calls()[0]["status"], "uncertain")
                self.assertTrue(saved.get(saved.calls()[0]["result_hash"])["raw_files"])
                self.assertTrue(saved.verify()["verified"])
            finally:
                saved.close()
        finally:
            if proc.poll() is None:
                proc.kill(); proc.communicate()

    def test_success_without_actual_skill_delivery_fails(self):
        adapter = self.adapter("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1}}))\n")
        result = adapter.execute(store=self.store, logical_id="test", bundle=Bundle(skill="A skill"),
                                 task=generate(2, "test", "development"), contract=tool_contract(), config=self.config, phase="test")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "MISSING_ACTUAL_LOAD_EVIDENCE")
        self.assertNotIn("evaluation", result)

    def test_foreign_tool_in_judgment_is_invalid(self):
        adapter = self.adapter("""
print(json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','server':'foreign','tool':'read_file'}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
""")
        result = adapter.infer(store=self.store, logical_id="test", prompt="test", schema=None, config=self.config, phase="judgment")
        self.assertEqual(result["error"], "UNEXPECTED_TOOL_CAPABILITY")

    def test_personal_skill_context_blocks_before_exec(self):
        context = json.dumps([{"content": [{"text": "- `r0` = `/personal/skills`\n- private: skill (file: r0/private/SKILL.md)"}]}])
        adapter = self.adapter("raise RuntimeError('must not launch exec')\n", context=context)
        result = adapter.infer(store=self.store, logical_id="test", prompt="test", schema=None, config=self.config, phase="judgment")
        self.assertIn("unexpected host Skill", result["error"])
        self.assertFalse(any((self.store.root / "executions").rglob("launch.json")))


if __name__ == "__main__":
    unittest.main()
