"""Numerical and input-contract tests; these are not agent experiments."""

import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

from sample_plan import SamplePlanError, plan_sample_size


class SamplePlanTests(unittest.TestCase):
    def test_default_requires_4612_pairs_and_is_minimal(self):
        result = plan_sample_size()
        self.assertEqual(result["status"], "attainable")
        self.assertEqual(result["minimum_pairs"], 4612)
        threshold = 0.05 + 0.01
        self.assertGreater(0.10 - math.sqrt(2 * math.log(40) / 4612), threshold)
        self.assertLessEqual(0.10 - math.sqrt(2 * math.log(40) / 4611), threshold)

    def test_equality_is_not_enough_for_strict_threshold(self):
        # log(2 / alpha) = 1, so n = 2 gives a lower bound of exactly zero.
        result = plan_sample_size(
            observed_mean_gain=1.0, delta=0.0, indifference=0.0,
            alpha=2 / math.e, min_pairs=1,
        )
        self.assertEqual(result["minimum_pairs"], 3)

    def test_declared_minimum_pairs_is_enforced(self):
        self.assertEqual(plan_sample_size(min_pairs=5000)["minimum_pairs"], 5000)

    def test_mean_one_float_above_threshold_finishes_with_strict_crossing(self):
        threshold = 0.05 + 0.01
        mean = math.nextafter(threshold, math.inf)
        result = plan_sample_size(observed_mean_gain=mean)
        self.assertGreater(result["minimum_pairs"], 10 ** 30)
        self.assertGreater(result["raw_lower_bound_at_minimum"], threshold)

    def test_mean_at_or_below_threshold_is_unattainable(self):
        for mean in (0.05 + 0.01, 0.05, -0.10):
            with self.subTest(mean=mean):
                result = plan_sample_size(observed_mean_gain=mean)
                self.assertEqual(result["status"], "unattainable")
                self.assertIsNone(result["minimum_pairs"])
                self.assertIsNone(result["radius_at_minimum"])

    def test_pilot_radii_match_known_values(self):
        examples = plan_sample_size()["small_pilot_examples"]
        self.assertEqual([row["n_pairs"] for row in examples], [20, 50, 100])
        for row, expected in zip(examples, (0.607361, 0.384129, 0.271620)):
            self.assertAlmostEqual(row["radius"], expected, places=5)
            self.assertFalse(row["crosses_threshold_and_minimum"])

    def test_bounds_scale_with_mean_and_threshold(self):
        result = plan_sample_size(
            observed_mean_gain=0.20, delta=0.10, indifference=0.02,
            utility_bounds=(-1, 1),
        )
        self.assertEqual(result["minimum_pairs"], 4612)

    def test_known_costs_count_both_sides_for_each_pair(self):
        result = plan_sample_size(baseline_cost_usd=0.01, candidate_cost_usd=0.02)
        cost = result["cost_estimate"]
        self.assertAlmostEqual(cost["heldout_execution_total_usd_per_modification"], 138.36)
        self.assertIn("candidate generation", cost["excludes"])
        self.assertIn("additional B/C validation", cost["excludes"])

    def test_unknown_or_unattainable_cost_is_null(self):
        for kwargs in ({}, {"baseline_cost_usd": 0.01}, {"candidate_cost_usd": 0.02},
                       {"observed_mean_gain": 0.0, "baseline_cost_usd": 0.01,
                        "candidate_cost_usd": 0.02}):
            with self.subTest(kwargs=kwargs):
                result = plan_sample_size(**kwargs)
                self.assertIsNone(result["cost_estimate"]["heldout_execution_total_usd_per_modification"])
        zero = plan_sample_size(baseline_cost_usd=0, candidate_cost_usd=0)
        self.assertEqual(zero["cost_estimate"]["heldout_execution_total_usd_per_modification"], 0)

    def test_booleans_and_nonfinite_scalars_are_rejected(self):
        for field in ("observed_mean_gain", "delta", "indifference", "alpha",
                      "baseline_cost_usd", "candidate_cost_usd"):
            for value in (False, True, float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value), self.assertRaises(SamplePlanError):
                    plan_sample_size(**{field: value})

    def test_invalid_bounds_and_impossible_mean_are_rejected(self):
        for bounds in ((1, 1), (2, 1), (False, 1), (0, float("inf")),
                       (0, float("nan")), (0,), "01", (-1e308, 1e308)):
            with self.subTest(bounds=bounds), self.assertRaises(SamplePlanError):
                plan_sample_size(utility_bounds=bounds)
        for mean in (-1.01, 1.01):
            with self.subTest(mean=mean), self.assertRaises(SamplePlanError):
                plan_sample_size(observed_mean_gain=mean)

    def test_invalid_parameter_domains_are_rejected(self):
        for kwargs in ({"alpha": 0}, {"alpha": 1}, {"alpha": -0.1},
                       {"indifference": -0.01}, {"min_pairs": 0},
                       {"min_pairs": True}, {"min_pairs": 3.0},
                       {"baseline_cost_usd": -0.01}, {"candidate_cost_usd": -0.01}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SamplePlanError):
                plan_sample_size(**kwargs)

    def test_cli_produces_json_and_rejects_invalid_input(self):
        script = str(Path(__file__).with_name("sample_plan.py"))
        good = subprocess.run([sys.executable, script], capture_output=True, text=True, check=True)
        result = json.loads(good.stdout)
        self.assertEqual(result["minimum_pairs"], 4612)
        self.assertIsNone(result["cost_estimate"]["heldout_execution_total_usd_per_modification"])
        bad = subprocess.run([sys.executable, script, "--alpha", "nan"],
                             capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        self.assertIn("sample plan rejected", bad.stderr)


if __name__ == "__main__":
    unittest.main()
