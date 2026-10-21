from __future__ import annotations

import copy
import random

from .legacy import probe
from .util import digest


def tool_contract(version="resource-v1"):
    if version not in ("resource-v1", "resource-v2"):
        raise ValueError("unknown tool contract")
    return {"version": version, "explicit_dependencies": True,
            "query_execution_status": True, "invalid_actions_do_not_mutate": True,
            "checked_mode": "checked" if version == "resource-v1" else "verified",
            "standard_mode": "standard", "family": "resource-dependency-tasks"}


def envelope(content, source_id, **metadata):
    return {"content": content, "sha256": digest(content), "source_id": source_id,
            "identifier_contract": "opaque", **metadata}


def validate(task):
    probe.validate_task(task)
    if not task["jobs"] or not task["goals"]:
        raise ValueError("nonempty jobs and goals required")
    for job in task["jobs"]:
        if set(job) != {"id", "requires", "payload"}:
            raise ValueError("unexpected job fields")
        p = job["payload"]
        if set(p) != {"kind", "requires_check"} or p["kind"] not in ("prepare", "build", "verify", "publish") or type(p["requires_check"]) is not bool:
            raise ValueError("invalid payload")


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

    def call(self, name, args):
        self.calls += 1
        def error(code):
            self.errors += 1
            return {"error": code}
        if self.calls > self.max_calls:
            return error("TOOL_LIMIT")
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
                   "goals": sorted(signature(i) for i in task["goals"])})


def generate(seed, source_id, split):
    rng = random.Random(seed)
    n = rng.randint(8, 16)
    parents = [[], [0], [1]]
    for i in range(3, n):
        parents.append(sorted(rng.sample(range(i), rng.randint(1, min(3, i)))))
    # All goals depend on a path of at least three nodes. Leave a distractor.
    parents[n - 2] = sorted(set(parents[n - 2] + [2]))
    ids_rng = random.Random(seed ^ 0xA57C9)
    ids = []
    while len(ids) < n:
        value = "r_" + f"{ids_rng.getrandbits(32):08x}"
        if value not in ids:
            ids.append(value)
    jobs = [{"id": ids[i], "requires": [ids[p] for p in parents[i]],
             "payload": {"kind": rng.choice(["prepare", "build", "verify", "publish"]),
                         "requires_check": bool(rng.getrandbits(1))}} for i in range(n)]
    rng.shuffle(jobs)
    task = {"id": source_id, "jobs": jobs, "goals": [ids[n - 2]]}
    validate(task)
    return envelope(task, source_id, split=split, seed=seed,
                    template_id=semantic_identity(task, False), semantic_hash=semantic_identity(task))


def plan_tasks(seed, development, units, heldout, forbidden_templates=()):
    seen = set(forbidden_templates)
    result = {"development": [], "validation": [], "sealed_evaluation": []}
    index = 0
    for split, count in [("development", development), ("validation", units), ("sealed_evaluation", units * heldout)]:
        while len(result[split]) < count:
            task = generate(seed + index, f"root-{seed}-{index}", split)
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
