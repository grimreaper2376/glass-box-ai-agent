"""
GlassBox — the engine.

One loop, run every tick, in a fixed order that encodes the safety argument:

    1. Observe            market data in, written to the ledger
    2. Protect            stops and targets fire before any agent gets a say
    3. Assess threat      the Guardian scores conditions
    4. Guardian override  if critical, hedge intents are built first
    5. Propose            Council and Narrative scout suggest transitions
    6. Adjudicate         Constitution rules on every intent, deterministically
    7. Veto               Guardian gets the last look
    8. Execute or queue   allowed intents fill; REQUIRE_HUMAN ones wait
    9. Route idle cash    YieldCompass parks whatever is left
   10. Receipt            everything above is chained into the audit ledger

Ordering is the design. Protection cannot be starved by analysis, the Guardian
cannot be talked out of a veto by a confident model, and nothing reaches an
exchange without leaving a signed record of why.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .agents.council import Council
from .agents.guardian import Guardian
from .agents.scouts import NarrativeScout, YieldCompass
from .config import (
    DATA_DIR,
    TOKEN_PATH,
    CapitalState,
    Clock,
    Decision,
    Intent,
    Mode,
    Settings,
    Side,
    Verdict,
)
from .constitution import Constitution
from .ledger import Ledger
from .anchor import Anchor
from .calibration import CalibrationTracker
from .feeds import BinanceFeeds
from .market import LiveMarket, SimMarket
from .marketdata import MarketContext, MarketDataService
from .macro import MacroMonitor
from .news import NewsMonitor
from .settlement import SettlementWallet
from .workflows import OnchainRail, PaymentRail, build_report
from .execution import Executor
from .mcp import CALLBACK_PORT, BinanceMCPClient
from .stream import MarketStream
from .portfolio import PaperPortfolio, Position
from .x402 import X402Meter


class Engine:
    def __init__(
        self,
        settings: Settings | None = None,
        emit: Callable[[str, dict], Any] | None = None,
        data_dir: Path | None = None,
    ):
        """
        `data_dir` isolates everything this engine writes to disk — the audit
        ledger, the calibration track record, and anchor checkpoints — into a
        directory of the caller's choosing instead of the operator's real
        `~/.glassbox`.

        This matters beyond tidiness. The adversarial safety drills deliberately
        corrupt a ledger record to prove tamper detection works. Running that
        against the operator's actual audit trail would corrupt real history the
        moment someone clicked "Run drills" in the dashboard. Every isolated test
        run — drills, scenario backtests, calibration previews — constructs an
        Engine with a fresh temporary `data_dir` for exactly this reason; see
        `testkit.py`.
        """
        self.settings = settings or Settings.from_env()
        self.emit = emit or (lambda kind, payload: None)
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR

        self.constitution = Constitution(self.settings.policy_path)
        x402_cfg = self.constitution.doc.get("x402", {})
        self.meter = X402Meter(
            daily_budget_usd=x402_cfg.get("daily_budget_usd", 5.0),
            max_per_request_usd=x402_cfg.get("max_per_request_usd", 0.25),
            allowed_providers=x402_cfg.get("allowed_providers"),
        )

        allow = self.constitution.rules_config.get("symbol_allowlist", {}).get("symbols")
        self.symbols = allow or self.settings.symbols

        # Real Binance data path. `feeds` and `data` are always constructed so
        # the Markets browser and anchoring work even in simulator mode; the
        # analysts only read them when live data is on.
        self.feeds = BinanceFeeds()
        self.data = MarketDataService(self.feeds, interval=self.settings.kline_interval)
        # One websocket carries a live price for every symbol on Binance at no
        # REST cost, so prices never consume the request budget the analysts
        # need for order books and candles.
        self.stream = MarketStream()
        self.ctx: MarketContext | None = None
        self.universe: list[dict] = []
        self.usd_rates: dict[str, float] = {}

        self.market = (
            LiveMarket(self.symbols)
            if self.settings.live_market_data
            else SimMarket(self.symbols)
        )
        self.clock = Clock(
            simulated=self.settings.simulated_clock,
            step_seconds=self.settings.sim_seconds_per_tick,
        )
        self.portfolio = PaperPortfolio(
            self.settings.starting_equity_usd, clock=self.clock
        )
        # USDⓈ-M perpetual futures book, sharing the portfolio's cash pool.
        # Spot (buy/sell above) is untouched; this adds long *and* short with
        # leverage, isolated margin and liquidation. See futures.py. Attaching
        # it here — and back-referencing it on the portfolio — is what lets the
        # equity, drawdown and gross-exposure figures, and therefore the
        # Guardian and the daily-loss rule, account for leveraged risk too.
        from .futures import FuturesBook
        self.futures = FuturesBook(self.portfolio, clock=self.clock)
        self.portfolio.futures = self.futures
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(path=self._data_dir / "ledger.jsonl")
        self.calibration = CalibrationTracker(
            horizon_seconds=self.settings.calibration_horizon_seconds,
            path=self._data_dir / "calibration.json",
        )
        self.council = Council(self.meter, calibration=self.calibration)
        self.anchor = Anchor(
            self.ledger, self._data_dir / "checkpoints.json", feeds=self.feeds
        )
        self._last_checkpoint = 0.0
        # News adjusts risk posture only. There is no path from a headline to a
        # buy — see news.py for why that is a deliberate refusal.
        self.news = NewsMonitor(self.symbols)
        self.event_risk = None
        # US macro regime: rates, curve shape, and the scheduled FOMC
        # calendar. The only layer that can de-risk *before* a known
        # volatility event rather than after it.
        self.macro = MacroMonitor()
        self.macro_posture = None
        # Payment and onchain workflows. Both fail closed and both are audited
        # exactly like a trade — see workflows.py. Payments settle for real on
        # a public testnet through a wallet generated once and persisted
        # encrypted at rest, same construction as the OAuth token store.
        self.settlement_wallet = SettlementWallet(
            self._data_dir / "settlement_wallet.enc", self.ledger._key
        )
        self.payments = PaymentRail(wallet=self.settlement_wallet)
        self.onchain = OnchainRail()
        # Read-only multi-chain inspector for looking up any address or
        # transaction hash the operator pastes in (see settlement.ChainInspector).
        from .settlement import ChainInspector
        self.chain_inspector = ChainInspector()

        # Real MCP client. Points at Binance in live mode, at the bundled mock
        # otherwise — same client, same protocol, same error handling.
        endpoint = self.settings.mcp_endpoint
        if self.settings.mode == Mode.MOCK:
            endpoint = f"http://127.0.0.1:{os.environ.get('GLASSBOX_PORT', '8787')}/mock/mcp"
        self.mcp = BinanceMCPClient(
            endpoint=endpoint,
            token_path=TOKEN_PATH,
            device_key=self.ledger._key,
            client_id=self.settings.mcp_client_id,
        )
        self.executor = Executor(self.mcp, feeds=self.feeds)
        self.mcp_connected = False
        self.guardian = Guardian()
        self.narrative = NarrativeScout(self.meter)
        self.compass = YieldCompass()

        self.pending_confirmations: dict[str, dict[str, Any]] = {}
        self.receipts: list[dict[str, Any]] = []
        self.activity: list[dict[str, Any]] = []
        self.council_verdicts: list[dict[str, Any]] = []
        self.running = False
        # Autonomous trading is OFF until the operator explicitly enables it.
        # The engine still ticks, streams live data, runs every analyst and
        # scores threats the moment it boots — so the dashboard is fully alive
        # and informative — but it will not *open a position on its own* until
        # this is switched on. Waking up to positions you did not personally
        # authorise, even paper ones, is a surprise a trading tool should
        # never spring; watching the reasoning is one thing, acting on it
        # unattended is a separate decision the operator makes deliberately.
        self.autonomous = False
        self.tick_count = 0
        self.defensive_actions: list[dict[str, Any]] = []
        self.counters: dict[str, int] = {
            "intents": 0, "allowed": 0, "denied": 0,
            "pending": 0, "vetoed": 0, "filled": 0,
        }
        # Passive benchmark: what an equal-weight buy-and-hold of the same
        # symbols would have done over the identical price path. Without this,
        # "we made 1.2%" is a number with no meaning attached to it.
        self._benchmark_basis: dict[str, float] | None = None
        self._task: asyncio.Task | None = None

        self.ledger.append(
            "session_start",
            {
                "mode": self.settings.mode.value,
                "symbols": self.symbols,
                "starting_equity_usd": self.settings.starting_equity_usd,
                "constitution": self.constitution.doc,
                "live_market_data": self.settings.live_market_data,
            },
        )

    # -- helpers -----------------------------------------------------------

    def _log(self, level: str, module: str, message: str, data: dict | None = None) -> None:
        entry = {
            "ts": time.time(),
            "level": level,
            "module": module,
            "message": message,
            "data": data or {},
        }
        self.activity.append(entry)
        self.activity = self.activity[-400:]
        self.emit("activity", entry)

    def _closest_to_trading(self) -> dict[str, Any] | None:
        """
        The verdict that came nearest to actually becoming a trade, and by
        how much it missed.

        Without this, a genuinely quiet market — where every analyst agrees
        there is nothing worth doing — looks exactly like a broken engine:
        zero trades, zero explanation. This turns "nothing is happening" into
        "BTCUSDT was bullish at 26%, and the bar is 30%", which is a real
        answer rather than an absence of one.

        A bearish verdict on a symbol with no held position is filtered out
        before ranking, not just noted afterward: GlassBox is spot-only and
        can never open a naked short, so a bearish read on something unheld
        was never going to become a trade no matter how high its conviction
        ran. Surfacing "32% conviction — 0 points under the bar" for a
        verdict that structurally could never fire is its own kind of
        confusing non-answer; only genuinely actionable verdicts are ranked.
        """
        held = set(self.portfolio.positions.keys())
        directional = [
            v for v in self.council_verdicts if v.get("direction") != "neutral"
        ]
        actionable = [
            v for v in directional
            if v.get("direction") == "bullish"
            or (v.get("direction") == "bearish" and v.get("symbol") in held)
        ]
        if not actionable:
            if directional:
                # There is a real view — it just can't become a trade. Spot
                # cannot open a naked short, so a bearish read on something
                # unheld was never actionable no matter its conviction. This
                # is a materially different, and equally real, answer from
                # "every analyst is neutral," and the two must not be
                # collapsed into the same message.
                unactionable = max(directional, key=lambda v: v.get("conviction", 0.0))
                return {
                    "symbol": unactionable.get("symbol"),
                    "direction": unactionable.get("direction"),
                    "conviction": round(unactionable.get("conviction", 0.0), 3),
                    "min_conviction": Council.MIN_CONVICTION,
                    "short_by": None,
                    "would_trade": False,
                    "blocked_reason": "not_actionable_spot_only",
                }
            return None
        best = max(actionable, key=lambda v: v.get("conviction", 0.0))
        bar = Council.MIN_CONVICTION
        conviction = best.get("conviction", 0.0)

        # Conviction is the first gate, but not the only one — quorum
        # (genuine analyst agreement, not one loud voice) is enforced
        # separately by the Constitution and is at least as common a real
        # blocker. Reporting "0 points under the bar" when conviction cleared
        # but quorum did not would say "should have traded" about an intent
        # that was always going to be denied for a completely different
        # reason — exactly the kind of confusing non-answer this exists to
        # avoid, just one gate further along.
        need = int(
            self.constitution.doc.get("rules", {})
            .get("quorum", {}).get("min_agreeing_analysts", 2)
        )
        signals = best.get("signals") or []
        agreeing = sum(1 for s in signals if s.get("stance") == best.get("direction"))
        quorum_met = agreeing >= need if signals else True

        result = {
            "symbol": best.get("symbol"),
            "direction": best.get("direction"),
            "conviction": round(conviction, 3),
            "min_conviction": bar,
            "short_by": round(max(bar - conviction, 0.0), 3),
            "would_trade": conviction >= bar and quorum_met,
            "agreeing_analysts": agreeing,
            "analysts_required": need,
            "quorum_met": quorum_met,
        }
        if conviction >= bar and not quorum_met:
            result["blocked_reason"] = "quorum_not_met"
        elif conviction >= bar and quorum_met:
            result["blocked_reason"] = None  # genuinely clears both known gates
        return result

    def _sim_marks(self) -> dict[str, float]:
        """Simulator-only prices. Internal fallback — use `_marks()` instead."""
        return {s: q["price"] for s, q in self.market.snapshot().items()}

    def _marks(self) -> dict[str, float]:
        """
        Current mark price for every watched symbol, preferring real Binance
        data whenever it's actually available.

        This used to always return simulator prices, full stop — even in
        `mock` and `live` mode, with real market data flowing everywhere
        else. That meant the Constitution's own risk math (`adjudicate`) and
        the dashboard's equity and P&L (`status`) were being computed against
        the wrong prices the entire time real data was on: two of the most
        important numbers in the whole system, silently wrong. Fixed once,
        here, rather than patched at each of the six call sites that use it.
        """
        return self._real_marks() if self._live_ready() else self._sim_marks()

    def _live_ready(self) -> bool:
        """True when real Binance data is on and this tick actually got some."""
        return bool(
            self.settings.live_market_data and self.ctx and self.ctx.klines
        )

    def positions_the_council_now_opposes(self) -> list[dict[str, Any]]:
        """
        Held positions the Council has since turned against.

        You can open a position when the analysis supports it, then have that
        analysis flip while you're still holding — the market moved, or a new
        signal landed. Nothing previously surfaced that: a bullish trade could
        quietly become one the whole Council now reads as bearish, and you'd
        only find out from the P&L.

        This is the missing feedback loop. It flags any position the Council
        now opposes with real conviction, so the operator can decide whether
        to hold or exit — the same information the second-opinion check gives
        *before* a trade, now kept live *after* it. It only warns, exactly
        like the news and macro layers: it never closes a position on its own,
        because an unrequested auto-close is its own kind of surprise.
        """
        held = self.portfolio.positions
        if not held:
            return []
        out = []
        for v in self.council_verdicts:
            sym = v.get("symbol")
            pos = held.get(sym)
            if not pos or pos.qty <= 0:
                continue
            # Every held position is a long (spot-only), so a bearish Council
            # read is the one that opposes it.
            if v.get("direction") == "bearish" and v.get("conviction", 0) >= Council.MIN_CONVICTION:
                out.append({
                    "symbol": sym,
                    "conviction": round(v.get("conviction", 0), 3),
                    "opened_mode": pos.opened_mode,
                    "message": (
                        f"You hold {sym}, but the Council now reads it as bearish at "
                        f"{v.get('conviction', 0) * 100:.0f}% conviction — the opposite of "
                        f"your position. It won't be closed for you; decide whether to hold "
                        f"or exit."
                    ),
                })
        return out

    def market_view(self) -> dict[str, dict]:
        """
        Per-symbol quote view in the shape the Constitution and the dashboard
        expect, built from real data when available and from the simulator
        otherwise. The rules never need to know which one they got.
        """
        if not self._live_ready():
            return self.market.snapshot()

        from .indicators import atr_pct

        out = {}
        for sym in self.symbols:
            k = self.ctx.klines.get(sym)
            t = self.ctx.tickers.get(sym, {})
            b = self.ctx.books.get(sym, {})
            price = self.ctx.price(sym)
            if price <= 0:
                continue
            bid = float(b.get("bidPrice", price) or price)
            ask = float(b.get("askPrice", price) or price)
            atr = 0.0
            if k and len(k) > 20:
                atr = atr_pct([c["high"] for c in k[-60:]], [c["low"] for c in k[-60:]],
                              [c["close"] for c in k[-60:]], 14)
            out[sym] = {
                "symbol": sym, "price": round(price, 8),
                "bid": bid, "ask": ask,
                "change_24h_pct": float(t.get("priceChangePercent", 0) or 0),
                "volume_24h_usd": float(t.get("quoteVolume", 0) or 0),
                "spread_bps": round((ask - bid) / max(price, 1e-9) * 10000, 3),
                "atr_pct": round(atr, 3),
                "high_24h": float(t.get("highPrice", 0) or 0),
                "low_24h": float(t.get("lowPrice", 0) or 0),
                "trades_24h": int(t.get("count", 0) or 0),
                "source": "binance_live",
                "ts": self.ctx.ts,
            }
        return out

    def _real_marks(self) -> dict[str, float]:
        """
        Live prices, preferring the websocket.

        Streamed quotes are sub-second and free; the REST context is a few
        seconds old and costs weight. A stale streamed quote is explicitly not
        preferred — if the socket has gone quiet we fall back rather than trade
        on a frozen number.
        """
        streamed = {}
        for sym in self.symbols:
            q = self.stream.quote(sym)
            if q and q.price > 0 and not q.is_stale:
                streamed[sym] = q.price
        if len(streamed) == len(self.symbols):
            return streamed
        if self.ctx:
            out = {s: self.ctx.price(s) for s in self.symbols}
            out = {k: v for k, v in out.items() if v > 0}
            if out:
                return out
        return self._marks()

    # -- adjudication ------------------------------------------------------

    def adjudicate(self, intent: Intent) -> tuple[Verdict, tuple[bool, str]]:
        marks = self._marks()
        state = self.portfolio.state_dict(marks)
        verdict = self.constitution.evaluate(intent, state, self.market_view())

        assessment = self.guardian.last or self.guardian.assess(self.market, state, self.symbols)
        guardian_ok, guardian_reason = self.guardian.review(intent, assessment)

        if not guardian_ok:
            verdict.decision = Decision.DENY
            verdict.reasons.append(f"[guardian] {guardian_reason}")
            verdict.triggered_rules.append("guardian_veto")

        return verdict, (guardian_ok, guardian_reason)

    def _write_receipt(
        self,
        intent: Intent,
        verdict: Verdict,
        guardian: tuple[bool, str],
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        assessment = self.guardian.last
        record = self.ledger.append(
            "decision",
            {
                "intent": intent.to_dict(),
                "verdict": verdict.to_dict(),
                "guardian": {
                    "approved": guardian[0],
                    "reason": guardian[1],
                    "assessment": assessment.to_dict() if assessment else None,
                },
                "market": self.market_view(),
                "outcome": outcome,
            },
        )
        receipt = {
            "receipt_id": f"rcpt-{uuid.uuid4().hex[:10]}",
            "ts": record["ts"],
            "intent": intent.to_dict(),
            "verdict": verdict.to_dict(),
            "guardian": {
                "approved": guardian[0],
                "reason": guardian[1],
                "threat_score": assessment.score if assessment else 0.0,
                "threat_level": assessment.level if assessment else "normal",
            },
            "outcome": outcome,
            "ledger_seq": record["seq"],
            "ledger_hash": record["hash"],
            "prev_hash": record["prev_hash"],
        }
        self.receipts.append(receipt)
        self.receipts = self.receipts[-300:]
        self.emit("receipt", receipt)
        return receipt

    async def _execute(self, intent: Intent, verdict: Verdict) -> dict[str, Any]:
        if intent.meta.get("market") == "futures":
            return await self._execute_futures(intent, verdict)
        notional = verdict.adjusted_notional_usd or intent.notional_usd
        # Same class of bug as _marks(): this used to always read
        # self.market.price(), the simulator, even when live data was on and
        # the intent itself was reasoned about using a real price moments
        # earlier. A paper fill could execute at a completely different
        # number than the one the Council or the operator actually saw.
        price = self._marks().get(intent.symbol) or self.market.price(intent.symbol)

        if self.settings.mode in (Mode.MOCK, Mode.LIVE):
            # A real order through a real MCP client. The ledger head is the
            # justification hash and travels with the request.
            result = await self.executor.execute_via_mcp(
                intent, notional, self.ledger.head
            )
            self.ledger.append("mcp_order", result.to_dict())
            if result.status == "filled":
                # Mirror the fill into the local book so PnL, stops and the
                # Constitution's exposure rules stay accurate.
                self.portfolio.execute(
                    intent.symbol, intent.side.value, result.filled_usd or notional,
                    result.price or price, intent_id=intent.intent_id,
                    module=intent.module, stop_loss=intent.stop_loss,
                    take_profit=intent.take_profit, state=intent.to_state,
                    force=True, mode=self.settings.mode.value,
                )
                self._log(
                    "fill", intent.module,
                    f"Binance MCP filled {result.side} {result.symbol} "
                    f"${result.filled_usd:,.0f} in {result.latency_ms:.0f}ms "
                    f"(order {result.order_id}).",
                    result.to_dict(),
                )
            else:
                self._log(
                    "deny", intent.module,
                    f"Binance rejected {intent.side.value} {intent.symbol}: "
                    f"{result.message[:140]}",
                )
            return {"status": result.status, "mcp": result.to_dict()}

        if self.settings.mode == Mode.BRIDGE:
            # Do not simulate a fill. Emit a signed instruction for the operator
            # to run against the Binance MCP server, and record it as pending.
            return {
                "status": "bridged",
                "mcp_instruction": self.mcp_instruction(intent, notional),
                "note": "Awaiting execution through the Binance MCP server.",
            }

        # Pull capital back from yield if the position needs it.
        if intent.side.value == "BUY" and self.portfolio.cash < notional:
            recalled = self.portfolio.recall_from_yield(notional - self.portfolio.cash)
            if recalled > 0:
                self._log(
                    "info",
                    "compass",
                    f"Recalled ${recalled:,.0f} from yield to fund {intent.symbol}.",
                )

        fill = self.portfolio.execute(
            symbol=intent.symbol,
            side=intent.side.value,
            notional_usd=notional,
            quote_price=price,
            intent_id=intent.intent_id,
            module=intent.module,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            state=intent.to_state,
            mode=self.settings.mode.value,
        )
        if not fill:
            return {"status": "rejected", "note": "Insufficient balance or nothing to sell."}
        return {"status": "filled", "fill": fill.to_dict()}

    async def _execute_futures(self, intent: Intent, verdict: Verdict) -> dict[str, Any]:
        """
        Execute a futures intent. `intent.notional_usd` is the leveraged
        position notional (size × leverage); the margin actually committed is
        that divided by leverage, drawn from the shared cash pool by the
        futures book. Opens and closes route to the same book; in mock/live
        mode they go out through the same MCP client the spot path uses.
        """
        meta = intent.meta
        action = meta.get("action", "OPEN_LONG")
        leverage = float(meta.get("leverage", 1) or 1)
        reduce_only = bool(meta.get("reduce_only"))
        notional = verdict.adjusted_notional_usd or intent.notional_usd
        price = self._marks().get(intent.symbol) or self.market.price(intent.symbol)

        if self.settings.mode in (Mode.MOCK, Mode.LIVE):
            result = await self.executor.execute_futures_via_mcp(
                intent, notional, leverage, self.ledger.head
            )
            self.ledger.append("mcp_futures_order", result.to_dict())
            self._log(
                "fill" if result.status == "filled" else "deny", intent.module,
                f"Binance futures {action} {intent.symbol} "
                + (f"filled ${result.filled_usd:,.0f}." if result.status == "filled"
                   else f"rejected: {result.message[:140]}"),
                result.to_dict(),
            )
            return {"status": result.status, "mcp": result.to_dict()}

        if self.settings.mode == Mode.BRIDGE:
            return {
                "status": "bridged",
                "mcp_instruction": self.mcp_instruction(intent, notional),
                "note": "Awaiting execution through the Binance MCP server (futures).",
            }

        # Paper / shadow: the futures book models the fill.
        if reduce_only or action in ("CLOSE_LONG", "CLOSE_SHORT", "CLOSE"):
            fill = self.futures.close(
                intent.symbol, price, fraction=1.0,
                mode=self.settings.mode.value, reason=intent.thesis,
            )
        else:
            # Pull margin back from yield if idle cash is parked, the same way
            # a spot buy recalls capital before it fills.
            margin_needed = notional / max(leverage, 1e-9) + notional * 0.001
            if self.portfolio.cash < margin_needed:
                recalled = self.portfolio.recall_from_yield(margin_needed - self.portfolio.cash)
                if recalled > 0:
                    self._log(
                        "info", "compass",
                        f"Recalled ${recalled:,.0f} from yield to margin {intent.symbol} futures.",
                    )
            side = "LONG" if action == "OPEN_LONG" else "SHORT"
            fill = self.futures.open(
                intent.symbol, side, notional, leverage, price,
                stop_loss=intent.stop_loss, take_profit=intent.take_profit,
                mode=self.settings.mode.value, reason=intent.thesis,
            )
        if not fill:
            return {
                "status": "rejected",
                "note": (
                    "Could not open: an opposite-side position is already held on "
                    f"{intent.symbol} (close it first), or there was too little "
                    "free cash for margin."
                ) if not reduce_only else f"No open {intent.symbol} futures position to close.",
            }
        self.ledger.append("futures_fill", fill.to_dict())
        return {"status": "filled", "futures_fill": fill.to_dict()}

    def mcp_instruction(self, intent: Intent, notional: float) -> dict[str, Any]:
        """
        The exact call to make against Binance's MCP server, plus the receipt
        hash that justifies it. This is what makes the bridge auditable: the
        instruction and its reasoning share a hash.
        """
        if intent.meta.get("market") == "futures":
            meta = intent.meta
            return {
                "server": "binance-mcp-server",
                "endpoint": "https://agent.binance.com/mcp/agentic",
                "action": "place_futures_order",
                "arguments": {
                    "symbol": intent.symbol,
                    "side": intent.side.value,
                    "positionSide": meta.get("position_side", "BOTH"),
                    "type": intent.order_type,
                    "quantity": round(notional / max(intent.reference_price or 1, 1e-9), 6),
                    "leverage": meta.get("leverage"),
                    "reduceOnly": bool(meta.get("reduce_only")),
                },
                "natural_language": (
                    f"{meta.get('action', 'OPEN_LONG').replace('_', ' ').title()} "
                    f"${notional:,.2f} notional of {intent.symbol} perpetual at "
                    f"{meta.get('leverage')}x on USDⓈ-M futures."
                ),
                "justification_hash": self.ledger.head,
            }
        return {
            "server": "binance-mcp-server",
            "endpoint": "https://agent.binance.com/mcp/agentic",
            "action": "place_spot_order",
            "arguments": {
                "symbol": intent.symbol,
                "side": intent.side.value,
                "type": intent.order_type,
                "quoteOrderQty": round(notional, 2),
            },
            "natural_language": (
                f"{intent.side.value.capitalize()} ${notional:,.2f} of "
                f"{intent.symbol} at market on spot in my Agentic sub-account."
            ),
            "justification_hash": self.ledger.head,
        }

    # -- the loop ----------------------------------------------------------

    async def tick(self) -> dict[str, Any]:
        self.tick_count += 1
        t0 = time.time()
        self.clock.advance()

        # 1. Observe. On the live path this is real Binance data: candles, order
        #    book, trade tape and futures positioning, fetched concurrently.
        if self.settings.live_market_data:
            state_peek = self.portfolio.state_dict(self._marks() or {})
            try:
                self.ctx = await self.data.build(
                    self.symbols,
                    intended_notional_usd=max(state_peek["equity_usd"] * 0.09, 25.0),
                    want_derivatives=True,
                )
                # The websocket tape is richer and fresher than a REST snapshot,
                # so prefer it where the stream is actually connected.
                for sym in self.symbols:
                    live = self.stream.recent_trades(sym, 400)
                    if len(live) > 20:
                        self.ctx.trades[sym] = live
                    q = self.stream.quote(sym)
                    if q and q.price > 0 and not q.is_stale:
                        self.ctx.tickers.setdefault(sym, {})
                        self.ctx.tickers[sym].update({
                            "lastPrice": q.price,
                            "priceChangePercent": q.change_24h_pct,
                            "quoteVolume": q.quote_volume_24h,
                            "highPrice": q.high_24h, "lowPrice": q.low_24h,
                        })
            except Exception as exc:
                self._log("error", "feeds", f"Market data fetch failed: {exc}")
        if not self._live_ready():
            await self.market.poll()
        # _marks() already does this same live-vs-simulator check internally;
        # called directly here for clarity at the one place mark-to-market
        # happens every tick, now that it's no longer the only place that
        # gets this right.
        marks = self._marks()
        self.portfolio.roll_day_if_needed(marks)

        # 2. Protect — stops and targets run before anything else
        for fill in self.portfolio.check_protective_exits(marks):
            self.defensive_actions.append(
                {"ts": fill.ts, "kind": "protective_exit", "symbol": fill.symbol,
                 "notional_usd": fill.notional_usd, "pnl_usd": fill.realised_pnl_usd}
            )
            self._log(
                "warn",
                "risk",
                f"Protective exit: sold {fill.qty:.5f} {fill.symbol} at "
                f"{fill.price:,.2f} for {fill.realised_pnl_usd:+,.2f} USD.",
                fill.to_dict(),
            )
            self.ledger.append("protective_exit", fill.to_dict())

        # 2a. Futures protection: liquidations, stops and targets on leveraged
        #     positions, checked with the same before-anything-else priority.
        #     A leveraged short that runs against the book must be liquidatable
        #     the same tick the price crosses, not after an analyst has spoken.
        for ev in self.futures.check_liquidations_and_exits(marks):
            kind = ev.get("kind")
            self.defensive_actions.append({
                "ts": ev["ts"],
                "kind": f"futures_{kind}",
                "symbol": ev["symbol"],
                "notional_usd": ev["notional_usd"],
                "pnl_usd": ev["realised_pnl_usd"],
            })
            self._log(
                "critical" if kind == "liquidation" else "warn", "risk",
                (f"Futures {ev['side']} {ev['symbol']} LIQUIDATED at {ev['price']:,.4f} "
                 f"— margin lost ({ev['realised_pnl_usd']:+,.2f})."
                 if kind == "liquidation" else
                 f"Futures {kind.replace('_', ' ')}: closed {ev['side']} {ev['symbol']} at "
                 f"{ev['price']:,.4f} for {ev['realised_pnl_usd']:+,.2f} USD."),
                ev,
            )
            self.ledger.append(f"futures_{kind}", ev)
        # Funding accrues while positions are held — the cost of carry, made
        # visible rather than ignored. The per-tick amount is negligible.
        if self.futures.positions:
            self.futures.accrue_funding(marks)

        state = self.portfolio.state_dict(marks)

        # 2b. Grade any analyst calls whose horizon has elapsed. This is what
        #     turns the ledger from a record of *what* was reasoned into a
        #     record of whether that reasoning was any good.
        graded = self.calibration.grade_due(marks)
        if graded:
            self.ledger.append(
                "calibration",
                {"graded": [g.to_dict() for g in graded[:20]], "count": len(graded)},
            )
            hits = sum(1 for g in graded if g.correct)
            self._log(
                "info", "calibration",
                f"Graded {len(graded)} analyst call(s); {hits} were right.",
            )

        # 2b-ii. Analyst circuit breaker. A run of wrong live calls benches an
        #        analyst out of the Council vote until it recovers; a recovery
        #        streak brings it back. Both are permanent ledger events.
        for ev in self.calibration.last_circuit_events:
            self.ledger.append(ev["type"], ev)
            if ev["type"] == "analyst_benched":
                self._log(
                    "warn", "calibration",
                    f"{ev['analyst']} benched after {ev['miss_streak']} wrong live "
                    f"calls in a row; its vote is sidelined until it recovers.",
                )
            else:
                self._log(
                    "info", "calibration",
                    f"{ev['analyst']} reinstated after {ev['hit_streak']} correct "
                    f"calls; back in the Council at a probationary weight.",
                )

        # 2c. Refresh event risk. Delistings, security incidents and scheduled
        #     macro releases change what the agent is allowed to do, never what
        #     it wants to do.
        try:
            await self.macro.refresh()
            self.macro_posture = self.macro.assess()
        except Exception as exc:
            self._log("warn", "macro", f"Macro refresh failed: {exc}")

        try:
            await self.news.refresh()
            self.news.symbols = self.symbols
            self.event_risk = self.news.assess()
            if self.event_risk.blocked_symbols:
                for sym in self.event_risk.blocked_symbols:
                    if sym in self.symbols:
                        self._log(
                            "critical", "news",
                            f"{sym} is blocked: {self.event_risk.reasons[0]}",
                        )
        except Exception as exc:
            self._log("warn", "news", f"News refresh failed: {exc}")

        # 3. Assess threat
        assessment = (
            self.guardian.assess_from_klines(self.ctx, state, self.symbols)
            if (self.settings.live_market_data and self.ctx and self.ctx.klines)
            else self.guardian.assess(self.market, state, self.symbols)
        )
        if assessment.level != "normal":
            self._log(
                "warn" if assessment.level == "elevated" else "critical",
                "sentinel",
                f"Threat {assessment.level} at {assessment.score:.0f}/100. "
                f"{assessment.recommendation}",
                assessment.to_dict(),
            )
        self.emit("threat", assessment.to_dict())

        # 4–7. Build intents, adjudicate, veto
        intents: list[Intent] = []
        # Recorded on execution, not on proposal. A hedge that was vetoed or
        # denied is not a defence, and showing it as one would overstate what
        # the system actually did.
        intents.extend(self.guardian.build_hedge_intents(state, self.market, assessment))

        if not self.guardian.quarantined:
            if self.settings.live_market_data and self.ctx and self.ctx.klines:
                council_intents, verdicts = await self.council.propose_async(
                    self.ctx, self.symbols, state
                )
                # Log every directional call so it can be graded later.
                for v in verdicts:
                    from .config import Signal as _S
                    sigs = [
                        _S(**{**t, "stance": t["stance"]}) for t in v["transcript"]
                    ]
                    self.calibration.record(sigs, marks, self.ledger.height)
            else:
                council_intents, verdicts = self.council.propose(
                    self.market, self.symbols, state
                )
            self.council_verdicts = verdicts
            self.emit("council", {"verdicts": verdicts})

            # Scan narratives every tick regardless of autonomy, exactly like
            # the Council above. The scan only reads market data and publishes
            # its reasoning to the Narratives panel — it opens nothing. This
            # was previously done only inside the autonomous branch, so with
            # autonomy off (the default) the panel never populated and sat on
            # "Scanning…" forever. Real 24h moves are passed when live data is
            # on so the themes track genuine price action.
            self.narrative.scan(
                self.market, self.symbols,
                snapshot=self.market_view() if self._live_ready() else None,
            )

            # The Council and Narrative scout always run and always publish
            # their reasoning — that's the analysis the dashboard exists to
            # show. But their proposals only become *trades the engine opens
            # on its own* when autonomous trading is explicitly enabled.
            # Guardian hedges are deliberately exempt: a hedge reduces risk on
            # a position that already exists, so it belongs to the safety
            # system, not to new-position-taking, and must keep working even
            # when autonomous entry is off.
            if self.autonomous:
                intents.extend(council_intents)
                intents.extend(
                    self.narrative.propose(
                        self.market, self.symbols, sizing_usd=state["equity_usd"] * 0.04
                    )
                )

        results = []
        for intent in intents:
            self.counters["intents"] += 1
            result = await self._process_one_intent(intent, assessment)
            if result is not None:
                results.append(result)

        # 9. Route idle cash. Only in local modes: when the Agentic sub-account
        #    holds the money, parking it in a simulated Earn product would make
        #    the local equity diverge from the exchange, and reconciliation
        #    would fight it every ten ticks.
        if self.settings.mode in (Mode.MOCK, Mode.LIVE):
            pass
        elif not self.guardian.quarantined and assessment.level == "normal":
            plan = self.compass.plan(self.portfolio.state_dict(marks))
            if plan["action"] == "deploy" and plan["amount_usd"] > 0:
                moved = self.portfolio.deploy_to_yield(plan["amount_usd"])
                if moved > 0:
                    self._log(
                        "info",
                        "compass",
                        f"Parked ${moved:,.0f} in {plan['venue']['venue']} at "
                        f"{plan['venue']['risk_adjusted_apy_pct']:.1f}% risk-adjusted.",
                        plan,
                    )
                    self.ledger.append(
                        "yield_deploy", {"amount_usd": moved, "venue": plan["venue"]}
                    )
        elif self.portfolio.yield_deployed_usd > 0 and assessment.level == "critical":
            recalled = self.portfolio.recall_from_yield(self.portfolio.yield_deployed_usd)
            if recalled > 0:
                self._log(
                    "warn",
                    "compass",
                    f"Threat is critical; recalled ${recalled:,.0f} from yield to cash.",
                )

        if self.compass.current_venue:
            self.portfolio.accrue_yield(
                self.compass.current_venue.apy_pct, self.settings.sim_seconds_per_tick
            )

        # 10a. Reconcile with the exchange. Binance is the source of truth for
        #      what we actually hold.
        if self.settings.mode in (Mode.MOCK, Mode.LIVE) and self.mcp_connected:
            if (self.tick_count % 10) == 0:
                try:
                    await self.reconcile()
                except Exception as exc:
                    self._log("warn", "binance", f"Reconciliation failed: {exc}")

        # 10b. Anchor the ledger head to an external witness on a timer.
        if (time.time() - self._last_checkpoint) > self.settings.checkpoint_interval_seconds:
            self._last_checkpoint = time.time()
            try:
                cp = await self.anchor.create(self.settings.anchor_webhook_url)
                self._log(
                    "info", "ledger",
                    f"Ledger anchored at height {cp.ledger_height} "
                    f"(checkpoint #{cp.seq}).",
                )
            except Exception as exc:
                self._log("warn", "ledger", f"Could not anchor ledger: {exc}")

            # Same cadence, cheap to check: is this machine's clock still in
            # sync with Binance's? A drifted clock silently misapplies the
            # Constitution's time-based rules (blackout windows, cooldowns),
            # which is a correctness issue worth surfacing even though our
            # OAuth-based execution doesn't depend on it for authentication
            # the way a raw-API-key integration would.
            if self.executor.clock:
                try:
                    report = await self.executor.check_clock_drift()
                    if report.get("healthy") is False:
                        self._log("warn", "system", report.get("note", "Clock drift detected."))
                except Exception as exc:
                    self._log("warn", "system", f"Could not check clock drift: {exc}")

        snapshot = self.status()
        snapshot["tick_ms"] = round((time.time() - t0) * 1000, 1)
        self.emit("tick", snapshot)
        return snapshot

    async def _process_one_intent(
        self, intent: Intent, assessment
    ) -> dict[str, Any] | None:
        """
        Run one proposed trade through every gate: macro, news, the
        Constitution, the Guardian, then execute or queue for confirmation.

        Factored out so a manually-placed trade (see `manual_intent` below)
        goes through *exactly* this pipeline rather than a parallel one — the
        only difference between an operator's trade and the Council's is who
        proposed it, never how thoroughly it is checked. Returns None for an
        intent that was blocked before ever reaching the Constitution (macro
        or news gate), or a result dict otherwise.
        """
        # Macro gate. Runs before the news gate because a scheduled Fed event
        # is knowable in advance, so it should be the first thing that
        # shrinks a position rather than the last. Like the news layer, it
        # can only ever reduce risk.
        if self.macro_posture and intent.urgency != "critical":
            mp = self.macro_posture
            if mp.blackout:
                self.counters["denied"] += 1
                self._log(
                    "deny", "macro",
                    f"Blocked {intent.side.value} {intent.symbol}: "
                    f"{mp.reasons[0]}",
                )
                self.ledger.append("macro_block", {
                    "symbol": intent.symbol, "reason": mp.reasons[0],
                    "intent_id": intent.intent_id,
                    "next_fomc": mp.next_fomc})
                return None
            if mp.size_multiplier < 1.0:
                intent.notional_usd *= mp.size_multiplier
                intent.thesis += (
                    f" Size cut to {mp.size_multiplier:.0%} on macro: "
                    f"{mp.reasons[0]}"
                )

        # Event risk gate. Blocks and shrinks; it can never enlarge or create.
        if self.event_risk:
            if intent.symbol in self.event_risk.blocked_symbols:
                self.counters["denied"] += 1
                self._log(
                    "deny", "news",
                    f"Blocked {intent.side.value} {intent.symbol}: "
                    f"{self.event_risk.reasons[0]}",
                )
                self.ledger.append("news_block", {
                    "symbol": intent.symbol, "reason": self.event_risk.reasons[0],
                    "intent_id": intent.intent_id})
                return None
            if self.event_risk.size_multiplier < 1.0 and intent.urgency != "critical":
                intent.notional_usd *= self.event_risk.size_multiplier
                intent.thesis += (
                    f" Size cut to {self.event_risk.size_multiplier:.0%} because "
                    f"event risk is {self.event_risk.level}: "
                    f"{self.event_risk.reasons[0]}"
                )

        verdict, guardian = self.adjudicate(intent)
        if "guardian_veto" in verdict.triggered_rules:
            self.counters["vetoed"] += 1

        if verdict.decision == Decision.DENY:
            receipt = self._write_receipt(
                intent, verdict, guardian, {"status": "denied"}
            )
            self._log(
                "deny",
                intent.module,
                f"Blocked {intent.side.value} {intent.symbol}: "
                f"{verdict.reasons[0] if verdict.reasons else 'policy'}",
                {"receipt_id": receipt["receipt_id"]},
            )
            self.counters["denied"] += 1
            return {"intent_id": intent.intent_id, "status": "denied"}

        if verdict.decision == Decision.REQUIRE_HUMAN:
            notional = verdict.adjusted_notional_usd or intent.notional_usd
            pending = {
                "intent": intent.to_dict(),
                "verdict": verdict.to_dict(),
                "guardian": {"approved": guardian[0], "reason": guardian[1]},
                "mcp_instruction": self.mcp_instruction(intent, notional),
                "requested_ts": time.time(),
                "_obj": intent,
            }
            self.pending_confirmations[intent.intent_id] = pending
            self._write_receipt(
                intent, verdict, guardian, {"status": "awaiting_confirmation"}
            )
            self._log(
                "confirm",
                intent.module,
                f"Your confirmation needed: {intent.side.value} "
                f"${notional:,.0f} of {intent.symbol}.",
                {"intent_id": intent.intent_id},
            )
            self.emit(
                "pending",
                {k: v for k, v in pending.items() if k != "_obj"},
            )
            self.counters["pending"] += 1
            return {"intent_id": intent.intent_id, "status": "pending"}

        outcome = await self._execute(intent, verdict)
        self.counters["allowed"] += 1
        self._write_receipt(intent, verdict, guardian, outcome)
        if outcome["status"] == "filled":
            self.counters["filled"] += 1
            # Paper mode returns {"status", "fill"}; the MCP path (mock/live)
            # returns {"status", "mcp"} with a different shape. This block
            # only has anything to do when there's a local fill dict to read.
            f = outcome.get("fill")
            if f and intent.module == "sentinel":
                self.defensive_actions.append(
                    {
                        "ts": f["ts"],
                        "kind": "guardian_hedge",
                        "symbol": f["symbol"],
                        "notional_usd": f["notional_usd"],
                        "pnl_usd": f["realised_pnl_usd"],
                        "threat_score": assessment.score,
                    }
                )
            if f:
                # The MCP path logs its own fill (with latency and order id)
                # inside _execute, so only the local paper fill needs one here.
                self._log(
                    "fill",
                    intent.module,
                    f"{f['side']} {f['qty']:.5f} {f['symbol']} at "
                    f"{f['price']:,.2f} — ${f['notional_usd']:,.0f}.",
                    f,
                )
        return {"intent_id": intent.intent_id, "status": outcome["status"]}

    async def run(self) -> None:
        self.running = True
        self._log("info", "engine", f"Engine started in {self.settings.mode.value} mode.")
        while self.running:
            try:
                await self.tick()
            except Exception as exc:  # a bad tick must not kill the session
                self._log("error", "engine", f"Tick failed: {exc}")
                self.ledger.append("engine_error", {"error": str(exc)})
            await asyncio.sleep(self.settings.tick_seconds)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())
        if self.settings.live_market_data:
            asyncio.create_task(self.stream.start(self.symbols))

    async def stop(self) -> None:
        """
        Pause the tick loop and stop the background WebSocket stream.

        Open positions are untouched — this only stops new ticks and new
        analysis from happening. Without explicitly stopping the stream too,
        its two background tasks (`_run_all_market`, `_run_watched`) keep
        their reconnect loops running forever, which leaks a live connection
        to Binance and can prevent the process from shutting down cleanly —
        exactly the kind of thing that makes "Ctrl+C to stop" feel like it
        didn't work.
        """
        self.running = False
        await self.stream.stop()
        self._log("info", "engine", "Engine paused. Open positions are untouched.")

    # -- operator actions --------------------------------------------------

    async def manual_intent(
        self, symbol: str, side: str, notional_usd: float,
        reason: str = "Manually placed by the operator.",
    ) -> dict[str, Any]:
        """
        Place a trade on demand, from the dashboard, instead of waiting for
        the Council to independently reach one.

        This is **not** a backdoor around the safety system. The intent is
        built the same way an AI-proposed one is — with a stop loss, a
        reference price, and a real symbol — and it runs through the exact
        same `_process_one_intent` pipeline: the Constitution can still deny
        it, the Guardian can still veto it, macro or news can still shrink or
        block it, and a large-enough size still routes to a human
        confirmation. The only thing that differs from a Council trade is
        who proposed the idea; every check downstream of that is identical
        and identically audited in the ledger.

        Exists because waiting for organic market conditions to clear the
        Council's conviction bar is not a good way to learn how order
        placement, fills, and P&L actually work — see TESTING_GUIDE.md.
        """
        symbol = symbol.upper().strip()
        try:
            side_enum = Side(side.upper())
        except ValueError:
            return {"ok": False, "error": f"Side must be BUY or SELL, got '{side}'."}

        marks = self._marks()
        price = marks.get(symbol)
        if not price or price <= 0:
            return {"ok": False, "error": f"No live price available for {symbol}."}

        state = self.portfolio.state_dict(marks)
        pos = self.portfolio.positions.get(symbol)

        if side_enum == Side.SELL:
            # Spot-only: a sell can only ever be an exit, never a fresh short.
            # Failing here with a clear message beats a silent no-op fill.
            if not pos or pos.qty <= 0:
                return {
                    "ok": False,
                    "error": (
                        f"You don't hold any {symbol} to sell. GlassBox is "
                        f"spot-only and cannot open a short position."
                    ),
                }
            held_value = pos.qty * price
            notional_usd = min(notional_usd, held_value)

        intent = Intent(
            intent_id=f"manual-{uuid.uuid4().hex[:10]}",
            ts=time.time(),
            module="operator",
            symbol=symbol,
            side=side_enum,
            notional_usd=round(notional_usd, 2),
            order_type="MARKET",
            limit_price=None,
            from_state=CapitalState.IDLE if side_enum == Side.BUY else CapitalState.DEPLOYED,
            to_state=CapitalState.DEPLOYED if side_enum == Side.BUY else CapitalState.IDLE,
            thesis=reason,
            # A reasonable default protective stop for a manual entry — the
            # Constitution's require_stop_loss rule applies to this exactly
            # as it would to any AI-proposed trade.
            stop_loss=round(price * 0.95, 8) if side_enum == Side.BUY else None,
            reference_price=price,
            urgency="routine",
        )

        assessment = self.guardian.last or self.guardian.assess(
            self.market, state, self.symbols
        )
        result = await self._process_one_intent(intent, assessment)
        if result is None:
            return {
                "ok": False,
                "error": "Blocked by macro or event-risk policy before reaching the Constitution.",
            }
        return {"ok": True, **result}

    async def close_position(self, symbol: str) -> dict[str, Any]:
        """
        Manually close (sell out of) an open position on demand.

        The complement to `manual_intent`: a one-click exit an operator can
        reach directly, rather than the position only ever closing when the
        Council decides to, a stop-loss fires, or the Guardian intervenes. It
        is a normal SELL of the full held quantity, so it flows through the
        exact same execution and ledger path as any other trade — the exit is
        as auditable as the entry, and in mock/live mode it goes out through
        the real MCP client.

        The Constitution's `cooldown_after_loss` and similar rules do not gate
        a *reduction* of existing risk (they only ever restrain new or larger
        exposure), so a close is never itself blocked by them — an operator
        can always get out.
        """
        symbol = symbol.upper().strip()
        marks = self._marks()
        price = marks.get(symbol)
        pos = self.portfolio.positions.get(symbol)
        if not pos or pos.qty <= 0:
            return {"ok": False, "error": f"No open {symbol} position to close."}
        if not price or price <= 0:
            return {"ok": False, "error": f"No live price available for {symbol}."}

        held_value = pos.qty * price
        intent = Intent(
            intent_id=f"close-{uuid.uuid4().hex[:10]}",
            ts=time.time(),
            module="operator",
            symbol=symbol,
            side=Side.SELL,
            notional_usd=round(held_value, 2),
            order_type="MARKET",
            limit_price=None,
            from_state=CapitalState.DEPLOYED,
            to_state=CapitalState.IDLE,
            thesis="Operator manually closed this position from the dashboard.",
            stop_loss=None,
            reference_price=price,
            urgency="routine",
        )
        # A manual exit is a risk reduction the operator asked for directly, so
        # it executes rather than being routed back for confirmation.
        verdict = Verdict(
            decision=Decision.ALLOW,
            reasons=["Operator-initiated close of an existing position."],
            triggered_rules=["operator_close"],
        )
        outcome = await self._execute(intent, verdict)
        self._write_receipt(intent, verdict, (True, "Operator close"), outcome)
        f = outcome.get("fill")
        self._log(
            "fill" if outcome["status"] == "filled" else "warn", "operator",
            f"You closed {symbol}"
            + (f" at {f['price']:,.2f} for ${f['notional_usd']:,.0f} "
               f"(realised {f['realised_pnl_usd']:+.2f})." if f else f" — {outcome['status']}."),
        )
        return {"ok": outcome["status"] == "filled", "outcome": outcome}

    async def futures_intent(
        self, symbol: str, action: str, notional_usd: float, leverage: float,
        reason: str = "Manually placed by the operator (futures).",
    ) -> dict[str, Any]:
        """
        Open or close a USDⓈ-M perpetual futures position from the dashboard.

        `action` is OPEN_LONG, OPEN_SHORT or CLOSE. `notional_usd` here is the
        **margin** the operator commits; the position's notional is that times
        leverage, and it is the notional — the real exposure — that every
        downstream rule sees, so leverage is treated as the risk it is.

        This is not a bypass. The intent runs through the exact same
        `_process_one_intent` pipeline a spot trade does: the Constitution
        (now including the leverage and futures-notional caps), the Guardian
        veto, and the macro and news gates all apply, a large-enough position
        still routes to a human confirmation, and every step is written to the
        ledger as a signed receipt. Opening leveraged risk in a critical-threat
        market is vetoed by the Guardian exactly as a spot entry would be;
        closing is a risk reduction and is never blocked.
        """
        symbol = symbol.upper().strip()
        action = action.upper().strip()
        if action not in ("OPEN_LONG", "OPEN_SHORT", "CLOSE"):
            return {"ok": False, "error": f"Unknown futures action '{action}'."}

        marks = self._marks()
        price = marks.get(symbol)
        if not price or price <= 0:
            return {"ok": False, "error": f"No live price available for {symbol}."}

        pos = self.futures.positions.get(symbol)

        if action == "CLOSE":
            if not pos or pos.qty <= 0:
                return {"ok": False, "error": f"No open {symbol} futures position to close."}
            return await self.close_futures(symbol)

        leverage = max(1.0, min(float(leverage or 1), self.futures.max_leverage))
        margin = max(float(notional_usd), 0.0)
        position_notional = margin * leverage
        if position_notional < 10:
            return {"ok": False, "error": "Position notional (margin × leverage) is too small."}

        if pos and pos.side != ("LONG" if action == "OPEN_LONG" else "SHORT"):
            return {
                "ok": False,
                "error": (
                    f"You already hold a {pos.side} {symbol} futures position. "
                    f"Close it before opening the opposite side."
                ),
            }

        side = Side.BUY if action == "OPEN_LONG" else Side.SELL
        position_side = "LONG" if action == "OPEN_LONG" else "SHORT"
        # A default protective stop that scales with leverage and sits well
        # inside the liquidation price: roughly half the naive liquidation
        # distance. Higher leverage ⇒ tighter stop, which is correct.
        stop_frac = min(0.5 / leverage, 0.5)
        if position_side == "LONG":
            stop_loss = round(price * (1 - stop_frac), 8)
            take_profit = round(price * (1 + stop_frac * 2), 8)
        else:
            stop_loss = round(price * (1 + stop_frac), 8)
            take_profit = round(price * (1 - stop_frac * 2), 8)

        intent = Intent(
            intent_id=f"fut-{uuid.uuid4().hex[:10]}",
            ts=time.time(),
            module="operator",
            symbol=symbol,
            side=side,
            notional_usd=round(position_notional, 2),
            order_type="MARKET",
            limit_price=None,
            from_state=CapitalState.IDLE,
            to_state=CapitalState.DEPLOYED,
            thesis=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reference_price=price,
            urgency="routine",
            meta={
                "market": "futures",
                "action": action,
                "leverage": leverage,
                "position_side": position_side,
                "margin_usd": round(margin, 2),
                "reduce_only": False,
                "notional_usd": round(position_notional, 2),
            },
        )
        assessment = self.guardian.last or self.guardian.assess(
            self.market, self.portfolio.state_dict(marks), self.symbols
        )
        result = await self._process_one_intent(intent, assessment)
        if result is None:
            return {
                "ok": False,
                "error": "Blocked by macro or event-risk policy before reaching the Constitution.",
            }
        return {"ok": True, **result}

    async def close_futures(self, symbol: str) -> dict[str, Any]:
        """
        Flatten an open futures position (reduce-only), one click. Like the spot
        close, this is a risk reduction the operator asked for directly, so it
        executes rather than routing back for confirmation, and it is audited
        identically to the open.
        """
        symbol = symbol.upper().strip()
        marks = self._marks()
        price = marks.get(symbol)
        pos = self.futures.positions.get(symbol)
        if not pos or pos.qty <= 0:
            return {"ok": False, "error": f"No open {symbol} futures position to close."}
        if not price or price <= 0:
            return {"ok": False, "error": f"No live price available for {symbol}."}

        notional = pos.notional(price)
        side = Side.SELL if pos.side == "LONG" else Side.BUY
        intent = Intent(
            intent_id=f"futclose-{uuid.uuid4().hex[:10]}",
            ts=time.time(),
            module="operator",
            symbol=symbol,
            side=side,
            notional_usd=round(notional, 2),
            order_type="MARKET",
            limit_price=None,
            from_state=CapitalState.DEPLOYED,
            to_state=CapitalState.IDLE,
            thesis="Operator manually closed this futures position from the dashboard.",
            stop_loss=None,
            reference_price=price,
            urgency="routine",
            meta={
                "market": "futures",
                "action": "CLOSE",
                "leverage": pos.leverage,
                "position_side": pos.side,
                "reduce_only": True,
                "notional_usd": round(notional, 2),
            },
        )
        verdict = Verdict(
            decision=Decision.ALLOW,
            reasons=["Operator-initiated close of an existing futures position."],
            triggered_rules=["operator_close"],
        )
        outcome = await self._execute(intent, verdict)
        self._write_receipt(intent, verdict, (True, "Operator close"), outcome)
        f = outcome.get("futures_fill")
        self._log(
            "fill" if outcome["status"] == "filled" else "warn", "operator",
            f"You closed {pos.side} {symbol} futures"
            + (f" at {f['price']:,.4f} — realised {f['realised_pnl_usd']:+.2f}."
               if f else f" — {outcome['status']}."),
        )
        return {"ok": outcome["status"] == "filled", "outcome": outcome}

    async def confirm(self, intent_id: str, approve: bool) -> dict[str, Any]:
        pending = self.pending_confirmations.pop(intent_id, None)
        if not pending:
            return {"ok": False, "error": "No pending intent with that id."}

        intent: Intent = pending["_obj"]
        verdict = Verdict(
            decision=Decision.ALLOW if approve else Decision.DENY,
            reasons=[
                f"Operator {'approved' if approve else 'rejected'} this intent manually."
            ],
            triggered_rules=["human_confirmation"],
            adjusted_notional_usd=pending["verdict"].get("adjusted_notional_usd"),
        )
        outcome = (
            await self._execute(intent, verdict)
            if approve
            else {"status": "rejected_by_operator"}
        )
        receipt = self._write_receipt(
            intent, verdict, (True, "Operator decision"), outcome
        )
        self._log(
            "fill" if approve else "deny",
            "operator",
            f"You {'approved' if approve else 'rejected'} "
            f"{intent.side.value} {intent.symbol}.",
            {"receipt_id": receipt["receipt_id"]},
        )
        return {"ok": True, "outcome": outcome, "receipt": receipt}

    async def reconcile(self) -> dict[str, Any]:
        """
        Compare the local book against the Agentic sub-account.

        Binance is authoritative. A silent divergence between what the agent
        thinks it holds and what it actually holds is how a position cap gets
        breached without any rule firing, so any drift is logged and surfaced
        rather than smoothed over.
        """
        report = await self.executor.balances()
        if not report.get("available"):
            return {"reconciled": False, "reason": report.get("reason")}

        exchange = {b["asset"]: float(b["free"]) for b in report.get("balances", [])}
        marks = self._real_marks()
        drift = []
        for asset, qty in exchange.items():
            if asset in ("USDT", "USDC", "BUSD"):
                continue
            sym = f"{asset}USDT"
            local = self.portfolio.positions.get(sym)
            local_qty = local.qty if local else 0.0
            if abs(local_qty - qty) > max(qty * 0.01, 1e-8):
                drift.append({
                    "symbol": sym, "exchange_qty": round(qty, 8),
                    "local_qty": round(local_qty, 8),
                    "difference": round(qty - local_qty, 8),
                })
                # Binance wins.
                price = marks.get(sym) or (local.avg_price if local else 0.0)
                if qty > 1e-10 and price > 0:
                    self.portfolio.positions[sym] = Position(
                        symbol=sym, qty=qty,
                        avg_price=local.avg_price if local else price,
                        state=local.state if local else CapitalState.DEPLOYED,
                        stop_loss=local.stop_loss if local else None,
                        take_profit=local.take_profit if local else None,
                    )
                elif sym in self.portfolio.positions:
                    del self.portfolio.positions[sym]

        # Binance holds everything, so any locally "deployed" yield is fiction
        # and would double-count into equity.
        if self.portfolio.yield_deployed_usd:
            self.portfolio.recall_from_yield(self.portfolio.yield_deployed_usd)
        cash = exchange.get("USDT", 0.0)
        cash_drift = abs(self.portfolio.cash - cash)
        self.portfolio.cash = cash
        self.portfolio.yield_deployed_usd = 0.0

        if drift or cash_drift > 1.0:
            self.ledger.append(
                "reconciliation",
                {"drift": drift, "cash_drift_usd": round(cash_drift, 2),
                 "exchange_total_usd": report.get("totalUsdValue")},
            )
            self._log(
                "warn", "binance",
                f"Reconciled against the sub-account: {len(drift)} position(s) "
                f"and ${cash_drift:,.2f} of cash differed. Binance is authoritative.",
            )
        return {
            "reconciled": True, "drift": drift,
            "cash_drift_usd": round(cash_drift, 2),
            "exchange_total_usd": report.get("totalUsdValue"),
        }

    async def set_mode(
        self, mode: Mode, live_data: bool | None = None, confirm_live: bool = False
    ) -> dict[str, Any]:
        """
        Switch execution surface at runtime — this is what lets the dashboard's
        Control Center change mode without a shell restart.

        `mode=Mode.LIVE` places real orders in a real Binance sub-account, so it
        requires `confirm_live=True` as an explicit second signal from the
        caller, separate from picking the radio button. One click should not be
        able to go live by accident.
        """
        if mode == Mode.LIVE and not confirm_live:
            return {
                "ok": False,
                "error": (
                    "Switching to live mode places real orders on Binance. "
                    "Pass confirm_live=true to proceed."
                ),
            }

        old_mode = self.settings.mode
        # mock and live both execute through a real MCP client, which needs real
        # market data to price anything — so live data is not optional for them.
        self.settings.live_market_data = bool(live_data) or mode in (Mode.MOCK, Mode.LIVE)
        self.settings.mode = mode

        endpoint = self.settings.mcp_endpoint
        if mode == Mode.MOCK:
            endpoint = f"http://127.0.0.1:{os.environ.get('GLASSBOX_PORT', '8787')}/mock/mcp"

        rebuilt_mcp = False
        if mode in (Mode.MOCK, Mode.LIVE) and self.mcp.endpoint != endpoint:
            self.mcp.disconnect()
            self.mcp = BinanceMCPClient(
                endpoint=endpoint, token_path=TOKEN_PATH,
                device_key=self.ledger._key, client_id=self.settings.mcp_client_id,
            )
            self.executor = Executor(self.mcp, feeds=self.feeds)
            self.mcp_connected = False
            rebuilt_mcp = True

        if self.settings.live_market_data:
            await self.stream.start(self.symbols)
        else:
            await self.stream.stop()

        self.ledger.append(
            "mode_change",
            {"from": old_mode.value, "to": mode.value,
             "live_data": self.settings.live_market_data, "rebuilt_mcp": rebuilt_mcp},
        )
        self._log(
            "critical" if mode == Mode.LIVE else "info", "operator",
            f"Mode switched from {old_mode.value} to {mode.value}"
            + (f" (rebuilt MCP session at {endpoint})" if rebuilt_mcp else "") + ".",
        )
        return {
            "ok": True, "mode": mode.value,
            "live_market_data": self.settings.live_market_data,
            "mcp_endpoint": self.mcp.endpoint, "rebuilt_mcp": rebuilt_mcp,
        }

    def set_mcp_client_id(self, url: str) -> dict[str, Any]:
        """
        Set the OAuth client id — a URL to a hosted client-metadata document —
        from the dashboard, live, with no restart.

        Binance's Agent OS advertises `client_id_metadata_document_supported`,
        meaning the client id is not a registered string but a URL the
        authorization server fetches at authorization time. Previously the
        only way to set this was the `GLASSBOX_MCP_CLIENT_ID` environment
        variable — meaning "Authorize with Binance" would fail with an error
        that pointed at a shell variable and a restart, in an interface
        otherwise built to need neither. This is the fix: paste a URL, save,
        authorize — the running MCP client picks it up immediately.
        """
        url = url.strip()
        if url:
            # Binance's authorization server will fetch whatever URL is put
            # here, so an unvalidated value turns *their* infrastructure into
            # the SSRF target — the MCP spec calls this out under "SSRF
            # Against Authorization Servers". Validating here means GlassBox
            # cannot be used as the instrument of that attack.
            from .security import SecurityViolation, validate_client_id_url

            try:
                url = validate_client_id_url(url)
            except SecurityViolation as exc:
                return {"ok": False, "error": str(exc)}
        from .config import save_persisted_client_id, clear_persisted_client_id

        if url:
            save_persisted_client_id(url)
        else:
            clear_persisted_client_id()
        self.settings.mcp_client_id = url
        self.mcp.client_id = url
        self.ledger.append("mcp_client_id_set", {"configured": bool(url)})
        self._log(
            "info", "binance",
            "OAuth client id configured." if url else "OAuth client id cleared.",
        )
        return {"ok": True, "configured": bool(url)}

    def generate_client_metadata(self, hosted_at: str) -> dict[str, Any]:
        """
        Build the exact JSON document Binance expects at the URL the operator
        is about to host it at, so nobody has to hand-write OAuth client
        metadata to get past this. `hosted_at` must be the exact URL the
        document will be served from — some implementations of this draft
        validate that the document's own `client_id` field matches the URL it
        was fetched from, so this is not a cosmetic detail.
        """
        hosted_at = hosted_at.strip()
        doc = {
            "client_id": hosted_at,
            "client_name": "GlassBox",
            "redirect_uris": [f"http://127.0.0.1:{CALLBACK_PORT}/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "native",
            "dpop_bound_access_tokens": False,
        }
        return {
            "document": doc,
            "hosted_at": hosted_at,
            "instructions": (
                "Save this exact JSON to a file, host it at the URL above "
                "(GitHub Pages is free and needs no shell — see "
                "docs/GITHUB_UPLOAD.md), then paste that same URL into the "
                "Client ID field and save."
            ),
        }

    async def connect_mcp(self) -> dict[str, Any]:
        """Open an MCP session using stored credentials, if we have any."""
        try:
            status = await self.mcp.connect()
            self.mcp_connected = True
            self.ledger.append(
                "mcp_connected",
                {"endpoint": self.mcp.endpoint, "tools": len(status.tools),
                 "server": status.server_name, "scope": status.scope},
            )
            self._log(
                "info", "binance",
                f"Connected to {status.server_name or 'Binance MCP'} — "
                f"{len(status.tools)} tools available.",
            )
            # Anchor local accounting to what the sub-account actually holds, so
            # P&L is measured against real starting capital rather than a
            # placeholder.
            if self.settings.mode in (Mode.MOCK, Mode.LIVE):
                bal = await self.executor.balances()
                total = float(bal.get("totalUsdValue") or 0)
                if total > 0:
                    self.portfolio.starting_equity = total
                    self.portfolio.starting_equity_today = total
                    self.portfolio.peak_equity = total
                    self.portfolio.yield_deployed_usd = 0.0
                    self._benchmark_basis = None
                    # Drop pre-anchor points, otherwise the equity chart shows a
                    # cliff where the starting capital was corrected.
                    self.portfolio.equity_curve.clear()
                    await self.reconcile()
                    self._log(
                        "info", "binance",
                        f"Sub-account holds ${total:,.2f}. Local accounting anchored to it.",
                    )
            return {"ok": True, **status.to_dict()}
        except Exception as exc:
            self.mcp_connected = False
            self.mcp.status.last_error = str(exc)
            self._log("warn", "binance", f"Could not connect to Binance MCP: {exc}")
            return {"ok": False, "error": str(exc)}

    def disconnect_mcp(self) -> dict[str, Any]:
        self.mcp.disconnect()
        self.mcp_connected = False
        self.ledger.append("mcp_disconnected", {})
        self._log("info", "binance", "Disconnected from Binance and cleared the session.")
        return {"ok": True}

    async def discover_universe(self) -> dict[str, Any]:
        """
        Pick the pairs to watch from live Binance data instead of a hardcoded list.

        Ranked by real 24h USDT volume, with spread and minimum notional
        checked, because a pair the agent cannot get filled in is not a
        tradable pair regardless of how interesting it looks.
        """
        uni = await self.feeds.universe("USDT")
        tickers = await self.feeds.tickers_24h()
        books = await self.feeds.book_tickers()

        rows = []
        for u in uni:
            sym = u["symbol"]
            t = tickers.get(sym)
            if not t:
                continue
            price = float(t.get("lastPrice", 0) or 0)
            vol = float(t.get("quoteVolume", 0) or 0)
            if price <= 0 or vol < 2e7:
                continue
            # Stablecoin pairs have volume but no direction worth trading.
            if u["base"] in ("USDC", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "BUSD"):
                continue
            b = books.get(sym, {})
            bid = float(b.get("bidPrice", price) or price)
            ask = float(b.get("askPrice", price) or price)
            spread = (ask - bid) / max(price, 1e-9) * 10000
            if spread > 8:
                continue
            rows.append({"symbol": sym, "base": u["base"], "volume_24h_usd": vol,
                         "spread_bps": round(spread, 2), "price": price,
                         "min_notional": u["min_notional"]})

        rows.sort(key=lambda r: -r["volume_24h_usd"])
        chosen = [r["symbol"] for r in rows[: self.settings.universe_size]]
        if chosen:
            self.symbols = chosen
            await self.stream.set_watched(chosen)
            self.ledger.append("universe_discovered", {"symbols": chosen})
            self._log(
                "info", "engine",
                f"Watching the {len(chosen)} deepest USDT pairs on Binance: "
                f"{', '.join(chosen)}.",
            )
        return {"symbols": chosen, "candidates": rows[:40],
                "note": "Ranked by live 24h USDT volume, filtered on spread and depth."}

    def kill_switch(self, on: bool) -> dict[str, Any]:
        self.constitution.engage_kill_switch(on)
        self.ledger.append("kill_switch", {"engaged": on})
        self._log(
            "critical" if on else "info",
            "operator",
            "Kill switch engaged. No new risk will be taken."
            if on
            else "Kill switch released.",
        )
        return {"engaged": on}

    def reload_constitution(self) -> dict[str, Any]:
        before = self.constitution.doc
        self.constitution.reload()
        self.ledger.append(
            "constitution_change", {"before": before, "after": self.constitution.doc}
        )
        self._log("info", "operator", "Constitution reloaded and change recorded.")
        return {"ok": True, "rules": self.constitution.describe()}

    def set_scenario(self, name: str) -> dict[str, Any]:
        if isinstance(self.market, SimMarket):
            self.market.set_scenario(name)
            self.ledger.append("scenario_change", {"scenario": name})
            self._log("info", "operator", f"Market scenario set to '{name}'.")
            return {"ok": True, "scenario": name}
        return {"ok": False, "error": "Scenarios only apply to the simulated market."}

    def inject_shock(self, symbol: str, pct: float) -> dict[str, Any]:
        if isinstance(self.market, SimMarket):
            self.market.inject_shock(symbol, pct)
            self.ledger.append("shock_injected", {"symbol": symbol, "pct": pct})
            self._log("critical", "operator", f"Injected a {pct:+.1f}% shock in {symbol}.")
            return {"ok": True}
        return {"ok": False, "error": "Shocks only apply to the simulated market."}

    def clear_quarantine(self) -> dict[str, Any]:
        self.guardian.quarantined = False
        self.ledger.append("quarantine_cleared", {"by": "operator"})
        self._log("info", "operator", "Quarantine cleared manually.")
        return {"ok": True}

    # -- state for the UI --------------------------------------------------

    def status(self) -> dict[str, Any]:
        marks = self._marks()
        state = self.portfolio.state_dict(marks)
        return {
            "ts": time.time(),
            "tick": self.tick_count,
            "running": self.running,
            "autonomous": self.autonomous,
            "mode": self.settings.mode.value,
            "live_market_data": self.settings.live_market_data,
            "scenario": getattr(self.market, "scenario", "live"),
            "portfolio": state,
            "performance": self.portfolio.performance(marks),
            "market": self.market_view(),
            "guardian": self.guardian.status(),
            "council": {
                "roster": self.council.roster(),
                "verdicts": self.council_verdicts,
                "min_conviction": Council.MIN_CONVICTION,
                # The single most useful line for "why isn't this trading":
                # the verdict that came closest to qualifying, and by how much
                # it missed. A quiet, low-conviction market looking identical
                # to a broken one is the exact gap that caused real confusion.
                "closest_to_trading": self._closest_to_trading(),
                # Held positions the Council has since turned against — the
                # live counterpart to the pre-trade second-opinion check.
                "positions_opposed": self.positions_the_council_now_opposes(),
            },
            "narrative": self.narrative.status(),
            "compass": self.compass.status(state),
            "x402": self.meter.summary(),
            "ledger": {
                "height": self.ledger.height,
                "head": self.ledger.head,
            },
            "pending": [
                {k: v for k, v in p.items() if k != "_obj"}
                for p in self.pending_confirmations.values()
            ],
            "equity_curve": [
                {"ts": t, "equity": round(e, 2)}
                for t, e in list(self.portfolio.equity_curve)[-180:]
            ],
            "fills": [f.to_dict() for f in self.portfolio.fills[-40:]],
            "activity": self.activity[-80:],
            "receipts": self.receipts[-40:],
            "capital_states": self._capital_breakdown(state),
            "defensive_actions": self.defensive_actions[-25:],
            "counters": dict(self.counters),
            "benchmark": self.benchmark(),
            "feeds": self.feeds.health.to_dict(),
            "ratelimit": self.feeds.governor.status(),
            "stream": self.stream.status(),
            "mcp": self.mcp.status.to_dict(),
            "execution": self.executor.summary(),
            "coverage": self.ctx.coverage() if self.ctx else None,
            "calibration": self.calibration.summary(
                [a.name for a in self.council.analysts]
            ),
            "anchor": self.anchor.status(),
            "news": self.news.status(),
            "macro": self.macro.status(),
            "payments": self.payments.status(),
            "onchain": self.onchain.status(),
            "kline_interval": self.settings.kline_interval,
        }

    def benchmark(self) -> dict[str, Any]:
        """Equal-weight buy-and-hold over the same price path, same start date."""
        marks = self._marks()
        if not marks:
            return {"available": False}
        if self._benchmark_basis is None:
            self._benchmark_basis = dict(marks)
        basis = self._benchmark_basis
        per_symbol = self.settings.starting_equity_usd / max(len(basis), 1)
        value = sum(
            per_symbol * (marks.get(s, p) / p) for s, p in basis.items() if p > 0
        )
        pnl_pct = (value / self.settings.starting_equity_usd - 1) * 100
        eq = self.portfolio.equity(marks)
        strategy_pct = (eq / self.settings.starting_equity_usd - 1) * 100
        return {
            "available": True,
            "buy_and_hold_equity_usd": round(value, 2),
            "buy_and_hold_pnl_pct": round(pnl_pct, 3),
            "strategy_pnl_pct": round(strategy_pct, 3),
            "excess_return_pct": round(strategy_pct - pnl_pct, 3),
        }

    def _capital_breakdown(self, state: dict) -> dict[str, float]:
        """The capital state machine, as numbers the dashboard can draw."""
        hedged = sum(
            abs(p["notional_usd"])
            for p in state["positions"].values()
            if p["state"] == CapitalState.HEDGED.value
        )
        deployed = sum(
            abs(p["notional_usd"])
            for p in state["positions"].values()
            if p["state"] != CapitalState.HEDGED.value
        )
        quarantined = state["equity_usd"] if self.guardian.quarantined else 0.0
        idle = max(state["cash_usd"] + state["yield_deployed_usd"], 0.0)
        if quarantined:
            return {
                "IDLE": 0.0,
                "DEPLOYED": 0.0,
                "HEDGED": 0.0,
                "QUARANTINED": round(quarantined, 2),
            }
        return {
            "IDLE": round(idle, 2),
            "DEPLOYED": round(deployed, 2),
            "HEDGED": round(hedged, 2),
            "QUARANTINED": 0.0,
        }
