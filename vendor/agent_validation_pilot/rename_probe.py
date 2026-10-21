"""Structural identifier-renaming probe for small JSON DAG tasks.

This utility checks structural equivalence only. It does not certify that an
agent's behaviour is invariant, or that identifiers are irrelevant in an
arbitrary task. Use it only when the task contract declares the job identifiers
opaque. Payloads and natural-language strings are never searched or replaced.

Only these identifier positions are interpreted:
    jobs[*].id, jobs[*].requires[*], goals[*]
The top-level task id is metadata and may change independently.
All other JSON values preserve their parsed types: booleans, integers, and
floating-point numbers are distinct, even when Python would compare them equal.
Object key order is irrelevant; array order is preserved.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import deque
from pathlib import Path
from typing import Any


class ProbeValidationError(ValueError):
    """The task, mapping, or claimed equivalence violates the probe contract."""


def _identifier(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProbeValidationError(f"{location} must be a nonempty string")
    return value


def _json_equal_strict(left: Any, right: Any) -> bool:
    """Compare JSON structures without Python's bool/number coercions.

    Native parsed types must match, so both False versus 0 and 1 versus 1.0
    differ. Object insertion order is ignored, while array order matters.
    Non-JSON containers and scalar types cannot pass a JSON round-trip check.
    The caller validates finiteness before using this comparison.
    """
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if any(type(key) is not str for key in left):
            return False
        if any(type(key) is not str for key in right):
            return False
        return left.keys() == right.keys() and all(
            _json_equal_strict(value, right[key]) for key, value in left.items()
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_equal_strict(before, after) for before, after in zip(left, right)
        )
    if type(left) in (str, int, float, bool, type(None)):
        return left == right
    return False


def validate_task(task: dict[str, Any]) -> None:
    """Validate identifiers, references, goals, and acyclicity without mutation."""
    if not isinstance(task, dict):
        raise ProbeValidationError("task must be a JSON object")
    try:
        round_trip = json.loads(json.dumps(task, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ProbeValidationError("task must contain finite JSON data") from exc
    if not _json_equal_strict(round_trip, task):
        raise ProbeValidationError("task must use JSON-compatible keys and containers")

    _identifier(task.get("id"), "task.id")
    jobs = task.get("jobs")
    goals = task.get("goals")
    if not isinstance(jobs, list) or not isinstance(goals, list):
        raise ProbeValidationError("jobs and goals must be lists")

    by_id: dict[str, dict[str, Any]] = {}
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ProbeValidationError(f"jobs[{index}] must be an object")
        job_id = _identifier(job.get("id"), f"jobs[{index}].id")
        if job_id in by_id:
            raise ProbeValidationError(f"duplicate job id: {job_id!r}")
        requires = job.get("requires")
        if not isinstance(requires, list):
            raise ProbeValidationError(f"jobs[{index}].requires must be a list")
        for dep_index, dependency in enumerate(requires):
            _identifier(dependency, f"jobs[{index}].requires[{dep_index}]")
        if len(set(requires)) != len(requires):
            raise ProbeValidationError(f"duplicate dependency in job {job_id!r}")
        if "payload" in job and not isinstance(job["payload"], dict):
            raise ProbeValidationError(f"jobs[{index}].payload must be an object")
        by_id[job_id] = job

    for index, goal in enumerate(goals):
        _identifier(goal, f"goals[{index}]")
        if goal not in by_id:
            raise ProbeValidationError(f"goal references unknown job: {goal!r}")
    if len(set(goals)) != len(goals):
        raise ProbeValidationError("goals must not contain duplicate identifiers")

    children: dict[str, list[str]] = {job_id: [] for job_id in by_id}
    indegree: dict[str, int] = {}
    for job_id, job in by_id.items():
        indegree[job_id] = len(job["requires"])
        for dependency in job["requires"]:
            if dependency not in by_id:
                raise ProbeValidationError(
                    f"job {job_id!r} references unknown dependency {dependency!r}"
                )
            children[dependency].append(job_id)

    ready = deque(job_id for job_id, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        job_id = ready.popleft()
        visited += 1
        for child in children[job_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(by_id):
        raise ProbeValidationError("job dependency graph must be acyclic")


def _validate_mapping(task: dict[str, Any], mapping: dict[str, str]) -> None:
    if not isinstance(mapping, dict):
        raise ProbeValidationError("mapping must be an object")
    expected = {job["id"] for job in task["jobs"]}
    if set(mapping) != expected:
        raise ProbeValidationError("mapping keys must exactly equal the original job ids")
    for source, target in mapping.items():
        _identifier(source, "mapping source")
        _identifier(target, f"mapping[{source!r}]")
    if len(set(mapping.values())) != len(mapping):
        raise ProbeValidationError("mapping must be one-to-one")


def _apply_mapping(task: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = copy.deepcopy(task)
    for job in result["jobs"]:
        job["id"] = mapping[job["id"]]
        job["requires"] = [mapping[dependency] for dependency in job["requires"]]
    result["goals"] = [mapping[goal] for goal in result["goals"]]
    return result


def verify_renaming(
    original: dict[str, Any], renamed: dict[str, Any], mapping: dict[str, str]
) -> bool:
    """Check exact inverse equality, allowing only the top-level task id to differ.

    Invalid claims raise ProbeValidationError; successful verification returns
    True. Object key order is irrelevant, but array order and parsed scalar
    types must match (including int versus float). No payload coercion occurs.
    """
    validate_task(original)
    validate_task(renamed)
    _validate_mapping(original, mapping)
    if {job["id"] for job in renamed["jobs"]} != set(mapping.values()):
        raise ProbeValidationError("renamed job ids do not match the mapping image")
    inverse = {target: source for source, target in mapping.items()}
    reconstructed = _apply_mapping(renamed, inverse)
    reconstructed["id"] = original["id"]
    if not _json_equal_strict(reconstructed, original):
        raise ProbeValidationError("inverse renaming does not reproduce the original task")
    return True


def rename_task(
    task: dict[str, Any],
    mapping: dict[str, str] | None = None,
    renamed_task_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return a deep-copied renamed task and a complete bijective mapping.

    When no mapping is given, targets are rename_0001, rename_0002, ... in the
    original job order. Explicit mappings may swap or reuse original ids as
    targets: simultaneous structural substitution keeps such mappings valid.
    """
    validate_task(task)
    if mapping is None:
        mapping = {
            job["id"]: f"rename_{index:04d}"
            for index, job in enumerate(task["jobs"], start=1)
        }
    else:
        mapping = copy.deepcopy(mapping)
    _validate_mapping(task, mapping)
    renamed = _apply_mapping(task, mapping)
    renamed["id"] = (
        renamed_task_id if renamed_task_id is not None else task["id"] + ":renamed"
    )
    verify_renaming(task, renamed, mapping)
    return renamed, mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON file containing one DAG task")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mapping", type=Path, help="Optional JSON object of exact id mappings")
    args = parser.parse_args()
    try:
        task = json.loads(args.input.read_text(encoding="utf-8"))
        mapping = (
            json.loads(args.mapping.read_text(encoding="utf-8"))
            if args.mapping is not None
            else None
        )
        renamed, used_mapping = rename_task(task, mapping)
        output = {
            "kind": "identifier_renaming_probe",
            "source_task_id": task["id"],
            "renamed_task": renamed,
            "mapping": used_mapping,
            "structural_equivalence_verified": True,
            "semantic_precondition": "job identifiers are opaque under the task contract",
        }
        encoded = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            print(encoded, end="")
        else:
            args.output.write_text(encoded, encoding="utf-8")
    except (OSError, json.JSONDecodeError, ProbeValidationError) as exc:
        parser.exit(2, f"probe rejected: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
