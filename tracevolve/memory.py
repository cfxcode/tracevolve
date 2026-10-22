from __future__ import annotations

from dataclasses import asdict

from .contracts import Bundle
from .tasks import generate, tool_contract
from .util import digest, now


def applicability(permit, *, task_family, contract, bundle_hash, trusted=True):
    """Three-valued check. Unknown is never interpreted as permission."""
    checks = []
    def add(name, value, source):
        checks.append({"checker": name, "version": 1, "result": value, "source": source})
    add("permit_status", "applicable" if permit.get("status") == "active" else "inapplicable", "registry")
    add("task_family", "applicable" if task_family == permit.get("task_family") else "inapplicable", "TaskProvider")
    add("bundle", "applicable" if bundle_hash == permit.get("bundle_hash") else "inapplicable", "immutable Bundle")
    expected = permit.get("dependencies", {})
    for key, value in expected.items():
        outcome = "unknown" if not trusted or key not in contract else "applicable" if contract[key] == value else "inapplicable"
        add("tool_contract." + key, outcome, "EnvironmentAdapter contract")
    values = [c["result"] for c in checks]
    result = "inapplicable" if "inapplicable" in values else "unknown" if "unknown" in values or not expected else "applicable"
    return {"result": result, "checks": checks}


def prepare_release(store, candidate, unit, bank, contract):
    if store.state("release_decision"):
        return store.state("release_decision")
    # Fixed P1a rule: judgment A only; B/C are additional fixed diagnostics,
    # never cherry-pick a favorable judge or use outer evaluation to publish.
    entry = next(v for v in bank.values() if v["unit_id"] == unit["unit_id"] and v["group"] == "A")
    evidence = unit["initial_evidence"] + unit["extra_B"] + unit["extra_C"]
    complete = all(e["valid"] for e in evidence)
    gains = [e["observation"]["candidate"].get("utility", 0) - e["observation"]["baseline"].get("utility", 0) for e in evidence]
    hard_checks = {"execution_complete": complete, "no_regression": all(g >= 0 for g in gains),
                   "positive_validation_gain": sum(gains) / len(gains) >= .05,
                   "boundary_checks": True}
    judgment = entry["response"]
    if judgment is None:
        status, reason = "deferred", "judge_error"
    elif not judgment["accept"]:
        status, reason = "rejected", "judge_rejected"
    elif not all(hard_checks.values()):
        status, reason = "deferred", "release_checks_not_satisfied"
    else:
        status, reason = "deferred", "maintainer_confirmation_required"
    permit = {"id": "permit-" + candidate["id"], "status": "pending_validation",
              "bundle_hash": Bundle(**candidate["candidate"]).hash, "task_family": "resource-dependency-tasks",
              "dependencies": dict(contract),
              "supporting_evidence": [store.put(e) for e in evidence], "counterexamples": [],
              "candidate": candidate["id"], "at": now()}
    # Execute boundary checks against a temporary active permit, never expose it
    # to runtime or persist an unapproved active state.
    probe = {**permit, "status": "active"}
    hard_checks["boundary_checks"] = (
        applicability(probe, task_family="unrelated", contract=contract, bundle_hash=permit["bundle_hash"])["result"] == "inapplicable"
        and applicability(probe, task_family=permit["task_family"], contract={}, bundle_hash=permit["bundle_hash"])["result"] == "unknown")
    decision = {"status": status, "reason": reason, "judgment": judgment,
                "candidate": candidate["id"], "hard_checks": hard_checks,
                "evidence_refs": permit["supporting_evidence"], "at": now(),
                "publish_eligible": status == "deferred" and reason == "maintainer_confirmation_required" and all(hard_checks.values())}
    store.set("permit", permit)
    store.set("release_decision", decision)
    return decision


