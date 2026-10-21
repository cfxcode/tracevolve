"""Program-integrity checks using fictional evidence, never research results."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pilot
from synthetic_fixture import upgrade_synthetic_fixture


def fixture(legacy=False):
    """All observations and dollar figures in this fixture are fabricated."""
    def event(event_id, kind, amount, valid=True):
        return {
            "event_id": event_id,
            "type": kind,
            "observation": {"description": "synthetic tool-routing fixture"},
            "cost_usd": amount,
            "synthetic": True,
            "valid": valid,
        }

    data = {
        "schema_version": 1,
        "additional_budget_usd": 0.50,
        "shared_overheads": {"candidate_generation_usd": 0.10, "calibration_usd": 0.0},
        "decision": {
            "delta": 0.05,
            "indifference": 0.01,
            "alpha": 0.05,
            "min_heldout_pairs": 3,
            "utility_bounds": [0, 1],
        },
        "units": [{
            "unit_id": "unit-test-only",
            "baseline_id": "fixed-baseline",
            "patch_id": "fixed-patch",
            "patch_description": "Synthetic candidate description for an integrity test.",
            "initial_evidence": [event("opaque-101", "full_validation", 0.10)],
            "extra_B": [event("opaque-202", "full_validation", 0.20)],
            "extra_C": [event("opaque-303", "diagnostic_identifier_renaming", 0.20)],
            "heldout": [{
                "task_id": "PRIVATE_HELDOUT_SENTINEL",
                "synthetic": True,
                "baseline": {
                    "utility": 0.2,
                    "execution_cost_usd": 0.03,
                    "private_marker": "DO_NOT_SHOW_TO_JUDGE",
                },
                "candidate": {"utility": 0.3, "execution_cost_usd": 0.04},
            }],
        }],
    }
    return data if legacy else upgrade_synthetic_fixture(data)


def read_traces(directory):
    return [json.loads(line) for line in (directory / "traces.jsonl").read_text().splitlines()]


class PilotIntegrityTests(unittest.TestCase):
    def run_demo(self, data):
        directory = tempfile.TemporaryDirectory(prefix="pilot-integrity-")
        self.addCleanup(directory.cleanup)
        output = Path(directory.name) / "output"
        summary = pilot.run(data, output, command=None, demo=True)
        return summary, read_traces(output)

    def test_heldout_never_enters_serialized_judge_request(self):
        data = fixture()
        _, traces = self.run_demo(data)
        for row in traces:
            serialized = json.dumps(row["judge_request"])
            self.assertNotIn("PRIVATE_HELDOUT_SENTINEL", serialized)
            self.assertNotIn("DO_NOT_SHOW_TO_JUDGE", serialized)
            self.assertNotIn('"heldout"', serialized)
            self.assertNotIn('"reference_label"', serialized)

    def test_group_labels_and_unlisted_event_metadata_are_not_exposed(self):
        data = fixture()
        unit = data["units"][0]
        unit["group"] = "PRIVATE_GROUP_SENTINEL"
        for channel in ("initial_evidence", "extra_B", "extra_C"):
            for event in unit[channel]:
                event["group"] = "PRIVATE_GROUP_SENTINEL"
                event["private_reference_label"] = "PRIVATE_LABEL_SENTINEL"
        _, traces = self.run_demo(data)
        for row in traces:
            request = row["judge_request"]
            self.assertNotIn("group", request)
            self.assertNotIn("extra_B", request)
            self.assertNotIn("extra_C", request)
            self.assertNotIn("PRIVATE_GROUP_SENTINEL", json.dumps(request))
            self.assertNotIn("PRIVATE_LABEL_SENTINEL", json.dumps(request))
        self.assertEqual(len({row["judge_request"]["instructions"] for row in traces}), 1)

    def test_initial_evidence_is_identical_for_all_groups_and_input_unchanged(self):
        data = fixture()
        original = copy.deepcopy(data)
        _, traces = self.run_demo(data)
        initial = traces[0]["judge_request"]["evidence"]
        self.assertEqual(len(initial), 1)
        for row in traces:
            self.assertEqual(row["judge_request"]["evidence"][:len(initial)], initial)
        self.assertEqual(data, original)
        self.assertEqual([len(row["shown_evidence_ids"]) for row in traces], [1, 2, 2])

    def test_demo_rejects_non_synthetic_evidence(self):
        data = fixture()
        data["units"][0]["extra_C"][0]["synthetic"] = False
        with self.assertRaisesRegex(pilot.ValidationError, "nonsynthetic"):
            pilot.validate_data(data, demo=True)

    def test_demo_rejects_non_synthetic_heldout(self):
        data = fixture()
        data["units"][0]["heldout"][0]["synthetic"] = False
        with self.assertRaisesRegex(pilot.ValidationError, "nonsynthetic"):
            pilot.validate_data(data, demo=True)

    def test_cli_demo_rejects_non_synthetic_input_and_writes_no_summary(self):
        data = fixture()
        data["units"][0]["initial_evidence"][0]["synthetic"] = False
        with tempfile.TemporaryDirectory(prefix="pilot-integrity-cli-") as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "output"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(Path(pilot.__file__).resolve()), "--input", str(source),
                 "--output", str(output), "--demo"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("nonsynthetic", result.stderr)
            self.assertFalse((output / "summary.json").exists())

    def test_missing_real_judge_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory(prefix="pilot-integrity-no-judge-") as directory:
            with self.assertRaisesRegex(pilot.ValidationError, "never falls back"):
                pilot.run(fixture(), Path(directory) / "output", command=None, demo=False)

    def test_unknown_evidence_cost_stays_null(self):
        data = fixture()
        data["units"][0]["extra_C"][0]["cost_usd"] = None
        summary, traces = self.run_demo(data)
        row = next(item for item in traces if item["group"] == "C")
        self.assertIsNone(row["costs"]["extra_evidence_usd"])
        self.assertIsNone(row["costs"]["additional_usd"])
        self.assertIsNone(row["costs"]["total_decision_usd"])
        self.assertIsNone(row["costs"]["within_additional_cap"])
        self.assertFalse(summary["all_decision_costs_complete"])
        self.assertFalse(summary["accounting_checks_passed"])
        self.assertIsNone(summary["budget_comparisons"][0]["equal_actual_added_spend"])

    def test_unknown_judge_cost_stays_null(self):
        response = {"accept": False, "rationale": "synthetic mocked response", "usage": {"cost_usd": None}}
        with patch.object(pilot, "call_judge", return_value=(response, None, None, 0.0)):
            summary, traces = self.run_demo(fixture())
        self.assertFalse(summary["all_decision_costs_complete"])
        for row in traces:
            self.assertIsNone(row["costs"]["judgment_usd"])
            self.assertIsNone(row["costs"]["total_decision_usd"])

    def test_failed_probe_is_visible_and_fully_charged(self):
        data = fixture(legacy=True)
        event = data["units"][0]["extra_C"][0]
        event["valid"] = False
        event["observation"] = {"error": "synthetic probe construction failure"}
        event["cost_usd"] = 0.37
        data = upgrade_synthetic_fixture(data)
        summary, traces = self.run_demo(data)
        row = next(item for item in traces if item["group"] == "C")
        self.assertEqual(row["invalid_evidence_ids"], ["opaque-303"])
        self.assertIn("opaque-303", row["shown_evidence_ids"])
        self.assertAlmostEqual(row["costs"]["extra_evidence_usd"], 0.37)
        self.assertAlmostEqual(row["costs"]["total_decision_usd"], 0.47)
        self.assertFalse(row["judge_request"]["evidence"][-1]["valid"])
        self.assertFalse(summary["input_contract_audit"]["units"][0]["diagnostic_complete"])

    def test_over_budget_run_is_ineligible_without_cheap_event_cherry_picking(self):
        data = fixture(legacy=True)
        data["additional_budget_usd"] = 0.25
        data["units"][0]["extra_C"].append({
            "event_id": "opaque-404",
            "type": "diagnostic_interpretation",
            "observation": {"description": "synthetic paid interpretation"},
            "cost_usd": 0.20,
            "synthetic": True,
            "valid": True,
        })
        data = upgrade_synthetic_fixture(data)
        summary, traces = self.run_demo(data)
        row = next(item for item in traces if item["group"] == "C")
        self.assertEqual(row["shown_evidence_ids"], ["opaque-101", "opaque-303", "opaque-404"])
        self.assertAlmostEqual(row["costs"]["additional_usd"], 0.40)
        self.assertFalse(row["costs"]["within_additional_cap"])
        self.assertFalse(summary["all_additional_caps_respected"])
        self.assertFalse(summary["accounting_checks_passed"])

    def test_fictional_non_demo_over_budget_replay_is_marked_invalid(self):
        # This mocked invocation only tests status handling; no model was run.
        data = fixture()
        data["additional_budget_usd"] = 0.10
        for unit in data["units"]:
            for channel in ("initial_evidence", "extra_B", "extra_C", "heldout"):
                for item in unit[channel]:
                    item["synthetic"] = False
        response = {"accept": False, "rationale": "MOCK integrity test only", "usage": {"cost_usd": 0.01}}
        with tempfile.TemporaryDirectory(prefix="pilot-integrity-status-") as directory:
            with patch.object(pilot, "call_judge", return_value=(response, 0.01, None, 0.0)):
                summary = pilot.run(data, Path(directory), command=["not-executed"], demo=False)
        self.assertEqual(summary["status"], "INCOMPLETE_OR_INVALID_REPLAY")
        self.assertFalse(summary["accounting_checks_passed"])

    def test_insufficient_heldout_pairs_remain_unresolved(self):
        data = fixture()
        data["units"][0]["heldout"][0]["baseline"]["utility"] = 0.0
        data["units"][0]["heldout"][0]["candidate"]["utility"] = 1.0
        settings = pilot.validate_data(data, demo=True)
        reference = pilot.heldout_summary(data["units"][0], settings)
        self.assertEqual(reference["reference_effect_class"], "unresolved")
        result = pilot.assess_decision({"accept": True}, reference)
        self.assertIsNone(result["fixed_loss_on_resolved_reference"])

    def test_near_zero_finite_sample_effect_is_not_forced_into_a_label(self):
        data = fixture(legacy=True)
        pair = data["units"][0]["heldout"][0]
        pair["candidate"]["utility"] = pair["baseline"]["utility"]
        data["units"][0]["heldout"] = [
            {**copy.deepcopy(pair), "task_id": f"synthetic-pair-{index}"}
            for index in range(20)
        ]
        data = upgrade_synthetic_fixture(data)
        settings = pilot.validate_data(data, demo=True)
        reference = pilot.heldout_summary(data["units"][0], settings)
        self.assertEqual(reference["mean_candidate_minus_baseline_utility"], 0.0)
        self.assertEqual(reference["reference_effect_class"], "unresolved")
        self.assertIsNone(pilot.assess_decision({"accept": False}, reference)["fixed_loss_on_resolved_reference"])

    def test_empty_heldout_is_unresolved_and_has_no_invented_gain(self):
        data = fixture()
        data["units"][0]["heldout"] = []
        summary, _ = self.run_demo(data)
        reference = summary["heldout_references"]["unit-test-only"]
        self.assertEqual(reference["reference_effect_class"], "unresolved")
        self.assertIsNone(reference["mean_candidate_minus_baseline_utility"])
        for decision in summary["decisions"]:
            self.assertIsNone(decision["fixed_loss_on_resolved_reference"])
            self.assertIsNone(decision["realized_heldout_mean_gain"])

    def test_explicit_hidden_label_inside_observation_is_rejected(self):
        data = fixture()
        data["units"][0]["initial_evidence"][0]["observation"] = {
            "nested": [{"reference_label": "above_threshold"}]
        }
        with self.assertRaisesRegex(pilot.ValidationError, "forbidden"):
            pilot.validate_data(data, demo=True)

    def test_demo_outputs_are_never_scientifically_eligible(self):
        summary, traces = self.run_demo(fixture())
        self.assertEqual(summary["status"], "SYNTHETIC_PLUMBING_ONLY")
        self.assertEqual(summary["real_agent_experiment_status"], "NOT_RUN_BY_THIS_TOOL")
        self.assertEqual(summary["scientific_validity"], "NOT_ASSESSED")
        self.assertNotIn("scientific_comparison_eligible", summary)
        self.assertTrue(all(row["mode"] == "synthetic_plumbing" for row in traces))

    def test_upstream_unknown_costs_are_not_silently_zero(self):
        data = fixture()
        del data["shared_overheads"]
        summary, _ = self.run_demo(data)
        self.assertFalse(summary["all_upstream_costs_complete"])
        self.assertTrue(summary["all_decision_costs_complete"])
        self.assertIsNone(summary["research_cost_accounting"]["shared_overheads_usd"])
        self.assertIsNone(summary["research_cost_accounting"]["recorded_total_usd"])
        self.assertFalse(summary["accounting_checks_passed"])

    def test_heldout_unknown_costs_are_reported_separately(self):
        data = fixture()
        data["units"][0]["heldout"][0]["candidate"]["execution_cost_usd"] = None
        summary, _ = self.run_demo(data)
        self.assertTrue(summary["all_decision_costs_complete"])
        self.assertTrue(summary["all_upstream_costs_complete"])
        self.assertFalse(summary["all_heldout_evaluation_costs_complete"])
        self.assertIsNone(summary["research_cost_accounting"]["heldout_evaluation_usd"])
        self.assertIsNone(summary["research_cost_accounting"]["recorded_total_usd"])

    def test_research_total_charges_shared_initial_evidence_once(self):
        summary, _ = self.run_demo(fixture())
        costs = summary["research_cost_accounting"]
        self.assertAlmostEqual(costs["unique_listed_evidence_collection_usd"], 0.50)
        self.assertAlmostEqual(costs["heldout_evaluation_usd"], 0.07)
        self.assertAlmostEqual(costs["shared_overheads_usd"], 0.10)
        self.assertAlmostEqual(costs["recorded_total_usd"], 0.67)
        self.assertTrue(summary["accounting_checks_passed"])
        self.assertEqual(summary["scientific_validity"], "NOT_ASSESSED")

    def test_judge_receives_verified_patch_content_and_paired_run_bindings(self):
        data = fixture()
        _, traces = self.run_demo(data)
        for row in traces:
            request = row["judge_request"]
            self.assertEqual(request["protocol_version"], 2)
            self.assertEqual(request["frozen"]["patch"]["content"],
                             data["units"][0]["frozen"]["candidate"]["content"])
            for event in request["evidence"]:
                self.assertEqual(event["status"], "completed")
                for side in ("baseline", "candidate"):
                    self.assertEqual(event["observation"][side]["task_sha256"],
                                     event["task"]["sha256"])

    def test_unlisted_nested_metadata_never_enters_judge_request(self):
        data = fixture()
        event = data["units"][0]["extra_C"][0]
        for key in ("binding", "task", "intervention"):
            event[key]["private_reference_label"] = "PRIVATE_NESTED_SENTINEL"
        for artifact in data["units"][0]["frozen"].values():
            artifact["private_reference_label"] = "PRIVATE_ARTIFACT_SENTINEL"
        _, traces = self.run_demo(data)
        for row in traces:
            request = json.dumps(row["judge_request"])
            self.assertNotIn("PRIVATE_NESTED_SENTINEL", request)
            self.assertNotIn("PRIVATE_ARTIFACT_SENTINEL", request)

    def test_reference_effect_class_is_a_forbidden_evidence_key(self):
        data = fixture()
        data["units"][0]["extra_C"][0]["binding"]["reference_effect_class"] = "above_threshold"
        with self.assertRaisesRegex(pilot.ValidationError, "forbidden"):
            pilot.validate_data(data, demo=True)

    def test_candidate_only_diagnostic_is_rejected_before_judge_or_output(self):
        data = fixture()
        del data["units"][0]["extra_C"][0]["observation"]["baseline"]
        with tempfile.TemporaryDirectory(prefix="pilot-contract-routing-") as directory:
            output = Path(directory) / "not-created"
            with patch.object(pilot, "call_judge") as judge:
                with self.assertRaisesRegex(pilot.ValidationError, "baseline"):
                    pilot.run(data, output, command=None, demo=True)
                judge.assert_not_called()
            self.assertFalse(output.exists())

    def test_tampered_run_hash_is_rejected_before_judge(self):
        data = fixture()
        data["units"][0]["extra_C"][0]["observation"]["candidate"]["harness_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="pilot-contract-hash-") as directory:
            with patch.object(pilot, "call_judge") as judge:
                with self.assertRaisesRegex(pilot.ValidationError, "harness_sha256"):
                    pilot.run(data, Path(directory), command=None, demo=True)
                judge.assert_not_called()

    def test_legacy_real_input_is_not_automatically_upgraded(self):
        data = fixture(legacy=True)
        for unit in data["units"]:
            for channel in ("initial_evidence", "extra_B", "extra_C", "heldout"):
                for record in unit[channel]:
                    record["synthetic"] = False
        with self.assertRaisesRegex(pilot.ValidationError, "schema_version=2"):
            pilot.validate_data(data, demo=False)

    def test_selective_judge_failure_is_paired_in_end_to_end_replay(self):
        # Reconstruct the user's easy/hard failure scenario, with artificial
        # paired future outcomes strong enough for the unchanged reference rule.
        data = fixture(legacy=True)
        template = data["units"][0]
        data["units"] = []
        for name in ("easy", "hard"):
            unit = copy.deepcopy(template)
            unit["unit_id"] = name
            unit["patch_id"] = "fictional-patch-" + name
            unit["heldout"] = [
                {"task_id": name + "-future-" + str(i), "synthetic": True,
                 "baseline": {"utility": 0.0, "execution_cost_usd": 0.0},
                 "candidate": {"utility": 1.0, "execution_cost_usd": 0.0}}
                for i in range(64)
            ]
            data["units"].append(unit)
        data = upgrade_synthetic_fixture(data)

        def completed(accept):
            return ({"accept": accept, "usage": {"cost_usd": 0.0}}, 0.0, None, 0.0)

        responses = [completed(False), completed(True), completed(True),
                     completed(False), completed(False),
                     (None, None, "MOCK judgment failure", 0.0)]
        with patch.object(pilot, "call_judge", side_effect=responses):
            summary, _ = self.run_demo(data)
        groups, paired = summary["groups"], summary["paired_BC_comparison"]
        self.assertEqual(groups["B"]["decision_completion"]["rate"], 1.0)
        self.assertEqual(groups["C"]["decision_completion"]["rate"], 0.5)
        self.assertEqual(groups["C"]["reference_resolvability"]["rate"], 1.0)
        self.assertEqual(paired["status"], "INCOMPLETE_PAIRING")
        self.assertEqual(paired["loss_comparison"]["included_unit_ids"], ["easy"])
        self.assertEqual(paired["loss_comparison"]["conditional_mean_C_minus_B_fixed_loss"], 0.0)
        self.assertEqual(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"], [-0.5, 0.0])
        self.assertIsNone(paired["overall_superiority_conclusion"])


if __name__ == "__main__":
    unittest.main()
