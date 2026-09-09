"""
GlassBox — agent base class.

Every analyst is a small, testable unit that turns market state into a Signal
with an explicit confidence and a rationale a human can read. Analysts are
deliberately *not* given the ability to place orders. They can only argue.

The heuristics here are real, if simple: EMA cross and RSI for technical, funding
skew for positioning, volume-weighted flow imbalance for on-chain. They run with
no API key, no model, and no network, which is why the demo works on a conference
wifi that has decided today is not the day.

When ANTHROPIC_API_KEY is present the analyst additionally asks a model to
critique its own heuristic conclusion, and the critique is attached to the
Signal. The heuristic remains the decision of record; the model can lower
confidence but never invent a position out of nothing. That asymmetry is
deliberate — it is the difference between a model that assists and a model that
is in charge.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..config import Signal
from ..x402 import BudgetExceeded, X402Meter


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(len(values) - period, len(values)):
        delta = values[i] - values[i - 1]
        (gains if delta >= 0 else losses).append(abs(delta))
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss < 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def zscore(values: list[float]) -> float:
    if len(values) < 8:
        return 0.0
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    return (values[-1] - mean) / sd if sd > 1e-12 else 0.0


class Analyst:
    """One voice on the Council."""

    name: str = "analyst"
    description: str = ""
    data_provider: str | None = None
    data_cost_usd: float = 0.0

    # A paid feed is re-purchased at most this often. Buying the same hourly
    # sentiment snapshot on every three-second tick is how an agent burns a
    # daily data budget before lunch, so freshness is bounded deliberately.
    data_ttl_seconds: float = 300.0

    def __init__(self, meter: X402Meter | None = None):
        self.meter = meter
        self.last_error: str | None = None
        self._data_cache: dict[str, float] = {}

    def buy_data(self, resource: str) -> float:
        """
        Charge the x402 meter for an external feed. Returns the cost, or 0.0 if
        the budget said no — in which case the analyst degrades to free data and
        says so in its rationale, rather than failing the whole tick.
        """
        if not (self.meter and self.data_provider and self.data_cost_usd > 0):
            return 0.0

        import time as _time

        last = self._data_cache.get(resource)
        if last is not None and (_time.time() - last) < self.data_ttl_seconds:
            return 0.0  # still fresh; no charge

        try:
            p = self.meter.purchase(
                provider=self.data_provider,
                resource=resource,
                cost_usd=self.data_cost_usd,
                requested_by=self.name,
            )
            self._data_cache[resource] = _time.time()
            return p.cost_usd
        except BudgetExceeded as exc:
            self.last_error = str(exc)
            return 0.0

    def analyse(self, symbol: str, market: Any) -> Signal:
        raise NotImplementedError

    def _signal(
        self,
        symbol: str,
        stance: str,
        confidence: float,
        rationale: str,
        evidence: dict | None = None,
        cost: float = 0.0,
    ) -> Signal:
        if self.last_error:
            rationale += f" (Degraded: {self.last_error})"
            self.last_error = None
        return Signal(
            agent=self.name,
            symbol=symbol,
            stance=stance,  # type: ignore[arg-type]
            confidence=max(0.0, min(1.0, confidence)),
            rationale=rationale,
            evidence=evidence or {},
            cost_usd=cost,
        )
