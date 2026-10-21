"""Version 2 input contracts for the identifier-renaming pilot.

This checks supplied content and provenance for internal consistency. It cannot
attest that a claimed execution happened, that an identifier is semantically
opaque, or that differently named tasks are statistically independent.

Harness artifacts are UTF-8 text. The first version deliberately supports one
unambiguous patch format: ``replace_text`` supplies the complete candidate text,
bound to the SHA-256 of the baseline. Tasks use canonical JSON SHA-256 (sorted
keys, no whitespace, UTF-8, ensure_ascii=False, allow_nan=False).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from rename_probe import ProbeValidationError, validate_task, verify_renaming


class ContractError(ValueError):
    """The supplied replay does not satisfy the version 2 evidence contract."""


CHANNELS = ("initial_evidence", "extra_B", "extra_C")
OVERHEAD_TYPES = {"probe_generation", "probe_validation", "probe_interpretation"}
RUN_STATUSES = {"completed", "failed", "not_run"}
COST_SCOPES = {"execution_only", "execution_plus_overhead"}
DEFAULT_COST_SCOPE = "execution_plus_overhead"
COST_REL_TOL = 1e-12
COST_ABS_TOL_USD = 1e-12
OUTCOME_KEYS = {
    "baseline", "candidate", "baseline_utility", "candidate_utility",
    "baseline_success", "candidate_success", "utility", "success", "run_id",
}


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sha256_json(content: Any) -> str:
    try:
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
        return sha256_text(raw)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContractError("content must be finite UTF-8 JSON") from exc


def _object(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be an object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{path} must be a nonempty string")
    return value


def _same(value: Any, expected: Any, path: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise ContractError(f"{path} does not match the frozen content or declared provenance")


def _cost(value: Any, path: str) -> None:
    if value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ContractError(f"{path} must be a finite nonnegative number or null")


def _task(value: Any, path: str) -> dict:
    task = _object(value, path)
    _text(task.get("source_id"), path + ".source_id")
    _same(task.get("identifier_contract"), "opaque", path + ".identifier_contract")
    content = _object(task.get("content"), path + ".content")
    try:
        validate_task(content)
    except ProbeValidationError as exc:
        raise ContractError(f"{path}: {exc}") from exc
    _same(task.get("sha256"), sha256_json(content), path + ".sha256")
    return task


def _artifact(value: Any, path: str) -> dict:
    artifact = _object(value, path)
    content = _text(artifact.get("content"), path + ".content")
    try:
        digest = sha256_text(content)
    except UnicodeError as exc:
        raise ContractError(f"{path}.content must be UTF-8 text") from exc
    _same(artifact.get("sha256"), digest, path + ".sha256")
    return artifact


def _frozen(unit: dict, path: str) -> dict:
    frozen = _object(unit.get("frozen"), path + ".frozen")
    for side in ("baseline", "candidate", "patch"):
        _artifact(frozen.get(side), path + ".frozen." + side)
    patch = frozen["patch"]
    _same(patch.get("format"), "replace_text", path + ".frozen.patch.format")
    _same(patch.get("base_sha256"), frozen["baseline"]["sha256"],
          path + ".frozen.patch.base_sha256")
    _same(patch["content"], frozen["candidate"]["content"],
          path + ".frozen.patch.content")
    return frozen


def _binding(record: dict, frozen: dict, source: dict, path: str) -> None:
    binding = _object(record.get("binding"), path + ".binding")
    for side in ("baseline", "candidate", "patch"):
        _same(binding.get(side + "_sha256"), frozen[side]["sha256"],
              path + ".binding." + side + "_sha256")
    _same(binding.get("source_task_sha256"), source["sha256"],
          path + ".binding.source_task_sha256")


def _run(value: Any, side: str, frozen: dict, task: dict, bounds: list,
         seen_runs: set, path: str, require_cost: bool = False) -> str:
    run = _object(value, path)
    run_id = _text(run.get("run_id"), path + ".run_id")
    if run_id in seen_runs:
        raise ContractError(f"{path}.run_id reuses an execution record within the unit")
    seen_runs.add(run_id)
    _same(run.get("harness_sha256"), frozen[side]["sha256"], path + ".harness_sha256")
    _same(run.get("task_sha256"), task["sha256"], path + ".task_sha256")
    status = run.get("status")
    if not isinstance(status, str) or status not in RUN_STATUSES:
        raise ContractError(f"{path}.status must be completed, failed, or not_run")
    if require_cost and "execution_cost_usd" not in run:
        raise ContractError(f"{path}.execution_cost_usd is required; unknown is null")
    if "execution_cost_usd" in run:
        _cost(run["execution_cost_usd"], path + ".execution_cost_usd")
    if status == "completed":
        if "success" in run and type(run["success"]) is not bool:
            raise ContractError(f"{path}.success must be boolean")
        if "utility" in run:
            utility = run["utility"]
            if type(utility) not in (int, float) or not math.isfinite(utility):
                raise ContractError(f"{path}.utility must be finite numeric data")
        elif type(run.get("success")) is bool:
            utility = float(run["success"])
        else:
            raise ContractError(f"{path} requires utility or boolean success")
        if not bounds[0] <= utility <= bounds[1]:
            raise ContractError(f"{path}.utility is outside declared bounds")
    else:
        _text(run.get("error"), path + ".error")
        if "utility" in run or "success" in run:
            raise ContractError(f"{path}: failed/not_run execution cannot supply an outcome")
    return status


def _contains_outcome(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in OUTCOME_KEYS or _contains_outcome(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_outcome(item) for item in value)
    return False


def _paired_event_costs(event: dict, path: str) -> dict:
    """Check supplied totals against supplied components without filling gaps.

    Missing/null execution components remain unknown. Their known subtotal is
    only a lower bound, not a complete execution cost. A separately reported
    total can be known even when its allocation is unknown. Conversely, a null
    total is never reconstructed from components, even in execution_only mode.
    v0.2 already defined cost_usd as an inclusive event total, so an omitted
    cost_scope retains that meaning rather than silently changing old records.
    """
    scope = _text(event.get("cost_scope", DEFAULT_COST_SCOPE), path + ".cost_scope")
    if scope not in COST_SCOPES:
        raise ContractError(f"{path}.cost_scope must be execution_only or execution_plus_overhead")
    costs = {side: event["observation"][side].get("execution_cost_usd")
             for side in ("baseline", "candidate")}
    known = [amount for amount in costs.values() if amount is not None]
    try:
        subtotal = math.fsum(known) if known else None
    except OverflowError as exc:
        raise ContractError(f"{path}: known execution cost subtotal exceeds the finite numerical range") from exc
    unknown = [side for side, amount in costs.items() if amount is None]
    total = event["cost_usd"]
    check = "unknown_total" if total is None else "unknown_execution_breakdown"
    if total is not None and subtotal is not None:
        close = math.isclose(total, subtotal, rel_tol=COST_REL_TOL, abs_tol=COST_ABS_TOL_USD)
        if total < subtotal and not close:
            raise ContractError(
                f"{path}.cost_usd={total!r} is below known paired execution cost "
                f"subtotal={subtotal!r} (cost_scope={scope})")
        if scope == "execution_only" and not unknown and not close:
            raise ContractError(
                f"{path}.cost_usd must equal the paired execution cost sum "
                "within tolerance when cost_scope=execution_only")
        check = ("known_lower_bound_only" if unknown else
                 "execution_sum_equal" if scope == "execution_only" else "execution_sum_covered")
    return {
        "event_id": event["event_id"], "cost_scope": scope,
        "scope_source": "explicit" if "cost_scope" in event else "default",
        "event_total_cost_usd": total,
        "known_execution_subtotal_usd": subtotal,
        "complete_execution_sum_usd": subtotal if not unknown else None,
        "unknown_execution_cost_sides": unknown,
        "relationship_check": check,
    }


def _event(event: Any, channel: str, frozen: dict, source: dict, bounds: list,
           seen_runs: set, path: str) -> tuple[str, dict | None, dict | None]:
    event = _object(event, path)
    _text(event.get("event_id"), path + ".event_id")
    _binding(event, frozen, source, path)
    observation = _object(event.get("observation"), path + ".observation")
    if type(event.get("synthetic")) is not bool or type(event.get("valid")) is not bool:
        raise ContractError(f"{path} requires boolean synthetic and valid")
    if "cost_usd" not in event:
        raise ContractError(f"{path}.cost_usd is required; unknown is null")
    _cost(event["cost_usd"], path + ".cost_usd")
    kind = _text(event.get("type"), path + ".type")
    if kind in OVERHEAD_TYPES:
        if channel != "extra_C":
            raise ContractError(f"{path}: probe overhead belongs in extra_C")
        if _contains_outcome(observation):
            raise ContractError(f"{path}: overhead cannot impersonate paired execution evidence")
        status = event.get("status")
        if status not in ("completed", "failed"):
            raise ContractError(f"{path}: overhead status must be completed or failed")
        _same(event["valid"], status == "completed", path + ".valid")
        if status == "failed":
            _text(observation.get("error"), path + ".observation.error")
        classification = "overhead"
        if status == "failed":
            classification = "interpretation_failed" if kind == "probe_interpretation" else "probe_failed"
        return classification, None, None
    if kind != "paired_validation":
        raise ContractError(f"{path}.type must be paired_validation or a declared probe overhead")
    task = _task(event.get("task"), path + ".task")
    _same(task["source_id"], source["source_id"], path + ".task.source_id")
    intervention = _object(event.get("intervention"), path + ".intervention")
    if channel in ("initial_evidence", "extra_B"):
        _same(intervention.get("kind"), "none", path + ".intervention.kind")
        _same(task["sha256"], source["sha256"], path + ".task.sha256")
    else:
        _same(intervention.get("kind"), "identifier_renaming", path + ".intervention.kind")
        mapping = intervention.get("mapping")
        try:
            verify_renaming(source["content"], task["content"], mapping)
        except ProbeValidationError as exc:
            raise ContractError(f"{path}: renaming verification failed: {exc}") from exc
        if not mapping or all(left == right for left, right in mapping.items()):
            raise ContractError(f"{path}: diagnostic renaming must change a job identifier")
    statuses = [
        _run(observation.get(side), side, frozen, task, bounds, seen_runs,
             path + ".observation." + side)
        for side in ("baseline", "candidate")
    ]
    completed = all(status == "completed" for status in statuses)
    _same(event.get("status"), "completed" if completed else "failed", path + ".status")
    _same(event["valid"], completed, path + ".valid")
    cost_check = _paired_event_costs(event, path)
    return ("paired_completed" if completed else "paired_failed"), task, cost_check


def validate_contract(data: Any) -> dict:
    """Validate v2 provenance without mutation; return checks and limitations.

    The runner separately validates decision settings, dollar accounting,
    forbidden held-out keys, and demo flags. This validator never upgrades
    legacy input, including real schema_version=1 logs.
    """
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        raise ContractError("input requires schema_version=2; legacy records must be recollected, not relabeled")
    units = data.get("units")
    if not isinstance(units, list) or not units:
        raise ContractError("units must be a nonempty list")
    decision = _object(data.get("decision", {}), "decision")
    bounds = decision.get("utility_bounds", [0, 1])
    if (not isinstance(bounds, list) or len(bounds) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
            or bounds[0] >= bounds[1]):
        raise ContractError("utility_bounds must contain two increasing finite numbers")
    evidence_ids, evidence_hashes, evidence_sources = set(), set(), set()
    heldout_tasks = []
    units_audit = []
    content_by_id = {}
    for index, unit in enumerate(units):
        path = f"units[{index}]"
        unit = _object(unit, path)
        _text(unit.get("unit_id"), path + ".unit_id")
        frozen = _frozen(unit, path)
        for key, side in (("baseline_id", "baseline"), ("patch_id", "patch")):
            identity = (key, _text(unit.get(key), path + "." + key))
            digest = (frozen[side]["sha256"], frozen[side].get("base_sha256"),
                      frozen[side].get("format"))
            if identity in content_by_id and content_by_id[identity] != digest:
                raise ContractError(f"{path}.{key} names inconsistent frozen content")
            content_by_id[identity] = digest
        source = _task(unit.get("source_task"), path + ".source_task")
        evidence_ids.add(source["content"]["id"])
        evidence_hashes.add(source["sha256"])
        evidence_sources.add(source["source_id"])
        seen_runs, seen_events = set(), set()
        channel_audit = {}
        event_cost_checks = []
        for channel in CHANNELS:
            events = unit.get(channel)
            if not isinstance(events, list) or not events:
                raise ContractError(f"{path}.{channel} must contain an attempted validation")
            counts = {"paired_completed": 0, "paired_failed": 0, "probe_failed": 0,
                      "interpretation_failed": 0, "overhead": 0}
            for event_index, event in enumerate(events):
                location = f"{path}.{channel}[{event_index}]"
                classification, task, cost_check = _event(
                    event, channel, frozen, source, bounds, seen_runs, location)
                if cost_check is not None:
                    event_cost_checks.append({"channel": channel, **cost_check})
                if event["event_id"] in seen_events:
                    raise ContractError(f"{location}: duplicate event_id")
                seen_events.add(event["event_id"])
                counts[classification] += 1
                if task is not None:
                    evidence_ids.add(task["content"]["id"])
                    evidence_hashes.add(task["sha256"])
            attempts = counts["paired_completed"] + counts["paired_failed"]
            if not attempts and not (channel == "extra_C" and counts["probe_failed"]):
                raise ContractError(f"{path}.{channel} lacks paired validation or an explicit failed probe")
            complete = bool(counts["paired_completed"]) and not (
                counts["paired_failed"] or counts["probe_failed"] or counts["interpretation_failed"])
            channel_audit[channel] = {**counts, "status": "complete" if complete else "incomplete_or_failed"}
        pairs = unit.get("heldout")
        if not isinstance(pairs, list):
            raise ContractError(f"{path}.heldout must be a list")
        seen_heldout, seen_heldout_sources = set(), set()
        for pair_index, pair in enumerate(pairs):
            location = f"{path}.heldout[{pair_index}]"
            pair = _object(pair, location)
            _binding(pair, frozen, source, location)
            task = _task(pair.get("task"), location + ".task")
            _same(pair.get("task_id"), task["content"]["id"], location + ".task_id")
            if pair["task_id"] in seen_heldout:
                raise ContractError(f"{location}: duplicate heldout task_id")
            seen_heldout.add(pair["task_id"])
            if task["source_id"] in seen_heldout_sources:
                raise ContractError(f"{location}: duplicate heldout source_id; repeated rollouts are not independent task pairs")
            seen_heldout_sources.add(task["source_id"])
            if type(pair.get("synthetic")) is not bool:
                raise ContractError(f"{location}.synthetic must be boolean")
            for side in ("baseline", "candidate"):
                status = _run(pair.get(side), side, frozen, task, bounds, seen_runs,
                              location + "." + side, require_cost=True)
                if status != "completed":
                    raise ContractError(f"{location}: heldout reference requires completed paired outcomes")
            heldout_tasks.append((location, task))
        units_audit.append({"unit_id": unit["unit_id"], "channels": channel_audit,
                            "diagnostic_complete": channel_audit["extra_C"]["status"] == "complete",
                            "event_cost_checks": event_cost_checks})
    for location, task in heldout_tasks:
        if (task["content"]["id"] in evidence_ids or task["sha256"] in evidence_hashes
                or task["source_id"] in evidence_sources):
            raise ContractError(f"{location}: heldout task overlaps evidence by task ID, content hash, or source_id")
    return {
        "schema_version": 2,
        "contract_revision": "2.1",
        "checks_passed": ["frozen_content_hashes", "replacement_patch_matches_candidate",
                          "run_and_event_bindings", "paired_execution_records",
                          "structural_renaming", "declared_heldout_separation",
                          "available_event_cost_relationships"],
        "event_cost_policy": {
            "default_scope": DEFAULT_COST_SCOPE,
            "execution_only": "Total equals both execution costs when both are known; otherwise it covers the known subtotal.",
            "execution_plus_overhead": "Total includes both executions and additional costs of this event; it covers every known execution component.",
            "absolute_tolerance_usd": COST_ABS_TOL_USD,
            "relative_tolerance": COST_REL_TOL,
            "unknown_policy": "Preserve null/missing components and null totals. A known inclusive total may have an unknown allocation; never fill unknowns with zero.",
            "accounting_rule": "Charge the event total once. Do not re-add its component costs or duplicate included overhead in another event.",
        },
        "all_validation_channels_complete": all(
            channel["status"] == "complete" for unit in units_audit
            for channel in unit["channels"].values()),
        "units": units_audit,
        "limits": ["Hashes check supplied contents and claims; they do not attest actual execution.",
                   "Cost checks reconcile only supplied amounts; an unknown breakdown cannot verify a known total's component sum or external billing.",
                   "Opaque identifier semantics remain a declared task-contract precondition.",
                   "Distinct task IDs, hashes, and source IDs do not prove semantic or statistical independence.",
                   "Probe failures remain charged and visible; passing this schema does not establish scientific validity."],
    }
