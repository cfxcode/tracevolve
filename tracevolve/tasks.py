from __future__ import annotations

import copy
import random

from .legacy import probe
from .util import digest


def tool_contract(version="resource-v1"):
    if version not in ("resource-v1", "resource-v2", "resource-recovery-v1", "resource-recovery-v2", "resource-capacity-v1", "resource-capacity-v2", "resource-allocation-v1", "resource-allocation-v2"):
        raise ValueError("unknown tool contract")
    contract = {"version": version, "explicit_dependencies": True,
            "query_execution_status": True, "invalid_actions_do_not_mutate": True,
            "checked_mode": "checked" if version.endswith("v1") else "verified",
            "standard_mode": "standard", "family": "resource-dependency-tasks"}
    if version.startswith("resource-recovery-"):
        contract.update(check_receipt_required=True, receipt_ttl_calls=3,
                        repeat_run_policy="reject_committed", environment_faults_may_commit=True,
                        fault_policy="once_per_job_per_execution")
    if version.startswith("resource-capacity-"):
        contract.update(shared_inventory=True, inventory_dimensions=4, atomic_inventory_effects=True,
                        irreversible_execution=True, repeat_run_policy="idempotent")
    if version.startswith("resource-allocation-"):
        contract.update(pool_assignment=True, allocation_policy="permanent_per_task",
                        repeat_run_policy="idempotent", invalid_pool_errors_do_not_mutate=True)
    return contract


def create_environment(task, contract, max_calls=64):
    if contract.get("pool_assignment"):
        from .allocation import AllocationEnvironment
        return AllocationEnvironment(task, contract, max_calls)
    if "pools" in task:
        raise ValueError("allocation task requires allocation contract")
    if contract.get("shared_inventory"):
        from .capacity import CapacityEnvironment
        return CapacityEnvironment(task, contract, max_calls)
    if "resources" in task:
        raise ValueError("capacity task requires capacity contract")
    if contract.get("check_receipt_required"):
        from .recovery import RecoveryEnvironment
        return RecoveryEnvironment(task, contract, max_calls)
    if any("fault" in j["payload"] for j in task["jobs"]):
        raise ValueError("recovery task requires recovery contract")
    return ResourceEnvironment(task, contract, max_calls)


def envelope(content, source_id, **metadata):
    return {"content": content, "sha256": digest(content), "source_id": source_id,
            "identifier_contract": "opaque", **metadata}


def validate(task):
    probe.validate_task(task)
    if not task["jobs"] or not task["goals"]:
        raise ValueError("nonempty jobs and goals required")
    allocation = "pools" in task
    if allocation:
        from .allocation import validate_pools
        validate_pools(task)
    capacity = "resources" in task
    if allocation and capacity:
        raise ValueError("mixed inventory profiles are unsupported")
    if capacity:
        from .capacity import validate_resources
        validate_resources(task)
    for job in task["jobs"]:
        if set(job) != {"id", "requires", "payload"}:
            raise ValueError("unexpected job fields")
        p = job["payload"]
        allowed = ({"kind", "requires_check", "units"},) if allocation else (({"kind", "requires_check", "consumes", "produces"},) if capacity else ({"kind", "requires_check"}, {"kind", "requires_check", "fault"}))
        if set(p) not in allowed or p["kind"] not in ("prepare", "build", "verify", "publish") or type(p["requires_check"]) is not bool:
            raise ValueError("invalid payload")
        if "fault" in p and p["fault"] not in ("stable", "busy_once", "lost_ack_once"):
            raise ValueError("invalid recovery fault category")


class ResourceEnvironment:
    def __init__(self, task, contract=None, max_calls=64):
        validate(task)
        self.task = copy.deepcopy(task)
        self.contract = contract or tool_contract()
        self.jobs = {j["id"]: j for j in task["jobs"]}
        self.done = set()
        self.submitted = False
        self.calls = 0
        self.errors = 0
        self.max_calls = max_calls
        self.loaded = False

    def call(self, name, args, *, bundle=None):
        """Count every unique gateway request, including loading and gate errors.

        The stdio transport deduplicates exact JSON-RPC retries before this call.
        Direct environment callers may omit bundle to exercise domain actions.
        """
        self.calls += 1
        if self.calls > self.max_calls:
            output = {"error": "TOOL_LIMIT"}
        elif bundle is not None and name == "load_bundle":
            if args != {}:
                output = {"error": "INVALID_ARGUMENT"}
            else:
                self.loaded = True
                output = {"bundle": bundle, "bundle_hash": digest(bundle)}
        elif bundle is not None and not self.loaded:
            output = {"error": "LOAD_BUNDLE_REQUIRED"}
        else:
            output = self._dispatch(name, args)
        if "error" in output:
            self.errors += 1
        return output

    def _dispatch(self, name, args):
        def error(code):
            return {"error": code}
        if self.submitted:
            return error("ALREADY_SUBMITTED")
        if not isinstance(args, dict):
            return error("INVALID_ARGUMENT")
        expected = {"list_jobs": set(), "inspect_job": {"job_id"}, "run_job": {"job_id", "mode"}, "submit": {"goal_ids"}}
        if name not in expected or set(args) != expected[name]:
            return error("INVALID_ARGUMENT")
        if name == "list_jobs":
            return {"jobs": [{"id": j["id"], "kind": j["payload"]["kind"]} for j in self.task["jobs"]]}
        if name == "submit":
            ids = args["goal_ids"]
            if not isinstance(ids, list) or any(type(i) is not str for i in ids) or len(set(ids)) != len(ids) or set(ids) != set(self.task["goals"]) or not set(ids) <= self.done:
                return error("INVALID_SUBMISSION")
            self.submitted = True
            return {"submitted": True}
        job_id = args["job_id"]
        if not isinstance(job_id, str):
            return error("INVALID_ARGUMENT")
        if job_id not in self.jobs:
            return error("UNKNOWN_JOB")
        job = self.jobs[job_id]
        if name == "inspect_job":
            return {**copy.deepcopy(job), "status": "done" if job_id in self.done else "pending"}
        required = self.contract["checked_mode"] if job["payload"]["requires_check"] else self.contract["standard_mode"]
        if args["mode"] != required:
            return error("WRONG_MODE")
        if not set(job["requires"]) <= self.done:
            return error("DEPENDENCIES_NOT_DONE")
        if job_id in self.done:
            return {"status": "already_done", "job_id": job_id}
        self.done.add(job_id)
        return {"status": "done", "job_id": job_id}

    def evaluate(self):
        return {"success": self.submitted, "utility": float(self.submitted),
                "tool_calls": self.calls, "tool_errors": self.errors,
                "done": [j["id"] for j in self.task["jobs"] if j["id"] in self.done]}


