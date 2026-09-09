"""
GlassBox — mock Binance MCP server.

Implements the same Streamable HTTP + JSON-RPC contract as
`https://agent.binance.com/mcp/agentic`, with the tool surface Binance
documents: market data, Agentic sub-account balances, spot/margin/convert/futures
trading, and transfers between wallets *inside* the sub-account.

Why this exists
---------------
Three reasons, all of them practical.

1. **The execution path can be tested.** Without it, "we wired up MCP" is a claim
   nobody can check, including us. With it, the full journey — intent, policy
   verdict, Guardian veto, MCP tool call, fill, receipt — runs in CI.
2. **The demo does not need someone's funded account.** A judge can watch real
   orders flow through a real MCP client without anyone risking money.
3. **Failure modes can be rehearsed.** Insufficient balance, a rejected symbol,
   an expired session, a withdrawal attempt. You cannot summon those on demand
   against production, and they are exactly the paths that must not surprise you.

It deliberately enforces the same *refusals* the real server does:

* there is no withdrawal tool, and asking for one is an error
* funds cannot be moved from a main account into the sub-account
* orders are rejected when the sub-account cannot cover them

Prices come from the live Binance public API, so fills are realistic. Only the
account is fictional.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "get_ticker",
        "description": "Get the current price and 24h statistics for a symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_account_balance",
        "description": "Balances across all wallets in the Agentic sub-account.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "place_spot_order",
        "description": "Place a spot order in the Agentic sub-account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["BUY", "SELL"]},
                "type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
                "quoteOrderQty": {"type": "number"},
                "quantity": {"type": "number"},
                "price": {"type": "number"},
            },
            "required": ["symbol", "side", "type"],
        },
    },
    {
        "name": "place_futures_order",
        "description": "Place a USDⓈ-M perpetual futures order in the Agentic sub-account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["BUY", "SELL"]},
                "positionSide": {"type": "string", "enum": ["LONG", "SHORT", "BOTH"]},
                "type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
                "quantity": {"type": "number"},
                "leverage": {"type": "number"},
                "reduceOnly": {"type": "boolean"},
                "price": {"type": "number"},
            },
            "required": ["symbol", "side", "type", "quantity"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an open order in the Agentic sub-account.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "orderId": {"type": "string"}},
            "required": ["symbol", "orderId"],
        },
    },
    {
        "name": "get_open_orders",
        "description": "List open orders in the Agentic sub-account.",
        "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
    },
    {
        "name": "get_order_history",
        "description": "Recent order history for the Agentic sub-account.",
        "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
    },
    {
        "name": "transfer_between_wallets",
        "description": (
            "Move funds between wallets inside the Agentic sub-account "
            "(e.g. Spot to USD-M). Cannot pull from the main account."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string"},
                "amount": {"type": "number"},
                "fromWallet": {"type": "string"},
                "toWallet": {"type": "string"},
            },
            "required": ["asset", "amount", "fromWallet", "toWallet"],
        },
    },
]


class MockBinanceMCP:
    """A stand-in Agentic sub-account with realistic behaviour."""

    def __init__(self, starting_usdt: float = 5000.0, feeds=None):
        self.balances: dict[str, float] = {"USDT": starting_usdt}
        self.orders: list[dict] = []
        self.feeds = feeds
        self.sessions: set[str] = set()
        self.calls = 0

    async def _price(self, symbol: str) -> float:
        if self.feeds:
            try:
                t = await self.feeds.tickers_24h([symbol])
                if symbol in t:
                    return float(t[symbol]["lastPrice"])
            except Exception:
                pass
        return 0.0

    async def call_tool(self, name: str, args: dict) -> dict:
        self.calls += 1

        # The real server has no withdrawal capability at all. Refusing loudly
        # here means the refusal is exercised in tests rather than assumed.
        if any(k in name.lower() for k in ("withdraw", "external", "send_to")):
            return self._error(
                "There is no withdrawal scope on Binance Agent OS. An agent can "
                "never move funds out of the sub-account."
            )

        handler = {
            "get_ticker": self._ticker,
            "get_account_balance": self._balance,
            "place_spot_order": self._place,
            "place_futures_order": self._place_futures,
            "cancel_order": self._cancel,
            "get_open_orders": self._open_orders,
            "get_order_history": self._history,
            "transfer_between_wallets": self._transfer,
        }.get(name)
        if not handler:
            return self._error(f"Unknown tool '{name}'.")
        return await handler(args)

    # -- tools -------------------------------------------------------------

    async def _ticker(self, a: dict) -> dict:
        sym = a.get("symbol", "").upper()
        price = await self._price(sym)
        if not price:
            return self._error(f"No market data for {sym}.")
        return self._ok({"symbol": sym, "price": price, "source": "binance_public"})

    async def _balance(self, a: dict) -> dict:
        rows = []
        total = 0.0
        for asset, free in self.balances.items():
            if free <= 1e-9:
                continue
            usd = free if asset in ("USDT", "USDC") else free * await self._price(f"{asset}USDT")
            total += usd
            rows.append({"asset": asset, "free": round(free, 8),
                         "locked": 0.0, "usdValue": round(usd, 2)})
        return self._ok({
            "accountType": "AGENTIC_SUB",
            "balances": rows,
            "totalUsdValue": round(total, 2),
        })

    async def _place(self, a: dict) -> dict:
        sym = a.get("symbol", "").upper()
        side = a.get("side", "").upper()
        price = await self._price(sym)
        if not price:
            return self._error(
                f"No live price available for {sym}; refusing to fill blind."
            )
        if not sym.endswith("USDT"):
            return self._error("This Agentic sub-account is funded in USDT only.")

        base = sym[:-4]
        notional = float(a.get("quoteOrderQty") or 0) or float(a.get("quantity") or 0) * price
        if notional <= 0:
            return self._error("Order size must be greater than zero.")
        if notional < 5:
            return self._error("Order is below the 5 USDT minimum notional.")

        fee = notional * 0.00075
        if side == "BUY":
            if self.balances.get("USDT", 0) < notional + fee:
                return self._error(
                    f"Insufficient balance: {self.balances.get('USDT', 0):.2f} USDT "
                    f"available, {notional + fee:.2f} required. Fund the sub-account "
                    f"from the Binance web UI."
                )
            qty = notional / price
            self.balances["USDT"] -= notional + fee
            self.balances[base] = self.balances.get(base, 0.0) + qty
        else:
            qty = notional / price
            if self.balances.get(base, 0) < qty:
                return self._error(
                    f"Insufficient {base}: {self.balances.get(base, 0):.8f} held, "
                    f"{qty:.8f} required."
                )
            self.balances[base] -= qty
            self.balances["USDT"] = self.balances.get("USDT", 0.0) + notional - fee

        order = {
            "orderId": str(uuid.uuid4().int % 10**10),
            "symbol": sym, "side": side, "type": a.get("type", "MARKET"),
            "status": "FILLED", "price": round(price, 8), "executedQty": round(qty, 8),
            "cummulativeQuoteQty": round(notional, 2), "commission": round(fee, 6),
            "transactTime": int(time.time() * 1000),
        }
        self.orders.append(order)
        return self._ok(order)

    async def _place_futures(self, a: dict) -> dict:
        """
        Model a USDⓈ-M perpetual fill. Margin (notional / leverage) is debited
        from the USDT wallet on an open and credited back on a reduce-only
        close, mirroring how isolated margin behaves — enough for the real MCP
        client to exercise the whole futures path against a stand-in.
        """
        sym = a.get("symbol", "").upper()
        side = a.get("side", "").upper()
        price = await self._price(sym)
        if not price:
            return self._error(f"No live price available for {sym}; refusing to fill blind.")
        if not sym.endswith("USDT"):
            return self._error("This Agentic futures sub-account is margined in USDT only.")

        qty = float(a.get("quantity") or 0)
        if qty <= 0:
            return self._error("Futures order quantity must be greater than zero.")
        leverage = max(1.0, float(a.get("leverage") or 1))
        reduce_only = bool(a.get("reduceOnly"))
        notional = qty * price
        fee = notional * 0.00045
        margin = notional / leverage

        if not reduce_only:
            if self.balances.get("USDT", 0) < margin + fee:
                return self._error(
                    f"Insufficient margin: {self.balances.get('USDT', 0):.2f} USDT "
                    f"available, {margin + fee:.2f} required for a {leverage:.0f}x position."
                )
            self.balances["USDT"] -= margin + fee
        else:
            # Return margin + a symbolic flat P&L on close (the mock does not
            # track per-position entry; the real accounting lives in the paper
            # futures book — this only proves the protocol path).
            self.balances["USDT"] = self.balances.get("USDT", 0.0) + margin - fee

        order = {
            "orderId": str(uuid.uuid4().int % 10**10),
            "symbol": sym, "side": side, "type": a.get("type", "MARKET"),
            "positionSide": a.get("positionSide", "BOTH"),
            "status": "FILLED", "avgPrice": round(price, 8), "price": round(price, 8),
            "executedQty": round(qty, 8),
            "cummulativeQuoteQty": round(notional, 2), "commission": round(fee, 6),
            "leverage": leverage, "reduceOnly": reduce_only,
            "transactTime": int(time.time() * 1000),
        }
        self.orders.append(order)
        return self._ok(order)

    async def _cancel(self, a: dict) -> dict:
        return self._error("Order not found or already filled.")

    async def _open_orders(self, a: dict) -> dict:
        return self._ok({"orders": []})  # market orders fill immediately

    async def _history(self, a: dict) -> dict:
        sym = (a.get("symbol") or "").upper()
        rows = [o for o in self.orders if not sym or o["symbol"] == sym]
        return self._ok({"orders": rows[-25:]})

    async def _transfer(self, a: dict) -> dict:
        frm = (a.get("fromWallet") or "").upper()
        if frm in ("MAIN", "SPOT_MAIN", "MASTER"):
            return self._error(
                "An agent cannot move funds from your main account into the "
                "sub-account. That transfer is manual, by design."
            )
        return self._ok({"transferred": a.get("amount"), "asset": a.get("asset"),
                         "from": frm, "to": a.get("toWallet")})

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _ok(payload: dict) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload, "isError": False}

    @staticmethod
    def _error(message: str) -> dict:
        return {"content": [{"type": "text", "text": message}], "isError": True}


def build_router(mock: MockBinanceMCP) -> APIRouter:
    """Mount the mock at /mock/mcp so a real MCP client can talk to it."""
    router = APIRouter()

    @router.post("/mock/mcp")
    async def rpc(request: Request):
        body = await request.json()
        method, rid = body.get("method"), body.get("id")
        params = body.get("params", {})

        if method == "initialize":
            sid = uuid.uuid4().hex
            mock.sessions.add(sid)
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rid, "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "binance-mcp-server (mock)", "version": "1.0.0"},
                }},
                headers={"Mcp-Session-Id": sid},
            )
        if method == "notifications/initialized":
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {}})
        if method == "tools/list":
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            result = await mock.call_tool(params.get("name", ""), params.get("arguments", {}))
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})

        return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"Method not found: {method}"}})

    return router
