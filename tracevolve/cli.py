from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from .codex import CodexAdapter
from .config import Config
from .memory import Registry
from .store import Store
from .study import Study
from .util import canonical, read_json, text_hash


def from_store(store):
    ref = store.state("frozen")
    if not ref:
        raise ValueError("study has not been frozen")
    data = store.get(ref)["identity"]["config"]
    data["prior_studies"] = tuple(data.get("prior_studies", []))
    return Config(**data)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracevolve", description="基于执行证据的 Agent 自优化框架")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ["plan", "preflight", "run"]:
        q = sub.add_parser(command)
        q.add_argument("--config", required=True)
        if command == "run":
            q.add_argument("--study", required=True)
    for command in ["resume", "report", "verify", "export", "replay", "publish", "rollback"]:
        q = sub.add_parser(command)
        q.add_argument("--study", required=True)
        if command in ("publish", "rollback"):
            q.add_argument("--expected-generation", type=int, required=True)
        if command == "publish":
            q.add_argument("--maintainer", required=True)
            q.add_argument("--confirm", action="store_true", required=True)
        if command == "rollback":
            q.add_argument("--reason", required=True)
    args = p.parse_args(argv)
    store = None
    try:
        adapter = CodexAdapter()
        if args.command in ("plan", "preflight", "run"):
            config = Config.load(args.config)
            if args.command == "plan":
                result = config.plan()
            elif args.command == "preflight":
                result = {**config.plan(), **adapter.preflight(config)}
            else:
                store = Store(args.study)
                result = Study(store, adapter, config).run()
        else:
            store = Store(args.study)
            config = from_store(store)
            study = Study(store, adapter, config)
            if args.command == "resume":
                result = study.run()
            elif args.command == "report":
                result = study.report()
            elif args.command == "verify":
                result = store.verify()
                for call in store.calls():
                    if call["result_hash"]:
                        record = store.get(call["result_hash"])
                        for path, sha in record.get("raw_files", {}).items():
                            if text_hash((store.root / path).read_text()) != sha:
                                raise ValueError("raw trace modified: " + path)
            elif args.command == "export":
                result = {"path": str(study.export())}
            elif args.command == "replay":
                result = study.replay()
            elif args.command == "publish":
                result = Registry(store).publish(store.state("candidates")[0], expected_generation=args.expected_generation,
                                                 maintainer=args.maintainer)
            else:
                result = Registry(store).rollback(expected_generation=args.expected_generation, reason=args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(canonical({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()
