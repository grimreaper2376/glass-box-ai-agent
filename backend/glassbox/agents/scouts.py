"""
GlassBox — the two scouts.

NarrativeScout finds reasons to move capital out of idle. YieldCompass finds
somewhere useful for capital to sit while there are no reasons. Together they
close the capital lifecycle: money is never simply parked and forgotten, and it
is never chased into a position just because the system was bored.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import CapitalState, Intent, Side, Signal


# ---------------------------------------------------------------------------
# Narrative detection
# ---------------------------------------------------------------------------


@dataclass
class Narrative:
    key: str
    label: str
    strength: float  # 0..1
    velocity: float  # rate of change of attention
    symbols: list[str]
    evidence: list[str] = field(default_factory=list)
    first_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "strength": round(self.strength, 3),
            "velocity": round(self.velocity, 3),
            "symbols": self.symbols,
            "evidence": self.evidence,
            "age_minutes": round((time.time() - self.first_seen) / 60, 1),
        }


class NarrativeScout:
    """
    Clusters attention signals into named themes and scores how early we are.

    The useful question is not "is this narrative strong" but "is it strengthening
    faster than price has moved". A narrative already reflected in price is news;
    one accelerating ahead of price is an opportunity. That ratio is what the
    scout actually trades on.
    """

    name = "narrative"

    THEMES = {
        "ai_agents": ("AI agents and agentic finance", ["ETHUSDT", "SOLUSDT"]),
        "depin": ("DePIN and physical infrastructure", ["SOLUSDT"]),
        "rwa": ("Real-world asset tokenisation", ["ETHUSDT", "BNBUSDT"]),
        "l2_scaling": ("Layer-2 scaling and rollups", ["ETHUSDT"]),
        "btc_macro": ("Bitcoin as a macro asset", ["BTCUSDT"]),
        "defi_yield": ("DeFi yield and restaking", ["ETHUSDT", "BNBUSDT"]),
    }

    def __init__(self, meter=None, seed: int = 91, threshold: float = 0.62):
        self.meter = meter
        self.threshold = threshold
        self.narratives: dict[str, Narrative] = {}
        self._prev_strength: dict[str, float] = {}
        # Slow-moving per-theme attention state. A trading dashboard whose
        # narrative bars re-roll to a new random value on every three-second
        # tick reads as broken, not as a live market — attention does not
        # actually teleport between scans. So each theme carries its own
        # persistent attention level that drifts by a small, mean-reverting
        # step each scan, driven by an independent seeded generator per theme.
        # The result changes visibly over minutes, not violently every tick,
        # and velocity (the change between scans) becomes a real, readable
        # signal instead of noise.
        self._attention: dict[str, float] = {}
        self._chatter: dict[str, float] = {}
        self._dev: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}
        for key in self.THEMES:
            checksum = sum(ord(c) for c in key)
            r = random.Random(seed + checksum)
            self._rng[key] = r
            # A stable per-theme baseline the walk mean-reverts toward, so
            # different themes sit at genuinely different levels rather than
            # all hovering around one mean.
            self._chatter[key] = 0.30 + r.random() * 0.45
            self._dev[key] = 0.25 + r.random() * 0.40
            self._attention[key] = self._chatter[key] * 0.55 + self._dev[key] * 0.45

    def _step_attention(self, key: str) -> tuple[float, float, float]:
        """
        Advance one theme's attention by a small, bounded, mean-reverting
        step. Returns the current chatter, dev-activity and blended attention,
        all evolving slowly rather than being redrawn from scratch.
        """
        r = self._rng[key]
        # Gentle random walk with mild pull back toward the baseline, so a
        # theme wanders but never runs off to 0 or 1 and never jumps.
        for store, base, vol in (
            (self._chatter, 0.42, 0.020),
            (self._dev, 0.36, 0.016),
        ):
            cur = store[key]
            cur += r.gauss(0.0, vol) + (base - cur) * 0.05
            store[key] = max(0.05, min(0.95, cur))
        attention = self._chatter[key] * 0.55 + self._dev[key] * 0.45
        self._attention[key] = attention
        return self._chatter[key], self._dev[key], attention

    def scan(self, market, symbols: list[str], snapshot: dict | None = None) -> list[Narrative]:
        """
        Score every theme. `snapshot` lets the caller pass real market data
        (per-symbol 24h change) so narratives track genuine price action when
        live data is on; without it, the simulated market's snapshot is used.
        The social/developer component evolves as a slow per-theme walk (see
        `_step_attention`) so the numbers are stable between scans.
        """
        out: list[Narrative] = []
        snapshot = snapshot if snapshot is not None else market.snapshot()

        for key, (label, mapped) in self.THEMES.items():
            relevant = [s for s in mapped if s in symbols]
            if not relevant:
                continue

            price_move = sum(
                float(snapshot.get(s, {}).get("change_24h_pct", 0.0) or 0.0)
                for s in relevant
            ) / len(relevant)
            chatter, dev_activity, attention = self._step_attention(key)

            # Attention (slow-moving) is the backbone; realised price move is a
            # smaller, stable overlay. Neither component jumps between ticks.
            strength = max(
                0.0, min(1.0, attention * 0.75 + min(abs(price_move) / 12, 1.0) * 0.25)
            )
            prev = self._prev_strength.get(key, strength)
            velocity = strength - prev
            self._prev_strength[key] = strength

            evidence = [
                f"Social mention share {chatter:.0%} of the sampled window",
                f"Developer activity index {dev_activity:.0%}",
                f"Mapped assets moved {price_move:+.1f}% over 24h",
            ]

            n = Narrative(key, label, strength, velocity, relevant, evidence)
            if key in self.narratives:
                n.first_seen = self.narratives[key].first_seen
            self.narratives[key] = n
            out.append(n)

        return sorted(out, key=lambda n: -n.strength)

    def propose(self, market, symbols: list[str], sizing_usd: float) -> list[Intent]:
        """
        Only act when a narrative is both strong and still accelerating, and
        when price has not already run. Being early is the entire edge.
        """
        intents: list[Intent] = []
        snapshot = market.snapshot()

        # Reuse the scan the engine already ran this tick rather than scanning
        # again — a second scan would advance the attention walk twice and make
        # the numbers the operator sees disagree with the ones a trade acted on.
        ranked = sorted(self.narratives.values(), key=lambda n: -n.strength)
        for n in ranked:
            if n.strength < self.threshold or n.velocity <= 0.01:
                continue
            for symbol in n.symbols:
                move = snapshot.get(symbol, {}).get("change_24h_pct", 0.0)
                if move > 6.0:
                    continue  # already priced in; this is news, not an edge
                price = snapshot.get(symbol, {}).get("price", 0.0)
                if price <= 0:
                    continue
                intents.append(
                    Intent(
                        intent_id=f"narr-{uuid.uuid4().hex[:8]}",
                        ts=time.time(),
                        module="narrative",
                        symbol=symbol,
                        side=Side.BUY,
                        notional_usd=sizing_usd,
                        order_type="MARKET",
                        limit_price=None,
                        from_state=CapitalState.IDLE,
                        to_state=CapitalState.DEPLOYED,
                        thesis=(
                            f"'{n.label}' is at {n.strength:.0%} strength and still "
                            f"building ({n.velocity:+.0%} this scan), while {symbol} "
                            f"has only moved {move:+.1f}%. Attention is running ahead "
                            f"of price."
                        ),
                        stop_loss=price * 0.94,
                        take_profit=price * 1.12,
                        reference_price=price,
                        signals=[
                            Signal(
                                agent="narrative",
                                symbol=symbol,
                                stance="bullish",
                                confidence=n.strength,
                                rationale=" | ".join(n.evidence),
                                evidence=n.to_dict(),
                            )
                        ],
                        meta={"narrative": n.to_dict()},
                    )
                )
                break  # one expression per narrative, not a basket
        return intents

    def status(self) -> dict[str, Any]:
        ranked = sorted(self.narratives.values(), key=lambda n: -n.strength)
        return {
            "threshold": self.threshold,
            "narratives": [n.to_dict() for n in ranked],
            "emerging": [
                n.to_dict()
                for n in ranked
                if n.strength >= self.threshold and n.velocity > 0.01
            ],
        }


# ---------------------------------------------------------------------------
# Yield routing
# ---------------------------------------------------------------------------


@dataclass
class YieldVenue:
    venue: str
    kind: str  # binance_earn | defi
    asset: str
    apy_pct: float
    liquidity_usd: float
    lockup_days: int
    risk_score: float  # 0..1, higher is riskier

    def risk_adjusted_apy(self, gas_cost_usd: float, amount_usd: float) -> float:
        """APY net of a risk haircut and of the cost of getting in and out."""
        gas_drag = (gas_cost_usd * 2) / max(amount_usd, 1.0) * 100 * (365 / 30)
        return self.apy_pct * (1 - self.risk_score * 0.55) - gas_drag

    def to_dict(self, amount_usd: float = 1000.0) -> dict[str, Any]:
        gas = 0.0 if self.kind == "binance_earn" else 2.4
        return {
            "venue": self.venue,
            "kind": self.kind,
            "asset": self.asset,
            "apy_pct": round(self.apy_pct, 2),
            "risk_adjusted_apy_pct": round(self.risk_adjusted_apy(gas, amount_usd), 2),
            "liquidity_usd": self.liquidity_usd,
            "lockup_days": self.lockup_days,
            "risk_score": round(self.risk_score, 2),
        }


class YieldCompass:
    """
    Routes idle cash to the best risk-adjusted home, and — this is the part that
    matters — hands it straight back when a conviction trade needs it.

    Comparing a CEX product against a DeFi pool in the same calculation is only
    possible because Agent OS exposes both surfaces to one agent.
    """

    name = "compass"

    VENUES = [
        YieldVenue("Binance Simple Earn (Flexible)", "binance_earn", "USDT", 4.8, 5e8, 0, 0.05),
        YieldVenue("Binance Simple Earn (30d Locked)", "binance_earn", "USDT", 7.2, 2e8, 30, 0.08),
        YieldVenue("Binance BNB Staking", "binance_earn", "BNB", 5.6, 9e7, 7, 0.18),
        YieldVenue("Aave v3 USDC", "defi", "USDC", 6.1, 8e8, 0, 0.22),
        YieldVenue("Curve 3pool", "defi", "USDT", 5.4, 3e8, 0, 0.28),
        YieldVenue("Pendle PT-sUSDe", "defi", "USDe", 11.9, 4e7, 0, 0.52),
    ]

    def __init__(self, min_idle_usd: float = 500.0, reserve_pct: float = 20.0):
        self.min_idle_usd = min_idle_usd
        self.reserve_pct = reserve_pct  # always keep dry powder for the Council
        self.current_venue: YieldVenue | None = None

    def rank(self, amount_usd: float, max_risk: float = 0.35) -> list[YieldVenue]:
        eligible = [v for v in self.VENUES if v.risk_score <= max_risk]
        return sorted(
            eligible,
            key=lambda v: -v.risk_adjusted_apy(0.0 if v.kind == "binance_earn" else 2.4, amount_usd),
        )

    def plan(self, portfolio_state: dict, max_risk: float = 0.35) -> dict[str, Any]:
        cash = portfolio_state.get("cash_usd", 0.0)
        equity = max(portfolio_state.get("equity_usd", 1.0), 1e-9)
        reserve = equity * self.reserve_pct / 100
        deployable = max(cash - reserve, 0.0)

        if deployable < self.min_idle_usd:
            return {
                "action": "hold",
                "reason": (
                    f"${deployable:,.0f} of deployable cash is under the "
                    f"${self.min_idle_usd:,.0f} minimum. Moving it would cost more "
                    f"in friction than it earns."
                ),
                "deployable_usd": round(deployable, 2),
                "reserve_usd": round(reserve, 2),
                "ranked": [v.to_dict(max(deployable, 1000)) for v in self.rank(max(deployable, 1000), max_risk)],
            }

        ranked = self.rank(deployable, max_risk)
        best = ranked[0]
        self.current_venue = best
        gas = 0.0 if best.kind == "binance_earn" else 2.4
        return {
            "action": "deploy",
            "venue": best.to_dict(deployable),
            "amount_usd": round(deployable, 2),
            "reserve_usd": round(reserve, 2),
            "deployable_usd": round(deployable, 2),
            "expected_annual_usd": round(
                deployable * best.risk_adjusted_apy(gas, deployable) / 100, 2
            ),
            "reason": (
                f"{best.venue} pays {best.apy_pct:.1f}% headline, "
                f"{best.risk_adjusted_apy(gas, deployable):.1f}% after a risk haircut "
                f"and round-trip costs. Best of {len(ranked)} venues checked across "
                f"Binance Earn and DeFi."
            ),
            "ranked": [v.to_dict(deployable) for v in ranked],
        }

    def status(self, portfolio_state: dict) -> dict[str, Any]:
        return {
            "min_idle_usd": self.min_idle_usd,
            "reserve_pct": self.reserve_pct,
            "current_venue": self.current_venue.to_dict() if self.current_venue else None,
            "deployed_usd": portfolio_state.get("yield_deployed_usd", 0.0),
            "earned_usd": round(portfolio_state.get("yield_earned_usd", 0.0), 4),
            "plan": self.plan(portfolio_state),
        }
