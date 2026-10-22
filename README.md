# TraceVolve

English | [中文](README.zh-CN.md)

An evidence-based framework for agent self-improvement. The initial release supports Codex CLI, resource dependency tasks, P0 prompt modifications, and P1a single-round Skill validation. The project's early design name was EvoHarness.

TraceVolve preserves raw execution evidence, freezes candidates, and validates them in independent sessions. Candidates may be rejected, and development tasks may produce no candidates; both outcomes are recorded faithfully. The current version does not support continuous automatic upgrades, arbitrary code modifications, or multi-agent topology optimization.

## Installation and preflight

Requires Python 3.11+ and Codex CLI authenticated through ChatGPT. The executor uses POSIX process groups (macOS/Linux). The current integration pins `codex-cli 0.163.0`, `gpt-6-astra`, and reasoning effort `high`. Production code uses the Python standard library.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/tracevolve plan --config configs/smoke.json
.venv/bin/tracevolve preflight --config configs/smoke.json
```

You can also run `python3 -m tracevolve …` directly from the repository root without installing the package. Neither `plan` nor `preflight` calls a model. `preflight` checks the CLI version and existing file-based ChatGPT authentication without displaying credentials. Before the first live execution, the actual startup context is also audited offline.

## P0: Live collection and legacy protocol replay

```bash
python3 -m tracevolve run --config configs/smoke.json --study .tracevolve/p0-smoke-001
python3 -m tracevolve report --study .tracevolve/p0-smoke-001
python3 -m tracevolve verify --study .tracevolve/p0-smoke-001
# Export and replay only when candidates exist and evidence is ready:
python3 -m tracevolve export --study .tracevolve/p0-smoke-001
python3 -m tracevolve replay --study .tracevolve/p0-smoke-001
```

The smoke study targets 1 naturally collected candidate and 3 held-out task pairs. It scans at most 6 development tasks and allows at most 32 CLI session attempts across the study. Each execution is limited to 64 gateway tool calls (including `load_bundle` and loading errors) and 300 seconds. A completed development run with a failed tool action also counts as an observable development problem. Infrastructure failures are not treated as prompt defects for candidate generation.

Each candidate undergoes 6 validation executions across A/B/C: A is the initial pair, B repeats the pairing on the original task, and C pairs runs on the same bijectively renamed task. Held-out evaluation starts only after all judgments are sealed. Frozen settings are recorded in `manifest.json`.

If no valid candidate is collected, only reports and raw records are produced; no v2 units are fabricated. If held-out execution is incomplete, the v2 export leaves that unit's entire held-out set empty, while the original failures and full plan remain in the study ledger. Such exports are diagnostic and do not constitute a complete study.

After the smoke study's evidence chain passes verification, start an independent five-candidate pilot:

```bash
python3 -m tracevolve plan --config configs/pilot.json
python3 -m tracevolve run --config configs/pilot.json --study .tracevolve/p0-pilot-001
```

The pilot targets 5 candidates with 5 held-out task pairs per candidate, scanning at most 30 development tasks and allowing at most 160 session attempts. Template exclusions cover all tasks from the preceding smoke study. A candidate shortfall does not reduce the planned target. At both study sizes, the original statistical reference labels cannot cross the default interval thresholds; these pilots do not support claims that C outperforms B.

## P1a: Skills, permissions, and contract changes

```bash
python3 -m tracevolve run --config configs/p1a.json --study .tracevolve/p1a-smoke-002
python3 -m tracevolve report --study .tracevolve/p1a-smoke-002
```

Skills are explicitly injected into the Codex context through the adapter-managed `load_bundle` tool. This implements controlled Skill loading rather than Codex's global native Skill discovery. Candidate loading evidence requires both a host-side read record and a successful Codex MCP response with matching content hashes. This proves delivery of the content, not that the model understood it, followed it, or benefited from it.

The fixed validation sequence includes the original task, a repeat of that task, a renamed pair, and deterministic boundary checks for task families and unknown dependencies. Release requires the A judgment, complete execution, no pairwise regression, and a mean gain of at least 0.05. B/C judgments are not used to select a more favorable release conclusion. When the conditions are met, the state becomes `AWAITING_MAINTAINER`, awaiting a maintainer's decision before outer evaluation runs.

After reviewing the release evidence, a maintainer can explicitly publish the candidate, then continue with subsequent tasks and revalidation:

```bash
python3 -m tracevolve publish --study .tracevolve/p1a-smoke-002 \
  --expected-generation 0 --maintainer YOUR_NAME --confirm
python3 -m tracevolve resume --study .tracevolve/p1a-smoke-002
```

The baseline is used when a candidate is rejected or evidence is insufficient. Tool contract v2 changes the mode associated with `requires_check=true` from `checked` to `verified`, with the tool description updated accordingly. Affected permissions are suspended. Isolated revalidation can still test the candidate, but does not automatically broaden its active permissions. A regression retires the candidate; other outcomes are deferred with the new evidence preserved.

```bash
python3 -m tracevolve rollback --study .tracevolve/p1a-smoke-002 \
  --expected-generation 1 --reason observed-regression
