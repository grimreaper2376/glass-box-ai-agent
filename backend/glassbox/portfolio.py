"""
GlassBox — paper portfolio.

Simulated execution with fees, slippage and partial-fill behaviour close enough
to a real spot book that the numbers on the dashboard mean something.

This is also the accounting brain the Constitution reads from: daily realised
PnL, peak equity, fill counts, cooldown timestamps. Getting those right is what
lets the rules be strict without being arbitrary.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .config import CapitalState, Clock

TAKER_FEE_BPS = 7.5  # Binance spot taker, roughly, before BNB discount
SLIPPAGE_BPS = 3.0


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    state: CapitalState = CapitalState.DEPLOYED
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_ts: float = field(default_factory=time.time)
    hedge_qty: float = 0.0  # opposing leg size when the Guardian hedges
    # Which execution surface was active when this position opened. Modes
    # can be switched mid-session without closing what's already open, so a
    # position from paper mode can still be sitting on screen after a switch
    # to mock or live — worth knowing at a glance, not inferred from context.
    opened_mode: str = "paper"

    def notional(self, price: float) -> float:
        return self.qty * price

    def unrealised(self, price: float) -> float:
        return (price - self.avg_price) * self.qty

    def to_dict(self, price: float) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "qty": round(self.qty, 8),
            "avg_price": round(self.avg_price, 4),
            "mark_price": round(price, 4),
            "notional_usd": round(self.notional(price), 2),
            "unrealised_pnl_usd": round(self.unrealised(price), 2),
            "unrealised_pct": round(
                (price / self.avg_price - 1) * 100 if self.avg_price else 0.0, 3
            ),
            "state": self.state.value,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "hedge_qty": round(self.hedge_qty, 8),
            "age_minutes": round((time.time() - self.opened_ts) / 60, 1),
            # GlassBox is spot-only, so a held position is always a long — it
            # was bought, never sold short. Stated explicitly rather than
            # left for the operator to infer, since "I hold X" alone doesn't
            # say which direction that exposure runs.
            "side": "BUY",
            "opened_mode": self.opened_mode,
        }


@dataclass
class Fill:
    ts: float
    symbol: str
    side: str
    qty: float
    price: float
    fee_usd: float
    notional_usd: float
    realised_pnl_usd: float
    intent_id: str
    module: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "symbol": self.symbol,
            "side": self.side,
            "qty": round(self.qty, 8),
            "price": round(self.price, 4),
            "fee_usd": round(self.fee_usd, 4),
            "notional_usd": round(self.notional_usd, 2),
            "realised_pnl_usd": round(self.realised_pnl_usd, 2),
            "intent_id": self.intent_id,
            "module": self.module,
        }


class PaperPortfolio:
    def __init__(
        self,
        starting_equity_usd: float = 10_000.0,
        base: str = "USDT",
        clock: Clock | None = None,
    ):
        self.clock = clock or Clock()
        self.base = base
        self.cash = starting_equity_usd
        self.starting_equity = starting_equity_usd
        self.starting_equity_today = starting_equity_usd
        self.peak_equity = starting_equity_usd
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.realised_today = 0.0
        self.total_fees = 0.0
        self.last_losing_exit_ts: float | None = None
        self.equity_curve: deque[tuple[float, float]] = deque(maxlen=2000)
        self.yield_deployed_usd = 0.0
        self.yield_earned_usd = 0.0
        self._day = time.gmtime().tm_yday
        # Optional futures book sharing this cash pool. Stays None unless the
        # engine wires one in, so the spot-only construction used throughout
        # the tests behaves exactly as before — every method below that reads
        # it guards on `self.futures` first.
        self.futures = None

    # -- accounting --------------------------------------------------------

    def equity(self, marks: dict[str, float]) -> float:
        pos_value = sum(
            p.qty * marks.get(s, p.avg_price) for s, p in self.positions.items()
        )
        futures_value = self.futures.equity_contribution(marks) if self.futures else 0.0
        return (
            self.cash + pos_value + self.yield_deployed_usd
            + self.yield_earned_usd + futures_value
        )

    def roll_day_if_needed(self, marks: dict[str, float]) -> None:
        today = time.gmtime(self.clock.now()).tm_yday
        if today != self._day:
            self._day = today
            self.realised_today = 0.0
            self.starting_equity_today = self.equity(marks)

    def fills_last_hour(self) -> int:
        cutoff = self.clock.now() - 3600
        return sum(1 for f in self.fills if f.ts >= cutoff)

    # -- execution ---------------------------------------------------------

    def execute(
        self,
        symbol: str,
        side: str,
        notional_usd: float,
        quote_price: float,
        intent_id: str = "",
        module: str = "",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        state: CapitalState = CapitalState.DEPLOYED,
        force: bool = False,
        mode: str = "paper",
    ) -> Fill | None:
        """
        Fill at the touch plus slippage, charge taker fees, update the book.

        `force` is for mirroring a fill that already happened on Binance. The
        money has moved on the exchange, so refusing it locally would leave the
        two books permanently disagreeing — and the local book is what the
        Constitution reads when it sizes the next trade.
        """
        if notional_usd <= 0 or quote_price <= 0:
            return None

        slip = SLIPPAGE_BPS / 10000
        price = quote_price * (1 + slip) if side == "BUY" else quote_price * (1 - slip)
        qty = notional_usd / price
        fee = notional_usd * TAKER_FEE_BPS / 10000
        realised = 0.0

        pos = self.positions.get(symbol)

        if side == "BUY":
            if force:
                self.cash -= notional_usd + fee  # may go negative; reconciled below
            elif notional_usd + fee > self.cash:
                notional_usd = max(self.cash - fee, 0.0)
                if notional_usd <= 1.0:
                    return None
                qty = notional_usd / price
                fee = notional_usd * TAKER_FEE_BPS / 10000
                self.cash -= notional_usd + fee
            else:
                self.cash -= notional_usd + fee
            if pos and pos.qty > 0:
                total = pos.qty + qty
                pos.avg_price = (pos.avg_price * pos.qty + price * qty) / total
                pos.qty = total
                if stop_loss is not None:
                    pos.stop_loss = stop_loss
                if take_profit is not None:
                    pos.take_profit = take_profit
            else:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    qty=qty,
                    avg_price=price,
                    state=state,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_mode=mode,
                )
        else:  # SELL
            if not pos or pos.qty <= 0:
                if not force:
                    return None
                pos = self.positions.setdefault(
                    symbol, Position(symbol=symbol, qty=qty, avg_price=price, state=state)
                )
            qty = min(qty, pos.qty)
            notional_usd = qty * price
            fee = notional_usd * TAKER_FEE_BPS / 10000
            realised = (price - pos.avg_price) * qty - fee
            self.cash += notional_usd - fee
            pos.qty -= qty
            self.realised_today += realised
            if realised < 0:
                self.last_losing_exit_ts = self.clock.now()
            if pos.qty <= 1e-10:
                del self.positions[symbol]

        self.total_fees += fee
        fill = Fill(
            ts=self.clock.now(),
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            fee_usd=fee,
            notional_usd=notional_usd,
            realised_pnl_usd=realised,
            intent_id=intent_id,
            module=module,
        )
        self.fills.append(fill)
        return fill

    # -- protective exits --------------------------------------------------

    def check_protective_exits(self, marks: dict[str, float]) -> list[Fill]:
        """Stops and targets are checked every tick, before any agent runs."""
        out: list[Fill] = []
        for symbol, pos in list(self.positions.items()):
            price = marks.get(symbol)
            if not price or pos.qty <= 0:
                continue
            hit = None
            if pos.stop_loss and price <= pos.stop_loss:
                hit = "stop_loss"
            elif pos.take_profit and price >= pos.take_profit:
                hit = "take_profit"
            if hit:
                f = self.execute(
                    symbol,
                    "SELL",
                    pos.qty * price,
                    price,
                    intent_id=f"auto-{hit}",
                    module="risk",
                )
                if f:
                    out.append(f)
        return out

    # -- yield routing -----------------------------------------------------

    def deploy_to_yield(self, amount_usd: float) -> float:
        amount = min(amount_usd, self.cash)
        if amount <= 0:
            return 0.0
        self.cash -= amount
        self.yield_deployed_usd += amount
        return amount

    def recall_from_yield(self, amount_usd: float) -> float:
        amount = min(amount_usd, self.yield_deployed_usd)
        if amount <= 0:
            return 0.0
        self.yield_deployed_usd -= amount
        self.cash += amount
        return amount

    def accrue_yield(self, apy_pct: float, seconds: float) -> float:
        earned = self.yield_deployed_usd * (apy_pct / 100) * (seconds / 31_536_000)
        self.yield_earned_usd += earned
        return earned

    # -- snapshot for the Constitution and the UI --------------------------

    def state_dict(self, marks: dict[str, float]) -> dict[str, Any]:
        eq = self.equity(marks)
        self.peak_equity = max(self.peak_equity, eq)
        self.equity_curve.append((self.clock.now(), eq))
        spot_gross = sum(p.qty * marks.get(s, p.avg_price) for s, p in self.positions.items())
        # Fold futures notional into gross exposure so the Guardian's threat
        # model and the drawdown/exposure figures on the dashboard account for
        # leveraged risk, not just spot. Zero when no futures book is attached.
        futures_gross = self.futures.gross_notional(marks) if self.futures else 0.0
        gross = spot_gross + futures_gross
        return {
            "equity_usd": eq,
            "cash_usd": self.cash,
            "starting_equity_usd": self.starting_equity,
            "starting_equity_today_usd": self.starting_equity_today,
            "peak_equity_usd": self.peak_equity,
            "realised_pnl_today_usd": self.realised_today,
            "total_pnl_usd": eq - self.starting_equity,
            "total_pnl_pct": (eq / self.starting_equity - 1) * 100,
            "drawdown_pct": (self.peak_equity - eq) / max(self.peak_equity, 1e-9) * 100,
            "gross_exposure_usd": gross,
            "gross_exposure_pct": gross / max(eq, 1e-9) * 100,
            "spot_gross_exposure_usd": spot_gross,
            "futures_gross_exposure_usd": futures_gross,
            "total_fees_usd": self.total_fees,
            "fills_last_hour": self.fills_last_hour(),
            "fill_count": len(self.fills),
            "last_losing_exit_ts": self.last_losing_exit_ts,
            "now_ts": self.clock.now(),
            "yield_deployed_usd": self.yield_deployed_usd,
            "yield_earned_usd": self.yield_earned_usd,
            "positions": {
                s: p.to_dict(marks.get(s, p.avg_price)) for s, p in self.positions.items()
            },
            "futures": self.futures.state_dict(marks) if self.futures else None,
        }

    def performance(self, marks: dict[str, float]) -> dict[str, Any]:
        """Win rate, profit factor and a rough Sharpe, for the results panel."""
        closed = [f for f in self.fills if f.side == "SELL"]
        wins = [f for f in closed if f.realised_pnl_usd > 0]
        losses = [f for f in closed if f.realised_pnl_usd <= 0]
        gross_win = sum(f.realised_pnl_usd for f in wins)
        gross_loss = abs(sum(f.realised_pnl_usd for f in losses))

        curve = [e for _t, e in self.equity_curve]
        sharpe = 0.0
        if len(curve) > 3:
            rets = [
                (curve[i] - curve[i - 1]) / max(curve[i - 1], 1e-9)
                for i in range(1, len(curve))
            ]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / len(rets)
            sd = var**0.5
            sharpe = (mean / sd * (365 * 24 * 60) ** 0.5) if sd > 1e-12 else 0.0

        return {
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": len(wins) / len(closed) * 100 if closed else 0.0,
            "gross_profit_usd": gross_win,
            "gross_loss_usd": gross_loss,
            # No losing trades means the ratio is undefined, not zero. Report
            # None rather than float("inf"): Python serialises infinity as the
            # bare token `Infinity`, which is not valid JSON, and every browser
            # silently drops the whole websocket frame. The UI renders None as
            # an em dash.
            "profit_factor": (
                round(gross_win / gross_loss, 2) if gross_loss > 1e-9 else None
            ),
            "avg_win_usd": gross_win / len(wins) if wins else 0.0,
            "avg_loss_usd": gross_loss / len(losses) if losses else 0.0,
            "sharpe_annualised": round(sharpe, 2),
            "max_drawdown_pct": round(
                (self.peak_equity - min(curve or [self.peak_equity]))
                / max(self.peak_equity, 1e-9)
                * 100,
                2,
            ),
        }
