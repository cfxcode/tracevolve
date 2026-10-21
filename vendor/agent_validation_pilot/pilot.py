#!/usr/bin/env python3
"""Offline A/B/C evidence replay. Demo output is NOT an agent experiment.

Each unit contains ONE frozen patch versus ONE frozen baseline. The external
judge receives only its allotted evidence, never the held-out evaluation pairs.
The JSON boundary prevents accidental input leakage; it is not an OS sandbox.
Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any

from contracts import ContractError, validate_contract


POLICY = (
    "Decide whether to accept this single frozen patch relative to its fixed "
    "baseline for subsequent tasks, where an accepted patch is applied exactly "
    "as supplied. Use only the supplied evidence. Initial/full validation and "
    "diagnostic intervention are different evidence channels: a diagnostic "
    "result is not a sample of deployment utility. Do not invent missing "
    "observations. Use a fixed 1:1 false-accept/false-reject loss and the supplied "
    "minimum mean utility improvement threshold. Return JSON with accept "
    "(boolean), rationale (string), optional estimated_gain (number or null), "
    "and usage.cost_usd (number or null). A belief or estimate is not a verified "
    "future effect. No held-out outcomes or hidden reference labels are supplied."
)
FORBIDDEN_EVIDENCE_KEYS = {
    "heldout", "heldout_results", "ground_truth_gain", "reference_label",
    "true_effect", "oracle_label",
    "reference_effect_class", "heldout_references", "reference_resolvability",
    "fixed_loss_on_resolved_reference", "realized_heldout_mean_gain",
    "paired_bc_comparison",
}


class ValidationError(ValueError):
    pass


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite number")
    if not math.isfinite(float(value)):
        raise ValidationError(f"{name} must be finite")
    return float(value)


def money(value: Any, name: str) -> float | None:
    if value is None:
        return None
    result = finite_number(value, name)
    if result < 0:
        raise ValidationError(f"{name} must be nonnegative or null")
    return result


def cost_sum(values: list[float | None]) -> float | None:
    # Missing costs are never silently treated as zero.
    return None if any(v is None for v in values) else math.fsum(values)


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def reject_hidden_keys(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_EVIDENCE_KEYS:
                raise ValidationError(f"forbidden held-out/reference key at {path}.{key}")
            reject_hidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_hidden_keys(item, f"{path}[{index}]")


def value_of(outcome: dict, path: str) -> float:
    if "utility" in outcome:
        return finite_number(outcome["utility"], path + ".utility")
    if isinstance(outcome.get("success"), bool):
        return float(outcome["success"])
    raise ValidationError(f"{path} requires numeric utility or boolean success")


def validate_data(data: Any, demo: bool) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise ValidationError("input must be an object with schema_version=2; legacy real records require recollection or explicit provenance repair")
    if "additional_budget_usd" not in data:
        raise ValidationError("additional_budget_usd is required; unknown is null")
    cap = money(data["additional_budget_usd"], "additional_budget_usd")
    overhead_input = data.get("shared_overheads", {})
    if not isinstance(overhead_input, dict):
        raise ValidationError("shared_overheads must be an object")
    overheads = {key: money(overhead_input.get(key), "shared_overheads." + key)
                 for key in ("candidate_generation_usd", "calibration_usd")}
    settings = data.get("decision", {})
    if not isinstance(settings, dict):
        raise ValidationError("decision must be an object")
    delta = finite_number(settings.get("delta", 0.05), "decision.delta")
    margin = finite_number(settings.get("indifference", 0.01), "decision.indifference")
    alpha = finite_number(settings.get("alpha", 0.05), "decision.alpha")
    minimum = settings.get("min_heldout_pairs", 3)
    bounds = settings.get("utility_bounds", [0.0, 1.0])
    if margin < 0 or not 0 < alpha < 1:
        raise ValidationError("indifference must be nonnegative and 0 < alpha < 1")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValidationError("min_heldout_pairs must be a positive integer")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValidationError("utility_bounds must contain two numbers")
    lo, hi = [finite_number(x, "utility_bounds") for x in bounds]
    if lo >= hi:
        raise ValidationError("utility_bounds must be increasing")
    units = data.get("units")
    if not isinstance(units, list) or not units:
        raise ValidationError("units must be a nonempty list")
    seen_units: set[str] = set()
    any_synthetic = False
    for unit in units:
        if not isinstance(unit, dict):
            raise ValidationError("each unit must be an object")
        for key in ("unit_id", "baseline_id", "patch_id", "patch_description"):
            if not isinstance(unit.get(key), str) or not unit[key]:
                raise ValidationError(f"each unit requires a nonempty {key}")
        if unit["unit_id"] in seen_units:
            raise ValidationError("unit_id must be unique")
        seen_units.add(unit["unit_id"])
        seen_events: set[str] = set()
        for channel in ("initial_evidence", "extra_B", "extra_C"):
            events = unit.get(channel)
            if not isinstance(events, list):
                raise ValidationError(f"{unit['unit_id']}.{channel} must be a list")
            for event in events:
                if not isinstance(event, dict):
                    raise ValidationError("events must be objects")
                for key in ("event_id", "type"):
                    if not isinstance(event.get(key), str) or not event[key]:
                        raise ValidationError(f"each event requires {key}")
                if event["event_id"] in seen_events:
                    raise ValidationError("event_id must be unique within a unit")
                seen_events.add(event["event_id"])
                if "observation" not in event or "cost_usd" not in event:
                    raise ValidationError("events require observation and cost_usd (may be null)")
                if not isinstance(event.get("synthetic"), bool):
                    raise ValidationError("events require a boolean synthetic flag")
                if "valid" in event and not isinstance(event["valid"], bool):
                    raise ValidationError("event.valid must be boolean")
                money(event["cost_usd"], "event.cost_usd")
                reject_hidden_keys(event["observation"], "event.observation")
                any_synthetic |= event["synthetic"]
                if demo and not event["synthetic"]:
                    raise ValidationError("--demo refuses any nonsynthetic evidence")
        pairs = unit.get("heldout")
        if not isinstance(pairs, list):
            raise ValidationError("heldout must be a list; it may be empty")
        seen_tasks: set[str] = set()
        for pair in pairs:
            if not isinstance(pair, dict) or not isinstance(pair.get("task_id"), str):
                raise ValidationError("heldout pairs require task_id")
            if not pair["task_id"] or pair["task_id"] in seen_tasks:
                raise ValidationError("heldout task_id must be nonempty and unique per unit")
            seen_tasks.add(pair["task_id"])
            if not isinstance(pair.get("synthetic"), bool):
                raise ValidationError("heldout pairs require boolean synthetic")
            any_synthetic |= pair["synthetic"]
            if demo and not pair["synthetic"]:
                raise ValidationError("--demo refuses nonsynthetic held-out pairs")
            for side in ("baseline", "candidate"):
                outcome = pair.get(side)
                if not isinstance(outcome, dict) or "execution_cost_usd" not in outcome:
                    raise ValidationError("heldout outcomes require execution_cost_usd (may be null)")
                utility = value_of(outcome, "heldout." + side)
                if not lo <= utility <= hi:
                    raise ValidationError("heldout utility outside declared utility_bounds")
                money(outcome["execution_cost_usd"], "heldout.execution_cost_usd")
    try:
        contract_audit = validate_contract(data)
    except ContractError as exc:
        raise ValidationError(str(exc)) from exc
    # These are exactly the additional structured fields exposed to the judge.
    # Textual/semantic leakage still requires an independent collection audit.
    for unit in units:
        reject_hidden_keys(unit["source_task"], "source_task")
        for channel in ("initial_evidence", "extra_B", "extra_C"):
            for event in unit[channel]:
                for key in ("task", "binding", "intervention"):
                    if key in event:
                        reject_hidden_keys(event[key], "event." + key)
    return {
        "cap": cap, "delta": delta, "indifference": margin, "alpha": alpha,
        "min_pairs": minimum, "bounds": [lo, hi], "any_synthetic": any_synthetic,
        "shared_overheads": overheads, "input_contract_audit": contract_audit,
    }


def public_event(event: dict) -> dict:
    # Metadata not on this whitelist (including accidental reference labels) is
    # never sent to the judgment process.
    public = {"event_id": event["event_id"], "type": event["type"], "status": event["status"],
              "observation": event["observation"], "valid": event.get("valid", True)}
    public["binding"] = {key: event["binding"][key] for key in
                         ("baseline_sha256", "candidate_sha256", "patch_sha256", "source_task_sha256")}
    if event["type"] == "paired_validation":
        public["task"] = {key: event["task"][key] for key in
                          ("content", "sha256", "source_id", "identifier_contract")}
        public["intervention"] = {"kind": event["intervention"]["kind"]}
        if event["intervention"]["kind"] == "identifier_renaming":
            public["intervention"]["mapping"] = event["intervention"]["mapping"]
    return public


def judge_request(unit: dict, events: list[dict], settings: dict) -> dict:
    return {
        "protocol_version": 2,
        "instructions": POLICY,
        "unit_id": unit["unit_id"],
        "baseline_id": unit["baseline_id"],
        "patch_id": unit["patch_id"],
        "patch_description": unit["patch_description"],
        "frozen": {
            side: {key: unit["frozen"][side][key]
                   for key in (("content", "sha256") if side != "patch" else
                               ("format", "base_sha256", "content", "sha256"))}
            for side in ("baseline", "candidate", "patch")
        },
        "source_task": {key: unit["source_task"][key]
                        for key in ("content", "sha256", "source_id", "identifier_contract")},
        "minimum_mean_utility_improvement": settings["delta"],
        "false_accept_cost": 1,
        "false_reject_cost": 1,
        "evidence": [public_event(e) for e in events],
    }


def call_judge(request: dict, command: list[str] | None,
               timeout: float, demo: bool) -> tuple[dict | None, float | None, str | None, float]:
    started = time.monotonic()
    if demo:
        # This constant control tests routing/accounting only. It deliberately
        # cannot manufacture a result in favour of the diagnostic arm.
        return ({"accept": False, "estimated_gain": None,
                 "rationale": "SYNTHETIC PLUMBING ONLY: constant always-reject control.",
                 "usage": {"cost_usd": 0.0}}, 0.0, None, time.monotonic() - started)
    try:
        result = subprocess.run(
            command, input=json.dumps(request, ensure_ascii=False, allow_nan=False),
            text=True, capture_output=True, timeout=timeout, check=False,
        )
        if result.returncode != 0:
            return None, None, f"judge exited with code {result.returncode}; incurred cost unknown", time.monotonic() - started
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or not isinstance(response.get("accept"), bool):
            raise ValidationError("judge must return an object with boolean accept")
        if not isinstance(response.get("rationale", ""), str):
            raise ValidationError("judge rationale must be a string")
        gain = response.get("estimated_gain")
        if gain is not None:
            finite_number(gain, "judge.estimated_gain")
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            raise ValidationError("judge usage must be an object")
        cost = money(usage.get("cost_usd"), "judge.usage.cost_usd")
        # Do not retain arbitrary response fields that might contain secrets or
        # unsolicited hidden data. Raw model content belongs in the adapter's log.
        kept = {"accept": response["accept"], "estimated_gain": gain,
                "rationale": response.get("rationale", ""), "usage": {"cost_usd": cost}}
        return kept, cost, None, time.monotonic() - started
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValidationError) as exc:
        # Failure is not a free call and is not silently converted to rejection.
        return None, None, f"{type(exc).__name__}: judgment unavailable; incurred cost unknown", time.monotonic() - started


def heldout_summary(unit: dict, settings: dict) -> dict:
    pairs = unit["heldout"]
    count = len(pairs)
    differences = [value_of(p["candidate"], "candidate") - value_of(p["baseline"], "baseline")
                   for p in pairs]
    mean = math.fsum(differences) / count if count else None
    lo, hi = settings["bounds"]
    width = hi - lo
    radius = width * math.sqrt(2 * math.log(2 / settings["alpha"]) / count) if count else None
    interval = [max(-width, mean - radius), min(width, mean + radius)] if count else None
    effect_class = "unresolved"
    if count >= settings["min_pairs"]:
        if interval[0] > settings["delta"] + settings["indifference"]:
            effect_class = "above_threshold"
        elif interval[1] < settings["delta"] - settings["indifference"]:
            effect_class = "below_threshold"
    return {
        "n_pairs": count,
        "mean_candidate_minus_baseline_utility": mean,
        "mean_utility_interval": interval,
        "interval_method": "Hoeffding for independent paired bounded utility differences",
        "interval_alpha": settings["alpha"],
        "interval_scope": "per unit; no simultaneous multiple-unit guarantee; independence is an assumption",
        "reference_effect_class": effect_class,
        "reference_status": "estimated from finite heldout; not oracle truth",
        "baseline_mean_utility": (math.fsum(value_of(p["baseline"], "baseline") for p in pairs) / count if count else None),
        "candidate_mean_utility": (math.fsum(value_of(p["candidate"], "candidate") for p in pairs) / count if count else None),
        "baseline_execution_cost_usd": cost_sum([p["baseline"]["execution_cost_usd"] for p in pairs]),
        "candidate_execution_cost_usd": cost_sum([p["candidate"]["execution_cost_usd"] for p in pairs]),
    }


def assess_decision(response: dict | None, reference: dict) -> dict:
    if response is None:
        return {"accept": None, "fixed_loss_on_resolved_reference": None,
                "realized_heldout_mean_gain": None, "decision_status": "judge_failed"}
    accepted = response["accept"]
    label = reference["reference_effect_class"]
    loss = None if label == "unresolved" else int(accepted != (label == "above_threshold"))
    gain = reference["mean_candidate_minus_baseline_utility"]
    return {
        "accept": accepted,
        "fixed_loss_on_resolved_reference": loss,
        "realized_heldout_mean_gain": gain if accepted else (0.0 if gain is not None else None),
        "selected_heldout_mean_utility": reference["candidate_mean_utility"] if accepted else reference["baseline_mean_utility"],
        "selected_heldout_execution_cost_usd": reference["candidate_execution_cost_usd"] if accepted else reference["baseline_execution_cost_usd"],
        "decision_status": "completed",
    }


def summarize_adoption_decisions(rows: list[dict], references: dict[str, dict]) -> dict:
    """Describe completion and compare B/C on identical, explicit unit sets.

    A failed judgment is missing, not a rejection or a free correct decision.
    Complete-case comparisons are conditional descriptions; when either arm
    has missing decisions they cannot establish overall superiority. Bounds
    additionally show every possible loss in [0, 1] for missing decisions on
    the entire reference-resolvable cohort, without imputing a single value.
    """
    unit_ids = list(references)
    total = len(unit_ids)
    by_group: dict[str, dict[str, dict]] = {g: {} for g in ("A", "B", "C")}
    for row in rows:
        unit_id, group = row["unit_id"], row["group"]
        if unit_id not in references or group not in by_group:
            raise ValidationError("aggregation row has unknown unit_id or group")
        if unit_id in by_group[group]:
            raise ValidationError(f"duplicate aggregation row for {unit_id}/{group}")
        by_group[group][unit_id] = row

    def mean(values: list[float]) -> float | None:
        return math.fsum(values) / len(values) if values else None

    def coverage(ids: list[str]) -> dict:
        return {"count": len(ids), "denominator": total,
                "rate": len(ids) / total if total else None, "unit_ids": ids}

    def completed(group: str, unit_id: str) -> bool:
        row = by_group[group].get(unit_id)
        return (row is not None and row["evaluation"]["decision_status"] == "completed"
                and isinstance(row["evaluation"]["accept"], bool))

    def completion_reasons(unit_id: str) -> list[str]:
        return [f"{group}_missing_decision_row" if unit_id not in by_group[group]
                else f"{group}_judge_failed"
                for group in ("B", "C") if not completed(group, unit_id)]

    resolved_ids = [u for u in unit_ids if references[u]["reference_effect_class"]
                    in ("above_threshold", "below_threshold")]
    resolved_set = set(resolved_ids)
    unresolved_ids = [u for u in unit_ids if u not in resolved_set]
    groups = {}
    for group in ("A", "B", "C"):
        completed_ids = [u for u in unit_ids if completed(group, u)]
        loss_ids = [u for u in completed_ids if u in resolved_set]
        gain_ids = [u for u in completed_ids
                    if by_group[group][u]["evaluation"]["realized_heldout_mean_gain"] is not None]
        evaluations = by_group[group]
        groups[group] = {
            "total_units": total,
            "decision_completion": {
                **coverage(completed_ids),
                "failed_unit_ids": [u for u in unit_ids if not completed(group, u)],
            },
            "reference_resolvability": {
                **coverage(resolved_ids), "unresolved_unit_ids": unresolved_ids,
                "note": "Reference resolvability is measured independently of judgment completion.",
            },
            "completed_and_resolvable": coverage(loss_ids),
            "completed_and_gain_observed": coverage(gain_ids),
            "accepted": sum(evaluations[u]["evaluation"]["accept"] is True
                            for u in completed_ids),
            "conditional_mean_fixed_loss_completed_and_resolved": mean([
                evaluations[u]["evaluation"]["fixed_loss_on_resolved_reference"] for u in loss_ids]),
            "conditional_mean_realized_gain_completed_and_observed": mean([
                evaluations[u]["evaluation"]["realized_heldout_mean_gain"] for u in gain_ids]),
            "conditional_means_note": (
                "Each mean conditions on its listed unit set; means on different completed sets "
                "must not be directly compared across groups. Use paired_BC_comparison."
            ),
            "total_decision_cost_usd": cost_sum([
                evaluations[u]["costs"]["total_decision_usd"] if u in evaluations else None
                for u in unit_ids]),
            "decision_cost_note": "initial evidence is charged per strategy for comparison; shared physical collection is not three separate runs",
        }

    joint_ids = [u for u in unit_ids if completed("B", u) and completed("C", u)]
    joint_set = set(joint_ids)
    pairing_complete = len(joint_ids) == total and total > 0
    loss_pairs, loss_excluded = [], []
    gain_pairs, gain_excluded = [], []
    for unit_id in unit_ids:
        reasons = completion_reasons(unit_id)
        loss_reasons = reasons + ([] if unit_id in resolved_set else ["reference_unresolved"])
        if loss_reasons:
            loss_excluded.append({"unit_id": unit_id, "reasons": loss_reasons})
        else:
            b_loss = by_group["B"][unit_id]["evaluation"]["fixed_loss_on_resolved_reference"]
            c_loss = by_group["C"][unit_id]["evaluation"]["fixed_loss_on_resolved_reference"]
            loss_pairs.append({"unit_id": unit_id, "B_loss": b_loss, "C_loss": c_loss,
                               "C_minus_B_loss": c_loss - b_loss})
        gain_reasons = list(reasons)
        if references[unit_id]["mean_candidate_minus_baseline_utility"] is None:
            gain_reasons.append("heldout_gain_unobserved")
        if gain_reasons:
            gain_excluded.append({"unit_id": unit_id, "reasons": gain_reasons})
        else:
            b_gain = by_group["B"][unit_id]["evaluation"]["realized_heldout_mean_gain"]
            c_gain = by_group["C"][unit_id]["evaluation"]["realized_heldout_mean_gain"]
            gain_pairs.append({"unit_id": unit_id, "B_gain": b_gain, "C_gain": c_gain,
                               "C_minus_B_gain": c_gain - b_gain})

    bound_rows = []
    for unit_id in resolved_ids:
        bounds = {}
        for group in ("B", "C"):
            loss = (by_group[group][unit_id]["evaluation"]["fixed_loss_on_resolved_reference"]
                    if completed(group, unit_id) else None)
            bounds[group] = [loss, loss] if loss is not None else [0, 1]
        bound_rows.append({
            "unit_id": unit_id,
            "B_loss_interval": bounds["B"], "C_loss_interval": bounds["C"],
            "C_minus_B_loss_interval": [bounds["C"][0] - bounds["B"][1],
                                        bounds["C"][1] - bounds["B"][0]],
        })

    def mean_bound(key: str) -> list[float] | None:
        return [mean([r[key][i] for r in bound_rows]) for i in (0, 1)] if bound_rows else None

    paired = {
        "status": "COMPLETE_PAIRING" if pairing_complete else "INCOMPLETE_PAIRING",
        "total_units": total,
        "joint_completion": coverage(joint_ids),
        "incomplete_units": [{"unit_id": u, "reasons": completion_reasons(u)}
                             for u in unit_ids if u not in joint_set],
        "full_cohort_decision_comparison_available": pairing_complete,
        "overall_superiority_conclusion": None,
        "conclusion_note": (
            "No overall superiority conclusion is permitted: incomplete pairing can select an easier "
            "subset even when both groups fail on the same units. Paired means below are conditional descriptions."
            if not pairing_complete else
            "All B/C decisions completed. Results below are descriptive; no inferential superiority test is implemented."
        ),
        "loss_comparison": {
            "analysis_population": "same units with completed B/C decisions and a resolvable heldout reference",
            "count": len(loss_pairs), "total_unit_denominator": total,
            "reference_resolvable_denominator": len(resolved_ids),
            "included_unit_ids": [p["unit_id"] for p in loss_pairs],
            "excluded_units": loss_excluded,
            "conditional_mean_B_fixed_loss": mean([p["B_loss"] for p in loss_pairs]),
            "conditional_mean_C_fixed_loss": mean([p["C_loss"] for p in loss_pairs]),
            "conditional_mean_C_minus_B_fixed_loss": mean([p["C_minus_B_loss"] for p in loss_pairs]),
            "difference_direction": "negative means lower C loss on this paired subset only",
            "unit_pairs": loss_pairs,
        },
        "gain_comparison": {
            "analysis_population": "same units with completed B/C decisions and observed heldout mean gain; reference need not be resolved",
            "count": len(gain_pairs), "total_unit_denominator": total,
            "included_unit_ids": [p["unit_id"] for p in gain_pairs],
            "excluded_units": gain_excluded,
            "conditional_mean_B_realized_gain": mean([p["B_gain"] for p in gain_pairs]),
            "conditional_mean_C_realized_gain": mean([p["C_gain"] for p in gain_pairs]),
            "conditional_mean_C_minus_B_realized_gain": mean([p["C_minus_B_gain"] for p in gain_pairs]),
            "difference_direction": "positive means greater C observed utility gain on this paired subset only",
            "unit_pairs": gain_pairs,
            "interpretation": "descriptive heldout utility only; not a monetary or inferential net-benefit conclusion",
        },
        "resolved_reference_loss_bounds": {
            "denominator": len(resolved_ids), "unit_ids": resolved_ids,
            "missing_decision_unit_ids": {g: [u for u in resolved_ids if not completed(g, u)]
                                          for g in ("B", "C")},
            "B_mean_loss_interval": mean_bound("B_loss_interval"),
            "C_mean_loss_interval": mean_bound("C_loss_interval"),
            "C_minus_B_mean_loss_interval": mean_bound("C_minus_B_loss_interval"),
            "unit_bounds": bound_rows,
            "interpretation": (
                "Algebraic missing-outcome bounds on the entire resolvable-reference cohort: observed "
                "losses remain fixed, each unavailable loss may be anywhere in [0,1]. These are not "
                "confidence intervals, do not resolve uncertain references, and do not prove superiority."
            ),
        },
        "reference_note": "Reference labels are finite-heldout estimates, not oracle truth; unresolved references are not relabeled as failures.",
    }
    return {"groups": groups, "paired_BC_comparison": paired}


def run(data: dict, output: Path, command: list[str] | None, demo: bool,
        timeout: float = 60.0, overwrite: bool = False) -> dict:
    if not demo and not command:
        raise ValidationError("provide --judge-command; absent real judge never falls back to demo")
    settings = validate_data(data, demo)
    output.mkdir(parents=True, exist_ok=True)
    trace_path = output / "traces.jsonl"
    summary_path = output / "summary.json"
    if not overwrite and (trace_path.exists() or summary_path.exists()):
        raise ValidationError("output already exists; choose another directory or use --overwrite")
    rows: list[dict] = []
    references: dict[str, dict] = {}
    with trace_path.open("w", encoding="utf-8") as trace:
        for unit in data["units"]:
            references[unit["unit_id"]] = heldout_summary(unit, settings)
            initial = unit["initial_evidence"]
            for group in ("A", "B", "C"):
                extra = [] if group == "A" else unit["extra_" + group]
                events = initial + extra
                request = judge_request(unit, events, settings)
                response, judge_cost, error, elapsed = call_judge(request, command, timeout, demo)
                initial_cost = cost_sum([e["cost_usd"] for e in initial])
                extra_cost = cost_sum([e["cost_usd"] for e in extra])
                additional_cost = cost_sum([extra_cost, judge_cost])
                total_cost = cost_sum([initial_cost, extra_cost, judge_cost])
                cap = settings["cap"] if group in ("B", "C") else None
                within_cap = (additional_cost <= cap + 1e-12
                              if cap is not None and additional_cost is not None else None)
                row = {
                    "unit_id": unit["unit_id"], "group": group,
                    "mode": "synthetic_plumbing" if demo else "external_judge_replay",
                    "shown_evidence_ids": [e["event_id"] for e in events],
                    "invalid_evidence_ids": [e["event_id"] for e in events if not e.get("valid", True)],
                    "request_sha256": digest(request), "judge_request": request,
                    "judge_response": response, "judge_error": error,
                    "judge_elapsed_seconds": elapsed,
                    "costs": {
                        "initial_evidence_usd": initial_cost,
                        "extra_evidence_usd": extra_cost, "judgment_usd": judge_cost,
                        "additional_usd": additional_cost, "total_decision_usd": total_cost,
                        "additional_cap_usd": cap, "within_additional_cap": within_cap,
                        "cost_complete": total_cost is not None,
                        "unknown_cost_event_ids": [e["event_id"] for e in events if e["cost_usd"] is None],
                        "cost_interpretation": "fictional supplied values" if settings["any_synthetic"] else "reported input/adapter costs; not independently verified",
                        "accounting_rule": "charge every listed event, including failed/invalid events; no cost-based cherry-picking",
                    },
                }
                trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                trace.flush()
                # Evaluation is added only after request serialization/judgment;
                # it never enters the public request.
                row["evaluation"] = assess_decision(response, references[unit["unit_id"]])
                rows.append(row)
    bc_pairs = []
    for unit in data["units"]:
        selected = {r["group"]: r for r in rows if r["unit_id"] == unit["unit_id"]}
        b_cost = selected["B"]["costs"]["additional_usd"]
        c_cost = selected["C"]["costs"]["additional_usd"]
        difference = c_cost - b_cost if b_cost is not None and c_cost is not None else None
        bc_pairs.append({
            "unit_id": unit["unit_id"],
            "equal_budget_caps": settings["cap"] is not None,
            "B_additional_usd": b_cost, "C_additional_usd": c_cost,
            "C_minus_B_additional_usd": difference,
            "equal_actual_added_spend": abs(difference) <= 1e-12 if difference is not None else None,
            "both_within_cap": (all(selected[g]["costs"]["within_additional_cap"] for g in ("B", "C"))
                                if all(selected[g]["costs"]["within_additional_cap"] is not None for g in ("B", "C")) else None),
        })
    any_failed = any(r["judge_response"] is None for r in rows)
    cost_complete = all(r["costs"]["cost_complete"] for r in rows)
    budget_valid = all(p["both_within_cap"] is True for p in bc_pairs)
    overhead_total = cost_sum(list(settings["shared_overheads"].values()))
    evaluation_total = cost_sum([
        ref[side + "_execution_cost_usd"] for ref in references.values()
        for side in ("baseline", "candidate")
    ])
    # Source evidence and held-out outcomes are collected once, while the three
    # judgment calls are separate. Per-strategy totals above are comparison
    # charges and must not be summed as if initial evidence was collected thrice.
    collection_total = cost_sum([
        e["cost_usd"] for unit in data["units"]
        for channel in ("initial_evidence", "extra_B", "extra_C")
        for e in unit[channel]
    ])
    judgment_total = cost_sum([r["costs"]["judgment_usd"] for r in rows])
    recorded_research_total = cost_sum([overhead_total, evaluation_total, collection_total, judgment_total])
    accounting_passed = cost_complete and budget_valid and recorded_research_total is not None
    adoption_summary = summarize_adoption_decisions(rows, references)
    summary = {
        "schema_version": 2,
        "status": "SYNTHETIC_PLUMBING_ONLY" if demo or settings["any_synthetic"] else
                  ("INCOMPLETE_OR_INVALID_REPLAY" if any_failed or not accounting_passed else "EXTERNAL_JUDGE_REPLAY_COMPLETED"),
        "real_agent_experiment_status": "NOT_RUN_BY_THIS_TOOL",
        "input_sha256": digest(data), "judge_policy_sha256": digest(POLICY),
        "judge_command_sha256": digest(command) if command else None,
        "decision_object": "one frozen patch versus baseline; binary acceptance; fixed subsequent application",
        "decision_settings": {k: v for k, v in settings.items()
                              if k not in ("any_synthetic", "shared_overheads", "input_contract_audit")},
        "input_contract_audit": settings["input_contract_audit"],
        "comparison_scope": "Identifier-renaming evidence versus repeated execution of the same unchanged source task; not versus new-task validation.",
        "all_decision_costs_complete": cost_complete,
        "all_upstream_costs_complete": overhead_total is not None,
        "all_heldout_evaluation_costs_complete": evaluation_total is not None,
        "all_additional_caps_respected": budget_valid,
        "accounting_checks_passed": accounting_passed,
        "scientific_validity": "NOT_ASSESSED",
        "data_provenance": "synthetic" if settings["any_synthetic"] else "claimed real; not independently verified",
        "shared_overheads": settings["shared_overheads"],
        "research_cost_accounting": {
            "shared_overheads_usd": overhead_total,
            "unique_listed_evidence_collection_usd": collection_total,
            "all_judgment_calls_usd": judgment_total,
            "heldout_evaluation_usd": evaluation_total,
            "recorded_total_usd": recorded_research_total,
            "note": "Candidate generation/calibration are common upstream costs; listed evidence is charged once here. Fictional demo prices are not measurements.",
        },
        **adoption_summary, "budget_comparisons": bc_pairs,
        "heldout_references": references,
        "decisions": [{"unit_id": r["unit_id"], "group": r["group"],
                       "reference_effect_class": references[r["unit_id"]]["reference_effect_class"],
                       **r["evaluation"]} for r in rows],
        "limitations": [
            "No candidate generation, model rollout collection, API billing verification, or automatic scheduler is implemented.",
            "Content hashes and paired-run bindings check internal consistency, not independent attestation that the claimed code actually ran.",
            "C versus B compares identifier-renaming evidence against repeated original-task validation, not equally costly new-task validation.",
            "Equal budget caps do not mean equal actual expenditure; inspect B/C differences.",
            "Replayed evidence must come from independent valid executions; reusing one stochastic observation is not a new sample.",
            "Unresolved reference effects are excluded from classification loss, never relabeled as failures.",
            "Group conditional means can use different unit sets; compare B/C only on the explicitly paired units.",
            "Incomplete B/C pairing prohibits an overall superiority conclusion; common completed cases can still be selected by difficulty.",
            "Heldout intervals assume independent bounded paired observations and are not simultaneous across units.",
            "Utility and USD are reported separately; no dollar cost is subtracted from a probability or uncalibrated utility.",
            "The serialized judge request excludes heldout data, but an external command is not OS-sandboxed.",
            "Accounting checks do not assess candidate provenance, split independence, task equivalence, unbiased sampling, or scientific validity.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="synthetic input only; constant reject control")
    mode.add_argument("--judge-command", help="external command; JSON stdin/stdout; no shell evaluation")
    parser.add_argument("--judge-timeout", type=float, default=60.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 0 < args.judge_timeout <= 300:
        parser.error("--judge-timeout must be in (0, 300]")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        command = shlex.split(args.judge_command) if args.judge_command else None
        if args.judge_command is not None and not command:
            raise ValidationError("--judge-command must not be empty")
        summary = run(data, args.output, command, args.demo, args.judge_timeout, args.overwrite)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": summary["status"], "real_agent_experiment_status": summary["real_agent_experiment_status"],
                      "accounting_checks_passed": summary["accounting_checks_passed"],
                      "scientific_validity": summary["scientific_validity"],
                      "units": len(data["units"]), "summary": str(args.output / "summary.json"),
                      "traces": str(args.output / "traces.jsonl")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
