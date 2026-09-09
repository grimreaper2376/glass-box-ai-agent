"""
GlassBox — the Council.

Fans out to four analysts, then resolves their disagreement into at most one
intent per symbol.

The resolution is not a vote average. Averaging is how multi-agent systems
launder a two-to-two split into false confidence. Instead:

* dissent is scored explicitly and shrinks position size
* a single analyst, however confident, cannot carry a decision
* the full transcript — including the losing arguments — is attached to the
  intent and survives into the ledger

That last point is the one that matters for a submission. When Binance says it
cannot see the agent's reasoning, this is the missing artifact.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from ..config import CapitalState, Intent, Side, Signal
from .analysts import ALL_ANALYSTS
from .analysts_sim import ALL_ANALYSTS as SIM_ANALYSTS


class Council:
    name = "council"
    # A verdict below this doesn't become an Intent at all — see propose_async's
    # default parameter of the same value below. Named here so the dashboard
    # can show the actual bar being cleared rather than a hardcoded guess,
    # and so "why hasn't anything traded" has a real number to point at
    # instead of looking like the system stopped working.
    MIN_CONVICTION = 0.3

    # Which independent method family each analyst belongs to. The point of a
    # council is that these read the market through genuinely different lenses;
    # three families agreeing is real confluence, whereas three analysts inside
    # one family agreeing is one idea counted three times. The synthesis step
    # (`_synthesise`) uses this to reward breadth and discount a verdict that
    # rests on a single family, which is the difference between a council that
    # deliberates and one that just averages votes.
    FAMILY = {
        "technical": "trend",
        "regime": "regime",
        "orderflow": "flow",
        "liquidity": "execution",
        "derivatives": "positioning",
        "sentiment": "sentiment",
        "onchain": "flow",
        "funding": "positioning",
    }

    def __init__(self, meter=None, base_size_pct: float = 9.0, calibration=None):
        self.analysts = [cls(meter) for cls in ALL_ANALYSTS]
        # The drill and simulator paths use seeded synchronous analysts: a
        # scripted flash crash has no order book to read, and the point of a
        # drill is to test the Guardian, not the data layer.
        self.sim_analysts = [cls(meter) for cls in SIM_ANALYSTS]
        self.base_size_pct = base_size_pct
        self.calibration = calibration
        self.last_transcript: dict[str, list[dict]] = {}

    def _weight(self, agent: str) -> float:
        """An analyst's vote is scaled by its measured track record."""
        if not self.calibration:
            return 1.0
        return self.calibration.reliability(agent)

    def _synthesise(self, signals, direction: str, conviction: float) -> tuple[float, list[str], str]:
        """
        Refine raw conviction into a judgement about *how well-founded* the
        verdict is, and explain it. Direction is never invented here — only the
        strength of an already-decided direction is adjusted:

        * breadth across independent method families raises conviction, because
          trend, flow, positioning and regime pointing the same way is far
          harder to dismiss than one indicator;
        * a verdict resting on a single family is discounted as fragile;
        * a strongly dissenting voice from a *different* family than the winners
          tempers conviction further, because genuine cross-method disagreement
          is exactly when a confident model is most likely wrong.

        Returns (adjusted_conviction, families, synthesis_sentence).
        """
        if direction == "neutral":
            return conviction, [], "No directional consensus to synthesise."

        winners = [s for s in signals if s.stance == direction and s.confidence >= 0.30]
        families = sorted({self.FAMILY.get(s.agent, "other") for s in winners})
        n_fam = len(families)

        if n_fam >= 3:
            mult, note = 1.15, (
                f"{n_fam} independent method families agree ({', '.join(families)}); "
                f"treated as genuine confluence."
            )
        elif n_fam == 1 and winners:
            mult, note = 0.78, (
                f"the entire {direction} case rests on one method family "
                f"({families[0]}); conviction discounted as fragile until a second "
                f"lens confirms it."
            )
        else:
            mult, note = 1.0, (
                f"{n_fam} method families support the {direction} read "
                f"({', '.join(families) or 'none'})."
            )

        # A confident opposing voice from a family not represented on the
        # winning side is the most informative kind of dissent.
        opp = direction == "bullish" and "bearish" or "bullish"
        cross = [
            s for s in signals
            if s.stance == opp and s.confidence >= 0.55
            and self.FAMILY.get(s.agent, "other") not in families
        ]
        if cross:
            mult *= 0.88
            note += (
                f" Tempered: {cross[0].agent} dissents from a different family "
                f"({self.FAMILY.get(cross[0].agent, 'other')}) at "
                f"{cross[0].confidence:.0%}."
            )

        return min(conviction * mult, 0.98), families, note

    async def deliberate_async(self, symbol: str, ctx):
        signals = [await a.analyse(symbol, ctx) for a in self.analysts]
        return signals, self._resolve(symbol, signals)

    async def propose_async(self, ctx, symbols, portfolio_state, min_conviction=None):
        min_conviction = self.MIN_CONVICTION if min_conviction is None else min_conviction
        """Real-data path. Same resolution logic, async analysts."""
        import time as _t, uuid as _u
        from ..config import CapitalState, Intent, Side

        intents, verdicts = [], []
        equity = portfolio_state.get("equity_usd", 0.0)

        for symbol in symbols:
            signals, verdict = await self.deliberate_async(symbol, ctx)
            verdicts.append(verdict)
            if verdict["direction"] == "neutral" or verdict["conviction"] < min_conviction:
                continue

            price = ctx.price(symbol)
            if price <= 0:
                continue

            held = portfolio_state.get("positions", {}).get(symbol)
            side = Side.BUY if verdict["direction"] == "bullish" else Side.SELL
            if side == Side.SELL and not held:
                continue  # spot only: it cannot open a short, so it cannot be liquidated

            atr = 1.0
            k = ctx.klines.get(symbol)
            if k and len(k) > 20:
                from ..indicators import atr_pct as _atr
                atr = _atr([c["high"] for c in k[-60:]], [c["low"] for c in k[-60:]],
                           [c["close"] for c in k[-60:]], 14)

            notional = self._size(equity, verdict["conviction"], verdict["dissent_ratio"], atr)
            if side == Side.SELL and held:
                notional = min(notional, abs(held.get("notional_usd", 0.0)))
            if notional < 15:
                continue

            # Stops scale with the asset's own volatility rather than a fixed
            # percentage: a 4.5% stop is noise on SOL and a disaster on BTC.
            stop_mult = max(1.6 * atr / 100, 0.02)
            winning = [x for x in signals if x.stance == verdict["direction"]]
            losing = [x for x in signals if x.stance not in (verdict["direction"], "neutral")]
            abstained = [x for x in signals
                         if x.evidence.get("data_quality") == "unavailable"]

            thesis = (
                f"{len(winning)} of {len(signals)} analysts are {verdict['direction']} on "
                f"{symbol} at {verdict['conviction']:.0%} conviction. "
                + " ".join(f"{x.agent}: {x.rationale}" for x in winning[:2])
            )
            if losing:
                thesis += (
                    f" Against it, {', '.join(x.agent for x in losing)}: {losing[0].rationale} "
                    f"Size cut {verdict['dissent_ratio'] * 80:.0f}% for the disagreement."
                )
            if abstained:
                thesis += (
                    f" {', '.join(x.agent for x in abstained)} abstained because required "
                    f"data was unavailable, so this decision was made partially blind."
                )

            intents.append(Intent(
                intent_id=f"cnc-{_u.uuid4().hex[:8]}", ts=_t.time(), module="council",
                symbol=symbol, side=side, notional_usd=round(notional, 2),
                order_type="MARKET", limit_price=None,
                from_state=CapitalState.IDLE if side == Side.BUY else CapitalState.DEPLOYED,
                to_state=CapitalState.DEPLOYED if side == Side.BUY else CapitalState.IDLE,
                thesis=thesis, signals=signals,
                stop_loss=round(price * (1 - stop_mult), 8) if side == Side.BUY else None,
                take_profit=round(price * (1 + stop_mult * 2), 8) if side == Side.BUY else None,
                reference_price=price,
                meta={"conviction": verdict["conviction"],
                      "dissent_ratio": verdict["dissent_ratio"],
                      "atr_pct": round(atr, 3),
                      "stop_distance_pct": round(stop_mult * 100, 2),
                      "blind_analysts": [x.agent for x in abstained],
                      "data_cost_usd": verdict["data_cost_usd"]},
            ))
        return intents, verdicts

    def _resolve(self, symbol: str, signals) -> dict:
        """Turn a set of signals into one directional verdict."""
        bulls = [x for x in signals if x.stance == "bullish"]
        bears = [x for x in signals if x.stance == "bearish"]
        neutrals = [x for x in signals if x.stance == "neutral"]
        blind = [x for x in signals if x.evidence.get("data_quality") == "unavailable"]

        bull_w = sum(x.confidence * self._weight(x.agent) for x in bulls)
        bear_w = sum(x.confidence * self._weight(x.agent) for x in bears)
        total = bull_w + bear_w

        families: list[str] = []
        synthesis = ""
        if total < 1e-9:
            direction, conviction, dissent = "neutral", 0.0, 0.0
        else:
            direction = "bullish" if bull_w > bear_w else "bearish"
            winner, loser = max(bull_w, bear_w), min(bull_w, bear_w)
            # An abstention counts as half a vote against acting, so a panel that
            # is mostly blind can never look like consensus.
            abstain_weight = 0.5 * len(neutrals)
            conviction = winner / max(winner + loser + abstain_weight, 1e-9)
            dissent = loser / winner if winner > 1e-9 else 1.0
            conviction, families, synthesis = self._synthesise(signals, direction, conviction)

        return {
            "symbol": symbol, "direction": direction,
            "conviction": round(conviction, 3), "dissent_ratio": round(dissent, 3),
            "bulls": [x.agent for x in bulls], "bears": [x.agent for x in bears],
            "abstained": [x.agent for x in neutrals],
            "blind": [x.agent for x in blind],
            "confluence_families": families,
            "synthesis": synthesis,
            "weights": {x.agent: round(self._weight(x.agent), 3) for x in signals},
            "data_cost_usd": round(sum(x.cost_usd for x in signals), 4),
            "transcript": [x.to_dict() for x in signals],
        }

    # -- deliberation ------------------------------------------------------

    def deliberate(self, symbol: str, market) -> tuple[list[Signal], dict[str, Any]]:
        signals = [a.analyse(symbol, market) for a in self.sim_analysts]

        bulls = [s for s in signals if s.stance == "bullish"]
        bears = [s for s in signals if s.stance == "bearish"]
        neutrals = [s for s in signals if s.stance == "neutral"]

        bull_weight = sum(s.confidence for s in bulls)
        bear_weight = sum(s.confidence for s in bears)
        total = bull_weight + bear_weight

        families: list[str] = []
        synthesis = ""
        if total < 1e-9:
            direction, conviction, dissent = "neutral", 0.0, 0.0
        else:
            direction = "bullish" if bull_weight > bear_weight else "bearish"
            winner = max(bull_weight, bear_weight)
            loser = min(bull_weight, bear_weight)
            # Share of voice: an abstention counts as half a vote against acting,
            # so four silent analysts can never look like consensus.
            abstain_weight = 0.5 * len(neutrals)
            conviction = winner / max(winner + loser + abstain_weight, 1e-9)
            dissent = loser / winner if winner > 1e-9 else 1.0
            conviction, families, synthesis = self._synthesise(signals, direction, conviction)

        verdict = {
            "symbol": symbol,
            "direction": direction,
            "conviction": round(conviction, 3),
            "dissent_ratio": round(dissent, 3),
            "bulls": [s.agent for s in bulls],
            "bears": [s.agent for s in bears],
            "abstained": [s.agent for s in neutrals],
            "confluence_families": families,
            "synthesis": synthesis,
            "data_cost_usd": round(sum(s.cost_usd for s in signals), 4),
            "transcript": [s.to_dict() for s in signals],
        }
        self.last_transcript[symbol] = verdict["transcript"]
        return signals, verdict

    # -- sizing ------------------------------------------------------------

    def _size(self, equity: float, conviction: float, dissent: float, atr_pct: float) -> float:
        """
        Size scales with conviction, shrinks with dissent, and shrinks again with
        volatility. Three independent brakes, all multiplicative.
        """
        base = equity * self.base_size_pct / 100
        conviction_factor = min(conviction * 1.6, 1.4)
        dissent_factor = max(1.0 - dissent * 0.8, 0.25)
        vol_factor = max(1.0 - max(atr_pct - 1.5, 0) * 0.12, 0.35)
        return base * conviction_factor * dissent_factor * vol_factor

    # -- proposals ---------------------------------------------------------

    def propose(
        self, market, symbols: list[str], portfolio_state: dict,
        min_conviction: float | None = None,
    ) -> tuple[list[Intent], list[dict[str, Any]]]:
        min_conviction = self.MIN_CONVICTION if min_conviction is None else min_conviction
        intents: list[Intent] = []
        verdicts: list[dict[str, Any]] = []
        equity = portfolio_state.get("equity_usd", 0.0)
        snapshot = market.snapshot()

        for symbol in symbols:
            signals, verdict = self.deliberate(symbol, market)
            verdicts.append(verdict)

            if verdict["direction"] == "neutral" or verdict["conviction"] < min_conviction:
                continue

            quote = snapshot.get(symbol, {})
            price = quote.get("price", 0.0)
            if price <= 0:
                continue

            held = portfolio_state.get("positions", {}).get(symbol)
            side = Side.BUY if verdict["direction"] == "bullish" else Side.SELL

            # Only sell what we hold. This is a spot system by design: it cannot
            # accidentally open a short and cannot be liquidated.
            if side == Side.SELL and not held:
                continue

            notional = self._size(
                equity, verdict["conviction"], verdict["dissent_ratio"], quote.get("atr_pct", 1.0)
            )
            if side == Side.SELL and held:
                notional = min(notional, abs(held.get("notional_usd", 0.0)))
            if notional < 15:
                continue

            winning = [
                s for s in signals if s.stance == verdict["direction"]
            ]
            losing = [
                s
                for s in signals
                if s.stance not in (verdict["direction"], "neutral")
            ]

            thesis = (
                f"{len(winning)} of {len(signals)} analysts are "
                f"{verdict['direction']} on {symbol} at {verdict['conviction']:.0%} "
                f"conviction. "
                + " ".join(f"{s.agent}: {s.rationale}" for s in winning[:2])
            )
            if losing:
                thesis += (
                    f" Dissent from {', '.join(s.agent for s in losing)}: "
                    f"{losing[0].rationale} Size reduced by "
                    f"{verdict['dissent_ratio'] * 80:.0f}% to reflect the disagreement."
                )

            intents.append(
                Intent(
                    intent_id=f"cnc-{uuid.uuid4().hex[:8]}",
                    ts=time.time(),
                    module="council",
                    symbol=symbol,
                    side=side,
                    notional_usd=round(notional, 2),
                    order_type="MARKET",
                    limit_price=None,
                    from_state=CapitalState.IDLE if side == Side.BUY else CapitalState.DEPLOYED,
                    to_state=CapitalState.DEPLOYED if side == Side.BUY else CapitalState.IDLE,
                    thesis=thesis,
                    signals=signals,
                    stop_loss=round(price * 0.955, 6) if side == Side.BUY else None,
                    take_profit=round(price * 1.09, 6) if side == Side.BUY else None,
                    reference_price=price,
                    meta={
                        "conviction": verdict["conviction"],
                        "dissent_ratio": verdict["dissent_ratio"],
                        "data_cost_usd": verdict["data_cost_usd"],
                    },
                )
            )

        return intents, verdicts

    def roster(self) -> list[dict]:
        return [
            {
                "name": a.name,
                "description": a.description,
                "requires": a.requires,
                "data_provider": a.data_provider or "binance public",
                "cost_per_call_usd": a.data_cost_usd,
                "reliability": round(self._weight(a.name), 3),
            }
            for a in self.analysts
        ]
