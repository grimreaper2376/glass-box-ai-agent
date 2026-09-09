"""
GlassBox — x402 metered data spend.

Binance x402 lets an agent pay per request for data and settle autonomously, up
to a documented daily cap. Autonomous spending is the least-watched attack
surface in agentic finance: an agent stuck in a retry loop can burn a budget
overnight and nobody notices until the invoice.

So spending here is treated like trading. Every purchase is metered, budgeted,
attributed to the signal that requested it, and written to the audit ledger. The
dashboard shows cost per decision, which turns "we used x402" into a number the
operator can act on.

In paper and shadow mode this simulates the settlement leg; the accounting,
budget enforcement and ledger trail are real.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Purchase:
    purchase_id: str
    ts: float
    provider: str
    resource: str
    cost_usd: float
    requested_by: str
    settled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "purchase_id": self.purchase_id,
            "ts": self.ts,
            "provider": self.provider,
            "resource": self.resource,
            "cost_usd": round(self.cost_usd, 4),
            "requested_by": self.requested_by,
            "settled": self.settled,
        }


class BudgetExceeded(Exception):
    pass


class X402Meter:
    def __init__(
        self,
        daily_budget_usd: float = 5.0,
        max_per_request_usd: float = 0.25,
        allowed_providers: list[str] | None = None,
    ):
        self.daily_budget_usd = daily_budget_usd
        self.max_per_request_usd = max_per_request_usd
        self.allowed_providers = set(allowed_providers or [])
        self.purchases: list[Purchase] = []
        self._day = time.gmtime().tm_yday

    def _roll(self) -> None:
        today = time.gmtime().tm_yday
        if today != self._day:
            self._day = today
            self.purchases = [p for p in self.purchases if p.ts > time.time() - 86400]

    @property
    def spent_today_usd(self) -> float:
        self._roll()
        cutoff = time.time() - 86400
        return sum(p.cost_usd for p in self.purchases if p.ts >= cutoff)

    @property
    def remaining_usd(self) -> float:
        return max(self.daily_budget_usd - self.spent_today_usd, 0.0)

    def can_afford(self, cost_usd: float) -> bool:
        return cost_usd <= self.max_per_request_usd and cost_usd <= self.remaining_usd

    def purchase(
        self, provider: str, resource: str, cost_usd: float, requested_by: str
    ) -> Purchase:
        """Raises rather than silently degrading, so a budget breach is loud."""
        self._roll()
        if self.allowed_providers and provider not in self.allowed_providers:
            raise BudgetExceeded(f"Provider '{provider}' is not in the allowed list.")
        if cost_usd > self.max_per_request_usd:
            raise BudgetExceeded(
                f"${cost_usd:.2f} exceeds the ${self.max_per_request_usd:.2f} "
                f"per-request limit."
            )
        if cost_usd > self.remaining_usd:
            raise BudgetExceeded(
                f"${cost_usd:.2f} would exceed today's remaining data budget of "
                f"${self.remaining_usd:.2f}."
            )
        p = Purchase(
            purchase_id=f"x402-{uuid.uuid4().hex[:10]}",
            ts=time.time(),
            provider=provider,
            resource=resource,
            cost_usd=cost_usd,
            requested_by=requested_by,
            settled=True,
        )
        self.purchases.append(p)
        return p

    def summary(self) -> dict[str, Any]:
        by_provider: dict[str, float] = {}
        for p in self.purchases:
            by_provider[p.provider] = by_provider.get(p.provider, 0.0) + p.cost_usd
        return {
            "daily_budget_usd": self.daily_budget_usd,
            "spent_today_usd": round(self.spent_today_usd, 4),
            "remaining_usd": round(self.remaining_usd, 4),
            "max_per_request_usd": self.max_per_request_usd,
            "purchase_count": len(self.purchases),
            "by_provider": {k: round(v, 4) for k, v in by_provider.items()},
            "recent": [p.to_dict() for p in self.purchases[-15:]],
        }
