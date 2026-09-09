"""
GlassBox — analysts operating on real Binance data.

Every number these analysts read is fetched live from Binance's public
endpoints: OHLCV candles, order book depth, aggregated trades, funding rates,
open interest and long/short account ratios. No simulated feeds.

The rule that matters
---------------------
**An analyst that cannot get its data abstains and says so.** It does not fall
back to a plausible-looking value and it does not silently reuse a stale one. A
fabricated signal is worse than silence, because the quorum rule downstream
would count it as an independent opinion.

Each Signal carries `data_quality` — `live`, `partial` or `unavailable` — which
the dashboard displays and the calibration tracker uses so an analyst is never
scored on a call it made while blind.
"""

from __future__ import annotations

import math
import statistics

from ..config import Signal
from ..indicators import (
    adx,
    atr_pct,
    bollinger,
    ema,
    macd,
    rsi,
    volume_profile_skew,
    zscore,
)
from ..x402 import BudgetExceeded, X402Meter


class Analyst:
    """One voice on the Council."""

    name = "analyst"
    description = ""
    data_provider: str | None = None
    data_cost_usd: float = 0.0
    data_ttl_seconds: float = 300.0
    requires: list[str] = []

    def __init__(self, meter: X402Meter | None = None):
        self.meter = meter
        self.notes: list[str] = []
        self._data_cache: dict[str, float] = {}

    def buy_data(self, resource: str) -> float:
        if not (self.meter and self.data_provider and self.data_cost_usd > 0):
            return 0.0
        import time as _time

        last = self._data_cache.get(resource)
        if last is not None and (_time.time() - last) < self.data_ttl_seconds:
            return 0.0
        try:
            p = self.meter.purchase(
                self.data_provider, resource, self.data_cost_usd, self.name
            )
            self._data_cache[resource] = _time.time()
            return p.cost_usd
        except BudgetExceeded as exc:
            self.notes.append(str(exc))
            return 0.0

    async def analyse(self, symbol: str, ctx) -> Signal:
        raise NotImplementedError

    def _signal(self, symbol, stance, confidence, rationale,
                evidence=None, cost=0.0, data_quality="live") -> Signal:
        if self.notes:
            rationale += " (" + "; ".join(self.notes) + ")"
            self.notes = []
        return Signal(
            agent=self.name,
            symbol=symbol,
            stance=stance,
            confidence=max(0.0, min(1.0, confidence)),
            rationale=rationale,
            evidence={**(evidence or {}), "data_quality": data_quality},
            cost_usd=cost,
        )

    def _abstain(self, symbol: str, why: str) -> Signal:
        """The honest response to a missing feed."""
        return self._signal(
            symbol, "neutral", 0.0,
            f"Abstaining: {why} This analyst does not guess when it cannot see.",
            data_quality="unavailable",
        )


# ---------------------------------------------------------------------------


