# Resource-pool assignment protocol (allocation-v1)

Recorded before implementation or live allocation runs. The capacity-v1 batch
and its selected high-search calibration all succeeded without agent errors
(15 sessions). High reference-search effort alone did not expose a model failure.
Those observations remain separate and unchanged.

This profile stays in the resource-job domain and keeps the baseline Prompt,
model, success score, A/B/C comparison, candidate criteria and dollar limitations.
It tests a different planning decision: assigning 25 jobs to five finite resource
pools. Each pool has capacity 100; every job's units and dependencies are public.
The tasks are generated from feasible partitions, shuffled, with opaque job IDs.
No partition witness is given to the model. A final zero-unit goal depends on
all production jobs. A topological execution of a valid assignment needs at
most 55 gateway calls, within the existing 64-call allowance.

The five tool names remain unchanged. list_jobs returns initial and remaining
pool capacities. inspect_job returns units and dependencies. run_job adds a
required string `pool`: choose a public pool for a positive-unit job; use an
empty string for the zero-unit final goal. A successful job atomically reserves
its units, permanently for this task. A repeated completed job is idempotent.
Insufficient capacity and invalid pool selections fail without mutation.
Locally legal choices can make remaining jobs impossible; this is explicit in
the public tool description. submit succeeds only for the exact completed goal
set. No environment faults are injected; illegal decisions remain agent errors.

An independent offline packing solver certifies every frozen task and the P1a
followup task before model execution. It is never imported by the live runtime,
provided to proposal or judgment sessions, or used to manufacture a modification.
All task/source splits and exclusions are frozen before development. No
calibration task becomes a formal candidate source.

Run two baseline-only calibration tasks (seed 1204501). If execution and audits
pass, independently scan up to six P0 development tasks (seed 1304501), then up
to six P1a development tasks (seed 1404501). Each formal study allows 32 sessions
including proposals, six validation runs, three judges, six heldout runs and
(P1a only) followup/revalidation. A complete P0 smoke permits an independent
five-candidate pilot, five heldout pairs each, up to 24 development tasks and
160 sessions (seed 1504501). Candidate order remains first legal distinct
proposals in development order. No change is required to improve.

For P1a, resource-allocation-v2 preserves pool semantics and changes checked mode
to verified. The full tool contract remains a permit dependency. Publication
requires existing fixed evidence gates and a maintainer decision. Rejected or
deferred candidates remain disabled in ordinary execution; isolated revalidation
does not imply release. This selected procedural task family does not establish
general optimization efficacy, even if an engineering loop completes.

## Conditional precision calibration (declared before any precision-model run)

If both uniform formal batches finish without a candidate, test the same
allocation semantics at finer integer granularity. `allocation-precision-v1`
uses five pools of capacity 1000 and 25 job sizes in [100, 300], generated as
five feasible partitions and shuffled. These are newly sampled integers, not
just the original values multiplied by ten. This selected full-capacity packing
regime has fewer accidental exact-sum combinations; it is a stress distribution,
not a claim about ordinary production utilization. All task attributes remain
public, and a reference assignment still takes 55 gateway calls. Model, Prompt,
64-call/300-second limits and scoring stay fixed.

Use two baseline calibration tasks (seed 1604501), followed, if technically
valid, by independent P0 smoke (seed 1704501) and P1a (seed 1804501) with up to
six development tasks and 32 sessions each. Five-candidate pilot seed 1904501
is reserved for after a complete smoke. The finer-granularity study must exclude
preceding allocation templates and preserve all unsuccessful calibration or
formal attempts. No observed candidate or validation result determines which
seed is used. The protocol may stop for a genuine infrastructure error; it must
not relabel a timeout as an optimization defect or retry an uncertain request.
