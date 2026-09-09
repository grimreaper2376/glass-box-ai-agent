# Architecture

## Why the order of operations is the design

The tick loop runs in a fixed sequence, and the sequence *is* the safety
argument. Reordering any two steps breaks a guarantee.

```
1. Observe            market data in, written to the ledger
2. Protect            stops and take-profits fire
3. Assess threat      Guardian scores conditions 0-100
4. Guardian override  if critical, hedge intents are built first
5. Propose            Council deliberates, Narrative scans
6. Adjudicate         Constitution rules deterministically
7. Veto               Guardian gets the last look
8. Execute or queue   allowed intents fill; REQUIRE_HUMAN ones wait
9. Route idle cash    Compass parks what is left
10. Receipt           everything above is chained into the ledger
```

**Protection before analysis (2 before 5).** Stops fire before any agent runs. An
agent cannot starve risk management by being slow, expensive or stuck.

**Threat before proposal (3 before 5).** The Guardian's assessment is already
available when the Council speaks, so sizing reacts to conditions rather than
lagging them.

**Hedges before entries (4 before 5).** Defensive intents are built and
adjudicated first. In a scramble, defence is not queued behind offence.

**Rules after reasoning (6 after 5).** The Constitution never sees the model's
argument, only its output. There is no prompt to manipulate.

**Veto after rules (7 after 6).** Two independent gates that fail differently.
The Constitution encodes what you decided in advance; the Guardian reacts to
what you could not have anticipated.

**Receipt last (10).** Nothing is recorded as done until it is done, and the
receipt binds the intent, the verdict, the Guardian's view, the market snapshot
and the outcome into one hashed record.

---

## Components

| File | Responsibility |
|---|---|
| `engine.py` | Tick loop, capital state machine, receipt generation, operator actions |
| `constitution.py` | 17 declarative rules; strictest verdict wins; fails closed |
| `ledger.py` | Append-only JSONL, SHA-256 chain, HMAC signature, `verify()` |
| `redact.py` | Secret scrubbing on every path to disk, socket or log |
| `portfolio.py` | Paper execution with fees and slippage, PnL, protective exits |
| `futures.py` | USDⓈ-M perpetual book: leverage, isolated margin, funding, liquidation |
| `market.py` | `LiveMarket` (public REST) and `SimMarket` (seeded, scriptable) |
| `x402.py` | Metered data spend: budget, per-request cap, provider allowlist |
| `agents/council.py` | Fan-out to analysts, dissent-aware sizing |
| `agents/analysts.py` | Technical, sentiment, on-chain, funding |
| `agents/guardian.py` | Threat model, veto, hedge construction, quarantine |
| `agents/scouts.py` | Narrative detection and yield routing |
| `server.py` | FastAPI, WebSocket broadcast, static hosting, token auth |

---

## The Council's sizing model

Three independent brakes, all multiplicative:

```
size = equity × base_pct
     × conviction_factor    min(conviction × 1.6, 1.4)
     × dissent_factor       max(1 - dissent × 0.8, 0.25)
     × volatility_factor    max(1 - (ATR% - 1.5) × 0.12, 0.35)
```

Conviction is **share of voice**, not an average across seats:

```
conviction = winner_weight / (winner_weight + loser_weight + 0.5 × abstentions)
```

An abstention counts as half a vote against acting, so four silent analysts can
never look like consensus. This matters: the first implementation divided by the
seat count, which made two mild agreements read as 25% conviction and blocked
almost every trade.

Dissent is priced rather than ignored. Two bulls against two bears does not
average to neutral — it produces a smaller position with the disagreement written
into the receipt.

---

## The Guardian's threat model

Five weighted factors, capped at 100:

| Factor | Trigger | Max contribution |
|---|---|---|
| Price velocity | worst move across 2/4/8/16-tick lookbacks below −2% | 62 |
| Volatility expansion | average ATR above 3% | 22 |
| Correlation breakdown | almost everything falling together | 16 |
| Portfolio drawdown | more than 3% below peak | 25 |
| Gross exposure | above 45% of equity | 14 |

Thresholds: 45 elevated, 70 critical, 88 quarantine.

Scanning several lookbacks is deliberate. A single window misses one of the two
dangerous shapes — a one-tick gap rolls out of a long window within a few ticks,
and a slow grind never registers in a short one. The first version used one
6-tick window and scored a 7.5% market-wide gap at 48/100, below its own critical
line.

---

## Time

`Clock` is either the wall clock or a simulated one that advances by the modelled
tick duration.

This exists because time-based rules — cooldowns, rate limits, daily loss windows
— are meaningless if 500 ticks of market action occupy 800 milliseconds of real
time. In the first backtest harness, one losing trade triggered a 30-minute
cooldown that silently blocked the entire remaining run, and the results looked
plausible anyway. Backtests now run at 300 simulated seconds per tick, so 500
ticks is roughly 41 hours of 5-minute bars.

Getting this wrong disables half the Constitution without any error appearing.

---

## Serialization

Every payload bound for a WebSocket frame passes through `_json_safe`, which
converts `inf` and `nan` to `null`.

Python serialises infinity as the bare token `Infinity`, which is not valid JSON.
A single one kills the whole frame, and the browser reports a parse error far
from the cause. This surfaced when `profit_factor` returned `float("inf")` for a
run with no losing trades — the dashboard froze silently while the backend looked
perfectly healthy.

---

## The data layer

Three sources, chosen by cost and freshness rather than convenience.

| Need | Source | Cost |
|---|---|---|
| Price, any symbol | websocket `!miniTicker@arr` | zero request weight |
| Book top, trade tape, watched symbols | multiplexed websocket | zero request weight |
| Order book snapshot | REST `/depth` | 5 weight |
| Candles | REST `/klines`, cached to a quarter of a bar | 2 weight |
| Futures positioning | REST `/fapi`, `/futures/data` | 1 weight each |

Candles are cached for a quarter of a bar because they only change once per bar.
Re-fetching 200 candles every three seconds is pure budget burn for data that has
not moved.

The governor reserves weight *before* each call. Reactive throttling means
discovering the limit by hitting it, and the penalty for hitting it is an IP ban
during a live session.

Streamed quotes are preferred over REST only while fresh. If the socket goes
quiet, `_real_marks()` falls back rather than trading on a frozen number — a
silently stale price is more dangerous than a missing one, because nothing looks
wrong.

## Frontend

Vanilla JavaScript, one CSS file, no build step. A judge clones the repo, runs
one command, and the dashboard is there. Adding a toolchain to a two-day
submission buys nothing and risks a demo failure.

State arrives over a WebSocket; every mutating action is a token-authenticated
POST. Themes are Binance's own palettes, dark and light, persisted to
`localStorage`.
