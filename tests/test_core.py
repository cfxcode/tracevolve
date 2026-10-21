import copy
import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.contracts import Bundle, ExecutionSnapshot, Patch, frozen_harness
from tracevolve.legacy import contracts, pilot
from tracevolve.memory import Registry, applicability, contract_changed, selected_bundle
from tracevolve.recorded_judge import lookup
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve.tasks import ResourceEnvironment, generate, plan_tasks, rename, semantic_identity, tool_contract
from tracevolve.util import canonical, digest, read_json, write_json


def solve(env):
    """TEST ONLY. A reference solver is never imported by the live runtime."""
    visited = set()
    def visit(i):
        if i in visited:
            return
        job = env.call("inspect_job", {"job_id": i})
        for parent in job["requires"]:
            visit(parent)
        mode = env.contract["checked_mode"] if job["payload"]["requires_check"] else "standard"
        env.call("run_job", {"job_id": i, "mode": mode})
        visited.add(i)
    for goal in env.task["goals"]:
        visit(goal)
    env.call("submit", {"goal_ids": env.task["goals"]})


class SyntheticAdapter:
    """Explicitly synthetic fixture. Never a fallback for CodexAdapter."""
    synthetic = True
    def __init__(self, no_failures=False, failure_phase=None):
        self.no_failures, self.failure_phase = no_failures, failure_phase
        self.order = []

    def runtime_identity(self):
        return {"adapter": "synthetic-test-only", "synthetic": True}

    def preflight(self, config):
        return {"synthetic": True}

    def snapshot(self, bundle, contract, config):
        return ExecutionSnapshot(bundle.hash, self.runtime_identity(), contract, {}, {"max_tool_calls": config.max_tool_calls})

    def execute(self, *, store, logical_id, bundle, task, contract, config, phase):
        request = {"bundle": asdict(bundle), "task": task, "contract": contract}
        cached = store.reserve(logical_id, phase, request, config.max_sessions)
        if cached:
            return cached
        self.order.append(phase)
        env = ResourceEnvironment(task["content"], contract)
        if self.no_failures or bundle != Bundle():
            solve(env)
        else:
            env.call("submit", {"goal_ids": task["content"]["goals"]})
        snapshot = self.snapshot(bundle, contract, config)
        result = {"run_id": logical_id, "status": "completed", "synthetic": True,
                  "execution_origin": "synthetic_test", "cost_usd": None, "usage": {"input_tokens": 2, "output_tokens": 1},
                  "elapsed_seconds": .01, "harness": frozen_harness(bundle, snapshot),
                  "task_sha256": task["sha256"], "evaluation": env.evaluate(), "tool_events": [],
                  "snapshot_hash": snapshot.hash, "execution_snapshot": asdict(snapshot)}
        if phase == self.failure_phase:
            result.update(status="failed", error="INJECTED_TEST_FAILURE")
            result.pop("evaluation")
        store.finish(logical_id, result)
        return result

    def infer(self, *, store, logical_id, prompt, schema, config, phase):
        cached = store.reserve(logical_id, phase, {"prompt": prompt, "schema": schema}, config.max_sessions)
        if cached:
            return cached
        self.order.append(phase)
        response = {"eligible": True, "content": "Inspect prerequisites, execute in dependency order, then submit. Scope: resource jobs only.",
                    "hypothesis": "Check prerequisites before submission", "rationale": "Observed failed submission"} if phase == "proposal" else {
                    "accept": True, "estimated_gain": 1., "rationale": "SYNTHETIC TEST DECISION"}
        result = {"run_id": logical_id, "status": "completed", "response": response,
                  "synthetic": True, "cost_usd": None, "elapsed_seconds": .01}
        if phase == self.failure_phase:
            result.update(status="failed", error="INJECTED_TEST_FAILURE")
            result.pop("response")
        return store.finish(logical_id, result)


