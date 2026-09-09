"""
GlassBox — market context.

One object holding everything the analysts need for a tick, gathered
concurrently so a five-symbol scan is one round trip rather than twenty.

Where a feed fails, the corresponding slot carries an `error` rather than a
substitute value, and the analyst that needs it abstains. Partial data is
normal — Binance geo-restricts some hosts, futures endpoints have no public
mirror, and a rate limit can drop one call out of a batch. The system is
designed to keep running informatively rather than pretend it is fully sighted.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .feeds import BinanceFeeds, FeedUnavailable


@dataclass
class MarketContext:
    ts: float
    symbols: list[str]
    tickers: dict[str, dict] = field(default_factory=dict)
    books: dict[str, dict] = field(default_factory=dict)
    klines: dict[str, list[dict]] = field(default_factory=dict)
    depth: dict[str, dict] = field(default_factory=dict)
    trades: dict[str, list[dict]] = field(default_factory=dict)
    derivatives: dict[str, dict] = field(default_factory=dict)
    intended_notional_usd: float = 1000.0
    errors: list[str] = field(default_factory=list)

    def price(self, symbol: str) -> float:
        t = self.tickers.get(symbol)
        if t:
            return float(t.get("lastPrice", 0) or 0)
        k = self.klines.get(symbol)
        return k[-1]["close"] if k else 0.0

    def coverage(self) -> dict[str, Any]:
        """What fraction of the intended data actually arrived, for the UI."""
        n = max(len(self.symbols), 1)
        return {
            "tickers": round(len(self.tickers) / n, 2),
            "klines": round(len(self.klines) / n, 2),
            "depth": round(len(self.depth) / n, 2),
            "trades": round(len(self.trades) / n, 2),
            "derivatives": round(
                sum(1 for d in self.derivatives.values() if not d.get("error")) / n, 2
            ),
            "errors": self.errors[:5],
        }


class MarketDataService:
    """Builds a MarketContext per tick from live Binance endpoints."""

    def __init__(self, feeds: BinanceFeeds, interval: str = "5m", kline_limit: int = 200):
        self.feeds = feeds
        self.interval = interval
        self.kline_limit = kline_limit
        self._kline_cache: dict[str, tuple[float, list[dict]]] = {}

    async def _klines_cached(self, symbol: str) -> list[dict] | None:
        """
        Candles change once per bar, not once per tick. Re-fetching 200 candles
        every three seconds is pure rate-limit burn for data that has not moved.
        """
        hit = self._kline_cache.get(symbol)
        bar_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}.get(self.interval, 300)
        if hit and (time.time() - hit[0]) < min(bar_seconds / 4, 60):
            return hit[1]
        try:
            k = await self.feeds.klines(symbol, self.interval, self.kline_limit)
            self._kline_cache[symbol] = (time.time(), k)
            return k
        except FeedUnavailable:
            return hit[1] if hit else None

    async def _derivatives(self, symbol: str) -> dict:
        """Futures data. Absent in some regions; the error travels with the slot."""
        out: dict[str, Any] = {}
        try:
            f = await self.feeds.funding(symbol)
            out.update(
                {"funding_bps": f["funding_bps"], "mark_price": f["mark_price"],
                 "next_funding_ms": f["next_funding_ms"]}
            )
        except FeedUnavailable as exc:
            return {"error": str(exc)[:120]}

        try:
            oi = await self.feeds.open_interest_hist(symbol, "5m", 12)
            if len(oi) >= 2 and oi[0]["oi"] > 0:
                out["oi_change_pct"] = (oi[-1]["oi"] / oi[0]["oi"] - 1) * 100
                out["open_interest"] = oi[-1]["oi"]
        except FeedUnavailable:
            pass

        try:
            ls = await self.feeds.long_short_ratio(symbol, "5m", 6)
            if ls:
                out["long_short_ratio"] = ls[-1]["ratio"]
                out["long_pct"] = ls[-1]["long_pct"]
        except FeedUnavailable:
            pass

        try:
            tf = await self.feeds.taker_flow(symbol, "5m", 6)
            if tf:
                out["taker_buy_sell_ratio"] = tf[-1]["buy_sell_ratio"]
        except FeedUnavailable:
            pass

        return out

    async def build(
        self, symbols: list[str], intended_notional_usd: float = 1000.0,
        want_derivatives: bool = True,
    ) -> MarketContext:
        ctx = MarketContext(
            ts=time.time(), symbols=list(symbols),
            intended_notional_usd=intended_notional_usd,
        )

        # BTC is always fetched even if untraded: the regime analyst needs it as
        # the correlation reference.
        kline_symbols = list(dict.fromkeys(symbols + ["BTCUSDT"]))

        async def safe(coro, label):
            try:
                return await coro
            except FeedUnavailable as exc:
                ctx.errors.append(f"{label}: {str(exc)[:80]}")
                return None
            except Exception as exc:
                ctx.errors.append(f"{label}: {type(exc).__name__}")
                return None

        tick_task = safe(self.feeds.tickers_24h(symbols), "tickers")
        book_task = safe(self.feeds.book_tickers(symbols), "bookTicker")
        kline_tasks = [self._klines_cached(s) for s in kline_symbols]
        depth_tasks = [safe(self.feeds.depth(s, 100), f"depth {s}") for s in symbols]
        trade_tasks = [safe(self.feeds.agg_trades(s, 400), f"trades {s}") for s in symbols]
        deriv_tasks = (
            [safe(self._derivatives(s), f"derivatives {s}") for s in symbols]
            if want_derivatives else []
        )

        results = await asyncio.gather(
            tick_task, book_task,
            *kline_tasks, *depth_tasks, *trade_tasks, *deriv_tasks,
            return_exceptions=True,
        )

        i = 0
        tickers, books = results[0], results[1]
        i = 2
        if isinstance(tickers, dict):
            ctx.tickers = tickers
        if isinstance(books, dict):
            ctx.books = books

        for s in kline_symbols:
            r = results[i]; i += 1
            if isinstance(r, list) and r:
                ctx.klines[s] = r
        for s in symbols:
            r = results[i]; i += 1
            if isinstance(r, dict) and r.get("bids"):
                ctx.depth[s] = r
        for s in symbols:
            r = results[i]; i += 1
            if isinstance(r, list) and r:
                ctx.trades[s] = r
        if want_derivatives:
            for s in symbols:
                r = results[i]; i += 1
                ctx.derivatives[s] = r if isinstance(r, dict) else {"error": "fetch failed"}

                # Price change gives the derivatives analyst the context to tell
                # new shorts from capitulation.
                k = ctx.klines.get(s)
                if k and len(k) > 12 and isinstance(ctx.derivatives[s], dict):
                    ctx.derivatives[s].setdefault(
                        "price_change_pct", (k[-1]["close"] / k[-13]["close"] - 1) * 100
                    )

        return ctx
