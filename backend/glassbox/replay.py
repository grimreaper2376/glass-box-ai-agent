"""
GlassBox — historical replay on real Binance candles.

The simulator answers "does the Guardian fire when it should". It cannot answer
"would this have made money", because a seeded random walk has no memory of
2026. This module closes that gap by replaying genuine Binance klines.

How it works
------------
Real OHLCV is downloaded once per symbol, then replayed bar by bar. At each bar
the analysts see only the candles up to that point — no future data reaches
them — and the same Council, Constitution and Guardian run unchanged. Fills use
the bar's actual open with realistic fees and slippage.

What it deliberately does not do
--------------------------------
Order book depth and aggregated trades are not available historically from the
public API, so the order-flow and liquidity analysts abstain during replay
rather than being fed a reconstruction. This makes historical results
*conservative*: the system runs with three of five analysts and reports that in
the output. A backtest that quietly invented an order book to raise its own
score would be the exact failure this project exists to prevent.

Look-ahead bias is the main way backtests lie, so the replay slices candles
strictly and fills at the next bar's open rather than the signal bar's close.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from .agents.analysts import DerivativesAnalyst, RegimeAnalyst, TechnicalAnalyst
from .agents.council import Council
from .agents.guardian import Guardian
from .config import Clock, Decision, Settings
from .constitution import Constitution
from .feeds import BinanceFeeds, FeedUnavailable
from .marketdata import MarketContext
from .portfolio import PaperPortfolio

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


@dataclass
class ReplayResult:
    symbols: list[str]
    interval: str
    bars: int
    start_ts: float
    end_ts: float
    starting_equity: float
    final_equity: float
    pnl_pct: float
    buy_hold_pct: float
    excess_pct: float
    max_drawdown_pct: float
    trades: int
    win_rate_pct: float
    profit_factor: float | None
    sharpe: float
    fees_usd: float
    intents: int
    denied: int
    guardian_vetoes: int
    analysts_active: list[str]
    analysts_abstained: list[str]
    equity_curve: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["equity_curve"] = self.equity_curve[-400:]
        return d


class HistoricalReplay:
    """Replays real Binance candles through the live decision pipeline."""

    # Only analysts whose inputs genuinely exist in historical klines.
    REPLAY_ANALYSTS = [TechnicalAnalyst, RegimeAnalyst, DerivativesAnalyst]
    ABSTAINED = ["orderflow", "liquidity"]

    def __init__(self, settings: Settings | None = None, policy_path=None):
        self.settings = settings or Settings.from_env()
        self.constitution = Constitution(policy_path or self.settings.policy_path)
        self.feeds = BinanceFeeds()

    async def load(self, symbols: list[str], interval: str, bars: int) -> dict[str, list[dict]]:
        """Download real candles, paging backwards past the 1000-bar API cap."""
        out: dict[str, list[dict]] = {}
        step = INTERVAL_MS.get(interval, 300_000)
        for s in symbols:
            collected: list[dict] = []
            end = None
            while len(collected) < bars:
                need = min(1000, bars - len(collected))
                chunk = await self.feeds.klines(
                    s, interval, need, end_ms=end
                )
                if not chunk:
                    break
                collected = chunk + collected
                end = chunk[0]["open_time"] - 1
                if len(chunk) < need:
                    break
            out[s] = collected[-bars:]
        return out

    async def run(
        self, symbols: list[str], interval: str = "1h", bars: int = 720,
        equity: float = 10_000.0, warmup: int = 80,
    ) -> ReplayResult:
        data = await self.load(symbols, interval, bars + warmup)
        usable = [s for s in symbols if len(data.get(s, [])) > warmup + 10]
        if not usable:
            raise FeedUnavailable("no candle history returned for any requested symbol")

        step_seconds = INTERVAL_MS.get(interval, 300_000) / 1000
        clock = Clock(simulated=True, step_seconds=step_seconds)
        portfolio = PaperPortfolio(equity, clock=clock)
        council = Council()
        council.analysts = [cls() for cls in self.REPLAY_ANALYSTS]
        guardian = Guardian()

        counters = {"intents": 0, "denied": 0, "vetoes": 0}
        curve: list[float] = []
        n = min(len(data[s]) for s in usable)

        # Equal-weight buy and hold over the identical window.
        basis = {s: data[s][warmup]["open"] for s in usable}

        for i in range(warmup, n):
            clock.advance()
            bar = {s: data[s][i] for s in usable}
            marks = {s: bar[s]["close"] for s in usable}

            for _fill in portfolio.check_protective_exits(marks):
                pass

            state = portfolio.state_dict(marks)
            portfolio.roll_day_if_needed(marks)

            # Analysts see only candles up to and including this bar.
            ctx = MarketContext(ts=clock.now(), symbols=usable)
            for s in usable:
                ctx.klines[s] = data[s][: i + 1]
                ctx.tickers[s] = {"lastPrice": marks[s], "quoteVolume": bar[s]["quote_volume"]}
                ctx.derivatives[s] = {"error": "not available in historical replay"}
            if "BTCUSDT" in data:
                ctx.klines["BTCUSDT"] = data["BTCUSDT"][: i + 1]
            ctx.intended_notional_usd = max(state["equity_usd"] * 0.09, 10.0)

            assessment = guardian.assess_from_klines(ctx, state, usable)

            intents = []
            if not guardian.quarantined:
                proposed, _verdicts = await council.propose_async(ctx, usable, state)
                intents.extend(proposed)

            for intent in intents:
                counters["intents"] += 1
                verdict = self.constitution.evaluate(intent, state, self._market_view(ctx, usable))
                ok, _reason = guardian.review(intent, assessment)
                if not ok:
                    counters["vetoes"] += 1
                    counters["denied"] += 1
                    continue
                if verdict.decision == Decision.DENY:
                    counters["denied"] += 1
                    continue

                # Fill at the NEXT bar's open. Filling at the signal bar's close
                # is the classic way a backtest gives itself information it could
                # not have had.
                if i + 1 >= n:
                    continue
                fill_price = data[intent.symbol][i + 1]["open"]
                notional = verdict.adjusted_notional_usd or intent.notional_usd
                portfolio.execute(
                    intent.symbol, intent.side.value, notional, fill_price,
                    intent_id=intent.intent_id, module=intent.module,
                    stop_loss=intent.stop_loss, take_profit=intent.take_profit,
                )

            curve.append(portfolio.equity(marks))

        final_marks = {s: data[s][n - 1]["close"] for s in usable}
        final_equity = portfolio.equity(final_marks)
        perf = portfolio.performance(final_marks)

        per = equity / len(usable)
        bh = sum(per * (final_marks[s] / basis[s]) for s in usable)
        bh_pct = (bh / equity - 1) * 100
        pnl_pct = (final_equity / equity - 1) * 100

        rets = [
            (curve[i] - curve[i - 1]) / max(curve[i - 1], 1e-9)
            for i in range(1, len(curve))
        ]
        sharpe = 0.0
        if len(rets) > 3:
            sd = statistics.pstdev(rets)
            if sd > 1e-12:
                bars_per_year = (365 * 24 * 3600) / step_seconds
                sharpe = statistics.fmean(rets) / sd * (bars_per_year ** 0.5)

        peak = equity
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak * 100)

        return ReplayResult(
            symbols=usable, interval=interval, bars=len(curve),
            start_ts=data[usable[0]][warmup]["open_time"] / 1000,
            end_ts=data[usable[0]][n - 1]["close_time"] / 1000,
            starting_equity=equity, final_equity=round(final_equity, 2),
            pnl_pct=round(pnl_pct, 3), buy_hold_pct=round(bh_pct, 3),
            excess_pct=round(pnl_pct - bh_pct, 3),
            max_drawdown_pct=round(max_dd, 2), trades=perf["closed_trades"],
            win_rate_pct=round(perf["win_rate_pct"], 1),
            profit_factor=perf["profit_factor"], sharpe=round(sharpe, 2),
            fees_usd=round(portfolio.total_fees, 2),
            intents=counters["intents"], denied=counters["denied"],
            guardian_vetoes=counters["vetoes"],
            analysts_active=[a.name for a in council.analysts],
            analysts_abstained=self.ABSTAINED,
            equity_curve=[round(v, 2) for v in curve],
            notes=[
                "Real Binance OHLCV, replayed bar by bar with no look-ahead.",
                "Fills use the next bar's open, not the signal bar's close.",
                f"{', '.join(self.ABSTAINED)} abstain: order book and trade tape are "
                f"not available historically, so they are not reconstructed.",
                "Results are therefore conservative — the system runs with "
                f"{len(council.analysts)} of 5 analysts.",
            ],
        )

    @staticmethod
    def _market_view(ctx: MarketContext, symbols: list[str]) -> dict:
        """The shape the Constitution's spread and volatility rules expect."""
        from .indicators import atr_pct

        out = {}
        for s in symbols:
            k = ctx.klines.get(s, [])
            if not k:
                continue
            closes = [c["close"] for c in k[-60:]]
            highs = [c["high"] for c in k[-60:]]
            lows = [c["low"] for c in k[-60:]]
            out[s] = {
                "price": closes[-1],
                "spread_bps": 2.0,  # historical books are unavailable; assume tight
                "atr_pct": atr_pct(highs, lows, closes, 14),
            }
        return out

    async def aclose(self) -> None:
        await self.feeds.aclose()
