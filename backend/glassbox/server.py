"""
GlassBox — HTTP and WebSocket server.

Binds to 127.0.0.1 by default. This is an operator console for a system that can
move money; it has no business listening on 0.0.0.0 because someone typed
--host to make a demo work.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import FRONTEND_DIR, Mode, Settings, ensure_dirs
from .engine import Engine

# A per-process token. Printed once at startup and required on every mutating
# call, so a stray page in another browser tab cannot press the buttons.
SESSION_TOKEN = secrets.token_urlsafe(24)

engine: Engine | None = None
_mock = None  # bound below once the mock router is mounted
_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _json_safe(obj: Any) -> Any:
    """
    Strip out values that json.dumps will happily emit but no browser will
    parse. Python writes float("inf") as the bare token `Infinity`, which is
    not valid JSON, and a single one silently kills the entire frame — so the
    dashboard freezes with no error anywhere near the cause.
    """
    import math

    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _broadcast(kind: str, payload: dict) -> None:
    if not _clients or _loop is None:
        return
    message = json.dumps({"kind": kind, "payload": _json_safe(payload)}, default=str)

    async def send() -> None:
        dead = []
        for ws in list(_clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)

    asyncio.run_coroutine_threadsafe(send(), _loop)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, _loop
    ensure_dirs()
    _loop = asyncio.get_running_loop()
    engine = Engine(Settings.from_env(), emit=_broadcast)
    # The mock sub-account prices its fills from the live Binance public API,
    # so the numbers in a mock demo are real even though the account is not.
    if _mock is not None:
        _mock.feeds = engine.feeds
    await engine.tick()  # populate state so the first page load is not empty

    # Start the tick loop automatically for every mode that cannot place a
    # real order — paper, shadow, and mock are all safe to run unattended the
    # instant the process boots, and requiring an extra click before real
    # Binance data even starts moving is exactly the friction this removes.
    #
    # Live and bridge are deliberately excluded: those modes are only reached
    # in the first place by an explicit `--mode live` flag or a Control
    # Center switch that already required a separate confirmation, and
    # auto-starting a live trading loop the instant a process boots — with no
    # further action from the operator — is the one thing this project is
    # built to never do.
    if engine.settings.mode not in (Mode.LIVE, Mode.BRIDGE):
        engine.start()

    yield
    if engine:
        await engine.stop()


app = FastAPI(title="GlassBox", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def _apply_security_headers(request, call_next):
    """
    Defence in depth on every response.

    The console is already loopback-only and token-gated for writes, so this
    is not the primary control — but a strict Content-Security-Policy means
    that even if a future change introduced an injection point, injected
    script could not execute or phone home, and `frame-ancestors 'none'`
    stops another page framing the console to trick someone into clicking
    through a live-trading confirmation.
    """
    from .security import security_headers

    response = await call_next(request)
    for header, value in security_headers().items():
        response.headers[header] = value
    return response


def require_token(x_glassbox_token: str | None) -> None:
    if x_glassbox_token != SESSION_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing session token.")


def _engine() -> Engine:
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine is still starting.")
    return engine


# --------------------------------------------------------------------------
# Read endpoints
# --------------------------------------------------------------------------


@app.get("/api/session")
async def session() -> dict[str, Any]:
    """The frontend fetches this once from localhost to learn the token."""
    return {"token": SESSION_TOKEN, "mode": _engine().settings.mode.value}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return _engine().status()


@app.get("/api/constitution")
async def constitution() -> dict[str, Any]:
    e = _engine()
    return {
        "path": str(e.constitution.path),
        "rules": e.constitution.describe(),
        "raw": e.constitution.path.read_text(encoding="utf-8")
        if e.constitution.path and e.constitution.path.exists()
        else "",
    }


@app.get("/api/ledger")
async def ledger(limit: int = 200) -> dict[str, Any]:
    e = _engine()
    return {
        "height": e.ledger.height,
        "head": e.ledger.head,
        "records": e.ledger.read(limit=limit),
    }


@app.get("/api/markets")
async def markets(
    quote: str = "USDT", search: str = "", sort: str = "volume", limit: int = 200
) -> dict[str, Any]:
    """
    Every tradable pair on Binance, live, searchable and sortable.

    This is the browser the operator uses to decide what belongs on the
    allowlist, so it shows the things that decision actually depends on:
    real volume, real spread, and whether the book is deep enough to trade.
    """
    e = _engine()
    try:
        uni = await e.feeds.universe(quote)
        tickers = await e.feeds.tickers_24h()
        books = await e.feeds.book_tickers()
        rates = await e.feeds.usd_rates()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Binance market data unavailable: {exc}")

    allowlist = set(
        e.constitution.rules_config.get("symbol_allowlist", {}).get("symbols") or []
    )
    q = search.strip().upper()
    rows = []
    for u in uni:
        sym = u["symbol"]
        if q and q not in sym and q not in u["base"]:
            continue
        t = tickers.get(sym)
        if not t:
            continue
        b = books.get(sym, {})
        price = float(t.get("lastPrice", 0) or 0)
        bid = float(b.get("bidPrice", price) or price)
        ask = float(b.get("askPrice", price) or price)

        # Prefer the websocket quote when it is live; it is sub-second and free.
        lq = e.stream.quote(sym)
        streamed = bool(lq and lq.price > 0 and not lq.is_stale)
        if streamed:
            price = lq.price
            if lq.bid and lq.ask:
                bid, ask = lq.bid, lq.ask

        # Volume must be converted to USD before anything is ranked by it.
        # A TRY-quoted pair and a USDT-quoted pair are otherwise compared in
        # different currencies, which puts IDR pairs at the top of every board.
        rate = rates.get(u["quote"], 0.0)
        vol_native = float(t.get("quoteVolume", 0) or 0)
        vol_usd = vol_native * rate if rate else 0.0

        rows.append({
            "volume_24h_usd_normalised": vol_usd,
            "quote_usd_rate": rate,
            "streamed": streamed,
            "symbol": sym, "base": u["base"], "quote": u["quote"],
            "price": price,
            "change_24h_pct": float(t.get("priceChangePercent", 0) or 0),
            "volume_24h_usd": float(t.get("quoteVolume", 0) or 0),
            "trades_24h": int(t.get("count", 0) or 0),
            "high_24h": float(t.get("highPrice", 0) or 0),
            "low_24h": float(t.get("lowPrice", 0) or 0),
            "spread_bps": round((ask - bid) / max(price, 1e-9) * 10000, 2) if price else 0,
            "margin": u["margin"],
            "min_notional": u["min_notional"],
            "on_allowlist": sym in allowlist,
            "watched": sym in e.symbols,
        })

    keys = {
        "volume": lambda r: -r["volume_24h_usd_normalised"],
        "change": lambda r: -r["change_24h_pct"],
        "losers": lambda r: r["change_24h_pct"],
        "spread": lambda r: r["spread_bps"],
        "symbol": lambda r: r["symbol"],
        "trades": lambda r: -r["trades_24h"],
    }
    rows.sort(key=keys.get(sort, keys["volume"]))
    return {
        "total_pairs": len(uni), "matched": len(rows),
        "quotes_available": sorted({u["quote"] for u in await e.feeds.universe(quote="")}),
        "rows": rows[:limit],
        "feeds": e.feeds.health.to_dict(),
        "ratelimit": e.feeds.governor.status(),
        "stream": e.stream.status(),
    }


@app.get("/api/coins")
async def coins(search: str = "", limit: int = 120) -> dict[str, Any]:
    """
    Asset-level view: every coin on Binance, with its activity aggregated
    across all the pairs it trades in, converted to USD.

    Pair-level tables answer "what is BTCUSDT doing". This answers "what is BTC
    doing", which is the question an operator actually asks first.
    """
    e = _engine()
    try:
        assets = await e.feeds.assets()
        tickers = await e.feeds.tickers_24h()
        rates = await e.feeds.usd_rates()
        uni = {u["symbol"]: u for u in await e.feeds.universe(quote="")}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    q = search.strip().upper()
    out = []
    for code, a in assets.items():
        if q and q not in code:
            continue
        vol_usd = 0.0
        trades = 0
        best_price = 0.0
        best_vol = 0.0
        changes: list[tuple[float, float]] = []
        for sym in a["pairs"]:
            t = tickers.get(sym)
            pair = uni.get(sym)
            if not t or not pair:
                continue
            rate = rates.get(pair["quote"], 0.0)
            if not rate:
                continue
            v = float(t.get("quoteVolume", 0) or 0) * rate
            vol_usd += v
            trades += int(t.get("count", 0) or 0)
            price_usd = float(t.get("lastPrice", 0) or 0) * rate
            changes.append((float(t.get("priceChangePercent", 0) or 0), v))
            if v > best_vol:
                best_vol, best_price = v, price_usd
        if vol_usd <= 0:
            continue
        # Weight the headline change by where the volume actually is, so a dead
        # pair cannot set the number for an actively traded coin.
        wsum = sum(w for _c, w in changes) or 1.0
        change = sum(c * w for c, w in changes) / wsum
        out.append({
            "asset": code, "price_usd": best_price,
            "change_24h_pct": round(change, 3),
            "volume_24h_usd": vol_usd, "trades_24h": trades,
            "pair_count": a["pair_count"], "quotes": a["quotes"],
            "is_quote_asset": a["as_quote"] > 0,
        })

    out.sort(key=lambda r: -r["volume_24h_usd"])
    return {
        "total_assets": len(assets), "matched": len(out), "rows": out[:limit],
        "usd_rates": {k: round(v, 6) for k, v in rates.items()},
        "note": "Volumes summed across every pair the coin trades in, converted to USD.",
    }


@app.get("/api/mcp")
async def mcp_status() -> dict[str, Any]:
    e = _engine()
    return {
        "status": e.mcp.status.to_dict(),
        "mode": e.settings.mode.value,
        "endpoint": e.mcp.endpoint,
        "has_session": e.mcp.tokens is not None,
        "execution": e.executor.summary(),
    }


@app.post("/api/mcp/connect")
async def mcp_connect(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return await _engine().connect_mcp()


@app.post("/api/mcp/disconnect")
async def mcp_disconnect(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().disconnect_mcp()


@app.get("/api/mcp/client-id")
async def get_client_id() -> dict[str, Any]:
    e = _engine()
    env_locked = bool(os.environ.get("GLASSBOX_MCP_CLIENT_ID"))
    return {
        "configured": bool(e.mcp.client_id),
        "value": e.mcp.client_id or None,
        "env_locked": env_locked,
        "note": (
            "Set via GLASSBOX_MCP_CLIENT_ID — clear the environment variable "
            "to manage this from the dashboard instead." if env_locked else
            "Saved from the dashboard; persists across restarts."
        ),
    }


class ClientIdBody(BaseModel):
    url: str = ""


@app.post("/api/mcp/client-id")
async def set_client_id(body: ClientIdBody, x_glassbox_token: str = Header(default=None)):
    """
    The operator-facing control that was missing: previously the only way to
    set the OAuth client id was the GLASSBOX_MCP_CLIENT_ID environment
    variable, which meant "Authorize with Binance" could fail with an error
    pointing at a shell restart in a dashboard otherwise built to need none.
    """
    require_token(x_glassbox_token)
    e = _engine()
    if os.environ.get("GLASSBOX_MCP_CLIENT_ID"):
        raise HTTPException(
            status_code=409,
            detail=(
                "GLASSBOX_MCP_CLIENT_ID is set in the environment and takes "
                "priority. Unset it and restart to manage this from here instead."
            ),
        )
    result = e.set_mcp_client_id(body.url)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


class GenerateMetadataBody(BaseModel):
    hosted_at: str


@app.post("/api/mcp/client-id/generate")
async def generate_client_metadata(
    body: GenerateMetadataBody, x_glassbox_token: str = Header(default=None)
):
    """
    Produces the exact JSON document Binance expects, so nobody has to
    hand-write OAuth client metadata. The operator hosts this at the URL they
    provide (GitHub Pages needs no shell — see docs/GITHUB_UPLOAD.md), then
    saves that same URL via POST /api/mcp/client-id.
    """
    require_token(x_glassbox_token)
    if not body.hosted_at.strip().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="hosted_at must be a full URL.")
    return _engine().generate_client_metadata(body.hosted_at)


@app.post("/api/mcp/authorize/start")
async def mcp_authorize_start(x_glassbox_token: str = Header(default=None)):
    """
    Step 1 of connecting a real Binance account from the dashboard, with no
    shell required. Opens a loopback listener and returns the URL for the
    frontend to open in a new tab; the person authorizes there, Binance
    redirects back to this machine, and /authorize/status picks it up.
    """
    require_token(x_glassbox_token)
    e = _engine()
    if not e.mcp.client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No OAuth client id is configured. Binance requires a URL to a "
                "hosted client-metadata document — set GLASSBOX_MCP_CLIENT_ID "
                "and restart, or connect once via Claude Code, which handles "
                "this automatically: claude mcp add binance-mcp-server "
                "--transport http https://agent.binance.com/mcp/agentic"
            ),
        )
    try:
        url = await e.mcp.start_authorization()
        return {"authorize_url": url, "expires_in_s": 300}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/mcp/authorize/status")
async def mcp_authorize_status() -> dict[str, Any]:
    """Step 2: poll this every second or two while the authorize tab is open."""
    e = _engine()
    result = await e.mcp.poll_authorization()
    if result["status"] == "connected":
        # The tokens are saved; finish the handshake the same way a normal
        # connect does, so tools are discovered immediately.
        connected = await e.connect_mcp()
        result["session"] = connected
    return result


@app.post("/api/mcp/authorize/cancel")
async def mcp_authorize_cancel(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    _engine().mcp.cancel_authorization()
    return {"ok": True}


class ModeBody(BaseModel):
    mode: str
    live_data: bool = True
    confirm_live: bool = False


@app.post("/api/mode")
async def set_mode(body: ModeBody, x_glassbox_token: str = Header(default=None)):
    """
    Switch execution surface from the dashboard's Control Center — this is
    what replaces `python -m glassbox serve --mode X` for anyone who would
    rather not open a shell at all.
    """
    require_token(x_glassbox_token)
    e = _engine()
    try:
        mode = Mode(body.mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode '{body.mode}'. Choose paper, shadow, mock, live or bridge.",
        )
    result = await e.set_mode(mode, live_data=body.live_data, confirm_live=body.confirm_live)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@app.get("/api/mcp/tools")
async def mcp_tools() -> dict[str, Any]:
    e = _engine()
    try:
        tools = await e.mcp.list_tools()
        return {"tools": tools, "count": len(tools)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/mcp/balances")
async def mcp_balances() -> dict[str, Any]:
    return await _engine().executor.balances()


class ToolCallBody(BaseModel):
    name: str
    arguments: dict = {}


@app.post("/api/mcp/call")
async def mcp_call(body: ToolCallBody, x_glassbox_token: str = Header(default=None)):
    """
    Call a Binance MCP tool directly. Read-only tools only.

    A console that can place arbitrary orders defeats the Constitution, so
    anything that writes must go through an Intent and be adjudicated.
    """
    require_token(x_glassbox_token)
    e = _engine()
    blocked = ("order", "trade", "transfer", "cancel", "buy", "sell", "withdraw")
    if any(w in body.name.lower() for w in blocked):
        raise HTTPException(
            status_code=403,
            detail=(
                "Write tools cannot be called from the console. Orders must be "
                "raised as an Intent so the Constitution and Guardian can rule "
                "on them, and so a receipt exists."
            ),
        )
    try:
        return await e.mcp.call_tool(body.name, body.arguments)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/universe/discover")
async def discover_universe(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return await _engine().discover_universe()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Everything an operator needs to know about whether the data is trustworthy."""
    e = _engine()
    return {
        "feeds": e.feeds.health.to_dict(),
        "ratelimit": e.feeds.governor.status(),
        "stream": e.stream.status(),
        "coverage": e.ctx.coverage() if e.ctx else None,
        "ledger": {"height": e.ledger.height, "head": e.ledger.head},
        "mode": e.settings.mode.value,
        "live_market_data": e.settings.live_market_data,
    }


