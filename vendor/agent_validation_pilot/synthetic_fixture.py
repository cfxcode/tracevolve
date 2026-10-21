"""Build conspicuously fictional v2 fixtures from old synthetic integrity tests.

Never use this helper for collected experiment data: missing task content,
harnesses, mappings, outcomes, and run IDs are FABRICATED, not recovered.
The production validator does not call this helper or auto-upgrade v1 logs.
"""

from __future__ import annotations

import copy

from contracts import ContractError, sha256_json, sha256_text
from rename_probe import rename_task


def _task(task_id: str, source_id: str) -> dict:
    content = {
        "id": task_id,
        "jobs": [{"id": "fictional-job", "requires": [],
                  "payload": {"synthetic_fixture": True, "enabled": False, "count": 1}}],
        "goals": ["fictional-job"],
    }
    return {"content": content, "sha256": sha256_json(content),
            "source_id": source_id, "identifier_contract": "opaque"}


def _artifact(content: str) -> dict:
    return {"content": content, "sha256": sha256_text(content)}


def upgrade_synthetic_fixture(data: dict) -> dict:
    """Deep-copy and synthesize a complete v2 fixture; refuse nonsynthetic data.

    This can be called again after a test deliberately changes a task_id or adds
    a legacy overhead event. It is a fixture builder, not an integrity checker.
    For integrity tests of tampering, call validate_contract on the altered v2
    fixture directly; rebuilding it would intentionally replace provenance.
    """
    if not isinstance(data, dict) or not isinstance(data.get("units"), list) or not data["units"]:
        raise ContractError("synthetic fixture builder requires nonempty units")
    for unit in data["units"]:
        for channel in ("initial_evidence", "extra_B", "extra_C", "heldout"):
            for item in unit.get(channel, []):
                if item.get("synthetic") is not True:
                    raise ContractError("fixture builder refuses nonsynthetic records; it fabricates provenance")
    result = copy.deepcopy(data)
    result["schema_version"] = 2
    result["synthetic"] = True
    result["provenance_notice"] = "FABRICATED FIXTURE CONTENT, HASHES, RUN IDS, AND TASKS; NOT EXECUTION EVIDENCE"
    for unit in result["units"]:
        unit_id = unit["unit_id"]
        baseline = _artifact("SYNTHETIC BASELINE FIXTURE\n" + unit["baseline_id"] + "\n")
        candidate = _artifact("SYNTHETIC CANDIDATE FIXTURE\n" + unit["patch_id"] + "\n")
        frozen = {"baseline": baseline, "candidate": candidate,
                  "patch": {**candidate, "format": "replace_text", "base_sha256": baseline["sha256"]}}
        unit["frozen"] = frozen
        source = _task(unit_id + ":fictional-source", unit_id + ":fictional-root")
        unit["source_task"] = source
        binding = {side + "_sha256": frozen[side]["sha256"] for side in frozen}
        binding["source_task_sha256"] = source["sha256"]

        def attach_run(outcome, side, task, suffix):
            outcome.update(run_id=unit_id + ":" + suffix + ":" + side,
                           harness_sha256=frozen[side]["sha256"], task_sha256=task["sha256"])
            outcome.setdefault("status", "completed")
            return outcome

        for channel in ("initial_evidence", "extra_B", "extra_C"):
            for event in unit[channel]:
                event["binding"] = copy.deepcopy(binding)
                obs = event.get("observation", {})
                valid = event.get("valid", True)
                event["valid"] = valid
                kind = event["type"]
                overhead = {
                    "probe_generation": "probe_generation",
                    "probe_validation": "probe_validation",
                    "diagnostic_interpretation": "probe_interpretation",
                    "probe_interpretation": "probe_interpretation",
                }.get(kind)
                if channel == "extra_C" and not valid and not ("baseline" in obs and "candidate" in obs):
                    overhead = "probe_validation"
                if overhead:
                    event["type"] = overhead
                    event["status"] = "completed" if valid else "failed"
                    event.pop("task", None)
                    event.pop("intervention", None)
                    if not valid:
                        obs.setdefault("error", "Deliberate fictional pre-execution probe failure.")
                    event["observation"] = obs
                    continue
                task = copy.deepcopy(source)
                if channel == "extra_C":
                    renamed, mapping = rename_task(source["content"],
                                                   renamed_task_id=unit_id + ":renamed:" + event["event_id"])
                    task.update(content=renamed, sha256=sha256_json(renamed))
                    event["intervention"] = {"kind": "identifier_renaming", "mapping": mapping}
                else:
                    event["intervention"] = {"kind": "none"}
                event["type"] = "paired_validation"
                event.setdefault("cost_scope", "execution_plus_overhead")
                event["task"] = task
                for side in ("baseline", "candidate"):
                    if side in obs:
                        outcome = obs[side]
                    else:
                        outcome = {"utility": obs.get(side + "_utility", 0.0)}
                        if side + "_success" in obs:
                            outcome["success"] = obs[side + "_success"]
                    attach_run(outcome, side, task, event["event_id"])
                    obs[side] = outcome
                    obs.pop(side + "_utility", None)
                    obs.pop(side + "_success", None)
                event["observation"] = obs
                completed = all(obs[side]["status"] == "completed" for side in ("baseline", "candidate"))
                event["status"] = "completed" if completed else "failed"
                event["valid"] = completed
        for index, pair in enumerate(unit["heldout"]):
            task = _task(pair["task_id"], pair["task_id"] + ":fictional-independent-root")
            pair["task"] = task
            pair["binding"] = copy.deepcopy(binding)
            for side in ("baseline", "candidate"):
                attach_run(pair[side], side, task, "future-" + str(index))
    return result
