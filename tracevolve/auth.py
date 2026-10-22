"""File-backed authentication lease, outside study evidence and Git.

Serialize TraceVolve users of an account. Preserve rotated credentials after
normal exit, cancellation, and process death; never overwrite a changed login.
Other Codex clients do not honor this advisory lock. Concurrent changes produce
a durable conflict requiring reconciliation, rather than losing either login.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _valid(data):
    try:
        value = json.loads(data)
        return value.get("auth_mode") == "chatgpt" and bool(value.get("tokens"))
    except (ValueError, AttributeError):
        return False


def _atomic(path, data):
    fd, name = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _reconcile(source, active):
    baseline = active / "baseline.sha256"
    copied = active / "auth.json"
    # An interrupted preparation never launched Codex.
    if not baseline.exists() or not copied.exists():
        shutil.rmtree(active)
        return
    updated = copied.read_bytes()
    initial_hash = baseline.read_text()
    if _hash(updated) != initial_hash:
        if not _valid(updated):
            raise ValueError("AUTH_REFRESH_INVALID: credentials retained in auth home for reconciliation")
        current = source.read_bytes() if source.exists() else b""
        if current != updated:
            if _hash(current) != initial_hash:
                raise ValueError("AUTH_REFRESH_CONFLICT: credentials retained in auth home; reconcile before retry")
            _atomic(source, updated)
    shutil.rmtree(active)


@contextmanager
def isolated_auth(auth_home):
    source = Path(auth_home).expanduser().resolve() / "auth.json"
    private = source.parent / ".tracevolve-auth"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    fd = os.open(private / "lease.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("AUTH_IN_USE: another TraceVolve session holds this login") from None
        active = private / "active"
        if active.exists():
            _reconcile(source, active)
        original = source.read_bytes()
        if not _valid(original):
            raise ValueError("ChatGPT subscription login required")
        active.mkdir(mode=0o700)
        _atomic(active / "baseline.sha256", _hash(original).encode())
        _atomic(active / "auth.json", original)
        try:
            yield active
        finally:
            _reconcile(source, active)
