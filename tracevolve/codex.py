from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .contracts import ExecutionSnapshot, frozen_harness
from .util import canonical, digest, now, read_json, source_manifest, text_hash, write_json


# An allowlisted runtime with no host file-reading, execution, browser, app,
# memory, personal Skill discovery or subagent tools. MCP is the sole task IO.
DISABLED = ("apps", "plugins", "remote_plugin", "memories", "shell_tool",
            "unified_exec", "shell_snapshot", "multi_agent", "multi_agent_v2",
            "browser_use", "browser_use_external", "computer_use", "in_app_browser",
            "image_generation", "view_image", "goals", "sleep_tool", "skill_search",
            "hooks", "code_mode", "tool_suggest", "worktrees",
            "unbounded_connection_retries", "skill_mcp_dependency_install", "in_app_chat")


class CodexAdapter:
    def __init__(self, executable="codex"):
        self.executable = shutil.which(executable) or executable
        self.process = None

    def capabilities(self):
        return {"editable": ["prompt", "skill"], "skill_delivery": "explicit MCP read into model context",
                "workflow_editing": False, "arbitrary_code": False,
                "enforced_task_gateway": True, "native_skill_discovery": False,
                "per_provider_request_accounting": False}

    def runtime_identity(self):
        version = subprocess.check_output([self.executable, "--version"], text=True).strip()
        return {"adapter": "codex-exec-json-v1", "version": version,
                "python": sys.version, "platform": platform.platform(),
                "binary_sha256": __import__("hashlib").sha256(Path(self.executable).resolve().read_bytes()).hexdigest(),
                "disabled_features": list(DISABLED), "skip_host_skill_discovery": True,
                "project_doc_max_bytes": 0, "web_search": "disabled",
                "remote_model_weights": "unknown", "capabilities": self.capabilities()}

    def preflight(self, config):
        identity = self.runtime_identity()
        if identity["version"] != config.expected_codex_version:
            raise ValueError("Codex version drift: " + identity["version"])
        auth = Path(config.auth_home).expanduser() / "auth.json"
        if not auth.is_file():
            raise ValueError("isolated execution requires the existing file-backed ChatGPT login")
        data = read_json(auth)
        if data.get("auth_mode") != "chatgpt" or not data.get("tokens"):
            raise ValueError("ChatGPT subscription login required; API credentials are not used")
        return {"runtime": identity, "auth_mode": "chatgpt", "live_network_used": False}

    def snapshot(self, bundle, contract, config):
        runtime = self.runtime_identity()
        runtime.update(model=config.model, reasoning_effort=config.reasoning_effort)
        return ExecutionSnapshot(bundle.hash, runtime, contract, source_manifest(),
                                 {"timeout_seconds": config.timeout_seconds, "max_tool_calls": config.max_tool_calls},
                                 (text_hash(bundle.skill),) if bundle.skill else ())

    def cancel(self):
        proc = self.process
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            except ProcessLookupError:
                pass

    def _options(self, config, server_root=None):
        options = ["-m", config.model, "-s", "read-only"]
        values = {"model_reasoning_effort": config.reasoning_effort, "approval_policy": "never",
                  "forced_login_method": "chatgpt",
                  "agents.enabled": False,
                  "project_doc_max_bytes": 0, "web_search": "disabled", "history.persistence": "none",
                  "features.skip_host_skill_discovery": True, "suppress_unstable_features_warning": True}
        values.update({"features." + f: False for f in DISABLED})
        if server_root:
            # Server launched by Codex; it receives only this run's task and Bundle.
            values.update({"mcp_servers.resources.command": sys.executable,
                           "mcp_servers.resources.args": ["-m", "tracevolve.task_server", "--root", str(server_root)],
                           "mcp_servers.resources.env.PYTHONPATH": str(Path(__file__).parent.parent),
                           "mcp_servers.resources.default_tools_approval_mode": "approve",
                           "mcp_servers.resources.required": True})
        for key, value in values.items():
            options.extend(["-c", key + "=" + json.dumps(value)])
        return options, values

    def _invoke(self, *, store, logical_id, prompt, schema, config, phase, server_input=None):
        self.preflight(config)
        request = {"prompt": prompt, "schema": schema, "config": config.public(), "server_input": server_input,
                   "runtime": self.runtime_identity(), "implementation": source_manifest()}
        cached = store.reserve(logical_id, phase, request, config.max_sessions)
        if cached is not None:
            return cached
        run_dir = store.root / "executions" / digest(logical_id)[:24]
        run_dir.mkdir(parents=True, exist_ok=False)
        write_json(run_dir / "request.json", request)
        if server_input:
            write_json(run_dir / "server_input.json", server_input)
        start = time.monotonic()
        outcome = {"run_id": logical_id, "status": "failed", "cost_usd": None,
                   "billing_mode": "chatgpt_subscription", "usage": None,
                   "execution_origin": "live_model", "synthetic": False, "events": [], "tool_events": []}
        interruption = None
        previous_sigterm = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            def interrupt(signum, frame):
                raise KeyboardInterrupt("SIGTERM")
            signal.signal(signal.SIGTERM, interrupt)
        try:
            with tempfile.TemporaryDirectory(prefix="tracevolve-") as tmp:
                temp = Path(tmp)
                auth_home, workspace = temp / "auth", temp / "workspace"
                auth_home.mkdir(mode=0o700)
                workspace.mkdir()
                # Credentials never enter the study, immutable objects, or public repository.
                shutil.copyfile(Path(config.auth_home).expanduser() / "auth.json", auth_home / "auth.json")
                (auth_home / "auth.json").chmod(0o600)
                env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL",
                       "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                       "http_proxy", "https_proxy", "all_proxy", "no_proxy") if k in os.environ}
                env["CODEX_HOME"] = str(auth_home)
                env["PYTHONPATH"] = str(Path(__file__).parent.parent)
                options, effective = self._options(config, run_dir if server_input else None)
                audit_options = []
                for i in range(4, len(options), 2):
                    if not options[i + 1].startswith("mcp_servers."):
                        audit_options.extend(options[i:i + 2])
                # Render the actual startup context offline. An isolated auth
                # home alone does not prove host .agents Skills were excluded.
                # prompt-input initializes configured MCP servers, so keep the
                # task server out of this instruction audit. It must start once,
                # only when exec begins; it contributes no startup instructions.
                audit = subprocess.run([self.executable, "debug", "prompt-input", *audit_options],
                                       env=env, cwd=workspace, capture_output=True, text=True, timeout=30)
                if audit.returncode:
                    raise ValueError("startup context audit failed: " + audit.stderr[:300])
                def inspect_context(raw):
                    context = json.loads(raw)
                    text = "\n".join(part.get("text", "") for item in context for part in item.get("content", []))
                    roots = dict(re.findall(r"- `(r\d+)` = `([^`]+)`", text))
                    paths = []
                    for alias, relative in re.findall(r"\(file: (r\d+)/([^\)]+)\)", text):
                        path = Path(roots.get(alias, "/unknown")) / relative
                        if not path.resolve().is_relative_to(auth_home.resolve()):
                            paths.append(path)
                    return text, paths
                context_text, foreign = inspect_context(audit.stdout)
                if foreign:
                    # Some installations supply app-owned Skill snapshots outside
                    # CODEX_HOME. Explicitly exclude each discovered entry, then
                    # prove the actual launch configuration excludes them.
                    disabled = sorted({str(p) for p in foreign})
                    inline = "[" + ",".join("{path=" + json.dumps(p) + ",enabled=false}" for p in disabled) + "]"
                    options.extend(["-c", "skills.config=" + inline])
                    audit_options.extend(["-c", "skills.config=" + inline])
                    effective["skills.config"] = [{"path": p, "enabled": False} for p in disabled]
                    audit = subprocess.run([self.executable, "debug", "prompt-input", *audit_options],
                                           env=env, cwd=workspace, capture_output=True, text=True, timeout=30)
                    if audit.returncode:
                        raise ValueError("context exclusion audit failed: " + audit.stderr[:300])
                    context_text, foreign = inspect_context(audit.stdout)
                    if foreign:
                        raise ValueError("unexpected host Skill after explicit exclusion: " + str(foreign[0]))
                write_json(run_dir / "effective_config.json", effective)
                normalized = context_text.replace(str(temp), "<ISOLATED_RUN>")
                write_json(run_dir / "startup_context.json", {"text": normalized, "sha256": text_hash(normalized),
                           "host_skill_entries": 0, "project_instructions_max_bytes": 0,
                           "task_mcp_omitted_from_instruction_audit": True})
                command = [self.executable, "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                           "--skip-git-repo-check", "--color", "never", "-C", str(workspace), *options]
                if schema:
                    write_json(temp / "output_schema.json", schema)
                    command += ["--output-schema", str(temp / "output_schema.json")]
                command += ["-"]
                # Log the launch vector without auth path contents or inherited environment.
                write_json(run_dir / "launch.json", {"argv": command, "new_session": True,
                                                     "workspace_initially_empty": True, "at": now()})
                with (run_dir / "stdout.jsonl").open("w") as stdout, (run_dir / "stderr.txt").open("w") as stderr:
                    self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                                    text=True, env=env, cwd=workspace, start_new_session=True)
                    try:
                        self.process.communicate(prompt, timeout=config.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        outcome["error"] = "EXECUTION_TIMEOUT"
                        outcome["status"] = "uncertain"
                        self.cancel()
                    except BaseException:
                        self.cancel()
                        raise
                outcome["returncode"] = self.process.returncode
                self.process = None
                parse_errors = []
                for line in (run_dir / "stdout.jsonl").read_text().splitlines():
                    try:
                        outcome["events"].append(json.loads(line))
                    except json.JSONDecodeError:
                        parse_errors.append(line)
                if parse_errors:
                    outcome["error"] = "MALFORMED_EVENT_STREAM"
                    outcome["malformed_lines"] = parse_errors
                events = outcome["events"]
                for event in events:
                    if event.get("type") == "thread.started":
                        outcome["thread_id"] = event.get("thread_id")
                    if event.get("type") == "turn.completed":
                        outcome["usage"] = event.get("usage")
                    if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                        outcome["text"] = event["item"].get("text", "")
                forbidden = [e for e in events if e.get("item", {}).get("type") in ("command_execution", "file_change", "web_search", "collab_agent_tool_call")
                             or (e.get("item", {}).get("type") == "mcp_tool_call" and
                                 (not server_input or e["item"].get("server") != "resources"))]
                if forbidden:
                    outcome["error"] = "UNEXPECTED_TOOL_CAPABILITY"
                if not outcome.get("error") and outcome["returncode"] == 0 and any(e.get("type") == "turn.completed" for e in events):
                    outcome["status"] = "completed"
                else:
                    outcome.setdefault("error", "CODEX_EXECUTION_FAILED")
                if schema and outcome["status"] == "completed":
                    try:
                        outcome["response"] = json.loads(outcome.get("text", ""))
                    except json.JSONDecodeError:
                        outcome.update(status="failed", error="INVALID_JSON_RESPONSE")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            outcome.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        except (KeyboardInterrupt, SystemExit) as exc:
            outcome.update(status="uncertain", error="CANCELLED")
            interruption = exc
        finally:
            self.cancel()
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
        outcome["elapsed_seconds"] = time.monotonic() - start
        if (run_dir / "tool_events.jsonl").exists():
            outcome["tool_events"] = [json.loads(s) for s in (run_dir / "tool_events.jsonl").read_text().splitlines()]
        if server_input:
            evaluation = read_json(run_dir / "environment_result.json") if (run_dir / "environment_result.json").exists() else {}
            expected_hash = digest(server_input["bundle"])
            reads = [t for t in outcome["tool_events"] if t["name"] == "load_bundle" and t["output"].get("bundle_hash") == expected_hash]
            # A host receipt plus the runtime MCP completion establishes delivery,
            # not comprehension or causal contribution of the Skill.
            deliveries = []
            for event in outcome["events"]:
                item = event.get("item", {})
                if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call" or item.get("tool") != "load_bundle" or item.get("status") != "completed":
                    continue
                for part in (item.get("result") or {}).get("content", []):
                    try:
                        payload = json.loads(part.get("text", ""))
                        if payload.get("bundle_hash") == expected_hash and digest(payload.get("bundle")) == expected_hash:
                            deliveries.append(event)
                            break
                    except (ValueError, TypeError, AttributeError):
                        continue
            outcome["load_evidence"] = {"bundle_hash": expected_hash, "host_reads": len(reads), "runtime_deliveries": len(deliveries),
                                        "verified": bool(reads and deliveries and evaluation.get("loaded"))}
            if outcome["status"] == "completed" and not outcome["load_evidence"]["verified"]:
                outcome.update(status="failed", error="MISSING_ACTUAL_LOAD_EVIDENCE")
            if outcome["status"] == "completed":
                outcome["evaluation"] = evaluation
        # Legacy has only failed/not_run; preserve uncertainty in the original
        # request ledger and the explicit error, never silently issue a retry.
        # Bind exact raw files for later verification; do not rewrite them.
        outcome["raw_files"] = {str(p.relative_to(store.root)): text_hash(p.read_text()) for p in sorted(run_dir.iterdir()) if p.is_file()}
        result = store.finish(logical_id, outcome)
        if interruption is not None:
            raise interruption
        return result

    def execute(self, *, store, logical_id, bundle, task, contract, config, phase):
        snapshot = self.snapshot(bundle, contract, config)
        prompt = ("You are executing one isolated resource task. First call resources.load_bundle and follow its installed instructions. "
                  "Use only resources tools. Finish by calling submit with the exact goal set. "
                  "The current tool schema specifies required modes. IDs are opaque.\nPublic goal IDs: " + canonical(task["content"]["goals"]))
        result = self._invoke(store=store, logical_id=logical_id, prompt=prompt, schema=None, config=config, phase=phase,
                              server_input={"bundle": asdict(bundle), "task": task["content"], "contract": contract,
                                            "max_tool_calls": config.max_tool_calls})
        record = {**result, "execution_snapshot": asdict(snapshot), "snapshot_hash": snapshot.hash,
                  "harness": frozen_harness(bundle, snapshot), "task_sha256": task["sha256"], "task_source_id": task["source_id"]}
        if record["status"] == "uncertain":
            record["status"] = "failed"
            record["request_uncertain"] = True
        store.set("run:" + logical_id, store.put(record))
        return record

    def infer(self, *, store, logical_id, prompt, schema, config, phase):
        return self._invoke(store=store, logical_id=logical_id, prompt=prompt, schema=schema, config=config, phase=phase)
