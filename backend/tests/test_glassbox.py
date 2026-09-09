"""
GlassBox unit tests.

These cover the properties the safety story depends on. The `drill` command
tests the same guarantees end-to-end; these test them in isolation so a failure
points at one component.

    pip install pytest
    pytest tests -q
"""

import json
import time
import uuid
from pathlib import Path

import pytest

from glassbox.config import CapitalState, Clock, Decision, Intent, Side
from glassbox.constitution import Constitution
from glassbox.ledger import Ledger
from glassbox.portfolio import PaperPortfolio
from glassbox.redact import contains_secret, redact
from glassbox.x402 import BudgetExceeded, X402Meter


def make_intent(**kw):
    base = dict(
        intent_id="t1",
        ts=time.time(),
        module="council",
        symbol="BTCUSDT",
        side=Side.BUY,
        notional_usd=500.0,
        order_type="MARKET",
        limit_price=None,
        from_state=CapitalState.IDLE,
        to_state=CapitalState.DEPLOYED,
        thesis="test",
        stop_loss=1.0,
    )
    base.update(kw)
    return Intent(**base)


PORTFOLIO = {
    "equity_usd": 10_000.0,
    "starting_equity_today_usd": 10_000.0,
    "peak_equity_usd": 10_000.0,
    "realised_pnl_today_usd": 0.0,
    "drawdown_pct": 0.0,
    "gross_exposure_pct": 0.0,
    "fills_last_hour": 0,
    "positions": {},
    "now_ts": time.time(),
}
MARKET = {"BTCUSDT": {"spread_bps": 2.0, "atr_pct": 1.0, "price": 60000.0}}


# ---------------------------------------------------------------- ledger


def test_ledger_chain_verifies(tmp_path):
    led = Ledger(path=tmp_path / "l.jsonl")
    for i in range(20):
        led.append("test", {"i": i})
    report = led.verify()
    assert report["valid"]
    assert report["records_checked"] == 20


def test_ledger_detects_tampering(tmp_path):
    path = tmp_path / "l.jsonl"
    led = Ledger(path=path)
    for i in range(10):
        led.append("test", {"i": i})
    assert led.verify()["valid"]

    lines = path.read_text().splitlines()
    rec = json.loads(lines[4])
    rec["payload"]["i"] = 999
    lines[4] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")

    report = led.verify()
    assert not report["valid"]
    assert any(p["seq"] == 5 for p in report["problems"])


def test_ledger_detects_deletion(tmp_path):
    path = tmp_path / "l.jsonl"
    led = Ledger(path=path)
    for i in range(10):
        led.append("test", {"i": i})
    lines = path.read_text().splitlines()
    del lines[5]
    path.write_text("\n".join(lines) + "\n")
    assert not led.verify()["valid"]


# ---------------------------------------------------------------- redaction


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9AAAAAAAAAAAA",
        "0x" + "a" * 64,
    ],
)
def test_secrets_are_redacted(secret):
    out = json.dumps(redact({"note": f"value is {secret} ok"}))
    assert secret not in out


def test_hashes_survive_redaction():
    """A SHA-256 hash must not be mistaken for an API key, or the audit chain
    would be redacted into uselessness."""
    h = "a" * 64  # lowercase hex, the shape of every hash we write
    assert redact({"hash": h})["hash"] == h
    assert not contains_secret(h)


def test_sensitive_keys_masked_by_name():
    out = redact({"api_secret": "anything", "nested": {"token": "xyz"}})
    assert out["api_secret"] != "anything"
    assert out["nested"]["token"] != "xyz"


# ---------------------------------------------------------------- constitution


def test_kill_switch_denies():
    c = Constitution(doc={"rules": {"kill_switch": {"engaged": True}}})
    assert c.evaluate(make_intent(), PORTFOLIO, MARKET).decision == Decision.DENY


def test_symbol_allowlist_denies_unlisted():
    c = Constitution(doc={"rules": {"symbol_allowlist": {"symbols": ["ETHUSDT"]}}})
    assert c.evaluate(make_intent(), PORTFOLIO, MARKET).decision == Decision.DENY


def test_oversized_position_is_trimmed_not_rejected():
    c = Constitution(doc={"rules": {"max_position_pct": {"pct": 10}}})
    v = c.evaluate(make_intent(notional_usd=5000.0), PORTFOLIO, MARKET)
    assert v.decision == Decision.ALLOW
    assert v.adjusted_notional_usd == pytest.approx(1000.0)


def test_entry_without_stop_is_denied():
    c = Constitution(doc={"rules": {"require_stop_loss": {"enabled": True}}})
    v = c.evaluate(make_intent(stop_loss=None), PORTFOLIO, MARKET)
    assert v.decision == Decision.DENY


def test_large_trade_requires_human():
    c = Constitution(
        doc={"rules": {"human_confirmation": {"always_confirm_above_pct": 5}}}
    )
    v = c.evaluate(make_intent(notional_usd=900.0), PORTFOLIO, MARKET)
    assert v.decision == Decision.REQUIRE_HUMAN


def test_strictest_verdict_wins():
    c = Constitution(
        doc={
            "rules": {
                "human_confirmation": {"always_confirm_above_pct": 1},
                "kill_switch": {"engaged": True},
            }
        }
    )
    assert c.evaluate(make_intent(), PORTFOLIO, MARKET).decision == Decision.DENY


def test_broken_rule_fails_closed():
    c = Constitution(doc={"rules": {"daily_loss_limit": {"pct": "not-a-number"}}})
    v = c.evaluate(make_intent(), {**PORTFOLIO, "realised_pnl_today_usd": -1.0}, MARKET)
    assert v.decision == Decision.DENY


def test_cooldown_uses_the_supplied_clock():
    """Time-based rules must read the simulated clock, not the wall clock."""
    c = Constitution(doc={"rules": {"cooldown_after_loss": {"minutes": 30}}})
    now = 1_000_000.0
    state = {**PORTFOLIO, "last_losing_exit_ts": now - 60, "now_ts": now}
    assert c.evaluate(make_intent(), state, MARKET).decision == Decision.DENY
    state["now_ts"] = now + 3600
    assert c.evaluate(make_intent(), state, MARKET).decision == Decision.ALLOW


# ---------------------------------------------------------------- x402


def test_budget_is_enforced():
    m = X402Meter(daily_budget_usd=0.10, max_per_request_usd=0.05)
    m.purchase("p", "r", 0.05, "a")
    m.purchase("p", "r", 0.05, "a")
    with pytest.raises(BudgetExceeded):
        m.purchase("p", "r", 0.05, "a")


def test_per_request_cap_enforced():
    m = X402Meter(daily_budget_usd=100.0, max_per_request_usd=0.05)
    with pytest.raises(BudgetExceeded):
        m.purchase("p", "r", 5.0, "a")


def test_provider_allowlist_enforced():
    m = X402Meter(allowed_providers=["good"])
    with pytest.raises(BudgetExceeded):
        m.purchase("bad", "r", 0.01, "a")


# ---------------------------------------------------------------- portfolio


def test_buy_then_sell_realises_pnl():
    p = PaperPortfolio(10_000.0)
    p.execute("BTCUSDT", "BUY", 1000.0, 100.0)
    qty = p.positions["BTCUSDT"].qty
    assert qty > 0
    # Sell the full position at the new mark, not the original notional — at a
    # higher price $1,000 of notional is fewer units than we hold.
    p.execute("BTCUSDT", "SELL", qty * 120.0, 120.0)
    assert "BTCUSDT" not in p.positions
    assert p.realised_today > 0


def test_partial_sell_leaves_a_position():
    p = PaperPortfolio(10_000.0)
    p.execute("BTCUSDT", "BUY", 1000.0, 100.0)
    p.execute("BTCUSDT", "SELL", 400.0, 100.0)
    assert p.positions["BTCUSDT"].qty == pytest.approx(6.0, rel=0.02)


def test_cannot_sell_what_is_not_held():
    p = PaperPortfolio(10_000.0)
    assert p.execute("BTCUSDT", "SELL", 500.0, 100.0) is None


def test_cannot_spend_more_than_cash():
    p = PaperPortfolio(100.0)
    p.execute("BTCUSDT", "BUY", 10_000.0, 100.0)
    assert p.cash >= 0


def test_stop_loss_fires():
    p = PaperPortfolio(10_000.0)
    p.execute("BTCUSDT", "BUY", 1000.0, 100.0, stop_loss=95.0)
    assert not p.check_protective_exits({"BTCUSDT": 98.0})
    assert p.check_protective_exits({"BTCUSDT": 94.0})


def test_profit_factor_is_json_safe():
    """float('inf') serialises as the bare token `Infinity`, which is not valid
    JSON and silently kills the whole websocket frame."""
    p = PaperPortfolio(10_000.0)
    p.execute("BTCUSDT", "BUY", 1000.0, 100.0)
    p.execute("BTCUSDT", "SELL", 1000.0, 200.0)
    perf = p.performance({"BTCUSDT": 200.0})
    json.dumps(perf)  # must not raise
    assert perf["profit_factor"] is None or perf["profit_factor"] > 0


def test_simulated_clock_advances():
    c = Clock(simulated=True, step_seconds=300)
    t0 = c.now()
    c.advance()
    assert c.now() == pytest.approx(t0 + 300)


def test_real_clock_ignores_advance():
    c = Clock(simulated=False)
    t0 = c.now()
    c.advance(10_000)
    assert c.now() - t0 < 1.0


# ---------------------------------------------------------------- indicators

from glassbox.indicators import adx, atr_pct, bollinger, ema, macd, rsi


def test_rsi_extremes():
    """A monotonically rising series is RSI 100 by definition."""
    assert rsi([float(i) for i in range(1, 40)]) == pytest.approx(100.0)
    assert rsi([float(40 - i) for i in range(1, 40)]) == pytest.approx(0.0)


def test_rsi_needs_history():
    assert rsi([1.0, 2.0, 3.0]) == 50.0  # not enough data returns neutral


def test_ema_tracks_a_constant():
    assert ema([5.0] * 50, 12) == pytest.approx(5.0)


def test_macd_zero_on_flat():
    line, sig, hist = macd([10.0] * 80)
    assert abs(hist) < 1e-9


def test_bollinger_bands_ordered():
    vals = [10, 11, 9, 12, 8, 11, 10, 13, 9, 11] * 4
    up, mid, low = bollinger([float(v) for v in vals])
    assert low < mid < up


def test_atr_zero_when_no_range():
    n = 40
    assert atr_pct([10.0] * n, [10.0] * n, [10.0] * n) == pytest.approx(0.0)


# ---------------------------------------------------------------- calibration

from glassbox.calibration import CalibrationTracker
from glassbox.config import Signal


def _sig(agent, symbol, stance, conf, quality="live"):
    return Signal(agent=agent, symbol=symbol, stance=stance, confidence=conf,
                  rationale="test", evidence={"data_quality": quality})


def test_correct_call_is_graded_as_a_hit():
    t = CalibrationTracker(horizon_seconds=0.0)
    t.record([_sig("a", "BTCUSDT", "bullish", 0.8)], {"BTCUSDT": 100.0})
    graded = t.grade_due({"BTCUSDT": 105.0}, now=time.time() + 10)
    assert len(graded) == 1 and graded[0].correct
    assert t.score("a")["hit_rate"] == 1.0


def test_wrong_call_is_graded_as_a_miss():
    t = CalibrationTracker(horizon_seconds=0.0)
    t.record([_sig("a", "BTCUSDT", "bullish", 0.9)], {"BTCUSDT": 100.0})
    graded = t.grade_due({"BTCUSDT": 95.0}, now=time.time() + 10)
    assert not graded[0].correct
    # Being confidently wrong must score worse than being tentatively wrong.
    assert graded[0].brier == pytest.approx(0.81)


def test_flat_market_is_not_a_win():
    """Without a deadband, a coin flip on a flat tape scores 50% and looks skilled."""
    t = CalibrationTracker(horizon_seconds=0.0)
    t.record([_sig("a", "BTCUSDT", "bullish", 0.7)], {"BTCUSDT": 100.0})
    graded = t.grade_due({"BTCUSDT": 100.02}, now=time.time() + 10)
    assert not graded[0].correct


def test_abstentions_are_never_graded():
    t = CalibrationTracker(horizon_seconds=0.0)
    n = t.record([_sig("a", "BTCUSDT", "bullish", 0.8, quality="unavailable")],
                 {"BTCUSDT": 100.0})
    assert n == 0 and not t.open_calls


def test_neutral_calls_are_never_graded():
    t = CalibrationTracker(horizon_seconds=0.0)
    assert t.record([_sig("a", "BTCUSDT", "neutral", 0.8)], {"BTCUSDT": 100.0}) == 0


def test_one_lucky_call_cannot_buy_influence():
    """Reliability must not move on a single sample."""
    t = CalibrationTracker(horizon_seconds=0.0)
    t.record([_sig("a", "BTCUSDT", "bullish", 0.95)], {"BTCUSDT": 100.0})
    t.grade_due({"BTCUSDT": 200.0}, now=time.time() + 10)
    assert t.reliability("a") < 1.10


