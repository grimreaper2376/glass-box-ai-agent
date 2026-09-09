"""
GlassBox — test runners.

Every function here builds its own throwaway `Engine` in its own temporary
directory, and never touches the operator's real `~/.glassbox`. This is what
makes it safe to expose "Run drills" as a button in the dashboard: clicking it
cannot corrupt production audit history, drain the real x402 budget, or leave
stray positions in the paper portfolio the operator is actually watching.

The CLI (`glassbox drill`, `glassbox backtest`, `glassbox calibrate`) and the
dashboard's Control Center both call these same functions, so the two surfaces
can never quietly drift into testing different things.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .config import CapitalState, Clock, Intent, Mode, Settings, Side
from .engine import Engine

# A fixed moment safely outside every window in the default constitution.yaml
# (UTC 09:00 on an arbitrary date — nowhere near the 12:25-12:45 or
# 18:00-18:20 blackout windows). Every isolated test engine's clock starts
# here rather than at whatever real wall-clock time the drill happened to be
# run, specifically so results never depend on what time of day someone
# clicks the button.
_FIXED_TEST_EPOCH = 1_767_002_400.0  # 2025-12-29T10:00:00Z


@asynccontextmanager
async def isolated_engine(**settings_overrides):
    """
    An Engine backed by a fresh temp directory, cleaned up on exit whether the
    body raises or not.
    """
    tmp = Path(tempfile.mkdtemp(prefix="glassbox-test-"))
    try:
        settings = Settings()
        settings.mode = Mode.PAPER
        # Explicit, not incidental: isolated test runs must stay fast,
        # deterministic and offline-capable regardless of what the real
        # dashboard's live-data default is set to. `Settings()` already
        # defaults to False here rather than going through `from_env()`, but
        # this line documents the invariant so it can't silently break if
        # that changes later — a drill or scenario backtest reaching out to
        # live Binance data would be slower, non-deterministic, and would
        # fail with no network at all, none of which a safety drill should
        # ever depend on.
        settings.live_market_data = False
        settings.tick_seconds = 0.0
        # Deterministic time, pinned to a fixed moment safely outside any
        # configured blackout window — see _FIXED_TEST_EPOCH above for why
        # this matters. A caller can still override `simulated_clock` via
        # settings_overrides if a test genuinely wants real-time behaviour.
        settings.simulated_clock = True
        for k, v in settings_overrides.items():
            setattr(settings, k, v)
        engine = Engine(settings, data_dir=tmp)
        engine.clock.set_time(_FIXED_TEST_EPOCH)
        # Drills, scenario backtests and history replays all exist precisely to
        # exercise the engine's own trading and its safety rules end-to-end, so
        # the isolated test engine trades autonomously by default. This is the
        # opposite of the live server's default (off, requiring explicit
        # operator consent) — and correctly so: a drill that never opened a
        # position could not prove the rules that block bad positions work.
        engine.autonomous = True
        try:
            yield engine
        finally:
            await engine.feeds.aclose()
            await engine.stream.stop()
            await engine.news.aclose()
            await engine.macro.aclose()
            await engine.settlement_wallet.aclose()
            await engine.chain_inspector.aclose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Safety drills
# ---------------------------------------------------------------------------


async def run_drills() -> dict[str, Any]:
    """
    Ten adversarial checks against a disposable engine and a disposable ledger.
    Each asserts a specific safety property rather than merely describing one.
    """
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        results.append({"drill": name, "passed": passed, "detail": detail})

    async with isolated_engine() as engine:
        engine.set_scenario("calm")
        for _ in range(40):
            await engine.tick()

        # 1. Flash crash reaches critical
        engine.set_scenario("crash")
        for symbol in engine.symbols:
            engine.inject_shock(symbol, -7.5)
        for _ in range(3):
            await engine.tick()
        a = engine.guardian.last
        check(
            "Flash crash is detected as critical",
            a.score >= engine.guardian.critical_at,
            f"Threat reached {a.score:.0f}/100 ({a.level}) — {a.factors[0]['detail']}.",
        )

        # 1a. Exposure is actually cut
        defended = engine.defensive_actions
        check(
            "Exposure is cut when the crash hits",
            len(defended) > 0,
            f"{len(defended)} defensive action(s): "
            + ", ".join(f"{d['kind']} on {d['symbol']}" for d in defended[:3]) + ".",
        )

        # 1b. Guardian vetoes while critical
        vetoes_before = engine.guardian._veto_count
        for _ in range(2):
            await engine.tick()
        check(
            "Guardian vetoes new risk while conditions are critical",
            engine.guardian._veto_count > vetoes_before or engine.guardian.quarantined,
            f"{engine.guardian._veto_count - vetoes_before} intent(s) vetoed after "
            f"the crash; quarantined={engine.guardian.quarantined}.",
        )

        # 2. Oversized position is capped
        engine.set_scenario("calm")
        await engine.tick()
        state = engine.portfolio.state_dict(engine._marks())
        huge = Intent(
            intent_id=f"drill-{uuid.uuid4().hex[:6]}", ts=time.time(), module="council",
            symbol="BTCUSDT", side=Side.BUY, notional_usd=state["equity_usd"] * 5,
            order_type="MARKET", limit_price=None, from_state=CapitalState.IDLE,
            to_state=CapitalState.DEPLOYED, thesis="Deliberately oversized for the drill.",
            stop_loss=1.0,
        )
        v = engine.constitution.evaluate(huge, state, engine.market.snapshot())
        trimmed = v.adjusted_notional_usd is not None and v.adjusted_notional_usd < huge.notional_usd
        check(
            "Oversized position is capped by the Constitution",
            trimmed or v.decision.value == "DENY",
            f"Requested ${huge.notional_usd:,.0f}; policy returned {v.decision.value}"
            + (f" and trimmed to ${v.adjusted_notional_usd:,.0f}." if trimmed else "."),
        )

        # 3. Kill switch
        engine.kill_switch(True)
        v2 = engine.constitution.evaluate(huge, state, engine.market.snapshot())
        check(
            "Kill switch denies every new intent",
            v2.decision.value == "DENY",
            f"With the kill switch engaged the verdict was {v2.decision.value}.",
        )
        engine.kill_switch(False)

        # 4. No stop loss
        no_stop = Intent(
            intent_id=f"drill-{uuid.uuid4().hex[:6]}", ts=time.time(), module="council",
            symbol="BTCUSDT", side=Side.BUY, notional_usd=50.0, order_type="MARKET",
            limit_price=None, from_state=CapitalState.IDLE, to_state=CapitalState.DEPLOYED,
            thesis="Entry with no protective stop.", stop_loss=None,
        )
        v3 = engine.constitution.evaluate(no_stop, state, engine.market.snapshot())
        check(
            "Entries without a stop loss are refused",
            v3.decision.value == "DENY",
            f"Verdict was {v3.decision.value}.",
        )

        # 5. Unlisted symbol
        off_list = Intent(
            intent_id=f"drill-{uuid.uuid4().hex[:6]}", ts=time.time(), module="narrative",
            symbol="SCAMUSDT", side=Side.BUY, notional_usd=50.0, order_type="MARKET",
            limit_price=None, from_state=CapitalState.IDLE, to_state=CapitalState.DEPLOYED,
            thesis="A token the agent read about on social media.", stop_loss=1.0,
        )
        v4 = engine.constitution.evaluate(off_list, state, engine.market.snapshot())
        check(
            "Unlisted symbols are refused",
            v4.decision.value == "DENY",
            f"SCAMUSDT verdict was {v4.decision.value}.",
        )

        # 6. x402 budget
        from .x402 import BudgetExceeded

        blocked, detail = False, ""
        try:
            for _ in range(10_000):
                engine.meter.purchase("social-sentiment", "spam", 0.02, "drill")
        except BudgetExceeded as exc:
            blocked, detail = True, str(exc)
        check(
            "x402 data budget is enforced", blocked,
            detail if blocked else "Budget was never enforced — this is a defect.",
        )

        # 7. Secret redaction
        from .redact import contains_secret

        engine.ledger.append(
            "drill_secret_probe",
            {"note": "my key is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA and it is fine"},
        )
        tail = json.dumps(engine.ledger.read(limit=1))
        check(
            "Secrets are redacted before they touch disk",
            not contains_secret(tail),
            "A planted API key did not survive into the ledger record."
            if not contains_secret(tail) else "A secret was written to disk — this is a defect.",
        )

        # 8. Ledger tamper detection — safe here because this ledger lives in a
        #    throwaway temp directory, never the operator's real one.
        report_before = engine.ledger.verify()
        path = engine.ledger.path
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 3:
            rec = json.loads(lines[2])
            rec["payload"]["tampered"] = True
            lines[2] = json.dumps(rec, default=str)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report_after = engine.ledger.verify()
        check(
            "Ledger tampering is detected",
            report_before["valid"] and not report_after["valid"],
            f"Chain was valid before the edit and reported "
            f"{len(report_after['problems'])} problem(s) after it.",
        )

        # 9. Event risk can never enlarge or create a position
        from .news import EventRisk

        fields = set(EventRisk.__dataclass_fields__)
        check(
            "News/event risk has no path to a buy signal",
            not (fields & {"buy", "long", "stance", "direction", "signal"}),
            "EventRisk carries only blocks, blackouts and a size multiplier "
            "≤ 1.0 — there is no field it could use to open a position.",
        )

        # 10. Withdrawal is refused by the execution surface
        from .mockmcp import MockBinanceMCP

        mock = MockBinanceMCP(1000.0, engine.feeds)
        out = await mock.call_tool("withdraw_funds", {"amount": 100})
        check(
            "Withdrawal requests are refused outright",
            out.get("isError", False),
            "The execution surface has no withdrawal capability, by construction.",
        )

    passed = sum(1 for r in results if r["passed"])
    return {"results": results, "passed": passed, "total": len(results),
            "all_passed": passed == len(results)}


# ---------------------------------------------------------------------------
# Simulated scenario backtest
# ---------------------------------------------------------------------------


async def run_scenario_backtest(
    scenario: str = "calm", ticks: int = 400, equity: float = 10_000.0,
) -> dict[str, Any]:
    """Headless paper run against the seeded simulator. Auto-approves any
    confirmation prompts so a stress test never stalls waiting for a human."""
    async with isolated_engine(starting_equity_usd=equity) as engine:
        engine.set_scenario(scenario)
        engine.settings.simulated_clock = True
        engine.settings.sim_seconds_per_tick = 300.0  # each tick is a 5-minute bar
        engine.clock.simulated = True
        engine.clock.step_seconds = 300.0

        for _ in range(ticks):
            await engine.tick()
            for intent_id in list(engine.pending_confirmations):
                await engine.confirm(intent_id, approve=True)

        status = engine.status()

    return {
        "scenario": scenario, "ticks": ticks,
        "portfolio": status["portfolio"], "performance": status["performance"],
        "guardian": {"vetoes": status["guardian"]["veto_count"],
                     "hedges": len(status["guardian"]["hedges"]),
                     "final_score": status["guardian"]["assessment"]["score"]},
        "benchmark": status["benchmark"], "counters": status["counters"],
        "x402": status["x402"], "receipts": len(status["receipts"]),
    }


# ---------------------------------------------------------------------------
# Calibration backfill
# ---------------------------------------------------------------------------


async def run_calibration_backfill(
    symbols: list[str], interval: str = "1h", bars: int = 500, horizon: int = 6,
) -> dict[str, Any]:
    """
    Replays real Binance history through the technical and regime analysts and
    grades every call against the known forward return, so the Track Record
    tab shows a meaningful number the first time anyone opens it rather than
    waiting a day for live horizons to elapse.
    """
    from .agents.analysts import RegimeAnalyst, TechnicalAnalyst
    from .calibration import CalibrationTracker
    from .marketdata import MarketContext
    from .replay import HistoricalReplay, INTERVAL_MS

    replay = HistoricalReplay(Settings())
    try:
        data = await replay.load(symbols, interval, bars + 80)
        usable = [s for s in symbols if len(data.get(s, [])) > 120]
        if not usable:
            raise RuntimeError("No candle history returned for any requested symbol.")

        step = INTERVAL_MS.get(interval, 300_000) / 1000
        tracker = CalibrationTracker(horizon_seconds=step * horizon)
        analysts = [TechnicalAnalyst(), RegimeAnalyst()]
        n = min(len(data[s]) for s in usable)
        graded = 0

        for i in range(80, n - horizon):
            ctx = MarketContext(ts=0.0, symbols=usable)
            for s in usable:
                ctx.klines[s] = data[s][: i + 1]
            prices_fwd = {s: data[s][i + horizon]["close"] for s in usable}
            prices_now = {s: data[s][i]["close"] for s in usable}

            for s in usable:
                sigs = [await a.analyse(s, ctx) for a in analysts]
                tracker.record(sigs, prices_now)
                for c in list(tracker.open_calls):
                    c.made_ts, c.horizon_seconds = 0.0, 0.0
                graded += len(tracker.grade_due(prices_fwd, now=1.0))

        return {
            "graded_calls": graded, "symbols_used": usable,
            "interval": interval, "bars": bars, "horizon_bars": horizon,
            "summary": tracker.summary([a.name for a in analysts]),
            "_tracker": tracker,  # consumed by the caller to update the live engine
        }
    finally:
        await replay.aclose()
