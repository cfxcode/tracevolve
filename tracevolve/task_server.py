"""Small newline-delimited MCP stdio server; exposes no filesystem or shell tool.

State and tool receipts belong to the host process, outside the model workspace.
No evaluator answers or other tasks are available to this server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tracevolve.tasks import create_environment
from tracevolve.util import append_json, digest, now, read_json, write_json


def schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def tool_specs(contract):
    string = {"type": "string"}
    specs = [
        {"name": "load_bundle", "description": "Read the installed planning instructions and optional Skill. Required before any job tool; records the actual content hash.", "inputSchema": schema({})},
        {"name": "list_jobs", "description": "List job IDs and kinds in source order. IDs are opaque.", "inputSchema": schema({})},
        {"name": "inspect_job", "description": "Read a job's dependencies, requires_check flag and current status.", "inputSchema": schema({"job_id": string})},
        {"name": "run_job", "description": f"Execute a job once prerequisites are done. requires_check=true requires mode {contract['checked_mode']}; false requires standard. Errors never mutate state.", "inputSchema": schema({"job_id": string, "mode": {"type": "string", "enum": [contract["checked_mode"], "standard"]}})},
        {"name": "submit", "description": "Submit exactly the public goal IDs after completing them. No duplicate IDs.", "inputSchema": schema({"goal_ids": {"type": "array", "items": string}})},
    ]
    if contract.get("check_receipt_required"):
        specs[2]["description"] += " Confirm status after OUTCOME_UNKNOWN before retrying; completed jobs must not be rerun."
        specs[3] = {"name": "run_job", "description": (
            f"Execute once dependencies are complete. requires_check=true requires mode {contract['checked_mode']} and a valid check_job receipt; "
            "false requires standard and an empty receipt. A legal attempt consumes the receipt. RESOURCE_BUSY does not commit; recheck then retry. "
            "OUTCOME_UNKNOWN may have committed: inspect status before retrying. Completed jobs reject reruns."),
            "inputSchema": schema({"job_id": string, "mode": {"type": "string", "enum": [contract["checked_mode"], "standard"]}, "receipt": string})}
        specs.insert(3, {"name": "check_job", "description": (
            f"Check a pending job after all dependencies complete. Issue a one-use receipt valid for the next {contract['receipt_ttl_calls']} gateway calls. "
            "Check immediately before run_job; intervening tool calls consume validity. A newer receipt replaces the previous receipt for that job."),
            "inputSchema": schema({"job_id": string})})
    for spec in specs:
        spec["annotations"] = {"readOnlyHint": spec["name"] in ("load_bundle", "list_jobs", "inspect_job"),
                               "destructiveHint": False, "openWorldHint": False}
    return specs


def serve(root):
    root = Path(root)
    # A transparent MCP restart must not reset an already-mutated environment.
    # A separate run always has a distinct root and a distinct state machine.
    with (root / "server.started").open("x") as marker:
        marker.write(now())
    spec = read_json(root / "server_input.json")
    env = create_environment(spec["task"], spec["contract"], spec["max_tool_calls"])
    cache = {}
    def persist():
        write_json(root / "environment_result.json", {**env.evaluate(), "loaded": env.loaded,
                                                     "bundle_hash": digest(spec["bundle"])})
    persist()
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if "id" not in request:
            continue
        request_id = request["id"]
        if method == "initialize":
            result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "tracevolve-resources", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": tool_specs(spec["contract"])}
        elif method == "tools/call":
            params = request["params"]
            key = digest(request_id)
            if key in cache:
                if cache[key][0] != digest(params):
                    raise ValueError("request id reused with different arguments")
                result = cache[key][1]
            else:
                name, args = params["name"], params.get("arguments", {})
                output = env.call(name, args, bundle=spec["bundle"])
                append_json(root / "tool_events.jsonl", {"at": now(), "request_id": request_id, "name": name,
                                                         "arguments": args, "output": output})
                persist()
                result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
                          "isError": "error" in output}
                cache[key] = (digest(params), result)
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
        if env.calls >= env.max_calls:
            # End the task capability after delivering the final allowed reply.
            # Further model attempts cannot keep executing or mutate this run.
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    serve(parser.parse_args().root)


if __name__ == "__main__":
    main()
