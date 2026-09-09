"""
GlassBox — the Guardian.

The Guardian is not another analyst. It sits above the Council with two powers
nobody else has:

    veto      — it can kill any intent, including ones the Constitution allowed
    override  — it can create its own intents at critical urgency, which are the
                only intents allowed to bypass the human-confirmation threshold,
                because a hedge that waits for a click is not a hedge

It watches three things continuously: how fast prices are moving, how correlated
the book has become, and how the portfolio's own risk is evolving. When the
composite threat score crosses the critical line it moves capital to HEDGED, and
if the score keeps climbing it QUARANTINEs, freezing every module out.

The design bet: an agent that can stop itself is worth more than an agent that
is slightly better at picking entries.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..config import CapitalState, Intent, Side


@dataclass
class ThreatAssessment:
    score: float  # 0..100
    level: str  # normal | elevated | critical
    factors: list[dict[str, Any]]
    recommendation: str
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "level": self.level,
            "factors": self.factors,
            "recommendation": self.recommendation,
            "ts": self.ts,
        }


class Guardian:
    name = "sentinel"

    def __init__(
        self,
        elevated_at: float = 45.0,
        critical_at: float = 70.0,
        quarantine_at: float = 88.0,
    ):
        self.elevated_at = elevated_at
        self.critical_at = critical_at
        self.quarantine_at = quarantine_at
        self.last: ThreatAssessment | None = None
        self.quarantined = False
        self.hedge_history: list[dict[str, Any]] = []
        self._veto_count = 0
        # A hedge takes time to fill and to matter. Re-proposing one for the
        # same symbol on every tick produces a wall of duplicate intents, buries
        # the operator, and pays taker fees repeatedly for the same decision.
        self.hedge_cooldown_seconds = 60.0
        self._last_hedge: dict[str, float] = {}

    # -- threat model ------------------------------------------------------

    def assess(self, market, portfolio_state: dict, symbols: list[str]) -> ThreatAssessment:
        factors: list[dict[str, Any]] = []
        score = 0.0

        # 1. Velocity. A fast move is more dangerous than a large slow one.
        #    Several lookbacks are scanned because a one-tick gap down and a
        #    ten-tick grind are both dangerous, and a single window misses one
        #    of them: a gap rolls out of a long window within a few ticks.
        worst_velocity = 0.0
        worst_symbol = ""
        worst_window = 0
        for s in symbols:
            series = market.series(s, 24)
            for lookback in (2, 4, 8, 16):
                if len(series) <= lookback:
                    continue
                move = (series[-1] / series[-1 - lookback] - 1) * 100
                if move < worst_velocity:
                    worst_velocity, worst_symbol, worst_window = move, s, lookback
        if worst_velocity < -2.0:
            contribution = min(abs(worst_velocity) * 8.5, 62)
            score += contribution
            factors.append(
                {
                    "factor": "Price velocity",
                    "detail": f"{worst_symbol} fell {abs(worst_velocity):.1f}% over "
                    f"{worst_window} ticks",
                    "contribution": round(contribution, 1),
                }
            )

        # 2. Volatility expansion across the book.
        snapshot = market.snapshot()
        atrs = [snapshot.get(s, {}).get("atr_pct", 0.0) for s in symbols]
        avg_atr = sum(atrs) / len(atrs) if atrs else 0.0
        if avg_atr > 3.0:
            contribution = min((avg_atr - 3.0) * 7, 22)
            score += contribution
            factors.append(
                {
                    "factor": "Volatility expansion",
                    "detail": f"Average ATR across the book at {avg_atr:.1f}%",
                    "contribution": round(contribution, 1),
                }
            )

        # 3. Correlation. When everything moves together, diversification is a story.
        moves = []
        for s in symbols:
            series = market.series(s, 10)
            if len(series) >= 6:
                moves.append((series[-1] / series[-6] - 1))
        if len(moves) >= 3:
            negatives = sum(1 for m in moves if m < -0.004)
            if negatives >= len(moves) - 1 and negatives >= 3:
                score += 16
                factors.append(
                    {
                        "factor": "Correlation breakdown",
                        "detail": f"{negatives} of {len(moves)} assets falling together; "
                        f"diversification is not helping",
                        "contribution": 16.0,
                    }
                )

        # 4. Drawdown pressure on our own book.
        dd = portfolio_state.get("drawdown_pct", 0.0)
        if dd > 3.0:
            contribution = min(dd * 2.6, 25)
            score += contribution
            factors.append(
                {
                    "factor": "Portfolio drawdown",
                    "detail": f"{dd:.1f}% below peak equity",
                    "contribution": round(contribution, 1),
                }
            )

        # 5. Exposure. Leverage of conviction, if not of margin.
        gross_pct = portfolio_state.get("gross_exposure_pct", 0.0)
        if gross_pct > 45:
            contribution = min((gross_pct - 45) * 0.5, 14)
            score += contribution
            factors.append(
                {
                    "factor": "Gross exposure",
                    "detail": f"{gross_pct:.0f}% of equity is at risk in open positions",
                    "contribution": round(contribution, 1),
                }
            )

        score = min(score, 100.0)
        if score >= self.critical_at:
            level, rec = "critical", "Hedge exposure now and stop opening new risk."
        elif score >= self.elevated_at:
            level, rec = "elevated", "Cut new position sizes and tighten stops."
        else:
            level, rec = "normal", "Conditions are within tolerance."

        if score >= self.quarantine_at:
            self.quarantined = True
            rec = "Capital quarantined. No module may open positions until cleared."
        elif score < self.elevated_at and self.quarantined:
            self.quarantined = False
            rec = "Threat has receded; quarantine lifted."

        if not factors:
            factors.append(
                {
                    "factor": "Baseline",
                    "detail": "No elevated risk factors detected",
                    "contribution": 0.0,
                }
            )

        self.last = ThreatAssessment(score, level, factors, rec, time.time())
        return self.last

    def assess_from_klines(self, ctx, portfolio_state: dict, symbols: list[str]) -> ThreatAssessment:
        """
        Same threat model, driven by real Binance candles.

        Adds two factors the simulator could not express: a real volume spike
        (panic has a volume signature) and a real intrabar range expansion,
        which is how a wick that stops you out actually looks.
        """
        from ..indicators import atr_pct

        factors: list[dict[str, Any]] = []
        score = 0.0

        worst, worst_sym, worst_win = 0.0, "", 0
        atrs, moves = [], []

        for s_ in symbols:
            k = ctx.klines.get(s_)
            if not k or len(k) < 20:
                continue
            closes = [c["close"] for c in k]
            highs = [c["high"] for c in k]
            lows = [c["low"] for c in k]
            atrs.append(atr_pct(highs, lows, closes, 14))
            for lookback in (1, 3, 6, 12):
                if len(closes) <= lookback:
                    continue
                mv = (closes[-1] / closes[-1 - lookback] - 1) * 100
                if mv < worst:
                    worst, worst_sym, worst_win = mv, s_, lookback
            if len(closes) > 6:
                moves.append(closes[-1] / closes[-7] - 1)

        if worst < -2.0:
            c = min(abs(worst) * 8.5, 62)
            score += c
            factors.append({"factor": "Price velocity",
                            "detail": f"{worst_sym} fell {abs(worst):.1f}% over {worst_win} bars",
                            "contribution": round(c, 1)})

        avg_atr = sum(atrs) / len(atrs) if atrs else 0.0
        if avg_atr > 3.0:
            c = min((avg_atr - 3.0) * 7, 22)
            score += c
            factors.append({"factor": "Volatility expansion",
                            "detail": f"average ATR across the book at {avg_atr:.1f}%",
                            "contribution": round(c, 1)})

        if len(moves) >= 3:
            neg = sum(1 for m in moves if m < -0.004)
            if neg >= len(moves) - 1 and neg >= 3:
                score += 16
                factors.append({"factor": "Correlation breakdown",
                                "detail": f"{neg} of {len(moves)} assets falling together, "
                                          f"so diversification is not helping",
                                "contribution": 16.0})

        # Volume spike: a fall on heavy volume is distribution, on light volume noise.
        for s_ in symbols:
            k = ctx.klines.get(s_)
            if not k or len(k) < 40:
                continue
            vols = [c["volume"] for c in k]
            recent = sum(vols[-3:]) / 3
            base = sum(vols[-40:-3]) / max(len(vols[-40:-3]), 1)
            closes = [c["close"] for c in k]
            falling = closes[-1] < closes[-4]
            if base > 0 and recent > base * 2.5 and falling:
                c = min((recent / base - 2.5) * 6 + 8, 18)
                score += c
                factors.append({"factor": "Volume spike on weakness",
                                "detail": f"{s_} trading at {recent / base:.1f}x normal volume "
                                          f"while falling",
                                "contribution": round(c, 1)})
                break

        dd = portfolio_state.get("drawdown_pct", 0.0)
        if dd > 3.0:
            c = min(dd * 2.6, 25)
            score += c
            factors.append({"factor": "Portfolio drawdown",
                            "detail": f"{dd:.1f}% below peak equity",
                            "contribution": round(c, 1)})

        gross = portfolio_state.get("gross_exposure_pct", 0.0)
        if gross > 45:
            c = min((gross - 45) * 0.5, 14)
            score += c
            factors.append({"factor": "Gross exposure",
                            "detail": f"{gross:.0f}% of equity at risk in open positions",
                            "contribution": round(c, 1)})

        return self._finalise(min(score, 100.0), factors)

    def _finalise(self, score: float, factors: list) -> ThreatAssessment:
        if score >= self.critical_at:
            level, rec = "critical", "Hedge exposure now and stop opening new risk."
        elif score >= self.elevated_at:
            level, rec = "elevated", "Cut new position sizes and tighten stops."
        else:
            level, rec = "normal", "Conditions are within tolerance."

        if score >= self.quarantine_at:
            self.quarantined = True
            rec = "Capital quarantined. No module may open positions until cleared."
        elif score < self.elevated_at and self.quarantined:
            self.quarantined = False
            rec = "Threat has receded; quarantine lifted."

        if not factors:
            factors = [{"factor": "Baseline",
                        "detail": "No elevated risk factors detected",
                        "contribution": 0.0}]

        self.last = ThreatAssessment(score, level, factors, rec, time.time())
        return self.last

    # -- veto --------------------------------------------------------------

    def review(self, intent: Intent, assessment: ThreatAssessment) -> tuple[bool, str]:
        """
        The last gate before execution. Returns (approved, reason).

        Runs after the Constitution, so a trade must satisfy both the operator's
        written rules and live conditions the rules could not have anticipated.
        """
        if self.quarantined and intent.urgency != "critical":
            self._veto_count += 1
            return False, (
                f"Vetoed: capital is quarantined at threat score "
                f"{assessment.score:.0f}/100."
            )
        if assessment.level == "critical" and intent.to_state == CapitalState.DEPLOYED:
            self._veto_count += 1
            return False, (
                f"Vetoed: threat level is critical ({assessment.score:.0f}/100). "
                f"{assessment.factors[0]['detail']}."
            )
        if assessment.level == "elevated" and intent.notional_usd > 0:
            return True, (
                f"Approved with caution at threat {assessment.score:.0f}/100; "
                f"size should already be reduced."
            )
        return True, f"Approved; threat {assessment.score:.0f}/100 is within tolerance."

    # -- hedging -----------------------------------------------------------

    def build_hedge_intents(
        self, portfolio_state: dict, market, assessment: ThreatAssessment
    ) -> list[Intent]:
        """
        Reduce the largest exposures rather than flattening everything. A total
        liquidation on a wick is itself a way to lose money.
        """
        if assessment.level != "critical":
            return []

        positions = portfolio_state.get("positions", {})
        if not positions:
            return []

        ranked = sorted(
            positions.items(), key=lambda kv: -abs(kv[1].get("notional_usd", 0.0))
        )
        fraction = 0.6 if assessment.score < self.quarantine_at else 1.0
        intents: list[Intent] = []
        now = time.time()

        for symbol, pos in ranked[:3]:
            last = self._last_hedge.get(symbol)
            if last is not None and (now - last) < self.hedge_cooldown_seconds:
                continue
            notional = abs(pos.get("notional_usd", 0.0)) * fraction
            if notional < 10:
                continue
            self._last_hedge[symbol] = now
            intents.append(
                Intent(
                    intent_id=f"hedge-{uuid.uuid4().hex[:8]}",
                    ts=time.time(),
                    module="sentinel",
                    symbol=symbol,
                    side=Side.SELL,
                    notional_usd=notional,
                    order_type="MARKET",
                    limit_price=None,
                    from_state=CapitalState.DEPLOYED,
                    to_state=CapitalState.HEDGED,
                    thesis=(
                        f"Threat score {assessment.score:.0f}/100. "
                        f"{assessment.factors[0]['detail']}. Reducing {symbol} by "
                        f"{fraction:.0%} to cut exposure before conditions worsen."
                    ),
                    urgency="critical",
                    reference_price=pos.get("mark_price"),
                    meta={
                        "threat_score": assessment.score,
                        "factors": assessment.factors,
                        "reduce_fraction": fraction,
                    },
                )
            )

        if intents:
            self.hedge_history.append(
                {
                    "ts": time.time(),
                    "score": assessment.score,
                    "symbols": [i.symbol for i in intents],
                    "total_notional": sum(i.notional_usd for i in intents),
                }
            )
        return intents

    def status(self) -> dict[str, Any]:
        return {
            "assessment": self.last.to_dict() if self.last else None,
            "quarantined": self.quarantined,
            "veto_count": self._veto_count,
            "hedges": self.hedge_history[-10:],
            "thresholds": {
                "elevated": self.elevated_at,
                "critical": self.critical_at,
                "quarantine": self.quarantine_at,
            },
        }
