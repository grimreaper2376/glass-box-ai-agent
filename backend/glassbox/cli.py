"""
GlassBox — command line.

    python -m glassbox serve                 start the dashboard
    python -m glassbox backtest --ticks 600  headless paper run, prints results
    python -m glassbox drill                 run the adversarial safety drills
    python -m glassbox verify                verify the audit ledger
"""

from __future__ import annotations

import argparse
import asyncio
import os
import json
import sys
import time

from .config import Mode, Settings, ensure_dirs
from .engine import Engine
from .ledger import Ledger


def _fmt(n: float, width: int = 12) -> str:
    return f"{n:>{width},.2f}"


async def _backtest(ticks: int, scenario: str, equity: float, quiet: bool) -> dict:
    """
    Thin wrapper around `testkit.run_scenario_backtest`.

    Builds its own isolated Engine in a temp directory — regardless of what
    GLASSBOX_HOME or GLASSBOX_LIVE_DATA happen to be set to in the calling
    shell — so a scenario stress test can never leak into, or be leaked into
    by, the operator's real ledger, x402 budget, or live-data configuration.
    The dashboard's Control Center calls the exact same function.
    """
    from .testkit import isolated_engine

    async with isolated_engine(starting_equity_usd=equity) as engine:
        engine.set_scenario(scenario)
        engine.settings.simulated_clock = True
        # One tick is a 5-minute bar, so 500 ticks is roughly 41 hours of
        # market time. At 3s/tick the whole run modelled 25 minutes, which
        # made the 8-trades-per-hour limit bind on the entire backtest and
        # flattered the results into meaninglessness.
        engine.settings.sim_seconds_per_tick = 300.0
        engine.clock.simulated = True
        engine.clock.step_seconds = 300.0
        return await _run_backtest_loop(engine, ticks, scenario, quiet)


