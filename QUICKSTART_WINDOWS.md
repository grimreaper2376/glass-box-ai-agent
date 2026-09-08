# Running GlassBox on Windows — start to finish

Written for someone who has never installed Python. Total time: about 6
minutes, most of which is Python's own installer.

Nothing here touches Binance or any money by default — trading stays in
simulated paper mode until you explicitly go further.

---

## Part 1 — Install Python (3 minutes)

1. Go to <https://www.python.org/downloads/windows/>
2. Download **Python 3.11** or newer (64-bit installer).
3. Run the installer. On the first screen, **tick "Add python.exe to PATH"** at
   the bottom before clicking Install. This is the step everyone misses and it
   causes every "python is not recognized" error later.
4. Click **Install Now**, then **Close**.

That's the only manual step. Everything else is one file.

> **Already have Python?** Skip ahead — but open PowerShell and run
> `python --version` first. If it's older than 3.11, install a newer one
> before continuing; GlassBox won't run on anything earlier.

---

## Part 2 — Get GlassBox and run it (2 minutes, one click)

1. Download the repository: either
   `git clone <your-repo-url> glassbox`, or download the ZIP from GitHub and
   right-click → **Extract All** to your Desktop.
2. Open the `glassbox` folder in File Explorer.
3. **Double-click `START.bat`.**

That's the entire installation. A console window opens and does everything by
itself:

```
  GlassBox
  ========

  First time setup — this takes about a minute...

  [... installing packages ...]

  Setup complete.

  Starting GlassBox with live Binance market data...
  Your browser will open automatically in a moment.
  Close this window (or press Ctrl+C) to stop.

══════════════════════════════════════════════════════════════
  GlassBox is running
══════════════════════════════════════════════════════════════
  Dashboard     http://127.0.0.1:8787
  Mode          paper
  Live data     on — real Binance prices and charts
  Engine        started automatically
══════════════════════════════════════════════════════════════
```

Your default browser opens on its own a moment later, already showing **real
Binance prices**, with the engine **already running** — no button to press,
no environment variable to set. Trading itself stays in paper mode: fills are
simulated, nothing reaches a real Binance account, and no credentials exist
anywhere on your machine.

**To stop it:** close the console window, or click inside it and press
`Ctrl+C`. Windows may ask **"Terminate batch job (Y/N)?"** — type `Y` and
press Enter. (This prompt is `cmd.exe`'s own default behaviour for any batch
script, not something GlassBox added; typing `Y` always stops it immediately.)

**Next time:** double-click `START.bat` again. Since everything is already
installed, it skips straight to launching — ready in a few seconds, and it
makes no network requests for anything already present, so it works even if
you're offline (aside from needing internet to actually reach Binance's data).

