# Shared-resource planning protocol (capacity-v1)

This protocol is written before model calibration. It extends the existing
resource task family with finite shared inventories. It is a new task profile,
not a reinterpretation of previous successful runs or a second task domain.

The five MCP operations remain load_bundle, list_jobs, inspect_job, run_job and
submit. list_jobs exposes inventory capacities, initial quantities and current
quantities. inspect_job exposes each job's consumes/produces vectors, dependencies,
required mode and status. run_job atomically subtracts consumes then adds produces;
it requires sufficient input stock and a final quantity no greater than capacity.
Errors do not mutate stock or completion state. Completed jobs are idempotent.
There is no undo; a locally legal execution order can leave remaining goals
unreachable. This models irreversible allocation and production, and is stated
in the tool descriptions available equally to baseline and candidate.

The generator constructs 20–24 production jobs plus a final goal, starting from
a legal resource trajectory, with sparse acyclic prerequisites and shuffled
opaque job IDs. No witness order is delivered to the model. An independent
exhaustive state-space solver checks solvability, without using the generator's
witness. A solution uses at most 53 gateway calls (load, list, inspect/run each
job, submit), within the unchanged 64-call limit. Capacity values and effects are
public task data, not hidden scoring criteria. Utility remains successful submit
(0 or 1); errors are recorded separately and can trigger natural proposals.

First run two baseline-only calibration tasks. If the environment and loading
checks pass, freeze independent P0 smoke (one Prompt modification, three heldout
pairs) and P1a (one Skill modification, three heldout pairs) studies. Their
development scans permit up to six tasks each, with at most 32 sessions per study.
A five-candidate P0 pilot is considered after a complete smoke, using the same
protocol with five heldout pairs per candidate and at most 160 sessions.
Calibration task templates are excluded from subsequent discovery. Each study
excludes all templates from preceding studies. Candidate order is development
order, never chosen for favorable validation outcomes. No model, baseline Prompt,
scoring, judge or statistical threshold is changed in response to results.

These batch limits bound collection; they do not mean the project is complete.
If a batch has no candidate, report it and reassess task adequacy. If inference
fails or is uncertain, preserve the attempt and stop dependent execution.
Do not manufacture a failure or promote a handled environment fault to a defect.

For P1a, resource-capacity-v2 changes checked mode to verified and preserves all
inventory semantics. The original Skill's full contract dependency is invalidated;
subsequent ordinary use must remain disabled while isolated revalidation runs.
A rejected/deferred candidate is a valid decision; do not publish just to show a
positive result. Subscription USD remains null, and neither equal-dollar cost
nor statistical optimization efficacy is claimed by these small experiments.

P0 retains the frozen A/B/C comparison and v0.2.1 data format: additional resource
payloads are preserved by ID renaming. Conclusions are limited to this explicitly
versioned resource profile; do not pool its observations with basic-v1 as though
they came from a single unchanged task distribution.

## Offline hardness calibration, recorded before the stress execution

The first uniform P0 batch yielded no candidate. To assess whether search
complexity is a useful difficulty indicator, an offline scan of generator seeds
0–1999 used the independent solver's deterministic search-state count. This was
not a model sweep and did not generate or select any Prompt/Skill candidate.
The largest count was 16,326 states at seed 1028 (next largest: 2,690 at seed 126).
Removing prerequisite edges on ten offline samples made search easier and was
not adopted. Both observations concern this reference algorithm, not proven
model difficulty.

If the uniform P1a batch also yields no candidate, run exactly one independent
baseline calibration task with seed 1028, using
`configs/calibration-capacity-hard.json`. The same tool contract, model, Prompt,
scoring, 64-call and 300-second limits apply. This intentionally selected stress
case is not a representative random sample. It is calibration-only, excluded
from subsequent candidate collection. A success is retained as success; a
failure does not authorize rewriting the original batches or reusing this task
as a formal candidate source.