class EnvironmentTests(unittest.TestCase):
    def test_solvable_within_budget_and_isolated(self):
        for seed in range(40):
            task = generate(seed, str(seed), "development")
            env = ResourceEnvironment(task["content"])
            solve(env)
            self.assertTrue(env.evaluate()["success"])
            self.assertLessEqual(env.calls, 64)
            self.assertFalse(ResourceEnvironment(task["content"]).evaluate()["success"])

    def test_invalid_actions_do_not_mutate(self):
        task = generate(10, "x", "development")["content"]
        env = ResourceEnvironment(task)
        for name, args in [("run_job", {"job_id": [], "mode": "checked"}),
                           ("run_job", {"job_id": "absent", "mode": "checked"}),
                           ("run_job", {"job_id": task["goals"][0], "mode": "wrong"}),
                           ("submit", {"goal_ids": task["goals"] * 2})]:
            self.assertIn("error", env.call(name, args))
            self.assertEqual(env.done, set())

    def test_dependency_error_and_duplicate_execution(self):
        task = generate(3, "x", "development")["content"]
        env = ResourceEnvironment(task)
        goal = env.jobs[task["goals"][0]]
        mode = "checked" if goal["payload"]["requires_check"] else "standard"
        self.assertEqual(env.call("run_job", {"job_id": goal["id"], "mode": mode})["error"], "DEPENDENCIES_NOT_DONE")
        root = next(j for j in task["jobs"] if not j["requires"])
        args = {"job_id": root["id"], "mode": "checked" if root["payload"]["requires_check"] else "standard"}
        env.call("run_job", args)
        self.assertEqual(env.call("run_job", args)["status"], "already_done")
        self.assertEqual(len(env.done), 1)

    def test_strict_payload_types(self):
        task = generate(1, "x", "development")["content"]
        task["jobs"][0]["payload"]["requires_check"] = 1
        with self.assertRaises(ValueError):
            ResourceEnvironment(task)

    def test_renaming_action_equivariance(self):
        task = generate(4, "x", "development")
        other, mapping = rename(task, 123)
        self.assertTrue(set(mapping).isdisjoint(mapping.values()))
        self.assertEqual(semantic_identity(task["content"]), semantic_identity(other["content"]))
        a, b = ResourceEnvironment(task["content"]), ResourceEnvironment(other["content"])
        def transform(value):
            if isinstance(value, str):
                return mapping.get(value, value)
            if isinstance(value, list):
                return [transform(x) for x in value]
            if isinstance(value, dict):
                return {k: transform(v) for k, v in value.items()}
            return value
        actions = [("list_jobs", {}), ("run_job", {"job_id": task["content"]["goals"][0], "mode": "checked"}),
                   ("inspect_job", {"job_id": "missing"}), ("submit", {"goal_ids": task["content"]["goals"] * 2})]
        actions += [("inspect_job", {"job_id": j["id"]}) for j in task["content"]["jobs"]]
        for name, args in actions:
            self.assertEqual(transform(a.call(name, args)), b.call(name, transform(args)))
        solve(a); solve(b)
        self.assertEqual(transform(a.evaluate()), b.evaluate())

    def test_template_partition_covers_previous_study(self):
        first = plan_tasks(100, 6, 5, 5)
        seen = [t["template_id"] for group in first.values() for t in group]
        self.assertEqual(len(seen), len(set(seen)))
        second = plan_tasks(101, 6, 5, 5, seen)
        self.assertFalse(set(seen) & {t["template_id"] for group in second.values() for t in group})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_completed_request_not_reexecuted(self):
        s = self.store
        self.assertIsNone(s.reserve("a", "test", {"input": 1}, 1))
        result = {"status": "completed", "cost_usd": None}
        s.finish("a", result)
        self.assertEqual(s.reserve("a", "test", {"input": 1}, 1), result)
        with self.assertRaises(ValueError):
            s.reserve("a", "test", {"input": 2}, 1)
        with self.assertRaises(RuntimeError):
            s.reserve("b", "test", {}, 1)
        self.assertTrue(s.verify()["verified"])

    def test_pending_request_blocks_restart(self):
        s = self.store
        s.reserve("a", "test", {}, 10)
        with self.assertRaises(RuntimeError):
            s.reserve("b", "test", {}, 10)
        self.assertEqual(s.accounting()["uncertain_attempts"], ["a"])

    def test_uncertain_terminal_blocks_new_request(self):
        s = self.store
        s.reserve("a", "test", {}, 10)
        s.finish("a", {"status": "uncertain", "cost_usd": None})
        with self.assertRaises(RuntimeError):
            s.reserve("b", "test", {}, 10)

    def test_unknown_dollars_are_not_zero(self):
        s = self.store
        s.reserve("a", "test", {}, 10)
        s.finish("a", {"status": "completed", "cost_usd": None, "usage": {"input_tokens": 5}})
        report = s.accounting()
        self.assertIsNone(report["cost_usd"])
        self.assertFalse(report["accounting_complete"])
        self.assertIsNone(report["budget_compliant"])
        self.assertEqual(report["tokens_observed"]["input_tokens"], 5)

    def test_append_only_and_tamper_detection(self):
        s = self.store
        s.set("key", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            s.db.execute("DELETE FROM events")
        ref = s.put({"x": 1})
        write_json(s.root / "objects" / (ref + ".json"), {"x": 2})
        with self.assertRaises(ValueError):
            s.verify()


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.config = Config(max_development_tasks=2, max_sessions=36)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_p0_end_to_end_and_exact_sealed_replay(self):
        adapter = SyntheticAdapter()
        study = Study(self.store, adapter, self.config)
        report = study.run()
        self.assertTrue(report["data_complete"])
        self.assertTrue(report["target_met"])
        self.assertFalse(report["accounting"]["accounting_complete"])
        self.assertEqual(adapter.order.count("validation"), 6)
        self.assertEqual(adapter.order.count("sealed_evaluation"), 6)
        self.assertLess(max(i for i, x in enumerate(adapter.order) if x == "judgment"), adapter.order.index("sealed_evaluation"))
        data = read_json(study.export())
        settings = pilot.validate_data(data, True)
        bank = self.store.get(self.store.state("judgments_sealed"))
        for entry in bank.values():
            self.assertEqual(lookup(bank, entry["request"]), entry["response"])
            self.assertNotIn("heldout", canonical(entry["request"]))
        with self.assertRaises(ValueError):
            lookup(bank, {"unknown": True})
        self.assertTrue(pilot.validate_data(data, False)["any_synthetic"])
        replay = study.replay()
        self.assertEqual(replay["input_sha256"], pilot.digest(data))
        before = len(self.store.calls())
        study.run()
        self.assertEqual(len(self.store.calls()), before)

    def test_no_failures_does_not_fabricate_candidates(self):
        study = Study(self.store, SyntheticAdapter(no_failures=True), self.config)
        report = study.run()
        self.assertEqual(report["actual_candidates"], 0)
        self.assertEqual(report["stop_reason"], "NO_ELIGIBLE_CANDIDATE")
        with self.assertRaises(ValueError):
            study.export()

    def test_judge_failure_not_rejection_and_no_heldout(self):
        adapter = SyntheticAdapter(failure_phase="judgment")
        report = Study(self.store, adapter, self.config).run()
        self.assertFalse(report["data_complete"])
        self.assertEqual(report["stop_reason"], "JUDGE_FAILED")
        self.assertNotIn("sealed_evaluation", adapter.order)
        bank = self.store.get(self.store.state("judgments_sealed"))
        self.assertTrue(all(x["response"] is None for x in bank.values()))

    def test_heldout_failure_not_silently_dropped(self):
        report = Study(self.store, SyntheticAdapter(failure_phase="sealed_evaluation"), self.config).run()
        self.assertFalse(report["data_complete"])
        self.assertEqual(len(self.store.state("heldout_attempts")), 3)
        self.assertEqual(self.store.state("units")[0]["heldout"], [])

    def test_freeze_detects_configuration_drift(self):
        adapter = SyntheticAdapter()
        Study(self.store, adapter, self.config).freeze()
        with self.assertRaises(ValueError):
            Study(self.store, adapter, replace(self.config, seed=999)).freeze()

    def test_export_refuses_partial_candidate_coverage(self):
        study = Study(self.store, SyntheticAdapter(), self.config)
        study.run()
        self.store.set("candidates", self.store.state("candidates") * 2)
        with self.assertRaisesRegex(ValueError, "candidate coverage"):
            study.export()

    def test_replay_refuses_modified_sealed_file(self):
        study = Study(self.store, SyntheticAdapter(), self.config)
        study.run()
        bank_path = self.store.root / "judgments/sealed.json"
        bank = read_json(bank_path)
        next(iter(bank.values()))["response"]["accept"] = False
        write_json(bank_path, bank)
        with self.assertRaisesRegex(ValueError, "immutable evidence"):
            study.replay()

    def test_bad_judge_gain_is_failure_not_a_decision(self):
        class InvalidJudge(SyntheticAdapter):
            def infer(self, **kwargs):
                result = super().infer(**kwargs)
                if kwargs["phase"] == "judgment":
                    result["response"]["estimated_gain"] = "not-a-number"
                return result
        report = Study(self.store, InvalidJudge(), self.config).run()
        self.assertFalse(report["data_complete"])
        self.assertEqual(report["stop_reason"], "JUDGE_FAILED")

    def test_p1a_skill_experiment_and_contract_change(self):
        config = replace(self.config, phase="p1a")
        study = Study(self.store, SyntheticAdapter(), config)
        pending = study.run()
        self.assertEqual(pending["phase"], "AWAITING_MAINTAINER")
        self.assertIsNone(pending["lifecycle"])
        self.assertEqual(len(self.store.state("heldout_attempts", {})), 0)
        Registry(self.store).publish(self.store.state("candidates")[0], expected_generation=0, maintainer="test maintainer")
        report = study.run()
        lifecycle = report["lifecycle"]
        self.assertTrue(lifecycle["revalidation_complete"])
        self.assertTrue(lifecycle["skill_paused_for_new_contract"])
        self.assertTrue(lifecycle["selection"]["skill_enabled"])
        self.assertTrue(lifecycle["was_published_before_change"])
        self.assertFalse(self.store.state("release_decision")["publish_eligible"])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            Study(self.store, SyntheticAdapter(), Config(phase="p1a", max_development_tasks=1)).run()
        self.candidate = self.store.state("candidates")[0]
        self.permit = self.store.state("permit")

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_publish_parent_cas_and_rollback(self):
        r = Registry(self.store)
        with self.assertRaises(ValueError):
            r.publish(self.candidate, expected_generation=1, maintainer="tester")
        r.publish(self.candidate, expected_generation=0, maintainer="tester")
        bundle, selection = selected_bundle(self.store, self.candidate, tool_contract())
        self.assertTrue(selection["skill_enabled"])
        self.assertIsNotNone(bundle.skill)
        r.rollback(expected_generation=1, reason="regression")
        self.assertFalse(selected_bundle(self.store, self.candidate, tool_contract())[1]["skill_enabled"])

    def test_unknown_precondition_denies_injection(self):
        permit = {**self.permit, "status": "active"}
        self.assertEqual(applicability(permit, task_family=permit["task_family"], contract={}, bundle_hash=permit["bundle_hash"])["result"], "unknown")
        self.assertEqual(applicability(permit, task_family="other", contract=tool_contract(), bundle_hash=permit["bundle_hash"])["result"], "inapplicable")

    def test_active_skill_suspends_on_contract_change(self):
        Registry(self.store).publish(self.candidate, expected_generation=0, maintainer="tester")
        change = contract_changed(self.store, tool_contract("resource-v2"))
        self.assertEqual(change["previous_status"], "active")
        self.assertEqual(self.store.state("permit")["status"], "needs_revalidation")
        self.assertFalse(selected_bundle(self.store, self.candidate, tool_contract("resource-v2"))[1]["skill_enabled"])

    def test_no_permission_from_global_active_only(self):
        self.assertEqual(applicability({"status": "active"}, task_family="x", contract={}, bundle_hash="x")["result"], "inapplicable")

    def test_publish_rechecks_evidence_not_just_a_boolean(self):
        decision = self.store.state("release_decision")
        self.store.set("release_decision", {**decision, "evidence_refs": []})
        with self.assertRaisesRegex(ValueError, "missing or inconsistent"):
            Registry(self.store).publish(self.candidate, expected_generation=0, maintainer="tester")


class MCPTests(unittest.TestCase):
    def test_server_stops_at_resource_call_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = generate(5, "x", "development")
            write_json(root / "server_input.json", {"task": task["content"], "contract": tool_contract(),
                       "max_tool_calls": 1, "bundle": asdict(Bundle())})
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "load_bundle", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "submit", "arguments": {"goal_ids": task["content"]["goals"]}}}]
            process = subprocess.run([sys.executable, "-m", "tracevolve.task_server", "--root", d],
                input="\n".join(map(canonical, requests)), capture_output=True, text=True, check=True)
            self.assertEqual(len(process.stdout.splitlines()), 2)
            self.assertEqual(read_json(root / "environment_result.json")["tool_calls"], 1)
            self.assertFalse(read_json(root / "environment_result.json")["success"])

    def test_real_stdio_server_load_gate_and_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = generate(5, "x", "development")
            write_json(root / "server_input.json", {"task": task["content"], "contract": tool_contract(),
                       "max_tool_calls": 64, "bundle": asdict(Bundle(skill="Test skill"))})
            requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {}}},
                        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "load_bundle", "arguments": {}}},
                        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {}}}]
            run = subprocess.run([sys.executable, "-m", "tracevolve.task_server", "--root", d],
                                 input="\n".join(map(canonical, requests)), capture_output=True, text=True, check=True)
            responses = [json.loads(x) for x in run.stdout.splitlines()]
            self.assertIn("LOAD_BUNDLE_REQUIRED", responses[1]["result"]["content"][0]["text"])
            self.assertIn("Test skill", responses[2]["result"]["content"][0]["text"])
            self.assertTrue(read_json(root / "environment_result.json")["loaded"])
            self.assertEqual(len((root / "tool_events.jsonl").read_text().splitlines()), 3)
            restart = subprocess.run([sys.executable, "-m", "tracevolve.task_server", "--root", d],
                                     input="", capture_output=True, text=True)
            self.assertNotEqual(restart.returncode, 0)
            self.assertEqual(len((root / "tool_events.jsonl").read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
