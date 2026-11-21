"""Append-only, hash-chained JSONL audit log.

Each line is {"entry", "prev", "hash"}; hash = sha256 of canonical JSON (sorted keys) of {"entry", "prev"},
and prev is the hash of the line before ("0"*64 for the first line).

The chain proves the order and integrity of the log, not that what was written is true.
It detects an edited, removed or reordered line inside the log. It cannot detect a cut-off tail or a
complete rewrite on its own: for that, keep the last hash somewhere else and compare.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

GENESIS = "0" * 64


def canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(entry: dict, prev: str) -> str:
    return hashlib.sha256(canonical({"entry": entry, "prev": prev}).encode("utf-8")).hexdigest()


def _check(text: str) -> tuple[bool, str, str]:
    """(ok, message, last hash) for the log text."""
    prev = GENESIS
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        try:
            rec = json.loads(line)
            entry, rprev, rhash = rec["entry"], rec["prev"], rec["hash"]
        except (ValueError, KeyError, TypeError):
            return False, f"line {n}: not a valid audit record", prev
        if rprev != prev:
            return False, f"line {n}: previous hash does not match line {n - 1} (removed or reordered line)", prev
        if _hash(entry, rprev) != rhash:
            return False, f"line {n}: hash does not match its content (edited line)", prev
        prev = rhash
    return True, f"{len(lines)} entries, chain intact; last hash {prev}", prev


def append(path, entry: dict) -> str:
    """Append one entry and return its hash. Refuses to extend a log that does not verify."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical(entry)  # fail before writing if the entry is not JSON
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0)
        ok, msg, prev = _check(fh.read())
        if not ok:
            raise ValueError(f"audit log {path} does not verify: {msg}")
        h = _hash(entry, prev)
        fh.write(canonical({"entry": entry, "prev": prev, "hash": h}) + "\n")
        fh.flush()
    return h


def verify(path) -> tuple[bool, str]:
    """(ok, message). An absent file is an empty, valid log."""
    path = Path(path)
    if not path.exists():
        return True, "0 entries (no log file)"
    ok, msg, _ = _check(path.read_text(encoding="utf-8"))
    return ok, msg
