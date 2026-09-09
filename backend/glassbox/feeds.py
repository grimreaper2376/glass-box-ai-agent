"""
GlassBox — real Binance market data.

Everything in this module is live data from Binance's public endpoints. No
authentication, no API key, no account scope, and no write path — this is the
read-only half of Agent OS and it is safe to point at production.

Host failover
-------------
Binance geo-restricts `api.binance.com` in some jurisdictions and returns HTTP
451. It also publishes `data-api.binance.vision`, a market-data-only mirror that
serves the same spot endpoints without that restriction. This client tries hosts
in order and remembers which one worked, so the same code runs from anywhere.

Honest degradation
------------------
USD-M futures data (funding rates, open interest, long/short ratios) lives on
`fapi.binance.com`, which has no public mirror. Where a feed is unavailable, the
client says so and the analyst that depends on it **abstains and reports why**.
It does not fall back to a plausible-looking number. An agent that invents data
when a feed is down is worse than an agent that admits it is blind.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

SPOT_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
FUTURES_HOSTS = ["https://fapi.binance.com"]

# data-stream.binance.vision is the market-data websocket mirror and is not
# geo-restricted the way stream.binance.com is.
WS_HOSTS = [
    "wss://data-stream.binance.vision",
    "wss://stream.binance.com:9443",
]


@dataclass
class FeedHealth:
    """What is actually reachable right now, surfaced in the dashboard."""

    spot_host: str | None = None
    futures_host: str | None = None
    spot_ok: bool = False
    futures_ok: bool = False
    last_error: str | None = None
    last_check: float = 0.0
    calls: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "spot_host": self.spot_host,
            "futures_host": self.futures_host,
            "spot_ok": self.spot_ok,
            "futures_ok": self.futures_ok,
            "futures_note": (
                None
                if self.futures_ok
                else "USD-M futures endpoints unreachable from this network. "
                "Derivatives analysts abstain rather than guess."
            ),
            "last_error": self.last_error,
            "calls": self.calls,
            "failures": self.failures,
            "last_check": self.last_check,
        }


class BinanceFeeds:
    """Cached, failover-aware client for Binance public market data."""

    def __init__(self, timeout: float = 10.0, governor=None):
        from .ratelimit import RateLimitGovernor

        self._client: httpx.AsyncClient | None = None
        self.health = FeedHealth()
        self.timeout = timeout
        self.governor = governor or RateLimitGovernor()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._universe: list[dict[str, Any]] = []
        self._universe_ts: float = 0.0
        self._assets: dict[str, dict] = {}
        self._usd_rates: dict[str, float] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": "GlassBox/1.0 (+public-market-data)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- transport ---------------------------------------------------------

    async def _fetch(
        self, path: str, params: dict | None = None, futures: bool = False, ttl: float = 0.0
    ) -> Any:
        key = f"{'f' if futures else 's'}:{path}:{sorted((params or {}).items())}"
        if ttl > 0:
            hit = self._cache.get(key)
            if hit and (time.time() - hit[0]) < ttl:
                return hit[1]

        # Reserve budget before the call, not after. Throttling reactively means
        # discovering the limit by hitting it.
        if not futures:
            await self.governor.reserve(path, params)

        client = await self._get_client()
        hosts = FUTURES_HOSTS if futures else SPOT_HOSTS
        preferred = self.health.futures_host if futures else self.health.spot_host
        if preferred and preferred in hosts:
            hosts = [preferred] + [h for h in hosts if h != preferred]

        errors: list[str] = []
        for host in hosts:
            self.health.calls += 1
            try:
                r = await client.get(f"{host}{path}", params=params)
                if r.status_code == 451:
                    errors.append(f"{host}: geo-restricted (451)")
                    continue
                if r.status_code in (429, 418):
                    wait = self.governor.penalise(r.status_code, r.headers)
                    errors.append(
                        f"{host}: rate limited ({r.status_code}), backing off {wait:.0f}s"
                    )
                    self.health.last_error = errors[-1]
                    await asyncio.sleep(min(wait, 5.0))
                    continue
                if r.status_code == 400:
                    # A bad request is our fault, not the host's. Retrying it
                    # against four more hosts wastes the failover chain and
                    # reports a misleading geo-block as the cause.
                    raise FeedUnavailable(f"bad request to {path}: {r.text[:160]}")
                r.raise_for_status()
                if not futures:
                    self.governor.observe(r.headers)
                data = r.json()
                if futures:
                    self.health.futures_host, self.health.futures_ok = host, True
                else:
                    self.health.spot_host, self.health.spot_ok = host, True
                self.health.last_check = time.time()
                if ttl > 0:
                    self._cache[key] = (time.time(), data)
                return data
            except FeedUnavailable:
                raise
            except Exception as exc:
                errors.append(f"{host}: {type(exc).__name__}")
                self.health.failures += 1
                continue

        last_err = "; ".join(errors[:3])
        self.health.last_error = last_err
        if futures:
            self.health.futures_ok = False
        else:
            self.health.spot_ok = False
        raise FeedUnavailable(last_err or "no host responded")

    # -- symbol universe ---------------------------------------------------

    async def universe(self, quote: str = "USDT", refresh: bool = False) -> list[dict]:
        """
        Every tradable spot pair on Binance, from `exchangeInfo`.

        Cached for an hour: the listing set changes on the order of days, and
        this is a large response.
        """
        if self._universe and not refresh and (time.time() - self._universe_ts) < 3600:
            return [s for s in self._universe if not quote or s["quote"] == quote]

        data = await self._fetch("/api/v3/exchangeInfo", ttl=3600)
        out = []
        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            lot = filters.get("LOT_SIZE", {})
            price_filter = filters.get("PRICE_FILTER", {})
            notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
            out.append(
                {
                    "symbol": s["symbol"],
                    "base": s["baseAsset"],
                    "quote": s["quoteAsset"],
                    "spot": s.get("isSpotTradingAllowed", False),
                    "margin": s.get("isMarginTradingAllowed", False),
                    "min_qty": float(lot.get("minQty", 0) or 0),
                    "step_size": float(lot.get("stepSize", 0) or 0),
                    "tick_size": float(price_filter.get("tickSize", 0) or 0),
                    "min_notional": float(
                        notional.get("minNotional", notional.get("notional", 0)) or 0
                    ),
                    "base_precision": s.get("baseAssetPrecision", 8),
                    "quote_precision": s.get("quoteAssetPrecision", 8),
                }
            )
        self._universe = out
        self._universe_ts = time.time()
        self.governor.set_limit_from_exchange_info(data)
        self._rebuild_assets(out)
        return [s for s in out if not quote or s["quote"] == quote]

    async def symbol_filters(self, symbol: str):
        """
        The exact LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL grid for one symbol,
        as a `precision.SymbolFilters` ready for exact order sizing.

        Refreshes the universe if the symbol isn't cached yet — a symbol the
        Constitution just added to its allowlist should not need a manual
        refresh before it can be sized correctly.
        """
        from .precision import SymbolFilters

        for row in self._universe:
            if row["symbol"] == symbol:
                return SymbolFilters.from_universe_row(row)
        await self.universe(quote="")
        for row in self._universe:
            if row["symbol"] == symbol:
                return SymbolFilters.from_universe_row(row)
        raise ValueError(f"{symbol} was not found in Binance's exchange info.")

    def _rebuild_assets(self, pairs: list[dict]) -> None:
        """Index every coin on Binance and which pairs it trades against."""
        assets: dict[str, dict] = {}
        for p in pairs:
            for role, code in (("base", p["base"]), ("quote", p["quote"])):
                a = assets.setdefault(
                    code, {"asset": code, "pairs": [], "quotes": set(), "as_quote": 0}
                )
                if role == "base":
                    a["pairs"].append(p["symbol"])
                    a["quotes"].add(p["quote"])
                else:
                    a["as_quote"] += 1
        for a in assets.values():
            a["quotes"] = sorted(a["quotes"])
            a["pair_count"] = len(a["pairs"])
        self._assets = assets

    async def assets(self) -> dict[str, dict]:
        if not self._assets:
            await self.universe(quote="")
        return self._assets

    async def usd_rates(self) -> dict[str, float]:
        """
        A USD price for every quote asset, so pairs quoted in BTC, ETH, BNB or
        EUR can be compared with USDT pairs on one scale. Without this, ranking
        the whole market by volume silently compares different currencies.
        """
        prices = await self._fetch("/api/v3/ticker/price", ttl=30.0)
        by_symbol = {r["symbol"]: float(r["price"]) for r in prices}
        rates = {"USDT": 1.0, "USDC": 1.0, "FDUSD": 1.0, "BUSD": 1.0, "TUSD": 1.0}
        for quote in ("BTC", "ETH", "BNB", "SOL", "XRP", "TRX", "DOGE"):
            p = by_symbol.get(f"{quote}USDT")
            if p:
                rates[quote] = p
        for fiat in ("EUR", "TRY", "BRL", "ARS", "JPY", "PLN", "ZAR", "AED", "COP", "IDR"):
            direct = by_symbol.get(f"{fiat}USDT")
            if direct:
                rates[fiat] = direct
                continue
            inverse = by_symbol.get(f"USDT{fiat}")
            if inverse and inverse > 0:
                rates[fiat] = 1 / inverse
        self._usd_rates = rates
        return rates

    async def all_prices(self) -> dict[str, float]:
        """Every symbol's last price in one weight-4 call."""
        rows = await self._fetch("/api/v3/ticker/price", ttl=5.0)
        return {r["symbol"]: float(r["price"]) for r in rows}

    async def rolling_ticker(self, symbol: str, window: str = "1h") -> dict:
        """Arbitrary-window stats, e.g. 1h or 4h change rather than only 24h."""
        d = await self._fetch(
            "/api/v3/ticker", {"symbol": symbol, "windowSize": window}, ttl=15.0
        )
        return {
            "symbol": d["symbol"],
            "window": window,
            "change_pct": float(d["priceChangePercent"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "volume_usd": float(d["quoteVolume"]),
            "trades": int(d["count"]),
        }

    async def avg_price(self, symbol: str) -> float:
        d = await self._fetch("/api/v3/avgPrice", {"symbol": symbol}, ttl=10.0)
        return float(d["price"])

    # -- spot market data --------------------------------------------------

    async def tickers_24h(self, symbols: list[str] | None = None) -> dict[str, dict]:
        if symbols and len(symbols) <= 50:
            data = await self._fetch(
                "/api/v3/ticker/24hr", {"symbols": _symbol_array(symbols)}, ttl=2.0
            )
        else:
            data = await self._fetch("/api/v3/ticker/24hr", ttl=5.0)
        rows = data if isinstance(data, list) else [data]
        return {r["symbol"]: r for r in rows}

    async def book_tickers(self, symbols: list[str] | None = None) -> dict[str, dict]:
        if symbols and len(symbols) <= 50:
            data = await self._fetch(
                "/api/v3/ticker/bookTicker", {"symbols": _symbol_array(symbols)}, ttl=1.0
            )
        else:
            data = await self._fetch("/api/v3/ticker/bookTicker", ttl=2.0)
        rows = data if isinstance(data, list) else [data]
        return {r["symbol"]: r for r in rows}

    async def klines(
        self, symbol: str, interval: str = "5m", limit: int = 200,
        start_ms: int | None = None, end_ms: int | None = None,
    ) -> list[dict]:
        """Real OHLCV candles. This is what the technical analyst reads."""
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        raw = await self._fetch("/api/v3/klines", params, ttl=0 if start_ms else 20.0)
        return [
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
            }
            for k in raw
        ]

    async def depth(self, symbol: str, limit: int = 100) -> dict:
        """Order book. Real liquidity, real imbalance, real slippage estimates."""
        d = await self._fetch("/api/v3/depth", {"symbol": symbol, "limit": limit}, ttl=3.0)
        return {
            "bids": [(float(p), float(q)) for p, q in d.get("bids", [])],
            "asks": [(float(p), float(q)) for p, q in d.get("asks", [])],
        }

    async def agg_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        raw = await self._fetch(
            "/api/v3/aggTrades", {"symbol": symbol, "limit": limit}, ttl=3.0
        )
        return [
            {
                "price": float(t["p"]),
                "qty": float(t["q"]),
                "ts": t["T"],
                # Binance sets m=True when the BUYER is the maker, i.e. the
                # aggressor was a seller. Getting this backwards inverts every
                # flow signal in the system.
                "buyer_is_maker": t["m"],
            }
            for t in raw
        ]

    # -- derivatives (may be unavailable; callers must handle) -------------

    async def funding(self, symbol: str) -> dict:
        d = await self._fetch("/fapi/v1/premiumIndex", {"symbol": symbol},
                              futures=True, ttl=30.0)
        return {
            "mark_price": float(d["markPrice"]),
            "index_price": float(d["indexPrice"]),
            "funding_rate": float(d["lastFundingRate"]),
            "funding_bps": float(d["lastFundingRate"]) * 10000,
            "next_funding_ms": d["nextFundingTime"],
        }

    async def open_interest_hist(self, symbol: str, period: str = "5m", limit: int = 30):
        raw = await self._fetch(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
            futures=True, ttl=60.0,
        )
        return [
            {"ts": r["timestamp"], "oi": float(r["sumOpenInterest"]),
             "oi_value": float(r["sumOpenInterestValue"])}
            for r in raw
        ]

    async def long_short_ratio(self, symbol: str, period: str = "5m", limit: int = 30):
        raw = await self._fetch(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
            futures=True, ttl=60.0,
        )
        return [
            {"ts": r["timestamp"], "ratio": float(r["longShortRatio"]),
             "long_pct": float(r["longAccount"]), "short_pct": float(r["shortAccount"])}
            for r in raw
        ]

    async def taker_flow(self, symbol: str, period: str = "5m", limit: int = 30):
        raw = await self._fetch(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit},
            futures=True, ttl=60.0,
        )
        return [
            {"ts": r["timestamp"], "buy_sell_ratio": float(r["buySellRatio"]),
             "buy_vol": float(r["buyVol"]), "sell_vol": float(r["sellVol"])}
            for r in raw
        ]

    async def probe(self) -> dict:
        """Check what is reachable. Called at startup and shown in the UI."""
        try:
            await self._fetch("/api/v3/ping")
        except FeedUnavailable:
            pass
        try:
            await self._fetch("/fapi/v1/ping", futures=True)
        except FeedUnavailable:
            pass
        return self.health.to_dict()


def _symbol_array(symbols: list[str]) -> str:
    """
    Binance wants `symbols=["A","B"]` with no whitespace. json.dumps inserts a
    space after each comma by default and the endpoint rejects it with a 400,
    which then looks like a host failure and burns the whole failover chain.
    """
    import json as _json

    return _json.dumps(symbols, separators=(",", ":"))


class FeedUnavailable(Exception):
    """A feed could not be reached. Callers must abstain, not improvise."""
