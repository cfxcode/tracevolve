# Shared-inventory calibration and collection

The `capacity-v1` implementation and evidence collection ran successfully, but
did not produce a natural candidate or complete the real optimization loop.
The implementation is in commit `bb9e982`; subsequent profiles must not be used
to reinterpret or silently resume these frozen executions.

| Study | Live sessions | Gateway calls | Successes | Agent errors | Candidates |
|---|---:|---|---:|---:|---:|
| difficulty-capacity-001 | 2 | 51, 54 | 2 | 0 | calibration only |
| p0-capacity-smoke-001 | 6 | 45, 46, 55, 49, 47, 47 | 6 | 0 | 0/1 |
| p1a-capacity-001 | 6 | 48, 47, 48, 53, 51, 45 | 6 | 0 | 0/1 |
| difficulty-capacity-hard-001 | 1 | 52 | 1 | 0 | calibration only |

All 15 executions have verified bundle delivery, deterministic tool-state
replay, event/call/result consistency and preserved raw-file hashes (150 files).
The machine-readable record is [capacity-data.json](capacity-data.json).
Dollar costs remain null. No proposal, validation pair, judgment, heldout pair,
publication or revalidation ran in these studies. Both formal batches report
`NO_ELIGIBLE_CANDIDATE`, `target_met=false` and `data_complete=false`.
The five-candidate pilot configuration was prepared but not executed: the
required smoke evidence chain has not completed.

Independent offline tests covered 50 seeds under both contract versions, strict
inventory validation, atomic errors, idempotence, renaming, stdio, replay and
synthetic protocol/lifecycle wiring. At this implementation milestone, 119
unaltered legacy tests and 85 project tests passed (204 total); synthetic
lifecycle tests are not evidence of real Skill improvement.

The selected stress calibration was declared before its model execution.
An offline scan of seeds 0–1999 found 16,326 deterministic reference-search states
at seed 1028. The model nevertheless completed it without errors. This is
evidence against using that reference algorithm's search count alone as a
model-difficulty proxy; it is not proof that every resource task is easy.
The scan can be reproduced with `python3 scripts/scan_capacity_complexity.py`;
its output is retained in [capacity-complexity.json](capacity-complexity.json).

The next calibration changes the decision being tested to pool assignment,
within the same resource-job domain, under the separate
[allocation protocol](allocation-protocol.md). It preserves the model, ordinary
baseline and outcome definitions. Neither zero candidates nor a working task
generator substitutes for P0/P1a's real candidate evidence chain.
