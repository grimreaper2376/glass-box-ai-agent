"""
GlassBox — the Decision Ledger.

Binance states plainly that it can monitor the orders an agent places but not
the agent's reasoning, which stays inside the user's AI client. That gap is the
reason this file exists.

Every observation, signal, intent, verdict and fill is appended here as a record
that is hashed into a chain and signed with a device-local key. Deleting or
editing any record breaks verification for every record after it, so an operator
(or an auditor, or a regulator) can prove after the fact exactly what the agent
knew and why it acted.

Design notes
------------
* Append-only JSONL. Human readable, greppable, trivially shippable to S3.
* SHA-256 over a canonical JSON encoding, chained: h_n = H(h_{n-1} || body).
* HMAC-SHA256 signature with a key generated on first run, stored 0600.
  This proves records were written by *this* install, not injected later.
* No secrets ever enter the ledger — payloads pass through the redactor first.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from .config import KEY_PATH, LEDGER_PATH, ensure_dirs
from .redact import redact

GENESIS = "0" * 64


def _canonical(obj: Any) -> bytes:
    """Deterministic JSON. Key order and separators must never drift."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def load_or_create_device_key() -> bytes:
    """
    A 32-byte key unique to this installation. Created 0600 on first use.

    This is deliberately not a wallet key and cannot move funds. It exists only
    to sign audit records.
    """
    ensure_dirs()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return bytes.fromhex(KEY_PATH.read_text().strip())
    key = secrets.token_bytes(32)
    KEY_PATH.write_text(key.hex())
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass  # Windows without POSIX perms; ACLs still apply to the user profile
    return key


class Ledger:
    """Append-only, hash-chained, signed record of everything the system did."""

    def __init__(self, path: Path | None = None, key: bytes | None = None):
        self.path = Path(path or LEDGER_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = key or load_or_create_device_key()
        self._lock = threading.Lock()
        self._seq, self._head = self._recover_head()

    # -- internals ---------------------------------------------------------

    def _recover_head(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        seq, head = 0, GENESIS
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                seq = rec["seq"]
                head = rec["hash"]
        return seq, head

    def _sign(self, digest: str) -> str:
        return hmac.new(self._key, digest.encode(), hashlib.sha256).hexdigest()

    # -- public API --------------------------------------------------------

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Write one record. Returns the full record including its chain position.

        `payload` is redacted before hashing, so a secret that leaks into an
        agent's rationale never lands on disk.
        """
        with self._lock:
            seq = self._seq + 1
            body = {
                "seq": seq,
                "ts": time.time(),
                "kind": kind,
                "payload": redact(payload),
                "prev_hash": self._head,
            }
            digest = hashlib.sha256(self._head.encode() + _canonical(body)).hexdigest()
            record = {**body, "hash": digest, "sig": self._sign(digest)}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            self._seq, self._head = seq, digest
            return record

    def read(self, limit: int | None = None, kinds: set[str] | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if kinds and rec["kind"] not in kinds:
                    continue
                out.append(rec)
        return out[-limit:] if limit else out

    def iter_records(self) -> Iterator[dict]:
        if not self.path.exists():
            return iter(())
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def verify(self) -> dict[str, Any]:
        """
        Walk the whole chain and re-derive every hash and signature.

        Returns a report rather than raising, because the dashboard shows this
        to the operator as a live integrity badge.
        """
        prev = GENESIS
        checked = 0
        problems: list[dict[str, Any]] = []

        for rec in self.iter_records():
            checked += 1
            body = {
                "seq": rec["seq"],
                "ts": rec["ts"],
                "kind": rec["kind"],
                "payload": rec["payload"],
                "prev_hash": rec["prev_hash"],
            }
            if rec["prev_hash"] != prev:
                problems.append(
                    {"seq": rec["seq"], "error": "chain break: prev_hash mismatch"}
                )
            expected = hashlib.sha256(prev.encode() + _canonical(body)).hexdigest()
            if expected != rec["hash"]:
                problems.append(
                    {"seq": rec["seq"], "error": "record altered: hash mismatch"}
                )
            if not hmac.compare_digest(self._sign(rec["hash"]), rec.get("sig", "")):
                problems.append(
                    {"seq": rec["seq"], "error": "signature invalid for this device"}
                )
            prev = rec["hash"]

        return {
            "valid": not problems,
            "records_checked": checked,
            "head": prev,
            "problems": problems[:50],
        }

    @property
    def head(self) -> str:
        return self._head

    @property
    def height(self) -> int:
        return self._seq
