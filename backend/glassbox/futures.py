"""
GlassBox — paper futures book (USDⓈ-M perpetuals).

The spot portfolio in `portfolio.py` is long-only by construction: you can buy
a coin and later sell what you hold, never less than zero. That is the right
model for spot and it is deliberately left exactly as it was. Futures are a
different instrument — you can be long *or* short, you post margin rather than
the full notional, and a position can be liquidated. Rather than bolt signed
quantities and leverage onto the spot book and risk quietly changing how spot
P&L, stops and the Constitution's exposure math already behave, this is a
separate accounting object that sits alongside it.

The two books share one thing: cash. Opening a futures position moves margin
out of the same `PaperPortfolio.cash` a spot buy would spend, and closing it
returns that margin plus or minus the realised result. So total account equity
stays a single coherent number, and the Guardian and the daily-loss rule see
futures risk exactly as they see spot risk.

What is modelled, and honestly
------------------------------
* **Isolated margin.** Each position posts `notional / leverage` as margin, and
  that margin is the most it can lose — a liquidation wipes the position's
  margin and nothing more, never the rest of the account.
* **Long and short.** A long gains when price rises; a short gains when price
  falls. P&L, the liquidation price and the stop/target checks all flip sign
  with the side.
* **Liquidation.** Checked every tick against a maintenance-margin estimate,
  before any analyst runs, the same priority the spot stop-loss already has.
* **Taker fees and funding.** A taker fee is charged on the notional at open
  and at close; funding accrues slowly while a position is held (longs pay when
  funding is positive, shorts receive it, and vice versa).

What it is not: a matching engine or a real derivatives clearer. Fills are at
the mark plus a small slippage, the maintenance-margin rate is a single
conservative constant rather than Binance's tiered schedule, and — like the
spot book — nothing here settles on a real exchange unless the mode is `mock`
or `live`, in which case the order goes out through the same MCP client the
spot path uses (see `execution.py`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import Clock

TAKER_FEE_BPS = 4.5   # Binance USDⓈ-M taker, roughly, before any discount
SLIPPAGE_BPS = 3.0
MAINT_MARGIN_RATE = 0.005   # 0.5% — a single conservative constant, not the tiered schedule
DEFAULT_MAX_LEVERAGE = 20.0
# Funding settles three times a day on Binance; modelled as a small continuous
# accrual so a held perp carries the cost of carry rather than being free.
FUNDING_INTERVAL_SECONDS = 8 * 3600.0
DEFAULT_FUNDING_BPS = 1.0   # per 8h interval; longs pay, shorts receive when positive


def _liquidation_price(side: str, entry: float, leverage: float, maint: float) -> float:
    """
    Isolated-margin liquidation estimate. A long is liquidated when the loss
    eats the posted margin down to the maintenance floor; a short when the same
    happens on the way up. Fees are ignored here — this is a guard rail, not a
    settlement figure, and erring slightly early is the safe direction.
    """
    if leverage <= 0:
        return 0.0
    if side == "LONG":
        return max(entry * (1 - 1 / leverage + maint), 0.0)
    return entry * (1 + 1 / leverage - maint)


@dataclass
class FuturesPosition:
    symbol: str
    side: str                 # LONG | SHORT
    qty: float                # base units, always > 0
    entry_price: float
    leverage: float
    margin_usd: float
    stop_loss: float | None = None
    take_profit: float | None = None
    liq_price: float = 0.0
    opened_ts: float = field(default_factory=time.time)
    opened_mode: str = "paper"
    fees_usd: float = 0.0
    funding_paid_usd: float = 0.0   # positive = paid out, negative = received

    def notional(self, price: float) -> float:
        return self.qty * price

    def unrealised(self, price: float) -> float:
        if self.side == "LONG":
            return (price - self.entry_price) * self.qty
        return (self.entry_price - price) * self.qty

    def roe_pct(self, price: float) -> float:
        return self.unrealised(price) / self.margin_usd * 100 if self.margin_usd > 1e-9 else 0.0

    def to_dict(self, price: float) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": round(self.qty, 8),
            "entry_price": round(self.entry_price, 6),
            "mark_price": round(price, 6),
            "leverage": round(self.leverage, 2),
            "margin_usd": round(self.margin_usd, 2),
            "notional_usd": round(self.notional(price), 2),
            "unrealised_pnl_usd": round(self.unrealised(price), 2),
            "roe_pct": round(self.roe_pct(price), 2),
            "liq_price": round(self.liq_price, 6),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "funding_paid_usd": round(self.funding_paid_usd, 4),
            "age_minutes": round((time.time() - self.opened_ts) / 60, 1),
            "opened_mode": self.opened_mode,
        }


@dataclass
class FuturesFill:
    ts: float
    symbol: str
    action: str           # OPEN_LONG | OPEN_SHORT | CLOSE_LONG | CLOSE_SHORT | LIQUIDATION
    side: str             # LONG | SHORT (the position's direction)
    qty: float
    price: float
    notional_usd: float
    margin_delta_usd: float   # margin moved out (+) or returned (−) to cash
    fee_usd: float
    realised_pnl_usd: float
    leverage: float
    reason: str = ""
    liquidation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "symbol": self.symbol,
            "action": self.action,
            "side": self.side,
            "qty": round(self.qty, 8),
            "price": round(self.price, 6),
            "notional_usd": round(self.notional_usd, 2),
            "margin_delta_usd": round(self.margin_delta_usd, 2),
            "fee_usd": round(self.fee_usd, 4),
            "realised_pnl_usd": round(self.realised_pnl_usd, 2),
            "leverage": round(self.leverage, 2),
            "reason": self.reason,
            "liquidation": self.liquidation,
        }


class FuturesBook:
    """
    Isolated-margin perpetual futures, sharing the spot book's cash.

    `open` and `close` are the only two operations; everything else is
    accounting the dashboard and the Constitution read. Every method that moves
    money moves it through `self.portfolio.cash`, so the two books never hold
    two disagreeing views of how much capital exists.
    """

    def __init__(
        self,
        portfolio,
        taker_fee_bps: float = TAKER_FEE_BPS,
        maint_margin_rate: float = MAINT_MARGIN_RATE,
        max_leverage: float = DEFAULT_MAX_LEVERAGE,
        clock: Clock | None = None,
    ):
        self.portfolio = portfolio
        self.taker_fee_bps = taker_fee_bps
        self.maint = maint_margin_rate
        self.max_leverage = max_leverage
        self.clock = clock or getattr(portfolio, "clock", None) or Clock()
        self.positions: dict[str, FuturesPosition] = {}
        self.fills: list[FuturesFill] = []
        self.realised_pnl_usd = 0.0
        self.fees_usd = 0.0
        self.funding_paid_usd = 0.0
        self._last_funding_ts = self.clock.now()

    # -- accounting --------------------------------------------------------

    @property
    def locked_margin_usd(self) -> float:
        return sum(p.margin_usd for p in self.positions.values())

    def unrealised(self, marks: dict[str, float]) -> float:
        return sum(
            p.unrealised(marks.get(s, p.entry_price)) for s, p in self.positions.items()
        )

    def gross_notional(self, marks: dict[str, float]) -> float:
        return sum(
            p.notional(marks.get(s, p.entry_price)) for s, p in self.positions.items()
        )

    def equity_contribution(self, marks: dict[str, float]) -> float:
        """
        What this book adds to total account equity: the margin locked inside
        it (already removed from cash at open) plus the live mark-to-market on
        every open position. At the moment a position opens this is exactly the
        margin, so equity only moves by the fee — as it should.
        """
        return self.locked_margin_usd + self.unrealised(marks)

    # -- open --------------------------------------------------------------

    def open(
        self, symbol: str, side: str, notional_usd: float, leverage: float,
        price: float, stop_loss: float | None = None,
        take_profit: float | None = None, mode: str = "paper",
        reason: str = "",
    ) -> FuturesFill | None:
        """
        Open or add to a futures position. Returns the fill, or None if it
        could not be opened (opposite side already held, or too little cash for
        even a trimmed position).

        Margin is drawn from the shared cash pool. If cash cannot cover the
        requested size the notional is trimmed to what it can — the same
        fail-soft the spot buy uses — rather than being rejected outright.
        """
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            return None
        if price <= 0 or notional_usd <= 0:
            return None
        leverage = max(1.0, min(float(leverage), self.max_leverage))

        existing = self.positions.get(symbol)
        if existing and existing.side != side:
            # One net position per symbol keeps the model honest and the UI
            # unambiguous. Flipping direction means closing first, explicitly.
            return None

        fee_rate = self.taker_fee_bps / 10000
        # Trim to available cash: margin + fee = notional*(1/lev + fee_rate).
        cash = self.portfolio.cash
        max_notional = cash / (1 / leverage + fee_rate) if cash > 0 else 0.0
        notional_usd = min(notional_usd, max_notional)
        if notional_usd < 10:
            return None

        slip = SLIPPAGE_BPS / 10000
        fill_price = price * (1 + slip) if side == "LONG" else price * (1 - slip)
        qty = notional_usd / fill_price
        margin = notional_usd / leverage
        fee = notional_usd * fee_rate

        self.portfolio.cash -= margin + fee
        self.fees_usd += fee

        if existing:
            total_qty = existing.qty + qty
            existing.entry_price = (
                existing.entry_price * existing.qty + fill_price * qty
            ) / total_qty
            existing.qty = total_qty
            existing.margin_usd += margin
            existing.fees_usd += fee
            # Effective leverage after adding, then re-derive the liq price.
            existing.leverage = (
                existing.notional(existing.entry_price) / existing.margin_usd
                if existing.margin_usd > 1e-9 else leverage
            )
            existing.liq_price = _liquidation_price(
                side, existing.entry_price, existing.leverage, self.maint
            )
            if stop_loss is not None:
                existing.stop_loss = stop_loss
            if take_profit is not None:
                existing.take_profit = take_profit
            pos = existing
        else:
            pos = FuturesPosition(
                symbol=symbol, side=side, qty=qty, entry_price=fill_price,
                leverage=leverage, margin_usd=margin,
                stop_loss=stop_loss, take_profit=take_profit,
                liq_price=_liquidation_price(side, fill_price, leverage, self.maint),
                opened_mode=mode, fees_usd=fee,
            )
            self.positions[symbol] = pos

        fill = FuturesFill(
            ts=self.clock.now(), symbol=symbol,
            action="OPEN_LONG" if side == "LONG" else "OPEN_SHORT",
            side=side, qty=qty, price=fill_price, notional_usd=notional_usd,
            margin_delta_usd=margin, fee_usd=fee, realised_pnl_usd=0.0,
            leverage=pos.leverage, reason=reason,
        )
        self.fills.append(fill)
        return fill

    # -- close -------------------------------------------------------------

    def close(
        self, symbol: str, price: float, fraction: float = 1.0,
        mode: str = "paper", reason: str = "", liquidation: bool = False,
    ) -> FuturesFill | None:
        """
        Reduce or flatten a position. Returns the fill, or None if there is
        nothing to close. Realised P&L (net of the close fee) is booked back to
        the shared cash pool along with the released margin, and is added to the
        portfolio's realised-today total so the daily-loss rule and the
        cooldown see a futures loss exactly as they would a spot one.
        """
        pos = self.positions.get(symbol)
        if not pos or pos.qty <= 0 or price <= 0:
            return None
        fraction = max(0.0, min(1.0, fraction))
        if fraction <= 0:
            return None

        qty_close = pos.qty * fraction
        margin_close = pos.margin_usd * fraction
        fee_rate = self.taker_fee_bps / 10000

        slip = SLIPPAGE_BPS / 10000
        # Closing a long sells (slips down); closing a short buys (slips up).
        fill_price = price * (1 - slip) if pos.side == "LONG" else price * (1 + slip)
        notional_close = qty_close * fill_price
        gross_pnl = (
            (fill_price - pos.entry_price) * qty_close if pos.side == "LONG"
            else (pos.entry_price - fill_price) * qty_close
        )
        fee = notional_close * fee_rate

        if liquidation:
            # The position's loss has reached its margin. Model it as the whole
            # margin lost and nothing returned — never more than the margin,
            # which is the point of isolated margin.
            realised = -margin_close
            returned_to_cash = 0.0
        else:
            realised = gross_pnl - fee
            returned_to_cash = max(margin_close + gross_pnl - fee, 0.0)

        self.portfolio.cash += returned_to_cash
        self.fees_usd += 0.0 if liquidation else fee
        self.realised_pnl_usd += realised

        # Fold into the spot book's realised-today so shared risk rules apply.
        self.portfolio.realised_today += realised
        if realised < 0:
            self.portfolio.last_losing_exit_ts = self.clock.now()

        pos.qty -= qty_close
        pos.margin_usd -= margin_close
        if pos.qty <= 1e-10 or fraction >= 1.0:
            del self.positions[symbol]

        action = "LIQUIDATION" if liquidation else (
            "CLOSE_LONG" if pos.side == "LONG" else "CLOSE_SHORT"
        )
        fill = FuturesFill(
            ts=self.clock.now(), symbol=symbol, action=action, side=pos.side,
            qty=qty_close, price=fill_price, notional_usd=notional_close,
            margin_delta_usd=-margin_close, fee_usd=0.0 if liquidation else fee,
            realised_pnl_usd=realised, leverage=pos.leverage,
            reason=reason, liquidation=liquidation,
        )
        self.fills.append(fill)
        return fill

    # -- protective + liquidation checks (run every tick) ------------------

    def check_liquidations_and_exits(self, marks: dict[str, float]) -> list[dict[str, Any]]:
        """
        Liquidations, stops and targets, checked before any analyst runs — the
        same priority the spot stop-loss already has. Returns a list of event
        dicts the engine logs and writes to the ledger.
        """
        events: list[dict[str, Any]] = []
        for symbol, pos in list(self.positions.items()):
            price = marks.get(symbol)
            if not price or price <= 0 or pos.qty <= 0:
                continue

            hit = None
            if pos.side == "LONG":
                if price <= pos.liq_price:
                    hit = "liquidation"
                elif pos.stop_loss and price <= pos.stop_loss:
                    hit = "stop_loss"
                elif pos.take_profit and price >= pos.take_profit:
                    hit = "take_profit"
            else:  # SHORT
                if price >= pos.liq_price:
                    hit = "liquidation"
                elif pos.stop_loss and price >= pos.stop_loss:
                    hit = "stop_loss"
                elif pos.take_profit and price <= pos.take_profit:
                    hit = "take_profit"

            if not hit:
                continue
            f = self.close(
                symbol, price, fraction=1.0,
                reason=f"Automatic {hit.replace('_', ' ')}",
                liquidation=(hit == "liquidation"),
            )
            if f:
                events.append({"kind": hit, **f.to_dict()})
        return events

    # -- funding -----------------------------------------------------------

    def accrue_funding(self, marks: dict[str, float], funding_bps: float = DEFAULT_FUNDING_BPS) -> float:
        """
        Accrue funding for the elapsed time since the last call. Longs pay when
        funding is positive, shorts receive it. The per-tick amount is tiny by
        design — this is the cost of carry made visible, not a P&L driver.
        """
        now = self.clock.now()
        elapsed = now - self._last_funding_ts
        self._last_funding_ts = now
        if elapsed <= 0 or not self.positions:
            return 0.0
        share = elapsed / FUNDING_INTERVAL_SECONDS
        rate = funding_bps / 10000 * share
        total = 0.0
        for symbol, pos in self.positions.items():
            notional = pos.notional(marks.get(symbol, pos.entry_price))
            cost = notional * rate if pos.side == "LONG" else -notional * rate
            pos.funding_paid_usd += cost
            self.portfolio.cash -= cost
            self.funding_paid_usd += cost
            total += cost
        return total

    # -- snapshots ---------------------------------------------------------

    def state_dict(self, marks: dict[str, float]) -> dict[str, Any]:
        return {
            "enabled": True,
            "max_leverage": self.max_leverage,
            "position_count": len(self.positions),
            "locked_margin_usd": round(self.locked_margin_usd, 2),
            "unrealised_pnl_usd": round(self.unrealised(marks), 2),
            "gross_notional_usd": round(self.gross_notional(marks), 2),
            "realised_pnl_usd": round(self.realised_pnl_usd, 2),
            "fees_usd": round(self.fees_usd, 4),
            "funding_paid_usd": round(self.funding_paid_usd, 4),
            "positions": {
                s: p.to_dict(marks.get(s, p.entry_price)) for s, p in self.positions.items()
            },
            "recent_fills": [f.to_dict() for f in self.fills[-20:]],
        }

    def status(self, marks: dict[str, float]) -> dict[str, Any]:
        return self.state_dict(marks)