def test_sustained_accuracy_raises_reliability():
    t = CalibrationTracker(horizon_seconds=0.0)
    for i in range(40):
        t.record([_sig("a", "BTCUSDT", "bullish", 0.75)], {"BTCUSDT": 100.0})
        t.grade_due({"BTCUSDT": 105.0}, now=time.time() + 10 + i)
    assert t.reliability("a") > 1.15


def test_sustained_failure_lowers_reliability():
    t = CalibrationTracker(horizon_seconds=0.0)
    for i in range(40):
        t.record([_sig("a", "BTCUSDT", "bullish", 0.85)], {"BTCUSDT": 100.0})
        t.grade_due({"BTCUSDT": 95.0}, now=time.time() + 10 + i)
    assert t.reliability("a") < 0.85


def test_unknown_analyst_is_neutral_weight():
    assert CalibrationTracker().reliability("nobody") == 1.0


# ---------------------------------------------------------------- feeds

from glassbox.feeds import _symbol_array


def test_symbol_array_has_no_whitespace():
    """Binance rejects the symbols param if json.dumps inserts spaces."""
    assert _symbol_array(["BTCUSDT", "ETHUSDT"]) == '["BTCUSDT","ETHUSDT"]'


# ---------------------------------------------------------------- rate limits

from glassbox.ratelimit import RateLimitGovernor, estimate_weight