class TechnicalAnalyst(Analyst):
    name = "technical"
    description = "Trend, momentum and mean reversion from real Binance candles"
    requires = ["klines"]

    async def analyse(self, symbol: str, ctx) -> Signal:
        candles = ctx.klines.get(symbol)
        if not candles or len(candles) < 60:
            return self._abstain(symbol, "not enough candle history from Binance.")

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        vols = [c["volume"] for c in candles]

        fast, slow = ema(closes, 12), ema(closes, 26)
        trend_pct = (fast - slow) / max(slow, 1e-9) * 100
        r = rsi(closes, 14)
        macd_line, signal_line, hist = macd(closes)
        upper, mid, lower = bollinger(closes, 20, 2.0)
        band_pos = (closes[-1] - lower) / max(upper - lower, 1e-9)
        strength = adx(highs, lows, closes, 14)
        z = zscore(closes[-60:])
        vskew = volume_profile_skew(closes, vols)

        score, notes = 0.0, []

        if trend_pct > 0.12:
            score += 0.30
            notes.append(f"EMA12 sits {trend_pct:.2f}% above EMA26")
        elif trend_pct < -0.12:
            score -= 0.30
            notes.append(f"EMA12 sits {abs(trend_pct):.2f}% below EMA26")

        if hist > 0 and macd_line > signal_line:
            score += 0.18
            notes.append("MACD histogram positive")
        elif hist < 0 and macd_line < signal_line:
            score -= 0.18
            notes.append("MACD histogram negative")

        if r < 32:
            score += 0.24
            notes.append(f"RSI {r:.0f}, oversold")
        elif r > 70:
            score -= 0.24
            notes.append(f"RSI {r:.0f}, overbought")
        else:
            notes.append(f"RSI {r:.0f}")

        if band_pos < 0.12:
            score += 0.18
            notes.append("price pinned to the lower Bollinger band")
        elif band_pos > 0.88:
            score -= 0.18
            notes.append("price pinned to the upper Bollinger band")

        if abs(z) > 1.6:
            score += -0.14 if z > 0 else 0.14
            notes.append(f"{abs(z):.1f} sigma from the 60-bar mean")

        if vskew > 0.15:
            score += 0.10
            notes.append("volume concentrated on up bars")
        elif vskew < -0.15:
            score -= 0.10
            notes.append("volume concentrated on down bars")

        # In a rangebound tape, trend signals are noise. Damp rather than drop.
        damp = 1.0
        if strength < 20:
            damp = 0.65
            notes.append(f"ADX {strength:.0f}, no real trend to follow")

        stance = "bullish" if score > 0.20 else "bearish" if score < -0.20 else "neutral"
        return self._signal(
            symbol, stance, min(0.30 + abs(score) * 1.25, 0.94) * damp,
            "; ".join(notes) + ".",
            {"ema_spread_pct": round(trend_pct, 3), "rsi": round(r, 1),
             "macd_hist": round(hist, 6), "bollinger_position": round(band_pos, 3),
             "adx": round(strength, 1), "zscore": round(z, 2),
             "volume_skew": round(vskew, 3), "candles_used": len(candles)},
        )


class OrderFlowAnalyst(Analyst):
    name = "orderflow"
    description = "Live order book imbalance and aggressive trade pressure"
    requires = ["depth"]

    async def analyse(self, symbol: str, ctx) -> Signal:
        book = ctx.depth.get(symbol)
        trades = ctx.trades.get(symbol)
        if not book or not book.get("bids") or not book.get("asks"):
            return self._abstain(symbol, "the order book could not be fetched.")

        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2

        # Only depth within 0.5% of mid counts. Resting orders further out are
        # routinely pulled before they ever trade.
        band = mid * 0.005
        bid_depth = sum(q for p, q in book["bids"] if p >= mid - band)
        ask_depth = sum(q for p, q in book["asks"] if p <= mid + band)
        book_imb = (bid_depth - ask_depth) / max(bid_depth + ask_depth, 1e-9)

        notes = [f"book imbalance {book_imb:+.2f} within 0.5% of mid"]
        score = book_imb * 0.55
        taker_imb = None

        if trades:
            # buyer_is_maker=True means the aggressor was a SELLER.
            buy = sum(t["qty"] for t in trades if not t["buyer_is_maker"])
            sell = sum(t["qty"] for t in trades if t["buyer_is_maker"])
            taker_imb = (buy - sell) / max(buy + sell, 1e-9)
            score += taker_imb * 0.60
            notes.append(
                f"aggressive flow {taker_imb:+.2f} over the last {len(trades)} trades"
            )
        else:
            notes.append("trade tape unavailable, reading the book alone")

        stance = "bullish" if score > 0.16 else "bearish" if score < -0.16 else "neutral"
        return self._signal(
            symbol, stance, min(0.30 + abs(score) * 0.95, 0.88), "; ".join(notes) + ".",
            {"book_imbalance": round(book_imb, 4),
             "taker_imbalance": round(taker_imb, 4) if taker_imb is not None else None,
             "bid_depth": round(bid_depth, 4), "ask_depth": round(ask_depth, 4)},
            data_quality="live" if trades else "partial",
        )


