"""
GlassBox — precision.

Binance rejects orders that don't land on an exact multiple of the exchange's
`LOT_SIZE` (quantity step), `PRICE_FILTER` (price tick), or fall under
`MIN_NOTIONAL`. A quantity computed as `notional / price` in ordinary
floating-point arithmetic essentially never lands on that grid — the classic
demonstration is `0.1 + 0.2 == 0.30000000000000004` in IEEE-754 — and either
gets silently truncated somewhere, or bounces off the exchange with a filter
error that a naive agent has no framework for recovering from.

This module fixes that with Python's built-in `decimal.Decimal`, using exact
base-10 arithmetic rather than a hand-rolled string-slicing workaround. This
is not a stylistic preference: `Decimal` is standard, extensively tested, and
gets rounding modes (`ROUND_DOWN`, `ROUND_HALF_UP`, ...) correct by
construction, where a string-slicing approach has to reinvent — and can
subtly get wrong — rounding-direction edge cases (negative numbers, exact
half-steps, trailing-zero step sizes) that `Decimal` already solved.

The rule that matters
----------------------
**Quantity is always rounded down, never up.** Rounding up could either
exceed the balance actually available or silently increase the risk the
Constitution already sized and approved. A quantity that rounds down below
the exchange's minimum is refused outright rather than bumped up to meet it —
bumping up a rejected order to force it through is how a $6 test order
quietly becomes a $47 one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any


@dataclass
class SymbolFilters:
    """The exchange's exact grid for one symbol, straight from exchangeInfo."""

    symbol: str
    step_size: Decimal  # LOT_SIZE — quantity must be a multiple of this
    min_qty: Decimal
    tick_size: Decimal  # PRICE_FILTER — price must be a multiple of this
    min_notional: Decimal  # MIN_NOTIONAL / NOTIONAL — quote_qty floor
    base_precision: int = 8
    quote_precision: int = 8

    @classmethod
    def from_universe_row(cls, row: dict[str, Any]) -> "SymbolFilters":
        """Build from the shape `BinanceFeeds.universe()` already returns."""
        return cls(
            symbol=row["symbol"],
            step_size=_dec(row.get("step_size") or 0),
            min_qty=_dec(row.get("min_qty") or 0),
            tick_size=_dec(row.get("tick_size") or 0),
            min_notional=_dec(row.get("min_notional") or 0),
            base_precision=int(row.get("base_precision", 8)),
            quote_precision=int(row.get("quote_precision", 8)),
        )


class FilterViolation(Exception):
    """Raised when an order cannot be made to fit the exchange's grid at all
    — e.g. the notional is so small that rounding down to a valid quantity
    would fall under min_notional. This must stop the order, not shrink the
    violation and hope Binance doesn't notice."""


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def round_step(value: Decimal, step: Decimal) -> Decimal:
    """
    Floor `value` to the nearest multiple of `step`.

    A step of 0 means "no constraint on this symbol" (some symbols genuinely
    have no LOT_SIZE-style filter) — returned unchanged rather than dividing
    by zero.
    """
    if step <= 0:
        return value
    steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return steps * step


def quantity_precision(step: Decimal) -> int:
    """How many decimal places a step size implies, e.g. 0.001 -> 3."""
    s = format(step.normalize(), "f")
    return len(s.split(".")[1]) if "." in s else 0


@dataclass
class SizedOrder:
    """The exact, exchange-legal quantity/price/notional for one order."""

    symbol: str
    quantity: Decimal
    price: Decimal | None
    notional: Decimal
    quantity_str: str  # exact string form to send over the wire — never a float
    price_str: str | None
    rounding_applied: bool  # true if the raw request didn't already sit on-grid

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity_str,
            "price": self.price_str,
            "notional": float(self.notional),
            "rounding_applied": self.rounding_applied,
        }