@app.get("/api/symbol/{symbol}")
async def symbol_detail(symbol: str) -> dict[str, Any]:
    """Deep view of one pair: candles, book shape, and what each analyst says."""
    e = _engine()
    symbol = symbol.upper()
    try:
        ctx = await e.data.build([symbol], intended_notional_usd=2500.0)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    signals = []
    for a in e.council.analysts:
        try:
            sig = await a.analyse(symbol, ctx)
            signals.append(sig.to_dict())
        except Exception as exc:
            signals.append({"agent": a.name, "stance": "neutral", "confidence": 0.0,
                            "rationale": f"Analyst failed: {type(exc).__name__}",
                            "evidence": {"data_quality": "error"}})
    k = ctx.klines.get(symbol, [])
    book = ctx.depth.get(symbol, {})
    return {
        "symbol": symbol,
        "price": ctx.price(symbol),
        "ticker": ctx.tickers.get(symbol, {}),
        "candles": [
            {"t": c["open_time"], "o": c["open"], "h": c["high"],
             "l": c["low"], "c": c["close"], "v": c["volume"]}
            for c in k[-120:]
        ],
        "book": {
            "bids": book.get("bids", [])[:20],
            "asks": book.get("asks", [])[:20],
        },
        "derivatives": ctx.derivatives.get(symbol, {}),
        "signals": signals,
        "coverage": ctx.coverage(),
    }


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, interval: str = "1h", limit: int = 200) -> dict[str, Any]:
    """Real OHLCV for charting, straight from Binance."""
    e = _engine()
    try:
        k = await e.feeds.klines(symbol.upper(), interval, min(limit, 500))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "symbol": symbol.upper(), "interval": interval,
        "candles": [
            {"t": c["open_time"], "o": c["open"], "h": c["high"],
             "l": c["low"], "c": c["close"], "v": c["volume"]}
            for c in k
        ],
    }


