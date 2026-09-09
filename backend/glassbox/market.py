"""
GlassBox — market data.

Two sources, one interface:

* LiveMarket  — Binance's public REST endpoints. No key, no auth, no account
                scope. This is the read-only half of Agent OS and it is safe to
                point at production because it cannot touch a balance.
* SimMarket   — a seeded generator with scriptable scenarios. This is what makes
                the Guardian demonstrable: you can summon a 9% flash crash on
                cue and watch the system react, which you cannot do on a live
                book while a judge is watching.

Both emit the same Quote shape, so nothing downstream knows or cares which is
running.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

BINANCE_PUBLIC = "https://api.binance.com"


@dataclass
class Quote:
    symbol: str
    price: float
    bid: float
    ask: float
    change_24h_pct: float
    volume_24h_usd: float
    spread_bps: float
    atr_pct: float
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": round(self.price, 6),
            "bid": round(self.bid, 6),
            "ask": round(self.ask, 6),
            "change_24h_pct": round(self.change_24h_pct, 3),
            "volume_24h_usd": round(self.volume_24h_usd, 0),
            "spread_bps": round(self.spread_bps, 2),
            "atr_pct": round(self.atr_pct, 3),
            "ts": self.ts,
        }


class BaseMarket:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.history: dict[str, deque[float]] = {s: deque(maxlen=240) for s in symbols}
        self._last: dict[str, Quote] = {}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {s: q.to_dict() for s, q in self._last.items()}

    def price(self, symbol: str) -> float:
        q = self._last.get(symbol)
        return q.price if q else 0.0

    def _atr_pct(self, symbol: str) -> float:
        h = self.history[symbol]
        if len(h) < 6:
            return 1.0
        window = list(h)[-30:]
        rets = [
            abs(window[i] - window[i - 1]) / max(window[i - 1], 1e-9)
            for i in range(1, len(window))
        ]
        return sum(rets) / len(rets) * 100 * math.sqrt(24)

    def series(self, symbol: str, n: int = 120) -> list[float]:
        return list(self.history.get(symbol, []))[-n:]


class SimMarket(BaseMarket):
    """
    Geometric random walk with regimes and injectable shocks.

    Seeded, so a demo run is reproducible and a bug is reproducible with it.
    """

    SCENARIOS = {
        "calm": dict(drift=0.00004, vol=0.0016, label="Calm drift"),
        "bull": dict(drift=0.00060, vol=0.0032, label="Trending bull"),
        "bear": dict(drift=-0.00055, vol=0.0038, label="Grinding bear"),
        "chop": dict(drift=0.0, vol=0.0075, label="Violent chop"),
        "crash": dict(drift=-0.00450, vol=0.0150, label="Cascading crash"),
    }

    SEEDS = {"BTCUSDT": 64000.0, "ETHUSDT": 3100.0, "BNBUSDT": 610.0, "SOLUSDT": 148.0}

    def __init__(self, symbols: list[str], seed: int = 7, scenario: str = "calm"):
        super().__init__(symbols)
        self.rng = random.Random(seed)
        self.scenario = scenario
        self._shock: dict[str, float] = {}
        self._t = 0
        for s in symbols:
            base = self.SEEDS.get(s, 100.0)
            self._last[s] = self._make_quote(s, base, 0.0)
            self.history[s].append(base)

    def set_scenario(self, name: str) -> None:
        if name in self.SCENARIOS:
            self.scenario = name

    def inject_shock(self, symbol: str, pct: float) -> None:
        """Apply an instantaneous move, e.g. -9.0 for a 9% gap down."""
        self._shock[symbol] = self._shock.get(symbol, 0.0) + pct / 100.0

    def _make_quote(self, symbol: str, price: float, change: float) -> Quote:
        spread_bps = 1.2 + self.rng.random() * 2.0
        half = price * spread_bps / 20000
        return Quote(
            symbol=symbol,
            price=price,
            bid=price - half,
            ask=price + half,
            change_24h_pct=change,
            volume_24h_usd=self.rng.uniform(3e8, 4e9),
            spread_bps=spread_bps,
            atr_pct=self._atr_pct(symbol),
            ts=time.time(),
        )

    async def poll(self) -> dict[str, Quote]:
        cfg = self.SCENARIOS[self.scenario]
        self._t += 1
        for s in self.symbols:
            prev = self._last[s].price
            drift = cfg["drift"]
            vol = cfg["vol"]
            # BTC leads; alts amplify. Crude but it makes correlation visible.
            beta = {"BTCUSDT": 1.0, "ETHUSDT": 1.15, "BNBUSDT": 1.05, "SOLUSDT": 1.45}
            step = (drift + self.rng.gauss(0, vol)) * beta.get(s, 1.0)
            if s in self._shock:
                step += self._shock.pop(s)
            price = max(prev * (1 + step), 0.01)
            self.history[s].append(price)
            window = self.history[s]
            ref = window[0] if len(window) > 1 else price
            change = (price - ref) / max(ref, 1e-9) * 100
            self._last[s] = self._make_quote(s, price, change)
        return dict(self._last)


class LiveMarket(BaseMarket):
    """Public Binance market data. Read-only by construction — no credentials."""

    def __init__(self, symbols: list[str]):
        super().__init__(symbols)
        self._client = None

    async def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=8.0)
        return self._client

    async def poll(self) -> dict[str, Quote]:
        import httpx

        client = await self._get_client()
        try:
            resp = await client.get(f"{BINANCE_PUBLIC}/api/v3/ticker/bookTicker")
            books = {r["symbol"]: r for r in resp.json()}
            resp2 = await client.get(f"{BINANCE_PUBLIC}/api/v3/ticker/24hr")
            stats = {r["symbol"]: r for r in resp2.json()}
        except (httpx.HTTPError, ValueError):
            return dict(self._last)  # keep last good tick rather than crash the loop

        for s in self.symbols:
            b, st = books.get(s), stats.get(s)
            if not b or not st:
                continue
            bid, ask = float(b["bidPrice"]), float(b["askPrice"])
            price = (bid + ask) / 2
            self.history[s].append(price)
            self._last[s] = Quote(
                symbol=s,
                price=price,
                bid=bid,
                ask=ask,
                change_24h_pct=float(st["priceChangePercent"]),
                volume_24h_usd=float(st["quoteVolume"]),
                spread_bps=(ask - bid) / max(price, 1e-9) * 10000,
                atr_pct=self._atr_pct(s),
                ts=time.time(),
            )
        return dict(self._last)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
