"""
GlassBox — resilience.

Two defensive layers that have nothing to do with whether a trade is a good
idea, and everything to do with whether the *mechanics* of placing it are
sound: never firing the same order twice, and never trusting a local clock
that has quietly drifted from Binance's.

Idempotency
-----------
A network hiccup between GlassBox and Binance's MCP server is not rare over a
long-running session — it is a when, not an if. If a response is lost after
Binance already filled the order, a naive retry files a second, unwanted
order. This module generates a **deterministic client order id** for every
order GlassBox sends, derived from the exact contents of the decision that
authorised it, and refuses to send the same id twice — returning the cached
outcome instead of re-submitting.

The deterministic id is bound to the ledger's justification hash specifically,
not just to symbol/side/notional. Two genuinely different decisions that
happen to want the same symbol, side and size still get different ids,
because they were justified by different reasoning and reference different
ledger records — this is the audit trail's own hash extended one layer
further, into the order itself, rather than a bolt-on feature borrowed from
elsewhere.

Clock drift
-----------
GlassBox authenticates to Binance's official Agent OS MCP server over OAuth,
not the legacy per-request HMAC signature scheme that requires a client's
clock to sit within a `recvWindow` of Binance's server time — so this is not
protecting authentication the way it would for an unofficial API-key
integration. It protects something specific to *this* architecture instead:
the Constitution's time-based rules (blackout windows, post-loss cooldowns)
read the local wall clock directly. A machine whose clock has drifted
meaningfully from real time would silently misapply those rules — a blackout
window checked five minutes early or late is a blackout window that isn't
actually protecting the moment it was configured for. Cheap to check, and
surfaced as an honest health signal rather than assumed away.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


def deterministic_client_order_id(
    symbol: str, side: str, notional_usd: float, justification_hash: str,
    time_bucket_seconds: float = 5.0,
) -> str:
    """
    A stable id for "this exact decision," safe to send as Binance's
    `newClientOrderId`.

    Binance limits `newClientOrderId` to 36 characters, so the id is a
    truncated hash rather than the full justification hash — collisions
    within that truncation are astronomically unlikely for the volume any
    single operator will generate, and a collision would only ever cause an
    order to be treated as a duplicate of a near-identical one, never
    silently executed as something else.

    The time bucket exists so a *genuinely* new decision — the Council
    re-evaluating the same symbol a minute later with the same size, after
    the market moved and back — gets its own id rather than being
    permanently treated as a duplicate of the first one forever.
    """
    bucket = int(time.time() / time_bucket_seconds)
    payload = f"{symbol}|{side}|{round(notional_usd, 2)}|{justification_hash}|{bucket}"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"gb{digest[:30]}"


@dataclass
class IdempotencyRecord:
    client_order_id: str
    result: dict[str, Any]
    created_ts: float = field(default_factory=time.time)


class IdempotencyGuard:
    """
    Remembers every client order id GlassBox has generated in this session,
    so an identical decision — from a retry, a duplicate confirmation click,
    or a re-run tick that produced the same intent before the first one's
    result was recorded — returns the original outcome instead of firing
    twice.
    """

    def __init__(self, ttl_seconds: float = 3600.0):
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, IdempotencyRecord] = {}

    def _evict_expired(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [k for k, v in self._records.items() if v.created_ts < cutoff]
        for k in expired:
            del self._records[k]

    def seen(self, client_order_id: str) -> dict[str, Any] | None:
        """Returns the cached result if this id was already submitted, else None."""
        self._evict_expired()
        rec = self._records.get(client_order_id)
        return rec.result if rec else None

    def record(self, client_order_id: str, result: dict[str, Any]) -> None:
        self._records[client_order_id] = IdempotencyRecord(client_order_id, result)

    def status(self) -> dict[str, Any]:
        self._evict_expired()
        return {
            "tracked_orders": len(self._records),
            "ttl_seconds": self.ttl_seconds,
            "policy": (
                "Every order carries a client id derived from the exact "
                "decision that authorised it. Resubmitting the same id "
                "returns the original result rather than firing again."
            ),
        }


@dataclass
class ClockDriftReport:
    drift_ms: float
    checked_ts: float
    healthy: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_ms": round(self.drift_ms, 1),
            "checked_ts": self.checked_ts,
            "healthy": self.healthy,
            "note": self.note,
        }


class ClockDriftMonitor:
    """Checks this machine's clock against Binance's own server time."""

    # A local clock drifting by more than this many seconds is treated as
    # unhealthy — wide enough that ordinary NTP jitter never trips it, tight
    # enough to catch a machine whose clock is genuinely wrong.
    WARN_THRESHOLD_MS = 3000.0

    def __init__(self, feeds):
        self.feeds = feeds
        self.last: ClockDriftReport | None = None

    async def check(self) -> ClockDriftReport:
        before = time.time()
        try:
            data = await self.feeds._fetch("/api/v3/time")
        except Exception as exc:
            report = ClockDriftReport(
                drift_ms=0.0, checked_ts=time.time(), healthy=False,
                note=f"Could not reach Binance to check clock drift: {exc}",
            )
            self.last = report
            return report

        after = time.time()
        server_time_s = float(data.get("serverTime", 0)) / 1000
        # Compare against the midpoint of the round trip, which cancels out
        # most of the network latency itself rather than mistaking it for
        # clock drift.
        local_mid = (before + after) / 2
        drift_ms = (local_mid - server_time_s) * 1000
        healthy = abs(drift_ms) < self.WARN_THRESHOLD_MS

        report = ClockDriftReport(
            drift_ms=drift_ms, checked_ts=time.time(), healthy=healthy,
            note=(
                "Clock is in sync with Binance." if healthy else
                f"Local clock differs from Binance's by {drift_ms:+.0f}ms — "
                f"time-based Constitution rules (blackout windows, cooldowns) "
                f"may fire at the wrong real-world moment. Sync this machine's "
                f"clock (most OSes do this automatically via NTP)."
            ),
        )
        self.last = report
        return report

    def status(self) -> dict[str, Any]:
        return self.last.to_dict() if self.last else {
            "drift_ms": None, "healthy": None, "note": "Not yet checked.",
        }