class Registry:
    def __init__(self, store):
        self.store = store

    def pointer(self, scope="resource-dependency-tasks"):
        row = self.store.db.execute("SELECT * FROM pointers WHERE scope=?", (scope,)).fetchone()
        return dict(row) if row else {"scope": scope, "bundle": Bundle().hash, "generation": 0}

    def publish(self, candidate, *, expected_generation, maintainer):
        s = self.store
        if not maintainer or not maintainer.strip():
            raise ValueError("explicit maintainer identity is required")
        s.db.execute("BEGIN IMMEDIATE")
        try:
            s.verify()
            self._verify_candidate(candidate)
            decision = s.state("release_decision", {})
            if not decision.get("publish_eligible") or candidate["id"] != decision.get("candidate"):
                raise ValueError("candidate lacks passing release evidence")
            permit = s.state("permit")
            if not permit or permit["status"] != "pending_validation":
                raise ValueError("permit is not pending approval")
            refs = decision.get("evidence_refs", [])
            if (not refs or refs != permit.get("supporting_evidence")
                    or decision.get("judgment", {}).get("accept") is not True
                    or not decision.get("hard_checks") or not all(decision["hard_checks"].values())):
                raise ValueError("release evidence/constraints are missing or inconsistent")
            gains = []
            for ref in refs:
                evidence = s.get(ref)
                if evidence.get("valid") is not True or evidence.get("status") != "completed":
                    raise ValueError("incomplete release evidence")
                observed = evidence["observation"]
                if any(observed[side].get("status") != "completed" for side in ("baseline", "candidate")):
                    raise ValueError("incomplete release pair")
                gains.append(observed["candidate"]["utility"] - observed["baseline"]["utility"])
            if any(g < 0 for g in gains) or sum(gains) / len(gains) < .05:
                raise ValueError("release evidence does not meet the fixed gain policy")
            current = self.pointer()
            if current["generation"] != expected_generation or current["bundle"] != candidate["baseline_hash"]:
                raise ValueError("stale parent: revalidation required before publication")
            new = Bundle(**candidate["candidate"])
            s.put(asdict(new))
            s.db.execute("INSERT INTO pointers VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET bundle=excluded.bundle,generation=excluded.generation",
                         (current["scope"], new.hash, current["generation"] + 1))
            permit = {**permit, "status": "active", "maintainer": maintainer}
            decision = {**decision, "status": "published", "maintainer": maintainer, "parent": current, "at": now()}
            for key, value in [("permit", permit), ("release_decision", decision)]:
                from .util import canonical
                s.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical(value)))
            s.event("published", decision)
            s.db.commit()
        except BaseException:
            s.db.rollback()
            raise
        return decision

    def _verify_candidate(self, candidate):
        """Bind the release to its frozen candidate, judgment and executed pairs."""
        from .study import DECISION, projection, public_trace
        from .util import canonical
        from .legacy import pilot
        s = self.store
        frozen = s.get(s.state("candidates_frozen"))
        if candidate not in frozen or candidate not in s.state("candidates", []):
            raise ValueError("publication candidate differs from frozen candidate")
        permit = s.state("permit", {})
        bundle = Bundle(**candidate["candidate"])
        if permit.get("bundle_hash") != bundle.hash or permit.get("candidate") != candidate["id"]:
            raise ValueError("publication bundle differs from permit")
        unit = next((u for u in s.state("units", []) if u["patch_id"] == candidate["id"]), None)
        if unit is None:
            raise ValueError("publication has no validation unit")
        decision = s.state("release_decision", {})
        bank = s.get(s.state("judgments_sealed"))
        entry = next((e for e in bank.values() if e["unit_id"] == unit["unit_id"] and e["group"] == "A"), None)
        if not entry or decision.get("judgment") != entry["response"]:
            raise ValueError("release judgment differs from sealed judgment")
        calls = {c["id"]: c for c in s.calls()}
        judgment_call = calls.get(entry["run_id"])
        if not judgment_call or judgment_call["status"] != "completed":
            raise ValueError("release judgment has no completed call")
        judgment = s.get(judgment_call["result_hash"])
        if entry["response"] != {**judgment.get("response", {}), "usage": {"cost_usd": judgment.get("cost_usd")}}:
            raise ValueError("release judgment differs from executed result")
        data = {"schema_version": 2, "additional_budget_usd": None,
                "decision": DECISION,
                "shared_overheads": {"candidate_generation_usd": None, "calibration_usd": None}, "units": [unit]}
        settings = pilot.validate_data(data, demo=bool(judgment.get("synthetic")))
        expected_request = pilot.judge_request(unit, unit["initial_evidence"], settings)
        if entry["request"] != expected_request or pilot.digest(entry["request"]) not in bank:
            raise ValueError("sealed judgment request mismatch")
        judge_input = s.get(judgment_call["request_hash"])
        if not judge_input.get("prompt", "").endswith(canonical(expected_request)):
            raise ValueError("judgment call input differs from sealed evidence")
        expected_refs = [digest(e) for channel in ("initial_evidence", "extra_B", "extra_C") for e in unit[channel]]
        if decision.get("evidence_refs") != expected_refs:
            raise ValueError("release evidence missing or inconsistent with validation unit")
        for key, channel in (("initial", "initial_evidence"), ("repeat", "extra_B"), ("rename", "extra_C")):
            evidence = unit[channel][0]
            for side in ("baseline", "candidate"):
                call_id = unit["unit_id"] + ":" + key + ":" + side
                call = calls.get(call_id)
                if not call or call["status"] != "completed":
                    raise ValueError("release pair has no completed call")
                result = s.get(call["result_hash"])
                ref = s.state("run:" + call_id)
                run = s.get(ref) if ref else result  # Synthetic adapter stores full runs directly.
                if ref and any(run.get(k) != v for k, v in result.items()):
                    raise ValueError("run record differs from executed result")
                if (evidence["observation"][side] != projection(run)
                        or evidence["observation"]["traces"][side] != public_trace(run)
                        or run["harness"] != unit["frozen"][side]
                        or run["task_sha256"] != evidence["task"]["sha256"]):
                    raise ValueError("release evidence differs from executed pair")
                snapshot = run["execution_snapshot"]
                expected_bundle = bundle if side == "candidate" else Bundle()
                from .contracts import ExecutionSnapshot, frozen_harness
                if (run["snapshot_hash"] != ExecutionSnapshot(**snapshot).hash
                        or run["harness"] != frozen_harness(expected_bundle, ExecutionSnapshot(**snapshot))):
                    raise ValueError("execution snapshot differs from frozen harness")
                request = s.get(call["request_hash"])
                task_input = request.get("server_input", request)
                expected_task = evidence["task"]["content"] if "server_input" in request else evidence["task"]
                if (task_input.get("bundle") != asdict(expected_bundle) or task_input.get("task") != expected_task
                        or task_input.get("contract") != snapshot["tool_contract"]):
                    raise ValueError("execution request differs from release evidence")
                if snapshot["bundle_hash"] != expected_bundle.hash:
                    raise ValueError("executed bundle differs from publication bundle")
                if not run.get("synthetic") and not run.get("load_evidence", {}).get("verified"):
                    raise ValueError("publication lacks actual loading evidence")

    def rollback(self, *, expected_generation, reason):
        s = self.store
        s.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.pointer()
            if current["generation"] != expected_generation:
                raise ValueError("stale rollback generation")
            if current["bundle"] == Bundle().hash:
                raise ValueError("no published candidate to roll back")
            s.db.execute("UPDATE pointers SET bundle=?, generation=generation+1 WHERE scope=?", (Bundle().hash, current["scope"]))
            permit = s.state("permit")
            if permit:
                from .util import canonical
                permit = {**permit, "status": "suspended", "reason": "rollback"}
                s.db.execute("UPDATE state SET value=? WHERE key='permit'", (canonical(permit),))
            s.event("rollback", {"from": current, "to": Bundle().hash, "reason": reason})
            s.db.commit()
        except BaseException:
            s.db.rollback()
            raise
        return self.pointer()


