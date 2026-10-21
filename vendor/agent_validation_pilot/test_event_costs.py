"""Event-total/paired-execution accounting regressions; no real model calls."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contracts import ContractError, validate_contract
import pilot
from test_pilot_integrity import fixture


MISSING = object()


class EventCostTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.event = self.data["units"][0]["extra_C"][0]

    def amounts(self, baseline, candidate, total, scope="execution_plus_overhead"):
        self.event["cost_usd"] = total
        if scope is MISSING:
            self.event.pop("cost_scope", None)
        else:
            self.event["cost_scope"] = scope
        for side, value in (("baseline", baseline), ("candidate", candidate)):
            run = self.event["observation"][side]
            if value is MISSING:
                run.pop("execution_cost_usd", None)
            else:
                run["execution_cost_usd"] = value

    def audit(self):
        audit = validate_contract(self.data)
        return next(item for item in audit["units"][0]["event_cost_checks"]
                    if item["event_id"] == self.event["event_id"])

    def replay(self):
        with tempfile.TemporaryDirectory(prefix="pilot-cost-test-") as directory:
            output = Path(directory) / "out"
            summary = pilot.run(self.data, output, command=None, demo=True)
            rows = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
            return summary, next(row for row in rows if row["group"] == "C")

    def test_reported_contradiction_rejected_in_every_channel_before_judge(self):
        for channel in ("initial_evidence", "extra_B", "extra_C"):
            with self.subTest(channel=channel):
                data = fixture()
                data["additional_budget_usd"] = 0.5
                event = data["units"][0][channel][0]
                event["cost_usd"] = 0.001
                for side in ("baseline", "candidate"):
                    event["observation"][side]["execution_cost_usd"] = 100.0
                with tempfile.TemporaryDirectory(prefix="pilot-cost-reject-") as directory:
                    output = Path(directory) / "must-not-exist"
                    with patch.object(pilot, "call_judge") as judge:
                        with self.assertRaisesRegex(pilot.ValidationError, "below known paired execution cost"):
                            pilot.run(data, output, command=None, demo=True)
                        judge.assert_not_called()
                    self.assertFalse(output.exists())

    def test_known_subtotal_rejects_partial_unknown_or_missing_components(self):
        for b, c in ((100.0, None), (None, 100.0), (100.0, MISSING), (MISSING, 100.0)):
            for scope in ("execution_only", "execution_plus_overhead"):
                with self.subTest(b=b, c=c, scope=scope):
                    self.amounts(b, c, 0.001, scope)
                    with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
                        validate_contract(self.data)

    def test_execution_only_accepts_equal_sum_with_float_roundoff(self):
        self.amounts(0.1, 0.2, 0.3, "execution_only")
        before = copy.deepcopy(self.data)
        self.assertEqual(self.audit()["relationship_check"], "execution_sum_equal")
        self.assertEqual(self.data, before)

    def test_execution_only_rejects_lower_and_higher_totals(self):
        for total in (0.29, 0.31):
            with self.subTest(total=total):
                self.amounts(0.1, 0.2, total, "execution_only")
                with self.assertRaisesRegex(ContractError, "cost_usd"):
                    validate_contract(self.data)

    def test_inclusive_total_allows_explicit_positive_overhead(self):
        self.amounts(0.1, 0.2, 0.35)
        audit = self.audit()
        self.assertEqual(audit["relationship_check"], "execution_sum_covered")
        self.assertEqual(audit["cost_scope"], "execution_plus_overhead")
        self.assertEqual(audit["scope_source"], "explicit")

    def test_omitted_scope_keeps_the_v02_inclusive_meaning(self):
        self.amounts(0.1, 0.2, 0.35, MISSING)
        audit = self.audit()
        self.assertEqual(audit["cost_scope"], "execution_plus_overhead")
        self.assertEqual(audit["scope_source"], "default")
        self.amounts(100.0, 100.0, 0.001, MISSING)
        with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
            validate_contract(self.data)

    def test_unknown_total_is_not_reconstructed_even_when_both_parts_are_known(self):
        for scope in ("execution_only", "execution_plus_overhead"):
            with self.subTest(scope=scope):
                self.amounts(0.1, 0.2, None, scope)
                before = copy.deepcopy(self.data)
                audit = self.audit()
                self.assertEqual(audit["relationship_check"], "unknown_total")
                self.assertIsNone(audit["event_total_cost_usd"])
                summary, row = self.replay()
                self.assertIsNone(row["costs"]["additional_usd"])
                self.assertIsNone(row["costs"]["within_additional_cap"])
                self.assertFalse(summary["accounting_checks_passed"])
                self.assertEqual(self.data, before)

    def test_known_total_does_not_fill_unknown_allocations(self):
        for scope in ("execution_only", "execution_plus_overhead"):
            for missing in (None, MISSING):
                with self.subTest(scope=scope, missing=missing):
                    self.amounts(0.1, missing, 0.2, scope)
                    before = copy.deepcopy(self.data)
                    audit = self.audit()
                    self.assertEqual(audit["relationship_check"], "known_lower_bound_only")
                    self.assertEqual(audit["known_execution_subtotal_usd"], 0.1)
                    self.assertIsNone(audit["complete_execution_sum_usd"])
                    self.assertEqual(audit["unknown_execution_cost_sides"], ["candidate"])
                    summary, row = self.replay()
                    self.assertTrue(row["costs"]["within_additional_cap"])
                    self.assertTrue(summary["accounting_checks_passed"])
                    self.assertEqual(self.data, before)

    def test_unknown_breakdown_is_not_reported_as_zero_execution_cost(self):
        for missing in (None, MISSING):
            with self.subTest(missing=missing):
                self.amounts(missing, missing, 0.2)
                before = copy.deepcopy(self.data)
                audit = self.audit()
                self.assertEqual(audit["relationship_check"], "unknown_execution_breakdown")
                self.assertIsNone(audit["known_execution_subtotal_usd"])
                self.assertIsNone(audit["complete_execution_sum_usd"])
                self.assertEqual(audit["event_total_cost_usd"], 0.2)
                self.assertEqual(self.data, before)

    def test_failed_or_not_run_execution_does_not_erase_reported_cost(self):
        for status in ("failed", "not_run"):
            with self.subTest(status=status):
                self.amounts(100.0, 100.0, 0.001)
                run = self.event["observation"]["baseline"]
                run.update(status=status, error="Fictional executor failure")
                run.pop("utility", None)
                run.pop("success", None)
                self.event.update(status="failed", valid=False)
                with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
                    validate_contract(self.data)

    def test_total_covers_sum_not_just_each_component_individually(self):
        self.amounts(0.2, 0.2, 0.3)
        with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
            validate_contract(self.data)

    def test_roundoff_tolerance_does_not_accept_material_shortfalls(self):
        for scope in ("execution_only", "execution_plus_overhead"):
            with self.subTest(scope=scope):
                self.amounts(0.1, 0.2, 0.3 - 1e-13, scope)
                validate_contract(self.data)
                self.amounts(0.1, 0.2, 0.3 - 1e-10, scope)
                with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
                    validate_contract(self.data)
                self.amounts(100.0, 100.0, 200.0 - 1e-8, scope)
                with self.assertRaisesRegex(ContractError, "below known paired execution cost"):
                    validate_contract(self.data)

    def test_invalid_scope_names_and_types_are_rejected(self):
        for scope in ("execution", "", None, [], {}, True):
            with self.subTest(scope=scope):
                self.amounts(0.1, 0.2, 0.3, scope)
                with self.assertRaisesRegex(ContractError, "cost_scope"):
                    validate_contract(self.data)

    def test_component_sum_overflow_is_a_controlled_validation_error(self):
        self.amounts(1e308, 1e308, None)
        with self.assertRaisesRegex(ContractError, "finite numerical range"):
            validate_contract(self.data)

    def test_valid_total_is_charged_once_without_readding_components(self):
        self.amounts(0.1, 0.2, 0.35)
        summary, row = self.replay()
        self.assertAlmostEqual(row["costs"]["extra_evidence_usd"], 0.35)
        self.assertAlmostEqual(row["costs"]["total_decision_usd"], 0.45)
        self.assertAlmostEqual(summary["research_cost_accounting"]["recorded_total_usd"], 0.82)
        self.assertTrue(row["costs"]["within_additional_cap"])
        self.assertTrue(summary["accounting_checks_passed"])


if __name__ == "__main__":
    unittest.main()