class DerivativesAnalyst(Analyst):
    name = "derivatives"
    description = "Funding, open interest and crowd positioning from Binance futures"
    requires = ["funding"]

    async def analyse(self, symbol: str, ctx) -> Signal:
        d = ctx.derivatives.get(symbol)
        if not d or d.get("error"):
            reason = d.get("error", "no response") if d else "no response"
            return self._abstain(
                symbol, f"Binance USD-M futures data is unreachable ({reason})."
            )

        score, notes = 0.0, []

        funding_bps = d.get("funding_bps")
        if funding_bps is not None:
            annual = funding_bps / 100 * 3 * 365  # funding settles three times daily
            if funding_bps > 3.0:
                score -= min(funding_bps / 12, 0.42)
                notes.append(
                    f"funding {funding_bps:.2f} bps ({annual:.0f}%/yr), longs paying up"
                )
            elif funding_bps < -2.0:
                score += min(abs(funding_bps) / 10, 0.40)
                notes.append(
                    f"funding {funding_bps:.2f} bps ({annual:.0f}%/yr), shorts paying"
                )
            else:
                notes.append(f"funding neutral at {funding_bps:.2f} bps")

        oi_change = d.get("oi_change_pct")
        price_change = d.get("price_change_pct", 0.0)
        if oi_change is not None:
            if oi_change > 2 and price_change < -0.5:
                score -= 0.18
                notes.append(
                    f"open interest +{oi_change:.1f}% while price fell, so this is "
                    f"new shorts rather than capitulation"
                )
            elif oi_change > 2 and price_change > 0.5:
                score += 0.14
                notes.append(f"open interest +{oi_change:.1f}% with price up, real buying")
            elif oi_change < -3:
                notes.append(f"open interest {oi_change:.1f}%, positions closing out")

        ls = d.get("long_short_ratio")
        if ls is not None:
            if ls > 2.4:
                score -= 0.22
                notes.append(f"{ls:.2f}:1 long/short, the crowd is one-sided")
            elif ls < 0.85:
                score += 0.20
                notes.append(f"{ls:.2f}:1 long/short, crowd is short")
            else:
                notes.append(f"long/short {ls:.2f}:1")

        stance = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"
        return self._signal(
            symbol, stance, min(0.32 + abs(score) * 1.15, 0.88),
            "; ".join(notes) + ".", d,
        )


class RegimeAnalyst(Analyst):
    name = "regime"
    description = "Volatility regime, BTC leadership and cross-asset correlation"
    requires = ["klines"]

    async def analyse(self, symbol: str, ctx) -> Signal:
        candles = ctx.klines.get(symbol)
        btc = ctx.klines.get("BTCUSDT")
        if not candles or len(candles) < 60:
            return self._abstain(symbol, "not enough candle history.")

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        vol = atr_pct(highs, lows, closes, 14)

        score, notes = 0.0, []
        if vol > 3.0:
            score -= 0.20
            notes.append(f"ATR {vol:.2f}%, a volatile regime where edges decay fast")
        else:
            notes.append(f"ATR {vol:.2f}%")

        beta = corr = None
        if btc and len(btc) >= 60 and symbol != "BTCUSDT":
            a = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))][-50:]
            bcl = [c["close"] for c in btc]
            b = [bcl[i] / bcl[i - 1] - 1 for i in range(1, len(bcl))][-50:]
            n = min(len(a), len(b))
            a, b = a[-n:], b[-n:]
            if n > 20:
                ma, mb = statistics.fmean(a), statistics.fmean(b)
                cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
                va = sum((x - ma) ** 2 for x in a) / n
                vb = sum((y - mb) ** 2 for y in b) / n
                if va > 1e-14 and vb > 1e-14:
                    corr = cov / math.sqrt(va * vb)
                    beta = cov / vb
                    btc_move = (bcl[-1] / bcl[-13] - 1) * 100 if len(bcl) > 13 else 0.0
                    if corr > 0.75:
                        notes.append(
                            f"{corr:.2f} correlation to BTC at beta {beta:.2f}, so this "
                            f"is a Bitcoin trade wearing a different ticker"
                        )
                        score += 0.16 if btc_move > 0.4 else -0.16 if btc_move < -0.4 else 0.0
                    else:
                        notes.append(f"{corr:.2f} correlation to BTC, moving on its own")

        rng = (max(closes[-48:]) - min(closes[-48:])) / max(closes[-1], 1e-9) * 100
        if rng < 1.5:
            notes.append(f"{rng:.1f}% range over 48 bars, compressed and likely to expand")

        stance = "bullish" if score > 0.12 else "bearish" if score < -0.12 else "neutral"
        return self._signal(
            symbol, stance, min(0.28 + abs(score) * 1.1, 0.80), "; ".join(notes) + ".",
            {"atr_pct": round(vol, 3),
             "btc_correlation": round(corr, 3) if corr is not None else None,
             "btc_beta": round(beta, 3) if beta is not None else None,
             "range_48_pct": round(rng, 2)},
        )


