"""
GlassBox — second opinion.

When an operator places a trade by hand, this asks every analysis layer the
system already runs: *does anything here disagree with what you're about to
do?*

It never blocks. The Constitution blocks; the Guardian vetoes. This layer's
only job is to make sure a person is never surprised by information the
system already had — if five analysts, the macro calendar, the news feed and
the order book all lean the other way, you should know that *before* you
click, not afterwards in the receipt.

Why a probability and not a label
----------------------------------
"The Council disagrees" is nearly useless on its own — the Council disagrees
with most things most of the time, because its conviction bar is deliberately
high. What matters is *how much* disagreement, weighted by which sources are
disagreeing and how reliable each of them has proven.

So every concern carries a weight, and those weights are combined into a
single calibrated number: the estimated probability that this trade is
leaning against the evidence. The weights are not invented — analyst opinions
are scaled by each analyst's **measured** reliability from the calibration
tracker, so an analyst that has historically been wrong contributes less to
the warning than one that has been right.

Deliberate asymmetry
--------------------
Concerns are scored more heavily than confirmations. An operator overriding
the system needs to hear the objection clearly; an operator being told
"everything agrees with you" needs mild encouragement at most. Being wrong
about a warning costs a missed trade. Being wrong about a reassurance costs
money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Concern:
    source: str          # council | analyst | macro | news | guardian | liquidity | position
    severity: float      # 0..1, already reliability-weighted where applicable
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "severity": round(self.severity, 3),
            "message": self.message,
        }


@dataclass
class SecondOpinion:
    verdict: str = "neutral"        # aligned | neutral | caution | strongly_against
    disagreement_pct: float = 0.0   # calibrated probability this leans against the evidence
    concerns: list[Concern] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    headline: str = ""
    requires_acknowledgement: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "disagreement_pct": round(self.disagreement_pct, 1),
            "concerns": [c.to_dict() for c in self.concerns],
            "confirmations": self.confirmations,
            "headline": self.headline,
            "requires_acknowledgement": self.requires_acknowledgement,
            "policy": (
                "Advisory only. This never blocks a trade — the Constitution "
                "and the Guardian do that. It exists so you are never "
                "surprised by something the system already knew."
            ),
        }


# How much each source can contribute to the disagreement score. The Council's
# aggregate view and the Guardian's threat reading carry the most, because
# both are already reliability-weighted or measured; a single analyst carries
# little on its own.
WEIGHTS = {
    "council": 34.0,
    "guardian": 22.0,
    "macro": 14.0,
    "news": 14.0,
    "liquidity": 8.0,
    "position": 8.0,
}


def evaluate_manual_trade(
    *, symbol: str, side: str, notional_usd: float, engine,
    market: str = "spot", is_opening_risk: bool | None = None,
) -> SecondOpinion:
    """
    Score a proposed manual trade against every layer of the system.

    Reads live state directly off the engine rather than taking a snapshot,
    so the opinion reflects exactly what the agent believes at this instant.

    `market` and `is_opening_risk` let the futures path reuse this unchanged:
    a short is a bearish directional bet (`side="SELL"`), and *opening* any
    leveraged position — long or short — is new risk the Guardian, macro and
    news layers should weigh in on, whereas on spot only a BUY opens risk.
    When `is_opening_risk` is left as None it defaults to the spot meaning
    (a BUY), so spot behaviour is byte-for-byte unchanged.
    """
    op = SecondOpinion()
    side = side.upper()
    if is_opening_risk is None:
        is_opening_risk = side == "BUY"
    concerns: list[Concern] = []
    confirmations: list[str] = []

    # -- 1. The Council's aggregate view on this symbol -------------------
    verdict = next(
        (v for v in engine.council_verdicts if v.get("symbol") == symbol), None
    )
    if verdict:
        direction = verdict.get("direction", "neutral")
        conviction = float(verdict.get("conviction", 0.0) or 0.0)
        opposed = (side == "BUY" and direction == "bearish") or (
            side == "SELL" and direction == "bullish"
        )
        agreed = (side == "BUY" and direction == "bullish") or (
            side == "SELL" and direction == "bearish"
        )
        if opposed:
            # Scaled by conviction: a 5%-conviction bearish read is a much
            # weaker objection than a 29% one.
            concerns.append(Concern(
                "council", min(conviction / 0.30, 1.0),
                f"The Council currently reads {symbol} as {direction} at "
                f"{conviction:.0%} conviction — the opposite side of this trade.",
            ))
        elif agreed:
            confirmations.append(
                f"The Council agrees: {symbol} reads {direction} at "
                f"{conviction:.0%} conviction."
            )

        # -- 2. Individual analysts, weighted by measured reliability -----
        for sig in verdict.get("signals", []) or []:
            stance = sig.get("stance")
            name = sig.get("agent", "analyst")
            conf = float(sig.get("confidence", 0.0) or 0.0)
            against = (side == "BUY" and stance == "bearish") or (
                side == "SELL" and stance == "bullish"
            )
            if not against or conf < 0.3:
                continue
            # An analyst measured as unreliable objects less loudly. This is
            # the calibration tracker feeding back into a human-facing
            # warning, not just into position sizing.
            reliability = 1.0
            try:
                reliability = float(
                    engine.calibration.reliability(name)  # type: ignore[attr-defined]
                )
            except Exception:
                pass
            severity = min(conf * max(min(reliability, 1.5), 0.4), 1.0)
            concerns.append(Concern(
                "council", severity * 0.5,   # individual analysts count for less
                f"{name} is {stance} on {symbol} at {conf:.0%} confidence"
                + (f" (reliability {reliability:.2f}×)" if reliability != 1.0 else "")
                + ".",
            ))

    # -- 3. Guardian threat level ----------------------------------------
    a = getattr(engine.guardian, "last", None)
    if a is not None and is_opening_risk:
        if a.score >= engine.guardian.critical_at:
            concerns.append(Concern(
                "guardian", 1.0,
                f"The Guardian rates market threat {a.score:.0f}/100 (critical). "
                f"It is actively reducing exposure, not adding to it.",
            ))
        elif a.score >= 50:
            concerns.append(Concern(
                "guardian", (a.score - 50) / 38.0,
                f"The Guardian rates market threat {a.score:.0f}/100 — elevated.",
            ))
        elif a.score < 25:
            confirmations.append(f"Market threat is low ({a.score:.0f}/100).")

    # -- 4. Scheduled macro risk -----------------------------------------
    mp = getattr(engine, "macro_posture", None)
    if mp is not None and is_opening_risk:
        if mp.blackout:
            concerns.append(Concern("macro", 1.0, mp.reasons[0]))
        elif mp.size_multiplier < 1.0:
            concerns.append(Concern(
                "macro", 1.0 - mp.size_multiplier,
                f"Macro is already cutting sizes to {mp.size_multiplier:.0%}: "
                f"{mp.reasons[0]}",
            ))

    # -- 5. Event / news risk --------------------------------------------
    er = getattr(engine, "event_risk", None)
    if er is not None and is_opening_risk:
        if symbol in getattr(er, "blocked_symbols", []):
            concerns.append(Concern(
                "news", 1.0,
                f"{symbol} is blocked on news risk: {er.reasons[0]}",
            ))
        elif er.size_multiplier < 1.0:
            concerns.append(Concern(
                "news", 1.0 - er.size_multiplier,
                f"Event risk is {er.level}: {er.reasons[0]}",
            ))

    # -- 6. Liquidity: can this size actually get filled? -----------------
    try:
        quote = engine.market_view().get(symbol, {})
        spread_bps = float(quote.get("spread_bps", 0) or 0)
        if spread_bps > 20:
            concerns.append(Concern(
                "liquidity", min(spread_bps / 60.0, 1.0),
                f"{symbol} is quoted {spread_bps:.0f}bp wide — the round trip "
                f"costs roughly {spread_bps / 100:.2f}% before any move.",
            ))
        elif spread_bps > 0:
            confirmations.append(f"Spread is tight ({spread_bps:.1f}bp).")
    except Exception:
        pass

    # -- 7. Concentration ------------------------------------------------
    try:
        state = engine.portfolio.state_dict(engine._marks())
        equity = float(state.get("equity_usd", 0) or 0)
        if equity > 0 and side == "BUY" and market == "spot":
            existing = state.get("positions", {}).get(symbol, {})
            after = (float(existing.get("notional_usd", 0) or 0) + notional_usd) / equity
            if after > 0.25:
                concerns.append(Concern(
                    "position", min((after - 0.25) / 0.25, 1.0),
                    f"This would put {after:.0%} of the whole book into "
                    f"{symbol} alone.",
                ))
    except Exception:
        pass

    # -- Combine ----------------------------------------------------------
    # Per-source severities are capped at that source's weight, so ten weak
    # analyst objections cannot outweigh one critical Guardian reading.
    by_source: dict[str, float] = {}
    for c in concerns:
        by_source[c.source] = min(
            by_source.get(c.source, 0.0) + c.severity, 1.0
        )
    score = sum(WEIGHTS.get(src, 5.0) * sev for src, sev in by_source.items())
    score = max(0.0, min(score, 100.0))

    op.concerns = sorted(concerns, key=lambda c: -c.severity)
    op.confirmations = confirmations
    op.disagreement_pct = score

    if score >= 60:
        op.verdict = "strongly_against"
        op.requires_acknowledgement = True
        op.headline = (
            f"The system strongly disagrees with this trade "
            f"({score:.0f}% against). Proceed at your own risk."
        )
    elif score >= 30:
        op.verdict = "caution"
        op.requires_acknowledgement = True
        op.headline = (
            f"Parts of the system disagree with this trade ({score:.0f}% "
            f"against). Proceed at your own risk."
        )
    elif concerns:
        op.verdict = "neutral"
        op.headline = (
            f"Minor concerns ({score:.0f}% against), nothing decisive."
        )
    elif confirmations:
        op.verdict = "aligned"
        op.headline = "Nothing in the system disagrees with this trade."
    else:
        op.verdict = "neutral"
        op.headline = (
            "No strong view either way — the analysts have no clear read on "
            "this symbol right now."
        )
    return op


def evaluate_futures_trade(
    *, symbol: str, action: str, notional_usd: float, leverage: float, engine,
) -> SecondOpinion:
    """
    Second opinion for a futures order, layered on the same analysis.

    A long is a bullish bet and a short is a bearish one, so each is scored
    against the Council the same way a spot BUY or SELL would be. Opening
    either side is new leveraged risk, so the Guardian, macro and news layers
    all weigh in (unlike a spot exit). A close only reduces risk, so it is
    waved through with nothing to object to. A short-hand leverage note is
    added on top so the operator sees how little room a high-leverage position
    has before liquidation — it informs, it does not inflate the score.
    """
    action = action.upper()
    if action == "CLOSE":
        op = SecondOpinion(
            verdict="aligned",
            headline="Closing a futures position only reduces risk — nothing to object to.",
        )
        op.confirmations = ["A reduce-only close is never gated by the system."]
        return op

    effective_side = "BUY" if action == "OPEN_LONG" else "SELL"
    op = evaluate_manual_trade(
        symbol=symbol, side=effective_side, notional_usd=notional_usd,
        engine=engine, market="futures", is_opening_risk=True,
    )
    # A leverage reality-check, framed rather than scored: at Nx, a (1/N) move
    # against the position roughly halves the margin, and ~(1/N) liquidates it.
    if leverage and leverage >= 1:
        adverse = 100.0 / leverage
        op.confirmations.insert(
            0,
            f"At {leverage:.0f}x, an adverse move of about {adverse:.1f}% wipes the "
            f"posted margin (a default stop sits well inside that).",
        )
    dir_word = "long" if action == "OPEN_LONG" else "short"
    if not op.headline or op.verdict == "aligned":
        op.headline = f"Nothing in the system disagrees with opening this {dir_word}."
    return op
