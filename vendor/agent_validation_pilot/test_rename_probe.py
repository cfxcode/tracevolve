"""Local tool tests only; these are not agent-research experiments."""

import copy
import unittest

from rename_probe import ProbeValidationError, rename_task, validate_task, verify_renaming


def example_task():
    return {
        "id": "structural-tool-test",
        "instructions": "Treat resource IDs as opaque handles.",
        "jobs": [
            {"id": "job-a", "requires": [], "payload": {"label": "job-a"}},
            {
                "id": "job-b",
                "requires": ["job-a"],
                "payload": {"text": "job-a should remain literal text", "id": "job-b"},
            },
            {"id": "job-c", "requires": ["job-a", "job-b"], "payload": {}},
        ],
        "goals": ["job-c"],
    }


class RenameProbeTests(unittest.TestCase):
    def test_default_mapping_preserves_graph_and_inverse(self):
        original = example_task()
        renamed, mapping = rename_task(original)
        self.assertEqual(renamed["jobs"][2]["requires"], [mapping["job-a"], mapping["job-b"]])
        self.assertEqual(renamed["goals"], [mapping["job-c"]])
        self.assertTrue(verify_renaming(original, renamed, mapping))

    def test_inputs_are_not_mutated(self):
        original = example_task()
        snapshot = copy.deepcopy(original)
        mapping = {"job-a": "x", "job-b": "y", "job-c": "z"}
        renamed, returned_mapping = rename_task(original, mapping)
        renamed["jobs"][0]["payload"]["label"] = "changed"
        returned_mapping["job-a"] = "changed"
        self.assertEqual(original, snapshot)
        self.assertEqual(mapping["job-a"], "x")

    def test_payload_and_natural_language_are_never_replaced(self):
        original = example_task()
        renamed, _ = rename_task(original)
        for before, after in zip(original["jobs"], renamed["jobs"]):
            self.assertEqual(before["payload"], after["payload"])
        self.assertEqual(renamed["jobs"][1]["payload"]["id"], "job-b")
        self.assertEqual(renamed["instructions"], original["instructions"])

    def test_swap_mapping_is_simultaneous(self):
        task = example_task()
        mapping = {"job-a": "job-b", "job-b": "job-a", "job-c": "job-c"}
        renamed, used = rename_task(task, mapping)
        self.assertEqual(renamed["jobs"][1]["requires"], ["job-b"])
        self.assertTrue(verify_renaming(task, renamed, used))

    def test_identity_mapping_is_valid(self):
        task = example_task()
        mapping = {job["id"]: job["id"] for job in task["jobs"]}
        renamed, _ = rename_task(task, mapping, renamed_task_id=task["id"])
        self.assertEqual(renamed, task)

    def test_duplicate_job_ids_are_rejected(self):
        task = example_task()
        task["jobs"][1]["id"] = "job-a"
        with self.assertRaises(ProbeValidationError):
            rename_task(task)

    def test_non_bijective_mapping_is_rejected(self):
        with self.assertRaises(ProbeValidationError):
            rename_task(example_task(), {"job-a": "x", "job-b": "x", "job-c": "z"})

    def test_partial_and_extra_mapping_keys_are_rejected(self):
        for mapping in (
            {"job-a": "x", "job-b": "y"},
            {"job-a": "x", "job-b": "y", "job-c": "z", "other": "w"},
        ):
            with self.subTest(mapping=mapping), self.assertRaises(ProbeValidationError):
                rename_task(example_task(), mapping)

    def test_missing_dependency_is_rejected(self):
        task = example_task()
        task["jobs"][1]["requires"] = ["unknown"]
        with self.assertRaises(ProbeValidationError):
            validate_task(task)

    def test_missing_goal_is_rejected(self):
        task = example_task()
        task["goals"] = ["unknown"]
        with self.assertRaises(ProbeValidationError):
            validate_task(task)

    def test_duplicate_references_are_rejected(self):
        task = example_task()
        task["jobs"][1]["requires"] = ["job-a", "job-a"]
        with self.assertRaises(ProbeValidationError):
            validate_task(task)

    def test_cycles_and_self_cycles_are_rejected(self):
        for dependency in ("job-c", "job-a"):
            task = example_task()
            task["jobs"][0]["requires"] = [dependency]
            with self.subTest(dependency=dependency), self.assertRaises(ProbeValidationError):
                validate_task(task)

    def test_inverse_check_rejects_changed_payload(self):
        original = example_task()
        renamed, mapping = rename_task(original)
        renamed["jobs"][0]["payload"]["label"] = "not equivalent"
        with self.assertRaises(ProbeValidationError):
            verify_renaming(original, renamed, mapping)

    def test_inverse_check_rejects_reported_bool_number_corruption(self):
        original = example_task()
        original["jobs"][0]["payload"] = {"enabled": False, "count": 1}
        renamed, mapping = rename_task(original)
        renamed["jobs"][0]["payload"] = {"enabled": 0, "count": True}
        with self.assertRaisesRegex(ProbeValidationError, "inverse renaming"):
            verify_renaming(original, renamed, mapping)

    def test_nested_bool_number_changes_are_rejected_in_both_directions(self):
        for boolean, number in ((False, 0), (True, 1), (False, 0.0), (True, 1.0)):
            for before, after in ((boolean, number), (number, boolean)):
                with self.subTest(before=repr(before), after=repr(after)):
                    original = example_task()
                    original["jobs"][0]["payload"] = {"nested": {"value": before}}
                    renamed, mapping = rename_task(original)
                    renamed["jobs"][0]["payload"]["nested"]["value"] = after
                    with self.assertRaisesRegex(ProbeValidationError, "inverse renaming"):
                        verify_renaming(original, renamed, mapping)

    def test_bool_number_changes_inside_arrays_are_rejected(self):
        for before, after in ((False, 0), (0, False), (True, 1), (1, True)):
            with self.subTest(before=repr(before), after=repr(after)):
                original = example_task()
                original["jobs"][0]["payload"] = {"items": [None, [{"value": before}]]}
                renamed, mapping = rename_task(original)
                renamed["jobs"][0]["payload"]["items"][1][0]["value"] = after
                with self.assertRaisesRegex(ProbeValidationError, "inverse renaming"):
                    verify_renaming(original, renamed, mapping)

    def test_unchanged_nested_json_types_are_accepted(self):
        original = example_task()
        original["jobs"][0]["payload"] = {
            "items": [False, 0, 0.0, True, 1, 1.0, None, "1", {"nested": [2.5]}]
        }
        renamed, mapping = rename_task(original)
        self.assertTrue(verify_renaming(original, renamed, mapping))

    def test_object_key_order_is_irrelevant(self):
        original = example_task()
        original["jobs"][0]["payload"] = {"first": False, "nested": {"a": 1, "b": 1.0}}
        renamed, mapping = rename_task(original)
        renamed["jobs"][0]["payload"] = {"nested": {"b": 1.0, "a": 1}, "first": False}
        renamed = dict(reversed(list(renamed.items())))
        self.assertTrue(verify_renaming(original, renamed, mapping))

    def test_array_order_is_preserved(self):
        original = example_task()
        original["jobs"][0]["payload"] = {"items": [{"value": 1}, {"value": 2}]}
        renamed, mapping = rename_task(original)
        renamed["jobs"][0]["payload"]["items"].reverse()
        with self.assertRaisesRegex(ProbeValidationError, "inverse renaming"):
            verify_renaming(original, renamed, mapping)

    def test_equal_valued_integer_float_changes_are_rejected(self):
        for before, after in ((1, 1.0), (1.0, 1), (0, 0.0), (0.0, 0)):
            with self.subTest(before=repr(before), after=repr(after)):
                original = example_task()
                original["jobs"][0]["payload"] = {"items": [{"count": before}]}
                renamed, mapping = rename_task(original)
                renamed["jobs"][0]["payload"]["items"][0]["count"] = after
                with self.assertRaisesRegex(ProbeValidationError, "inverse renaming"):
                    verify_renaming(original, renamed, mapping)

    def test_inverse_check_rejects_changed_edges_even_when_acyclic(self):
        original = example_task()
        renamed, mapping = rename_task(original)
        renamed["jobs"][2]["requires"] = [mapping["job-a"]]
        with self.assertRaises(ProbeValidationError):
            verify_renaming(original, renamed, mapping)

    def test_inverse_check_allows_top_level_task_id_change_only(self):
        original = example_task()
        renamed, mapping = rename_task(original, renamed_task_id="a-new-task-id")
        self.assertTrue(verify_renaming(original, renamed, mapping))
        renamed["new_metadata"] = "unapproved change"
        with self.assertRaises(ProbeValidationError):
            verify_renaming(original, renamed, mapping)

    def test_nonfinite_and_non_json_data_are_rejected(self):
        for value in (float("nan"), float("inf"), ("tuple",)):
            task = example_task()
            task["jobs"][0]["payload"]["bad"] = value
            with self.subTest(value=value), self.assertRaises(ProbeValidationError):
                validate_task(task)

    def test_non_string_json_object_keys_are_rejected(self):
        for key in (0, True, None):
            task = example_task()
            task["jobs"][0]["payload"] = {"nested": {key: "value"}}
            with self.subTest(key=repr(key)), self.assertRaises(ProbeValidationError):
                validate_task(task)


if __name__ == "__main__":
    unittest.main()
