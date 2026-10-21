"""Conditional sample-count calculation for the pilot's Hoeffding threshold.

Given a hypothetical *observed* paired mean gain m, find the smallest n obeying
    m - width * sqrt(2 * log(2 / alpha) / n) > delta + indifference
and the declared minimum number of pairs. This is not a prospective power
calculation: future observations need not have the hypothetical mean. Freeze
thresholds and confidence settings before collecting evaluation data; do not
use this tool to relax them after seeing a result.
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Any


class SamplePlanError(ValueError):
    """A planning input is outside its declared numerical domain."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SamplePlanError(f"{name} must be a finite number, not a boolean")
    try:
        result = float(value)
    except OverflowError as exc:
        raise SamplePlanError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise SamplePlanError(f"{name} must be finite")
    return result


def _cost(value: Any, name: str) -> float | None:
    if value is None:
        return None
    result = _finite(value, name)
    if result < 0:
        raise SamplePlanError(f"{name} must be nonnegative")
    return result


def plan_sample_size(
    observed_mean_gain: float = 0.10,
    delta: float = 0.05,
    indifference: float = 0.01,
    alpha: float = 0.05,
    utility_bounds: tuple[float, float] | list[float] = (0.0, 1.0),
    min_pairs: int = 3,
    baseline_cost_usd: float | None = None,
    candidate_cost_usd: float | None = None,
) -> dict:
    """Return a JSON-compatible plan for one frozen modification.

    A pair is one independent future task, executed once by each version.
    Repeating the same task does not automatically create independent pairs.
    A nonpositive gap above the threshold is unattainable for any finite n.
    """
    mean = _finite(observed_mean_gain, "observed_mean_gain")
    delta = _finite(delta, "delta")
    margin = _finite(indifference, "indifference")
    alpha = _finite(alpha, "alpha")
    if margin < 0:
        raise SamplePlanError("indifference must be nonnegative")
    if not 0 < alpha < 1:
        raise SamplePlanError("alpha must satisfy 0 < alpha < 1")
    if isinstance(min_pairs, bool) or not isinstance(min_pairs, int) or min_pairs < 1:
        raise SamplePlanError("min_pairs must be a positive integer")
    if not isinstance(utility_bounds, (tuple, list)) or len(utility_bounds) != 2:
        raise SamplePlanError("utility_bounds must contain two numbers")
    lo, hi = (_finite(value, "utility_bounds") for value in utility_bounds)
    width = hi - lo
    if not math.isfinite(width) or width <= 0:
        raise SamplePlanError("utility_bounds must be increasing with finite width")
    if not -width <= mean <= width:
        raise SamplePlanError("observed_mean_gain is outside the bounded paired-difference range")
    threshold = delta + margin
    if not math.isfinite(threshold):
        raise SamplePlanError("delta + indifference must be finite")
    baseline_cost = _cost(baseline_cost_usd, "baseline_cost_usd")
    candidate_cost = _cost(candidate_cost_usd, "candidate_cost_usd")

    # Subtracting logs also handles tiny positive alpha without 2 / alpha
    # overflowing. It is mathematically identical to log(2 / alpha).
    factor = 2 * (math.log(2) - math.log(alpha))

    def radius(n: int) -> float:
        return width * math.sqrt(factor / n)

    def crosses(n: int) -> bool:
        return mean - radius(n) > threshold

    required_pairs = None
    gap = mean - threshold
    if gap > 0:
        try:
            cutoff = factor * (width / gap) ** 2
            if not math.isfinite(cutoff):
                raise OverflowError
            required_pairs = max(min_pairs, math.floor(cutoff) + 1)
            # Keep strict > at floating-point boundaries. Integer bisection
            # avoids an enormous linear adjustment when the mean lies only
            # one representable float above the threshold.
            low = min_pairs - 1
            while not crosses(required_pairs):
                low = required_pairs
                required_pairs *= 2
            while required_pairs - low > 1:
                midpoint = (low + required_pairs) // 2
                if crosses(midpoint):
                    required_pairs = midpoint
                else:
                    low = midpoint
        except OverflowError as exc:
            raise SamplePlanError("parameters exceed the supported numerical range") from exc

    total_cost = None
    if required_pairs is not None and baseline_cost is not None and candidate_cost is not None:
        try:
            total_cost = required_pairs * (baseline_cost + candidate_cost)
        except OverflowError as exc:
            raise SamplePlanError("heldout cost estimate exceeds the finite numerical range") from exc
        if not math.isfinite(total_cost):
            raise SamplePlanError("heldout cost estimate exceeds the finite numerical range")

    return {
        "kind": "conditional_hoeffding_threshold_plan",
        "status": "attainable" if required_pairs is not None else "unattainable",
        "hypothetical_observed_mean_gain": mean,
        "settings": {
            "delta": delta, "indifference": margin, "alpha": alpha,
            "utility_bounds": [lo, hi], "min_pairs": min_pairs,
        },
        "strict_threshold": threshold,
        "criterion": "m - width * sqrt(2 * log(2 / alpha) / n) > delta + indifference",
        "minimum_pairs": required_pairs,
        "radius_at_minimum": radius(required_pairs) if required_pairs is not None else None,
        "raw_lower_bound_at_minimum": (
            mean - radius(required_pairs) if required_pairs is not None else None
        ),
        "small_pilot_examples": [
            {
                "n_pairs": n,
                "radius": radius(n),
                "raw_lower_bound": mean - radius(n),
                "crosses_threshold_and_minimum": n >= min_pairs and crosses(n),
            }
            for n in (20, 50, 100)
        ],
        "cost_estimate": {
            "baseline_per_task_usd": baseline_cost,
            "candidate_per_task_usd": candidate_cost,
            "heldout_execution_total_usd_per_modification": total_cost,
            "includes": "One baseline and one candidate execution per independent heldout task.",
            "excludes": [
                "candidate generation", "initial evidence", "additional B/C validation",
                "judge calls", "calibration", "retries and orchestration",
            ],
            "unknown_cost_policy": "Total is null when either per-task cost is unknown or no finite n exists.",
        },
        "interpretation": [
            "Conditional threshold crossing at a hypothetical observed mean; not a statistical power guarantee.",
            "Independent paired bounded utility differences are assumed; repeated original tasks are not automatically independent future tasks.",
            "Alpha applies to one modification; no simultaneous guarantee across modifications.",
            "Freeze thresholds, confidence settings, and evaluation sampling before data collection; do not tune them after seeing results.",
            "Small pilots can describe observed gains and failures even when reference labels remain unresolved.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mean-gain", type=float, default=0.10, help="Hypothetical observed mean paired gain")
    parser.add_argument("--delta", type=float, default=0.05)
    parser.add_argument("--indifference", type=float, default=0.01)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--utility-min", type=float, default=0.0)
    parser.add_argument("--utility-max", type=float, default=1.0)
    parser.add_argument("--min-pairs", type=int, default=3)
    parser.add_argument("--baseline-cost-usd", type=float)
    parser.add_argument("--candidate-cost-usd", type=float)
    args = parser.parse_args()
    try:
        result = plan_sample_size(
            observed_mean_gain=args.mean_gain, delta=args.delta,
            indifference=args.indifference, alpha=args.alpha,
            utility_bounds=(args.utility_min, args.utility_max), min_pairs=args.min_pairs,
            baseline_cost_usd=args.baseline_cost_usd,
            candidate_cost_usd=args.candidate_cost_usd,
        )
    except SamplePlanError as exc:
        parser.exit(2, f"sample plan rejected: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
