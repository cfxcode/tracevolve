#!/usr/bin/env python3
"""Make conspicuously synthetic fixtures. No model is called, no empirical result is claimed."""
import json
from pathlib import Path

from synthetic_fixture import upgrade_synthetic_fixture


def event(event_id, kind, b=None, c=None, cost=0.01, valid=True, note=""):
    obs = {"note": note, "synthetic_fixture": True}
    if b is not None:
        obs.update(baseline_utility=b, candidate_utility=c,
                   baseline_success=bool(b), candidate_success=bool(c))
    if not valid:
        obs["error"] = "Deliberate synthetic invalid-probe example."
    return {"event_id": event_id, "type": kind, "observation": obs,
            "cost_usd": cost, "synthetic": True, "valid": valid}


def unit(name, initial, extra_b, extra_c, future_b, future_c):
    return {
        "unit_id": name,
        "baseline_id": "synthetic-baseline",
        "patch_id": "synthetic-patch-" + name,
        "patch_description": "Artificial fixture for I/O and accounting tests; not an LLM-generated modification.",
        "initial_evidence": [event(name+"-initial", "complete_validation", *initial)],
        "extra_B": extra_b,
        "extra_C": extra_c,
        "heldout": [
            {"task_id": name+"-heldout-"+str(i), "synthetic": True,
             "baseline": {"utility": future_b, "execution_cost_usd": 0.001},
             "candidate": {"utility": future_c, "execution_cost_usd": 0.001}}
            for i in range(64)
        ]
    }


def main():
    units = [
        unit("agreement", (0,1),
             [event("agreement-b", "complete_validation", 0,1)],
             [event("agreement-c", "identifier_renaming", 0,1)], 0,1),
        unit("diagnostic_changes_evidence", (0,1),
             [event("different-b", "complete_validation", 0,1)],
             [event("different-c", "identifier_renaming", 1,0)], 1,0),
        unit("diagnostic_misleads", (0,1),
             [event("mislead-b", "complete_validation", 0,1)],
             [event("mislead-c", "identifier_renaming", 1,0)], 0,1),
        unit("invalid_probe_is_charged", (1,1),
             [event("invalid-b", "complete_validation", 1,1)],
             [event("invalid-c-generation", "probe_generation", cost=0.004),
              event("invalid-c-validation", "identifier_renaming", cost=0.006, valid=False)], 1,1),
        unit("unknown_cost_stays_unknown", (1,0),
             [event("unknown-b", "complete_validation", 1,0, cost=None)],
             [event("unknown-c", "identifier_renaming", 1,0, cost=None)], 1,0)
    ]
    document = {
        "schema_version": 1,
        "synthetic": True,
        "purpose": "INTEGRITY_TEST_ONLY_NOT_RESEARCH_EVIDENCE",
        "cost_semantics": "All numeric USD values are fabricated fixture values, not observed spending.",
        "additional_budget_usd": 0.03,
        "shared_overheads": {"candidate_generation_usd": 0.0, "calibration_usd": 0.0},
        "decision": {"delta": 0.05, "indifference": 0.01,
                     "min_heldout_pairs": 3, "utility_bounds": [0,1], "alpha": 0.05},
        "units": units
    }
    document = upgrade_synthetic_fixture(document)
    dest = Path(__file__).resolve().parent / "data" / "demo_records.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(document, ensure_ascii=False, indent=2)+"\n")
    print(dest)


if __name__ == "__main__":
    main()
