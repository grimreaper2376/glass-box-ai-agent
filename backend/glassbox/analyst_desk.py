"""
GlassBox — the Analyst Desk.

A conversational analyst that answers questions about any Binance pair, grounded
in exactly the same machinery that trades: the five-analyst Council on live order
books and trade tape, the technical indicators, the Guardian's threat model, the
narrative scout, and the Constitution itself. It is deliberately *not* a language
model paraphrasing a prompt. Every sentence it produces is backed by a number it
just computed from real data — a stretched RSI is a stretched RSI, funding that
longs are paying is a real rate, and a size the Constitution would trim is the
size it would actually trim. The audit story that the rest of GlassBox depends on
would be worth nothing if the desk that explains it were free to invent.

Two things sit on top of this engine:

* an on-demand read for *any* pair, so the Council is no longer limited to the
  handful of symbols the engine trades — ask about a coin and the same five
  analysts examine it live;
* a chat surface for discussing a pair, which resolves the symbol from the
  question, gathers the briefing, and composes an answer that leads with a view
  and defends it with the evidence.

An optional language-model narration layer can restate the briefing more fluidly
when an ``ANTHROPIC_API_KEY`` is present in the environment. It is strictly
opt-in, is instructed to use only the facts it is given, and falls back to the
grounded composer on any error, so the default behaviour never depends on it.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .indicators import adx, atr_pct, bollinger, macd, rsi


# A small alias table so a human can say "bitcoin" or "$sol" and mean the pair.
# Anything not here is treated as a ticker stem and quoted in USDT.
_ALIASES = {
    "bitcoin": "BTC", "btc": "BTC", "xbt": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "binance coin": "BNB", "binancecoin": "BNB", "bnb": "BNB",
    "ripple": "XRP", "xrp": "XRP",
    "dogecoin": "DOGE", "doge": "DOGE",
    "cardano": "ADA", "ada": "ADA",
    "avalanche": "AVAX", "avax": "AVAX",
    "polkadot": "DOT", "dot": "DOT",
    "chainlink": "LINK", "link": "LINK",
    "polygon": "MATIC", "matic": "MATIC", "pol": "POL",
    "litecoin": "LTC", "ltc": "LTC",
    "tron": "TRX", "trx": "TRX",
    "shiba": "SHIB", "shiba inu": "SHIB", "shib": "SHIB",
    "toncoin": "TON", "ton": "TON",
    "sui": "SUI", "aptos": "APT", "apt": "APT",
    "arbitrum": "ARB", "arb": "ARB", "optimism": "OP",
    "injective": "INJ", "inj": "INJ", "sei": "SEI",
    "near": "NEAR", "pepe": "PEPE", "wif": "WIF", "bonk": "BONK",
    "uniswap": "UNI", "uni": "UNI", "aave": "AAVE",
    "render": "RENDER", "fetch": "FET", "fet": "FET",
}

_QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB")


def resolve_symbol(text: str, known: set[str] | None = None,
                   fallback: str | None = None) -> str | None:
    """
    Pull a trading pair out of free text. Handles an explicit pair (BTCUSDT,
    ``SOL/USDT``), a bare ticker (``sol``, ``$sol``), and a spoken name
    (``bitcoin``, ``solana``). Returns an uppercase pair or the fallback.
    """
    if not text:
        return fallback
    raw = text.strip()

    # 1. An explicit pair already ending in a known quote asset.
    for m in re.findall(r"[A-Za-z]{2,15}[/\-]?(?:USDT|USDC|FDUSD|BUSD|TUSD)", raw, re.I):
        pair = m.replace("/", "").replace("-", "").upper()
        if known is None or pair in known:
            return pair

    # 2. Spoken names, longest first so "binance coin" beats "coin".
    low = " " + raw.lower() + " "
    for name in sorted(_ALIASES, key=len, reverse=True):
        if re.search(r"[^a-z]" + re.escape(name) + r"[^a-z]", low):
            pair = _ALIASES[name] + "USDT"
            if known is None or pair in known:
                return pair

    # 3. A $-tagged ticker is an explicit signal — accept it in any case.
    m = re.search(r"\$([A-Za-z]{2,10})\b", raw)
    if m:
        stem = _ALIASES.get(m.group(1).lower(), m.group(1).upper())
        pair = stem + "USDT"
        if known is None or pair in known:
            return pair

    # 4. A bare word is a coin ONLY if it is a known alias, or is written in
    #    ALL-CAPS the way people actually type tickers. Ordinary lower-case words
    #    ("how", "ok", "tell", "why", "market") are never treated as coins — this
    #    is an allowlist, not a blocklist, so new noise words can't leak through.
    _CAPS_NOT_TICKER = {
        "RSI", "MACD", "ADX", "ATR", "OI", "USD", "USDT", "LSR", "PNL", "ROI",
        "API", "CEO", "CPI", "FOMC", "ETF", "AI", "OK", "USA", "UK", "EU", "US",
        "FAQ", "DYOR", "HODL", "ATH", "ATL", "TP", "SL", "LOL", "IMO", "TBH",
        "IDK", "YOLO", "FUD", "NFA", "LFG", "GM", "OG", "PS", "FYI",
    }
    for tok in re.findall(r"\b([A-Za-z]{2,10})\b", raw):
        low = tok.lower()
        if low in _ALIASES:
            pair = _ALIASES[low] + "USDT"
            if known is None or pair in known:
                return pair
        if tok.isupper() and 2 <= len(tok) <= 6 and tok not in _CAPS_NOT_TICKER:
            pair = tok + "USDT"
            if known is None or pair in known:
                return pair

    return fallback


def classify_intent(text: str) -> str:
    """Coarse intent so the answer can lead with the right thing."""
    t = " " + (text or "").lower() + " "
    # Levels / risk / explain are checked before comparison, because a word like
    # "for" must never be mistaken for the "or" in a comparison.
    if any(w in t for w in ("level", "support", "resistance", "entry", "target",
                            "take profit", "stop ", "where do i", "where should",
                            "where to")):
        return "levels"
    if any(w in t for w in ("risk", "risky", "safe", "danger", "downside",
                            "liquidat", "how much can i lose", "wipe", "blow up")):
        return "risk"
    if any(w in t for w in ("why", "explain", "reason", "how come", "what makes",
                            "walk me through")):
        return "explain"
    if any(w in t for w in ("market", "everything", "overall", "how are things",
                            "how is it looking", "whole book", "portfolio", "book right now")):
        return "market"
    # Comparison only on real comparison words, matched at word boundaries.
    if re.search(r"\b(vs|versus|compare|better than|which is better)\b", t):
        return "compare"
    return "opinion"


# --------------------------------------------------------------------------- #
#  small numeric helpers
# --------------------------------------------------------------------------- #

def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    w = values[-period:]
    return sum(w) / len(w)


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100 if b else 0.0


class AnalystDesk:
    """Answers questions and produces on-demand reads, grounded in real data."""

    CACHE_TTL_SECONDS = 25.0

    def __init__(self, engine):
        self.engine = engine
        self._cache: dict[str, tuple[float, dict]] = {}
        self._models_cache: dict[str, tuple[float, list]] = {}

    # -- public: on-demand analysis of any pair ---------------------------- #

    async def analyze(self, symbol: str) -> dict:
        """
        Run the full analyst stack on one pair using real public market data,
        and assemble a briefing. Cached briefly so repeated questions about the
        same pair don't re-hit the exchange. Never raises: on a data failure it
        returns a briefing with ``ok`` False and an explanation.
        """
        symbol = (symbol or "").upper().strip()
        if not symbol:
            return {"ok": False, "symbol": symbol, "error": "No symbol given."}

        hit = self._cache.get(symbol)
        if hit and (time.time() - hit[0]) < self.CACHE_TTL_SECONDS:
            return hit[1]

        briefing = await self._build_briefing(symbol)
        self._cache[symbol] = (time.time(), briefing)
        return briefing

    async def _build_briefing(self, symbol: str) -> dict:
        eng = self.engine
        notes: list[str] = []
        b: dict[str, Any] = {
            "ok": True, "symbol": symbol, "as_of": time.time(),
            "quote": None, "indicators": None, "council": None,
            "guardian": None, "derivatives": {"available": False},
            "narratives": [], "constitution_preview": None, "notes": notes,
        }

        # 1. Real market data for this one pair. build() is public Binance REST
        #    and works whether or not the engine is trading on live data; the
        #    only requirement is network reach to the exchange.
        ctx = None
        try:
            equity_peek = eng.portfolio.equity(eng._marks() or {})
            ctx = await eng.data.build(
                [symbol],
                intended_notional_usd=max(equity_peek * 0.09, 25.0),
                want_derivatives=True,
            )
        except Exception as exc:  # noqa: BLE001 — a lookup tool explains, never crashes
            notes.append(f"Live data for {symbol} could not be fetched ({type(exc).__name__}).")
            b["ok"] = False
            b["error"] = (
                f"Couldn't reach live market data for {symbol}. Check the pair "
                f"exists on Binance and that the machine has network access."
            )
            return b

        price = ctx.price(symbol) if ctx else 0.0
        if not price or price <= 0:
            b["ok"] = False
            b["error"] = (
                f"No live price for {symbol}. It may not be a Binance spot pair — "
                f"try the full pair name, e.g. {symbol if symbol.endswith('USDT') else symbol + 'USDT'}."
            )
            return b

        # 2. Quote.
        tk = ctx.tickers.get(symbol, {}) or {}
        depth = ctx.depth.get(symbol) or {}
        spread_bps = None
        try:
            bids = depth.get("bids") or []
            asks = depth.get("asks") or []
            if bids and asks:
                bid = float(bids[0][0]); ask = float(asks[0][0])
                if bid > 0 and ask > 0:
                    spread_bps = (ask - bid) / ((ask + bid) / 2) * 10000
        except Exception:  # noqa: BLE001
            spread_bps = None
        b["quote"] = {
            "price": price,
            "change_24h_pct": _flt(tk.get("priceChangePercent")),
            "high_24h": _flt(tk.get("highPrice")),
            "low_24h": _flt(tk.get("lowPrice")),
            "quote_volume": _flt(tk.get("quoteVolume")),
            "spread_bps": round(spread_bps, 2) if spread_bps is not None else None,
        }

        # 3. Indicators from the candle series.
        k = ctx.klines.get(symbol) or []
        if len(k) >= 30:
            closes = [c["close"] for c in k]
            highs = [c["high"] for c in k]
            lows = [c["low"] for c in k]
            m_line, m_sig, m_hist = macd(closes)
            up, mid, lo = bollinger(closes, 20, 2.0)
            band = (up - lo)
            b["indicators"] = {
                "rsi": round(rsi(closes, 14), 1),
                "macd_line": round(m_line, 6),
                "macd_signal": round(m_sig, 6),
                "macd_hist": round(m_hist, 6),
                "bb_pctb": round((closes[-1] - lo) / band, 3) if band > 0 else None,
                "bb_bandwidth_pct": round(band / mid * 100, 2) if mid > 0 else None,
                "adx": round(adx(highs, lows, closes, 14), 1),
                "atr_pct": round(atr_pct(highs, lows, closes, 14), 3),
                "sma20": round(_sma(closes, 20), 6),
                "sma50": round(_sma(closes, 50), 6),
                "price_vs_sma20_pct": round(_pct(closes[-1], _sma(closes, 20)), 2),
                "price_vs_sma50_pct": round(_pct(closes[-1], _sma(closes, 50)), 2),
                "dist_to_high_pct": round(_pct(b["quote"]["high_24h"] or price, price), 2),
                "dist_to_low_pct": round(_pct(b["quote"]["low_24h"] or price, price), 2),
            }
        else:
            notes.append("Not enough candle history for indicators on this pair.")

        # 4. The Council — the same five analysts, on this pair, live.
        try:
            _signals, verdict = await eng.council.deliberate_async(symbol, ctx)
            b["council"] = {
                "direction": verdict["direction"],
                "conviction": verdict["conviction"],
                "dissent_ratio": verdict["dissent_ratio"],
                "confluence_families": verdict.get("confluence_families", []),
                "synthesis": verdict.get("synthesis", ""),
                "bulls": verdict.get("bulls", []),
                "bears": verdict.get("bears", []),
                "abstained": verdict.get("abstained", []),
                "blind": verdict.get("blind", []),
                "transcript": [
                    {"agent": t["agent"], "stance": t["stance"],
                     "confidence": t["confidence"], "rationale": t["rationale"],
                     "data_quality": (t.get("evidence") or {}).get("data_quality", "live")}
                    for t in verdict.get("transcript", [])
                ],
            }
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Council analysis was unavailable ({type(exc).__name__}).")

        # 5. Derivatives positioning, if the futures feed answered.
        d = (ctx.derivatives.get(symbol) or {})
        if d and not d.get("error"):
            b["derivatives"] = {
                "available": True,
                "funding_pct": _maybe(d.get("funding_rate"), lambda x: round(x * 100, 4)),
                "open_interest": d.get("open_interest"),
                "long_short_ratio": d.get("long_short_ratio"),
                "taker_buy_sell_ratio": d.get("taker_buy_sell_ratio"),
            }
        else:
            b["derivatives"] = {"available": False}
            if symbol.endswith("USDT"):
                notes.append("No USDⓈ-M futures data for this pair, so positioning is read partially blind.")

        # 6. Guardian posture (portfolio-wide, but it frames every entry).
        try:
            g = eng.guardian.last
            if g:
                b["guardian"] = {
                    "score": round(g.score, 1), "level": g.level,
                    "recommendation": g.recommendation,
                    "quarantined": eng.guardian.quarantined,
                }
        except Exception:  # noqa: BLE001
            pass

        # 7. Narratives touching this pair.
        try:
            for n in sorted(eng.narrative.narratives.values(), key=lambda x: -x.strength):
                if symbol in getattr(n, "symbols", []):
                    b["narratives"].append({
                        "key": n.key, "label": n.label,
                        "strength": round(n.strength, 3),
                        "velocity": round(n.velocity, 3),
                    })
        except Exception:  # noqa: BLE001
            pass

        # 8. What the Constitution would do with a base-size entry here — the
        #    single most useful thing a risk-first desk can tell you.
        b["constitution_preview"] = self._constitution_preview(symbol, b)

        return b

    def _constitution_preview(self, symbol: str, b: dict) -> dict | None:
        """Dry-run a base-size long through the Constitution for this pair."""
        eng = self.engine
        try:
            from .config import CapitalState, Intent, Side

            marks = eng._marks() or {}
            state = eng.portfolio.state_dict(marks)
            equity = state.get("equity_usd", 0.0)
            price = b["quote"]["price"]
            base_notional = max(equity * (eng.council.base_size_pct / 100), 25.0)
            atr = (b.get("indicators") or {}).get("atr_pct") or 1.0
            stop_mult = max(1.6 * atr / 100, 0.02)

            intent = Intent(
                intent_id="preview", ts=time.time(), module="preview",
                symbol=symbol, side=Side.BUY, notional_usd=round(base_notional, 2),
                order_type="MARKET", limit_price=None,
                from_state=CapitalState.IDLE, to_state=CapitalState.DEPLOYED,
                thesis="preview", reference_price=price,
                stop_loss=round(price * (1 - stop_mult), 8),
                take_profit=round(price * (1 + stop_mult * 2), 8),
            )
            market = {symbol: {
                "spread_bps": b["quote"].get("spread_bps") or 2.0,
                "atr_pct": atr, "price": price,
            }}
            v = eng.constitution.evaluate(intent, state, market)
            size = v.adjusted_notional_usd if v.adjusted_notional_usd is not None else base_notional
            return {
                "decision": v.decision.value,
                "requested_usd": round(base_notional, 2),
                "allowed_usd": round(size, 2),
                "allowed_pct": round(size / equity * 100, 2) if equity > 0 else None,
                "triggered_rules": v.triggered_rules,
                "reasons": v.reasons,
            }
        except Exception:  # noqa: BLE001
            return None

    # -- public: chat ------------------------------------------------------ #

    async def answer(self, message: str, history: list[dict] | None = None,
                     lang: str | None = None) -> dict:
        """
        Hold a conversation about a pair. The reply is scaled to the question:
        a broad "what do you think of X" gets a full read, while a follow-up like
        "is it bullish?", "why?", or "what about risk?" gets a short, direct
        answer about the pair already in play — it does not re-dump the briefing.

        When a language-model key is configured (see ``_llm_config``) the model
        becomes the primary voice: it receives the same grounded facts and the
        conversation so it can converse naturally, and it is constrained to those
        facts so it cannot invent a number. With no key, the grounded desk answers
        at the right level of detail on its own.
        """
        history = history or []
        message = (message or "").strip()
        if not message:
            return {"ok": False, "symbol": None, "intent": None, "briefing": None,
                    "used_llm": False,
                    "answer": ("Ask me about a coin or pair — for example, "
                               "\"what do you think of SOL right now?\"")}

        # A pasted wallet address or transaction hash is handled first, before we
        # ever try to read the message as a coin. The chain is taken from the
        # message if named ("on base"), otherwise inferred from the format.
        insp = self._find_chain_query(message)
        if insp:
            return await self._inspect_answer(insp)

        last_symbol = self._last_symbol(history)
        known = self._known_symbols()
        # No fallback yet, so we can tell whether THIS message named a pair.
        named = resolve_symbol(message, known=known, fallback=None)
        intent = classify_intent(message)

        # A whole-market question ("how's the market", "market sentiments") goes to
        # the market read even if a stray word looked like a ticker. Only a message
        # that clearly names a coin overrides it.
        if intent == "market" and named is None:
            return {"ok": True, "answer": self._compose_market(), "symbol": None,
                    "intent": "market", "briefing": None, "used_llm": False}

        fresh = named is not None and named != last_symbol
        symbol = named or last_symbol
        if symbol is None:
            return {"ok": True, "symbol": None, "intent": intent, "briefing": None,
                    "used_llm": False,
                    "answer": ("I couldn't tell which pair you mean. Name a coin or "
                               "pair — e.g. \"BTC\", \"solana\", or \"INJUSDT\" — and "
                               "I'll pull a full read.")}

        briefing = await self.analyze(symbol)
        if not briefing.get("ok"):
            return {"ok": False, "symbol": symbol, "intent": intent,
                    "briefing": briefing, "used_llm": False,
                    "answer": briefing.get("error", f"Couldn't analyse {symbol}.")}

        granularity = self._granularity(message, intent, fresh)

        # Primary: a real model when one is configured. It handles conversation
        # naturally; the grounded facts keep it honest.
        llm = await self._llm_narrate(message, briefing, intent, history, granularity, lang)
        if llm:
            out = {"ok": True, "symbol": symbol, "intent": intent,
                   "briefing": briefing, "used_llm": True, "answer": llm}
            if getattr(self, "_llm_provider_name", None):
                out["llm_provider"] = self._llm_provider_name
            return out

        # Fallback: the grounded desk, answering at the right level of detail.
        grounded = self._compose_grounded(briefing, intent, message, granularity)
        return {"ok": True, "symbol": symbol, "intent": intent,
                "briefing": briefing, "used_llm": False, "answer": grounded}

    # -- conversational granularity --------------------------------------- #

    def _granularity(self, message: str, intent: str, fresh: bool) -> str:
        """How much to say. A new pair or an explicit request earns a full read;
        a short follow-up earns a focused one."""
        t = " " + message.lower().strip() + " "
        if any(w in t for w in ("full read", "full analysis", "analyz", "analyse",
                                "rundown", "break it down", "break down", "deep dive",
                                "everything", "complete", "in detail", "detailed",
                                "walk me through", "whole picture", "the works")):
            return "full"
        if self._is_yesno(t):
            return "verdict"
        if intent == "explain":
            if self._is_movement_q(t):
                return "why_move"
            return "why"
        if intent == "risk":
            return "risk"
        if intent == "levels":
            return "levels"
        metric = self._metric_focus(t)
        if metric:
            return "metric"
        if fresh:
            return "full"      # a new coin, open-ended → full read
        return "recap"         # same coin, open-ended follow-up → short recap

    @staticmethod
    def _is_yesno(t: str) -> bool:
        t = t.strip()
        if re.match(r"^(is|are|does|do|should|would|will|can|has|have)\b", t):
            return True
        if any(w in " " + t + " " for w in (" bull or bear", " long or short",
                                            " buy or sell", " yes or no",
                                            " up or down", " in or out")):
            return True
        return t in {"bullish?", "bearish?", "buy?", "sell?", "long?", "short?",
                     "safe?", "risky?", "good?", "bad?", "worth it?"}

    @staticmethod
    def _is_movement_q(t: str) -> bool:
        """A 'why is it moving/dumping/pumping' style question."""
        moves = ("dump", "dropping", "drop", "falling", "fall", "tank", "crash",
                 "selling", "sell off", "sell-off", "red", "down", "sinking", "bleed",
                 "pump", "rally", "rallying", "moon", "mooning", "ripping", "rip",
                 "spike", "spiking", "surge", "green", "up ", "rising", "rise", "soar")
        return any(w in t for w in moves)

    @staticmethod
    def _metric_focus(t: str):
        pairs = [("rsi", "rsi"), ("macd", "macd"), ("adx", "adx"),
                 ("funding", "funding"), ("open interest", "oi"), (" oi ", "oi"),
                 ("long/short", "lsr"), ("long short", "lsr"), ("spread", "spread"),
                 ("atr", "atr"), ("volatility", "atr"), ("volume", "volume"),
                 ("price", "price"), ("how much is", "price")]
        # Only treat as a metric question if it's fairly short and pointed.
        if len(t.split()) > 9:
            return None
        for needle, tag in pairs:
            if needle in t:
                return tag
        return None

    # -- grounded composition, dispatched by how much the question wants --- #

    def _compose_grounded(self, b: dict, intent: str, question: str, granularity: str) -> str:
        if granularity == "full":
            return self._compose_full(b, intent, question)
        if granularity == "verdict":
            return self._verdict_only(b)
        if granularity == "why":
            return self._explain_only(b)
        if granularity == "why_move":
            return self._why_moving(b)
        if granularity == "risk":
            return self._risk_only(b)
        if granularity == "levels":
            return self._levels_only(b)
        if granularity == "metric":
            return self._metric_answer(b, question) or self._recap(b)
        return self._recap(b)

    @staticmethod
    def _chg_phrase(q: dict) -> str:
        chg = q.get("change_24h_pct")
        if chg is None or abs(chg) < 0.05:
            return "flat on the day"
        return f"{chg:+.2f}% on the day"

    @staticmethod
    def _spread_phrase(s: float) -> str:
        if s < 0.5:
            return "under 1 bp — extremely liquid"
        if s > 20:
            return f"{s:.1f} bps — wide enough to cost you on entry"
        return f"{s:.1f} bps — workable"

    def _top_driver(self, b: dict) -> str:
        c = b.get("council") or {}
        d = c.get("direction")
        cands = [t for t in c.get("transcript", [])
                 if t.get("stance") == d and t.get("data_quality") != "unavailable"]
        if not cands:
            return ""
        top = max(cands, key=lambda t: t.get("confidence", 0))
        r = (top.get("rationale") or "").strip().rstrip(".")
        return f"The main driver is {top['agent']}: {r}." if r else ""

    def _verdict_only(self, b: dict) -> str:
        c = b.get("council") or {}
        sym = b["symbol"]; q = b["quote"]
        d = c.get("direction", "neutral"); conv = c.get("conviction", 0.0)
        head = f"{sym} at {_price(q['price'])}. "
        if d == "neutral" or conv < 0.15:
            return head + (f"Not really — the Council is effectively neutral ({conv:.0%}); the "
                           "analysts cancel out, so there's no direction worth acting on right now.")
        fams = c.get("confluence_families", []) or []
        strength = "strongly" if conv >= 0.55 else "moderately" if conv >= 0.35 else "only weakly"
        lean = "bullish" if d == "bullish" else "bearish"
        yn = "Leaning yes" if d == "bullish" else "Leaning no — it's bearish"
        frag = (" The case rests on a single method family, so I'd call it fragile until a second "
                "lens agrees.") if len(fams) <= 1 else ""
        driver = self._top_driver(b)
        return (head + f"{yn}, but {strength}: the Council is {lean} at {conv:.0%}."
                + ((" " + driver) if driver else "") + frag
                + " I wouldn't treat it as a standalone signal to act.")

    def _explain_only(self, b: dict) -> str:
        parts = [self._explain_council(b)]
        nz = self._divergences(b)
        if nz:
            parts.append(nz)
        return "\n\n".join(parts)

    def _why_moving(self, b: dict) -> str:
        """Grounded read of WHY a pair is moving, from market internals — honest
        that it can't see the news catalyst, only what the data shows."""
        sym = b["symbol"]; q = b["quote"]; ind = b.get("indicators") or {}
        d = b.get("derivatives") or {}
        chg = q.get("change_24h_pct")
        lead = f"{sym} at {_price(q['price'])}. "
        if chg is None:
            lead += "I don't have a clean 24h change for it right now. "
        elif chg <= -0.3:
            lead += f"It's down {abs(chg):.2f}% over the last 24h. "
        elif chg >= 0.3:
            lead += f"It's up {chg:.2f}% over the last 24h. "
        else:
            lead += (f"On the daily it's basically flat ({chg:+.2f}%), so any 'move' you're "
                     f"seeing is on a shorter timeframe than the 24h figure shows. ")

        bits = []
        hist = ind.get("macd_hist")
        if hist is not None:
            bits.append(f"momentum is {'building' if hist > 0 else 'fading'} (MACD histogram "
                        f"{'positive' if hist > 0 else 'negative'})")
        r = ind.get("rsi")
        if r is not None:
            if r >= 70:
                bits.append(f"RSI {r:.0f} is overbought, so buyers are stretched")
            elif r <= 30:
                bits.append(f"RSI {r:.0f} is oversold, so sellers are stretched and a bounce is likely")
            else:
                bits.append(f"RSI {r:.0f} is mid-range, so this isn't an exhausted move yet")
        adx_v = ind.get("adx")
        if adx_v is not None:
            if adx_v < 20:
                bits.append(f"ADX {adx_v:.0f} says there's no real trend — this is range chop, "
                            f"not a directional break")
            else:
                bits.append(f"ADX {adx_v:.0f} says the move has genuine trend strength behind it")

        # Leverage vs spot read from funding.
        if d.get("available") and d.get("funding_pct") is not None:
            fr = d["funding_pct"]
            if fr > 0.02:
                bits.append("funding is positive — the crowd is leveraged long, so a drop can "
                            "cascade as those longs get squeezed")
            elif fr < -0.02:
                bits.append("funding is negative — the crowd is leveraged short, which can fuel "
                            "a sharp bounce if they're squeezed")
            else:
                bits.append("funding is roughly neutral, so this looks spot-driven rather than a "
                            "leverage flush")

        out = lead
        if bits:
            out += "What the internals say: " + "; ".join(bits) + ". "
        out += ("I can't see news or headlines, so I can't name the catalyst — this is what the "
                "market's own data shows, not the reason in the news.")
        return out

    def _risk_only(self, b: dict) -> str:
        head = f"{b['symbol']} at {_price(b['quote']['price'])}. "
        r = self._risk(b)
        if not r:
            return head + ("Nothing in the risk read stands out — the Guardian is calm and this "
                           "pair's volatility is unremarkable right now.")
        return head + r

    def _levels_only(self, b: dict) -> str:
        head = f"{b['symbol']} at {_price(b['quote']['price'])}. "
        if not b.get("indicators"):
            return head + "Not enough candle history to place levels on this pair yet."
        return head + self._levels(b)

    def _recap(self, b: dict) -> str:
        c = b.get("council") or {}
        sym = b["symbol"]; q = b["quote"]
        d = c.get("direction", "neutral"); conv = c.get("conviction", 0.0)
        bits = [f"{sym} at {_price(q['price'])}, {self._chg_phrase(q)}."]
        if d == "neutral" or conv < 0.15:
            bits.append(f"The Council is roughly neutral ({conv:.0%}) — no edge to act on.")
        else:
            fams = c.get("confluence_families", []) or []
            f = " (single lens, so I'd treat it as fragile)" if len(fams) <= 1 else f" across {len(fams)} independent lenses"
            bits.append(f"It still reads {d} at {conv:.0%}{f}.")
        nz = self._one_nuance(b)
        if nz:
            bits.append(nz)
        bits.append("Ask for the full read, the reasoning, the levels, or the risk if you want to go deeper.")
        return " ".join(bits)

    def _one_nuance(self, b: dict) -> str:
        ind = b.get("indicators") or {}
        r = ind.get("rsi"); dhigh = ind.get("dist_to_high_pct"); adx_v = ind.get("adx")
        if r is not None and r > 68 and dhigh is not None and 0 <= dhigh < 1.5:
            return "It's overbought right at the 24h high — a natural spot for sellers to step in."
        if r is not None and r < 32:
            return "It's oversold, so a relief bounce is the higher-probability near-term path."
        if adx_v is not None and adx_v < 18:
            return "ADX is low — this is a range, not a trend, so extremes are worth fading rather than chasing."
        deriv = b.get("derivatives") or {}
        if deriv.get("available") and deriv.get("funding_pct") is not None:
            fr = deriv["funding_pct"]
            if fr > 0.02:
                return "Funding is positive — the crowd is paying to be long, which leaves room for a squeeze."
            if fr < -0.02:
                return "Funding is negative — shorts are paying, so a rally would squeeze them."
        return ""

    def _metric_answer(self, b: dict, question: str) -> str:
        tag = self._metric_focus(" " + question.lower() + " ")
        ind = b.get("indicators") or {}
        q = b["quote"]; d = b.get("derivatives") or {}
        sym = b["symbol"]
        if tag == "rsi":
            r = ind.get("rsi")
            if r is None:
                return f"No RSI available for {sym} yet."
            note = "overbought" if r >= 70 else "oversold" if r <= 30 else "mid-range, no extreme either way"
            return f"{sym} RSI is {r:.0f} — {note}."
        if tag == "macd":
            h = ind.get("macd_hist")
            if h is None:
                return f"No MACD available for {sym} yet."
            return f"{sym} MACD histogram is {'positive' if h > 0 else 'negative'} — short-term momentum is {'building' if h > 0 else 'fading'}."
        if tag == "adx":
            a = ind.get("adx")
            if a is None:
                return f"No ADX available for {sym} yet."
            note = "a real trend, so trend signals carry weight" if a >= 25 else "no trend — this is a range" if a < 18 else "a developing trend"
            return f"{sym} ADX is {a:.0f} — {note}."
        if tag == "atr":
            a = ind.get("atr_pct")
            if a is None:
                return f"No volatility read for {sym} yet."
            note = "quiet" if a < 0.5 else "lively" if a < 2 else "volatile"
            return f"{sym} average true range is {a:.2f}% per candle — {note}."
        if tag == "spread":
            s = q.get("spread_bps")
            if s is None:
                return f"No spread read for {sym} yet."
            return f"{sym} top-of-book spread is {self._spread_phrase(s)}."
        if tag == "funding":
            if not d.get("available") or d.get("funding_pct") is None:
                return f"No futures funding data for {sym} right now."
            fr = d["funding_pct"]
            note = "longs are paying (crowd long)" if fr > 0.02 else "shorts are paying (crowd short)" if fr < -0.02 else "roughly neutral"
            return f"{sym} funding is {fr:+.3f}% — {note}."
        if tag == "lsr":
            if not d.get("available") or d.get("long_short_ratio") is None:
                return f"No long/short data for {sym} right now."
            return f"{sym} long/short account ratio is {d['long_short_ratio']}."
        if tag == "oi":
            if not d.get("available") or d.get("open_interest") is None:
                return f"No open-interest data for {sym} right now."
            return f"{sym} open interest is {d['open_interest']}."
        if tag == "volume":
            v = q.get("quote_volume")
            if v is None:
                return f"No volume read for {sym} yet."
            return f"{sym} 24h quote volume is ${v:,.0f}."
        if tag == "price":
            return f"{sym} is at {_price(q['price'])}, {self._chg_phrase(q)}."
        return ""

    # -- the full read ----------------------------------------------------- #

    def _compose_full(self, b: dict, intent: str, question: str) -> str:
        sym = b["symbol"]
        q = b["quote"]
        ind = b.get("indicators") or {}
        c = b.get("council") or {}
        parts: list[str] = []

        # --- headline: the view and the one-line reason ---
        direction = c.get("direction", "neutral")
        conv = c.get("conviction", 0.0)
        fams = c.get("confluence_families", []) or []
        chg_txt = self._chg_phrase(q)
        lead = f"**{sym}** is at {_price(q['price'])}, {chg_txt}. "
        if direction == "neutral" or conv < 0.15:
            lead += ("The Council has no real directional edge here right now — the "
                     "analysts roughly cancel out, which is itself a reason not to force a trade.")
        else:
            strength = ("a high-conviction" if conv >= 0.55 else
                        "a moderate" if conv >= 0.35 else "a low-conviction")
            fam_txt = (f" corroborated across {len(fams)} independent lenses ({', '.join(fams)})"
                       if len(fams) >= 2 else
                       f" resting on a single lens ({fams[0]})" if len(fams) == 1 else "")
            lead += (f"The Council reads it **{direction}** at {conv:.0%} — {strength} call{fam_txt}.")
        parts.append(lead)

        # --- the evidence, translated (only what we actually have) ---
        ev: list[str] = []
        if ind:
            r = ind.get("rsi")
            if r is not None:
                if r >= 70:
                    ev.append(f"RSI is {r:.0f}, stretched into overbought — momentum is strong but late.")
                elif r <= 30:
                    ev.append(f"RSI is {r:.0f}, oversold — sellers are extended and prone to a bounce.")
                else:
                    ev.append(f"RSI is {r:.0f}, mid-range with room either way.")
            adx_v = ind.get("adx")
            if adx_v is not None:
                if adx_v >= 25:
                    ev.append(f"ADX at {adx_v:.0f} says the trend is real, not chop, so trend signals carry weight.")
                elif adx_v < 18:
                    ev.append(f"ADX at {adx_v:.0f} means there is no trend to speak of — this is a range, and breakouts will tend to fail.")
            hist = ind.get("macd_hist")
            if hist is not None and abs(hist) > 0:
                ev.append(f"MACD histogram is {'positive' if hist > 0 else 'negative'}, "
                          f"so short-term momentum is {'building' if hist > 0 else 'fading'}.")
            pctb = ind.get("bb_pctb")
            if pctb is not None:
                if pctb > 1:
                    ev.append("Price is above the upper Bollinger band — a stretched, mean-reversion-prone extension.")
                elif pctb < 0:
                    ev.append("Price is below the lower Bollinger band — an extended, oversold extension.")
            s20 = ind.get("price_vs_sma20_pct")
            if s20 is not None:
                if abs(s20) < 0.05:
                    ev.append("It is sitting right on its 20-period average.")
                elif s20 > 0:
                    ev.append(f"It is {s20:.1f}% above its 20-period average, holding above trend.")
                else:
                    ev.append(f"It is {abs(s20):.1f}% below its 20-period average, trading below trend.")
        if q.get("spread_bps") is not None:
            ev.append(f"Top-of-book spread is {self._spread_phrase(q['spread_bps'])}.")

        deriv = b.get("derivatives") or {}
        if deriv.get("available"):
            fr = deriv.get("funding_pct")
            if fr is not None:
                if fr > 0.02:
                    ev.append(f"Funding is {fr:+.3f}% — longs are paying to hold, so positioning is crowded long and vulnerable to a squeeze.")
                elif fr < -0.02:
                    ev.append(f"Funding is {fr:+.3f}% — shorts are paying, so the crowd is short and a rally would hurt them.")
                else:
                    ev.append(f"Funding is {fr:+.3f}%, roughly neutral positioning.")
            lsr = deriv.get("long_short_ratio")
            if lsr:
                ev.append(f"The long/short account ratio is {lsr}, {'crowd-long' if _flt(lsr) and _flt(lsr) > 1.3 else 'crowd-short' if _flt(lsr) and _flt(lsr) < 0.8 else 'balanced'}.")
        if ev:
            parts.append("What the data says: " + " ".join(ev))

        # --- divergence / nuance the numbers together imply ---
        nuance = self._divergences(b)
        if nuance:
            parts.append(nuance)

        # --- intent-specific section ---
        if intent in ("levels", "opinion") and ind:
            parts.append(self._levels(b))
        if intent == "risk" or intent == "opinion":
            parts.append(self._risk(b))
        if intent == "explain" and c.get("transcript"):
            parts.append(self._explain_council(b))

        # --- what the system itself would permit ---
        cp = b.get("constitution_preview")
        if cp:
            if cp["decision"] == "DENY":
                parts.append(f"If you tried to act, the Constitution would **deny** it outright — "
                             f"{_first_reason(cp)}. That is the risk layer doing its job, not a glitch.")
            elif cp["decision"] == "REQUIRE_HUMAN":
                parts.append(f"On sizing: a base position here (~{_usd(cp['allowed_usd'])}, "
                             f"{cp['allowed_pct']:.0f}% of equity) would clear the rules but "
                             f"**route to your confirmation** before it filled.")
            else:
                trimmed = cp["allowed_usd"] < cp["requested_usd"] - 0.5
                if trimmed:
                    parts.append(f"On sizing: the Constitution would trim a base entry to "
                                 f"~{_usd(cp['allowed_usd'])} ({cp['allowed_pct']:.0f}% of equity) "
                                 f"— {_first_reason(cp) or 'a position-size cap'}.")
                else:
                    parts.append(f"On sizing: a base entry of ~{_usd(cp['allowed_usd'])} "
                                 f"({cp['allowed_pct']:.0f}% of equity) would pass the rules as-is.")

        # --- honesty about what's missing ---
        if b.get("notes"):
            parts.append("Caveat: " + " ".join(b["notes"]))

        return "\n\n".join(p for p in parts if p)

    def _divergences(self, b: dict) -> str:
        """Read two or more signals against each other for a real edge."""
        ind = b.get("indicators") or {}
        q = b["quote"]
        deriv = b.get("derivatives") or {}
        out: list[str] = []

        chg = q.get("change_24h_pct")
        oi = deriv.get("open_interest") if deriv.get("available") else None
        # Price up but not confirmed by momentum.
        if chg is not None and chg > 2 and ind.get("macd_hist") is not None and ind["macd_hist"] < 0:
            out.append("the day is green while momentum is already rolling over, which usually marks a move running out of buyers rather than a fresh leg up")
        if chg is not None and chg < -2 and ind.get("rsi") is not None and ind["rsi"] < 32:
            out.append("the sell-off has pushed it oversold, so the easy downside may be behind it and a relief bounce is the higher-probability near-term path")
        # Overbought into resistance.
        r = ind.get("rsi"); dhigh = ind.get("dist_to_high_pct")
        if r is not None and r > 68 and dhigh is not None and 0 <= dhigh < 1.5:
            out.append("it is overbought and pressed right up against the 24h high, a classic spot to see sellers step in")
        # Trend vs range mismatch.
        adx_v = ind.get("adx")
        if adx_v is not None and adx_v < 18 and abs(ind.get("price_vs_sma20_pct", 0)) > 3:
            out.append("it has pushed well away from its mean with no trend strength behind it, which tends to snap back rather than extend")
        if not out:
            return ""
        return "Reading the signals together: " + "; ".join(out) + "."

    def _levels(self, b: dict) -> str:
        ind = b["indicators"]; q = b["quote"]; price = q["price"]
        up, mid, lo = None, None, None
        # Reconstruct band edges from stored width if present.
        support_bits, resist_bits = [], []
        if q.get("low_24h"):
            support_bits.append(f"the 24h low near {_price(q['low_24h'])}")
        if ind.get("sma20"):
            (support_bits if price >= ind["sma20"] else resist_bits).append(
                f"the 20-period average at {_price(ind['sma20'])}")
        if ind.get("sma50"):
            (support_bits if price >= ind["sma50"] else resist_bits).append(
                f"the 50-period average at {_price(ind['sma50'])}")
        if q.get("high_24h"):
            resist_bits.append(f"the 24h high near {_price(q['high_24h'])}")
        atr = ind.get("atr_pct") or 1.0
        stop = price * (1 - max(1.6 * atr / 100, 0.02))
        s = "Levels: "
        s += ("support sits at " + ", ".join(support_bits) + ". ") if support_bits else ""
        s += ("Resistance is " + ", ".join(resist_bits) + ". ") if resist_bits else ""
        s += (f"Given {atr:.1f}% average true range, a volatility-aware stop on a long "
              f"belongs around {_price(stop)} — roughly {max(1.6*atr,2):.1f}% away, so normal "
              f"noise doesn't take you out.")
        return s

    def _risk(self, b: dict) -> str:
        g = b.get("guardian") or {}
        ind = b.get("indicators") or {}
        atr = ind.get("atr_pct")
        bits = []
        if g:
            posture = {"normal": "calm", "elevated": "elevated", "critical": "critical"}.get(g.get("level"), g.get("level"))
            bits.append(f"Portfolio-wide, the Guardian threat is {g.get('score')}/100 ({posture})"
                        + (" and capital is quarantined — new risk is frozen" if g.get("quarantined") else ""))
            if g.get("level") == "critical":
                bits.append("in this state a leveraged open would be vetoed on the spot; only risk-reducing closes get through")
        if atr is not None:
            bits.append(f"this pair's own volatility is {atr:.1f}% ATR, so at 10x leverage a routine "
                        f"{atr:.1f}% tick against you is already ~{atr*10:.0f}% of margin — size accordingly")
        if not bits:
            return ""
        return "On risk: " + ". ".join(bits) + "."

    def _explain_council(self, b: dict) -> str:
        c = b["council"]
        lines = []
        for t in c["transcript"]:
            tag = {"bullish": "for", "bearish": "against", "neutral": "abstained"}.get(t["stance"], t["stance"])
            if t["data_quality"] == "unavailable":
                lines.append(f"{t['agent']} abstained — its data was unavailable")
            else:
                lines.append(f"{t['agent']} ({t['confidence']:.0%}, {tag}): {t['rationale']}")
        head = (f"Why the Council landed {c['direction']} at {c['conviction']:.0%}: each analyst reads a "
                f"different lens, and the verdict weights them by track record and shrinks for disagreement.")
        return head + "\n\n" + "\n".join(f"• {ln}" for ln in lines)

    def _compose_market(self) -> str:
        """A whole-book read from the current verdicts and Guardian."""
        eng = self.engine
        vs = eng.council_verdicts or []
        g = eng.guardian.last
        parts = []
        if g:
            posture = {"normal": "calm", "elevated": "elevated", "critical": "critical"}.get(g.level, g.level)
            parts.append(f"Across the book the Guardian threat is **{g.score:.0f}/100** ({posture}). {g.recommendation}")
        bulls = [v for v in vs if v.get("direction") == "bullish" and v.get("conviction", 0) >= 0.3]
        bears = [v for v in vs if v.get("direction") == "bearish" and v.get("conviction", 0) >= 0.3]
        if bulls:
            parts.append("Leaning long: " + ", ".join(f"{v['symbol']} ({v['conviction']:.0%})"
                         for v in sorted(bulls, key=lambda x: -x["conviction"])) + ".")
        if bears:
            parts.append("Leaning short: " + ", ".join(f"{v['symbol']} ({v['conviction']:.0%})"
                         for v in sorted(bears, key=lambda x: -x["conviction"])) + ".")
        if not bulls and not bears:
            parts.append("No name on the traded book is clearing the conviction bar right now — the "
                         "Council is sidelined, which in a risk-first system is a position in itself.")
        parts.append("Ask me about any specific pair — including ones the engine doesn't trade — for a full read.")
        return "\n\n".join(parts)

    # -- pasted address / transaction lookup ------------------------------ #

    _CHAIN_WORDS = [
        # checked longest-first; maps a phrase in the message to an inspector chain id
        ("base sepolia", "base-sepolia"), ("sepolia", "base-sepolia"),
        ("bitcoin", "bitcoin"), ("btc", "bitcoin"),
        ("ethereum", "ethereum"), ("eth", "ethereum"), ("erc20", "ethereum"),
        ("solana", "solana"), ("sol", "solana"),
        ("binance smart chain", "bnb"), ("bsc", "bnb"), ("bnb chain", "bnb"), ("bnb", "bnb"),
        ("arbitrum", "arbitrum"), ("arb", "arbitrum"),
        ("polygon", "polygon"), ("matic", "polygon"), ("pol ", "polygon"),
        ("optimism", "optimism"), (" op ", "optimism"),
        ("avalanche", "avalanche"), ("avax", "avalanche"),
        ("base", "base"),
    ]

    def _detect_chain(self, message: str):
        t = " " + message.lower() + " "
        for phrase, cid in self._CHAIN_WORDS:
            if phrase in t:
                return cid
        return None

    @staticmethod
    def _default_chain_for(kind: str):
        if kind in ("evm_address", "evm_tx"):
            return "ethereum"          # EVM formats are shared; Ethereum unless told otherwise
        if kind in ("btc_address", "btc_tx"):
            return "bitcoin"
        if kind == "solana":
            return "solana"
        return None

    def _find_chain_query(self, message: str):
        """Return {query, chain_id, kind, named, ambiguous} if the message
        contains a wallet address or tx hash, else None."""
        from .settlement import ChainInspector
        named = self._detect_chain(message)
        for tok in re.findall(r"[A-Za-z0-9]{25,90}", message):
            kind = ChainInspector.classify(tok)
            if kind == "unknown":
                continue
            chain_id = named or self._default_chain_for(kind)
            if not chain_id:
                continue
            ambiguous = (named is None and kind in ("evm_address", "evm_tx"))
            return {"query": tok, "chain_id": chain_id, "kind": kind,
                    "named": bool(named), "ambiguous": ambiguous}
        return None

    async def _inspect_answer(self, insp: dict) -> dict:
        from .settlement import ChainInspector, INSPECT_CHAINS
        inspector = getattr(self, "_inspector", None)
        if inspector is None:
            inspector = ChainInspector()
            self._inspector = inspector

        q = insp["query"]
        chain_id = insp["chain_id"]
        label = (INSPECT_CHAINS.get(chain_id) or {}).get("label", chain_id)
        short = q[:10] + "…" + q[-6:] if len(q) > 20 else q

        try:
            res = await inspector.inspect(q, chain_id)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}"}

        if not res.get("ok"):
            msg = (f"I couldn't look that up on {label}: {res.get('error', 'lookup failed')}. "
                   "If it's on a different chain, tell me which — e.g. \"on base\" or \"on solana\".")
            return {"ok": True, "answer": msg, "symbol": None, "intent": "inspect",
                    "briefing": None, "used_llm": False}

        sym = res.get("symbol", "")
        if res.get("type") == "address":
            bal = res.get("balance")
            txc = res.get("tx_count")
            line = f"**{label} address** {short}\n\n"
            line += f"Balance: {bal:g} {sym}. " if bal is not None else ""
            line += f"{txc:,} transactions on record. " if txc is not None else ""
            if insp.get("ambiguous"):
                line += ("\n\nI read this as an Ethereum address — EVM chains share the same "
                         "address format, so if it's on Base, Arbitrum, BNB or another EVM chain, "
                         "say which and I'll re-check.")
        elif res.get("type") == "transaction":
            status = res.get("status", "unknown")
            val = res.get("value")
            conf = res.get("confirmed")
            line = f"**{label} transaction** {short}\n\n"
            line += f"Status: {status}"
            line += f", {'confirmed' if conf else 'not yet confirmed'}" if conf is not None else ""
            line += ". "
            line += f"Value moved: {val:g} {sym}. " if val is not None else ""
            if insp.get("ambiguous"):
                line += ("\n\nI checked Ethereum by default; if this hash is on another EVM chain, "
                         "tell me which (e.g. \"on base\").")
        else:
            line = f"On {label}, {short} resolved but returned an unfamiliar shape."

        return {"ok": True, "answer": line.strip(), "symbol": None, "intent": "inspect",
                "briefing": None, "used_llm": False, "chain": chain_id}

    # -- language-model brain (multi-provider, fail-safe) ----------------- #

    # Providers are tried in this order. The first two have generous free tiers,
    # so a real conversational desk costs nothing to switch on. Set any one key
    # in the environment (or a .env file in the project root) and it takes over;
    # with none set, the grounded desk answers on its own.
    _PROVIDERS = [
        # (env var, provider tag, default model)
        ("GROQ_API_KEY", "groq", "llama-3.3-70b-versatile"),
        ("GEMINI_API_KEY", "gemini", "gemini-2.5-flash"),
        ("GOOGLE_API_KEY", "gemini", "gemini-2.5-flash"),
        ("OPENAI_API_KEY", "openai", "gpt-4o-mini"),
        ("ANTHROPIC_API_KEY", "anthropic", "claude-3-5-sonnet-latest"),
        ("OPENROUTER_API_KEY", "openrouter", "meta-llama/llama-3.3-70b-instruct"),
    ]
    # Gemini 1.5 is end-of-life (returns 404); try current models in order.
    _GEMINI_FALLBACKS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

    # env var -> (tag, default model), and the reverse, for the UI key flow.
    _DEFAULT_MODEL = {
        "groq": "llama-3.3-70b-versatile",
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-latest",
        "openrouter": "meta-llama/llama-3.3-70b-instruct",
    }
    _ENV_FOR = {
        "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }

    def _llm_config(self):
        """Pick the active provider. Returns (tag, key, model) or None.

        An explicit ``GLASSBOX_LLM_PROVIDER`` (set when the user connects a model
        in the UI) wins; otherwise the first configured key in priority order is
        used, so a free provider is preferred automatically."""
        self._load_env_once()
        chosen = (os.getenv("GLASSBOX_LLM_PROVIDER") or "").lower().strip()
        if chosen and chosen in self._ENV_FOR:
            key = os.getenv(self._ENV_FOR[chosen])
            if key:
                model = os.getenv("GLASSBOX_LLM_MODEL", self._DEFAULT_MODEL[chosen])
                self._llm_provider_name = chosen
                return chosen, key, model
        for env_var, tag, default_model in self._PROVIDERS:
            key = os.getenv(env_var)
            if key:
                model = os.getenv("GLASSBOX_LLM_MODEL", default_model)
                self._llm_provider_name = tag
                return tag, key, model
        self._llm_provider_name = None
        return None

    # -- UI key flow: connect / verify / disconnect a model --------------- #

    def llm_status(self) -> dict:
        cfg = self._llm_config()
        if cfg:
            return {"connected": True, "provider": cfg[0], "model": cfg[2]}
        return {"connected": False, "provider": None, "model": None}

    async def set_llm_key(self, provider: str, key: str) -> dict:
        """Connect a model at runtime. Lists the provider's models live (which
        both validates the key and future-proofs against new releases), pins the
        best current one, and persists to a .env. Free providers only, in the UI."""
        provider = (provider or "").lower().strip()
        key = (key or "").strip()
        if provider not in ("groq", "gemini", "openai", "anthropic"):
            return {"ok": False, "error": "Unknown provider."}
        if len(key) < 8:
            return {"ok": False, "error": "That doesn't look like a valid key."}

        env_var = self._ENV_FOR[provider]
        models, status = await self._list_models(provider, key)

        if status == "auth":
            hint = (" If it starts with \"AQ.\", some accounts have those blocked — a free Groq "
                    "key (console.groq.com) is a reliable alternative.") if provider == "gemini" else ""
            return {"ok": False, "error": "That key was rejected by the provider." + hint}

        if status == "ok" and models:
            default = models[0]
            os.environ[env_var] = key
            os.environ["GLASSBOX_LLM_PROVIDER"] = provider
            os.environ["GLASSBOX_LLM_MODEL"] = default
            self._models_cache[provider] = (time.time(), models)
            persisted = self._persist_env({env_var: key, "GLASSBOX_LLM_PROVIDER": provider,
                                           "GLASSBOX_LLM_MODEL": default})
            self._llm_provider_name = provider
            return {"ok": True, "provider": provider, "verified": True, "persisted": persisted,
                    "note": f"Connected — using {default}. You can pick another model below.",
                    "models": models, "current": default}

        # Couldn't reach the provider to list — save and let it try next question.
        os.environ[env_var] = key
        os.environ["GLASSBOX_LLM_PROVIDER"] = provider
        persisted = self._persist_env({env_var: key, "GLASSBOX_LLM_PROVIDER": provider})
        self._llm_provider_name = provider
        return {"ok": True, "provider": provider, "verified": False, "persisted": persisted,
                "note": "Saved, but couldn't reach the provider to list models (network?). It'll be used on your next question.",
                "models": [], "current": os.getenv("GLASSBOX_LLM_MODEL")}

    async def list_models_for_ui(self) -> dict:
        """For the model-picker: the connected provider's available models
        (best first) and the one currently selected."""
        cfg = self._llm_config()
        if not cfg:
            return {"connected": False, "provider": None, "models": [], "current": None}
        provider, key, _ = cfg
        cached = self._models_cache.get(provider)
        if cached and (time.time() - cached[0]) < 300:
            models = cached[1]
        else:
            models, status = await self._list_models(provider, key)
            if status == "ok" and models:
                self._models_cache[provider] = (time.time(), models)
            if not models:
                models = (list(self._GEMINI_FALLBACKS) if provider == "gemini"
                          else [self._DEFAULT_MODEL.get(provider, "")])
                models = [m for m in models if m]
        return {"connected": True, "provider": provider, "models": models,
                "current": os.getenv("GLASSBOX_LLM_MODEL") or (models[0] if models else None)}

    def set_model(self, model: str) -> dict:
        """Pin a specific model within the connected provider."""
        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "No model given."}
        os.environ["GLASSBOX_LLM_MODEL"] = model
        self._persist_env({"GLASSBOX_LLM_MODEL": model})
        return {"ok": True, "current": model}

    async def _list_models(self, provider: str, key: str):
        """Fetch the provider's chat-capable models, best first.
        Returns (models, status) with status 'ok' | 'auth' | 'net'."""
        try:
            import httpx
            if provider in ("openai", "anthropic"):
                return await self._list_models_extra(provider, key)
            if provider == "groq":
                async with httpx.AsyncClient(timeout=12.0) as client:
                    r = await client.get("https://api.groq.com/openai/v1/models",
                                         headers={"Authorization": f"Bearer {key}"})
                if r.status_code in (401, 403):
                    return [], "auth"
                if r.status_code != 200:
                    return [], "net"
                ids = [m.get("id", "") for m in (r.json().get("data") or [])]
                return self._rank_groq(ids), "ok"
            async with httpx.AsyncClient(timeout=12.0) as client:  # gemini
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": key}, headers={"x-goog-api-key": key})
            if provider == "gemini":
                if r.status_code in (401, 403):
                    return [], "auth"
                if r.status_code != 200:
                    return [], "net"
                out = []
                for m in (r.json().get("models") or []):
                    if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                        continue
                    name = (m.get("name") or "").replace("models/", "")
                    if name:
                        out.append(name)
                return self._rank_gemini(out), "ok"
        except Exception:  # noqa: BLE001
            return [], "net"

    async def _list_models_extra(self, provider: str, key: str):
        """OpenAI and Anthropic model listing (kept separate for clarity)."""
        try:
            import httpx
            if provider == "openai":
                async with httpx.AsyncClient(timeout=12.0) as client:
                    r = await client.get("https://api.openai.com/v1/models",
                                         headers={"Authorization": f"Bearer {key}"})
                if r.status_code in (401, 403):
                    return [], "auth"
                if r.status_code != 200:
                    return [], "net"
                ids = [m.get("id", "") for m in (r.json().get("data") or [])]
                return self._rank_openai(ids), "ok"
            if provider == "anthropic":
                async with httpx.AsyncClient(timeout=12.0) as client:
                    r = await client.get("https://api.anthropic.com/v1/models",
                                         headers={"x-api-key": key,
                                                  "anthropic-version": "2023-06-01"})
                if r.status_code in (401, 403):
                    return [], "auth"
                if r.status_code != 200:
                    return [], "net"
                ids = [m.get("id", "") for m in (r.json().get("data") or [])]
                return self._rank_anthropic(ids), "ok"
        except Exception:  # noqa: BLE001
            return [], "net"
        return [], "net"

    @staticmethod
    def _rank_openai(ids):
        chat = [i for i in ids if i and not any(x in i.lower() for x in
                ("embedding", "whisper", "tts", "audio", "dall-e", "image", "moderation",
                 "realtime", "transcribe", "search", "computer-use"))]

        def score(i):
            s, low = 0.0, i.lower()
            m = re.search(r"gpt-(\d+(?:\.\d+)?)", low)
            if m:
                try: s += float(m.group(1)) * 10
                except ValueError: pass
            if low.startswith("o1") or low.startswith("o3") or low.startswith("o4"):
                s += 45          # reasoning line
            if "gpt-4o" in low: s += 40
            if "gpt-4.1" in low: s += 42
            if "mini" in low: s -= 6   # prefer full model as default
            if "nano" in low: s -= 8
            if any(x in low for x in ("preview", "0301", "0314", "instruct")): s -= 5
            return s
        return sorted(dict.fromkeys(chat), key=score, reverse=True)

    @staticmethod
    def _rank_anthropic(ids):
        cand = [i for i in ids if i]

        def score(i):
            s, low = 0.0, i.lower()
            if "opus" in low: s += 30
            if "sonnet" in low: s += 25    # best balance, default
            if "haiku" in low: s += 12
            m = re.search(r"(?:claude-)?(?:.*?-)?(\d+(?:[.-]\d+)?)", low)
            # prefer explicit version families: 4.x/4-x > 3.7 > 3.5 > 3
            for tag, boost in (("-4-", 40), ("-4.", 40), ("sonnet-4", 45), ("opus-4", 48),
                               ("3-7", 30), ("3.7", 30), ("3-5", 20), ("3.5", 20)):
                if tag in low:
                    s += boost
            if "latest" in low: s += 2
            return s
        return sorted(dict.fromkeys(cand), key=score, reverse=True)

    @staticmethod
    def _rank_groq(ids):
        chat = [i for i in ids if i and not any(x in i.lower() for x in
                ("whisper", "tts", "guard", "embed", "embedding", "moderation"))]

        def score(i):
            s, low = 0.0, i.lower()
            if "versatile" in low: s += 100
            if "70b" in low: s += 50
            if "llama-3.3" in low or "llama-3.1" in low: s += 20
            if "llama" in low: s += 10
            if "instant" in low: s -= 5
            m = re.search(r"(\d+(?:\.\d+)?)", low)
            if m:
                try: s += float(m.group(1))
                except ValueError: pass
            return s
        return sorted(dict.fromkeys(chat), key=score, reverse=True)

    @staticmethod
    def _rank_gemini(ids):
        cand = [i for i in ids if i and not any(x in i.lower() for x in
                ("embedding", "aqa", "imagen", "image", "tts", "vision", "learnlm"))]

        def score(i):
            s, low = 0.0, i.lower()
            m = re.search(r"gemini-(\d+(?:\.\d+)?)", low)
            if m:
                try: s += float(m.group(1)) * 10
                except ValueError: pass
            if "flash" in low: s += 6
            if "flash-lite" in low: s -= 2
            if "pro" in low: s += 2
            if low.endswith("-latest"): s += 1
            if any(x in low for x in ("preview", "exp", "-thinking")): s -= 4
            return s
        return sorted(dict.fromkeys(cand), key=score, reverse=True)

    def clear_llm_key(self) -> dict:
        """Disconnect the model — the grounded desk takes over again."""
        prov = (os.getenv("GLASSBOX_LLM_PROVIDER") or "").lower().strip()
        for env_var in self._ENV_FOR.values():
            os.environ.pop(env_var, None)
        os.environ.pop("GLASSBOX_LLM_PROVIDER", None)
        self._persist_env({}, clear=list(self._ENV_FOR.values()) + ["GLASSBOX_LLM_PROVIDER"])
        self._llm_provider_name = None
        return {"ok": True, "disconnected": prov or None}

    async def _verify_key(self, provider: str, key: str):
        """Best-effort check. Returns (True, msg) verified, (False, msg) rejected,
        or (None, msg) couldn't reach the provider — in which case the key is
        still accepted and used on the next question."""
        try:
            import httpx
            if provider == "groq":
                async with httpx.AsyncClient(timeout=12.0) as client:
                    r = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}",
                                 "content-type": "application/json"},
                        json={"model": self._DEFAULT_MODEL["groq"], "max_tokens": 1,
                              "messages": [{"role": "user", "content": "ping"}]},
                    )
                if r.status_code == 200:
                    return True, "Model connected and verified (Groq)."
                if r.status_code in (401, 403):
                    return False, "That key was rejected — check you copied all of it."
                if r.status_code == 429:
                    return True, "Connected (the free tier is rate-limited right now, but the key is valid)."
                return None, f"Saved, but Groq returned {r.status_code}. It'll be tried on your next question."
            else:  # gemini — try current models; 1.5 is EOL and 404s
                async with httpx.AsyncClient(timeout=12.0) as client:
                    last = None
                    for gm in self._GEMINI_FALLBACKS:
                        r = await client.post(
                            f"https://generativelanguage.googleapis.com/v1beta/models/"
                            f"{gm}:generateContent?key={key}",
                            headers={"x-goog-api-key": key},
                            json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                                  "generationConfig": {"maxOutputTokens": 1}},
                        )
                        last = r
                        if r.status_code == 200:
                            os.environ["GLASSBOX_LLM_MODEL"] = gm  # pin the one that works
                            return True, f"Model connected and verified ({gm})."
                        if r.status_code in (401, 403):
                            return False, ("That key was rejected by Google. If it starts with "
                                           "\"AQ.\", note some accounts have those blocked — try a "
                                           "Groq key instead (console.groq.com).")
                        if r.status_code == 429:
                            os.environ["GLASSBOX_LLM_MODEL"] = gm
                            return True, f"Connected ({gm}); the free tier is rate-limited right now."
                        # 404 → model retired, try the next one
                    code = last.status_code if last is not None else "?"
                    return None, (f"Saved, but Google returned {code} for the current models. "
                                  f"It'll be retried on your next question.")
        except Exception:  # noqa: BLE001
            return None, "Saved, but couldn't reach the provider to verify (network?). It'll be used on your next question."

    def _persist_env(self, updates: dict, clear: list | None = None) -> bool:
        """Upsert KEY=VALUE lines into a .env in the project root. Best-effort."""
        try:
            from pathlib import Path
            path = Path(__file__).resolve().parents[2] / ".env"
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
            keys_to_touch = set(updates) | set(clear or [])
            kept = []
            for ln in lines:
                head = ln.split("=", 1)[0].strip() if "=" in ln else ""
                if head in keys_to_touch:
                    continue  # drop old lines for keys we're setting/clearing
                kept.append(ln)
            for k, v in updates.items():
                kept.append(f"{k}={v}")
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _llm_narrate(self, question, briefing, intent, history, granularity, lang=None) -> str | None:
        """
        When a provider key is configured, have a model answer the question using
        the grounded FACTS and the conversation. It is told to use only those
        facts and to match its length to the question — a short follow-up gets a
        short reply, not the whole briefing again. Any failure (no key, network,
        bad response) returns None so the grounded desk answers instead.
        """
        cfg = self._llm_config()
        if not cfg:
            return None
        tag, key, model = cfg
        try:
            import json
            import httpx

            facts = json.dumps(briefing, default=str)[:6500]
            convo = [{"role": h.get("role"), "content": h.get("content")}
                     for h in (history or [])[-6:] if h.get("content")]
            length_hint = {
                "full": "The user wants a full read: give a complete but readable analysis.",
                "verdict": "The user asked a yes/no or should-I question: answer directly in 1-3 sentences.",
                "why": "The user asked why: explain the reasoning concisely; do not re-list price, levels or sizing.",
                "risk": "The user asked about risk: focus on risk in a few sentences.",
                "levels": "The user asked about levels: give support, resistance and a stop in a few sentences.",
                "metric": "The user asked about one specific metric: answer just that, briefly.",
                "recap": "This is a follow-up on the pair already in play: reply briefly, do not repeat the whole briefing.",
            }.get(granularity, "Match your length to the question.")
            system = (
                "You are GlassBox's trading analyst desk — a sharp, plain-spoken crypto analyst "
                "inside a risk-first trading agent. You are given a FACTS object: real numbers just "
                "computed from live Binance data and the system's own five-analyst Council, Guardian "
                "threat model, technical indicators, derivatives feed and Constitution. Answer the "
                "user's question about the pair using ONLY these facts. Never invent or guess a "
                "number, level, funding rate or signal that is not in FACTS; if something needed is "
                "missing or marked unavailable, say so plainly. Read signals against each other rather "
                "than listing them. Be concrete and honest, never hype, and never give financial "
                "advice as a guarantee. " + length_hint
            )
            if lang and lang.lower() not in ("english", "en"):
                system += (f" Write your entire reply in {lang}. Keep ticker symbols, numbers and "
                           f"units as-is; translate only the words.")
            user = (f"FACTS (live, just computed):\n{facts}\n\n"
                    f"User question: {question}")

            if tag in ("groq", "openai", "openrouter"):
                url = {
                    "groq": "https://api.groq.com/openai/v1/chat/completions",
                    "openai": "https://api.openai.com/v1/chat/completions",
                    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
                }[tag]
                messages = [{"role": "system", "content": system}] + convo + \
                           [{"role": "user", "content": user}]
                headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
                # If the pinned model has been retired/renamed, fall back to the
                # provider's live model list rather than failing.
                listed = (self._models_cache.get(tag) or (0, []))[1] if tag == "groq" else []
                cand = list(dict.fromkeys([model] + listed))
                async with httpx.AsyncClient(timeout=22.0) as client:
                    for cm in cand:
                        payload = {"model": cm, "max_tokens": 900, "temperature": 0.4,
                                   "messages": messages}
                        resp = await client.post(url, headers=headers, json=payload)
                        if resp.status_code in (400, 404) and cm != cand[-1]:
                            continue  # bad model name — try the next
                        if resp.status_code != 200:
                            return None
                        if cm != model:
                            os.environ["GLASSBOX_LLM_MODEL"] = cm  # pin the one that worked
                        return (resp.json()["choices"][0]["message"]["content"] or "").strip() or None
                return None

            if tag == "anthropic":
                messages = convo + [{"role": "user", "content": user}]
                async with httpx.AsyncClient(timeout=22.0) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"},
                        json={"model": model, "max_tokens": 900, "system": system,
                              "messages": messages},
                    )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                return "".join(blk.get("text", "") for blk in data.get("content", [])
                               if blk.get("type") == "text").strip() or None

            if tag == "gemini":
                # Gemini folds the system prompt into system_instruction and uses
                # its own role names. Gemini 1.5 is EOL (404), so try current
                # models in order; the key travels as ?key= (correct for both the
                # classic AIza and the newer AQ. key formats on the native API).
                contents = []
                for m in convo:
                    contents.append({"role": "user" if m["role"] != "assistant" else "model",
                                     "parts": [{"text": m["content"]}]})
                contents.append({"role": "user", "parts": [{"text": user}]})
                payload = {"system_instruction": {"parts": [{"text": system}]},
                           "contents": contents,
                           "generationConfig": {"maxOutputTokens": 900, "temperature": 0.4}}
                chosen = os.getenv("GLASSBOX_LLM_MODEL")
                listed = (self._models_cache.get("gemini") or (0, []))[1]
                models = list(dict.fromkeys(
                    ([chosen] if chosen else []) + list(self._GEMINI_FALLBACKS) + listed))
                async with httpx.AsyncClient(timeout=22.0) as client:
                    for gm in models:
                        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                               f"{gm}:generateContent?key={key}")
                        resp = await client.post(url, json=payload,
                                                 headers={"x-goog-api-key": key})
                        if resp.status_code == 404:
                            continue  # model retired — try the next
                        if resp.status_code != 200:
                            return None
                        if gm != chosen:
                            os.environ["GLASSBOX_LLM_MODEL"] = gm  # pin the one that worked
                        data = resp.json()
                        cand = (data.get("candidates") or [{}])[0]
                        txt = "".join(p.get("text", "") for p in
                                      (cand.get("content", {}).get("parts") or [])).strip()
                        return txt or None
                return None

            return None
        except Exception:  # noqa: BLE001 — never let the model path break chat
            return None

    _ENV_LOADED = False

    def _load_env_once(self):
        """Best-effort: load KEY=VALUE lines from a .env in the project root into
        the environment (without overwriting anything already set), so a user can
        drop their key in a file instead of exporting a variable. Runs once."""
        if AnalystDesk._ENV_LOADED:
            return
        AnalystDesk._ENV_LOADED = True
        try:
            from pathlib import Path
            here = Path(__file__).resolve()
            candidates = [here.parents[2] / ".env", Path.cwd() / ".env"]
            for path in candidates:
                if not path.is_file():
                    continue
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
                break
        except Exception:  # noqa: BLE001
            pass

    # -- helpers ----------------------------------------------------------- #

    def _known_symbols(self) -> set[str] | None:
        """The exchange's spot symbols, if the feeds client has them cached."""
        for attr in ("symbols_cache", "_symbols", "known_symbols"):
            v = getattr(self.engine.feeds, attr, None)
            if isinstance(v, (set, list, tuple)) and v:
                return set(v)
        return None  # None means "don't gate on membership" — resolution stays permissive

    def _last_symbol(self, history: list[dict]) -> str | None:
        # Only the user's own turns — the assistant's prose is full of capitalised
        # words ("Council", "Funding") that would otherwise resolve as tickers.
        for h in reversed(history or []):
            if h.get("role") and h.get("role") != "user":
                continue
            s = resolve_symbol(h.get("content", ""), known=None, fallback=None)
            if s:
                return s
        return None


# --------------------------------------------------------------------------- #
#  module-level tiny helpers
# --------------------------------------------------------------------------- #

def _flt(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _maybe(x, fn):
    v = _flt(x)
    return fn(v) if v is not None else None


def _price(p: float) -> str:
    if p is None:
        return "n/a"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    return f"${p:.6f}".rstrip("0").rstrip(".")


def _usd(v: float) -> str:
    return f"${v:,.0f}" if v is not None else "n/a"


def _first_reason(cp: dict) -> str:
    reasons = cp.get("reasons") or []
    return reasons[0] if reasons else ""
