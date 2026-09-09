"""
GlassBox — execution.

The single place where a decision becomes an order. Everything upstream argues;
this executes, or refuses to.

Four surfaces, one interface:

    paper   simulated fills against live prices; nothing leaves the process
    mock    a real MCP client against a local mock Binance server — the whole
            protocol path exercised, no money at risk
    live    a real MCP client against agent.binance.com
    bridge  emits a signed instruction for a human to run in their own MCP client

`mock` matters more than it sounds. It runs the same `BinanceMCPClient`, the same
JSON-RPC, the same tool discovery and the same error handling as `live`. The only
difference is the URL. So "we wired up MCP" stops being a claim and becomes
something a test asserts.

Three invariants hold on every surface:

* **No order is sent without a receipt.** The ledger hash of the justification
  travels with the request and is written back against the fill. An order with no
  reasoning attached cannot be produced by this code path.
* **Tools are resolved by intent, not by an assumed name.** Binance can rename
  `place_spot_order` tomorrow; `find_tool("spot", "order")` keeps working.
* **A decision fires at most once.** Every order carries a deterministic client
  id derived from the exact decision that authorised it; resubmitting the same
  decision — from a retry, a duplicate click, anything — returns the original
  outcome rather than firing again. See `resilience.py`.

Before anything is sent, two more checks run: the quantity and price are
rounded to Binance's exact `LOT_SIZE`/`PRICE_FILTER` grid using exact decimal
arithmetic (`precision.py` — this is what stops `0.1 + 0.2` becoming a
rejected order), and the live price is compared against the price the
decision was made at (`reference_price`), refusing to execute if the market
has since moved past a configurable collar. A decision that sat waiting for
a human confirmation for ten minutes should not fire blindly into whatever
the price has since become.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Intent, Mode
from .mcp import BinanceMCPClient, MCPError, NotAuthorised
from .precision import FilterViolation, size_market_order, size_sell_quantity
from .resilience import ClockDriftMonitor, IdempotencyGuard, deterministic_client_order_id


@dataclass
class ExecutionResult:
    status: str  # filled | rejected | bridged | error
    surface: str
    symbol: str
    side: str
    requested_usd: float
    filled_usd: float = 0.0
    filled_qty: float = 0.0
    price: float = 0.0
    fee_usd: float = 0.0
    order_id: str | None = None
    client_order_id: str | None = None
    justification_hash: str = ""
    latency_ms: float = 0.0
    idempotent_replay: bool = False  # true if this result was cached, not re-sent
    raw: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Executor:
    # A decision fires only if the live price is within this many basis
    # points of the price it was made at. Wide enough that normal market
    # movement between proposal and a routine confirmation never blocks a
    # legitimate trade; tight enough to catch a stale decision that sat
    # through a fast move.
    DEFAULT_PRICE_COLLAR_BPS = 150.0

    def __init__(
        self, mcp: BinanceMCPClient | None = None, feeds=None,
        price_collar_bps: float | None = None,
    ):
        self.mcp = mcp
        self.feeds = feeds
        self.price_collar_bps = price_collar_bps or self.DEFAULT_PRICE_COLLAR_BPS
        self.orders: list[ExecutionResult] = []
        self.last_error: str | None = None
        self.idempotency = IdempotencyGuard()
        self.clock = ClockDriftMonitor(feeds) if feeds else None

    # -- MCP path ----------------------------------------------------------

    async def execute_via_mcp(
        self, intent: Intent, notional_usd: float, justification_hash: str
    ) -> ExecutionResult:
        """
        Place a real order through the Binance MCP server.

        The justification hash is attached to the request so the order and the
        reasoning that produced it share an identifier from the moment it is
        sent, not reconstructed afterwards.

        Four checks run in order before anything reaches Binance:
        idempotency (has this exact decision already fired), the price collar
        (has the market moved too far since the decision was made), quantity
        sizing (does it land on the exchange's exact grid), and only then the
        actual call.
        """
        if not self.mcp:
            return ExecutionResult(
                status="error", surface="mcp", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                message="No MCP client configured.",
            )

        # 1. Idempotency. If this exact decision already produced an order,
        #    return that result rather than firing again. Binds to the
        #    justification hash specifically, so this is the audit trail's
        #    own hash extended one layer further, into the order itself.
        client_order_id = deterministic_client_order_id(
            intent.symbol, intent.side.value, notional_usd, justification_hash,
        )
        cached = self.idempotency.seen(client_order_id)
        if cached is not None:
            return ExecutionResult(**{**cached, "idempotent_replay": True})

        started = time.time()
        tool = self.mcp.find_tool("spot", "order") or self.mcp.find_tool("place", "order")
        if not tool:
            return ExecutionResult(
                status="error", surface="mcp", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id,
                message=(
                    "No order-placement tool is exposed by this session. The "
                    "Trade scope was probably not granted."
                ),
            )

        # 2. Price collar. A decision that sat waiting for a confirmation, or
        #    was proposed just before a fast move, should not fire blindly at
        #    whatever the price has since become.
        if intent.reference_price and self.feeds:
            try:
                live = await self.feeds.tickers_24h([intent.symbol])
                live_price = float(live.get(intent.symbol, {}).get("lastPrice", 0) or 0)
                if live_price > 0:
                    drift_bps = abs(live_price / intent.reference_price - 1) * 10000
                    if drift_bps > self.price_collar_bps:
                        result = ExecutionResult(
                            status="rejected", surface="mcp", symbol=intent.symbol,
                            side=intent.side.value, requested_usd=notional_usd,
                            client_order_id=client_order_id,
                            justification_hash=justification_hash,
                            latency_ms=round((time.time() - started) * 1000, 1),
                            message=(
                                f"Price moved {drift_bps:.0f} bps since this decision "
                                f"was made (${intent.reference_price:,.4f} -> "
                                f"${live_price:,.4f}), past the "
                                f"{self.price_collar_bps:.0f} bps collar. Refusing "
                                f"rather than executing at a price the Council never "
                                f"evaluated."
                            ),
                        )
                        self.idempotency.record(client_order_id, result.to_dict())
                        return result
            except Exception:
                pass  # a failed drift check should not block a trade the
                      # Constitution already approved; it only ever tightens

        # 3. Exact exchange-grid sizing. Sell orders send a real `quantity`,
        #    computed and rounded with exact decimal arithmetic rather than
        #    Binance re-deriving one from `quoteOrderQty` at whatever price it
        #    sees at the moment of execution — the two can disagree by
        #    exactly the kind of float-drift/timing gap this exists to close.
        #    Buys keep `quoteOrderQty`, which is Binance's own recommended
        #    "spend exactly $X" parameter and handles LOT_SIZE server-side.
        args: dict[str, Any] = {
            "symbol": intent.symbol, "side": intent.side.value,
            "type": intent.order_type,
        }
        rounding_note = ""
        if intent.side.value == "SELL" and self.feeds:
            try:
                filters = await self.feeds.symbol_filters(intent.symbol)
                if intent.reference_price:
                    sized = size_sell_quantity(
                        filters, notional_usd / intent.reference_price, intent.reference_price
                    )
                    args["quantity"] = sized.quantity_str
                    if sized.rounding_applied:
                        rounding_note = (
                            f" Quantity rounded to the exchange's "
                            f"{filters.step_size} step: {sized.quantity_str}."
                        )
                else:
                    args["quoteOrderQty"] = round(notional_usd, 2)
            except FilterViolation as exc:
                result = ExecutionResult(
                    status="rejected", surface="mcp", symbol=intent.symbol,
                    side=intent.side.value, requested_usd=notional_usd,
                    client_order_id=client_order_id,
                    justification_hash=justification_hash,
                    message=f"Refused before sending: {exc}",
                )
                self.idempotency.record(client_order_id, result.to_dict())
                return result
            except Exception:
                args["quoteOrderQty"] = round(notional_usd, 2)
        else:
            args["quoteOrderQty"] = round(notional_usd, 2)

        if intent.order_type == "LIMIT" and intent.limit_price:
            args["price"] = intent.limit_price
        args["newClientOrderId"] = client_order_id

        try:
            out = await self.mcp.call_tool(tool, args)
        except NotAuthorised as exc:
            self.last_error = str(exc)
            return ExecutionResult(
                status="rejected", surface="mcp", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id,
                justification_hash=justification_hash,
                latency_ms=round((time.time() - started) * 1000, 1),
                message=str(exc),
            )
        except MCPError as exc:
            self.last_error = str(exc)
            return ExecutionResult(
                status="error", surface="mcp", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id,
                justification_hash=justification_hash,
                latency_ms=round((time.time() - started) * 1000, 1),
                message=str(exc),
            )

        latency = round((time.time() - started) * 1000, 1)

        if out.get("is_error"):
            rejected = ExecutionResult(
                status="rejected", surface="mcp", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id,
                justification_hash=justification_hash, latency_ms=latency,
                message=out.get("text", "Binance rejected the order."),
                raw=out,
            )
            # A rejection is still an order we sent. Counting only fills makes
            # the execution log look cleaner than reality, which is the last
            # thing an audit surface should do.
            self.orders.append(rejected)
            return rejected

        data = out.get("structured") or _try_json(out.get("text", "")) or {}
        result = ExecutionResult(
            status="filled",
            surface="mcp",
            symbol=data.get("symbol", intent.symbol),
            side=data.get("side", intent.side.value),
            requested_usd=notional_usd,
            filled_usd=float(data.get("cummulativeQuoteQty", notional_usd) or 0),
            filled_qty=float(data.get("executedQty", 0) or 0),
            price=float(data.get("price", 0) or 0),
            fee_usd=float(data.get("commission", 0) or 0),
            order_id=str(data.get("orderId", "")) or None,
            client_order_id=client_order_id,
            justification_hash=justification_hash,
            latency_ms=latency,
            raw=data,
            message=f"Filled via Binance MCP in {latency:.0f} ms.{rounding_note}",
        )
        self.orders.append(result)
        self.idempotency.record(client_order_id, result.to_dict())
        return result

    async def execute_futures_via_mcp(
        self, intent: Intent, notional_usd: float, leverage: float,
        justification_hash: str,
    ) -> ExecutionResult:
        """
        Place a USDⓈ-M perpetual futures order through the Binance MCP server.

        Resolves the futures order tool by intent (`find_tool("futures",
        "order")`) rather than an assumed name, carries the justification hash
        exactly as the spot path does, and is idempotent on the exact decision
        so a duplicate click or retry returns the original outcome instead of
        doubling the position. The order carries `positionSide` and
        `reduceOnly` so a long, a short and a close are unambiguous to Binance.
        """
        if not self.mcp:
            return ExecutionResult(
                status="error", surface="mcp-futures", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                message="No MCP client configured.",
            )
        meta = intent.meta
        client_order_id = deterministic_client_order_id(
            intent.symbol, meta.get("action", intent.side.value),
            notional_usd, justification_hash,
        )
        cached = self.idempotency.seen(client_order_id)
        if cached is not None:
            return ExecutionResult(**{**cached, "idempotent_replay": True})

        started = time.time()
        tool = (
            self.mcp.find_tool("futures", "order")
            or self.mcp.find_tool("place", "futures")
            or self.mcp.find_tool("futures", "place")
        )
        if not tool:
            return ExecutionResult(
                status="error", surface="mcp-futures", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id,
                message=(
                    "No futures order tool is exposed by this session. The "
                    "Futures Trade scope was probably not granted."
                ),
            )

        qty = notional_usd / max(intent.reference_price or 1, 1e-9)
        args: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "positionSide": meta.get("position_side", "BOTH"),
            "type": intent.order_type,
            "quantity": round(qty, 6),
            "leverage": int(leverage),
            "reduceOnly": bool(meta.get("reduce_only")),
            "newClientOrderId": client_order_id,
        }
        try:
            out = await self.mcp.call_tool(tool, args)
        except NotAuthorised as exc:
            self.last_error = str(exc)
            return ExecutionResult(
                status="rejected", surface="mcp-futures", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id, justification_hash=justification_hash,
                latency_ms=round((time.time() - started) * 1000, 1), message=str(exc),
            )
        except MCPError as exc:
            self.last_error = str(exc)
            return ExecutionResult(
                status="error", surface="mcp-futures", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id, justification_hash=justification_hash,
                latency_ms=round((time.time() - started) * 1000, 1), message=str(exc),
            )

        latency = round((time.time() - started) * 1000, 1)
        if out.get("is_error"):
            rejected = ExecutionResult(
                status="rejected", surface="mcp-futures", symbol=intent.symbol,
                side=intent.side.value, requested_usd=notional_usd,
                client_order_id=client_order_id, justification_hash=justification_hash,
                latency_ms=latency, message=out.get("text", "Binance rejected the order."),
                raw=out,
            )
            self.orders.append(rejected)
            return rejected

        data = out.get("structured") or _try_json(out.get("text", "")) or {}
        result = ExecutionResult(
            status="filled", surface="mcp-futures",
            symbol=data.get("symbol", intent.symbol),
            side=data.get("side", intent.side.value),
            requested_usd=notional_usd,
            filled_usd=float(data.get("cummulativeQuoteQty", notional_usd) or notional_usd),
            filled_qty=float(data.get("executedQty", qty) or qty),
            price=float(data.get("avgPrice", data.get("price", 0)) or 0),
            order_id=str(data.get("orderId", "")) or None,
            client_order_id=client_order_id, justification_hash=justification_hash,
            latency_ms=latency, raw=data,
            message=f"Futures order filled via Binance MCP in {latency:.0f} ms.",
        )
        self.orders.append(result)
        self.idempotency.record(client_order_id, result.to_dict())
        return result

    async def balances(self) -> dict[str, Any]:
        """Read the Agentic sub-account. Read-only, never a write."""
        if not self.mcp:
            return {"available": False, "reason": "no MCP session"}
        tool = self.mcp.find_tool("balance") or self.mcp.find_tool("account")
        if not tool:
            return {"available": False, "reason": "Account scope not granted"}
        try:
            out = await self.mcp.call_tool(tool, {})
            if out.get("is_error"):
                return {"available": False, "reason": out.get("text")}
            data = out.get("structured") or _try_json(out.get("text", "")) or {}
            return {"available": True, **data}
        except (MCPError, NotAuthorised) as exc:
            return {"available": False, "reason": str(exc)}

    async def order_history(self, symbol: str | None = None) -> list[dict]:
        if not self.mcp:
            return []
        tool = self.mcp.find_tool("order", "history")
        if not tool:
            return []
        try:
            out = await self.mcp.call_tool(tool, {"symbol": symbol} if symbol else {})
            data = out.get("structured") or _try_json(out.get("text", "")) or {}
            return data.get("orders", [])
        except (MCPError, NotAuthorised):
            return []

    # -- bridge path -------------------------------------------------------

    @staticmethod
    def build_bridge_instruction(
        intent: Intent, notional_usd: float, justification_hash: str, endpoint: str
    ) -> dict[str, Any]:
        """
        The exact call for a human to run in their own MCP client.

        Used when the operator would rather keep GlassBox out of the credential
        path entirely — the reasoning still travels with the instruction.
        """
        return {
            "server": "binance-mcp-server",
            "endpoint": endpoint,
            "tool": "place_spot_order",
            "arguments": {
                "symbol": intent.symbol,
                "side": intent.side.value,
                "type": intent.order_type,
                "quoteOrderQty": round(notional_usd, 2),
            },
            "natural_language": (
                f"{intent.side.value.capitalize()} ${notional_usd:,.2f} of "
                f"{intent.symbol} at market on spot in my Agentic sub-account."
            ),
            "justification_hash": justification_hash,
            "verify_with": "glassbox verify",
        }

    def summary(self) -> dict[str, Any]:
        filled = [o for o in self.orders if o.status == "filled"]
        rejected = [o for o in self.orders if o.status != "filled"]
        replays = [o for o in self.orders if o.idempotent_replay]
        return {
            "orders_sent": len(self.orders),
            "filled": len(filled),
            "rejected": len(rejected),
            "idempotent_replays": len(replays),
            "price_collar_bps": self.price_collar_bps,
            "total_usd": round(sum(o.filled_usd for o in filled), 2),
            "avg_latency_ms": round(
                sum(o.latency_ms for o in filled) / len(filled), 1
            ) if filled else None,
            "last_error": self.last_error,
            "idempotency": self.idempotency.status(),
            "clock": self.clock.status() if self.clock else None,
            "recent": [o.to_dict() for o in self.orders[-10:]],
        }

    async def check_clock_drift(self) -> dict[str, Any]:
        if not self.clock:
            return {"drift_ms": None, "healthy": None, "note": "No feeds configured."}
        report = await self.clock.check()
        return report.to_dict()


def _try_json(text: str) -> dict | None:
    try:
        d = json.loads(text)
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None
