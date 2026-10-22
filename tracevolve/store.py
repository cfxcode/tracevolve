from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .util import canonical, digest, now, read_json, write_json


class Store:
    """SQLite control state and append-only, hash-chained audit events.

    This detects accidental edits; it is not protection against the machine owner.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "objects").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "state.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at TEXT NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT UNIQUE NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN
          SELECT RAISE(ABORT, 'events are append only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN
          SELECT RAISE(ABORT, 'events are append only'); END;
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, phase TEXT NOT NULL,
          started TEXT NOT NULL, status TEXT NOT NULL, request_hash TEXT NOT NULL, result_hash TEXT);
        CREATE TABLE IF NOT EXISTS pointers(scope TEXT PRIMARY KEY, bundle TEXT NOT NULL, generation INTEGER NOT NULL);
        """)

    def close(self):
        self.db.close()

    def put(self, obj) -> str:
        key = digest(obj)
        p = self.root / "objects" / (key + ".json")
        if p.exists():
            if digest(read_json(p)) != key:
                raise ValueError("immutable object corrupted: " + key)
        else:
            write_json(p, obj)
        return key

    def get(self, key):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid object reference")
        obj = read_json(self.root / "objects" / (key + ".json"))
        if digest(obj) != key:
            raise ValueError("object integrity mismatch")
        return obj

    def event(self, kind, payload):
        previous = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = previous[0] if previous else "0" * 64
        row = {"at": now(), "kind": kind, "payload": payload, "previous": previous}
        self.db.execute("INSERT INTO events(at,kind,payload,previous,hash) VALUES(?,?,?,?,?)",
                        (row["at"], kind, canonical(payload), previous, digest(row)))
        return row

    def set(self, key, value):
        self.set_many({key: value})

    def set_many(self, values):
        with self.db:
            for key, value in values.items():
                self.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical(value)))
                self.event("state_changed", {"key": key, "value": value})

    def state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def reserve(self, call_id, phase, request, max_calls):
        """Persist before launch. An uncertain call blocks further launches/resume."""
        request_hash = self.put(request)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise ValueError("frozen request changed")
                if row["status"] != "completed":
                    raise RuntimeError("existing failed/uncertain attempt is never retried automatically")
                self.db.commit()
                return self.get(row["result_hash"])
            if self.db.execute("SELECT 1 FROM calls WHERE status IN ('started','uncertain')").fetchone():
                raise RuntimeError("UNRECONCILED_EXECUTION: inspect outstanding attempt before continuing")
            if self.db.execute("SELECT count(*) FROM calls").fetchone()[0] >= max_calls:
                raise RuntimeError("SESSION_BUDGET_EXHAUSTED")
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,NULL)", (call_id, phase, now(), "started", request_hash))
            self.event("request_started", {"call_id": call_id, "phase": phase, "request_hash": request_hash})
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return None

    def finish(self, call_id, result):
        ref = self.put(result)
        with self.db:
            cursor = self.db.execute("UPDATE calls SET status=?,result_hash=? WHERE id=? AND status='started'",
                                     (result["status"], ref, call_id))
            if cursor.rowcount != 1:
                raise ValueError("attempt already finalized or not reserved")
            self.event("request_finished", {"call_id": call_id, "result_hash": ref, "status": result["status"]})
        return result

    def calls(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM calls ORDER BY rowid")]

    def verify(self):
        previous = "0" * 64
        count = 0
        attempts, states, pointers = {}, {}, {}
        for r in self.db.execute("SELECT * FROM events ORDER BY seq"):
            obj = {"at": r["at"], "kind": r["kind"], "payload": json.loads(r["payload"]), "previous": previous}
            if r["previous"] != previous or digest(obj) != r["hash"]:
                raise ValueError("audit chain corrupted")
            previous = r["hash"]
            count += 1
            payload = obj["payload"]
            if r["kind"] == "request_started":
                call_id = payload["call_id"]
                if call_id in attempts:
                    raise ValueError("duplicate request start in audit")
                attempts[call_id] = {"id": call_id, "phase": payload["phase"],
                    "request_hash": payload["request_hash"], "status": "started", "result_hash": None}
            elif r["kind"] == "request_finished":
                attempt = attempts.get(payload["call_id"])
                if not attempt or attempt["status"] != "started" or payload["status"] not in ("completed", "failed", "uncertain"):
                    raise ValueError("invalid request finish in audit")
                attempt.update(status=payload["status"], result_hash=payload["result_hash"])
            elif r["kind"] == "state_changed":
                states[payload["key"]] = payload["value"]
            elif r["kind"] == "published":
                states["release_decision"] = payload
                states["permit"] = {**states["permit"], "status": "active", "maintainer": payload["maintainer"]}
                parent = payload["parent"]
                pointers[parent["scope"]] = {"scope": parent["scope"], "bundle": states["permit"]["bundle_hash"],
                                             "generation": parent["generation"] + 1}
            elif r["kind"] == "rollback":
                parent = payload["from"]
                pointers[parent["scope"]] = {"scope": parent["scope"], "bundle": payload["to"], "generation": parent["generation"] + 1}
                if "permit" in states:
                    states["permit"] = {**states["permit"], "status": "suspended", "reason": "rollback"}
        actual_states = {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM state")}
        if canonical(actual_states) != canonical(states):
            raise ValueError("state differs from audit events")
        actual_pointers = {r["scope"]: dict(r) for r in self.db.execute("SELECT * FROM pointers")}
        if actual_pointers != pointers:
            raise ValueError("publication pointers differ from audit events")
        calls = self.calls()
        if {c["id"] for c in calls} != set(attempts):
            raise ValueError("call ledger differs from audit events")
        for call in calls:
            if any(call[k] != v for k, v in attempts[call["id"]].items()):
                raise ValueError("call ledger differs from audit events")
        for p in (self.root / "objects").glob("*.json"):
            self.get(p.stem)
        for call in self.calls():
            self.get(call["request_hash"])
            if call["result_hash"]:
                result = self.get(call["result_hash"])
                if result["status"] != call["status"] or result.get("run_id", call["id"]) != call["id"]:
                    raise ValueError("call result differs from ledger")
                from .util import text_hash
                for path, expected in result.get("raw_files", {}).items():
                    resolved = (self.root / path).resolve()
                    if not resolved.is_relative_to(self.root) or text_hash(resolved.read_text()) != expected:
                        raise ValueError("raw evidence modified: " + path)
        return {"events": count, "chain_head": previous, "verified": True}

    def accounting(self):
        calls = self.calls()
        results = [self.get(c["result_hash"]) for c in calls if c["result_hash"]]
        tokens = {}
        for result in results:
            for key, value in (result.get("usage") or {}).items():
                if type(value) is int:
                    tokens[key] = tokens.get(key, 0) + value
        known = [r["cost_usd"] for r in results if r.get("cost_usd") is not None]
        complete = len(known) == len(calls)
        return {"execution_count": len(calls), "tokens_observed": tokens,
                "execution_count_unit": "reserved CLI session attempts, not underlying model HTTP requests",
                "elapsed_seconds": sum(r.get("elapsed_seconds", 0) for r in results),
                "cost_usd": sum(known) if complete else None,
                "known_cost_lower_bound_usd": sum(known), "accounting_complete": complete,
                "budget_compliant": None, "billing_mode": "chatgpt_subscription",
                "usage_coverage": len([r for r in results if r.get("usage") is not None]),
                "uncertain_attempts": [c["id"] for c in calls if c["status"] in ("started", "uncertain")]}
