"""
GlassBox — the four specialist analysts.

Each looks at the same market through a different lens and reports independently.
They never see each other's output before speaking, which is the whole point:
correlated agents produce confident nonsense, and a quorum rule over correlated
voices is security theatre.
"""

from __future__ import annotations

import random

from ..config import Signal
from .base import Analyst, ema, rsi, zscore


class TechnicalAnalyst(Analyst):
    name = "technical"
    description = "Trend and momentum from price and volume"

    def analyse(self, symbol: str, market) -> Signal:
        series = market.series(symbol, 120)
        if len(series) < 20:
            return self._signal(symbol, "neutral", 0.2, "Not enough price history yet.")

        fast, slow = ema(series[-24:], 9), ema(series[-60:], 26)
        r = rsi(series, 14)
        z = zscore(series[-40:])
        spread = (fast - slow) / max(slow, 1e-9) * 100

        score = 0.0
        notes = []

        if spread > 0.15:
            score += 0.35
            notes.append(f"fast EMA {spread:.2f}% above slow")
        elif spread < -0.15:
            score -= 0.35
            notes.append(f"fast EMA {abs(spread):.2f}% below slow")

        if r < 32:
            score += 0.28
            notes.append(f"RSI {r:.0f}, oversold")
        elif r > 70:
            score -= 0.28
            notes.append(f"RSI {r:.0f}, overbought")
        else:
            notes.append(f"RSI {r:.0f}, mid-range")

        if z < -1.4:
            score += 0.2
            notes.append(f"price {abs(z):.1f}σ below its recent mean")
        elif z > 1.4:
            score -= 0.2
            notes.append(f"price {z:.1f}σ above its recent mean")

        stance = "bullish" if score > 0.18 else "bearish" if score < -0.18 else "neutral"
        return self._signal(
            symbol,
            stance,
            min(0.34 + abs(score) * 1.35, 0.94),
            "; ".join(notes) + ".",
            {"ema_spread_pct": round(spread, 3), "rsi": round(r, 1), "zscore": round(z, 2)},
        )


class SentimentAnalyst(Analyst):
    name = "sentiment"
    description = "Social and news tone, paid for per request via x402"
    data_provider = "social-sentiment"
    data_cost_usd = 0.02

    def __init__(self, meter=None, seed: int = 11):
        super().__init__(meter)
        self.rng = random.Random(seed)

    def analyse(self, symbol: str, market) -> Signal:
        cost = self.buy_data(f"sentiment/{symbol}/1h")
        series = market.series(symbol, 60)

        # Sentiment lags price and overshoots it — model that rather than
        # pretending we have a real feed in paper mode.
        drift = (series[-1] / series[0] - 1) * 100 if len(series) > 2 else 0.0
        base = max(-1.0, min(1.0, drift / 2.5))
        noise = self.rng.gauss(0, 0.28)
        tone = max(-1.0, min(1.0, base * 0.7 + noise))
        mentions = int(abs(tone) * 4200 + self.rng.randint(200, 900))

        if tone > 0.75:
            # Euphoria is a contrarian tell, not a buy signal.
            return self._signal(
                symbol,
                "bearish",
                0.5,
                f"Social tone at {tone:+.2f} with {mentions:,} mentions is euphoric; "
                f"crowded longs unwind badly.",
                {"tone": round(tone, 2), "mentions": mentions, "regime": "euphoric"},
                cost,
            )

        stance = "bullish" if tone > 0.22 else "bearish" if tone < -0.22 else "neutral"
        return self._signal(
            symbol,
            stance,
            min(0.32 + abs(tone) * 0.85, 0.88),
            f"Social tone {tone:+.2f} across {mentions:,} mentions in the last hour.",
            {"tone": round(tone, 2), "mentions": mentions, "regime": "normal"},
            cost,
        )


class OnChainAnalyst(Analyst):
    name = "onchain"
    description = "Exchange flows and smart-money positioning"
    data_provider = "onchain-flows"
    data_cost_usd = 0.04

    def __init__(self, meter=None, seed: int = 23):
        super().__init__(meter)
        self.rng = random.Random(seed)

    def analyse(self, symbol: str, market) -> Signal:
        cost = self.buy_data(f"flows/{symbol}/exchange-netflow")
        series = market.series(symbol, 60)
        momentum = (series[-1] / series[max(0, len(series) - 20)] - 1) if len(series) > 20 else 0

        # Net outflow from exchanges reads as accumulation; inflow as distribution.
        netflow = self.rng.gauss(-momentum * 6000, 1800)
        whale_txns = self.rng.randint(3, 42)

        if netflow < -2200:
            return self._signal(
                symbol,
                "bullish",
                min(0.36 + abs(netflow) / 9000, 0.86),
                f"Net {abs(netflow):,.0f} units left exchanges over 4h across "
                f"{whale_txns} large transfers — accumulation, not distribution.",
                {"netflow_units": round(netflow), "whale_txns": whale_txns},
                cost,
            )
        if netflow > 2200:
            return self._signal(
                symbol,
                "bearish",
                min(0.36 + netflow / 9000, 0.86),
                f"Net {netflow:,.0f} units moved onto exchanges across {whale_txns} "
                f"large transfers — supply arriving at the sell side.",
                {"netflow_units": round(netflow), "whale_txns": whale_txns},
                cost,
            )
        return self._signal(
            symbol,
            "neutral",
            0.3,
            f"Exchange flows balanced at {netflow:+,.0f} units; no directional edge.",
            {"netflow_units": round(netflow), "whale_txns": whale_txns},
            cost,
        )


class FundingAnalyst(Analyst):
    name = "funding"
    description = "Derivatives positioning and crowding"
    data_provider = "funding-oracle"
    data_cost_usd = 0.01

    def __init__(self, meter=None, seed: int = 37):
        super().__init__(meter)
        self.rng = random.Random(seed)

    def analyse(self, symbol: str, market) -> Signal:
        cost = self.buy_data(f"funding/{symbol}/perp")
        series = market.series(symbol, 40)
        trend = (series[-1] / series[0] - 1) * 100 if len(series) > 2 else 0.0

        funding_bps = self.rng.gauss(trend * 0.9, 1.6)  # 8h funding, basis points
        oi_change = self.rng.gauss(trend * 2.2, 4.0)

        if funding_bps > 4.0:
            return self._signal(
                symbol,
                "bearish",
                min(0.34 + funding_bps / 14, 0.84),
                f"Perp funding at {funding_bps:.1f} bps with open interest "
                f"{oi_change:+.1f}% — longs are paying up and the trade is crowded.",
                {"funding_bps": round(funding_bps, 2), "oi_change_pct": round(oi_change, 1)},
                cost,
            )
        if funding_bps < -3.0:
            return self._signal(
                symbol,
                "bullish",
                min(0.34 + abs(funding_bps) / 12, 0.82),
                f"Funding is negative at {funding_bps:.1f} bps — shorts are paying, "
                f"which is where squeezes start.",
                {"funding_bps": round(funding_bps, 2), "oi_change_pct": round(oi_change, 1)},
                cost,
            )
        return self._signal(
            symbol,
            "neutral",
            0.25,
            f"Funding neutral at {funding_bps:.1f} bps; positioning is not stretched.",
            {"funding_bps": round(funding_bps, 2), "oi_change_pct": round(oi_change, 1)},
            cost,
        )


ALL_ANALYSTS = [TechnicalAnalyst, SentimentAnalyst, OnChainAnalyst, FundingAnalyst]
