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
    env = tasks.ResourceEnvironment(spec["task"], spec["contract"], spec["max_tool_calls"])
    loaded = False
    for event in result["tool_events"]:
        name, args = event["name"], event["arguments"]
        if name == "load_bundle" and args == {}:
            expected = {"bundle": spec["bundle"], "bundle_hash": digest(spec["bundle"])}
            loaded = True
        elif not loaded:
            expected = {"error": "LOAD_BUNDLE_REQUIRED"}
        else:
            expected = env.call(name, args)
        if canonical(expected) != canonical(event["output"]):
            raise ValueError("tool response is inconsistent with deterministic state transitions")
    if not loaded:
        raise ValueError("completed resource run has no loading receipt")
    expected = env.evaluate()
    observed = {key: result["evaluation"][key] for key in expected}
    if canonical(expected) != canonical(observed):
        raise ValueError("reported final outcome differs from replayed resource state")
    return {"checked": True, "success": expected["success"], "tool_calls": expected["tool_calls"],
            "tool_errors": expected["tool_errors"]}


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
