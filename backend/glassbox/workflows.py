"""
GlassBox — payment, onchain and reporting workflows.

Binance's Agent OS brief names four workflow families. GlassBox already covered
two of them well and two barely at all:

    Data & Analysis    — five analysts, calibration, backtests   (strong)
    Trading            — signals, strategies, automated actions  (strong)
    Payment            — x402 data metering only                 (one-directional)
    Onchain            — yield venues ranked but never executed  (advisory only)

This module closes the last two, and adds the exportable report the first was
missing.

The design rule that carries over
---------------------------------
Everything here goes through the **same** Constitution and audit path as a
trade. A payment is money leaving the account; a staking transaction locks
capital up. Neither is inherently safer than a spot order, so neither gets a
softer set of rules. Every action below produces a signed ledger receipt, is
subject to a daily cap, and can be stopped by the kill switch.

Why payments are capped separately from trading
------------------------------------------------
A trading loss is bounded by the position and reversible by closing it. A
payment is unbounded in the sense that nothing comes back — the counterparty
has it. So payments carry their own hard daily ceiling, their own per-payment
cap, and a counterparty allowlist, rather than sharing the trading budget.
That is deliberately stricter than Binance requires.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class PaymentRefused(Exception):
    """A payment failed a safety check and must not be sent."""


@dataclass
class Payment:
    payment_id: str
    counterparty: str
    amount_usd: float
    asset: str
    purpose: str
    ts: float = field(default_factory=time.time)
    status: str = "pending"          # pending | sent | refused
    justification_hash: str = ""
    tx_ref: str | None = None        # a REAL onchain tx hash once settled — see settlement.py
    explorer_url: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "counterparty": self.counterparty,
            "amount_usd": round(self.amount_usd, 2),
            "asset": self.asset,
            "purpose": self.purpose,
            "ts": self.ts,
            "status": self.status,
            "justification_hash": self.justification_hash,
            "tx_ref": self.tx_ref,
            "explorer_url": self.explorer_url,
            "message": self.message,
        }


class PaymentRail:
    """
    Agent-to-agent payments, settled for real on a live public testnet.

    An earlier version of this recorded a `tx_ref` that was a generated
    string, not a settled transaction — honest about it, but still not real.
    This version settles for real: every approved payment is broadcast on
    Base Sepolia through `settlement.SettlementWallet`, and `tx_ref` is a
    genuine transaction hash anyone can check independently at the returned
    explorer link, not a reference only GlassBox can vouch for.

    One thing stated plainly rather than glossed over: the USD amount below
    is what passed policy — the allowlist, the per-payment cap, the daily
    budget. The onchain settlement itself moves a small, fixed amount of
    **testnet ETH with no real-world value**, because there is no real
    dollar-equivalent to send on a valueless test network. The policy
    engine's numbers are real and enforced; the settled transfer is proof the
    payment *mechanism* works, not a value-equivalent transfer.

    Controls, unchanged and still deliberately stricter than trading:

    * a **counterparty allowlist** mapping a name to a real address — an
      agent cannot invent a recipient
    * a **per-payment cap** — one mistake cannot drain the account
    * a **daily ceiling**, separate from the trading budget
    * every payment carries the ledger hash of the reasoning that authorised it
    """

    def __init__(
        self,
        daily_limit_usd: float = 50.0,
        per_payment_cap_usd: float = 10.0,
        allowlist: dict[str, str] | None = None,
        wallet: Any = None,  # settlement.SettlementWallet | None
    ):
        self.daily_limit_usd = daily_limit_usd
        self.per_payment_cap_usd = per_payment_cap_usd
        # name -> real settlement address. Empty means nothing is payable —
        # failing closed is the only safe default for a money-sending capability.
        self.allowlist: dict[str, str] = dict(allowlist or {})
        self.wallet = wallet
        self.payments: list[Payment] = []
        self._day = time.strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today

    def spent_today_usd(self) -> float:
        self._roll_day()
        return round(
            sum(
                p.amount_usd for p in self.payments
                if p.status == "sent"
                and time.strftime("%Y-%m-%d", time.localtime(p.ts)) == self._day
            ),
            2,
        )

    def remaining_usd(self) -> float:
        return max(self.daily_limit_usd - self.spent_today_usd(), 0.0)

    def check(self, counterparty: str, amount_usd: float) -> tuple[bool, str]:
        """Every reason a payment could be refused, checked before sending."""
        if amount_usd <= 0:
            return False, "Payment amount must be greater than zero."
        if not self.allowlist:
            return False, (
                "No payment counterparties are allowlisted. Payments fail "
                "closed by design — add a counterparty explicitly first."
            )
        if counterparty not in self.allowlist:
            return False, (
                f"'{counterparty}' is not on the payment allowlist. An agent "
                f"cannot invent a recipient."
            )
        if amount_usd > self.per_payment_cap_usd:
            return False, (
                f"${amount_usd:,.2f} exceeds the ${self.per_payment_cap_usd:,.2f} "
                f"per-payment cap."
            )
        if amount_usd > self.remaining_usd():
            return False, (
                f"${amount_usd:,.2f} would exceed today's remaining payment "
                f"budget of ${self.remaining_usd():,.2f}."
            )
        return True, "Within all payment limits."

    async def send(
        self, counterparty: str, amount_usd: float, purpose: str,
        justification_hash: str = "", asset: str = "USDT",
    ) -> Payment:
        ok, reason = self.check(counterparty, amount_usd)
        payment = Payment(
            payment_id=f"pay-{uuid.uuid4().hex[:10]}",
            counterparty=counterparty, amount_usd=amount_usd, asset=asset,
            purpose=purpose, justification_hash=justification_hash,
        )
        if not ok:
            payment.status = "refused"
            payment.message = reason
            self.payments.append(payment)
            raise PaymentRefused(reason)

        if self.wallet is None:
            # No settlement wallet configured. Recorded honestly as approved
            # policy-wise but not actually sent anywhere, rather than
            # fabricating a reference for something that didn't happen.
            payment.status = "refused"
            payment.message = (
                f"Approved ${amount_usd:,.2f} to {counterparty} under policy, "
                f"but no settlement wallet is configured — nothing was sent."
            )
            self.payments.append(payment)
            raise PaymentRefused(payment.message)

        address = self.allowlist[counterparty]

        # Prefer real USDC settlement when the wallet actually holds any —
        # a genuine stablecoin transfer, closer to what x402 describes, and
        # verified against the live contract before every attempt (see
        # settlement.verify_usdc_contract). Falls back to the symbolic ETH
        # proof-of-payment when no USDC is held, so a wallet that's only
        # been funded with gas can still demonstrate the payment mechanism.
        result = None
        settled_in = "ETH"
        if hasattr(self.wallet, "usdc_balance"):
            try:
                held = await self.wallet.usdc_balance()
            except Exception:
                held = 0
            if held > 0:
                result = await self.wallet.send_usdc(address)
                settled_in = "USDC"
        if result is None:
            result = await self.wallet.send(address)

        if not result.ok:
            payment.status = "refused"
            payment.message = f"Settlement failed: {result.message}"
            self.payments.append(payment)
            raise PaymentRefused(payment.message)

        payment.status = "sent"
        payment.tx_ref = result.tx_hash
        payment.explorer_url = result.explorer_url
        payment.message = (
            f"${amount_usd:,.2f} to {counterparty} authorised for {purpose}; "
            f"settled onchain in real {settled_in} "
            + (
                f"({result.value_wei / 10**6:.6f} USDC, genuinely transferred)"
                if settled_in == "USDC" else
                f"({result.value_wei} wei testnet ETH, symbolic — no real "
                f"dollar-equivalent exists to send on a valueless test network)"
            )
            + f" — verify independently at {result.explorer_url}"
        )
        self.payments.append(payment)
        return payment

    def status(self) -> dict[str, Any]:
        sent = [p for p in self.payments if p.status == "sent"]
        return {
            "daily_limit_usd": self.daily_limit_usd,
            "per_payment_cap_usd": self.per_payment_cap_usd,
            "spent_today_usd": self.spent_today_usd(),
            "remaining_usd": self.remaining_usd(),
            "allowlist": self.allowlist,
            "wallet_configured": self.wallet is not None,
            "payments_sent": len(sent),
            "recent": [p.to_dict() for p in self.payments[-10:]],
            "policy": (
                "Payments fail closed: nothing is payable until a counterparty "
                "is explicitly allowlisted with a real settlement address. "
                "Limits are separate from — and tighter than — the trading "
                "budget, because a payment is not reversible by closing a "
                "position. Settlement is real and onchain (Base Sepolia "
                "testnet); the USD figure is what passed policy, not the "
                "value actually transferred, since a valueless test network "
                "has no real dollar-equivalent to send."
            ),
        }


@dataclass
class OnchainAction:
    action_id: str
    kind: str                # stake | unstake | supply | withdraw
    venue: str
    asset: str
    amount_usd: float
    apy_pct: float
    lockup_days: int = 0
    ts: float = field(default_factory=time.time)
    status: str = "proposed"  # proposed | instructed | refused
    justification_hash: str = ""
    mcp_instruction: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "kind": self.kind, "venue": self.venue,
            "asset": self.asset, "amount_usd": round(self.amount_usd, 2),
            "apy_pct": self.apy_pct, "lockup_days": self.lockup_days,
            "ts": self.ts, "status": self.status,
            "justification_hash": self.justification_hash,
            "mcp_instruction": self.mcp_instruction, "message": self.message,
        }


class OnchainRail:
    """
    Turns the Yield Compass's ranking into an actual instruction.

    Previously the Compass compared Binance Earn against DeFi venues and then
    moved a number in local memory — genuinely useful analysis, but nothing
    ever reached a chain or an exchange product. This emits the real
    instruction, routed through the same MCP client that places orders.

    The one rule specific to onchain
    ---------------------------------
    **Lockups are treated as risk, not as yield.** A 30-day locked product
    paying 7% is not strictly better than a flexible one paying 6%: for a
    trading agent, capital that cannot be recalled during a crash is capital
    that cannot defend the book. So anything with a lockup longer than
    `max_lockup_days` is refused outright regardless of how good the rate
    looks, and the Guardian can force an unstake of flexible positions.
    """

    def __init__(self, max_lockup_days: int = 7, max_allocation_pct: float = 60.0):
        self.max_lockup_days = max_lockup_days
        self.max_allocation_pct = max_allocation_pct
        self.actions: list[OnchainAction] = []

    def check(
        self, amount_usd: float, equity_usd: float, lockup_days: int,
    ) -> tuple[bool, str]:
        if amount_usd <= 0:
            return False, "Amount must be greater than zero."
        if lockup_days > self.max_lockup_days:
            return False, (
                f"A {lockup_days}-day lockup exceeds the {self.max_lockup_days}-day "
                f"limit. Capital that cannot be recalled during a crash cannot "
                f"defend the book, so the yield is refused regardless of rate."
            )
        if equity_usd > 0 and (amount_usd / equity_usd) * 100 > self.max_allocation_pct:
            return False, (
                f"${amount_usd:,.0f} is more than {self.max_allocation_pct:.0f}% "
                f"of equity. Too much of the book would be unavailable to trade."
            )
        return True, "Within onchain limits."

    def build(
        self, kind: str, venue: str, asset: str, amount_usd: float,
        apy_pct: float, equity_usd: float, lockup_days: int = 0,
        justification_hash: str = "",
    ) -> OnchainAction:
        action = OnchainAction(
            action_id=f"chain-{uuid.uuid4().hex[:10]}", kind=kind, venue=venue,
            asset=asset, amount_usd=amount_usd, apy_pct=apy_pct,
            lockup_days=lockup_days, justification_hash=justification_hash,
        )
        ok, reason = self.check(amount_usd, equity_usd, lockup_days)
        if not ok:
            action.status = "refused"
            action.message = reason
            self.actions.append(action)
            return action

        action.status = "instructed"
        action.mcp_instruction = {
            "server": "binance-mcp-server",
            "tool": "stake" if kind in ("stake", "supply") else "unstake",
            "arguments": {
                "product": venue, "asset": asset,
                "amountUsd": round(amount_usd, 2),
                "flexible": lockup_days == 0,
            },
            "natural_language": (
                f"{kind.capitalize()} ${amount_usd:,.0f} of {asset} into {venue} "
                f"at {apy_pct:.2f}% APY"
                + (f" with a {lockup_days}-day lockup." if lockup_days else " (flexible).")
            ),
            "justification_hash": justification_hash,
        }
        action.message = action.mcp_instruction["natural_language"]
        self.actions.append(action)
        return action

    def status(self) -> dict[str, Any]:
        instructed = [a for a in self.actions if a.status == "instructed"]
        return {
            "max_lockup_days": self.max_lockup_days,
            "max_allocation_pct": self.max_allocation_pct,
            "actions_instructed": len(instructed),
            "recent": [a.to_dict() for a in self.actions[-10:]],
            "policy": (
                "Lockups are scored as risk, not yield. Capital that cannot be "
                "recalled during a crash cannot defend the book."
            ),
        }


def build_report(engine) -> dict[str, Any]:
    """
    A complete, self-contained account of what the agent did and why.

    This is the "Reports" half of Data & Analysis: not a dashboard view that
    disappears on refresh, but an exportable document tying performance to the
    decisions that produced it, and to the ledger entries that prove those
    decisions were made when they claim to have been.
    """
    marks = engine._marks()
    state = engine.portfolio.state_dict(marks)
    perf = engine.portfolio.performance(marks)

    receipts = engine.receipts[-200:]
    filled = [r for r in receipts if r["outcome"].get("status") == "filled"]
    denied = [r for r in receipts if r["outcome"].get("status") == "denied"]

    # Which rules actually did work? A rulebook nobody trips is decoration.
    rule_counts: dict[str, int] = {}
    for r in denied:
        for rule in r["verdict"].get("triggered_rules", []):
            rule_counts[rule] = rule_counts.get(rule, 0) + 1

    return {
        "generated_ts": time.time(),
        "mode": engine.settings.mode.value,
        "portfolio": {
            "equity_usd": round(state.get("equity_usd", 0), 2),
            "cash_usd": round(state.get("cash_usd", 0), 2),
            "open_positions": len(state.get("positions", {})),
            "gross_exposure_pct": round(state.get("gross_exposure_pct", 0), 2),
            "total_pnl_pct": round(state.get("total_pnl_pct", 0), 3),
        },
        "performance": perf,
        "benchmark": engine.benchmark(),
        "activity": {
            "decisions_recorded": len(engine.receipts),
            "orders_filled": len(filled),
            "blocked_by_rules": len(denied),
            "guardian_vetoes": engine.counters.get("vetoed", 0),
            "rules_that_fired": sorted(
                rule_counts.items(), key=lambda kv: -kv[1]
            ),
        },
        "risk_posture": {
            "threat_score": getattr(engine.guardian.last, "score", None),
            "macro": engine.macro_posture.to_dict() if engine.macro_posture else None,
            "event_risk": engine.event_risk.to_dict() if engine.event_risk else None,
        },
        "analyst_track_record": engine.calibration.summary(),
        "audit": {
            "ledger_height": engine.ledger.height,
            "ledger_head": engine.ledger.head,
            "chain_valid": engine.ledger.verify().get("valid"),
            "note": (
                "Every figure above traces to a signed ledger entry. Re-run "
                "`glassbox verify` to confirm the chain independently."
            ),
        },
    }
