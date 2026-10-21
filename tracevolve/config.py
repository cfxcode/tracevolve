from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .util import read_json


@dataclass(frozen=True)
class Config:
    study_id: str = "smoke"
    phase: str = "p0"
    model: str = "gpt-6-astra"
    reasoning_effort: str = "high"
    expected_codex_version: str = "codex-cli 0.163.0"
    auth_home: str = "~/.codex"
    billing_mode: str = "chatgpt_subscription"
    target_modifications: int = 1
    heldout_tasks_per_modification: int = 3
    max_development_tasks: int = 6
    max_sessions: int = 32
    timeout_seconds: int = 300
    max_tool_calls: int = 64
    seed: int = 104201
    prior_studies: tuple[str, ...] = ()

    def __post_init__(self):
        if self.phase not in ("p0", "p1a") or self.billing_mode != "chatgpt_subscription":
            raise ValueError("only P0/P1a subscription execution is implemented")
        for name in ["target_modifications", "heldout_tasks_per_modification", "max_development_tasks", "max_sessions", "timeout_seconds", "max_tool_calls", "seed"]:
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(name + " must be a positive integer")
        if self.target_modifications > 5:
            raise ValueError("this release is limited to five-candidate pilots")
        if self.phase == "p1a" and self.target_modifications != 1:
            raise ValueError("P1a is a single-candidate round")

    @classmethod
    def load(cls, path):
        data = read_json(Path(path))
        if "prior_studies" in data:
            data["prior_studies"] = tuple(data["prior_studies"])
        return cls(**data)

    def public(self):
        d = asdict(self)
        d.pop("auth_home")
        return d

    def plan(self):
        n, h = self.target_modifications, self.heldout_tasks_per_modification
        required = n * (6 + 2 * h + 3)
        return {"study": self.study_id, "phase": self.phase, "target_candidates": n,
                "validation_executions": 6 * n, "heldout_executions": 2 * h * n,
                "judgment_sessions": 3 * n, "max_development_tasks": self.max_development_tasks,
                "max_proposal_sessions": self.max_development_tasks,
                "max_sessions": self.max_sessions, "minimum_sessions_after_discovery": required,
                "model": self.model, "reasoning_effort": self.reasoning_effort,
                "dollar_budget": None, "billing_mode": self.billing_mode,
                "per_request_tokens_and_provider_attempts": "not observable/controllable through exec JSON",
                "cost_comparison": "not established", "live_network_used": False}
