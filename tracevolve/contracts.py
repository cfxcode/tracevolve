from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Protocol

from .util import canonical, digest, text_hash


BASE_PROMPT = """Complete the user's resource jobs using the available tools. Read the public
job attributes and dependencies, use the required execution mode, complete the
target jobs and their prerequisites, and submit exactly the requested goals.
Handle recoverable tool errors using their feedback. Report honestly if blocked.
Resource identifiers are opaque; their spelling has no ordering or semantic meaning."""


@dataclass(frozen=True)
class Bundle:
    prompt: str = BASE_PROMPT
    skill: str | None = None

    def __post_init__(self):
        if not isinstance(self.prompt, str) or not 1 <= len(self.prompt) <= 12000:
            raise ValueError("prompt must contain 1..12000 characters")
        if self.skill is not None and (not isinstance(self.skill, str) or not 1 <= len(self.skill) <= 12000):
            raise ValueError("skill must contain 1..12000 characters")

    @property
    def hash(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class ExecutionSnapshot:
    bundle_hash: str
    runtime: dict
    tool_contract: dict
    implementation: dict
    limits: dict
    memory_view: tuple[str, ...] = ()
    retrieval_policy: str = "explicit-permit-only-v1"
    injection_policy: str = "mcp-load-bundle-before-action-v1"

    @property
    def hash(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class Patch:
    baseline_hash: str
    candidate: Bundle
    component: str
    hypothesis: str
    source_runs: tuple[str, ...]

    def validate(self, base: Bundle):
        if base.hash != self.baseline_hash or base == self.candidate or not self.source_runs:
            raise ValueError("patch must bind a different candidate and real source runs")
        if self.component == "prompt":
            if self.candidate.skill != base.skill:
                raise ValueError("P0 allows only prompt edits")
        elif self.component == "skill":
            if self.candidate.prompt != base.prompt or not self.candidate.skill:
                raise ValueError("P1a allows only a Skill edit")
        else:
            raise ValueError("unsupported editable component")


class AgentAdapter(Protocol):
    def capabilities(self) -> dict: ...
    def runtime_identity(self) -> dict: ...
    def preflight(self, config) -> dict: ...
    def snapshot(self, bundle, contract, config) -> ExecutionSnapshot: ...
    def execute(self, *, store, logical_id, bundle, task, contract, config, phase) -> dict: ...
    def infer(self, *, store, logical_id, prompt, schema, config, phase) -> dict: ...
    def cancel(self) -> None: ...


class EnvironmentAdapter(Protocol):
    def call(self, name: str, arguments: dict) -> dict: ...
    def evaluate(self) -> dict: ...


class ValidationPlanner(Protocol):
    def required_checks(self, patch: Patch, capabilities: dict) -> list[str]: ...
    def optional_probes(self, patch: Patch, budget: dict) -> list[str]: ...


def frozen_harness(bundle: Bundle, snapshot: ExecutionSnapshot) -> dict:
    content = canonical({"bundle": asdict(bundle), "execution_snapshot": asdict(snapshot)})
    return {"content": content, "sha256": text_hash(content)}
