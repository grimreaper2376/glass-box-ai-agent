# Windows verification checklist

A tick-box run-through for confirming GlassBox actually works on your machine,
feature by feature. Pairs with [FEATURE_CHECKLIST.md](FEATURE_CHECKLIST.md),
which explains *why* each check matters; this document is just the literal
sequence of clicks and the exact thing to look for, tuned for Windows paths and
PowerShell.

Total time: about 25 minutes if you go through everything once.

---

## Part A — Install and boot (2 min)

- [ ] Install Python 3.11+ from <https://www.python.org/downloads/windows/>,
      **ticking "Add python.exe to PATH"** on the first installer screen.
- [ ] `cd $HOME\Desktop` then `git clone <your-repo-url> glassbox` (or extract
      a downloaded ZIP there).
- [ ] Open the `glassbox` folder in File Explorer and **double-click
      `START.bat`**.
- [ ] A console window opens and, on first run, installs everything
      automatically — this takes about a minute. Confirm it ends with
      `Setup complete.` and no red error text.
- [ ] Console then shows:
      ```
      GlassBox is running
      Dashboard     http://127.0.0.1:8787
      Mode          paper
      Live data     on — real Binance prices and charts
      Engine        started automatically
      ```
- [ ] Your **browser opens on its own** within a couple of seconds — you did
      not do this yourself. Dashboard loads, dark theme, yellow diamond logo,
      sidebar with 14 tabs.
- [ ] Confirm the top-right button already says **Pause engine**, not
      **Start engine** — the engine auto-started, no click needed.
- [ ] Watch the **Equity** figure top-left, and check the **Pairs** tab —
      real Binance prices should already be moving, with no configuration
      step in between.

**If any of the above fails**, stop here and check
[QUICKSTART_WINDOWS.md](../QUICKSTART_WINDOWS.md)'s troubleshooting section
before continuing — nothing past this point will work if the server itself
won't boot.

---

## Part B — Safety, without touching real money (5 min)

All of Part B is safe to run repeatedly and cannot lose money or corrupt real
data — everything here is either read-only or runs in an isolated disposable
copy.

- [ ] Click **Control Center** in the sidebar.
- [ ] Under **Run the safety drills**, click **Run 12 drills**.
- [ ] Within a few seconds, a green pill reads **12 / 12 passed**, followed by
      12 named checks each with a green checkmark and a one-line detail.
      - Specifically confirm these three lines are present and green:
        "Ledger tampering is detected," "Secrets are redacted before they
        touch disk," "Withdrawal requests are refused outright."
- [ ] Under **Run a simulated stress test**, select **Cascading crash**,
      leave ticks at 400, click **Run backtest**.
- [ ] Result appears inline: a **Net P&L** near flat (single-digit percent
      either way) next to a **Buy & hold benchmark** that's deeply negative
      (typically −60% to −90%), and a positive **Difference**.
- [ ] Under **Backfill the analyst track record**, leave defaults, click
      **Run calibration**.
- [ ] After 10–30 seconds (this downloads real Binance history), a table
      appears showing `technical` and `regime` analysts with a calls count,
      hit rate, Brier score, and weight.
- [ ] Click the **Track record** tab. Confirm the same numbers you just saw
      are now shown there too — proves the button actually updated the live
      system, not just its own preview.

### Try to break the mode-switch safety gate

- [ ] Back in Control Center, click the **Live** mode card.
- [ ] A red banner appears: "Live mode places real orders on Binance with
      real funds." **Do not tick the checkbox yet.**
- [ ] Click **Apply** *without* ticking the confirmation box.
- [ ] Confirm it's refused — a toast/error appears and mode does **not**
      change. This is the one-click-cannot-go-live-by-accident guarantee.
- [ ] Click the **Mock** card to return to a safe mode, click **Apply**.

---

## Part C — The audit ledger (3 min)

- [ ] Click the **Ledger** tab.
- [ ] Click **Verify chain**. Confirm it reports **chain intact** with a
      record count.
- [ ] Open File Explorer, navigate to
      `%USERPROFILE%\.glassbox\data\ledger.jsonl`, open it in Notepad.
- [ ] Change a single character anywhere inside the file (e.g. one digit in
      one timestamp), save.
- [ ] Back in the dashboard, click **Verify chain** again.
- [ ] Confirm it now reports **broken**, and names the exact record number
      that no longer matches, plus every record after it.
- [ ] Under **External anchors**, click **Anchor now**, then
      **Verify anchors** — confirm it shows at least one checkpoint bound to
      a live BTC price.

---

## Part D — Real Binance data, no credentials (2 min)

- [ ] Nothing to enable — live data has been on since Part A. Click the
      **Pairs** tab directly. The **Data health** panel should already show:
      - Websocket: **live**
      - Symbols streaming: several hundred, growing
      - API weight utilisation: roughly 10–20%, "Times throttled: 0"