def contract_changed(store, contract):
    permit = store.state("permit")
    if not permit:
        return {"affected": False, "reason": "no_skill_candidate"}
    changed = [k for k, v in permit["dependencies"].items() if contract.get(k) != v]
    record = {"affected": bool(changed), "changed_dependencies": changed, "previous_status": permit["status"],
              "new_contract": contract, "at": now()}
    updates = {"contract_change": record}
    if changed:
        updates["permit"] = {**permit, "status": "needs_revalidation", "change": record}
    store.set_many(updates)
    return record


def selected_bundle(store, candidate, contract):
    permit = store.state("permit", {})
    proposed = Bundle(**candidate["candidate"])
    check = applicability(permit, task_family="resource-dependency-tasks", contract=contract, bundle_hash=proposed.hash)
    pointer = Registry(store).pointer()
    enabled = check["result"] == "applicable" and pointer["bundle"] == proposed.hash
    return proposed if enabled else Bundle(), {"applicability": check, "pointer": pointer, "skill_enabled": enabled}


def run_followup(study, frozen, candidate):
    s, c = study.store, study.config
    if s.state("lifecycle"):
        return s.state("lifecycle")
    # Additional tasks were frozen by seed/rule before any sealed results.
    snapshot = s.state("followup_snapshot")
    if snapshot is None:
        task = generate(c.seed + 30000, f"followup-{c.seed}", "post_release", c.task_profile)
        bundle, selection = selected_bundle(s, candidate, study.contract)
        snapshot = {"task": task, "bundle": asdict(bundle), "selection": selection}
        s.set("followup_snapshot", snapshot)
    task, bundle, selection = snapshot["task"], Bundle(**snapshot["bundle"]), snapshot["selection"]
    first = study._run("p1a-followup", bundle, task, "post_release")
    changed = tool_contract("resource-recovery-v2" if study.contract.get("check_receipt_required") else "resource-v2")
    change = s.state("contract_change") or contract_changed(s, changed)
    after, check = selected_bundle(s, candidate, changed)
    paused = not check["skill_enabled"]
    # Explicit experiment authorization permits testing the candidate without
    # making it available to ordinary post-release execution.
    result = study._run("p1a-revalidation-candidate", Bundle(**candidate["candidate"]), task, "revalidation", changed)
    base = study._run("p1a-revalidation-baseline", Bundle(), task, "revalidation", changed)
    permit = s.state("permit")
    complete = result["status"] == base["status"] == "completed"
    regression = complete and result["evaluation"]["utility"] < base["evaluation"]["utility"]
    revalidation = {"status": "rejected" if regression else "deferred",
                    "reason": "regression_under_new_contract" if regression else "new_contract_needs_independent_release_confirmation" if complete else "incomplete_execution",
                    "evidence": [s.put(base), s.put(result)], "contract": changed, "at": now()}
    s.set("revalidation_decision", revalidation)
    if permit:
        permit = {**permit, "status": "retired" if regression else "needs_revalidation",
                  "revalidation_evidence": [s.put(base), s.put(result)],
                  "reason": "new contract requires new release decision and maintainer approval"}
        s.set("permit", permit)
        decision = s.state("release_decision")
        s.set("release_decision", {**decision, "publish_eligible": False,
                                   "reason": "contract_changed_requires_new_decision"})
    lifecycle = {"followup_run": first["run_id"], "selection": selection, "contract_change": change,
                 "skill_paused_for_new_contract": paused, "selection_after_change": check,
                 "revalidation_runs": [base["run_id"], result["run_id"]],
                 "revalidation_complete": base["status"] == result["status"] == "completed",
                 "revalidation_decision": revalidation,
                 "was_published_before_change": change.get("previous_status") == "active",
                 "note": "candidate delivery is not evidence of comprehension; permit expansion is not automatic"}
    s.set("lifecycle", lifecycle)
    return lifecycle
