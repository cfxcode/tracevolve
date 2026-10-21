"""Adversarial contract checks with explicitly fabricated evidence only."""

import copy
import unittest

from contracts import ContractError, sha256_json, sha256_text, validate_contract
from synthetic_fixture import upgrade_synthetic_fixture


def legacy_fixture():
    def event(event_id, kind):
        return {"event_id": event_id, "type": kind, "synthetic": True,
                "valid": True, "observation": {"baseline_utility": 0, "candidate_utility": 1},
                "cost_usd": 0.03}
    return {
        "schema_version": 1,
        "decision": {"utility_bounds": [0, 1]},
        "units": [{
            "unit_id": "synthetic-unit", "baseline_id": "synthetic-base", "patch_id": "synthetic-patch",
            "patch_description": "FABRICATED",
            "initial_evidence": [event("initial", "complete_validation")],
            "extra_B": [event("repeat", "complete_validation")],
            "extra_C": [event("rename", "identifier_renaming")],
            "heldout": [{"task_id": "future-task", "synthetic": True,
                         "baseline": {"utility": 0, "execution_cost_usd": 0.01},
                         "candidate": {"utility": 1, "execution_cost_usd": 0.02}}],
        }],
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.data = upgrade_synthetic_fixture(legacy_fixture())
        self.unit = self.data["units"][0]
        self.probe = self.unit["extra_C"][0]

    def reject(self, phrase):
        with self.assertRaisesRegex(ContractError, phrase):
            validate_contract(self.data)

    def test_good_fixture_is_valid_without_mutation(self):
        original = copy.deepcopy(self.data)
        audit = validate_contract(self.data)
        self.assertEqual(original, self.data)
        self.assertTrue(audit["all_validation_channels_complete"])
        self.assertTrue(audit["units"][0]["diagnostic_complete"])
        self.assertTrue(any("do not attest" in limit for limit in audit["limits"]))

    def test_legacy_version_is_rejected(self):
        self.data["schema_version"] = 1
        self.reject("schema_version=2")

    def test_candidate_only_probe_is_rejected(self):
        del self.probe["observation"]["baseline"]
        self.reject("baseline must be an object")

    def test_baseline_must_run_same_renamed_task(self):
        self.probe["observation"]["baseline"]["task_sha256"] = self.unit["source_task"]["sha256"]
        self.reject("task_sha256")

    def test_patch_binding_cannot_change_between_events(self):
        self.probe["binding"]["patch_sha256"] = "0" * 64
        self.reject("patch_sha256")

    def test_harness_hash_must_match_actual_content(self):
        self.unit["frozen"]["candidate"]["content"] += "tampering"
        self.reject("candidate.sha256")

    def test_hash_format_alone_cannot_validate_patch(self):
        self.unit["frozen"]["patch"]["sha256"] = "a" * 64
        self.reject("patch.sha256")

    def test_rehashed_patch_still_must_produce_candidate(self):
        patch = self.unit["frozen"]["patch"]
        patch["content"] += "different full replacement"
        patch["sha256"] = sha256_text(patch["content"])
        self.reject("patch.content")

    def test_patch_base_is_frozen(self):
        self.unit["frozen"]["patch"]["base_sha256"] = "f" * 64
        self.reject("base_sha256")

    def test_missing_provenance_is_rejected(self):
        del self.probe["binding"]
        self.reject("binding")

    def test_missing_renaming_map_is_rejected(self):
        del self.probe["intervention"]["mapping"]
        self.reject("mapping")

    def test_identity_mapping_is_not_a_diagnostic_intervention(self):
        source = self.unit["source_task"]
        self.probe["task"] = copy.deepcopy(source)
        self.probe["intervention"]["mapping"] = {"fictional-job": "fictional-job"}
        self.reject("must change")

    def test_rehashed_boolean_numeric_body_corruption_is_rejected(self):
        task = self.probe["task"]
        task["content"]["jobs"][0]["payload"]["enabled"] = 0
        task["sha256"] = sha256_json(task["content"])
        for side in ("baseline", "candidate"):
            self.probe["observation"][side]["task_sha256"] = task["sha256"]
        self.reject("inverse renaming")

    def test_B_cannot_use_a_new_task(self):
        repeat = self.unit["extra_B"][0]
        repeat["task"]["content"]["id"] = "another-task"
        repeat["task"]["sha256"] = sha256_json(repeat["task"]["content"])
        self.reject("task.sha256")

    def test_actual_run_cannot_be_reused_as_repeated_validation(self):
        initial = self.unit["initial_evidence"][0]["observation"]["baseline"]
        self.unit["extra_B"][0]["observation"]["baseline"]["run_id"] = initial["run_id"]
        self.reject("reuses an execution")

    def test_arbitrary_event_type_is_rejected(self):
        self.probe["type"] = "anything_goes"
        self.reject("declared probe overhead")

    def test_malformed_event_type_and_status_raise_contract_error(self):
        for value in ([], {}):
            self.probe["type"] = value
            self.reject("type must be a nonempty string")
        self.probe["type"] = "paired_validation"
        for value in ([], {}):
            self.probe["observation"]["baseline"]["status"] = value
            self.reject("status must be")

    def test_overhead_cannot_smuggle_candidate_outcome(self):
        self.probe["type"] = "probe_generation"
        self.reject("impersonate")

    def test_successful_overhead_alone_cannot_replace_paired_evidence(self):
        self.probe["type"] = "probe_generation"
        self.probe["observation"] = {"description": "Generated a fictional variant."}
        self.reject("lacks paired validation")

    def test_failed_probe_remains_visible_incomplete_and_unknown_cost(self):
        self.probe.update(type="probe_validation", status="failed", valid=False,
                          observation={"error": "Fictional generation failed."}, cost_usd=None)
        audit = validate_contract(self.data)
        self.assertFalse(audit["all_validation_channels_complete"])
        self.assertFalse(audit["units"][0]["diagnostic_complete"])
        self.assertIsNone(self.probe["cost_usd"])
        self.assertEqual(audit["units"][0]["channels"]["extra_C"]["probe_failed"], 1)

    def test_failed_run_must_be_explicit_without_fake_outcome(self):
        run = self.probe["observation"]["candidate"]
        run.update(status="failed", error="Fictional executor failed.")
        self.probe.update(status="failed", valid=False)
        self.reject("cannot supply an outcome")
        del run["utility"]
        audit = validate_contract(self.data)
        self.assertEqual(audit["units"][0]["channels"]["extra_C"]["paired_failed"], 1)
        self.assertFalse(audit["units"][0]["diagnostic_complete"])

    def test_failed_interpretation_alone_is_not_failed_probe_construction(self):
        self.probe.update(type="probe_interpretation", status="failed", valid=False,
                          observation={"error": "Fictional explanation failed."})
        self.reject("lacks paired validation")

    def test_heldout_explicit_source_reuse_is_rejected(self):
        self.unit["heldout"][0]["task"]["source_id"] = self.unit["source_task"]["source_id"]
        self.reject("heldout task overlaps")

    def test_heldout_task_id_and_content_must_agree(self):
        self.unit["heldout"][0]["task_id"] = "mislabelled"
        self.reject("task_id")

    def test_repeated_heldout_source_does_not_count_as_independent_task(self):
        pair = copy.deepcopy(self.unit["heldout"][0])
        pair["task_id"] = pair["task"]["content"]["id"] = "different-label-same-source"
        pair["task"]["sha256"] = sha256_json(pair["task"]["content"])
        self.unit["heldout"].append(pair)
        self.reject("duplicate heldout source_id")

    def test_heldout_uses_same_frozen_patch(self):
        self.unit["heldout"][0]["candidate"]["harness_sha256"] = "0" * 64
        self.reject("harness_sha256")

    def test_same_baseline_name_cannot_change_content_across_units(self):
        other = copy.deepcopy(self.unit)
        other["unit_id"] = "another-unit"
        other["frozen"]["baseline"]["content"] += "new baseline"
        other["frozen"]["baseline"]["sha256"] = sha256_text(other["frozen"]["baseline"]["content"])
        other["frozen"]["patch"]["base_sha256"] = other["frozen"]["baseline"]["sha256"]
        self.data["units"].append(other)
        self.reject("baseline_id names inconsistent")

    def test_same_patch_id_with_a_different_base_is_rejected(self):
        other = copy.deepcopy(self.unit)
        other["unit_id"] = "another-unit"
        other["baseline_id"] = "another-baseline"
        other["frozen"]["baseline"]["content"] += "new baseline"
        other["frozen"]["baseline"]["sha256"] = sha256_text(other["frozen"]["baseline"]["content"])
        other["frozen"]["patch"]["base_sha256"] = other["frozen"]["baseline"]["sha256"]
        self.data["units"].append(other)
        self.reject("patch_id names inconsistent")

    def test_synthetic_builder_refuses_real_or_unlabelled_evidence(self):
        for value in (False, None):
            data = legacy_fixture()
            data["units"][0]["extra_C"][0]["synthetic"] = value
            with self.assertRaisesRegex(ContractError, "refuses nonsynthetic"):
                upgrade_synthetic_fixture(data)

    def test_synthetic_builder_preserves_original_and_fees(self):
        old = legacy_fixture()
        original = copy.deepcopy(old)
        old["units"][0]["extra_C"][0]["cost_usd"] = None
        upgraded = upgrade_synthetic_fixture(old)
        self.assertEqual(old["schema_version"], 1)
        self.assertNotIn("frozen", old["units"][0])
        self.assertEqual(upgraded["units"][0]["initial_evidence"][0]["cost_usd"],
                         original["units"][0]["initial_evidence"][0]["cost_usd"])
        self.assertIsNone(upgraded["units"][0]["extra_C"][0]["cost_usd"])
        validate_contract(upgraded)


if __name__ == "__main__":
    unittest.main()