- [ ] Type `SOL` in the search box. List narrows to a handful of matches in
      real time.
- [ ] Click any `SOLUSDT`-style row. A candlestick chart renders on the
      right with real OHLC bars. Click **4h** and **1d** buttons above the
      chart — it redraws with different data each time.
- [ ] Scroll down in the pair-detail panel — five analysts (`technical`,
      `orderflow`, `derivatives`, `regime`, `liquidity`) each show a live
      stance, confidence percentage, and rationale referencing real numbers
      (RSI value, spread in basis points, etc).
- [ ] Click the **Coins** tab. Confirm it shows ~500 total assets, and that
      clicking on `BTC` (or any coin) shows aggregate volume across *multiple*
      pairs, not just one.
- [ ] Click the **Events** tab. Confirm real headlines appear from at least
      `binance`, `federal_reserve`, `coindesk`, and `cointelegraph` sources,
      each tagged with a category.

---

## Part E — A real MCP client, safely (5 min)

- [ ] Control Center → select the **Mock** mode card, tick live data if not
      already on, click **Apply**.
- [ ] Click the **Binance** tab. A **Connect** button is visible.
- [ ] Click **Connect**. Within a couple of seconds:
      - Session shows **connected**
      - Server shows `binance-mcp-server (mock) 1.0.0`
      - Tools available: **7**
      - A table lists each tool with a human-readable name (e.g.
        "Place spot order") and the raw protocol id underneath in small text
- [ ] Confirm the **Agentic sub-account** panel on the right shows a
      **$5,000.00** USDT starting balance.
- [ ] Let the engine run for a minute or two (with **Start engine** already
      pressed). Watch the **Orders sent through MCP** table at the bottom —
      as trades occur, rows appear showing `filled`, a side, a pair, a fill
      price, a latency in milliseconds, an order id, and a justification
      hash.
- [ ] Click any row's justification hash, cross-reference it against the
      **Ledger** tab — the same hash should appear as a `decision` record
      with the full reasoning that led to that specific order.

### Optional: authorize your real Binance account

**Only proceed if you understand this step is about connecting a real
account — no orders are placed without also switching to Live mode and
confirming, per Part B.**

- [ ] Control Center → select **Live**, tick the confirmation checkbox,
      click **Apply**.
- [ ] An **Authorize with Binance** panel appears. Click
      **Authorize with Binance**.
- [ ] A new browser tab opens Binance's real login page.
- [ ] After logging in and approving, the tab shows "GlassBox is connected."
- [ ] Back in the original tab, within a couple of seconds the dashboard
      shows **connected** and reads your real Agentic sub-account balance.

If instead you see "No OAuth client id is configured," Binance hasn't issued
you a client-metadata URL yet — see QUICKSTART_WINDOWS.md Part 5 for the
Claude Code fallback.

---

## Part F — Everything else, quickly (2 min)

- [ ] **Council** tab — four-to-five analysts argue per symbol with
      confidence and dissent percentages visible.
- [ ] **Narratives** tab — six themes with strength bars; only some show a
      green "tradable" badge.
- [ ] **Yield** tab — a ranked table of Binance Earn vs. DeFi venues.
- [ ] **Sentinel** tab — click **Crash the whole book**. Threat gauge climbs
      past 70 within a few ticks; **Defensive actions** table populates.
- [ ] **Backtest** tab — set interval to `1d`, bars to `400`, click
      **Run backtest**. A real 13-month-ish window appears with a P&L vs.
      buy-and-hold comparison built from genuine historical candles.
- [ ] **Constitution** tab — shows every rule, its config, and whether it's
      currently `enforced`.
- [ ] Bottom-left, click **Light**. Every tab you've already opened should
      still be legible in the light theme. Click **Dark** to go back.

---

## Part G — Run the automated test suite (2 min)

For completeness, and because eyeballing 15 tabs is not the same guarantee as
an assertion:

```powershell
pip install pytest pytest-asyncio
pytest tests -q
```

- [ ] Confirm the final line reads `200 passed` (a handful of seconds to a
      couple of minutes, since two tests make real network calls to
      Binance's OAuth servers).

```powershell
python -m glassbox drill
```

- [ ] Confirm `12/12 drills passed`, matching what you already saw in the
      Control Center in Part B.

---

## If something doesn't match

Every row in this checklist has a corresponding entry in
[FEATURE_CHECKLIST.md](FEATURE_CHECKLIST.md) with more detail on what the
feature does and why it should behave this way. If a specific number is
wildly different from what's described here (not just "market moved, numbers
differ slightly" but structurally wrong — e.g. 0 drills instead of 12, or an
error instead of a table), that's worth investigating before you rely on the
result, and the architecture doc (`ARCHITECTURE.md`) explains the internals
of whichever component is misbehaving.