def semantic_identity(task, include_payload=True):
    """Recursive DAG structural identity independent of IDs and array ordering.

    Conservative: equivalent recursive signatures are grouped, even if this
    merges some non-isomorphic DAGs. It never uses task/source IDs as templates.
    """
    by_id = {j["id"]: j for j in task["jobs"]}
    cache = {}
    def signature(i):
        if i not in cache:
            j = by_id[i]
            cache[i] = digest({"parents": sorted(signature(p) for p in j["requires"]),
                               "payload": j["payload"] if include_payload else None})
        return cache[i]
    return digest({"nodes": sorted(signature(i) for i in by_id),
                   "goals": sorted(signature(i) for i in task["goals"]),
                   **({"resources": task["resources"]} if include_payload and "resources" in task else {}),
                   **({"pools": task["pools"]} if include_payload and "pools" in task else {})})


def generate(seed, source_id, split, profile="basic-v1"):
    if profile in ("allocation-v1", "allocation-precision-v1"):
        from .allocation import generate_allocation
        return generate_allocation(seed, source_id, split, precision=10 if profile == "allocation-precision-v1" else 1)
    if profile == "capacity-v1":
        from .capacity import generate_capacity
        return generate_capacity(seed, source_id, split)
    if profile not in ("basic-v1", "planning-v2", "recovery-v1"):
        raise ValueError("unknown task profile")
    rng = random.Random(seed)
    n = rng.randint(26, 30) if profile == "planning-v2" else rng.randint(8, 16)
    parents = [[], [0], [1]]
    for i in range(3, n):
        parents.append(sorted(rng.sample(range(i), rng.randint(1, min(3, i)))))
    # All goals depend on a path of at least three nodes. Leave a distractor.
    parents[n - 2] = sorted(set(parents[n - 2] + [2]))
    if profile == "planning-v2":
        # A long shared prerequisite chain plus cross-branch edges, three goals,
        # and a distractor. A correct traversal needs <= 2*(n-1)+2 calls.
        for i in range(1, n - 1):
            parents[i] = sorted(set(parents[i] + [i - 1]))
    ids_rng = random.Random(seed ^ 0xA57C9)
    ids = []
    while len(ids) < n:
        value = "r_" + f"{ids_rng.getrandbits(32):08x}"
        if value not in ids:
            ids.append(value)
    jobs = [{"id": ids[i], "requires": [ids[p] for p in parents[i]],
             "payload": {"kind": rng.choice(["prepare", "build", "verify", "publish"]),
                         "requires_check": bool(rng.getrandbits(1))}} for i in range(n)]
    if profile == "recovery-v1":
        for job in jobs:
            job["payload"]["fault"] = "stable"
        # Both jobs lie on the guaranteed path to the main goal. Faults are
        # fixed before any model execution and independent of the bundle.
        jobs[1]["payload"].update(fault="busy_once", requires_check=True)
        jobs[2]["payload"].update(fault="lost_ack_once", requires_check=True)
    rng.shuffle(jobs)
    task = {"id": source_id, "jobs": jobs, "goals": [ids[n - 2]] if profile != "planning-v2" else [ids[n - 4], ids[n - 3], ids[n - 2]]}
    validate(task)
    return envelope(task, source_id, split=split, seed=seed,
                    template_id=semantic_identity(task, False), semantic_hash=semantic_identity(task))


def plan_tasks(seed, development, units, heldout, forbidden_templates=(), profile="basic-v1"):
    seen = set(forbidden_templates)
    result = {"development": [], "validation": [], "sealed_evaluation": []}
    index = 0
    for split, count in [("development", development), ("validation", units), ("sealed_evaluation", units * heldout)]:
        while len(result[split]) < count:
            task = generate(seed + index, f"root-{seed}-{index}", split, profile)
            index += 1
            if task["template_id"] in seen:
                continue
            seen.add(task["template_id"])
            result[split].append(task)
    return result


def rename(task, seed):
    rng = random.Random(seed)
    old = {j["id"] for j in task["content"]["jobs"]}
    new = set()
    mapping = {}
    for j in task["content"]["jobs"]:
        while True:
            candidate = "r_" + f"{rng.getrandbits(32):08x}"
            if candidate not in old | new:
                break
        new.add(candidate)
        mapping[j["id"]] = candidate
    content, mapping = probe.rename_task(task["content"], mapping, renamed_task_id=task["content"]["id"] + "-renamed")
    probe.verify_renaming(task["content"], content, mapping)
    return envelope(content, task["source_id"]), mapping
