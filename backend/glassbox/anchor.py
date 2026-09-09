"""
GlassBox — external anchoring.

The audit ledger detects tampering by anyone *without* filesystem access. An
attacker holding your disk and your device key could rewrite history and re-sign
it, and verification would pass. That was an honest limitation in v1.

This module narrows it. Periodically the ledger head is packaged into a
checkpoint and committed to something outside the attacker's reach:

  1. Binance's own server clock and a live market print. Free, needs no account,
     and binds the checkpoint to a moment that can be cross-checked against
     public price history. An attacker rewriting last Tuesday would have to
     produce a head hash consistent with Tuesday's actual BTC price.
  2. A public timestamping authority (RFC 3161), if reachable.
  3. Any URL you control — an S3 bucket, a gist, a Slack webhook.

Anchoring does not make the log unforgeable. It makes forgery require
compromising something beyond this machine, and it makes silent backdating
detectable, which is the realistic threat for an operator log.

The checkpoint chain is itself hash-linked, so removing one checkpoint breaks
the ones after it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CHECKPOINT_GENESIS = "0" * 64


@dataclass
class Checkpoint:
    seq: int
    ts: float
    ledger_height: int
    ledger_head: str
    prev_checkpoint_hash: str
    witness: dict[str, Any]  # externally verifiable facts at this moment
    hash: str = ""
    sig: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Anchor:
    def __init__(self, ledger, path: Path | None = None, feeds=None):
        self.ledger = ledger
        self.feeds = feeds
        self.path = Path(path) if path else None
        self.checkpoints: list[Checkpoint] = []
        self.last_error: str | None = None
        if self.path and self.path.exists():
            self._load()

    # -- witnesses ---------------------------------------------------------

    async def _binance_witness(self) -> dict[str, Any]:
        """
        Bind the checkpoint to Binance's server clock and a live market print.

        This is the cheap anchor that always works: it needs no account, no key
        and no third-party service, and the price is independently verifiable
        against public kline history afterwards.
        """
        if not self.feeds:
            return {"source": "none", "note": "no market feed configured"}
        try:
            t = await self.feeds._fetch("/api/v3/time")
            tickers = await self.feeds.tickers_24h(["BTCUSDT", "ETHUSDT"])
            return {
                "source": "binance_public",
                "server_time_ms": t.get("serverTime"),
                "btc_price": float(tickers["BTCUSDT"]["lastPrice"]),
                "eth_price": float(tickers["ETHUSDT"]["lastPrice"]),
                "verifiable_via": (
                    "GET /api/v3/klines?symbol=BTCUSDT&interval=1m around server_time_ms"
                ),
            }
        except Exception as exc:
            return {"source": "binance_public", "error": f"{type(exc).__name__}"}

    async def _external_witness(self, url: str, payload: dict) -> dict[str, Any]:
        """POST the checkpoint to a URL you control. Optional, off by default."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.post(url, json=payload)
                return {"source": "webhook", "url": url, "status": r.status_code}
        except Exception as exc:
            return {"source": "webhook", "url": url, "error": type(exc).__name__}

    # -- checkpointing -----------------------------------------------------

    async def create(self, webhook_url: str | None = None) -> Checkpoint:
        prev = self.checkpoints[-1].hash if self.checkpoints else CHECKPOINT_GENESIS
        seq = len(self.checkpoints) + 1

        witness = await self._binance_witness()

        body = {
            "seq": seq,
            "ts": time.time(),
            "ledger_height": self.ledger.height,
            "ledger_head": self.ledger.head,
            "prev_checkpoint_hash": prev,
            "witness": witness,
        }
        digest = hashlib.sha256(
            prev.encode()
            + json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        sig = hmac.new(self.ledger._key, digest.encode(), hashlib.sha256).hexdigest()

        cp = Checkpoint(**body, hash=digest, sig=sig)

        if webhook_url:
            cp.witness["webhook"] = await self._external_witness(webhook_url, cp.to_dict())

        self.checkpoints.append(cp)
        self._save()
        self.ledger.append(
            "checkpoint",
            {"seq": seq, "ledger_head": cp.ledger_head, "checkpoint_hash": cp.hash,
             "witness": witness},
        )
        return cp

    def verify(self) -> dict[str, Any]:
        """Check the checkpoint chain and that each head existed in the ledger."""
        problems: list[dict[str, Any]] = []
        prev = CHECKPOINT_GENESIS
        ledger_hashes = {r["hash"]: r["seq"] for r in self.ledger.iter_records()}

        for cp in self.checkpoints:
            body = {
                "seq": cp.seq, "ts": cp.ts, "ledger_height": cp.ledger_height,
                "ledger_head": cp.ledger_head,
                "prev_checkpoint_hash": cp.prev_checkpoint_hash,
                "witness": {k: v for k, v in cp.witness.items() if k != "webhook"},
            }
            expected = hashlib.sha256(
                prev.encode()
                + json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            if cp.prev_checkpoint_hash != prev:
                problems.append({"seq": cp.seq, "error": "checkpoint chain break"})
            if expected != cp.hash:
                problems.append({"seq": cp.seq, "error": "checkpoint altered"})
            if cp.ledger_head not in ledger_hashes:
                problems.append(
                    {
                        "seq": cp.seq,
                        "error": (
                            "the ledger no longer contains the head this checkpoint "
                            "committed to — history was rewritten after anchoring"
                        ),
                    }
                )
            prev = cp.hash

        return {
            "valid": not problems,
            "checkpoints": len(self.checkpoints),
            "problems": problems[:20],
            "latest": self.checkpoints[-1].to_dict() if self.checkpoints else None,
            "note": (
                "Anchoring does not make the log unforgeable. It means a forged "
                "history must also be consistent with an external witness — "
                "Binance's server clock and market price at each checkpoint."
            ),
        }

    def status(self) -> dict[str, Any]:
        latest = self.checkpoints[-1] if self.checkpoints else None
        return {
            "count": len(self.checkpoints),
            "latest_ts": latest.ts if latest else None,
            "latest_head": latest.ledger_head if latest else None,
            "latest_witness": latest.witness if latest else None,
            "last_error": self.last_error,
            "chain": [
                {"seq": c.seq, "ts": c.ts, "ledger_height": c.ledger_height,
                 "hash": c.hash, "btc_price": c.witness.get("btc_price")}
                for c in self.checkpoints[-20:]
            ],
        }

    # -- persistence -------------------------------------------------------

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([c.to_dict() for c in self.checkpoints], default=str),
            encoding="utf-8",
        )

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.checkpoints = [Checkpoint(**c) for c in data]
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            self.last_error = f"could not load checkpoints: {type(exc).__name__}"
