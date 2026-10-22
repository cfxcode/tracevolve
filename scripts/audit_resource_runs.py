"""Offline resource-state replay; never calls a model or modifies raw evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracevolve import tasks
from tracevolve.store import Store
from tracevolve.util import canonical, digest, now, text_hash, write_json


def replay_run(request, result):
    spec = request.get("server_input")
    if not spec or result["status"] != "completed":
        return {"checked": False, "reason": "not a completed resource execution"}
    if request["implementation"]["tasks.py"] != text_hash(Path(tasks.__file__).read_text()):
        raise ValueError("environment source differs: use the archived source version to audit")
    code = spec["contract"].get("family") == "python-code-maintenance"
    if code:
        from tracevolve import code_tasks, code_runner, code_corpus
        for module in (code_tasks, code_runner, code_corpus):
            if request["implementation"].get(Path(module.__file__).name) != text_hash(Path(module.__file__).read_text()):
                raise ValueError("code evaluator source differs: use archived source")
    if spec["contract"].get("check_receipt_required"):
        from tracevolve import recovery
        if request["implementation"].get("recovery.py") != text_hash(Path(recovery.__file__).read_text()):
            raise ValueError("recovery source differs: use the archived source version to audit")
    if spec["contract"].get("shared_inventory"):
        from tracevolve import capacity
        if request["implementation"].get("capacity.py") != text_hash(Path(capacity.__file__).read_text()):
            raise ValueError("capacity source differs: use the archived source version to audit")
    if spec["contract"].get("pool_assignment"):
        from tracevolve import allocation
        if request["implementation"].get("allocation.py") != text_hash(Path(allocation.__file__).read_text()):
            raise ValueError("allocation source differs: use the archived source version to audit")
    env = tasks.create_environment(spec["task"], spec["contract"], spec["max_tool_calls"])
    for event in result["tool_events"]:
        name, args = event["name"], event["arguments"]
        expected = env.call(name, args, bundle=spec["bundle"])
        if canonical(expected) != canonical(event["output"]):
            raise ValueError("tool response is inconsistent with deterministic state transitions")
    if not env.loaded:
        raise ValueError("completed resource run has no loading receipt")
    expected = env.evaluate()
    if code:
        acceptance = code_tasks.acceptance(spec["task"], expected["artifact"])
        success = expected["submitted"] and acceptance["passed"] == acceptance["total"]
        expected.update(acceptance=acceptance, success=success, utility=float(success))
    observed = {key: result["evaluation"][key] for key in expected}
    if canonical(expected) != canonical(observed):
        raise ValueError("reported final outcome differs from replayed resource state")
    return {"checked": True, "success": expected["success"], "tool_calls": expected["tool_calls"],
            "tool_errors": expected["tool_errors"],
            **({"agent_errors": expected["agent_errors"], "environment_faults": expected["environment_faults"]}
               if "agent_errors" in expected else {})}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True)
    args = parser.parse_args(argv)
    store = Store(args.study)
    try:
        integrity = store.verify()
        checks = []
        for call in store.calls():
            if call["result_hash"]:
                check = replay_run(store.get(call["request_hash"]), store.get(call["result_hash"]))
            else:
                check = {"checked": False, "reason": "unfinished attempt"}
            checks.append({"call_id": call["id"], **check})
        report = {"at": now(), "integrity": integrity, "runs": checks,
                  "completed_resource_runs_verified": sum(c["checked"] for c in checks),
                  "scope": "deterministic tool transition consistency; not independent proof of remote inference"}
        write_json(store.root / "audit.resource.json", report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