class LiquidityAnalyst(Analyst):
    name = "liquidity"
    description = "Real slippage cost, walked through the live book at our size"
    requires = ["depth"]

    async def analyse(self, symbol: str, ctx) -> Signal:
        book = ctx.depth.get(symbol)
        ticker = ctx.tickers.get(symbol, {})
        if not book or not book.get("asks") or not book.get("bids"):
            return self._abstain(symbol, "the order book could not be fetched.")

        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
        spread_bps = (book["asks"][0][0] - book["bids"][0][0]) / max(mid, 1e-9) * 10000

        # Walk the real book for the size we actually intend to trade. This is
        # the difference between a backtest and a fill.
        target_usd = max(ctx.intended_notional_usd, 1.0)
        filled = cost = 0.0
        for price, qty in book["asks"]:
            take = min(qty * price, target_usd - filled)
            if take <= 0:
                break
            cost += take * price
            filled += take
        vwap = cost / filled if filled > 0 else mid
        slippage_bps = (vwap / mid - 1) * 10000 if filled > 0 else 999.0
        coverage = filled / target_usd
        vol_24h = float(ticker.get("quoteVolume", 0) or 0)

        score = 0.0
        notes = [
            f"${target_usd:,.0f} would fill at {slippage_bps:.1f} bps of slippage",
            f"spread {spread_bps:.1f} bps",
        ]
        if coverage < 0.98:
            score -= 0.5
            notes.append(f"the visible book only covers {coverage:.0%} of that size")
        if slippage_bps > 20:
            score -= 0.35
            notes.append("thin book, this size moves the price against us")
        elif slippage_bps < 4 and vol_24h > 5e7:
            score += 0.22
            notes.append("deep book, execution is cheap")
        if vol_24h and vol_24h < 5e6:
            score -= 0.30
            notes.append(f"only ${vol_24h / 1e6:.1f}M of 24h volume")

        # Liquidity never argues *for* a trade. It can only argue against one.
        stance = "bearish" if score < -0.2 else "neutral"
        return self._signal(
            symbol, stance,
            min(abs(score), 0.85) if stance == "bearish" else 0.25,
            "; ".join(notes) + ".",
            {"spread_bps": round(spread_bps, 2), "slippage_bps": round(slippage_bps, 2),
             "book_coverage": round(coverage, 3), "volume_24h_usd": vol_24h,
             "sized_for_usd": round(target_usd, 2)},
        )


ALL_ANALYSTS = [
    TechnicalAnalyst,
    OrderFlowAnalyst,
    DerivativesAnalyst,
    RegimeAnalyst,
    LiquidityAnalyst,
]