def size_market_order(
    filters: SymbolFilters, notional_usd: float, reference_price: float,
) -> SizedOrder:
    """
    Size a market order to the exchange's exact grid.

    Binance's `quoteOrderQty` parameter (which GlassBox uses for market buys)
    already lets the exchange compute the base-asset quantity itself from a
    requested USD amount — sidestepping most of this problem for that one
    order shape. This function exists for everything `quoteOrderQty` does not
    cover: market **sells** of an exact held quantity, and any limit order,
    both of which must send a `quantity` we compute ourselves and which must
    already sit exactly on Binance's grid before it ever leaves this process.
    """
    price = _dec(reference_price)
    if price <= 0:
        raise FilterViolation(f"No valid reference price for {filters.symbol}.")

    raw_qty = _dec(notional_usd) / price
    qty = round_step(raw_qty, filters.step_size)
    rounded = qty != raw_qty

    if qty < filters.min_qty:
        raise FilterViolation(
            f"{filters.symbol}: quantity {qty} is below the exchange minimum "
            f"of {filters.min_qty} after rounding to the {filters.step_size} "
            f"step. Refusing rather than rounding up past the intended size."
        )

    notional = qty * price
    if notional < filters.min_notional:
        raise FilterViolation(
            f"{filters.symbol}: ${notional} notional after rounding is below "
            f"the exchange minimum of ${filters.min_notional}."
        )

    prec = quantity_precision(filters.step_size)
    return SizedOrder(
        symbol=filters.symbol, quantity=qty, price=None, notional=notional,
        quantity_str=f"{qty:.{prec}f}" if prec else str(int(qty)),
        price_str=None, rounding_applied=rounded,
    )


def size_limit_order(
    filters: SymbolFilters, notional_usd: float, limit_price: float,
) -> SizedOrder:
    """Size a limit order: both quantity and price must land on-grid."""
    raw_price = _dec(limit_price)
    price = round_step(raw_price, filters.tick_size)
    if price <= 0:
        raise FilterViolation(f"Limit price for {filters.symbol} rounds to zero.")

    raw_qty = _dec(notional_usd) / price
    qty = round_step(raw_qty, filters.step_size)
    rounded = qty != raw_qty or price != raw_price

    if qty < filters.min_qty:
        raise FilterViolation(
            f"{filters.symbol}: quantity {qty} is below the exchange minimum "
            f"of {filters.min_qty} after rounding."
        )

    notional = qty * price
    if notional < filters.min_notional:
        raise FilterViolation(
            f"{filters.symbol}: ${notional} notional is below the exchange "
            f"minimum of ${filters.min_notional}."
        )

    qprec = quantity_precision(filters.step_size)
    pprec = quantity_precision(filters.tick_size)
    return SizedOrder(
        symbol=filters.symbol, quantity=qty, price=price, notional=notional,
        quantity_str=f"{qty:.{qprec}f}" if qprec else str(int(qty)),
        price_str=f"{price:.{pprec}f}" if pprec else str(int(price)),
        rounding_applied=rounded,
    )


def size_sell_quantity(filters: SymbolFilters, held_qty: float, price: float) -> SizedOrder:
    """
    Size a sell of an exact held position — a stop-loss or take-profit exit,
    or a Guardian hedge. Rounds down so we never attempt to sell more than we
    actually hold, which Binance would reject outright.
    """
    raw_qty = _dec(held_qty)
    qty = round_step(raw_qty, filters.step_size)
    rounded = qty != raw_qty

    if qty < filters.min_qty or qty <= 0:
        raise FilterViolation(
            f"{filters.symbol}: held quantity {raw_qty} rounds down to {qty}, "
            f"under the exchange minimum of {filters.min_qty} — this dust "
            f"amount cannot be sold through a normal order."
        )

    p = _dec(price)
    notional = qty * p
    prec = quantity_precision(filters.step_size)
    return SizedOrder(
        symbol=filters.symbol, quantity=qty, price=None, notional=notional,
        quantity_str=f"{qty:.{prec}f}" if prec else str(int(qty)),
        price_str=None, rounding_applied=rounded,
    )
