"""
GlassBox — the Constitution.

A machine-checkable rulebook the operator writes once, in YAML, that every
Intent must survive before it can become an order.

The point is inversion of trust. Most agent-trading demos ask a model to "be
careful". Here the model has no say: the Constitution is deterministic Python
evaluating declarative rules, it runs after the agents have spoken, and it can
shrink, block, or escalate an intent regardless of how confident the agents are.

Each rule returns one of three decisions:
    ALLOW          — proceed
    REQUIRE_HUMAN  — proceed only with an explicit operator confirmation
    DENY           — never; the intent dies here and is recorded as denied

The strictest decision across all rules wins. Rules are pure functions of
(intent, portfolio_state, market) so they are trivially unit-testable, and every
firing is written to the ledger with the rule name attached.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import Decision, Intent, Verdict

Severity = {Decision.ALLOW: 0, Decision.REQUIRE_HUMAN: 1, Decision.DENY: 2}


@dataclass
class RuleResult:
    decision: Decision
    reason: str
    adjusted_notional_usd: float | None = None


RuleFn = Callable[[Intent, dict, dict, dict], RuleResult | None]

_REGISTRY: dict[str, RuleFn] = {}


def rule(name: str) -> Callable[[RuleFn], RuleFn]:
    def deco(fn: RuleFn) -> RuleFn:
        _REGISTRY[name] = fn
        return fn

    return deco


# ---------------------------------------------------------------------------
# Rules. Signature: (intent, portfolio, market, cfg) -> RuleResult | None
# Returning None means "this rule has no opinion".
# ---------------------------------------------------------------------------


@rule("kill_switch")
def _kill_switch(intent, portfolio, market, cfg):
    if cfg.get("engaged"):
        return RuleResult(
            Decision.DENY, "Kill switch is engaged; no new risk may be taken."
        )
    return None


@rule("symbol_allowlist")
def _symbol_allowlist(intent, portfolio, market, cfg):
    allowed = cfg.get("symbols") or []
    if allowed and intent.symbol not in allowed:
        return RuleResult(
            Decision.DENY, f"{intent.symbol} is not on the approved symbol list."
        )
    return None


@rule("max_position_pct")
def _max_position_pct(intent, portfolio, market, cfg):
    """Cap a single position as a share of equity, shrinking rather than blocking."""
    if intent.meta.get("market") == "futures":
        # Futures notional is leveraged and lives in a separate book; it has
        # its own caps (max_leverage, futures_position_pct). Applying the spot
        # per-symbol cap here would wrongly trim a leveraged order against a
        # spot-sized ceiling.
        return None
    equity = max(portfolio.get("equity_usd", 0.0), 1e-9)
    cap_pct = float(cfg.get("pct", 25.0))
    existing = abs(portfolio.get("positions", {}).get(intent.symbol, {}).get("notional_usd", 0.0))
    projected = existing + intent.notional_usd
    cap_usd = equity * cap_pct / 100.0
    if projected > cap_usd:
        room = cap_usd - existing
        if room <= equity * 0.001:
            return RuleResult(
                Decision.DENY,
                f"{intent.symbol} is already at the {cap_pct:.0f}% position cap.",
            )
        return RuleResult(
            Decision.ALLOW,
            f"Trimmed to the {cap_pct:.0f}% single-position cap "
            f"(${intent.notional_usd:,.0f} → ${room:,.0f}).",
            adjusted_notional_usd=room,
        )
    return None


@rule("max_portfolio_concentration")
def _concentration(intent, portfolio, market, cfg):
    """Stop the whole spot book collapsing into one correlated bet."""
    if intent.meta.get("market") == "futures":
        return None  # futures gross is capped by futures_position_pct instead
    cap_pct = float(cfg.get("pct", 60.0))
    equity = max(portfolio.get("equity_usd", 0.0), 1e-9)
    gross = sum(
        abs(p.get("notional_usd", 0.0)) for p in portfolio.get("positions", {}).values()
    )
    projected = gross + intent.notional_usd
    if projected > equity * cap_pct / 100.0:
        return RuleResult(
            Decision.DENY,
            f"Gross exposure would reach {projected / equity * 100:.0f}% of equity, "
            f"over the {cap_pct:.0f}% ceiling.",
        )
    return None


@rule("max_leverage")
def _max_leverage(intent, portfolio, market, cfg):
    """
    Cap leverage on a futures open. Leverage is the single biggest amplifier of
    a bad decision, so this is a hard denial, not a trim — an operator who wants
    more raises the number in their own Constitution and it is written to the
    ledger when they do.
    """
    if intent.meta.get("market") != "futures":
        return None
    lev = float(intent.meta.get("leverage", 1) or 1)
    cap = float(cfg.get("max", 10))
    if lev > cap:
        return RuleResult(
            Decision.DENY,
            f"{lev:.0f}x leverage exceeds the {cap:.0f}x futures leverage cap.",
        )
    return None


@rule("futures_position_pct")
def _futures_position_pct(intent, portfolio, market, cfg):
    """
    Cap a single futures position's *notional* as a share of equity, trimming
    rather than blocking. A reduce-only close is never gated by a size cap —
    an operator can always cut leveraged risk.
    """
    if intent.meta.get("market") != "futures" or intent.meta.get("reduce_only"):
        return None
    equity = max(portfolio.get("equity_usd", 0.0), 1e-9)
    cap_pct = float(cfg.get("pct", 150.0))
    fut = portfolio.get("futures") or {}
    existing = abs(
        (fut.get("positions", {}).get(intent.symbol, {}) or {}).get("notional_usd", 0.0)
    )
    projected = existing + intent.notional_usd
    cap_usd = equity * cap_pct / 100.0
    if projected > cap_usd:
        room = cap_usd - existing
        if room <= equity * 0.01:
            return RuleResult(
                Decision.DENY,
                f"{intent.symbol} futures notional is already at the {cap_pct:.0f}% "
                f"of-equity cap.",
            )
        return RuleResult(
            Decision.ALLOW,
            f"Trimmed to the {cap_pct:.0f}% futures notional cap "
            f"(${intent.notional_usd:,.0f} → ${room:,.0f}).",
            adjusted_notional_usd=room,
        )
    return None


@rule("daily_loss_limit")
def _daily_loss(intent, portfolio, market, cfg):
    limit_pct = float(cfg.get("pct", 5.0))
    realised = portfolio.get("realised_pnl_today_usd", 0.0)
    equity = max(portfolio.get("starting_equity_today_usd", 1.0), 1e-9)
    loss_pct = -realised / equity * 100.0
    if realised < 0 and loss_pct >= limit_pct:
        return RuleResult(
            Decision.DENY,
            f"Down {loss_pct:.1f}% today, at the {limit_pct:.0f}% daily loss limit. "
            f"Trading is closed until the next session.",
        )
    return None


@rule("max_drawdown")
def _max_drawdown(intent, portfolio, market, cfg):
    limit_pct = float(cfg.get("pct", 15.0))
    peak = max(portfolio.get("peak_equity_usd", 0.0), 1e-9)
    equity = portfolio.get("equity_usd", 0.0)
    dd = (peak - equity) / peak * 100.0
    if dd >= limit_pct:
        return RuleResult(
            Decision.DENY,
            f"Drawdown from peak is {dd:.1f}%, past the {limit_pct:.0f}% circuit breaker.",
        )
    return None


@rule("trade_rate_limit")
def _rate_limit(intent, portfolio, market, cfg):
    max_per_hour = int(cfg.get("max_per_hour", 12))
    recent = portfolio.get("fills_last_hour", 0)
    if recent >= max_per_hour:
        return RuleResult(
            Decision.DENY,
            f"{recent} trades in the last hour hits the {max_per_hour} limit. "
            f"This is the anti-runaway guard.",
        )
    return None


@rule("min_confidence")
def _min_confidence(intent, portfolio, market, cfg):
    floor = float(cfg.get("threshold", 0.55))
    if not intent.signals:
        return None
    want = "bullish" if intent.side.value == "BUY" else "bearish"
    agree = [s for s in intent.signals if s.stance == want]
    if not agree:
        return RuleResult(Decision.DENY, "No analyst backs this direction.")
    conf = sum(s.confidence for s in agree) / len(agree)
    if conf < floor:
        return RuleResult(
            Decision.DENY,
            f"Council confidence {conf:.0%} is under the {floor:.0%} floor.",
        )
    return None


@rule("quorum")
def _quorum(intent, portfolio, market, cfg):
    """Require genuine agreement, not one loud analyst."""
    need = int(cfg.get("min_agreeing_analysts", 2))
    if not intent.signals:
        return None
    want = "bullish" if intent.side.value == "BUY" else "bearish"
    agreeing = sum(1 for s in intent.signals if s.stance == want)
    if agreeing < need:
        return RuleResult(
            Decision.DENY,
            f"Only {agreeing} of {len(intent.signals)} analysts agree; "
            f"{need} are required.",
        )
    return None


@rule("spread_guard")
def _spread(intent, portfolio, market, cfg):
    max_bps = float(cfg.get("max_bps", 25.0))
    bps = market.get(intent.symbol, {}).get("spread_bps", 0.0)
    if bps > max_bps:
        return RuleResult(
            Decision.DENY,
            f"Spread is {bps:.0f} bps, wider than the {max_bps:.0f} bps limit. "
            f"Thin books turn good ideas into bad fills.",
        )
    return None


@rule("volatility_guard")
def _volatility(intent, portfolio, market, cfg):
    """Halve size in violent markets rather than refusing outright."""
    trigger = float(cfg.get("atr_pct_trigger", 4.0))
    factor = float(cfg.get("size_multiplier", 0.5))
    atr = market.get(intent.symbol, {}).get("atr_pct", 0.0)
    if atr > trigger and intent.urgency != "critical":
        return RuleResult(
            Decision.ALLOW,
            f"Volatility at {atr:.1f}% exceeds {trigger:.1f}%; size cut to "
            f"{factor:.0%}.",
            adjusted_notional_usd=intent.notional_usd * factor,
        )
    return None


@rule("require_stop_loss")
def _require_stop(intent, portfolio, market, cfg):
    if not cfg.get("enabled", True):
        return None
    if intent.to_state.value == "DEPLOYED" and intent.stop_loss is None:
        return RuleResult(
            Decision.DENY, "Entries must carry a stop loss. None was attached."
        )
    return None


@rule("human_confirmation")
def _human(intent, portfolio, market, cfg):
    """
    Mirrors Binance's own confirm-before-execute posture, but with a threshold
    the operator sets, so routine rebalances don't train them to click yes.
    """
    equity = max(portfolio.get("equity_usd", 0.0), 1e-9)
    pct = intent.notional_usd / equity * 100.0
    always = cfg.get("always_confirm_above_pct", 10.0)
    if pct >= float(always):
        return RuleResult(
            Decision.REQUIRE_HUMAN,
            f"${intent.notional_usd:,.0f} is {pct:.1f}% of equity, above the "
            f"{always}% confirmation threshold.",
        )
    if intent.symbol in (cfg.get("always_confirm_symbols") or []):
        return RuleResult(
            Decision.REQUIRE_HUMAN, f"{intent.symbol} always requires confirmation."
        )
    return None


@rule("blackout_windows")
def _blackout(intent, portfolio, market, cfg):
    """
    Refuse to trade around scheduled macro events, when spreads gap and models
    are at their least reliable.

    Reads the portfolio's `now_ts` — the engine's Clock abstraction — rather
    than the real wall clock directly. In live trading these are the same
    value, so nothing changes there. But an isolated test engine's Clock can
    be simulated and disconnected from the real calendar entirely, and a rule
    that checked `time.gmtime()` directly would spuriously deny every trade
    whenever the drill or backtest happened to be *run* during a UTC window
    that has no relationship to the market conditions being simulated. This
    is the same class of bug `cooldown_after_loss` was already fixed for —
    this rule was simply missed at the time.
    """
    now = time.gmtime(portfolio.get("now_ts", time.time()))
    hhmm = now.tm_hour * 60 + now.tm_min
    for window in cfg.get("utc_windows") or []:
        start_h, start_m = map(int, window["start"].split(":"))
        end_h, end_m = map(int, window["end"].split(":"))
        if start_h * 60 + start_m <= hhmm <= end_h * 60 + end_m:
            return RuleResult(
                Decision.DENY,
                f"Inside the {window['start']}–{window['end']} UTC blackout "
                f"({window.get('label', 'scheduled')}).",
            )
    return None


@rule("cooldown_after_loss")
def _cooldown(intent, portfolio, market, cfg):
    minutes = float(cfg.get("minutes", 30))
    last = portfolio.get("last_losing_exit_ts")
    now = portfolio.get("now_ts", time.time())
    if last and (now - last) < minutes * 60:
        remaining = minutes - (now - last) / 60
        return RuleResult(
            Decision.DENY,
            f"Cooling off after a loss; {remaining:.0f} minutes remain. "
            f"This is the anti-revenge-trading rule.",
        )
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Constitution:
    def __init__(self, path: Path | str | None = None, doc: dict | None = None):
        self.path = Path(path) if path else None
        self.doc: dict[str, Any] = doc or {}
        if self.path and self.path.exists():
            self.reload()

    def reload(self) -> None:
        if self.path and self.path.exists():
            self.doc = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    @property
    def rules_config(self) -> dict[str, Any]:
        return self.doc.get("rules", {})

    def engage_kill_switch(self, on: bool = True) -> None:
        self.doc.setdefault("rules", {}).setdefault("kill_switch", {})["engaged"] = on

    def evaluate(self, intent: Intent, portfolio: dict, market: dict) -> Verdict:
        """
        Run every enabled rule. The strictest decision wins; size adjustments
        compound so two independent shrink rules both apply.
        """
        decision = Decision.ALLOW
        reasons: list[str] = []
        triggered: list[str] = []
        notional = intent.notional_usd

        for name, cfg in self.rules_config.items():
            fn = _REGISTRY.get(name)
            if fn is None or cfg is None:
                continue
            if isinstance(cfg, dict) and cfg.get("enabled") is False:
                continue
            try:
                result = fn(intent, portfolio, market, cfg if isinstance(cfg, dict) else {})
            except Exception as exc:  # a broken rule must fail closed
                decision = Decision.DENY
                reasons.append(f"Rule '{name}' failed to evaluate: {exc}")
                triggered.append(name)
                continue
            if result is None:
                continue
            triggered.append(name)
            reasons.append(f"[{name}] {result.reason}")
            if Severity[result.decision] > Severity[decision]:
                decision = result.decision
            if result.adjusted_notional_usd is not None:
                notional = min(notional, result.adjusted_notional_usd)

        return Verdict(
            decision=decision,
            reasons=reasons,
            triggered_rules=triggered,
            adjusted_notional_usd=None if notional == intent.notional_usd else notional,
        )

    def describe(self) -> list[dict[str, Any]]:
        """Rendered in the dashboard so the operator can read their own rules."""
        out = []
        for name, cfg in self.rules_config.items():
            out.append(
                {
                    "name": name,
                    "config": cfg,
                    "enabled": not (isinstance(cfg, dict) and cfg.get("enabled") is False),
                    "implemented": name in _REGISTRY,
                }
            )
        return out


def available_rules() -> list[str]:
    return sorted(_REGISTRY)
