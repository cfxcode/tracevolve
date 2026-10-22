# Compound code maintenance smoke

This protocol is fixed after the first six-task code scan completed with no
natural candidates, and before the next model call. The same model, baseline,
64-tool limit and 300-second timeout remain. No failed task is fabricated.

`configs/p1a-code-compound.json` fixes seed 2104510, one development task, one
candidate target, three heldout pairs, and 24 total sessions. All 11 frozen issue
templates from `p1a-code-smoke-001` are excluded, including its unused validation,
heldout and followup tasks. Five new compound issue templates extend the corpus;
the previously unused retry-policy issue is reserved for followup.

| Split | Issues |
|---|---|
| Development | Sequential atomic JSON document patches |
| Validation | Nested transactions with snapshot reads |
| Heldout | Weighted TTL LRU, weighted range normalization, reference configuration |
| Followup / contract change | Retry policy |

The document patch issue covers array insertion, sequential move semantics,
copy independence, pointer escaping, atomic rollback, and strict Boolean versus
numeric comparisons. Its semantics are stated in the issue, with 27 fixed host
checks including the public example. This is a curated maintenance fixture, not
a claim about production bug frequency or a full RFC compliance benchmark.

Reference repairs, broken starters, module renaming, input immutability and split
separation are checked offline before inference. Passing all development checks
still yields no candidate and stops the scan. A failure may produce a Skill
proposal, but an invalid proposal or non-improving candidate is not forced through.

A pre-run evaluator correction accepts valid `lookup` import aliases and module
imports, instead of assuming a consumer-local symbol named `lookup`. The first
API run used ordinary direct imports and its original acceptance result was not
affected. Its raw evidence and original source archive remain unchanged.

```bash
python3 -m tracevolve run --config configs/p1a-code-compound.json --study .tracevolve/p1a-code-compound-001
python3 scripts/summarize_studies.py --study .tracevolve/p1a-code-compound-001 --output docs/code-compound-data.json
```

The generic controlled-code tool, evidence and release contract remains
[code-maintenance-v1](code-maintenance-protocol.md). These code results never
stand in for the original resource P0 export or an equal-cost comparison.
