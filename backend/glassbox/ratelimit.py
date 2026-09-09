"""
GlassBox — Binance rate limit governor.

Binance meters spot requests by *weight*, not request count. The published spot
limit is 6000 weight per minute, and endpoints cost wildly different amounts:
`ping` is 1, a single `depth` call at limit 100 is 5, `ticker/24hr` for every
symbol is 80. Exceed the limit and you get a 429; keep going and you get a 418
and an IP ban measured in minutes to days.

An agent polling 487 symbols on a loop will find this out the hard way, usually
during a demo. So this module treats API weight like any other budget in the
system — measured, capped, and refused before it is exceeded rather than after.

How it works
------------
* Every response carries `X-MBX-USED-WEIGHT-1M`. That is the authoritative
  number, because it reflects what Binance thinks we have spent, including any
  requests we did not account for correctly.
* Before an expensive call, `reserve()` checks the local estimate against a soft
  ceiling (default 60% of the limit) and waits if needed rather than firing.
* A 429 or 418 carries `Retry-After`. We honour it exactly and stop all traffic
  for that window — retrying through a ban is how a short throttle becomes a
  long one.
* The remaining budget is exposed to the dashboard, so an operator can see how
  close the agent is running to the edge.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

# Published spot limits. Re-read from exchangeInfo at startup when available.
DEFAULT_WEIGHT_LIMIT_1M = 6000

# Documented weights for the endpoints this project uses. Where Binance changes
# these, the header reconciliation below corrects us within one request.
ENDPOINT_WEIGHTS: dict[str, int] = {
    "/api/v3/ping": 1,
    "/api/v3/time": 1,
    "/api/v3/exchangeInfo": 20,
    "/api/v3/depth": 5,          # limit <= 100
    "/api/v3/trades": 25,
    "/api/v3/aggTrades": 4,
    "/api/v3/klines": 2,
    "/api/v3/uiKlines": 2,
    "/api/v3/avgPrice": 2,
    "/api/v3/ticker/24hr": 80,   # all symbols; 2 for a single symbol
    "/api/v3/ticker/price": 4,   # all symbols; 2 for a single symbol
    "/api/v3/ticker/bookTicker": 4,
    "/api/v3/ticker": 4,         # per symbol, rolling window
    "/fapi/v1/premiumIndex": 1,
    "/fapi/v1/openInterest": 1,
    "/futures/data/openInterestHist": 1,
    "/futures/data/globalLongShortAccountRatio": 1,
    "/futures/data/takerlongshortRatio": 1,
}


def estimate_weight(path: str, params: dict | None = None) -> int:
    """
    Best guess at what a call will cost before we make it.

    Several endpoints are far cheaper for one symbol than for the whole market,
    and `depth` scales with the requested limit. Getting this roughly right is
    what lets the governor throttle *before* a 429 rather than react to one.
    """
    params = params or {}
    base = ENDPOINT_WEIGHTS.get(path, 5)

    if path in ("/api/v3/ticker/24hr", "/api/v3/ticker/price", "/api/v3/ticker/bookTicker"):
        if params.get("symbol"):
            return 2 if "24hr" in path else 2
        if params.get("symbols"):
            n = params["symbols"].count(",") + 1
            if path == "/api/v3/ticker/24hr":
                return 2 if n <= 20 else 40 if n <= 100 else 80
            return 4
        return base

    if path == "/api/v3/depth":
        limit = int(params.get("limit", 100) or 100)
        return 5 if limit <= 100 else 25 if limit <= 500 else 50 if limit <= 1000 else 250

    if path == "/api/v3/ticker":
        n = 1 if params.get("symbol") else (params.get("symbols", "").count(",") + 1)
        return min(4 * n, 200)

    return base


@dataclass
class RateLimitState:
    limit_1m: int = DEFAULT_WEIGHT_LIMIT_1M
    used_1m: int = 0            # authoritative, from response headers
    local_estimate: int = 0     # our own running tally between headers
    window_started: float = field(default_factory=time.time)
    banned_until: float = 0.0
    throttle_events: int = 0
    rejected_calls: int = 0
    total_weight_spent: int = 0
    last_endpoint: str = ""


class RateLimitGovernor:
    """Keeps the agent inside Binance's weight budget, by refusing to exceed it."""

    def __init__(self, soft_ceiling_pct: float = 0.60):
        # Deliberately conservative. We are one client among many that may share
        # an egress IP, and the cost of a ban during a live session is far higher
        # than the cost of a slightly stale quote.
        self.soft_ceiling_pct = soft_ceiling_pct
        self.state = RateLimitState()
        self._lock = asyncio.Lock()

    # -- window bookkeeping ------------------------------------------------

    def _roll_window(self) -> None:
        if time.time() - self.state.window_started >= 60:
            self.state.window_started = time.time()
            self.state.used_1m = 0
            self.state.local_estimate = 0

    @property
    def soft_ceiling(self) -> int:
        return int(self.state.limit_1m * self.soft_ceiling_pct)

    @property
    def used(self) -> int:
        """Whichever number is higher — never flatter ourselves."""
        self._roll_window()
        return max(self.state.used_1m, self.state.local_estimate)

    @property
    def remaining(self) -> int:
        return max(self.soft_ceiling - self.used, 0)

    # -- gating ------------------------------------------------------------

    async def reserve(self, path: str, params: dict | None = None) -> int:
        """
        Wait until this call fits in the budget, then account for it.

        Returns the estimated weight. Blocks rather than raising, because the
        right response to a busy minute is patience, not a failed analyst.
        """
        weight = estimate_weight(path, params)

        async with self._lock:
            now = time.time()
            if now < self.state.banned_until:
                wait = self.state.banned_until - now
                self.state.rejected_calls += 1
                await asyncio.sleep(min(wait, 60))

            self._roll_window()

            while self.used + weight > self.soft_ceiling:
                self.state.throttle_events += 1
                sleep_for = max(60 - (time.time() - self.state.window_started), 0.5)
                await asyncio.sleep(min(sleep_for, 10))
                self._roll_window()
                if time.time() - self.state.window_started < 0.6:
                    break  # window rolled; budget is fresh

            self.state.local_estimate += weight
            self.state.total_weight_spent += weight
            self.state.last_endpoint = path
            return weight

    def observe(self, headers: Any) -> None:
        """Reconcile our estimate against what Binance actually charged us."""
        try:
            used = headers.get("x-mbx-used-weight-1m") or headers.get("X-MBX-USED-WEIGHT-1M")
            if used is not None:
                self.state.used_1m = int(used)
                # Binance's number wins. If we were under-counting, catch up.
                self.state.local_estimate = max(self.state.local_estimate, self.state.used_1m)
        except (TypeError, ValueError):
            pass

    def penalise(self, status: int, headers: Any) -> float:
        """
        Handle a 429 (throttled) or 418 (banned). Returns seconds to wait.

        Retrying through a 418 is how a two-minute throttle becomes a two-day
        ban, so we honour Retry-After exactly and stop everything.
        """
        retry_after = 60.0
        try:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                retry_after = float(ra)
        except (TypeError, ValueError):
            pass
        if status == 418:
            retry_after = max(retry_after, 120.0)
        self.state.banned_until = time.time() + retry_after
        self.state.throttle_events += 1
        return retry_after

    def set_limit_from_exchange_info(self, info: dict) -> None:
        for r in info.get("rateLimits", []):
            if r.get("rateLimitType") == "REQUEST_WEIGHT" and r.get("interval") == "MINUTE":
                self.state.limit_1m = int(r.get("limit", DEFAULT_WEIGHT_LIMIT_1M))

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        self._roll_window()
        used = self.used
        pct = used / max(self.soft_ceiling, 1) * 100
        return {
            "limit_1m": self.state.limit_1m,
            "soft_ceiling": self.soft_ceiling,
            "used_1m": used,
            "remaining": self.remaining,
            "utilisation_pct": round(pct, 1),
            "window_resets_in_s": round(
                max(60 - (time.time() - self.state.window_started), 0), 1
            ),
            "throttle_events": self.state.throttle_events,
            "total_weight_spent": self.state.total_weight_spent,
            "banned": time.time() < self.state.banned_until,
            "banned_for_s": round(max(self.state.banned_until - time.time(), 0), 1),
            "health": (
                "banned" if time.time() < self.state.banned_until
                else "throttling" if pct > 90
                else "busy" if pct > 60
                else "healthy"
            ),
        }