```

Release pointers and permissions are checked against the parent version, scope, and generation. In-flight runs retain their original snapshots. P1a does not automatically start another search round.

## Recovery task calibration

The experimental `recovery-v1` profile adds expiring check receipts, transient busy responses, and lost execution acknowledgments within the resource task domain. These are predefined procedural environment scenarios. Environment faults and agent handling errors are recorded separately; successfully handling an injected fault does not trigger a candidate proposal. See the [recovery protocol](docs/recovery-protocol.md) (Chinese).

```bash
python3 scripts/calibrate_tasks.py --config configs/calibration-recovery.json --study .tracevolve/difficulty-recovery-001
python3 scripts/audit_resource_runs.py --study .tracevolve/difficulty-recovery-001
# After reviewing calibration evidence, use an independent formal task split:
python3 -m tracevolve run --config configs/p1a-recovery.json --study .tracevolve/p1a-recovery-001
```

Latest evidence: all six calibration/development runs succeeded with zero agent handling errors. No natural candidate or real improvement has been established; see [recovery acceptance](docs/recovery-acceptance.md) (Chinese).

Calibration is capped at two baseline sessions. The formal configuration allows four development tasks and at most 32 sessions. Both retain the normal baseline and binary task-success score; no improvement is assumed.

## Authentication, isolation, and costs

- Only existing ChatGPT authentication is reused. Each run leases a restricted isolated directory under the authentication home, preserves refreshed credentials, and removes the directory after reconciliation. Concurrent login changes retain both versions and stop execution for manual reconciliation. Credentials are never stored in the study directory or Git. API key environment variables are not passed to child processes.
- Each role uses a fresh session, an independent workspace, and a temporary Codex configuration directory. Personal configuration and rules are excluded; plugins, memory, shell, browser, and sub-agents are disabled. Required system proxy settings are preserved.
- Before execution, `codex debug prompt-input` checks the context. External personal Skills are explicitly disabled and the context is checked again; execution stops if any remain. Codex's built-in system instructions and system Skill metadata are part of the fixed runtime, and context records are retained.
- Execution agents receive only the resource MCP tools for their run. Proposers and judges have no resource MCP registration, shell access, or file-reading entry point. They receive only the selected development or validation inputs. Held-out data never enters judgment requests.
- Each CLI session attempt is persisted before launch. Uncertain attempts caused by interruption or timeout prevent automatic resubmission on resume. Internal HTTP requests, retries, and per-request token limits are not fully exposed by `exec --json`; the framework does not claim to control them.
- Available token fields, elapsed time, tool calls, and session counts are recorded. Subscription costs in USD remain `null`, with `accounting_complete=false` and `budget_compliant=null`. These values do not establish free usage, USD budget compliance, or equal-cost comparisons.

## Study artifacts

```text
.tracevolve/<study>/
  manifest.json          Frozen settings, task splits, source and runtime identity
  state.sqlite           Control state, request ledger, append-only audit events
  objects/<sha>.json     Immutable candidates, runs, evidence, and judgments
  executions/<id>/       Raw stdout/stderr, startup context, tool and loading receipts
  judgments/sealed.json  Judgments sealed using the original request hashes
  export/records.v2.json v0.2.1-compatible input (P0 only)
  replay/               Original replay results, with no model calls
  report.json            Integrity, candidate counts, stop reasons, and usage
```

Study directories are excluded from Git by default. `verify` checks objects, raw files, and the audit chain. Hashes detect content changes; they are not third-party execution certification. Start a new study after changing code, CLI version, model settings, or task rules. `resume` reuses only identical requests and does not silently retry failed calls. If an uncertain call exists, review the existing logs manually first.

You can also replay resource tool state offline to check every recorded response and final outcome:

```bash
python3 scripts/audit_resource_runs.py --study .tracevolve/p0-smoke-001
```

This check requires the environment source to match the runtime snapshot and does not query the model again. Full source files are also stored in the study object store so historical runs can be reviewed after implementation changes.

## Tests and further reading

```bash
python3 -m unittest discover -s vendor/agent_validation_pilot -v
python3 -m unittest discover -s tests -v
```

The original 119 regression tests remain unchanged. Additional tests cover adapter test doubles running as real subprocesses, real MCP stdio processes, failure handling and ledger behavior, and explicitly labeled synthetic loop tests. Synthetic tests do not establish live model experiment results.

Further documentation is currently in Chinese:

- [Architecture and extension boundaries](docs/architecture.md)
- [Input and state contracts](docs/contracts.md)
- [Execution and reproduction](docs/reproduction.md)
- [Stage acceptance records](docs/acceptance.md)
- [Legacy tool provenance and checksums](vendor/README.md)

Codex integration references: [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Authentication](https://learn.chatgpt.com/docs/auth), and [Configuration reference](https://developers.openai.com/codex/config-reference). Specific behavior is also verified against calibration records from the pinned local CLI.