async def _run_backtest_loop(engine, ticks: int, scenario: str, quiet: bool) -> dict:
    for i in range(ticks):
        await engine.tick()
        # Auto-approve confirmations in headless runs so the loop does not stall,
        # and record that they were machine-approved, not operator-approved.
        for intent_id in list(engine.pending_confirmations):
            await engine.confirm(intent_id, approve=True)
        if not quiet and (i + 1) % max(ticks // 10, 1) == 0:
            s = engine.status()
            print(
                f"  tick {i + 1:>5}  equity {_fmt(s['portfolio']['equity_usd'])}  "
                f"pnl {s['portfolio']['total_pnl_pct']:+6.2f}%  "
                f"threat {s['guardian']['assessment']['score']:>5.1f}  "
                f"trades {s['portfolio']['fill_count']:>4}"
            )

    status = engine.status()
    return {
        "scenario": scenario,
        "ticks": ticks,
        "portfolio": status["portfolio"],
        "performance": status["performance"],
        "guardian": {
            "vetoes": status["guardian"]["veto_count"],
            "hedges": len(status["guardian"]["hedges"]),
            "final_score": status["guardian"]["assessment"]["score"],
        },
        "x402": status["x402"],
        "ledger": engine.ledger.verify(),
        "receipts": len(engine.receipts),
        "counters": status["counters"],
        "benchmark": status["benchmark"],
        "defensive_actions": len(engine.defensive_actions),
    }


def cmd_backtest(args) -> int:
    print(f"\nGlassBox paper run — scenario '{args.scenario}', {args.ticks} ticks\n")
    result = asyncio.run(_backtest(args.ticks, args.scenario, args.equity, args.quiet))
    p, perf = result["portfolio"], result["performance"]
    print("\n" + "─" * 66)
    print(f"  Starting equity     {_fmt(p['starting_equity_usd'])}")
    print(f"  Final equity        {_fmt(p['equity_usd'])}")
    print(f"  Net P&L             {_fmt(p['total_pnl_usd'])}  ({p['total_pnl_pct']:+.2f}%)")
    print(f"  Max drawdown        {perf['max_drawdown_pct']:>11.2f}%")
    print(f"  Closed trades       {perf['closed_trades']:>12}")
    print(f"  Win rate            {perf['win_rate_pct']:>11.1f}%")
    pf = perf["profit_factor"]
    print(f"  Profit factor       {(f'{pf:.2f}' if pf is not None else 'n/a'):>12}")
    print(f"  Sharpe (annualised) {perf['sharpe_annualised']:>12.2f}")
    print(f"  Fees paid           {_fmt(p['total_fees_usd'])}")
    print(f"  Yield earned        {_fmt(p['yield_earned_usd'])}")
    b = result["benchmark"]
    if b.get("available"):
        print(f"  Buy & hold benchmark{b['buy_and_hold_pnl_pct']:>11.2f}%")
        print(f"  Excess vs benchmark {b['excess_return_pct']:>11.2f}%")
    print("─" * 66)
    c = result["counters"]
    print(f"  Intents raised            {c['intents']:>6}")
    print(f"  Executed                  {c['allowed']:>6}")
    print(f"  Denied by the Constitution{c['denied']:>6}")
    print(f"  Guardian vetoes           {result['guardian']['vetoes']:>6}")
    print(f"  Defensive actions         {result['defensive_actions']:>6}")
    print(f"  Guardian hedges           {result['guardian']['hedges']:>6}")
    print(f"  Decision receipts written {result['receipts']:>6}")
    print(f"  x402 data spend           {result['x402']['spent_today_usd']:>9.4f} USD")
    print(
        f"  Ledger integrity          "
        f"{'VALID' if result['ledger']['valid'] else 'BROKEN':>6}  "
        f"({result['ledger']['records_checked']} records)"
    )
    print("─" * 66 + "\n")
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_drill(args) -> int:
    """
    Ten adversarial checks against a disposable engine and a disposable ledger.

    This deliberately never touches GLASSBOX_HOME. One of the drills corrupts a
    ledger record on purpose to prove tamper detection works, and running that
    against the operator's real audit trail would corrupt real history —
    `testkit.run_drills` builds its own throwaway engine in its own temp
    directory specifically so this command (and the dashboard's "Run drills"
    button, which calls the same function) can never do that.
    """
    from .testkit import run_drills

    print("\nGlassBox safety drills\n" + "─" * 66)
    result = asyncio.run(run_drills())
    for d in result["results"]:
        mark = "PASS" if d["passed"] else "FAIL"
        print(f"  [{mark}] {d['drill']}\n         {d['detail']}")
    print("─" * 66)
    print(f"  {result['passed']}/{result['total']} drills passed\n")
    return 0 if result["all_passed"] else 1


async def _replay(symbols, interval, bars, equity):
    from .replay import HistoricalReplay
    r = HistoricalReplay(Settings.from_env())
    try:
        return await r.run(symbols, interval=interval, bars=bars, equity=equity)
    finally:
        await r.aclose()


def cmd_replay(args) -> int:
    symbols = args.symbols.split(",") if args.symbols else \
        ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    print(f"\nDownloading {args.bars} × {args.interval} real candles from Binance...\n")
    try:
        res = asyncio.run(_replay(symbols, args.interval, args.bars, args.equity))
    except Exception as exc:
        print(f"  Backtest could not run: {exc}\n")
        return 1
    d = res.to_dict()
    frm = time.strftime("%Y-%m-%d %H:%M", time.gmtime(d["start_ts"]))
    to = time.strftime("%Y-%m-%d %H:%M", time.gmtime(d["end_ts"]))
    print(f"  Window          {frm} → {to} UTC")
    print(f"  Symbols         {', '.join(d['symbols'])}")
    print(f"  Bars replayed   {d['bars']} × {d['interval']}")
    print("─" * 66)
    print(f"  Final equity    {d['final_equity']:>14,.2f}")
    print(f"  Net P&L         {d['pnl_pct']:>13.2f}%")
    print(f"  Buy & hold      {d['buy_hold_pct']:>13.2f}%")
    print(f"  Difference      {d['excess_pct']:>13.2f}%")
    print(f"  Max drawdown    {d['max_drawdown_pct']:>13.2f}%")
    print(f"  Trades          {d['trades']:>14}")
    print(f"  Win rate        {d['win_rate_pct']:>13.1f}%")
    print(f"  Sharpe          {d['sharpe']:>14.2f}")
    print(f"  Intents/denied  {d['intents']:>7} / {d['denied']}")
    print("─" * 66)
    print(f"  Active analysts    {', '.join(d['analysts_active'])}")
    print(f"  Abstained          {', '.join(d['analysts_abstained'])}")
    for n in d["notes"]:
        print(f"  · {n}")
    print()
    if args.json:
        print(json.dumps(d, indent=2, default=str))
    return 0


def cmd_calibrate(args) -> int:
    """
    Backfill the analyst track record from real Binance history, then save it
    to the real `~/.glassbox/data/calibration.json` — unlike drills and
    scenario backtests, calibration is *meant* to seed the live installation's
    Track Record, whether run from here or from the dashboard's Control Center.
    """
    from .config import DATA_DIR
    from .testkit import run_calibration_backfill

    symbols = args.symbols.split(",") if args.symbols else \
        ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    print(f"\nBackfilling analyst track record from real Binance history...")
    print(f"  {args.bars} × {args.interval} bars, {args.horizon}-bar forward horizon\n")
    try:
        result = asyncio.run(
            run_calibration_backfill(symbols, args.interval, args.bars, args.horizon)
        )
    except Exception as exc:
        print(f"  Could not calibrate: {exc}\n")
        return 1

    tracker = result["_tracker"]
    tracker.path = DATA_DIR / "calibration.json"
    tracker.save()
    summary, graded, usable = result["summary"], result["graded_calls"], result["symbols_used"]
    print(f"  Graded {graded:,} calls across {', '.join(usable)}\n")
    print(f"  {'analyst':<14}{'calls':>8}{'hit rate':>11}{'brier':>9}{'edge':>10}{'weight':>9}")
    print("  " + "─" * 61)
    for a in summary["analysts"]:
        if not a["samples"]:
            continue
        print(f"  {a['analyst']:<14}{a['samples']:>8,}"
              f"{a['hit_rate'] * 100:>10.1f}%{a['brier']:>9.3f}"
              f"{a['edge_bps']:>9.1f}bp{a['reliability']:>8.2f}×")
    print()
    print("  Brier below 0.25 beats an uninformed guess. Weight scales the")
    print("  analyst's vote on the Council and is now saved to disk.\n")
    return 0


async def _connect(endpoint, client_id):
    from .config import TOKEN_PATH
    from .ledger import load_or_create_device_key
    from .mcp import BinanceMCPClient

    c = BinanceMCPClient(endpoint=endpoint, token_path=TOKEN_PATH,
                         device_key=load_or_create_device_key(), client_id=client_id)
    meta = await c.discover()
    print("  Discovered Binance's OAuth configuration:")
    print(f"    authorize  {meta['authorization_endpoint']}")
    print(f"    token      {meta['token_endpoint']}")
    print(f"    PKCE       {', '.join(meta['pkce_methods'])}")
    if not client_id:
        print()
        print("  No client id configured. Binance advertises")
        print("  client_id_metadata_document_supported, so the client id is a URL")
        print("  pointing at a hosted client metadata document.")
        print("  Set GLASSBOX_MCP_CLIENT_ID to that URL and run this again.")
        print()
        print("  Meanwhile you can connect through Claude Code, which handles this:")
        print("    claude mcp add binance-mcp-server --transport http \\")
        print(f"      {endpoint}")
        await c.aclose()
        return None
    await c.authorize_with_callback()
    status = await c.connect()
    await c.aclose()
    return status


def cmd_connect(args) -> int:
    from .config import Settings as _S

    st = _S.from_env()
    endpoint = args.endpoint or st.mcp_endpoint
    print(f"\nConnecting GlassBox to Binance Agent OS\n  {endpoint}\n")
    try:
        status = asyncio.run(_connect(endpoint, args.client_id or st.mcp_client_id))
    except Exception as exc:
        print(f"  Could not connect: {exc}\n")
        return 1
    if status is None:
        return 0
    print(f"\n  Connected to {status.server_name} {status.server_version}")
    print(f"  Protocol {status.protocol_version}, {len(status.tools)} tools:\n")
    for t in status.tools:
        print(f"    {t.get('name'):<28} {(t.get('description') or '')[:60]}")
    print("\n  Session stored encrypted at rest. Run `glassbox serve` to use it.\n")
    return 0


async def _discover(size):
    from .engine import Engine

    st = Settings.from_env()
    st.live_market_data = True
    st.universe_size = size
    e = Engine(st)
    out = await e.discover_universe()
    await e.feeds.aclose()
    return out


def cmd_discover(args) -> int:
    print(f"\nRanking live USDT pairs on Binance by real 24h volume...\n")
    try:
        out = asyncio.run(_discover(args.size))
    except Exception as exc:
        print(f"  Could not reach Binance: {exc}\n")
        return 1
    print(f"  {'pair':<14}{'24h volume':>14}{'spread':>10}{'price':>16}")
    print("  " + "-" * 54)
    for r in out["candidates"][: args.size]:
        print(f"  {r['symbol']:<14}${r['volume_24h_usd'] / 1e6:>12,.0f}M"
              f"{r['spread_bps']:>9.2f}bp{r['price']:>16,.4f}")
    print()
    print(f"  Selected: {', '.join(out['symbols'])}")
    print(f"  {out['note']}")
    print()
    print("  Add these to constitution.yaml under symbol_allowlist to make them")
    print("  tradable. Watching is looking; the allowlist is permission.\n")
    return 0


def cmd_verify(args) -> int:
    report = Ledger().verify()
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


def _port_in_use(port: int) -> bool:
    """Is something already listening on this port?"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_our_server(port: int) -> bool:
    """
    Is the thing on that port a GlassBox instance, or something unrelated?

    This matters: offering to stop "the existing server" would be alarming if
    the port were actually held by someone's unrelated dev server.
    """
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/session", timeout=1.5
        ) as r:
            return "token" in json.load(r)
    except Exception:
        return False


def cmd_serve(args) -> int:
    import threading
    import webbrowser

    import uvicorn

    if getattr(args, "mode", None):
        os.environ["GLASSBOX_MODE"] = args.mode
    os.environ["GLASSBOX_PORT"] = str(args.port)

    # Double-launch handling. Running start.sh / START.bat twice used to print
    # a success banner and then an uncaught "address already in use" traceback,
    # which looks like a crash rather than "it's already running".
    url = f"http://127.0.0.1:{args.port}"
    if _port_in_use(args.port):
        if _is_our_server(args.port):
            print("\n" + "═" * 62)
            print("  GlassBox is already running")
            print("═" * 62)
            print(f"  Dashboard     {url}")
            print("\n  Nothing to do — opening the existing dashboard.")
            print("  To restart it cleanly, close the other window first,")
            print(f"  or run on a different port:  --port {args.port + 1}\n")
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return 0
        print(f"\n  Port {args.port} is already in use by something that is not")
        print("  GlassBox. Leaving it alone — start on another port with:")
        print(f"      python -m glassbox serve --port {args.port + 1}\n")
        return 1

    from .server import SESSION_TOKEN

    settings = Settings.from_env()
    auto_started = settings.mode.value not in ("live", "bridge")

    print("\n" + "═" * 62)
    print("  GlassBox is running")
    print("═" * 62)
    print(f"  Dashboard     {url}")
    print(f"  Mode          {settings.mode.value}")
    print(f"  Live data     {'on — real Binance prices and charts' if settings.live_market_data else 'off — seeded simulator'}")
    print(f"  Engine        {'started automatically' if auto_started else 'stopped — press Start engine, or use --mode paper for auto-start'}")
    print(f"  Session       {SESSION_TOKEN[:10]}…  (bound to this process)")
    print("═" * 62 + "\n")

    if not args.no_browser:
        # Opened from a background thread on a short delay rather than before
        # uvicorn starts, so the tab doesn't load before anything is
        # listening on the port yet.
        #
        # daemon=True is not optional here. threading.Timer threads default
        # to non-daemon, and webbrowser.open() has no timeout: on a machine
        # with no browser configured (headless servers, some containers,
        # some minimal Linux setups) the underlying subprocess call can hang
        # indefinitely. A non-daemon thread stuck inside that call silently
        # blocks the whole Python process from exiting on Ctrl+C — the
        # server looks stopped, uvicorn's own shutdown completes, and the
        # process just never returns control to the terminal. Wrapping the
        # call and marking the thread daemon means the worst case is a
        # browser tab that doesn't open, never a launcher that won't quit.
        def _open_browser() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                pass  # opening a tab is a courtesy, never a requirement

        t = threading.Timer(1.5, _open_browser)
        t.daemon = True
        t.start()

    uvicorn.run(
        "glassbox.server:app",
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        reload=False,
        # uvicorn's default here is None — wait indefinitely for every open
        # connection to close naturally before exiting. With a persistent
        # WebSocket endpoint and a background market-data stream, that means
        # Ctrl+C can hang forever instead of actually stopping the process.
        # A bounded timeout guarantees the launcher scripts, and a person
        # pressing Ctrl+C, get a process that actually exits.
        timeout_graceful_shutdown=3,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    parser = argparse.ArgumentParser(prog="glassbox", description="GlassBox agent console")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Start the dashboard")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument(
        "--mode", default=None,
        choices=["paper", "shadow", "mock", "live", "bridge"],
        help="execution surface; mock runs a real MCP client against a local server",
    )
    p_serve.add_argument(
        "--no-browser", action="store_true",
        help="don't open a browser tab automatically on startup",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_bt = sub.add_parser("backtest", help="Headless paper run")
    p_bt.add_argument("--ticks", type=int, default=400)
    p_bt.add_argument("--scenario", default="calm", choices=["calm", "bull", "bear", "chop", "crash"])
    p_bt.add_argument("--equity", type=float, default=10_000.0)
    p_bt.add_argument("--json", action="store_true")
    p_bt.add_argument("--quiet", action="store_true")
    p_bt.set_defaults(func=cmd_backtest)

    p_dr = sub.add_parser("drill", help="Run adversarial safety drills")
    p_dr.set_defaults(func=cmd_drill)

    p_rp = sub.add_parser("replay", help="Backtest on real Binance candles")
    p_rp.add_argument("--symbols", default=None, help="comma separated, e.g. BTCUSDT,ETHUSDT")
    p_rp.add_argument("--interval", default="1h", choices=["15m", "1h", "4h", "1d"])
    p_rp.add_argument("--bars", type=int, default=600)
    p_rp.add_argument("--equity", type=float, default=10_000.0)
    p_rp.add_argument("--json", action="store_true")
    p_rp.set_defaults(func=cmd_replay)

    p_cal = sub.add_parser("calibrate", help="Backfill analyst track record from real history")
    p_cal.add_argument("--symbols", default=None)
    p_cal.add_argument("--interval", default="1h", choices=["15m", "1h", "4h", "1d"])
    p_cal.add_argument("--bars", type=int, default=500)
    p_cal.add_argument("--horizon", type=int, default=6, help="forward bars to grade against")
    p_cal.set_defaults(func=cmd_calibrate)

    p_cn = sub.add_parser("connect", help="Connect to Binance Agent OS over MCP")
    p_cn.add_argument("--endpoint", default=None)
    p_cn.add_argument("--client-id", default=None, help="CIMD URL for OAuth")
    p_cn.set_defaults(func=cmd_connect)

    p_ds = sub.add_parser("discover", help="Rank live USDT pairs from the Binance API")
    p_ds.add_argument("--size", type=int, default=10)
    p_ds.set_defaults(func=cmd_discover)

    p_vf = sub.add_parser("verify", help="Verify the audit ledger")
    p_vf.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
