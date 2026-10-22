# Code maintenance experiment v1

This is the explicitly authorized second task domain. It does not replace the
resource-only P0 protocol or establish P1b runtime portability.

Before any model execution, freeze the implementation, public task repositories,
independent acceptance cases, split identities, baseline, model, tool contract,
and session limits. First run a bounded P1a smoke study. A failed final acceptance
or an actual gateway error may produce a textual Skill proposal. Failing public
tests on the initially broken repository are diagnostic observations, not agent
errors. No candidate is fabricated when the baseline succeeds.

Each task is a small Python library maintenance issue. The user-facing issue
states the required behavior. Public examples are immutable; editable Python
files are task artifacts, never Harness candidates. Independent acceptance runs
outside the task gateway, after execution ends. It compares returned values with
host-owned expected values. Neither the executor nor the proposer/judge receives
another task's hidden cases. The judge receives validation traces and aggregate
results only, never heldout tasks or outputs.

Tools (JSON schemas in `tracevolve.code_tasks.tool_specs`):

- `load_bundle()` delivers the installed Prompt and optional Skill.
- `list_files()` returns paths and SHA-256 values, the issue and public examples.
- `read_file(path)` returns one allowlisted task file.
- `write_file(path, content)` replaces one existing editable file, at most 32 KiB.
  Version 2 additionally requires `expected_sha256`; stale writes are rejected.
- `run_tests()` executes immutable public examples against the current files.
- `check_examples(cases)` runs up to 32 model-authored input/expected-output
  examples. These supplement, and never replace, immutable checks.
- `submit()` freezes the final task artifact. It does not reveal acceptance cases.

All unique calls count, including invalid arguments and bundle loads. Every
response is persisted. There is no shell, external filesystem, network, package
installation or user repository access. Python execution requires the macOS
Seatbelt sandbox; unsupported hosts fail closed (offline pure tests remain
portable). A fresh directory and scrubbed environment are used per test batch.
CPU, output, file and wall-time bounds apply. Source artifacts and public test
results are replayable. This sandbox is a local engineering boundary, not a
claim of isolation against kernel exploits.

The fixed validation is an initial pair, an independent repeated pair, and a
pair with consistent internal module renaming. A/B/C judgments are sealed before
three heldout pairs. This is **code-maintenance-v1**, not a v0.2.1 resource export.
The utility is binary full acceptance; passing subsets do not count as success.
Release uses the existing conservative P1a gate and explicit maintainer decision.
Rejection/deferment is a valid outcome, not evidence of an optimization gain.
Followup selection and v2 compare-and-swap contract revalidation use the same
code task adapter. An unapproved candidate cannot become active through testing.

Issue templates, not numerical examples or renamed modules, define split
identity. Exhausting a finite corpus is an explicit error, never an infinite
resampling loop. No continuous search, additional runtime, paid API access or
automatic Skill publication is authorized by this protocol.

## Frozen smoke plan and reproduction

The initial corpus consists of 12 curated small library issues, not claims of
production bug prevalence. Each exposes its complete behavioral requirement.
The smoke seed `2004503` fixes the following split before any live execution:

- Development: API migration, decimal money, atomic batch, TTL cache, merge patch,
  half-open intervals (maximum 6).
- Validation: last-occurrence event deduplication (one task, three paired probes).
- Heldout: topological order, pagination, incremental UTF-8 (three pairs).
- Followup and contract revalidation: CSV records.

The last two sets remain unavailable to the proposer and judge. No task is
selected after observing the smoke outcomes. The 32-session ceiling includes
execution, proposals, judgments and lifecycle checks; unused capacity is not a
request to keep searching. Model/version/reasoning and subscription accounting
are inherited from the existing pinned configuration. Unknown USD remains null.
The finite corpus does not currently support an independent five-candidate pilot;
that requires additional distinct issues after the smoke evidence is reviewed.

```bash
python3 -m tracevolve plan --config configs/p1a-code-smoke.json
python3 -m tracevolve run --config configs/p1a-code-smoke.json --study .tracevolve/p1a-code-smoke-001
python3 -m tracevolve verify --study .tracevolve/p1a-code-smoke-001
python3 scripts/audit_resource_runs.py --study .tracevolve/p1a-code-smoke-001
python3 scripts/summarize_studies.py --study .tracevolve/p1a-code-smoke-001 --output docs/code-maintenance-data.json
```

`run` freezes the study and automatically certifies every initial defect and
reference repair, including the renamed validation and followup, before its
first model call. The immutable certificate is materialized in `reference.json`.
Despite its historical filename, the audit script dispatches to either task
domain and recomputes code acceptance in the sandbox. Use the archived source
revision to replay older studies. Re-running remote inference may differ;
replaying saved artifacts establishes deterministic local evidence consistency.
