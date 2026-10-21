from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".write-", delete=False) as f:
            temporary = Path(f.name)
            f.write(canonical(value) + "\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path):
    return json.loads(path.read_text())


def append_json(path: Path, value):
    with path.open("a") as f:
        f.write(canonical(value) + "\n")
        f.flush()
        os.fsync(f.fileno())


def source_manifest() -> dict:
    root = Path(__file__).parent
    return {str(p.relative_to(root)): text_hash(p.read_text()) for p in sorted(root.rglob("*.py"))}