@app.get("/api/news")
async def news(force: bool = False) -> dict[str, Any]:
    """
    Current headlines and the event-risk posture they imply.

    `force=true` bypasses the 5-minute cache — used by the dashboard's
    "Refresh now" button so an operator can pull the latest immediately
    rather than waiting for the next scheduled fetch.
    """
    e = _engine()
    await e.news.refresh(force=force)
    e.news.symbols = e.symbols
    return e.news.status()


class PaymentBody(BaseModel):
    counterparty: str
    amount_usd: float
    purpose: str
    asset: str = "USDT"


@app.post("/api/payments/send")
async def send_payment(body: PaymentBody, x_glassbox_token: str = Header(default=None)):
    """Agent-to-agent payment. Fails closed — see workflows.PaymentRail."""
    require_token(x_glassbox_token)
    from .workflows import PaymentRefused

    e = _engine()
    if e.constitution.doc.get("rules", {}).get("kill_switch", {}).get("engaged"):
        raise HTTPException(
            status_code=403,
            detail="Kill switch is engaged — no payments while all new risk is stopped.",
        )
    try:
        payment = await e.payments.send(
            body.counterparty, body.amount_usd, body.purpose,
            justification_hash=e.ledger.head, asset=body.asset,
        )
    except PaymentRefused as exc:
        e.ledger.append("payment_refused", {
            "counterparty": body.counterparty, "amount_usd": body.amount_usd,
            "reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))
    e.ledger.append("payment_sent", payment.to_dict())
    e._log("info", "payments", payment.message, payment.to_dict())
    return {"ok": True, **payment.to_dict()}


class CounterpartyBody(BaseModel):
    counterparty: str
    address: str = ""   # a real settlement address — required when allow=True
    allow: bool = True


@app.post("/api/payments/allowlist")
async def payment_allowlist(
    body: CounterpartyBody, x_glassbox_token: str = Header(default=None)
):
    """
    Allowlist (or remove) a payment counterparty.

    A name alone is not enough to receive a real settlement — adding one
    requires the real address funds would actually be sent to, so "I
    allowlisted someone" and "money could reach them" are the same statement,
    not two that can silently drift apart.
    """
    require_token(x_glassbox_token)
    e = _engine()
    if body.allow:
        if not body.address:
            raise HTTPException(
                status_code=400,
                detail="A real settlement address is required to allowlist a counterparty.",
            )
        e.payments.allowlist[body.counterparty] = body.address
    else:
        e.payments.allowlist.pop(body.counterparty, None)
    e.ledger.append("payment_allowlist_change", {
        "counterparty": body.counterparty, "address": body.address, "allowed": body.allow})
    return {"ok": True, "allowlist": e.payments.allowlist}


@app.get("/api/payments/wallet")
async def payment_wallet_status() -> dict[str, Any]:
    """The real settlement wallet's address, balance, and faucet links."""
    return await _engine().settlement_wallet.status()


class InspectBody(BaseModel):
    query: str
    chain: str = "bnb"


@app.post("/api/chain/inspect")
async def chain_inspect(body: InspectBody) -> dict[str, Any]:
    """
    Read-only lookup of any address or transaction hash on a public chain
    (Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon,
    Optimism, Avalanche). Queries each chain's own public endpoint directly —
    it never signs or sends anything.
    """
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Enter an address or transaction hash.")
    return await _engine().chain_inspector.inspect(body.query, body.chain)


@app.get("/api/chain/chains")
async def chain_list() -> dict[str, Any]:
    """The chains the inspector can query."""
    from .settlement import INSPECT_CHAINS

    return {
        "chains": [
            {"id": k, "label": v["label"], "symbol": v["symbol"]}
            for k, v in INSPECT_CHAINS.items()
        ]
    }


class OnchainBody(BaseModel):
    kind: str = "stake"
    venue: str
    asset: str = "USDT"
    amount_usd: float
    apy_pct: float = 0.0
    lockup_days: int = 0


@app.post("/api/onchain/execute")
async def onchain_execute(body: OnchainBody, x_glassbox_token: str = Header(default=None)):
    """Turn a yield decision into a real MCP instruction."""
    require_token(x_glassbox_token)
    e = _engine()
    if e.constitution.doc.get("rules", {}).get("kill_switch", {}).get("engaged"):
        raise HTTPException(status_code=403, detail="Kill switch is engaged.")
    state = e.portfolio.state_dict(e._marks())
    action = e.onchain.build(
        body.kind, body.venue, body.asset, body.amount_usd, body.apy_pct,
        equity_usd=state.get("equity_usd", 0), lockup_days=body.lockup_days,
        justification_hash=e.ledger.head,
    )
    e.ledger.append(f"onchain_{action.status}", action.to_dict())
    e._log(
        "info" if action.status == "instructed" else "deny",
        "onchain", action.message, action.to_dict(),
    )
    return {"ok": action.status == "instructed", **action.to_dict()}


@app.get("/api/report")
async def report() -> dict[str, Any]:
    """A complete, exportable account of what the agent did and why."""
    from .workflows import build_report

    return build_report(_engine())


@app.get("/api/macro")
async def macro(force: bool = False) -> dict[str, Any]:
    """US rate regime, curve shape, and the scheduled FOMC calendar."""
    e = _engine()
    await e.macro.refresh(force=force)
    return e.macro.status()


@app.get("/api/calibration")
async def calibration() -> dict[str, Any]:
    e = _engine()
    return e.calibration.summary([a.name for a in e.council.analysts])


@app.get("/api/anchor")
async def anchor_status() -> dict[str, Any]:
    return _engine().anchor.status()


@app.get("/api/anchor/verify")
async def anchor_verify() -> dict[str, Any]:
    return _engine().anchor.verify()


@app.post("/api/anchor/create")
async def anchor_create(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    e = _engine()
    cp = await e.anchor.create(e.settings.anchor_webhook_url)
    return cp.to_dict()


class WatchBody(BaseModel):
    symbols: list[str]


@app.post("/api/watchlist")
async def set_watchlist(body: WatchBody, x_glassbox_token: str = Header(default=None)):
    """
    Change which pairs the agents watch. Note this does NOT change the
    Constitution's allowlist — a symbol must be on both before it can be traded.
    Watching is looking; the allowlist is permission.
    """
    require_token(x_glassbox_token)
    e = _engine()
    syms = [s.strip().upper() for s in body.symbols if s.strip()][:12]
    if not syms:
        raise HTTPException(status_code=400, detail="Provide at least one symbol.")
    e.symbols = syms
    e.ledger.append("watchlist_change", {"symbols": syms})
    allowed = set(e.constitution.rules_config.get("symbol_allowlist", {}).get("symbols") or [])
    return {
        "symbols": syms,
        "tradable": [s for s in syms if not allowed or s in allowed],
        "watch_only": [s for s in syms if allowed and s not in allowed],
        "note": "Symbols not on the Constitution allowlist are monitored but cannot be traded.",
    }


class ReplayBody(BaseModel):
    symbols: list[str] | None = None
    interval: str = "1h"
    bars: int = 600
    equity: float = 10000.0


@app.post("/api/replay")
async def replay(body: ReplayBody, x_glassbox_token: str = Header(default=None)):
    """Backtest against real Binance candles, not a simulator."""
    require_token(x_glassbox_token)
    from .replay import HistoricalReplay

    e = _engine()
    r = HistoricalReplay(e.settings)
    try:
        res = await r.run(
            body.symbols or e.symbols, interval=body.interval,
            bars=min(body.bars, 2000), equity=body.equity,
        )
        return res.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await r.aclose()


@app.get("/api/ledger/verify")
async def verify_ledger() -> dict[str, Any]:
    return _engine().ledger.verify()


# ---------------------------------------------------------------------------
# Control Center — the CLI diagnostics, as dashboard buttons.
#
# Every one of these calls the exact same function `python -m glassbox drill`
# / `backtest` / `calibrate` calls, and every one builds its own isolated,
# throwaway Engine (see testkit.py) so a click here can never corrupt the
# operator's real audit ledger, drain the real x402 budget, or leave stray
# positions in the paper portfolio someone is actually watching.
# ---------------------------------------------------------------------------


@app.post("/api/ops/drill")
async def ops_drill(x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    from .testkit import run_drills

    return await run_drills()


class BacktestBody(BaseModel):
    scenario: str = "calm"
    ticks: int = 400
    equity: float = 10_000.0


@app.post("/api/ops/backtest")
async def ops_backtest(
    body: BacktestBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    require_token(x_glassbox_token)
    from .testkit import run_scenario_backtest

    if body.scenario not in ("calm", "bull", "bear", "chop", "crash"):
        raise HTTPException(status_code=400, detail=f"Unknown scenario '{body.scenario}'.")
    return await run_scenario_backtest(
        body.scenario, min(body.ticks, 3000), body.equity
    )


class CalibrateBody(BaseModel):
    symbols: list[str] | None = None
    interval: str = "1h"
    bars: int = 400
    horizon: int = 6
    apply: bool = True  # write the result into the live engine's Track Record


@app.post("/api/ops/calibrate")
async def ops_calibrate(
    body: CalibrateBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """
    Backfills the analyst track record from real Binance history. With
    `apply=true` (the default), the freshly graded tracker replaces the live
    engine's in-memory one and is saved to disk, so the Track Record tab shows
    it immediately rather than after the next restart.
    """
    require_token(x_glassbox_token)
    from .testkit import run_calibration_backfill

    e = _engine()
    symbols = body.symbols or e.symbols
    result = await run_calibration_backfill(
        symbols, body.interval, min(body.bars, 2000), body.horizon
    )
    tracker = result.pop("_tracker")
    if body.apply:
        tracker.path = e.calibration.path
        tracker.save()
        e.calibration = tracker
        e.council.calibration = tracker
    return result


@app.get("/api/receipt/{receipt_id}")
async def receipt(receipt_id: str) -> dict[str, Any]:
    for r in _engine().receipts:
        if r["receipt_id"] == receipt_id:
            return r
    raise HTTPException(status_code=404, detail="Receipt not found.")


# --------------------------------------------------------------------------
# Write endpoints — all token-protected
# --------------------------------------------------------------------------


class ConfirmBody(BaseModel):
    intent_id: str
    approve: bool


class ToggleBody(BaseModel):
    on: bool


class ScenarioBody(BaseModel):
    scenario: str


class ShockBody(BaseModel):
    symbol: str
    pct: float


@app.post("/api/engine/start")
async def start(x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    e = _engine()
    e.start()
    return {"running": True}


@app.post("/api/engine/stop")
async def stop(x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    await _engine().stop()
    return {"running": False}


class AutonomyBody(BaseModel):
    enabled: bool
    acknowledged: bool = False


@app.post("/api/engine/autonomy")
async def set_autonomy(
    body: AutonomyBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """
    Turn autonomous trading on or off.

    When off (the default), the engine still runs, streams live data and
    publishes all analysis — it simply does not open positions on its own.
    Enabling it is a deliberate act and requires acknowledgement, so nobody
    ends up with unattended trades they did not ask for.
    """
    require_token(x_glassbox_token)
    e = _engine()
    if body.enabled and not body.acknowledged:
        return {
            "ok": False,
            "needs_acknowledgement": True,
            "message": (
                "Autonomous trading lets the engine open positions on its own, "
                "following the Council and Narrative scout, without asking each "
                "time. Every trade still passes the full Constitution and "
                "Guardian, and in paper mode no real money is involved — but "
                "you will return to find positions you did not place by hand. "
                "Confirm to enable."
            ),
        }
    e.autonomous = bool(body.enabled)
    e.ledger.append("autonomy_change", {"enabled": e.autonomous})
    e._log(
        "info", "engine",
        "Autonomous trading enabled — the engine may now open positions on its own."
        if e.autonomous else
        "Autonomous trading disabled — the engine will not open new positions by itself.",
    )
    return {"ok": True, "autonomous": e.autonomous}


class ClosePositionBody(BaseModel):
    symbol: str


@app.post("/api/position/close")
async def close_position(
    body: ClosePositionBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """Manually close (sell out of) an open position — the operator's stop button."""
    require_token(x_glassbox_token)
    return await _engine().close_position(body.symbol)


class ManualTradeBody(BaseModel):
    symbol: str
    side: str
    notional_usd: float
    reason: str = "Manually placed by the operator."
    acknowledged_risk: bool = False


@app.post("/api/second-opinion")
async def second_opinion(body: ManualTradeBody) -> dict[str, Any]:
    """
    What every analysis layer thinks about a trade you're considering — before
    you place it. Read-only and advisory; it never blocks anything.
    """
    from .second_opinion import evaluate_manual_trade

    return evaluate_manual_trade(
        symbol=body.symbol.upper(), side=body.side.upper(),
        notional_usd=body.notional_usd, engine=_engine(),
    ).to_dict()


def _desk():
    """The Analyst Desk, attached lazily so the engine and its tests are untouched."""
    e = _engine()
    desk = getattr(e, "_analyst_desk", None)
    if desk is None:
        from .analyst_desk import AnalystDesk
        desk = AnalystDesk(e)
        e._analyst_desk = desk
    return desk


class CouncilAnalyzeBody(BaseModel):
    symbol: str


@app.post("/api/council/analyze")
async def council_analyze(body: CouncilAnalyzeBody) -> dict[str, Any]:
    """
    Run the full five-analyst Council on any Binance pair on demand — including
    ones the engine does not trade — and return the briefing behind the verdict.
    Read-only and advisory: it fetches public market data and analyses it, and
    changes nothing.
    """
    symbol = (body.symbol or "").upper().strip()
    if not symbol:
        return {"ok": False, "error": "No symbol given."}
    return await _desk().analyze(symbol)


class CouncilChatTurn(BaseModel):
    role: str
    content: str


class CouncilChatBody(BaseModel):
    message: str
    history: list[CouncilChatTurn] = []
    lang: str | None = None


@app.post("/api/council/chat")
async def council_chat(body: CouncilChatBody) -> dict[str, Any]:
    """
    Ask the desk about a pair and discuss it. The reply is grounded in the same
    Council, indicators, Guardian and Constitution the engine runs; every figure
    it cites was just computed from live data. Read-only and advisory.
    """
    history = [{"role": t.role, "content": t.content} for t in body.history]
    return await _desk().answer(body.message, history, lang=body.lang)


@app.get("/api/desk/llm-status")
async def desk_llm_status() -> dict[str, Any]:
    """Whether a language model is connected to the desk, and which one."""
    return _desk().llm_status()


class DeskKeyBody(BaseModel):
    provider: str
    key: str


@app.post("/api/desk/llm-key")
async def desk_llm_connect(
    body: DeskKeyBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """Connect a free model (Groq or Gemini) at runtime by pasting a key. The key
    is verified when possible, used immediately, and saved to a local .env so it
    persists. Never leaves this machine except to the chosen provider."""
    require_token(x_glassbox_token)
    return await _desk().set_llm_key(body.provider, body.key)


@app.post("/api/desk/llm-disconnect")
async def desk_llm_disconnect(x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    """Disconnect the model; the grounded desk answers again."""
    require_token(x_glassbox_token)
    return _desk().clear_llm_key()


@app.get("/api/desk/llm-models")
async def desk_llm_models() -> dict[str, Any]:
    """The connected provider's available models (best first) for the picker."""
    return await _desk().list_models_for_ui()


class DeskModelBody(BaseModel):
    model: str


@app.post("/api/desk/llm-model")
async def desk_llm_model(
    body: DeskModelBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """Pin a specific model within the connected provider."""
    require_token(x_glassbox_token)
    return _desk().set_model(body.model)


def _onchain():
    """The paper Payment / on-chain action book, attached lazily."""
    e = _engine()
    oc = getattr(e, "_onchain_actions", None)
    if oc is None:
        from .onchain_actions import OnchainActions
        oc = OnchainActions(e)
        e._onchain_actions = oc
    return oc


@app.get("/api/onchain/state")
async def onchain_state() -> dict[str, Any]:
    """The paper wallet, staking positions, holdings and signed action receipts."""
    return _onchain().state()


class StakeBody(BaseModel):
    asset: str
    amount: float


class UnstakeBody(BaseModel):
    asset: str
    amount: float


class ClaimBody(BaseModel):
    asset: str


class SwapBody(BaseModel):
    from_asset: str
    to_asset: str
    amount: float


class PayBody(BaseModel):
    to: str
    amount: float
    memo: str = ""


@app.post("/api/onchain/stake")
async def onchain_stake(body: StakeBody, x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    return _onchain().stake(body.asset, body.amount)


@app.post("/api/onchain/unstake")
async def onchain_unstake(body: UnstakeBody, x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    return _onchain().unstake(body.asset, body.amount)


@app.post("/api/onchain/claim")
async def onchain_claim(body: ClaimBody, x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    return _onchain().claim(body.asset)


@app.post("/api/onchain/swap")
async def onchain_swap(body: SwapBody, x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    return _onchain().swap(body.from_asset, body.to_asset, body.amount)


@app.post("/api/onchain/pay")
async def onchain_pay(body: PayBody, x_glassbox_token: str = Header(default=None)) -> dict[str, Any]:
    require_token(x_glassbox_token)
    return _onchain().pay(body.to, body.amount, body.memo)


@app.post("/api/manual-trade")
async def manual_trade(
    body: ManualTradeBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """
    Place a trade on demand instead of waiting for the Council to
    independently reach one — see Engine.manual_intent for what this does
    and does not bypass.
    """
    require_token(x_glassbox_token)
    if body.notional_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")

    from .second_opinion import evaluate_manual_trade

    e = _engine()
    opinion = evaluate_manual_trade(
        symbol=body.symbol.upper(), side=body.side.upper(),
        notional_usd=body.notional_usd, engine=e,
    )
    # When the system materially disagrees, the trade is held until the
    # operator explicitly acknowledges the objection. This is not a block —
    # resubmitting with acknowledged_risk=true proceeds — it exists so nobody
    # trades against information the system already had without seeing it.
    if opinion.requires_acknowledgement and not body.acknowledged_risk:
        return {
            "ok": False,
            "needs_acknowledgement": True,
            "second_opinion": opinion.to_dict(),
            "error": opinion.headline,
        }

    result = await e.manual_intent(
        body.symbol, body.side, body.notional_usd,
        body.reason + (
            f" [Operator proceeded against a {opinion.disagreement_pct:.0f}% "
            f"disagreement score.]" if opinion.requires_acknowledgement else ""
        ),
    )
    result["second_opinion"] = opinion.to_dict()
    return result


class FuturesTradeBody(BaseModel):
    symbol: str
    action: str            # OPEN_LONG | OPEN_SHORT | CLOSE
    margin_usd: float = 0.0  # margin committed; position notional = margin × leverage
    leverage: float = 3.0
    reason: str = "Manually placed by the operator (futures)."
    acknowledged_risk: bool = False


@app.post("/api/futures/second-opinion")
async def futures_second_opinion(body: FuturesTradeBody) -> dict[str, Any]:
    """What every analysis layer thinks about a futures order — advisory only."""
    from .second_opinion import evaluate_futures_trade

    e = _engine()
    notional = max(body.margin_usd, 0.0) * max(body.leverage, 1.0)
    return evaluate_futures_trade(
        symbol=body.symbol.upper(), action=body.action.upper(),
        notional_usd=notional, leverage=body.leverage, engine=e,
    ).to_dict()


@app.post("/api/futures/trade")
async def futures_trade(
    body: FuturesTradeBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """
    Open (long or short) or close a USDⓈ-M perpetual futures position — see
    Engine.futures_intent for exactly what this does and does not bypass. Spot
    buy/sell is unchanged; this is the separate, cleanly-introduced futures
    path with its own leverage and notional caps.
    """
    require_token(x_glassbox_token)
    action = body.action.upper()
    e = _engine()

    if action == "CLOSE":
        return await e.close_futures(body.symbol)

    if body.margin_usd <= 0:
        raise HTTPException(status_code=400, detail="Margin must be greater than zero.")

    from .second_opinion import evaluate_futures_trade

    notional = body.margin_usd * max(body.leverage, 1.0)
    opinion = evaluate_futures_trade(
        symbol=body.symbol.upper(), action=action,
        notional_usd=notional, leverage=body.leverage, engine=e,
    )
    if opinion.requires_acknowledgement and not body.acknowledged_risk:
        return {
            "ok": False,
            "needs_acknowledgement": True,
            "second_opinion": opinion.to_dict(),
            "error": opinion.headline,
        }

    result = await e.futures_intent(
        body.symbol, action, body.margin_usd, body.leverage,
        body.reason + (
            f" [Operator proceeded against a {opinion.disagreement_pct:.0f}% "
            f"disagreement score.]" if opinion.requires_acknowledgement else ""
        ),
    )
    result["second_opinion"] = opinion.to_dict()
    return result


@app.post("/api/futures/close")
async def futures_close(
    body: ClosePositionBody, x_glassbox_token: str = Header(default=None)
) -> dict[str, Any]:
    """Flatten an open futures position (reduce-only) — the operator's stop button."""
    require_token(x_glassbox_token)
    return await _engine().close_futures(body.symbol)


@app.post("/api/confirm")
async def confirm(body: ConfirmBody, x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return await _engine().confirm(body.intent_id, body.approve)


@app.post("/api/kill-switch")
async def kill_switch(body: ToggleBody, x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().kill_switch(body.on)


@app.post("/api/constitution/reload")
async def reload_constitution(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().reload_constitution()


@app.post("/api/scenario")
async def scenario(body: ScenarioBody, x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().set_scenario(body.scenario)


@app.post("/api/shock")
async def shock(body: ShockBody, x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().inject_shock(body.symbol, body.pct)


@app.post("/api/quarantine/clear")
async def clear_quarantine(x_glassbox_token: str = Header(default=None)):
    require_token(x_glassbox_token)
    return _engine().clear_quarantine()


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        await ws.send_text(
            json.dumps(
                {"kind": "tick", "payload": _json_safe(_engine().status())}, default=str
            )
        )
        while True:
            await ws.receive_text()  # client keepalive; no commands over WS
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _clients.discard(ws)


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------

# The mock Binance MCP server, mounted in-process. Lets the real MCP client
# exercise the entire protocol path without credentials or risk.
try:
    from .mockmcp import MockBinanceMCP, build_router

    _mock = MockBinanceMCP(starting_usdt=5000.0)
    app.include_router(build_router(_mock))
    if engine is not None:
        _mock.feeds = engine.feeds
except Exception:  # never let the mock break a live deployment
    _mock = None


@app.get("/api/mock/state")
async def mock_state() -> dict[str, Any]:
    if _mock is None:
        return {"available": False}
    if _mock.feeds is None and engine is not None:
        _mock.feeds = engine.feeds
    return {
        "available": True, "balances": _mock.balances,
        "orders": _mock.orders[-20:], "calls": _mock.calls,
        "note": "A local stand-in for the Binance Agentic sub-account.",
    }


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))