> **Prefer typing commands yourself?** See
> [Advanced: manual setup](#advanced-manual-setup) near the end of this guide.
> `START.bat` runs the identical steps; nothing about it is hidden or
> different from what you'd type by hand.

---

## Part 3 — Prove it works, without opening PowerShell again

Everything below used to be a shell command. It isn't any more — it's the

**Control Center** tab in the dashboard you already have open at
<http://127.0.0.1:8787>.

Click **Control Center** in the left sidebar.

### Run the safety drills

Click **Run 12 drills**.

This deliberately tries to break the system — an oversized order, an unlisted
token, a runaway data-spend loop, a planted API key, and a deliberately
corrupted ledger record — and shows you a pass/fail line for each. Expect
**12 / 12 passed**.

This is safe to click at any time, including while the engine is running.
It builds its own disposable copy of GlassBox in a temporary folder and never
touches your real trading history — one of the checks intentionally corrupts
a ledger record to prove tampering is detectable, and that record lives in
the throwaway copy, not yours.

### Run a stress test

Under **Run a simulated stress test**, pick **Cascading crash** from the
dropdown, leave the defaults, and click **Run backtest**.

You'll see the result appear inline: GlassBox's P&L next to what plain
buy-and-hold would have done over the same simulated crash. Buy-and-hold
typically loses 60–90%; GlassBox finishes close to flat because the Guardian
recognises the crash and quarantines capital. Try **Trending bull** and
**Grinding bear** too — the honest result is that GlassBox gives up some
upside in a bull run in exchange for avoiding most of a crash's downside.

### Score the analysts against real history

Under **Backfill the analyst track record**, leave the defaults and click
**Run calibration**.

This downloads real Binance candles, replays them through the analysts, and
grades every call against what actually happened next — hit rate, a Brier
score (whether the analyst's *confidence* was honest, not just its
direction), and a vote weight. Click the **Track record** tab afterwards to
see it reflected there immediately.

### Verify the audit chain

Click the **Ledger** tab, then **Verify chain**. This re-derives every hash
from genesis against your real ledger — it should say **chain intact**.

To prove it catches tampering: open
`C:\Users\<you>\.glassbox\data\ledger.jsonl` in Notepad, change a single
character inside any line, save, and press **Verify chain** again. It names
the exact record that no longer matches.

---

## Part 4 — Drive the running system

With the engine started (top-right **Start engine** button):

| Tab | What to try |
|---|---|
| **Control Center** | Switch mode, run drills, backtests and calibration — see Part 3 |
| **Cockpit** | Watch the capital lifecycle bar shift between IDLE and DEPLOYED |
| **Binance** | See the MCP session, tools discovered from the server, and orders sent |
| **Coins** | All ~500 assets on Binance, volume aggregated across every pair in USD |
| **Pairs** | Search any of 487 USDT pairs; click one for a live candlestick chart and every analyst's opinion; **Data health** panel shows the API weight budget |
| **Events** | Binance announcements, Fed releases and news — risk posture only, never a buy signal |
| **Council** | Read all five analysts arguing, with confidence and dissent |
| **Track record** | Each analyst's hit rate, Brier score and current vote weight |
| **Sentinel** | Set the regime to *Cascading crash*, then press **Crash the whole book** |
| **Backtest** | Run a real-history replay (not simulated) from inside the dashboard |
| **Ledger** | Every hash re-derived from genesis, on demand |
| **Constitution** | See your rules and whether each is currently enforced |
| **Dark / Light** | Bottom-left. Your choice is remembered |

### Editing your rules

Open `glassbox\constitution.yaml` in Notepad, change something — try
`max_position_pct` from `20` down to `5` — save, then press **Reload from
file** on the Constitution page. The change takes effect immediately and is
written to the audit ledger with a before/after diff.

---

## Part 5 — Real Binance data, and a real MCP session, still without a shell

Live Binance market data has been on since `START.bat` first launched — Part
2 already covered this, and there's nothing further to turn on. From here,
"real" means something more specific: an actual MCP session, first against a
safe mock account, then optionally against your real one.

### Connect a real MCP client against a safe mock account

In **Control Center**, select the **Mock** mode card and press **Apply**.
Then open the **Binance** tab — a **Connect** button appears. Click it: you
should see 7 tools discovered and a $5,000 mock Agentic sub-account. This
runs the *exact same* MCP client, the same OAuth-shaped handshake, and the
same JSON-RPC protocol as talking to real Binance — just pointed at a local
stand-in. Orders placed here move real Binance prices through a fake account,
so it's an honest demo with nothing at risk.

### Connect to your real Binance account

**Only do this when you're comfortable with everything above — this step
involves real money.**

In **Control Center**, select the **Live** mode card. A confirmation banner
appears; tick **"I understand this places real orders in my Agentic
sub-account"**, then press **Apply**.

An **Authorize with Binance** panel appears. Click **Authorize with Binance**
— a new browser tab opens Binance's real login. Per Binance's own
documentation: never paste the MCP endpoint into an AI chat and ask it to
install the server, and never open it directly in a browser. This button
does the correct thing for you: it's OAuth 2.1 with PKCE, and the
authorization code never leaves your machine.

> **"No OAuth client id is configured"?** Binance requires a URL to a hosted
> client-metadata document for this. If you haven't set one up, connect once
> through Claude Code instead, which handles it automatically:
> ```powershell
> claude mcp add binance-mcp-server --transport http https://agent.binance.com/mcp/agentic
> ```
> Then re-open the dashboard's Binance tab and press **Connect**.

Once authorized, the dashboard shows your session and reads your real
Agentic sub-account balance.

### Open and fund your Agentic sub-account

Your agent trades inside a dedicated sub-account isolated from your main
account. It starts empty, and **the agent cannot move funds into it** — that
first deposit is always a manual step you take yourself, on Binance.com:

**Binance.com → Profile → Dashboard → Sub-account → Asset Management →
Transfer**

Fund it with an amount you'd be relaxed about losing entirely.

### Where the real emergency stop lives

GlassBox's kill switch (bottom-left, **Stop all new risk**) stops *GlassBox*
from proposing new risk. It cannot cancel orders already placed on Binance.

The authoritative stop is on Binance:

**Profile → Dashboard → Sub-account → Account Management → Emergency stop**

That disconnects every connected agent and cancels all spot, margin and
futures orders in the Agentic account in one step. Know where it is before
you need it.

---

## Advanced: the command line, if you want it

## Advanced: manual setup

`START.bat` runs the exact commands below — nothing about it is hidden, and
this path is here for anyone who'd rather type it themselves, or who's
scripting a scheduled task or CI job:

```powershell
cd $HOME\Desktop\glassbox\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **"running scripts is disabled on this system"?** Only this manual path
> needs it — `START.bat` never touches PowerShell's script policy. Run this
> once, then retry the line above:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

```powershell
pip install -r requirements.txt

python -m glassbox drill                             # same as "Run 12 drills"
python -m glassbox backtest --ticks 500 --scenario crash
python -m glassbox calibrate                         # same as "Run calibration"
python -m glassbox replay --interval 1d --bars 400   # real-history backtest
python -m glassbox verify                            # same as "Verify chain"
python -m glassbox discover --size 10                # rank live USDT pairs
python -m glassbox connect                           # OAuth without the dashboard

# Real Binance market data is already the default. To start with the seeded
# simulator instead:
$env:GLASSBOX_LIVE_DATA="0"; python -m glassbox serve

# Real MCP client against the bundled mock server:
python -m glassbox serve --mode mock

# Real orders in your Agentic sub-account:
$env:GLASSBOX_MODE="live"; python -m glassbox serve
```

To run the unit tests:

```powershell
pip install pytest pytest-asyncio
pytest tests -q
```

Expect `200 passed`.

---

## Settings reference

| Variable | Default | Meaning |
|---|---|---|
| `GLASSBOX_MODE` | `paper` | `paper`, `shadow`, `mock`, `live` or `bridge` |
| `GLASSBOX_LIVE_DATA` | `1` | Real Binance market data by default; set to `0` for the seeded simulator |
| `GLASSBOX_EQUITY` | `10000` | Starting paper equity in USDT |
| `GLASSBOX_TICK` | `3.0` | Seconds between ticks |
| `GLASSBOX_HOME` | `%USERPROFILE%\.glassbox` | Where the ledger and device key live |
| `GLASSBOX_POLICY` | `constitution.yaml` | Path to your rulebook |
| `GLASSBOX_INTERVAL` | `5m` | Candle size the analysts read |
| `GLASSBOX_MCP_CLIENT_ID` | *(none)* | CIMD URL for OAuth |
| `GLASSBOX_ANCHOR_WEBHOOK` | *(none)* | URL to POST ledger checkpoints to |

Set one in PowerShell with `$env:NAME="value"` before running `serve`, or
just use the Control Center instead.

---

## Troubleshooting

**`python` is not recognized** — PATH wasn't set during install. Re-run the
installer, choose Modify, tick "Add python.exe to PATH", reopen PowerShell.

**`Activate.ps1 cannot be loaded`** — run
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once.

**`[Errno 10048] address already in use`** — port 8787 is taken by something
else. Close whatever's using it, or use the
[manual setup](#advanced-manual-setup) path with `--port 8899` added to the
final command.

**Dashboard says "reconnecting"** — the backend stopped. Check the console
window for the error and restart it.

**Dashboard loads but equity never changes** — that's expected in paper mode
during a quiet market; the engine auto-starts and is already running (check
the topbar — it should say **Pause engine**, not **Start engine**). If it
does say **Start engine**, you're most likely in **Live** or **Bridge** mode
— those two never auto-start, on purpose, since starting a live trading loop
the instant a process boots with no further action from you is exactly the
one thing this project is built to never do. Press **Start engine** yourself
once you're ready.

**"No OAuth client id is configured" in Control Center** — see the note under
Part 5's "Connect to your real Binance account" above.

**`pip install` fails behind a corporate proxy** — try
`pip install -r requirements.txt --proxy http://your.proxy:port`

**Want a clean slate** — delete the folder `%USERPROFILE%\.glassbox`. That wipes
the ledger, the device key and all paper history. A new key is generated on next
run, so previously written records will no longer verify against it — which is
the intended behaviour, not a bug.
