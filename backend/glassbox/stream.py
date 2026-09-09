"""
GlassBox — real-time market streams.

Polling REST for prices is the expensive, slow and rate-limited way to know what
the market is doing. Binance publishes the same data over websockets for free
and in real time, and one stream — `!miniTicker@arr` — carries a price update
for *every* symbol on the exchange, roughly once a second, at zero REST weight.

That single fact reshapes the data layer:

* the whole 1,362-pair universe stays live without spending any request budget
* watched symbols additionally get book-top, trade and candle streams
* REST is reserved for the things websockets cannot give us — order book
  snapshots, historical candles, and futures positioning

Resilience matters more than throughput here. Binance closes a connection after
24 hours by design, and networks drop. The client reconnects with exponential
backoff and jitter, keeps serving the last known value while disconnected, and
marks data as stale rather than pretending a five-minute-old price is current.
A trading agent acting on a silently frozen feed is the failure this guards
against.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

WS_HOSTS = [
    "wss://data-stream.binance.vision",
    "wss://stream.binance.com:9443",
]

# Anything older than this is not a live price any more.
STALE_AFTER_SECONDS = 20.0


@dataclass
class LiveQuote:
    symbol: str
    price: float
    open_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    volume_24h: float = 0.0
    quote_volume_24h: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    ts: float = 0.0

    @property
    def change_24h_pct(self) -> float:
        return (self.price / self.open_24h - 1) * 100 if self.open_24h > 0 else 0.0

    @property
    def spread_bps(self) -> float:
        if self.bid > 0 and self.ask > 0:
            mid = (self.bid + self.ask) / 2
            return (self.ask - self.bid) / mid * 10000
        return 0.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.ts) > STALE_AFTER_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "change_24h_pct": round(self.change_24h_pct, 3),
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "volume_24h": self.volume_24h,
            "quote_volume_24h": self.quote_volume_24h,
            "bid": self.bid,
            "ask": self.ask,
            "spread_bps": round(self.spread_bps, 3),
            "age_s": round(time.time() - self.ts, 1),
            "stale": self.is_stale,
        }


class MarketStream:
    """
    Maintains a live view of the entire Binance spot market over websockets.

    Two connections at most: one for the all-market ticker, one multiplexed
    stream for whatever symbols the agents are currently watching.
    """

    def __init__(self, on_update: Callable[[str, LiveQuote], Any] | None = None):
        self.quotes: dict[str, LiveQuote] = {}
        self.trades: dict[str, list[dict]] = {}
        self.on_update = on_update
        self.watched: list[str] = []
        self.connected = False
        self.host: str | None = None
        self.reconnects = 0
        self.last_message_ts = 0.0
        self.messages = 0
        self.last_error: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._watch_generation = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self, watched: list[str] | None = None) -> None:
        if self._running:
            return
        self._running = True
        self.watched = [s.upper() for s in (watched or [])]
        self._tasks = [
            asyncio.create_task(self._run_all_market()),
            asyncio.create_task(self._run_watched()),
        ]

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self.connected = False

    async def set_watched(self, symbols: list[str]) -> None:
        """Change the focused symbol set; the watcher task reconnects itself."""
        self.watched = [s.upper() for s in symbols]
        self._watch_generation += 1

    # -- connections -------------------------------------------------------

    async def _connect(self, path: str):
        """Try each host in turn. Returns an open connection or raises."""
        import websockets

        last = None
        for host in WS_HOSTS:
            try:
                ws = await websockets.connect(
                    f"{host}{path}", open_timeout=10, ping_interval=20, ping_timeout=20,
                    max_queue=256,
                )
                self.host = host
                return ws
            except Exception as exc:
                last = f"{host}: {type(exc).__name__}"
                continue
        raise ConnectionError(last or "no websocket host reachable")

    async def _run_all_market(self) -> None:
        """
        One stream, every symbol, ~1s cadence, zero REST weight.

        This is what makes a 1,362-pair live market browser affordable.
        """
        backoff = 1.0
        while self._running:
            try:
                ws = await self._connect("/ws/!miniTicker@arr")
                self.connected = True
                self.last_error = None
                backoff = 1.0
                async for raw in ws:
                    if not self._running:
                        break
                    self.messages += 1
                    self.last_message_ts = time.time()
                    for t in json.loads(raw):
                        self._apply_mini_ticker(t)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.reconnects += 1
                self.last_error = f"{type(exc).__name__}: {str(exc)[:80]}"
                # Jitter matters: without it every client reconnects in lockstep
                # after an outage and hammers the endpoint simultaneously.
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 60.0)

    async def _run_watched(self) -> None:
        """Book-top and trades for the symbols the agents actually care about."""
        backoff = 1.0
        while self._running:
            generation = self._watch_generation
            if not self.watched:
                await asyncio.sleep(1.0)
                continue
            streams = "/".join(
                f"{s.lower()}@bookTicker/{s.lower()}@aggTrade" for s in self.watched[:20]
            )
            try:
                ws = await self._connect(f"/stream?streams={streams}")
                backoff = 1.0
                async for raw in ws:
                    if not self._running or generation != self._watch_generation:
                        await ws.close()
                        break
                    msg = json.loads(raw)
                    stream, data = msg.get("stream", ""), msg.get("data", {})
                    self.last_message_ts = time.time()
                    self.messages += 1
                    if "@bookTicker" in stream:
                        self._apply_book_ticker(data)
                    elif "@aggTrade" in stream:
                        self._apply_trade(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:80]}"
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 60.0)

    # -- message handling --------------------------------------------------

    def _apply_mini_ticker(self, t: dict) -> None:
        sym = t.get("s")
        if not sym:
            return
        q = self.quotes.get(sym) or LiveQuote(symbol=sym, price=0.0)
        q.price = float(t.get("c", q.price) or 0)
        q.open_24h = float(t.get("o", q.open_24h) or 0)
        q.high_24h = float(t.get("h", q.high_24h) or 0)
        q.low_24h = float(t.get("l", q.low_24h) or 0)
        q.volume_24h = float(t.get("v", q.volume_24h) or 0)
        q.quote_volume_24h = float(t.get("q", q.quote_volume_24h) or 0)
        q.ts = time.time()
        self.quotes[sym] = q
        if self.on_update:
            self.on_update(sym, q)

    def _apply_book_ticker(self, d: dict) -> None:
        sym = d.get("s")
        if not sym:
            return
        q = self.quotes.get(sym) or LiveQuote(symbol=sym, price=0.0)
        q.bid = float(d.get("b", 0) or 0)
        q.ask = float(d.get("a", 0) or 0)
        if q.price <= 0 and q.bid and q.ask:
            q.price = (q.bid + q.ask) / 2
        q.ts = time.time()
        self.quotes[sym] = q

    def _apply_trade(self, d: dict) -> None:
        sym = d.get("s")
        if not sym:
            return
        buf = self.trades.setdefault(sym, [])
        buf.append({
            "price": float(d["p"]), "qty": float(d["q"]), "ts": d["T"],
            # m=True means the buyer was the maker, so the aggressor was a seller.
            "buyer_is_maker": d["m"],
        })
        if len(buf) > 1000:
            del buf[:-600]

    # -- reads -------------------------------------------------------------

    def price(self, symbol: str) -> float:
        q = self.quotes.get(symbol.upper())
        return q.price if q else 0.0

    def quote(self, symbol: str) -> LiveQuote | None:
        return self.quotes.get(symbol.upper())

    def recent_trades(self, symbol: str, limit: int = 400) -> list[dict]:
        return self.trades.get(symbol.upper(), [])[-limit:]

    def status(self) -> dict[str, Any]:
        age = time.time() - self.last_message_ts if self.last_message_ts else None
        fresh = sum(1 for q in self.quotes.values() if not q.is_stale)
        return {
            "connected": self.connected,
            "host": self.host,
            "symbols_streaming": len(self.quotes),
            "symbols_fresh": fresh,
            "watched": self.watched,
            "messages": self.messages,
            "reconnects": self.reconnects,
            "last_message_age_s": round(age, 1) if age is not None else None,
            "last_error": self.last_error,
            "health": (
                "live" if self.connected and age is not None and age < 5
                else "degraded" if self.connected
                else "disconnected"
            ),
            "note": (
                "One websocket carries a price for every symbol on Binance at no "
                "REST weight cost. Quotes older than "
                f"{STALE_AFTER_SECONDS:.0f}s are marked stale rather than served "
                "as if current."
            ),
        }
