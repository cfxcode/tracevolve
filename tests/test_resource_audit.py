import copy
import importlib.util
import unittest
from dataclasses import asdict
from pathlib import Path

from tracevolve.contracts import Bundle
from tracevolve.tasks import ResourceEnvironment, generate, tool_contract
from tracevolve.util import digest, source_manifest

spec = importlib.util.spec_from_file_location("resource_audit", Path(__file__).resolve().parents[1] / "scripts/audit_resource_runs.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ResourceAuditTests(unittest.TestCase):
    def test_valid_behavior_failure_is_preserved_and_fabricated_success_rejected(self):
        task = generate(5, "test", "development")["content"]
        bundle = asdict(Bundle())
        env = ResourceEnvironment(task)
        args = {"goal_ids": task["goals"]}
        output = env.call("submit", args)
        request = {"implementation": source_manifest(), "server_input": {"task": task, "bundle": bundle,
                   "contract": tool_contract(), "max_tool_calls": 64}}
        result = {"status": "completed", "evaluation": env.evaluate(), "tool_events": [
            {"name": "load_bundle", "arguments": {}, "output": {"bundle": bundle, "bundle_hash": digest(bundle)}},
            {"name": "submit", "arguments": args, "output": output}]}
        audit = module.replay_run(request, result)
        self.assertTrue(audit["checked"])
        self.assertFalse(audit["success"])
        damaged = copy.deepcopy(result)
        damaged["tool_events"][-1]["output"] = {"submitted": True}
        with self.assertRaisesRegex(ValueError, "state transitions"):
            module.replay_run(request, damaged)

    def test_missing_trace_cannot_support_success(self):
        task = generate(2, "test", "development")["content"]
        request = {"implementation": source_manifest(), "server_input": {"task": task, "bundle": asdict(Bundle()),
                   "contract": tool_contract(), "max_tool_calls": 64}}
        with self.assertRaisesRegex(ValueError, "loading receipt"):
            module.replay_run(request, {"status": "completed", "tool_events": [], "evaluation": {"success": True}})
