# Feature checklist — cross-check every single thing yourself

This is the master reference for verifying every capability GlassBox has,
one at a time. Each row gives you three things: what the feature is, the exact
click-path or command to trigger it, and the specific number or behaviour you
should see if it's actually working — not just "it loads."

Numbers shown were captured during a real verification pass on 2026-09-08 and
will drift slightly with live market conditions (that's expected — the
mechanism being verified is "does this respond to real data," not "does it
always show this exact number").

Legend: 🖱 dashboard click-path · ⌨ shell command · both are given where both exist.

---

## 1. Core safety: the Constitution (policy-as-code)

The rulebook in `constitution.yaml`. Deterministic Python, evaluated *after*
the AI has finished reasoning, on every proposed trade.

| Rule | What it does | How to verify |
|---|---|---|
| `kill_switch` | Denies every new intent when engaged | 🖱 Sidebar → **Stop all new risk** → watch Activity feed: every subsequent proposal shows `denied` |
| `symbol_allowlist` | Only listed pairs can be traded | ⌨ `pytest tests -k unlisted` — asserts `SCAMUSDT` is refused |
| `max_position_pct` | Trims oversized orders rather than rejecting outright | 🖱 Control Center → Run drills → "Oversized position is capped" shows the exact before/after dollar amounts |
| `max_portfolio_concentration` | Caps total gross exposure | Constitution tab → shows current setting and whether it's `enforced` |
| `daily_loss_limit` | Halts trading for the session after a loss threshold | Same tab, read the rule's config |
| `max_drawdown` | Circuit-breaker from peak equity | Same |
| `trade_rate_limit` | Caps trades per hour (anti-runaway) | Same |
| `min_confidence` | Refuses trades below a confidence floor | Same |
| `quorum` | Requires N analysts to independently agree | Council tab → watch `dissent` percentage; low-conviction symbols show no trade |
| `spread_guard` | Refuses trades in illiquid/wide-spread conditions | Pairs tab → sort by spread; wide-spread pairs are flagged `not permitted` |
| `volatility_guard` | Cuts size in violent markets | Constitution tab |
| `require_stop_loss` | Every entry must carry a stop | 🖱 Run drills → "Entries without a stop loss are refused" |
| `blackout_windows` | Refuses trading in scheduled macro windows | Constitution tab shows configured UTC windows |
| `cooldown_after_loss` | Anti-revenge-trading delay | Constitution tab |
| `human_confirmation` | Routes large trades to a pending-approval card instead of auto-executing | Cockpit → a confirm card appears above the fold when a large intent is proposed |

**Live edit test:** open `constitution.yaml`, change `max_position_pct` from
20 to 5, save, then Constitution tab → **Reload from file**. Confirm the
change is reflected immediately and a `constitution_change` record with a
before/after diff appears in the Ledger tab.

**Command line, all at once:**
```bash
python -m glassbox drill
```
Expect `12/12 drills passed`. This is the fastest way to check every safety
rule fires correctly in one shot — see §9 for exactly what each drill proves.

---

## 2. The Decision Ledger

Append-only, SHA-256 hash-chained, HMAC-signed audit trail. Every decision —
taken or refused — becomes a record here.

| What to check | How |
|---|---|
| Every trade produces a record | Ledger tab → each row shows `kind: decision` with the full intent, verdict, market snapshot |
| The full reasoning transcript survives, not just the order | Open any receipt on the Cockpit → "Full reasoning and rules applied" → every analyst's stance, confidence, rationale |
| Chain integrity | Ledger tab → **Verify chain** → re-derives every hash from genesis, should say `chain intact` |
| Tamper detection | Edit one character in `~/.glassbox/data/ledger.jsonl` (or `%USERPROFILE%\.glassbox\...` on Windows), save, **Verify chain** again → names the exact broken record |
| Secrets never reach disk | 🖱 Run drills → "Secrets are redacted before they touch disk" plants a fake API key and confirms it never lands in the ledger |
| Device key is 0600 / owner-only | ⌨ `ls -la ~/.glassbox/device.key` (Linux/macOS) — should show `-rw-------` |

⌨ `python -m glassbox verify` re-derives the whole chain from the shell and
prints a JSON report.

---

## 3. External ledger anchoring

Periodically binds the ledger's current head hash to Binance's own server
clock and live BTC/ETH price, via a hash-linked checkpoint chain.

| What to check | How |
|---|---|
| A checkpoint gets created | Ledger tab → **External anchors** panel → shows checkpoint count, the anchored head hash, and the BTC price at that moment |
| Checkpoints are independently verifiable | **Verify anchors** button → re-derives the checkpoint chain and confirms each head existed in the ledger at the time |
| Manual anchor | **Anchor now** button, or ⌨ `POST /api/anchor/create` |

---

## 4. The Council — five real-data analysts

Every analyst reads **live Binance data**, not a simulation, when live mode is
on. Each one can abstain honestly when its required feed is unavailable.

| Analyst | Reads | Where to see it argue |
|---|---|---|
| `technical` | RSI, MACD, Bollinger, ADX, volume skew from real candles | Council tab → per-symbol transcript |
| `orderflow` | Real order-book imbalance within 0.5% of mid, real aggressive trade tape | Same |
| `derivatives` | Funding rate, open interest, long/short ratio from Binance futures | Same — will show `unavailable` and abstain if futures endpoints are geo-blocked from your network, which is honest, not broken |
| `regime` | Volatility regime, BTC correlation and beta | Same |
| `liquidity` | Walks the *real* order book to price slippage at the size intended | Same — never argues *for* a trade, only against one |

**How to verify an analyst is reading real data, not a placeholder:** Pairs
tab → click any symbol → the "What the analysts see right now" panel shows
live numbers (RSI, spread in basis points, funding bps) that change between
page loads as the market moves.

**Dissent-aware sizing:** Council tab shows `conviction` and `dissent_ratio`
per symbol. A split panel produces a smaller position, and the losing
argument is written into both the transcript and the eventual receipt.

---

## 4b. The Analyst Desk — ask about any pair

The Council tab has a desk that runs the same five analysts on any Binance
pair on demand, and a chat panel to discuss it. Everything it says is grounded
in numbers it just computed — it does not improvise.

| What to check | How |
|---|---|
| Analyze a pair the engine doesn't trade | Council tab → "Analyze any pair" → type e.g. `INJUSDT` → **Analyze** → a full verdict card appears (direction, conviction, per-analyst reads, indicators, what the Constitution would allow) |
| Chat about a coin | Council tab → "Ask the desk" → "what do you think of SOL right now?" → a grounded read comes back with a verdict chip |
| It resolves names, tickers and pairs | "bitcoin", "$inj", "SOL/USDT" and "ETHUSDT" all resolve to the right pair (`test_desk_resolves_pairs_from_free_text`) |
| Intent is read correctly | "key support for ETH" is treated as a levels question, not mistaken for a comparison because of the word "for" (`test_desk_classifies_intent_without_false_compare`) |
| Every figure is real | The answer cites the actual RSI, funding and sizing from the briefing and asserts nothing that isn't in it (`test_desk_compose_is_grounded_in_the_briefing`) |
| It reads signals together | An overbought RSI pressed into the 24h high is called out as an exhaustion setup, not two separate numbers (`test_desk_reads_signals_together_for_divergence`) |
| It degrades honestly | With no market data it explains that cleanly instead of raising (`test_desk_answer_handles_unknown_pair_gracefully`) |
| Follow-ups are conversational, not re-dumps | Ask "what do you think of BTC", then "is it bullish?", "why?", "what about risk?" — each reply is short and about BTC, not the whole briefing again (`test_desk_followup_is_short_not_a_full_redump`, `test_desk_followup_stays_on_pair_and_answers_metric`) |
| Whole-market questions aren't read as tickers | "how is the whole market" and "market sentiments" route to a book-wide read, not WHOLEUSDT / SENTIMENTSUSDT (`test_desk_market_question_is_not_read_as_a_ticker`) |
| Optional LLM brain, free and off by default | Works with no key. Set one of `GROQ_API_KEY` (free, console.groq.com), `GEMINI_API_KEY` (free, aistudio.google.com), `OPENAI_API_KEY` or `OPENROUTER_API_KEY` in `.env` or the shell to make it fully conversational; the model gets the grounded facts, is constrained to them, and falls back to the grounded desk on any error. `GLASSBOX_LLM_MODEL` overrides the model. When a model answers, the chat chip shows "· via groq" |

---

## 5. Track Record (calibration)

Grades every analyst call against what the market actually did, computing
hit rate, Brier score, and a vote-weight multiplier the Council actually uses.

| What to check | How |
|---|---|
| Backfill from real history | 🖱 Control Center → set bars/interval/horizon → **Run calibration** → applies immediately |
| ⌨ equivalent | `python -m glassbox calibrate --interval 1h --bars 400 --horizon 6` |
| Brier score interpretation | Below 0.25 beats an uninformed guess. Track Record tab shows this colour-coded (green/red) |
| Vote weight actually changes sizing | Confirmed by `test_sustained_accuracy_raises_reliability` and `test_sustained_failure_lowers_reliability` in the test suite — an analyst with 40 consistently-wrong calls measurably loses influence |
| Abstained calls are never graded | An analyst that couldn't reach its data feed is not penalised for honestly sitting out — see `test_abstentions_are_never_graded` |
| Live grading | As the engine runs, calls made 30 minutes ago (configurable horizon) get graded automatically each tick — watch "Calls awaiting grade" decrease and "Calls graded so far" increase over time |

---

## 6. The Guardian — threat detection, veto, hedge, quarantine

| What to check | How |
|---|---|
| Threat score computation | Sentinel tab → gauge shows 0–100 with a factor breakdown (price velocity, volatility, correlation, drawdown, exposure) |
| Force a crash on demand | Sentinel tab → set regime to **Cascading crash** → **Crash the whole book** → watch the gauge climb past 70 (critical) within a few ticks |
| Veto power | During a critical event, new Council proposals are denied — Activity feed shows `[guardian] Vetoed` entries |
| Auto-hedge | Guardian builds its own hedge intents at critical urgency, visible in "Defensive actions" panel on the Sentinel tab |
| Quarantine | Above 88/100, the capital-lifecycle bar on Cockpit shows a red QUARANTINED segment and no module can open new risk |
| Recovery | Once the score drops back below the elevated threshold, quarantine lifts automatically — or clear it manually with **Clear quarantine** |

⌨ Prove it without the UI: `python -m glassbox drill` — drills 1, 1a, 1b
specifically assert (a) a 7.5%-per-symbol shock is detected as critical,
(b) exposure is actually cut (by a stop or a hedge), (c) the Guardian vetoes
new risk while critical.

---

## 7. Narrative scout

Detects six themes (AI agents, DePIN, RWA tokenisation, L2 scaling, BTC macro,
DeFi yield/restaking) and only calls a theme "tradable" when it's both above a
strength threshold *and* still accelerating *and* the mapped asset hasn't
already moved more than 6% in 24 hours.

| What to check | How |
|---|---|
| Live strength bars | Narratives tab → six themes with strength % and velocity arrows |
| Tradable badge logic | Only themes meeting all three conditions above show the green "tradable" pill |

---

## 8. Yield Compass

Ranks Binance Earn against DeFi venues (Aave, Curve, Pendle) in one
risk-adjusted calculation, net of gas and round-trip cost.

| What to check | How |
|---|---|
| Cross-venue ranking | Yield tab → table sorted by risk-adjusted APY, chosen venue marked |
| Reserve kept as dry powder | "Kept as dry powder" figure — the Compass never deploys 100% of cash |
| Disabled correctly in MCP modes | In `mock`/`live` mode, "Deployed" shows $0.00 — yield routing is intentionally switched off because Binance holds the real funds, and locally simulating a yield position would make local equity diverge from the actual exchange balance |

---

## 9. x402 metered data spend

Every simulated paid data request is budgeted, capped per-request, restricted
to an allowed-provider list, and logged like a trade.

| What to check | How |
|---|---|
| Budget enforcement | Yield tab → "Data spending" panel shows daily budget used vs. cap |
| Cannot be overspent | 🖱 Run drills → "x402 data budget is enforced" — the drill deliberately tries 10,000 purchases and confirms the budget stops it |
| Attribution | Each purchase is tagged with the analyst that requested it |
| TTL caching prevents burn | The same feed isn't re-bought every tick — confirmed by `data_ttl_seconds` logic in `agents/base.py` |

---

## 10. Real Binance market data (the whole point of this pass)

| What to check | How |
|---|---|
| 1,362 total pairs, 500 distinct coins indexed | Coins tab → header shows total asset count |
| 487 USDT pairs live | Pairs tab → header count |
| Search works | Pairs or Coins tab → type "SOL" → list narrows in real time |
| USD-normalised ranking | Coins tab → BTC shows aggregate volume across ~14 pairs, not just `BTCUSDT` alone |
| Real candlestick charts | Pairs tab → click any pair → chart renders with 15m/1h/4h/1d toggles |
| Host failover | `api.binance.com` is geo-blocked in some regions; `feeds.py`'s `_fetch` tries `data-api.binance.vision` and others automatically — check **Data health** panel shows which host is currently serving |
| Live WebSocket streaming | Pairs tab → Data health → "Websocket: live", several hundred symbols streaming, updates sub-second |
| Stale-quote protection | A quote older than 20 seconds is marked stale and the system falls back to REST rather than trading on a frozen price — see `stream.py`'s `STALE_AFTER_SECONDS` |
| Rate-limit governance | Data health panel → API weight utilisation, typically 10–20% because prices are free over the socket; "Times throttled" should read 0 in normal operation |

---

## 11. Event and geopolitical risk

| What to check | How |
|---|---|
| Live headline classification | Events tab → headlines from Binance's own feed, Federal Reserve, CoinDesk, Cointelegraph, tagged by category |
| Binance delisting hard-blocks a symbol | Structural — see `test_binance_delisting_is_the_only_hard_block`; a delisting from a non-Binance source does *not* trigger the same block |
| News can never buy | Events tab explicitly states the policy; structurally enforced — `EventRisk` has no direction field, asserted by `test_news_can_never_produce_a_buy` |
| Fed release opens a blackout | Classified as `macro`, contributes to blackout scoring within a 2-hour window |
| Social/public-figure posts | Deliberately **disabled by default** (`SocialAdapter.enabled == False`) — see §14 for why |

---

## 12. Binance MCP integration — the real client

| What to check | How |
|---|---|
| Real OAuth discovery | `python -m glassbox connect` (or Control Center → Authorize with Binance) fetches Binance's actual `.well-known` endpoints live |
| PKCE + loopback + CSRF | Binance tab → session detail shows protocol version, scope, tool count once connected |
| Mock mode exercises the identical client | `--mode mock` uses the same `BinanceMCPClient` class against a bundled local server — same JSON-RPC, same error handling |
| Tools discovered live, not hardcoded | Binance tab → "Tools Binance exposed to this session" table |
| Human-readable tool names | Same table — `place_spot_order` renders as "Place spot order," raw id kept visible underneath |
| Orders carry their justification hash | Every filled order in the Binance tab shows a `hash` column linking back to the ledger record that justified it |
| Reconciliation against the real sub-account | Every 10 ticks, the local book is compared against Binance's real balance; **Binance wins** on any drift, logged to the ledger as `kind: reconciliation` |
| No withdrawal capability, structurally | 🖱 Run drills → "Withdrawal requests are refused outright" |

---

## 13. Execution resilience — precision, idempotency, price collar, clock drift

Four defensive layers between a decision and an order actually reaching
Binance. None of these decide *whether* to trade — the Constitution and
Guardian already did that — they make sure the *mechanics* of placing the
order are correct once it's been decided.

| What to check | How |
|---|---|
| Exact decimal order sizing | `pytest tests -k precision` — asserts the classic `0.1 + 0.2` float artifact never reaches an order, and that dust orders are refused rather than rounded up |
| Sell orders round to the exchange's real LOT_SIZE | Binance tab → any `SELL` order's quantity should show a clean, exact decimal (e.g. `0.00275`), not a long float tail |
| Idempotent client order ids | Binance tab → orders table's justification-hash column; `pytest tests -k idempoten` proves an identical decision reaches the mock MCP server exactly once even if submitted twice |
| Price collar | `pytest tests -k collar` — proves a decision is refused if the live price has moved past a configurable threshold since the decision was made |
| Clock drift monitor | `/api/status` → `execution.clock` shows live drift in milliseconds against Binance's real server time, checked automatically on the same timer as ledger anchoring |
| All four run against real Binance servers, not simulated | `pytest tests -k "resilience"` includes a live call to `/api/v3/time` |

---

## 14. Control Center — the no-shell operator surface

| What to check | How |
|---|---|
| Mode switching without restart | Control Center → click a mode card → **Apply** → topbar `Session` pill updates immediately |
| Live mode requires explicit confirmation | Selecting **Live** reveals a red banner and a mandatory checkbox; **Apply** is refused (HTTP 400) without it — try it |
| Run drills from a button | **Run 12 drills** → pass/fail list appears inline within a few seconds |
| Run a stress test from a button | Pick a scenario, **Run backtest** → P&L vs. buy-and-hold appears inline |
| Run calibration from a button | **Run calibration** → immediately reflected in the Track Record tab |
| Authorize with Binance, no shell | **Authorize with Binance** → opens Binance's real login in a new tab, polls automatically until connected |
| Isolation proof | `test_drills_never_touch_a_real_ledger` hashes a real ledger before and after a drill run and asserts it's unchanged |

---

## 15. Operator trading desk — spot and futures

| What to check | How |
|---|---|
| Manual spot trade is adjudicated, not bypassed | Cockpit → "Place a trade" → **Spot** → Buy → it runs the second opinion, Constitution and Guardian, and writes a signed receipt — same as an autonomous trade |
| Spot Buy/Sell unchanged | Buy opens a long-only position; Sell exits it — the original behaviour is untouched |
| Futures long and short | Toggle **Futures** → green **Open Long** and red **Open Short** appear, with a leverage selector and a reduce-only **Close** |
| Leverage is treated as risk | The position *notional* (size × leverage) is what every rule sees — a large enough leveraged open still routes to human confirmation |
| Hard leverage ceiling is a denial | `test_futures_leverage_over_cap_is_denied` — leverage above the Constitution cap is refused, not trimmed |
| Opening leveraged risk in a crash is vetoed | The Guardian vetoes a futures open in critical threat exactly as it does a spot entry; a reduce-only close is never blocked |
| Isolated margin caps the loss | `test_futures_liquidation_forfeits_the_whole_margin` — a liquidation loses the margin and no more; nothing is returned |
| Futures P&L settles to the shared cash pool | `test_futures_long_profits_and_close_settles_to_cash` — realised P&L and released margin return to cash, and the daily-loss total sees it |
| One net position per symbol | `test_futures_opposite_side_is_rejected_until_flat` — the opposite side is refused until the position is flat, never silently flipped |

---

## 16. Multi-chain inspector

| What to check | How |
|---|---|
| Ten networks | Payments → inspector dropdown lists Bitcoin, Ethereum, Solana, BNB Chain, Base, Arbitrum, Polygon, Optimism, Avalanche and Base Sepolia (`test_inspect_chains_include_the_major_networks`) |
| Bitcoin address and tx | Paste a `bc1…`/`1…`/`3…` address or a 64-hex txid → it resolves via Blockstream / mempool.space (`test_inspector_classifies_bitcoin_addresses_and_txids`) |
| EVM and Solana | An `0x…` address or a base58 Solana address resolves via that chain's RPC with fallbacks |
| Settlement is honestly scoped | Payment *settlement* stays on Base Sepolia, where it is real ETH/USDC testnet; the inspector is the read-only part that spans chains |

---

## 17. Security properties

| Property | How to verify |
|---|---|
| No credentials ever requested | Nowhere in the UI or CLI does GlassBox ask for a raw Binance API key — only OAuth against the real Agent OS server |
| Session-token gated writes | Try any `POST` endpoint with `curl` and no `X-Glassbox-Token` header → `401` |
| Local-only binding | Server binds `127.0.0.1` — check with `netstat`/`Get-NetTCPConnection`, no `0.0.0.0` listener exists |
| PKCE mandatory, verifier never leaves the process | `test_pkce_challenge_is_s256_of_the_verifier` |
| Redirect is loopback-only | `test_redirect_uri_is_loopback_only` |
| CSRF state-check | `test_oauth_rejects_state_mismatch_before_ever_calling_binance` — deliberately sends a wrong `state` to the callback and confirms refusal |
| Tokens encrypted at rest | `test_tokens_are_not_stored_in_the_clear` |
| Social posts require verified-API evidence, disabled by default | `news.py`'s `SocialAdapter` docstring explains the threat model; `test_social_is_off_by_default` and `test_social_refuses_to_scrape` |

---

## 18. Themes

| What to check | How |
|---|---|
| Dark theme (default) | Binance's own dark palette — `#0B0E11` background, `#FCD535` yellow accent |
| Light theme | Sidebar → **Light** button, bottom-left |
| Persists across reload | Reload the page — your last choice is remembered (`localStorage`) |

---

## Running the automated version of all of the above

```bash
cd backend
pip install pytest pytest-asyncio
pytest tests -q
```

Expect **200 passed**. This runs every property above as an isolated,
repeatable assertion rather than something you have to eyeball — including two
tests that make real network calls to Binance's actual OAuth servers.

```bash
python -m glassbox drill
```

Expect **12/12 drills passed** — the same properties, demonstrated end-to-end
against a live (disposable) engine rather than a unit-level mock.