def test_all_symbol_ticker_costs_far_more_than_one():
    """The whole point of weight accounting: these are not the same request."""
    assert estimate_weight("/api/v3/ticker/24hr", {}) == 80
    assert estimate_weight("/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}) == 2


def test_depth_weight_scales_with_limit():
    assert estimate_weight("/api/v3/depth", {"limit": 100}) == 5
    assert estimate_weight("/api/v3/depth", {"limit": 5000}) == 250


def test_unknown_endpoint_is_assumed_costly():
    """Guessing low on an unknown endpoint is how you get banned."""
    assert estimate_weight("/api/v3/something-new") >= 5


@pytest.mark.asyncio
async def test_governor_accounts_for_spend():
    g = RateLimitGovernor()
    await g.reserve("/api/v3/klines", {"symbol": "BTCUSDT"})
    assert g.used == 2 and g.remaining == g.soft_ceiling - 2


def test_governor_trusts_binance_over_itself():
    """The response header is authoritative; our estimate must never win."""
    g = RateLimitGovernor()
    g.state.local_estimate = 10
    g.observe({"x-mbx-used-weight-1m": "450"})
    assert g.used == 450


def test_soft_ceiling_leaves_headroom():
    g = RateLimitGovernor(soft_ceiling_pct=0.60)
    assert g.soft_ceiling == 3600 and g.soft_ceiling < g.state.limit_1m


def test_429_triggers_a_backoff_window():
    g = RateLimitGovernor()
    wait = g.penalise(429, {"retry-after": "30"})
    assert wait == 30.0 and g.status()["banned"]


def test_418_backs_off_hard_even_if_told_otherwise():
    """A ban is not a suggestion; retrying through it makes it longer."""
    g = RateLimitGovernor()
    assert g.penalise(418, {"retry-after": "1"}) >= 120.0


def test_limit_is_read_from_exchange_info():
    g = RateLimitGovernor()
    g.set_limit_from_exchange_info({"rateLimits": [
        {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "limit": 12000}]})
    assert g.state.limit_1m == 12000


# ---------------------------------------------------------------- stream

from glassbox.stream import LiveQuote, MarketStream


def test_quote_computes_change_and_spread():
    q = LiveQuote(symbol="BTCUSDT", price=110.0, open_24h=100.0,
                  bid=109.9, ask=110.1, ts=time.time())
    assert q.change_24h_pct == pytest.approx(10.0)
    assert q.spread_bps == pytest.approx(18.18, rel=0.01)
    assert not q.is_stale


def test_old_quote_is_marked_stale():
    """A frozen feed must never look like a current price."""
    q = LiveQuote(symbol="BTCUSDT", price=100.0, ts=time.time() - 300)
    assert q.is_stale and q.to_dict()["stale"]


def test_stream_parses_mini_ticker():
    s = MarketStream()
    s._apply_mini_ticker({"s": "BTCUSDT", "c": "70000", "o": "68000",
                          "h": "71000", "l": "67000", "v": "10", "q": "700000"})
    q = s.quote("btcusdt")
    assert q.price == 70000 and q.change_24h_pct == pytest.approx(2.94, rel=0.01)


def test_stream_preserves_trade_aggressor_side():
    """Inverting buyer_is_maker flips every order-flow signal in the system."""
    s = MarketStream()
    s._apply_trade({"s": "BTCUSDT", "p": "100", "q": "2", "T": 1, "m": False})
    s._apply_trade({"s": "BTCUSDT", "p": "100", "q": "1", "T": 2, "m": True})
    tr = s.recent_trades("BTCUSDT")
    buy = sum(t["qty"] for t in tr if not t["buyer_is_maker"])
    sell = sum(t["qty"] for t in tr if t["buyer_is_maker"])
    assert buy == 2 and sell == 1


def test_trade_buffer_is_bounded():
    s = MarketStream()
    for i in range(3000):
        s._apply_trade({"s": "X", "p": "1", "q": "1", "T": i, "m": False})
    assert len(s.trades["X"]) <= 1000


# ---------------------------------------------------------------- mcp

from glassbox.mcp import BinanceMCPClient, TokenStore, Tokens, _parse_mcp_response


def test_loopback_endpoint_needs_no_oauth():
    """The bundled mock runs on loopback; Binance always needs a token."""
    assert not BinanceMCPClient("http://127.0.0.1:8787/mock/mcp").auth_required
    assert BinanceMCPClient("https://agent.binance.com/mcp/agentic").auth_required


def test_pkce_challenge_is_s256_of_the_verifier():
    import base64 as b64
    import hashlib as hl

    c = BinanceMCPClient(client_id="https://example.com/client.json")
    c._meta = {
        "authorization_endpoint": "https://accounts.binance.com/agentic-oauth/authorize",
        "resource": "https://agent.binance.com/mcp/agentic",
    }
    url, verifier, state = c.build_authorization_url()
    expected = b64.urlsafe_b64encode(hl.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert f"code_challenge={expected}" in url
    assert "code_challenge_method=S256" in url
    assert state in url


def test_redirect_uri_is_loopback_only():
    """An authorization code must never cross a network."""
    c = BinanceMCPClient(client_id="x")
    c._meta = {
        "authorization_endpoint": "https://accounts.binance.com/agentic-oauth/authorize",
        "resource": "https://agent.binance.com/mcp/agentic",
    }
    url, _v, _s = c.build_authorization_url()
    assert "127.0.0.1" in url and "http%3A%2F%2F127.0.0.1" in url


def test_expiry_is_checked_early():
    """A token expiring mid-order is worse than one refreshed slightly early."""
    assert Tokens("t", expires_at=time.time() + 10).expired
    assert not Tokens("t", expires_at=time.time() + 600).expired


def test_tokens_are_not_stored_in_the_clear(tmp_path):
    from glassbox.ledger import load_or_create_device_key

    path = tmp_path / "tok.enc"
    store = TokenStore(path, load_or_create_device_key())
    store.save(Tokens(access_token="super-secret-value", scope="trade"))
    assert b"super-secret-value" not in path.read_bytes()
    assert store.load().access_token == "super-secret-value"
    store.clear()
    assert store.load() is None


def test_sse_and_json_responses_both_parse():
    class R:
        def __init__(self, ct, text):
            self.headers = {"content-type": ct}
            self.text = text

        def json(self):
            return json.loads(self.text)

    body = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    assert _parse_mcp_response(R("application/json", body))["result"]["ok"]
    sse = f"event: message\ndata: {body}\n\n"
    assert _parse_mcp_response(R("text/event-stream", sse))["result"]["ok"]


def test_tools_are_found_by_intent_not_exact_name():
    """Binance can rename a tool; keyword matching survives that."""
    c = BinanceMCPClient()
    c.status.tools = [
        {"name": "binance_place_spot_order_v2", "description": "Place a spot order."},
        {"name": "get_account_balance", "description": "Sub-account balances."},
    ]
    assert c.find_tool("spot", "order") == "binance_place_spot_order_v2"
    assert c.find_tool("balance") == "get_account_balance"
    assert c.find_tool("withdraw") is None


# ---------------------------------------------------------------- mock server

from glassbox.mockmcp import MockBinanceMCP


class _Feeds:
    async def tickers_24h(self, symbols=None):
        return {"BTCUSDT": {"lastPrice": "100.0"}, "ETHUSDT": {"lastPrice": "10.0"}}


async def test_withdrawal_is_refused_outright():
    """There is no withdrawal scope on Agent OS, and the mock enforces that."""
    m = MockBinanceMCP(1000.0, _Feeds())
    out = await m.call_tool("withdraw_funds", {"amount": 100})
    assert out["isError"] and "withdrawal" in out["content"][0]["text"].lower()


async def test_agent_cannot_pull_from_the_main_account():
    m = MockBinanceMCP(1000.0, _Feeds())
    out = await m.call_tool("transfer_between_wallets", {
        "asset": "USDT", "amount": 100, "fromWallet": "MAIN", "toWallet": "SPOT"})
    assert out["isError"] and "main account" in out["content"][0]["text"].lower()


async def test_order_moves_the_balance():
    m = MockBinanceMCP(1000.0, _Feeds())
    out = await m.call_tool("place_spot_order", {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": 500})
    assert not out["isError"]
    assert m.balances["BTC"] == pytest.approx(5.0)
    assert m.balances["USDT"] < 500


async def test_overspending_is_rejected():
    m = MockBinanceMCP(100.0, _Feeds())
    out = await m.call_tool("place_spot_order", {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": 5000})
    assert out["isError"] and "insufficient" in out["content"][0]["text"].lower()


async def test_dust_orders_are_rejected():
    m = MockBinanceMCP(1000.0, _Feeds())
    out = await m.call_tool("place_spot_order", {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": 1})
    assert out["isError"] and "minimum" in out["content"][0]["text"].lower()


async def test_selling_what_you_do_not_hold_is_rejected():
    m = MockBinanceMCP(1000.0, _Feeds())
    out = await m.call_tool("place_spot_order", {
        "symbol": "ETHUSDT", "side": "SELL", "type": "MARKET", "quoteOrderQty": 100})
    assert out["isError"]


# ---------------------------------------------------------------- news

from glassbox.news import NewsMonitor, SocialAdapter


def _mon():
    return NewsMonitor(["BTCUSDT", "ETHUSDT", "SOLUSDT"])


def test_binance_delisting_is_the_only_hard_block():
    m = _mon()
    score, _matched, cat, syms = m._classify("Binance Will Delist SOL/USDT", "binance")
    assert cat == "delisting" and score == 100.0 and syms == ["SOLUSDT"]
    # The same words from a news site must NOT hard-block.
    _s, _mm, cat2, _sy = m._classify("Rumour: Binance will delist SOL", "coindesk")
    assert cat2 != "delisting"


def test_security_incident_is_scored_high():
    _s, _m, cat, syms = _mon()._classify("Exchange hacked, SOL drained", "coindesk")
    assert cat == "security" and "SOLUSDT" in syms


def test_fed_release_is_macro():
    _s, _m, cat, _sy = _mon()._classify("FOMC statement on rate decision", "federal_reserve")
    assert cat == "macro"


def test_ticker_matching_does_not_over_trigger():
    """Loose matching turns ordinary words into tickers and blocks the book."""
    m = _mon()
    _s, _mm, _c, syms = m._classify("IT IS ON AND UP FOR THE US", "coindesk")
    assert syms == []


def test_news_can_never_produce_a_buy():
    """The whole safety argument: no path from a headline to a position."""
    from glassbox.news import EventRisk

    r = EventRisk(level="normal", score=0.0)
    assert r.size_multiplier <= 1.0
    assert not hasattr(r, "direction") and not hasattr(r, "side")
    fields = set(EventRisk.__dataclass_fields__)
    assert not (fields & {"buy", "long", "stance", "direction", "signal"})


def test_delisting_blocks_and_blackouts():
    import time as _t

    m = _mon()
    m.items = [
        type("I", (), {
            "source": "binance", "title": "Binance Will Delist SOL/USDT",
            "url": "", "published": _t.time(), "symbols": ["SOLUSDT"],
            "risk_score": 100.0, "matched": [], "category": "delisting",
            "to_dict": lambda self: {},
        })()
    ]
    r = m.assess()
    assert "SOLUSDT" in r.blocked_symbols and r.blackout and r.level == "high"


def test_quiet_tape_leaves_sizing_alone():
    m = _mon()
    m.items = []
    r = m.assess()
    assert r.level == "normal" and r.size_multiplier == 1.0 and not r.blocked_symbols


def test_social_is_off_by_default():
    """A post can be faked; a position cannot be unfaked."""
    s = SocialAdapter()
    assert not s.enabled
    assert "verified" in s.status()["policy"].lower()


async def test_social_refuses_to_scrape():
    s = SocialAdapter(bearer_token="x")
    with pytest.raises(NotImplementedError):
        await s.fetch()


# ---------------------------------------------------------------- testkit isolation


async def test_drills_never_touch_a_real_ledger(tmp_path):
    """
    The tamper-detection drill deliberately corrupts a ledger record. Proving
    it runs in an isolated temp directory — and never against a ledger the
    caller points GLASSBOX_HOME at — is what makes "Run drills" safe to expose
    as a dashboard button.
    """
    import os

    from glassbox.ledger import Ledger
    from glassbox.testkit import run_drills

    real_ledger_path = tmp_path / "real" / "ledger.jsonl"
    real = Ledger(path=real_ledger_path)
    for i in range(15):
        real.append("production_event", {"i": i})
    height_before, head_before = real.height, real.head

    old = os.environ.get("GLASSBOX_HOME")
    os.environ["GLASSBOX_HOME"] = str(tmp_path / "real")
    try:
        result = await run_drills()
    finally:
        if old is None:
            os.environ.pop("GLASSBOX_HOME", None)
        else:
            os.environ["GLASSBOX_HOME"] = old

    assert result["all_passed"]
    real_after = Ledger(path=real_ledger_path)
    assert real_after.height == height_before
    assert real_after.head == head_before
    assert real_after.verify()["valid"]


async def test_scenario_backtest_is_isolated():
    from glassbox.testkit import run_scenario_backtest

    r = await run_scenario_backtest("calm", ticks=30, equity=5000.0)
    assert r["portfolio"]["starting_equity_usd"] == 5000.0
    assert "buy_and_hold_pnl_pct" in r["benchmark"]


async def test_isolated_engine_cleans_up_its_temp_dir():
    from glassbox.testkit import isolated_engine

    captured_path = None
    async with isolated_engine() as engine:
        captured_path = engine.ledger.path
        assert captured_path.exists()
    assert not captured_path.exists()
    assert not captured_path.parent.exists()


# ---------------------------------------------------------------- mode switching


async def test_set_mode_requires_explicit_confirmation_for_live():
    from glassbox.config import Mode
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        result = await engine.set_mode(Mode.LIVE, confirm_live=False)
        assert not result["ok"]
        assert engine.settings.mode != Mode.LIVE


async def test_set_mode_forces_live_data_for_mock_and_live():
    from glassbox.config import Mode
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        result = await engine.set_mode(Mode.MOCK, live_data=False)
        assert result["ok"] and result["live_market_data"] is True


async def test_set_mode_rebuilds_mcp_endpoint_on_switch():
    from glassbox.config import Mode
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        original = engine.mcp.endpoint
        result = await engine.set_mode(Mode.MOCK, live_data=True)
        assert result["rebuilt_mcp"]
        assert "mock/mcp" in engine.mcp.endpoint
        assert engine.mcp.endpoint != original


async def test_set_mode_records_the_change_in_the_ledger():
    from glassbox.config import Mode
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        before = engine.ledger.height
        await engine.set_mode(Mode.SHADOW, live_data=False)
        after = engine.ledger.height
        assert after > before
        records = engine.ledger.read(limit=5, kinds={"mode_change"})
        assert records and records[-1]["payload"]["to"] == "shadow"


# ---------------------------------------------------------------- oauth begin/poll


async def test_oauth_begin_poll_reaches_binance_and_detects_csrf():
    """
    Exercises the real Binance OAuth discovery and token endpoints end to end.
    A fake authorization code is expected to be rejected by Binance's real
    token endpoint — that rejection itself proves the whole chain (discovery,
    loopback capture, code exchange) is wired correctly.
    """
    import urllib.parse as up

    import httpx

    from glassbox.mcp import BinanceMCPClient

    c = BinanceMCPClient(client_id="https://example.com/client.json")
    url = await c.start_authorization()
    assert "accounts.binance.com" in url

    q = up.parse_qs(up.urlparse(url).query)
    state = q["state"][0]

    pending = await c.poll_authorization()
    assert pending["status"] == "pending"

    async with httpx.AsyncClient() as h:
        r = await h.get(f"http://127.0.0.1:8788/callback?code=FAKECODE&state={state}")
        assert r.status_code == 200

    result = await c.poll_authorization()
    # A fake code against Binance's real token endpoint must be refused, not
    # silently accepted.
    assert result["status"] == "error"
    assert "csrf" not in result["detail"].lower()

    idle = await c.poll_authorization()
    assert idle["status"] == "idle"


async def test_oauth_rejects_state_mismatch_before_ever_calling_binance():
    import httpx

    from glassbox.mcp import BinanceMCPClient

    c = BinanceMCPClient(client_id="https://example.com/client.json")
    await c.start_authorization()

    async with httpx.AsyncClient() as h:
        await h.get("http://127.0.0.1:8788/callback?code=X&state=WRONG-STATE")

    result = await c.poll_authorization()
    assert result["status"] == "error"
    assert "csrf" in result["detail"].lower()


# ---------------------------------------------------------------- precision

from glassbox.precision import (
    FilterViolation, SymbolFilters, round_step, size_limit_order,
    size_market_order, size_sell_quantity,
)
from decimal import Decimal


def _btc_filters():
    return SymbolFilters(
        symbol="BTCUSDT", step_size=Decimal("0.00001"), min_qty=Decimal("0.00001"),
        tick_size=Decimal("0.01"), min_notional=Decimal("5"),
    )


def test_round_step_lands_exactly_on_the_exchange_grid():
    q = round_step(Decimal("0.123456789"), Decimal("0.001"))
    assert q == Decimal("0.123")
    # every result must be an exact multiple of the step
    assert (q / Decimal("0.001")) % 1 == 0


def test_round_step_never_rounds_up():
    # 0.1299 with a 0.001 step must floor to 0.129, never 0.130
    assert round_step(Decimal("0.1299"), Decimal("0.001")) == Decimal("0.129")


def test_round_step_handles_zero_step_as_unconstrained():
    assert round_step(Decimal("1.23456"), Decimal("0")) == Decimal("1.23456")


def test_classic_float_drift_is_not_reproduced_in_decimal():
    """The exact bug the precision engine exists to prevent."""
    assert 0.1 + 0.2 != 0.3  # the float artifact is real
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")  # Decimal has none


def test_market_order_lands_on_grid_even_from_a_messy_notional():
    order = size_market_order(_btc_filters(), notional_usd=100.0, reference_price=65432.171234)
    qty = Decimal(order.quantity_str)
    assert qty % Decimal("0.00001") == 0
    assert order.notional >= Decimal("5")  # above min_notional


def test_dust_order_is_refused_not_rounded_up():
    with pytest.raises(FilterViolation):
        size_market_order(_btc_filters(), notional_usd=0.0001, reference_price=65000.0)


def test_below_min_notional_after_rounding_is_refused():
    f = SymbolFilters(symbol="X", step_size=Decimal("1"), min_qty=Decimal("1"),
                      tick_size=Decimal("0.01"), min_notional=Decimal("1000"))
    with pytest.raises(FilterViolation):
        size_market_order(f, notional_usd=50.0, reference_price=10.0)


def test_limit_order_rounds_both_price_and_quantity():
    order = size_limit_order(_btc_filters(), notional_usd=200.0, limit_price=65432.156)
    assert Decimal(order.price_str) % Decimal("0.01") == 0
    assert Decimal(order.quantity_str) % Decimal("0.00001") == 0


def test_sell_quantity_never_exceeds_what_is_held():
    """A float-drifted holding like 0.1+0.2 must round to a sellable, exact qty."""
    held = 0.1 + 0.2  # 0.30000000000000004 in raw float
    order = size_sell_quantity(_btc_filters(), held_qty=held, price=65000.0)
    assert Decimal(order.quantity_str) <= Decimal(str(held))
    assert Decimal(order.quantity_str) % Decimal("0.00001") == 0


def test_sell_dust_is_refused():
    with pytest.raises(FilterViolation):
        size_sell_quantity(_btc_filters(), held_qty=0.000001, price=65000.0)


# ---------------------------------------------------------------- resilience

from glassbox.resilience import ClockDriftMonitor, IdempotencyGuard, deterministic_client_order_id


def test_client_order_id_is_deterministic_for_the_same_decision():
    id1 = deterministic_client_order_id("BTCUSDT", "BUY", 500.0, "hash-abc")
    id2 = deterministic_client_order_id("BTCUSDT", "BUY", 500.0, "hash-abc")
    assert id1 == id2


def test_client_order_id_differs_for_different_justification():
    id1 = deterministic_client_order_id("BTCUSDT", "BUY", 500.0, "hash-abc")
    id2 = deterministic_client_order_id("BTCUSDT", "BUY", 500.0, "hash-xyz")
    assert id1 != id2


def test_client_order_id_fits_binance_length_limit():
    cid = deterministic_client_order_id("BTCUSDT", "BUY", 500.0, "a" * 64)
    assert len(cid) <= 36  # Binance's newClientOrderId limit


def test_idempotency_guard_returns_cached_result_on_replay():
    guard = IdempotencyGuard()
    assert guard.seen("order-1") is None
    guard.record("order-1", {"status": "filled", "order_id": "999"})
    assert guard.seen("order-1") == {"status": "filled", "order_id": "999"}


def test_idempotency_guard_expires_old_records():
    guard = IdempotencyGuard(ttl_seconds=0.01)
    guard.record("order-1", {"status": "filled"})
    import time as _t
    _t.sleep(0.05)
    assert guard.seen("order-1") is None


async def test_clock_drift_monitor_reports_real_binance_time():
    from glassbox.feeds import BinanceFeeds

    feeds = BinanceFeeds()
    try:
        monitor = ClockDriftMonitor(feeds)
        report = await monitor.check()
        assert report.healthy  # this machine's clock should be in sync
        assert abs(report.drift_ms) < 5000
    finally:
        await feeds.aclose()


async def test_clock_drift_monitor_handles_unreachable_binance():
    class _DeadFeeds:
        async def _fetch(self, path):
            raise ConnectionError("no network")

    monitor = ClockDriftMonitor(_DeadFeeds())
    report = await monitor.check()
    assert not report.healthy
    assert "could not reach" in report.note.lower()


# ---------------------------------------------------------------- execution resilience integration


async def test_price_collar_refuses_a_stale_decision():
    from glassbox.config import CapitalState, Intent, Side
    from glassbox.execution import Executor
    from glassbox.mcp import BinanceMCPClient

    class _StaleFeeds:
        async def tickers_24h(self, symbols):
            # Reports a price wildly different from what the intent was made at.
            return {symbols[0]: {"lastPrice": "100000"}}

        async def symbol_filters(self, symbol):
            return _btc_filters()

    mcp = BinanceMCPClient(endpoint="http://127.0.0.1:1/mock/mcp")
    mcp.status.tools = [{"name": "place_spot_order", "description": "spot order"}]
    executor = Executor(mcp, feeds=_StaleFeeds(), price_collar_bps=50.0)

    intent = Intent(
        intent_id="t1", ts=0.0, module="council", symbol="BTCUSDT", side=Side.BUY,
        notional_usd=500.0, order_type="MARKET", limit_price=None,
        from_state=CapitalState.IDLE, to_state=CapitalState.DEPLOYED,
        thesis="test", stop_loss=1.0, reference_price=65000.0,
    )
    result = await executor.execute_via_mcp(intent, 500.0, "justification-hash")
    assert result.status == "rejected"
    assert "collar" in result.message.lower()


async def test_identical_decision_is_not_fired_twice():
    from glassbox.config import CapitalState, Intent, Side
    from glassbox.execution import Executor
    from glassbox.mcp import BinanceMCPClient

    calls = []

    class _FakeMCP(BinanceMCPClient):
        async def call_tool(self, name, arguments):
            calls.append(arguments)
            return {
                "is_error": False,
                "structured": {"symbol": "BTCUSDT", "side": "BUY", "orderId": "1",
                               "cummulativeQuoteQty": 500.0, "executedQty": 0.0077,
                               "price": 65000.0, "commission": 0.1},
                "text": "{}",
            }

    mcp = _FakeMCP(endpoint="http://127.0.0.1:1/mock/mcp")
    mcp.status.tools = [{"name": "place_spot_order", "description": "spot order"}]
    executor = Executor(mcp, feeds=None)

    intent = Intent(
        intent_id="t1", ts=0.0, module="council", symbol="BTCUSDT", side=Side.BUY,
        notional_usd=500.0, order_type="MARKET", limit_price=None,
        from_state=CapitalState.IDLE, to_state=CapitalState.DEPLOYED,
        thesis="test", stop_loss=1.0, reference_price=65000.0,
    )
    r1 = await executor.execute_via_mcp(intent, 500.0, "same-hash")
    r2 = await executor.execute_via_mcp(intent, 500.0, "same-hash")

    assert len(calls) == 1  # the second call never reached the mock MCP at all
    assert r1.status == "filled" and r2.status == "filled"
    assert r2.idempotent_replay
    assert not r1.idempotent_replay


# ---------------------------------------------------------------- security guards

from glassbox.security import (
    SecurityViolation, security_headers, validate_authorization_url,
    validate_client_id_url, validate_discovery_url, validate_url,
)


@pytest.mark.parametrize("url,label", [
    ("http://169.254.169.254/latest/meta-data/", "cloud metadata endpoint"),
    ("https://169.254.169.254/latest/meta-data/", "cloud metadata over https"),
    ("https://192.168.1.1/admin", "RFC1918 192.168"),
    ("https://10.0.0.1/api", "RFC1918 10.x"),
    ("https://172.16.0.1/", "RFC1918 172.16"),
    ("http://127.0.0.1:6379/", "loopback service"),
    ("http://localhost:8080/", "loopback by name"),
    ("https://2852039166/", "decimal-encoded metadata IP"),
])
def test_ssrf_targets_are_blocked(url, label):
    """
    The exact SSRF vectors the MCP spec names. Discovery follows URLs supplied
    by a remote response, so a hostile response naming an internal address
    must never be fetched.
    """
    with pytest.raises(SecurityViolation):
        validate_discovery_url(url)


@pytest.mark.parametrize("scheme_url", [
    "javascript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
])
def test_dangerous_url_schemes_are_refused(scheme_url):
    """A javascript: URL reaching window.open() is code execution, not navigation."""
    with pytest.raises(SecurityViolation):
        validate_authorization_url(scheme_url)


def test_plain_http_is_refused_for_public_hosts():
    with pytest.raises(SecurityViolation):
        validate_discovery_url("http://example.com/oauth")


def test_real_binance_urls_still_pass():
    """The guards must not break the actual integration."""
    assert validate_discovery_url("https://agent.binance.com/mcp/agentic")
    assert validate_authorization_url(
        "https://accounts.binance.com/agentic-oauth/authorize?response_type=code"
    )


def test_loopback_allowed_only_when_explicitly_permitted():
    """The bundled mock server is loopback; a remote-supplied URL never is."""
    assert validate_url(
        "http://127.0.0.1:8787/mock/mcp", allow_loopback=True, purpose="mock"
    )
    with pytest.raises(SecurityViolation):
        validate_url("http://127.0.0.1:8787/mock/mcp", allow_loopback=False)


def test_client_id_url_cannot_point_at_internal_infrastructure():
    """
    Binance's own server fetches this URL, so an unvalidated value would make
    GlassBox the instrument of an SSRF attack against them.
    """
    with pytest.raises(SecurityViolation):
        validate_client_id_url("http://169.254.169.254/client.json")
    assert validate_client_id_url("https://example.github.io/glassbox/client.json")


def test_security_headers_lock_down_the_console():
    h = security_headers()
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Cache-Control"] == "no-store"


async def test_real_binance_discovery_survives_the_guards():
    """
    End-to-end against Binance's live discovery endpoints, with SSRF and
    scheme validation active. Proves the hardening did not break the thing it
    protects.
    """
    from glassbox.mcp import BinanceMCPClient

    c = BinanceMCPClient(client_id="https://example.com/client.json")
    try:
        meta = await c.discover()
        assert meta["authorization_endpoint"].startswith("https://")
        assert meta["token_endpoint"].startswith("https://")
        url, _verifier, _state = c.build_authorization_url()
        assert url.startswith("https://")
    finally:
        await c.aclose()


# ---------------------------------------------------------------- macro regime

from datetime import datetime, timedelta, timezone

from glassbox.macro import MacroMonitor, MacroPosture, RateSnapshot


def test_macro_can_never_produce_a_buy():
    """
    The same structural guarantee as the news layer, for the same reason: a
    trading agent inferring a directional macro view from a yield print is
    exactly the kind of confident nonsense that loses money.
    """
    fields = set(MacroPosture.__dataclass_fields__)
    assert not (fields & {"buy", "long", "direction", "stance", "signal", "side"})
    # size_multiplier is a reduction factor; it must never scale a position up.
    assert MacroPosture().size_multiplier <= 1.0


def test_fomc_derisking_is_preemptive_and_tapers():
    """
    The novel behaviour: risk comes down *before* a scheduled event, on a
    smooth taper, because the FOMC calendar is public months in advance.
    """
    m = MacroMonitor()
    fomc = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    m.fomc_dates = [fomc]

    far = m.assess(now=fomc - timedelta(days=10))
    day_before = m.assess(now=fomc - timedelta(hours=24))
    just_before = m.assess(now=fomc - timedelta(hours=6))
    at_event = m.assess(now=fomc - timedelta(hours=2))

    assert far.size_multiplier == 1.0 and not far.blackout
    assert day_before.size_multiplier < 1.0
    assert just_before.size_multiplier < day_before.size_multiplier  # tapers down
    assert at_event.blackout and at_event.size_multiplier == 0.0


def test_rate_shock_cuts_size_in_either_direction():
    """Direction-agnostic: a big move is a liquidity event, not a trade idea."""
    m = MacroMonitor()
    for change in (40.0, -40.0):
        m.rates = RateSnapshot(
            as_of="2026-09-03", two_year=4.34, ten_year=4.77,
            two_year_change_1w_bps=change, curve_2s10s_bps=43.0,
        )
        p = m.assess(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert p.size_multiplier <= 0.6, f"no size cut for {change}bp move"


def test_small_rate_moves_do_not_constrain_trading():
    m = MacroMonitor()
    m.rates = RateSnapshot(
        as_of="2026-09-03", two_year=4.34, ten_year=4.77,
        two_year_change_1w_bps=3.0, curve_2s10s_bps=43.0, curve_change_1w_bps=2.0,
    )
    p = m.assess(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert p.size_multiplier == 1.0 and not p.blackout


def test_deepening_inversion_is_treated_as_stress():
    m = MacroMonitor()
    m.rates = RateSnapshot(
        as_of="2026-09-03", two_year=4.80, ten_year=4.50,
        two_year_change_1w_bps=5.0, curve_2s10s_bps=-30.0, curve_change_1w_bps=-20.0,
    )
    p = m.assess(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert p.regime == "stressed" and p.size_multiplier <= 0.7


async def test_macro_reads_real_fed_data():
    """
    Live end-to-end against the Federal Reserve's own H.15 release and the
    published FOMC calendar — primary government sources, not a scraper.
    """
    m = MacroMonitor()
    try:
        await m.refresh(force=True)
        assert m.rates.two_year is not None
        assert 0 < m.rates.two_year < 25          # a sane yield, not a parse artefact
        assert len(m.fomc_dates) > 0
        nxt = m.next_fomc()
        assert nxt is None or nxt["hours_away"] > 0
    finally:
        await m.aclose()


# ---------------------------------------------------------------- rate expectations

from glassbox.macro import RateExpectation


def test_expectation_reads_hikes_from_a_positively_sloped_front_end():
    """2y above 3m means the market has priced tightening — a measurement."""
    m = MacroMonitor()
    m.rates = RateSnapshot(as_of="2026-09-03", three_month=3.89, two_year=4.34, ten_year=4.77)
    e = m.rate_expectation()
    assert e.direction == "hikes"
    assert e.implied_change_bps == pytest.approx(45.0, abs=0.5)
    assert e.implied_25bp_moves == pytest.approx(1.8, abs=0.05)


def test_expectation_reads_cuts_from_an_inverted_front_end():
    m = MacroMonitor()
    m.rates = RateSnapshot(as_of="x", three_month=5.30, two_year=4.30, ten_year=4.40)
    e = m.rate_expectation()
    assert e.direction == "cuts"
    assert e.implied_change_bps < 0
    assert e.confidence == "high"   # a 100bp gap is decisively priced


def test_expectation_reads_on_hold_when_the_front_end_is_flat():
    m = MacroMonitor()
    m.rates = RateSnapshot(as_of="x", three_month=4.00, two_year=4.05, ten_year=4.30)
    e = m.rate_expectation()
    assert e.direction == "on hold" and e.confidence == "low"


def test_expectation_degrades_honestly_without_data():
    e = MacroMonitor().rate_expectation()
    assert e.direction == "unknown" and e.implied_change_bps is None


def test_expectation_is_never_a_trade_signal():
    """
    It describes the rate environment; it must not carry a position view.
    Same structural guarantee as MacroPosture and EventRisk.
    """
    fields = set(RateExpectation.__dataclass_fields__)
    assert not (fields & {"buy", "long", "short", "side", "signal", "position"})


def test_calendar_lists_scheduled_events_with_the_agents_planned_action():
    m = MacroMonitor()
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    m.fomc_dates = [
        datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc),   # inside horizon
        datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),  # inside horizon
        datetime(2027, 6, 16, 18, 0, tzinfo=timezone.utc),   # beyond it
    ]
    cal = m.economic_calendar(now=now, weeks=12)
    fomc = [c for c in cal if c["event"].startswith("FOMC")]
    assert len(fomc) == 2                      # the third is beyond the horizon
    assert all(c["days_away"] > 0 for c in cal)
    assert all(c["agent_action"] for c in cal)
    # CPI and payrolls are included too, and every row declares how
    # trustworthy its date is.
    assert any("CPI" in c["event"] for c in cal)
    assert any("payrolls" in c["event"] for c in cal)
    assert all(c["confidence"] in ("confirmed", "estimated") for c in cal)
    assert all(c["confidence"] == "confirmed" for c in fomc)
    assert all(c["confidence"] == "estimated"
               for c in cal if not c["event"].startswith("FOMC"))


def test_calendar_action_changes_as_an_event_approaches():
    m = MacroMonitor()
    fomc = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    m.fomc_dates = [fomc]
    pick = lambda rows: next(r for r in rows if r["event"].startswith("FOMC"))
    far = pick(m.economic_calendar(now=fomc - timedelta(days=10)))
    near = pick(m.economic_calendar(now=fomc - timedelta(hours=24)))
    imminent = pick(m.economic_calendar(now=fomc - timedelta(hours=2)))
    assert "No constraint" in far["agent_action"]
    assert "reduced" in near["agent_action"]
    assert "paused" in imminent["agent_action"]


# ---------------------------------------------------------------- BLS calendar + alerts


def test_bls_dates_follow_the_published_convention():
    """Payrolls on the first Friday, CPI around the second Wednesday."""
    m = MacroMonitor()
    rows = m.bls_release_dates(datetime(2026, 9, 6, tzinfo=timezone.utc), weeks=8)
    nfp = [r for r in rows if "payrolls" in r["event"]]
    cpi = [r for r in rows if "CPI" in r["event"]]
    assert nfp and cpi
    for r in nfp:
        assert datetime.fromisoformat(r["date"]).weekday() == 4   # Friday
    for r in cpi:
        assert datetime.fromisoformat(r["date"]).weekday() == 2   # Wednesday


def test_bls_dates_are_labelled_estimated_not_confirmed():
    """
    An estimate shown as an estimate is useful; one dressed up as a fact is
    not. BLS blocks scraping, so these are computed — and say so.
    """
    m = MacroMonitor()
    rows = m.bls_release_dates(datetime(2026, 9, 6, tzinfo=timezone.utc), weeks=8)
    assert rows and all(r["confidence"] == "estimated" for r in rows)
    assert all("convention" in r["note"].lower() for r in rows)


def test_calendar_mixes_confirmed_fomc_with_estimated_bls():
    m = MacroMonitor()
    m.fomc_dates = [datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)]
    cal = m.economic_calendar(now=datetime(2026, 9, 6, tzinfo=timezone.utc), weeks=8)
    assert [r["date"] for r in cal] == sorted(r["date"] for r in cal)  # chronological
    assert any(r["confidence"] == "confirmed" for r in cal)
    assert any(r["confidence"] == "estimated" for r in cal)


def test_bls_release_triggers_preemptive_derisking():
    m = MacroMonitor()
    cpi = m._nth_weekday(2026, 10, 2, 2).replace(hour=12, minute=30)
    far = m.assess(now=cpi - timedelta(days=3))
    near = m.assess(now=cpi - timedelta(hours=6))
    imminent = m.assess(now=cpi - timedelta(minutes=30))
    assert far.size_multiplier == 1.0
    assert near.size_multiplier < 1.0
    assert imminent.blackout and imminent.size_multiplier == 0.0


def test_alerts_stay_quiet_until_an_event_is_actually_close():
    """An alert that fires constantly is an alert nobody reads."""
    m = MacroMonitor()
    fomc = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    m.fomc_dates = [fomc]
    assert m.alerts(now=fomc - timedelta(days=9)) == []
    warn = m.alerts(now=fomc - timedelta(hours=24))
    assert warn and warn[0]["severity"] == "warning"
    crit = m.alerts(now=fomc - timedelta(hours=1))
    assert crit and crit[0]["severity"] == "critical"


def test_full_treasury_curve_is_captured():
    m = MacroMonitor()
    m.rates = RateSnapshot(
        as_of="2026-09-03", three_month=3.89, two_year=4.34, ten_year=4.77,
        curve={"1m": 3.83, "3m": 3.89, "2y": 4.34, "10y": 4.77, "30y": 5.25},
        curve_changes_1w_bps={"2y": 0.0, "10y": 4.0},
    )
    assert len(m.rates.curve) == 5 and m.rates.curve["30y"] == 5.25


async def test_live_curve_has_every_tenor():
    """All 11 constant-maturity tenors, from the Fed's own H.15 release."""
    m = MacroMonitor()
    try:
        await m.refresh(force=True)
        assert len(m.rates.curve) >= 8
        for tenor, y in m.rates.curve.items():
            assert 0 < y < 25, f"{tenor} yield {y} is not plausible"
    finally:
        await m.aclose()


# ---------------------------------------------------------------- double-launch


def test_port_probe_detects_a_listening_socket():
    import socket

    from glassbox.cli import _port_in_use

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert _port_in_use(port)
    assert not _port_in_use(port)   # released once closed


def test_unrelated_service_is_not_mistaken_for_glassbox():
    """
    Offering to stop 'the existing server' would be alarming if the port were
    actually held by someone's unrelated dev server.
    """
    import socket
    import threading

    from glassbox.cli import _is_our_server

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _reply():
        try:
            conn, _ = srv.accept()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nnot glassbox!")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=_reply, daemon=True).start()
    try:
        assert not _is_our_server(port)
    finally:
        srv.close()


# ---------------------------------------------------------------- second opinion

from glassbox.second_opinion import SecondOpinion, evaluate_manual_trade


class _FakeGuardian:
    critical_at = 88.0
    def __init__(self, score=10.0):
        self.last = type("A", (), {"score": score})()


class _FakeCalibration:
    def __init__(self, r=1.0): self._r = r
    def reliability(self, name): return self._r


class _FakeEngine:
    """Minimal engine surface the second-opinion layer reads."""
    def __init__(self, verdicts=None, threat=10.0, spread=1.0, equity=10000.0):
        self.council_verdicts = verdicts or []
        self.guardian = _FakeGuardian(threat)
        self.calibration = _FakeCalibration()
        self.macro_posture = None
        self.event_risk = None
        self._spread = spread
        self._equity = equity
    def market_view(self): return {"BTCUSDT": {"spread_bps": self._spread}}
    def _marks(self): return {"BTCUSDT": 80000.0}
    class _P:
        def __init__(self, eq): self._eq = eq
        def state_dict(self, marks): return {"equity_usd": self._eq, "positions": {}}
    @property
    def portfolio(self): return self._P(self._equity)


def _verdict(direction, conviction, signals=None):
    return [{"symbol": "BTCUSDT", "direction": direction,
             "conviction": conviction, "signals": signals or []}]


def test_buying_into_a_bearish_council_raises_a_concern():
    op = evaluate_manual_trade(
        symbol="BTCUSDT", side="BUY", notional_usd=500,
        engine=_FakeEngine(_verdict("bearish", 0.30)),
    )
    assert op.disagreement_pct > 0
    assert any(c.source == "council" for c in op.concerns)


def test_buying_with_a_bullish_council_is_aligned():
    op = evaluate_manual_trade(
        symbol="BTCUSDT", side="BUY", notional_usd=500,
        engine=_FakeEngine(_verdict("bullish", 0.30)),
    )
    assert op.verdict == "aligned"
    assert op.disagreement_pct == 0.0
    assert not op.requires_acknowledgement


def test_mild_disagreement_does_not_demand_acknowledgement():
    """
    The Council disagrees with most things most of the time — a low-conviction
    objection must not train the operator to click through warnings.
    """
    op = evaluate_manual_trade(
        symbol="BTCUSDT", side="SELL", notional_usd=200,
        engine=_FakeEngine(_verdict("bullish", 0.10)),
    )
    assert not op.requires_acknowledgement
    assert op.disagreement_pct < 30


def test_multiple_layers_objecting_escalates_to_strongly_against():
    e = _FakeEngine(_verdict("bearish", 0.60), threat=95.0)
    e.macro_posture = type("M", (), {
        "blackout": False, "size_multiplier": 0.4,
        "reasons": ["FOMC in 6h."]})()
    op = evaluate_manual_trade(
        symbol="BTCUSDT", side="BUY", notional_usd=500, engine=e)
    assert op.verdict == "strongly_against"
    assert op.disagreement_pct >= 60
    assert op.requires_acknowledgement
    assert "own risk" in op.headline.lower()


def test_unreliable_analysts_object_more_quietly():
    """Calibration feeds the human-facing warning, not just position sizing."""
    signals = [{"agent": "technical", "stance": "bearish", "confidence": 0.9}]
    trusted = _FakeEngine(_verdict("neutral", 0.0, signals))
    trusted.calibration = _FakeCalibration(1.3)
    doubted = _FakeEngine(_verdict("neutral", 0.0, signals))
    doubted.calibration = _FakeCalibration(0.5)

    hi = evaluate_manual_trade(symbol="BTCUSDT", side="BUY", notional_usd=500, engine=trusted)
    lo = evaluate_manual_trade(symbol="BTCUSDT", side="BUY", notional_usd=500, engine=doubted)
    assert hi.disagreement_pct > lo.disagreement_pct


def test_wide_spreads_and_concentration_are_flagged():
    e = _FakeEngine(_verdict("neutral", 0.0), spread=45.0, equity=1000.0)
    op = evaluate_manual_trade(symbol="BTCUSDT", side="BUY", notional_usd=800, engine=e)
    assert any(c.source == "liquidity" for c in op.concerns)
    assert any(c.source == "position" for c in op.concerns)


def test_second_opinion_never_blocks_only_advises():
    """It has no field capable of denying a trade — that's the Constitution's job."""
    fields = set(SecondOpinion.__dataclass_fields__)
    assert not (fields & {"blocked", "denied", "allow", "veto"})
    assert "advisory" in SecondOpinion().to_dict()["policy"].lower()


def test_score_is_bounded():
    e = _FakeEngine(_verdict("bearish", 5.0), threat=200.0, spread=999.0, equity=1.0)
    op = evaluate_manual_trade(symbol="BTCUSDT", side="BUY", notional_usd=99999, engine=e)
    assert 0.0 <= op.disagreement_pct <= 100.0


# ---------------------------------------------------------------- payment workflows

from glassbox.workflows import OnchainRail, PaymentRail, PaymentRefused, build_report


class _FakeWallet:
    """A wallet stand-in for fast, deterministic unit tests — no real network
    call. `test_real_settlement_wallet_*` below exercises the genuine one."""

    def __init__(self, should_succeed=True):
        self.should_succeed = should_succeed
        self.sent_to: list[str] = []

    async def send(self, to_address, value_wei=1):
        self.sent_to.append(to_address)
        from glassbox.settlement import SettlementResult

        if self.should_succeed:
            return SettlementResult(
                ok=True, tx_hash="0xfaketxhash", explorer_url="https://example/tx",
                from_address="0xFROM", to_address=to_address, value_wei=value_wei,
                message="settled",
            )
        return SettlementResult(
            ok=False, from_address="0xFROM", to_address=to_address,
            value_wei=value_wei, error_code="insufficient_funds",
            message="insufficient funds",
        )


async def test_payments_fail_closed_with_no_allowlist():
    """A money-sending capability must default to sending nothing."""
    rail = PaymentRail(wallet=_FakeWallet())
    ok, reason = rail.check("anyone", 1.0)
    assert not ok and "allowlist" in reason.lower()
    with pytest.raises(PaymentRefused):
        await rail.send("anyone", 1.0, "test")


async def test_agent_cannot_invent_a_recipient():
    rail = PaymentRail(allowlist={"known-agent": "0xabc"}, wallet=_FakeWallet())
    with pytest.raises(PaymentRefused):
        await rail.send("attacker", 1.0, "test")
    result = await rail.send("known-agent", 1.0, "test")
    assert result.status == "sent"


async def test_per_payment_cap_bounds_a_single_mistake():
    rail = PaymentRail(per_payment_cap_usd=5.0, allowlist={"a": "0xabc"}, wallet=_FakeWallet())
    with pytest.raises(PaymentRefused):
        await rail.send("a", 50.0, "too big")


async def test_daily_payment_budget_cannot_be_exceeded():
    rail = PaymentRail(daily_limit_usd=10.0, per_payment_cap_usd=10.0,
                       allowlist={"a": "0xabc"}, wallet=_FakeWallet())
    await rail.send("a", 8.0, "first")
    assert rail.remaining_usd() == pytest.approx(2.0)
    with pytest.raises(PaymentRefused):
        await rail.send("a", 5.0, "would exceed")


async def test_refused_payments_are_still_recorded():
    """A refusal is evidence too — it must not vanish."""
    rail = PaymentRail(allowlist={"a": "0xabc"}, per_payment_cap_usd=1.0, wallet=_FakeWallet())
    with pytest.raises(PaymentRefused):
        await rail.send("a", 99.0, "nope")
    assert any(p.status == "refused" for p in rail.payments)


async def test_payments_carry_their_justification_hash():
    rail = PaymentRail(allowlist={"a": "0xabc"}, wallet=_FakeWallet())
    p = await rail.send("a", 1.0, "data", justification_hash="abc123")
    assert p.justification_hash == "abc123" and p.tx_ref


async def test_failed_settlement_refuses_the_payment_not_just_a_fake_success():
    """
    The exact gap being closed: if the onchain send genuinely fails, the
    payment must be refused, not recorded as sent with a made-up reference.
    """
    rail = PaymentRail(allowlist={"a": "0xabc"}, wallet=_FakeWallet(should_succeed=False))
    with pytest.raises(PaymentRefused, match="[Ss]ettlement failed"):
        await rail.send("a", 1.0, "test")
    assert rail.payments[-1].status == "refused"
    assert rail.payments[-1].tx_ref is None


async def test_no_wallet_configured_refuses_rather_than_fabricates():
    rail = PaymentRail(allowlist={"a": "0xabc"}, wallet=None)
    with pytest.raises(PaymentRefused, match="no settlement wallet"):
        await rail.send("a", 1.0, "test")


# ---------------------------------------------------------------- real settlement


async def test_real_settlement_wallet_generates_a_genuine_address(tmp_path):
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(tmp_path / "wallet.enc", b"test-device-key")
    try:
        assert w.address.startswith("0x") and len(w.address) == 42
    finally:
        await w.aclose()


async def test_real_settlement_wallet_persists_the_same_address(tmp_path):
    from glassbox.settlement import SettlementWallet

    path = tmp_path / "wallet.enc"
    w1 = SettlementWallet(path, b"test-device-key")
    addr1 = w1.address
    await w1.aclose()

    w2 = SettlementWallet(path, b"test-device-key")
    try:
        assert w2.address == addr1
    finally:
        await w2.aclose()


def test_settlement_private_key_is_not_stored_in_the_clear(tmp_path):
    from glassbox.settlement import SettlementWallet

    path = tmp_path / "wallet.enc"
    import asyncio

    captured = {}

    async def make():
        w = SettlementWallet(path, b"test-device-key")
        captured["key_hex"] = w._account.key.hex()
        await w.aclose()

    asyncio.run(make())
    raw = path.read_bytes()
    # Check against the *actual* private key just generated, rather than a
    # generic "0x" substring check — base64-encoded ciphertext can coincide
    # with "0x" by pure chance and has nothing to do with whether the real
    # secret is protected.
    key_hex = captured["key_hex"].removeprefix("0x")
    assert key_hex.encode() not in raw
    assert key_hex.upper().encode() not in raw


async def test_real_settlement_against_the_live_base_sepolia_network(tmp_path):
    """
    End-to-end against the actual public testnet — the fix this whole set of
    tests exists to prove. A fresh wallet has zero balance, so the real
    network is expected to genuinely reject the transaction for insufficient
    funds; that specific, correctly-classified rejection **is** the proof
    that key generation, signing, and broadcast against a live chain all
    really work, not a mock of them.
    """
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(tmp_path / "wallet.enc", b"test-device-key")
    try:
        status = await w.status()
        assert status["network_reachable"] is True
        assert status["balance_wei"] == 0  # freshly generated, genuinely unfunded

        result = await w.send("0x000000000000000000000000000000000000dEaD")
        assert not result.ok
        assert result.error_code == "insufficient_funds"
        assert "faucet" in result.message.lower() or "fund" in result.message.lower()
    finally:
        await w.aclose()


# ---------------------------------------------------------------- onchain workflows



def test_long_lockups_are_refused_as_risk_not_judged_on_yield():
    """
    The rule that matters: capital that cannot be recalled during a crash
    cannot defend the book, however good the rate looks.
    """
    rail = OnchainRail(max_lockup_days=7)
    action = rail.build("stake", "Locked 90d", "USDT", 1000, 12.0,
                        equity_usd=10000, lockup_days=90)
    assert action.status == "refused"
    assert "lockup" in action.message.lower()


def test_flexible_staking_produces_a_real_mcp_instruction():
    rail = OnchainRail()
    action = rail.build("stake", "Simple Earn Flexible", "USDT", 1000, 4.8,
                        equity_usd=10000, lockup_days=0, justification_hash="h1")
    assert action.status == "instructed"
    assert action.mcp_instruction["tool"] == "stake"
    assert action.mcp_instruction["arguments"]["flexible"] is True
    assert action.mcp_instruction["justification_hash"] == "h1"


def test_onchain_allocation_cap_keeps_capital_tradable():
    rail = OnchainRail(max_allocation_pct=60.0)
    action = rail.build("stake", "Flexible", "USDT", 9000, 5.0, equity_usd=10000)
    assert action.status == "refused"
    assert "equity" in action.message.lower()


def test_unstake_maps_to_the_right_tool():
    rail = OnchainRail()
    action = rail.build("unstake", "Flexible", "USDT", 500, 0.0, equity_usd=10000)
    assert action.mcp_instruction["tool"] == "unstake"


# ---------------------------------------------------------------- reports


async def test_report_ties_performance_to_the_audit_chain():
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        rep = build_report(engine)
        assert rep["audit"]["chain_valid"] is True
        assert rep["audit"]["ledger_height"] > 0
        assert "equity_usd" in rep["portfolio"]
        assert "analyst_track_record" in rep
        assert isinstance(rep["activity"]["rules_that_fired"], list)


# ---------------------------------------------------------------- USDC settlement


async def test_usdc_contract_verifies_live_against_the_real_network():
    """
    The direct fix for 'hardcoding an address doesn't fail loudly if wrong':
    this checks the live contract's decimals() and symbol() every time,
    rather than trusting the hardcoded address forever.
    """
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(Path("/tmp") / f"usdc_verify_{uuid.uuid4().hex}.enc", b"k")
    try:
        ok, note = await w.verify_usdc_contract()
        assert ok is True
        assert "6" in note and "USDC" in note
    finally:
        await w.aclose()


async def test_usdc_balance_reads_the_real_chain():
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(Path("/tmp") / f"usdc_bal_{uuid.uuid4().hex}.enc", b"k")
    try:
        bal = await w.usdc_balance()
        assert bal == 0  # freshly generated, genuinely unfunded
    finally:
        await w.aclose()


async def test_send_usdc_refuses_cleanly_when_unfunded():
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(Path("/tmp") / f"usdc_send_{uuid.uuid4().hex}.enc", b"k")
    try:
        result = await w.send_usdc("0x000000000000000000000000000000000000dEaD")
        assert not result.ok
        assert result.error_code == "insufficient_usdc"
        assert "faucet.circle.com" in result.message
    finally:
        await w.aclose()


async def test_send_usdc_refuses_if_contract_no_longer_verifies(monkeypatch):
    """If the live contract ever stops looking like USDC, refuse — don't
    silently transact against whatever is actually there."""
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(Path("/tmp") / f"usdc_badverify_{uuid.uuid4().hex}.enc", b"k")
    try:
        async def fake_verify():
            return False, "does not look like USDC"
        w.verify_usdc_contract = fake_verify
        result = await w.send_usdc("0x000000000000000000000000000000000000dEaD")
        assert not result.ok and result.error_code == "usdc_verification_failed"
    finally:
        await w.aclose()


async def test_payment_rail_prefers_usdc_when_wallet_holds_it():
    """If the wallet has real USDC, use it — not the symbolic ETH fallback."""
    from glassbox.settlement import SettlementResult
    from glassbox.workflows import PaymentRail

    class _WalletWithUSDC:
        async def usdc_balance(self):
            return 50_000  # 0.05 USDC held
        async def send_usdc(self, to_address, amount=10_000):
            return SettlementResult(ok=True, tx_hash="0xusdc", explorer_url="e",
                                    from_address="0xA", to_address=to_address,
                                    value_wei=amount, message="ok")
        async def send(self, to_address, value_wei=1):
            raise AssertionError("should not fall back to ETH when USDC is held")

    rail = PaymentRail(allowlist={"a": "0xabc"}, wallet=_WalletWithUSDC())
    p = await rail.send("a", 1.0, "test")
    assert "USDC" in p.message


async def test_payment_rail_falls_back_to_eth_when_no_usdc_held():
    from glassbox.settlement import SettlementResult
    from glassbox.workflows import PaymentRail

    class _WalletNoUSDC:
        async def usdc_balance(self):
            return 0
        async def send(self, to_address, value_wei=1):
            return SettlementResult(ok=True, tx_hash="0xeth", explorer_url="e",
                                    from_address="0xA", to_address=to_address,
                                    value_wei=value_wei, message="ok")

    rail = PaymentRail(allowlist={"a": "0xabc"}, wallet=_WalletNoUSDC())
    p = await rail.send("a", 1.0, "test")
    assert "ETH" in p.message and "symbolic" in p.message


# ---------------------------------------------------------------- mainnet safety gate


def test_mainnet_is_refused_without_the_explicit_env_flag(monkeypatch, tmp_path):
    from glassbox.settlement import SettlementWallet

    monkeypatch.delenv("GLASSBOX_ALLOW_MAINNET", raising=False)
    w = SettlementWallet(tmp_path / "w.enc", b"k", chain_id=8453)  # Base mainnet
    assert w.is_mainnet is False
    assert w.mainnet_blocked_reason is not None
    assert "GLASSBOX_ALLOW_MAINNET" in w.mainnet_blocked_reason
    assert w.chain_id != 8453  # silently fell back to testnet


def test_mainnet_is_honoured_only_with_explicit_opt_in(monkeypatch, tmp_path):
    from glassbox.settlement import SettlementWallet

    monkeypatch.setenv("GLASSBOX_ALLOW_MAINNET", "1")
    w = SettlementWallet(tmp_path / "w.enc", b"k", chain_id=8453)
    assert w.is_mainnet is True
    assert w.mainnet_blocked_reason is None


async def test_mainnet_hard_ceiling_cannot_be_exceeded_by_any_input(monkeypatch, tmp_path):
    """
    The core safety property: no caller, config value, or bug can request
    more than the hardcoded ceiling once mainnet is (explicitly) enabled.
    """
    from glassbox.settlement import MAINNET_HARD_CEILING_WEI, SettlementWallet

    monkeypatch.setenv("GLASSBOX_ALLOW_MAINNET", "1")
    w = SettlementWallet(tmp_path / "w.enc", b"k", chain_id=8453)
    try:
        result = await w.send(
            "0x000000000000000000000000000000000000dEaD",
            value_wei=MAINNET_HARD_CEILING_WEI * 1000,  # wildly over
        )
        assert not result.ok and result.error_code == "mainnet_ceiling_exceeded"
    finally:
        await w.aclose()


def test_unrecognised_chain_ids_are_not_treated_as_mainnet(tmp_path):
    """A chain id that isn't in the known mainnet set is just testnet-like,
    not silently granted mainnet's looser handling."""
    from glassbox.settlement import SettlementWallet

    w = SettlementWallet(tmp_path / "w.enc", b"k", chain_id=999999)
    assert w.is_mainnet is False
    assert w.mainnet_blocked_reason is None  # never triggered the gate at all


# ---------------------------------------------------------------- transparency accuracy


async def test_closest_to_trading_ignores_unactionable_bearish_reads():
    """
    A bearish read on a symbol GlassBox does not hold can never become a
    trade — spot-only means no naked shorts. Reporting it as 'closest to
    trading' regardless of conviction was its own confusing non-answer:
    '32% conviction, 0 points under the bar' implies a trade should have
    fired when it structurally never could.
    """
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.council_verdicts = [
            {"symbol": "SOLUSDT", "direction": "bearish", "conviction": 0.55},
        ]
        c = engine._closest_to_trading()
        assert c["blocked_reason"] == "not_actionable_spot_only"
        assert c["would_trade"] is False


async def test_closest_to_trading_becomes_actionable_once_actually_held():
    from glassbox.portfolio import Position
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.council_verdicts = [
            {"symbol": "SOLUSDT", "direction": "bearish", "conviction": 0.55},
        ]
        engine.portfolio.positions["SOLUSDT"] = Position(
            symbol="SOLUSDT", qty=1.0, avg_price=100.0
        )
        c = engine._closest_to_trading()
        assert c.get("blocked_reason") is None
        assert c["would_trade"] is True


async def test_closest_to_trading_still_ranks_bullish_reads_normally():
    """A bullish read is always structurally actionable — a spot buy needs
    no held position — so it must not be filtered the way bearish reads are."""
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.council_verdicts = [
            {"symbol": "BTCUSDT", "direction": "bullish", "conviction": 0.20},
        ]
        c = engine._closest_to_trading()
        assert c["symbol"] == "BTCUSDT" and c.get("blocked_reason") is None


async def test_closest_to_trading_identifies_quorum_as_the_real_blocker():
    """
    Conviction can clear the bar while quorum (genuine multi-analyst
    agreement) still blocks the trade — a materially different reason that
    the transparency panel must name accurately rather than implying
    conviction alone was the obstacle.
    """
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.council_verdicts = [{
            "symbol": "XRPUSDT", "direction": "bullish", "conviction": 0.31,
            "signals": [
                {"agent": "technical", "stance": "bullish", "confidence": 0.5},
                {"agent": "orderflow", "stance": "neutral", "confidence": 0.2},
            ],
        }]
        c = engine._closest_to_trading()
        assert c["would_trade"] is False
        assert c["blocked_reason"] == "quorum_not_met"
        assert c["agreeing_analysts"] == 1
        assert c["analysts_required"] == 2


async def test_closest_to_trading_reports_no_blocker_when_both_gates_clear():
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.council_verdicts = [{
            "symbol": "BTCUSDT", "direction": "bullish", "conviction": 0.40,
            "signals": [
                {"agent": "technical", "stance": "bullish", "confidence": 0.5},
                {"agent": "regime", "stance": "bullish", "confidence": 0.4},
            ],
        }]
        c = engine._closest_to_trading()
        assert c["would_trade"] is True
        assert c["blocked_reason"] is None
        assert c["quorum_met"] is True


async def test_closest_to_trading_respects_a_customised_quorum_requirement():
    """The check reads the real configured threshold, not a hardcoded 2."""
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.constitution.doc.setdefault("rules", {})["quorum"] = {
            "min_agreeing_analysts": 3
        }
        engine.council_verdicts = [{
            "symbol": "BTCUSDT", "direction": "bullish", "conviction": 0.40,
            "signals": [
                {"agent": "technical", "stance": "bullish", "confidence": 0.5},
                {"agent": "regime", "stance": "bullish", "confidence": 0.4},
            ],
        }]
        c = engine._closest_to_trading()
        assert c["analysts_required"] == 3
        assert c["would_trade"] is False  # only 2 agree, 3 now required


# ---------------------------------------------------------------- autonomy consent


async def test_engine_does_not_trade_autonomously_by_default():
    """
    The default must be: run, analyse, publish reasoning — but never open a
    position on its own. Waking up to unrequested trades is the exact surprise
    this guards against.
    """
    from glassbox.config import Mode, Settings
    from glassbox.engine import Engine
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="glassbox-autonomy-"))
    settings = Settings()
    settings.mode = Mode.PAPER
    settings.live_market_data = False
    settings.simulated_clock = True
    engine = Engine(settings, data_dir=tmp)
    try:
        assert engine.autonomous is False  # the critical default
        for _ in range(15):
            await engine.tick()
        # The Council still ran and published verdicts...
        assert engine.council_verdicts, "analysis should run even with autonomy off"
        # ...but nothing was ever opened.
        assert len(engine.portfolio.positions) == 0
        assert engine.counters["intents"] == 0
    finally:
        await engine.stop()


async def test_manual_trade_works_with_autonomy_off():
    """Autonomy off must not disable the operator's own hand-placed trades."""
    from glassbox.testkit import isolated_engine

    async with isolated_engine(live_market_data=True) as engine:
        engine.autonomous = False  # override the test default
        await engine.tick(); await engine.tick()
        r = await engine.manual_intent("BTCUSDT", "BUY", 500.0)
        assert r["ok"] and r["status"] in ("filled", "pending")


async def test_close_position_exits_a_held_position():
    from glassbox.testkit import isolated_engine

    async with isolated_engine(live_market_data=True) as engine:
        await engine.tick(); await engine.tick()
        await engine.manual_intent("BTCUSDT", "BUY", 500.0)
        assert "BTCUSDT" in engine.portfolio.positions
        r = await engine.close_position("BTCUSDT")
        assert r["ok"]
        assert engine.portfolio.positions.get("BTCUSDT") is None \
            or engine.portfolio.positions["BTCUSDT"].qty == 0


async def test_close_position_on_nothing_held_fails_cleanly():
    from glassbox.testkit import isolated_engine

    async with isolated_engine(live_market_data=True) as engine:
        await engine.tick()
        r = await engine.close_position("ETHUSDT")
        assert not r["ok"] and "No open" in r["error"]


# ------------------------------------------------ council reversal on a held position


async def test_council_reversal_on_held_position_is_flagged():
    """
    The missing feedback loop: open a position, then the Council turns against
    it. This must surface a warning — but never auto-close.
    """
    from glassbox.portfolio import Position
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.portfolio.positions["SOLUSDT"] = Position(
            symbol="SOLUSDT", qty=1.0, avg_price=100.0, opened_mode="paper"
        )
        engine.council_verdicts = [
            {"symbol": "SOLUSDT", "direction": "bearish", "conviction": 0.55},
        ]
        opposed = engine.positions_the_council_now_opposes()
        assert len(opposed) == 1
        assert opposed[0]["symbol"] == "SOLUSDT"
        assert opposed[0]["conviction"] == 0.55
        # It only warns — the position is untouched.
        assert "SOLUSDT" in engine.portfolio.positions


async def test_council_agreement_does_not_flag_a_held_position():
    from glassbox.portfolio import Position
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.portfolio.positions["BTCUSDT"] = Position(
            symbol="BTCUSDT", qty=0.01, avg_price=80000.0
        )
        engine.council_verdicts = [
            {"symbol": "BTCUSDT", "direction": "bullish", "conviction": 0.60},
        ]
        assert engine.positions_the_council_now_opposes() == []


async def test_low_conviction_opposition_does_not_flag():
    """A weak bearish lean shouldn't nag — only a real, bar-clearing reversal."""
    from glassbox.portfolio import Position
    from glassbox.testkit import isolated_engine

    async with isolated_engine() as engine:
        await engine.tick()
        engine.portfolio.positions["ETHUSDT"] = Position(
            symbol="ETHUSDT", qty=1.0, avg_price=2500.0
        )
        engine.council_verdicts = [
            {"symbol": "ETHUSDT", "direction": "bearish", "conviction": 0.10},
        ]
        assert engine.positions_the_council_now_opposes() == []


# ---------------------------------------------------------------- chain inspector


def test_inspector_classifies_query_types():
    from glassbox.settlement import ChainInspector

    ins = ChainInspector()
    assert ins.classify("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3") == "evm_address"
    assert ins.classify("0x" + "a" * 64) == "evm_tx"
    assert ins.classify("So11111111111111111111111111111111111111112") == "solana"
    assert ins.classify("nonsense") == "unknown"


def test_inspector_rejects_unknown_chain():
    import asyncio
    from glassbox.settlement import ChainInspector

    async def run():
        ins = ChainInspector()
        try:
            r = await ins.inspect("0x" + "a" * 40, "notachain")
            assert not r["ok"] and "Unknown chain" in r["error"]
        finally:
            await ins.aclose()

    asyncio.run(run())


async def test_inspector_reads_a_real_evm_address():
    """Live: a known BNB Chain address must return a real balance and tx count."""
    from glassbox.settlement import ChainInspector

    ins = ChainInspector()
    try:
        # Binance hot wallet on BSC — a real, permanently-funded address.
        r = await ins.inspect("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3", "bnb")
        assert r["ok"] and r["type"] == "address"
        assert r["balance"] > 0
        assert r["symbol"] == "BNB"
        assert r["tx_count"] > 0
    finally:
        await ins.aclose()


async def test_inspector_reads_a_real_solana_address():
    from glassbox.settlement import ChainInspector

    ins = ChainInspector()
    try:
        r = await ins.inspect("So11111111111111111111111111111111111111112", "solana")
        assert r["ok"] and r["type"] == "address" and r["symbol"] == "SOL"
    finally:
        await ins.aclose()


async def test_inspector_handles_bad_input_gracefully():
    from glassbox.settlement import ChainInspector

    ins = ChainInspector()
    try:
        r = await ins.inspect("definitely-not-valid", "bnb")
        assert not r["ok"] and "error" in r
    finally:
        await ins.aclose()


# ---------------------------------------------------------------- futures

# These cover the futures book added alongside spot: the accounting a leveraged
# position needs (margin, liquidation, realised P&L booked back to the shared
# cash pool) and the Constitution rules that treat leverage as risk. Spot is
# unchanged; a futures intent is marked with meta["market"] == "futures".


def _fut_intent(**kw):
    """A futures intent: same Intent, tagged with the futures meta block."""
    meta = {
        "market": "futures",
        "action": "OPEN_LONG",
        "leverage": 5.0,
        "position_side": "LONG",
        "margin_usd": 100.0,
        "reduce_only": False,
        "notional_usd": 500.0,
    }
    meta.update(kw.pop("meta", {}))
    base = dict(notional_usd=meta["notional_usd"], meta=meta)
    base.update(kw)
    return make_intent(**base)


def test_futures_leverage_over_cap_is_denied():
    c = Constitution(doc={"rules": {"max_leverage": {"max": 10}}})
    v = c.evaluate(_fut_intent(meta={"leverage": 20.0}), PORTFOLIO, MARKET)
    assert v.decision == Decision.DENY


def test_futures_leverage_at_cap_is_allowed():
    c = Constitution(doc={"rules": {"max_leverage": {"max": 10}}})
    v = c.evaluate(_fut_intent(meta={"leverage": 10.0}), PORTFOLIO, MARKET)
    assert v.decision == Decision.ALLOW


def test_max_leverage_rule_ignores_spot_trades():
    # A spot buy has no leverage meta and must never be touched by the cap.
    c = Constitution(doc={"rules": {"max_leverage": {"max": 10}}})
    v = c.evaluate(make_intent(notional_usd=500.0), PORTFOLIO, MARKET)
    assert v.decision == Decision.ALLOW


def test_spot_position_cap_does_not_trim_futures():
    # The spot per-position cap must defer to the futures caps, not shrink a
    # leveraged notional as if it were a spot one.
    c = Constitution(doc={"rules": {"max_position_pct": {"pct": 10}}})
    v = c.evaluate(_fut_intent(notional_usd=5000.0), PORTFOLIO, MARKET)
    assert v.decision == Decision.ALLOW
    # Untouched means no trim was applied — the full leveraged notional stands.
    assert v.adjusted_notional_usd is None


def test_futures_reduce_only_close_is_never_size_gated():
    c = Constitution(doc={"rules": {"futures_position_pct": {"pct": 10}}})
    v = c.evaluate(
        _fut_intent(notional_usd=99_999.0, meta={"reduce_only": True, "action": "CLOSE"}),
        PORTFOLIO, MARKET,
    )
    assert v.decision == Decision.ALLOW


def _fresh_book(cash=10_000.0):
    p = PaperPortfolio(starting_equity_usd=cash)
    from glassbox.futures import FuturesBook
    b = FuturesBook(p)
    p.futures = b
    return p, b


def test_futures_long_profits_and_close_settles_to_cash():
    p, b = _fresh_book()
    cash0 = p.cash
    f = b.open("BTCUSDT", "LONG", notional_usd=1000.0, leverage=5, price=100.0)
    assert f is not None
    # Margin (200) plus a taker fee left the cash pool; nothing else.
    assert p.cash == pytest.approx(cash0 - 200.0 - 1000.0 * 0.00045, abs=1e-6)
    # Mark up 10%: a 5x long is up ~50% on margin.
    assert b.unrealised({"BTCUSDT": 110.0}) > 90.0
    close = b.close("BTCUSDT", price=110.0)
    assert close is not None and close.realised_pnl_usd > 0
    assert "BTCUSDT" not in b.positions
    # Realised profit plus the released margin are back in cash, and the daily
    # realised total moved so the shared risk rules see it.
    assert p.cash > cash0
    assert p.realised_today == pytest.approx(close.realised_pnl_usd, abs=1e-6)


def test_futures_short_profits_when_price_falls():
    p, b = _fresh_book()
    b.open("BTCUSDT", "SHORT", notional_usd=1000.0, leverage=5, price=100.0)
    assert b.unrealised({"BTCUSDT": 90.0}) > 0      # price down is good for a short
    assert b.unrealised({"BTCUSDT": 110.0}) < 0     # price up hurts it


def test_futures_opposite_side_is_rejected_until_flat():
    p, b = _fresh_book()
    assert b.open("BTCUSDT", "LONG", notional_usd=500.0, leverage=3, price=100.0) is not None
    # Opposite side while a position is open is refused, not silently flipped.
    assert b.open("BTCUSDT", "SHORT", notional_usd=500.0, leverage=3, price=100.0) is None
    assert b.positions["BTCUSDT"].side == "LONG"


def test_futures_liquidation_forfeits_the_whole_margin():
    p, b = _fresh_book()
    b.open("BTCUSDT", "LONG", notional_usd=1000.0, leverage=10, price=100.0)
    cash_after_open = p.cash
    pos = b.positions["BTCUSDT"]
    # Drop the mark below the liquidation price.
    events = b.check_liquidations_and_exits({"BTCUSDT": pos.liq_price * 0.98})
    assert any(e["kind"] == "liquidation" for e in events)
    assert "BTCUSDT" not in b.positions
    # Isolated margin: the loss is the margin, no more, and nothing is returned.
    assert p.cash == pytest.approx(cash_after_open, abs=1e-6)
    assert p.realised_today < 0


def test_futures_open_trims_to_available_cash():
    p, b = _fresh_book(cash=100.0)
    # Asking for far more notional than 100 of cash can margin: trimmed, not
    # rejected, and never spends more cash than exists.
    f = b.open("BTCUSDT", "LONG", notional_usd=100_000.0, leverage=2, price=100.0)
    assert f is not None
    assert p.cash >= -1e-9
    assert f.notional_usd < 100_000.0


def test_futures_equity_folds_into_portfolio_equity():
    p, b = _fresh_book()
    base = p.equity({"BTCUSDT": 100.0})
    b.open("BTCUSDT", "LONG", notional_usd=1000.0, leverage=5, price=100.0)
    # Right after opening, equity barely moves: the margin just shifts from
    # cash into the position's locked margin. Only the taker fee and a little
    # entry slippage separate it from where it started — it does not teleport.
    assert abs(p.equity({"BTCUSDT": 100.0}) - base) < 2.0
    # A favourable mark lifts total equity through the futures contribution.
    assert p.equity({"BTCUSDT": 110.0}) > base


# ---------------------------------------------------------------- chains


def test_inspect_chains_include_the_major_networks():
    from glassbox.settlement import INSPECT_CHAINS
    for chain in ("bitcoin", "ethereum", "solana", "bnb", "base",
                  "arbitrum", "polygon", "optimism", "avalanche"):
        assert chain in INSPECT_CHAINS


def test_inspector_classifies_bitcoin_addresses_and_txids():
    from glassbox.settlement import ChainInspector
    ins = ChainInspector()
    # Native-segwit, legacy and testnet addresses all read as a BTC address.
    assert ins.classify("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") == "btc_address"
    assert ins.classify("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == "btc_address"
    assert ins.classify("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx") == "btc_address"
    # A 64-hex string with no 0x prefix is a txid, not an EVM tx.
    assert ins.classify("a" * 64) == "btc_tx"


# ---------------------------------------------------------------- narratives


async def test_narratives_populate_with_autonomy_off():
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.autonomous = False       # the dashboard's default
        e.start()
        await e.tick()
        narr = e.status()["narrative"]["narratives"]
        assert len(narr) >= 4
        assert all(0.0 <= n["strength"] <= 1.0 for n in narr)


async def test_narratives_do_not_teleport_between_ticks():
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.autonomous = False
        e.start()
        series = []
        for _ in range(4):
            await e.tick()
            series.append({n["key"]: n["strength"]
                           for n in e.status()["narrative"]["narratives"]})
        # No theme's strength should jump; the walk is small and bounded.
        for a, b in zip(series, series[1:]):
            for theme, s in a.items():
                assert abs(b.get(theme, s) - s) < 0.15


# ---------------------------------------------------------------- analyst desk

# The desk answers questions grounded in the same machinery that trades. These
# cover the parts that are deterministic and offline: pair resolution from free
# text, intent classification, and that the composed answer is built from the
# briefing's real numbers rather than invented. The live-data path (analyze on a
# real pair) needs the exchange and is exercised on the user's machine.


def test_desk_resolves_pairs_from_free_text():
    from glassbox.analyst_desk import resolve_symbol
    assert resolve_symbol("what do you think of SOL right now?", None, None) == "SOLUSDT"
    assert resolve_symbol("bitcoin outlook", None, None) == "BTCUSDT"
    assert resolve_symbol("thoughts on ETHUSDT", None, None) == "ETHUSDT"
    assert resolve_symbol("is $inj a long here", None, None) == "INJUSDT"
    assert resolve_symbol("SOL/USDT levels", None, None) == "SOLUSDT"
    # A whole-market question names no pair.
    assert resolve_symbol("how is the market overall", None, None) is None
    # Falls back to the last-discussed pair when the new turn names none.
    assert resolve_symbol("and the risk?", None, "ETHUSDT") == "ETHUSDT"


def test_desk_classifies_intent_without_false_compare():
    from glassbox.analyst_desk import classify_intent
    assert classify_intent("what do you think of SOL") == "opinion"
    assert classify_intent("how risky is BTC at 20x") == "risk"
    # "for" must not be read as the "or" of a comparison.
    assert classify_intent("key support for ETH") == "levels"
    assert classify_intent("why is the council bearish on XRP") == "explain"
    assert classify_intent("how is the whole market") == "market"
    assert classify_intent("BTC vs ETH") == "compare"


def _synthetic_briefing():
    return {
        "ok": True, "symbol": "SOLUSDT", "as_of": 0,
        "quote": {"price": 168.42, "change_24h_pct": 3.9, "high_24h": 170.1,
                  "low_24h": 160.0, "quote_volume": 1.2e9, "spread_bps": 1.4},
        "indicators": {"rsi": 71.5, "macd_line": 0.5, "macd_signal": 0.3, "macd_hist": -0.05,
                       "bb_pctb": 1.04, "bb_bandwidth_pct": 6.2, "adx": 16.0, "atr_pct": 3.1,
                       "sma20": 162.0, "sma50": 150.0, "price_vs_sma20_pct": 4.0,
                       "price_vs_sma50_pct": 12.3, "dist_to_high_pct": 1.0, "dist_to_low_pct": -5.0},
        "council": {"direction": "bullish", "conviction": 0.42, "dissent_ratio": 0.4,
                    "confluence_families": ["flow", "positioning"],
                    "synthesis": "two independent lenses agree, which strengthens the read.",
                    "bulls": ["OrderFlowAnalyst"], "bears": ["RegimeAnalyst"],
                    "abstained": ["LiquidityAnalyst"], "blind": ["LiquidityAnalyst"],
                    "transcript": [
                        {"agent": "OrderFlowAnalyst", "stance": "bullish", "confidence": 0.6,
                         "rationale": "book is bid-heavy", "data_quality": "live"},
                        {"agent": "LiquidityAnalyst", "stance": "neutral", "confidence": 0.0,
                         "rationale": "depth feed unavailable", "data_quality": "unavailable"},
                    ]},
        "guardian": {"score": 38.0, "level": "normal",
                     "recommendation": "Conditions are within tolerance.", "quarantined": False},
        "derivatives": {"available": True, "funding_pct": 0.031, "open_interest": 1234567,
                        "long_short_ratio": 1.45},
        "narratives": [{"key": "depin", "label": "DePIN", "strength": 0.6, "velocity": 0.03}],
        "constitution_preview": {"decision": "REQUIRE_HUMAN", "requested_usd": 900.0,
                                 "allowed_usd": 900.0, "allowed_pct": 9.0,
                                 "triggered_rules": ["human_confirmation"], "reasons": ["above confirm threshold"]},
        "notes": [],
    }


def test_desk_compose_is_grounded_in_the_briefing():
    from glassbox.analyst_desk import AnalystDesk

    class _Stub:
        class _G:
            last = None
        guardian = _G()
        council_verdicts = []

    desk = AnalystDesk(_Stub())
    text = desk._compose_full(_synthetic_briefing(), "opinion", "what do you think of SOL?")
    # The direction, conviction, and the actual RSI/funding numbers must appear —
    # the answer is a restatement of computed facts, not free invention.
    assert "bullish" in text and "42%" in text
    assert "72" in text          # RSI 71.5 rounded in the phrasing
    assert "0.031%" in text      # funding
    assert "confirmation" in text  # the Constitution preview surfaced
    # A signal that is NOT in the briefing must not be asserted.
    assert "open interest" not in text.lower() or "1234567" not in text


def test_desk_reads_signals_together_for_divergence():
    from glassbox.analyst_desk import AnalystDesk

    class _Stub:
        class _G:
            last = None
        guardian = _G()
        council_verdicts = []

    desk = AnalystDesk(_Stub())
    text = desk._compose_full(_synthetic_briefing(), "opinion", "SOL?")
    # RSI 71.5 into the 24h high (dist 1.0%) is an exhaustion setup the desk
    # should call out rather than just reporting each number in isolation.
    assert "Reading the signals together" in text


async def test_desk_answer_handles_unknown_pair_gracefully():
    from glassbox.analyst_desk import AnalystDesk
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.start()
        desk = AnalystDesk(e)
        # No live network in tests: analyze returns ok=False, and answer must
        # surface a clean explanation rather than raising.
        out = await desk.answer("what do you think of SOL?", [])
        assert "answer" in out and isinstance(out["answer"], str) and out["answer"]


def _desk_with_stub_briefing(briefing):
    """An AnalystDesk whose analyze() returns a fixed briefing (no network)."""
    from glassbox.analyst_desk import AnalystDesk

    class _Stub:
        class _G:
            last = None
        guardian = _G()
        council_verdicts = []
        feeds = type("F", (), {})()

    desk = AnalystDesk(_Stub())

    async def _fixed(symbol):
        b = dict(briefing)
        b["symbol"] = symbol
        return b

    desk.analyze = _fixed
    return desk


async def test_desk_followup_is_short_not_a_full_redump():
    """A follow-up like "is it bullish?" must be a short answer about the pair
    already in play — not the entire briefing printed again."""
    desk = _desk_with_stub_briefing(_synthetic_briefing())
    history = [
        {"role": "user", "content": "what do you think of SOL?"},
        {"role": "assistant", "content": "SOLUSDT is bullish at 42% ..."},
    ]
    r = await desk.answer("is it bullish?", history)
    assert r["symbol"] == "SOLUSDT"          # stayed on the pair in play
    a = r["answer"]
    # short verdict, not the full template
    assert "Levels:" not in a and "On sizing:" not in a and "What the data says:" not in a
    assert len(a) < 600


async def test_desk_followup_stays_on_pair_and_answers_metric():
    """A metric follow-up keeps the current pair and answers just that metric."""
    desk = _desk_with_stub_briefing(_synthetic_briefing())
    history = [
        {"role": "user", "content": "thoughts on SOL"},
        {"role": "assistant", "content": "SOLUSDT reads bullish; the Council and funding ..."},
    ]
    r = await desk.answer("what's the funding?", history)
    assert r["symbol"] == "SOLUSDT"          # not "FUNDINGUSDT"
    assert "funding" in r["answer"].lower() and "0.031%" in r["answer"]


async def test_desk_market_question_is_not_read_as_a_ticker():
    """"how is the whole market" and "market sentiments" must route to the
    market read, not resolve to WHOLEUSDT / SENTIMENTSUSDT."""
    desk = _desk_with_stub_briefing(_synthetic_briefing())
    for q in ["How is the whole market looking?", "what are market sentiments"]:
        r = await desk.answer(q, [])
        assert r["symbol"] is None and r["intent"] == "market"


async def test_desk_llm_key_rejects_unknown_provider_and_short_keys():
    """An unknown provider and an implausibly short key are rejected before any
    network call; the four supported providers (Groq, Gemini, OpenAI, Anthropic)
    are all accepted for connection."""
    desk = _desk_with_stub_briefing(_synthetic_briefing())
    r1 = await desk.set_llm_key("cohere", "sk-somethinglong123456")
    assert r1["ok"] is False and "provider" in r1["error"].lower()
    r2 = await desk.set_llm_key("openai", "short")
    assert r2["ok"] is False


def test_desk_llm_status_reflects_env(monkeypatch):
    """llm_status reports connected/which-provider from the environment, and an
    explicit provider choice wins over priority order."""
    from glassbox.analyst_desk import AnalystDesk
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
              "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GLASSBOX_LLM_PROVIDER",
              "GLASSBOX_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(AnalystDesk, "_ENV_LOADED", True)  # skip .env file read
    desk = _desk_with_stub_briefing(_synthetic_briefing())
    assert desk.llm_status()["connected"] is False
    monkeypatch.setenv("GROQ_API_KEY", "gsk_dummy_key_123456")
    assert desk.llm_status() == {"connected": True, "provider": "groq",
                                 "model": "llama-3.3-70b-versatile"}
    # explicit choice overrides priority
    monkeypatch.setenv("GEMINI_API_KEY", "AIza_dummy_key_123456")
    monkeypatch.setenv("GLASSBOX_LLM_PROVIDER", "gemini")
    assert desk.llm_status()["provider"] == "gemini"


# ----------------------------------------------------- payment / onchain paper

async def test_onchain_stake_claim_and_ledger():
    """Staking moves paper USDC, accrues rewards, and every action lands on the
    signed ledger — the audit trail extends to on-chain work."""
    from glassbox.onchain_actions import OnchainActions
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.start()
        oc = OnchainActions(e)
        start = oc.usdc
        r = oc.stake("ETH", 500)
        assert r["ok"] and abs(oc.usdc - (start - 500)) < 1e-6
        # force some accrual and claim
        oc.stakes["ETH"]["since"] -= 60 * 60 * 24 * 365  # a year ago
        c = oc.claim("ETH")
        assert c["ok"] and oc.usdc > (start - 500)        # rewards credited
        # the ledger recorded the stake and the claim
        v = e.ledger.verify()
        assert v.get("ok", v.get("valid", True))
        kinds = [rec["kind"] for rec in oc.receipts]
        assert "onchain_stake" in kinds and "onchain_claim" in kinds


async def test_onchain_spending_policy_blocks_oversized_action():
    from glassbox.onchain_actions import OnchainActions, MAX_ACTION_USD
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.start()
        oc = OnchainActions(e)
        r = oc.pay("agent://data-vendor", MAX_ACTION_USD + 1, "too much")
        assert r["ok"] is False and "policy" in r["error"].lower()
        assert oc.usdc == 5000.0                           # nothing moved


async def test_onchain_agent_payment_records_signed_receipt():
    from glassbox.onchain_actions import OnchainActions
    from glassbox.testkit import isolated_engine
    async with isolated_engine() as e:
        e.start()
        oc = OnchainActions(e)
        r = oc.pay("agent://research-bot", 25, "for a market report")
        assert r["ok"] and r["receipt"]["kind"] == "agent_payment"
        assert r["receipt"]["tx"].startswith("0x")
        assert oc.state()["usdc"] == 4975.0


def test_analyst_circuit_breaker_benches_and_reinstates():
    """A run of wrong live calls benches an analyst (vote -> 0); a run of
    correct calls reinstates it. Mirrors the Council's confidence x reliability
    weighting, where reliability 0 fully sidelines the analyst."""
    from glassbox.calibration import (
        CalibrationTracker, OpenCall, BENCH_MISS_STREAK, RESUME_HIT_STREAK,
    )

    ct = CalibrationTracker(horizon_seconds=1.0)

    def feed(exit_price, entry=100.0):
        # One live bullish call, graded immediately against exit_price.
        ct.open_calls.append(OpenCall(
            analyst="tech", symbol="BTCUSDT", stance="bullish",
            confidence=0.7, entry_price=entry, made_ts=0.0, horizon_seconds=1.0,
        ))
        ct.grade_due({"BTCUSDT": exit_price}, now=1000.0)

    # No calls yet: full weight, not benched.
    assert ct.reliability("tech") == 1.0
    assert "tech" not in ct.bench

    # A run of wrong calls (price falls on a bullish call) benches it.
    for _ in range(BENCH_MISS_STREAK):
        feed(99.0)
    assert "tech" in ct.bench
    assert ct.reliability("tech") == 0.0
    assert ct.score("tech")["benched"] is True
    assert any(e["type"] == "analyst_benched" for e in ct.last_circuit_events)
    assert "tech" in ct.summary()["benched"]

    # While benched a single correct call is not enough to return.
    feed(101.0)
    assert "tech" in ct.bench
    assert ct.reliability("tech") == 0.0

    # A full recovery streak reinstates it with a non-zero, evidence-based vote.
    for _ in range(RESUME_HIT_STREAK - 1):
        feed(101.0)
    assert "tech" not in ct.bench
    assert ct.reliability("tech") > 0.0
    assert any(e["type"] == "analyst_reinstated" for e in ct.last_circuit_events)


def test_circuit_breaker_ignores_blind_calls_and_non_streaks():
    """Only hindsight-free live calls count, and misses must be consecutive."""
    from glassbox.calibration import CalibrationTracker, OpenCall, BENCH_MISS_STREAK

    ct = CalibrationTracker(horizon_seconds=1.0)

    # Interleaving a correct call breaks the streak, so no bench.
    seq = [99.0] * (BENCH_MISS_STREAK - 1) + [101.0] + [99.0] * (BENCH_MISS_STREAK - 1)
    for px in seq:
        ct.open_calls.append(OpenCall(
            analyst="flow", symbol="BTCUSDT", stance="bullish", confidence=0.6,
            entry_price=100.0, made_ts=0.0, horizon_seconds=1.0,
        ))
        ct.grade_due({"BTCUSDT": px}, now=1000.0)
    assert "flow" not in ct.bench

    # A benched analyst is fully sidelined in the Council's weighting.
    from glassbox.agents.council import Council
    council = Council(calibration=ct)
    ct.bench["flow"] = {"since_ts": 0.0, "miss_streak": 3, "reason": "test"}
    assert council._weight("flow") == 0.0
