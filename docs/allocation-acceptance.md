# Resource-pool assignment collection

The initial `allocation-v1` study family ran under implementation `7fbbf2e`.
Its tasks require assigning 25 production jobs to five pools of capacity 100,
then completing a final zero-unit goal. All constraints are public; allocations
are irreversible within a task. This is still a procedural resource-job domain.

| Study | Sessions | Successes | Calls per task | Agent errors | Natural candidates |
|---|---:|---:|---:|---:|---:|
| difficulty-allocation-001 | 2 | 2 | 55 | 0 | calibration only |
| p0-allocation-smoke-001 | 6 | 6 | 55 | 0 | 0/1 |
| p1a-allocation-001 | 6 | 6 | 55 | 0 | 0/1 |

All 14 live executions have verified bundle delivery and deterministic state
replay. Event/call/result consistency and 140 raw-file hashes passed checks.
The full machine-readable summary is [allocation-data.json](allocation-data.json).
Observed elapsed time totals approximately 644.69 seconds. Token fields are
recorded separately; USD remains null, with no equal-dollar or budget-compliance
claim.

Both formal scans stopped at `NO_ELIGIBLE_CANDIDATE`, with `target_met=false`
and `data_complete=false`. They did not execute proposals, A/B/C validation,
judges, heldout evaluation, publication or revalidation. The five-candidate
pilot was prepared but not started because no complete smoke chain exists.
These successes do not constitute optimization acceptance or demonstrated gain.

At this implementation milestone, the 119 unchanged legacy tests and 91 project
tests passed (210 total). Allocation-specific checks include two modes across
50 seeds, state-preserving errors, duplicate actions, identifier renaming,
deterministic replay, MCP stdio and a reference schedule of 55 calls.

Before any finer-granularity execution, the
[protocol](allocation-protocol.md#conditional-precision-calibration-declared-before-any-precision-model-run)
declared a conditional `allocation-precision-v1` calibration: capacity 1000 and
new integer sizes in [100, 300], with unchanged model, baseline, modes, scoring
and execution limits. Its evidence must be reported separately. Historical
studies must use their archived source or the recorded implementation commit
for state replay; current source is not a silent replacement.
