"""Regression tests for attrition, paired denominators, and missing-loss bounds."""

import unittest

from pilot import ValidationError, assess_decision, summarize_adoption_decisions


def reference(label="above_threshold", gain=0.4):
    return {
        "reference_effect_class": label,
        "mean_candidate_minus_baseline_utility": gain,
        "baseline_mean_utility": 0.5 if gain is not None else None,
        "candidate_mean_utility": 0.5 + gain if gain is not None else None,
        "baseline_execution_cost_usd": 0.1,
        "candidate_execution_cost_usd": 0.2,
    }


def decision_rows(references, answers):
    rows = []
    for unit_id, ref in references.items():
        for group in ("A", "B", "C"):
            answer = answers[unit_id].get(group, False)
            response = {"accept": answer} if answer is not None else None
            rows.append({
                "unit_id": unit_id,
                "group": group,
                "evaluation": assess_decision(response, ref),
                "costs": {"total_decision_usd": 0.1 if response is not None else None},
            })
    return rows


class PairedMetricsTests(unittest.TestCase):
    def summarize(self, references, answers):
        return summarize_adoption_decisions(decision_rows(references, answers), references)

    def test_reported_two_unit_attrition_counterexample(self):
        refs = {"easy": reference(), "hard": reference()}
        summary = self.summarize(refs, {
            "easy": {"B": True, "C": True},
            "hard": {"B": False, "C": None},
        })
        b, c = summary["groups"]["B"], summary["groups"]["C"]
        # These recover the old misleading 0.5 versus 0; the names now state
        # that they condition on different explicit sets.
        self.assertEqual(b["conditional_mean_fixed_loss_completed_and_resolved"], 0.5)
        self.assertEqual(c["conditional_mean_fixed_loss_completed_and_resolved"], 0.0)
        self.assertEqual(b["decision_completion"]["rate"], 1.0)
        self.assertEqual(c["decision_completion"]["rate"], 0.5)
        self.assertEqual(c["reference_resolvability"]["count"], 2)
        self.assertEqual(c["reference_resolvability"]["rate"], 1.0)
        self.assertEqual(c["completed_and_resolvable"]["unit_ids"], ["easy"])
        self.assertEqual(c["completed_and_resolvable"]["rate"], 0.5)

        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "INCOMPLETE_PAIRING")
        self.assertFalse(paired["full_cohort_decision_comparison_available"])
        self.assertIsNone(paired["overall_superiority_conclusion"])
        loss = paired["loss_comparison"]
        self.assertEqual(loss["included_unit_ids"], ["easy"])
        self.assertEqual(loss["conditional_mean_C_minus_B_fixed_loss"], 0.0)
        self.assertEqual(loss["excluded_units"], [
            {"unit_id": "hard", "reasons": ["C_judge_failed"]}])
        self.assertEqual(paired["gain_comparison"]["included_unit_ids"], ["easy"])
        self.assertEqual(paired["gain_comparison"]["conditional_mean_C_minus_B_realized_gain"], 0)
        bounds = paired["resolved_reference_loss_bounds"]
        self.assertEqual(bounds["denominator"], 2)
        self.assertEqual(bounds["unit_ids"], ["easy", "hard"])
        self.assertEqual(bounds["B_mean_loss_interval"], [0.5, 0.5])
        self.assertEqual(bounds["C_mean_loss_interval"], [0.0, 0.5])
        self.assertEqual(bounds["C_minus_B_mean_loss_interval"], [-0.5, 0.0])
        self.assertIsNone(c["total_decision_cost_usd"])
        self.assertNotIn("mean_fixed_loss_resolved_only", c)

    def test_reverse_asymmetric_failure_is_not_scored_as_a_correct_decision(self):
        summary = self.summarize({"easy": reference(), "hard": reference()}, {
            "easy": {"B": True, "C": True},
            "hard": {"B": None, "C": False},
        })
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "INCOMPLETE_PAIRING")
        self.assertEqual(paired["loss_comparison"]["conditional_mean_C_minus_B_fixed_loss"], 0)
        self.assertEqual(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"], [0, 0.5])
        self.assertEqual(paired["incomplete_units"], [
            {"unit_id": "hard", "reasons": ["B_judge_failed"]}])

    def test_both_groups_failing_on_same_unit_still_prohibits_overall_conclusion(self):
        summary = self.summarize({"easy": reference(), "hard": reference()}, {
            "easy": {"B": True, "C": True},
            "hard": {"B": None, "C": None},
        })
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "INCOMPLETE_PAIRING")
        self.assertIsNone(paired["overall_superiority_conclusion"])
        self.assertEqual(paired["joint_completion"]["count"], 1)
        self.assertEqual(paired["joint_completion"]["denominator"], 2)
        self.assertEqual(paired["joint_completion"]["rate"], 0.5)
        self.assertEqual(paired["loss_comparison"]["excluded_units"], [
            {"unit_id": "hard", "reasons": ["B_judge_failed", "C_judge_failed"]}])
        self.assertEqual(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"], [-0.5, 0.5])

    def test_unresolved_reference_is_separate_from_completed_judgment(self):
        summary = self.summarize({"uncertain": reference("unresolved", 0.04)}, {
            "uncertain": {"B": True, "C": False},
        })
        for group in ("B", "C"):
            stats = summary["groups"][group]
            self.assertEqual(stats["decision_completion"]["rate"], 1.0)
            self.assertEqual(stats["reference_resolvability"]["rate"], 0.0)
            self.assertEqual(stats["completed_and_resolvable"]["count"], 0)
            self.assertIsNone(stats["conditional_mean_fixed_loss_completed_and_resolved"])
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "COMPLETE_PAIRING")
        self.assertEqual(paired["loss_comparison"]["count"], 0)
        self.assertEqual(paired["loss_comparison"]["excluded_units"], [
            {"unit_id": "uncertain", "reasons": ["reference_unresolved"]}])
        self.assertIsNone(paired["loss_comparison"]["conditional_mean_C_minus_B_fixed_loss"])
        self.assertIsNone(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"])
        self.assertEqual(paired["gain_comparison"]["count"], 1)
        self.assertAlmostEqual(paired["gain_comparison"]["conditional_mean_C_minus_B_realized_gain"], -0.04)
        self.assertIsNone(paired["overall_superiority_conclusion"])

    def test_complete_pairs_compute_loss_and_gain_on_same_ids(self):
        summary = self.summarize({
            "helpful": reference("above_threshold", 0.4),
            "harmful": reference("below_threshold", -0.2),
        }, {
            "helpful": {"B": False, "C": True},
            "harmful": {"B": True, "C": False},
        })
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "COMPLETE_PAIRING")
        self.assertTrue(paired["full_cohort_decision_comparison_available"])
        self.assertEqual(paired["joint_completion"]["rate"], 1)
        loss = paired["loss_comparison"]
        self.assertEqual(loss["included_unit_ids"], ["helpful", "harmful"])
        self.assertEqual(loss["excluded_units"], [])
        self.assertEqual(loss["conditional_mean_B_fixed_loss"], 1)
        self.assertEqual(loss["conditional_mean_C_fixed_loss"], 0)
        self.assertEqual(loss["conditional_mean_C_minus_B_fixed_loss"], -1)
        self.assertEqual(loss["unit_pairs"], [
            {"unit_id": "helpful", "B_loss": 1, "C_loss": 0, "C_minus_B_loss": -1},
            {"unit_id": "harmful", "B_loss": 1, "C_loss": 0, "C_minus_B_loss": -1},
        ])
        gain = paired["gain_comparison"]
        self.assertEqual(gain["included_unit_ids"], ["helpful", "harmful"])
        self.assertEqual(gain["conditional_mean_B_realized_gain"], -0.1)
        self.assertEqual(gain["conditional_mean_C_realized_gain"], 0.2)
        self.assertAlmostEqual(gain["conditional_mean_C_minus_B_realized_gain"], 0.3)
        self.assertEqual(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"], [-1, -1])
        self.assertIsNone(paired["overall_superiority_conclusion"])

    def test_all_failures_have_null_means_and_full_missingness_bounds(self):
        summary = self.summarize({"x": reference()}, {"x": {"B": None, "C": None}})
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["joint_completion"]["count"], 0)
        self.assertIsNone(paired["loss_comparison"]["conditional_mean_B_fixed_loss"])
        self.assertIsNone(paired["gain_comparison"]["conditional_mean_C_minus_B_realized_gain"])
        self.assertEqual(paired["resolved_reference_loss_bounds"]["C_minus_B_mean_loss_interval"], [-1, 1])
        for group in ("B", "C"):
            self.assertEqual(summary["groups"][group]["reference_resolvability"]["count"], 1)
            self.assertEqual(summary["groups"][group]["completed_and_resolvable"]["count"], 0)

    def test_missing_gain_has_its_own_exclusion_reason(self):
        summary = self.summarize({"no-heldout": reference("unresolved", None)}, {
            "no-heldout": {"B": True, "C": False},
        })
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "COMPLETE_PAIRING")
        self.assertEqual(paired["joint_completion"]["count"], 1)
        self.assertEqual(paired["gain_comparison"]["count"], 0)
        self.assertEqual(paired["gain_comparison"]["excluded_units"], [
            {"unit_id": "no-heldout", "reasons": ["heldout_gain_unobserved"]}])

    def test_failure_on_unresolved_reference_remains_in_total_completion_denominator(self):
        summary = self.summarize({
            "known": reference(), "unknown": reference("unresolved", 0.01),
        }, {
            "known": {"B": True, "C": True},
            "unknown": {"B": False, "C": None},
        })
        paired = summary["paired_BC_comparison"]
        self.assertEqual(paired["status"], "INCOMPLETE_PAIRING")
        self.assertEqual(paired["joint_completion"]["denominator"], 2)
        self.assertEqual(paired["loss_comparison"]["excluded_units"], [
            {"unit_id": "unknown", "reasons": ["C_judge_failed", "reference_unresolved"]}])
        self.assertEqual(paired["resolved_reference_loss_bounds"]["denominator"], 1)
        self.assertIsNone(paired["overall_superiority_conclusion"])

    def test_missing_row_is_missing_not_silently_removed_from_universe(self):
        refs = {"x": reference()}
        rows = decision_rows(refs, {"x": {"B": True, "C": True}})
        rows = [r for r in rows if r["group"] != "C"]
        summary = summarize_adoption_decisions(rows, refs)
        self.assertEqual(summary["groups"]["C"]["total_units"], 1)
        self.assertEqual(summary["groups"]["C"]["decision_completion"]["rate"], 0)
        self.assertIsNone(summary["groups"]["C"]["total_decision_cost_usd"])
        self.assertEqual(summary["paired_BC_comparison"]["incomplete_units"], [
            {"unit_id": "x", "reasons": ["C_missing_decision_row"]}])

    def test_duplicate_group_rows_cannot_inflate_denominators(self):
        refs = {"x": reference()}
        rows = decision_rows(refs, {"x": {"B": True, "C": True}})
        with self.assertRaisesRegex(ValidationError, "duplicate aggregation row"):
            summarize_adoption_decisions(rows + [rows[0]], refs)


if __name__ == "__main__":
    unittest.main()
