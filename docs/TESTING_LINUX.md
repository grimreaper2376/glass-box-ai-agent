# Running and testing GlassBox on Linux

Works on any distro with Python 3.11+ — Ubuntu, Debian, Fedora, Arch, WSL2.
Commands below assume a Debian/Ubuntu-family system; adjust the package
manager line for others.

---

## Part 1 — Install Python (2 minutes)

Most distros ship Python 3.11+ already. Check:

```bash
python3 --version
```

If it's older than 3.11 or missing:

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y python3 python3-venv python3-pip

# Fedora
sudo dnf install -y python3 python3-pip

# Arch
sudo pacman -S python python-pip
```

---

## Part 2 — Get GlassBox and run it (one command)

```bash
git clone <your-repo-url> glassbox
cd glassbox
./start.sh
```

That's the whole install. The script checks for Python, creates an isolated
virtual environment the first time it runs, installs everything GlassBox
needs, starts the dashboard with **real Binance market data already on**, and
opens your browser automatically:

```
  GlassBox
  ========

  First time setup — this takes about a minute...
  [... installing packages ...]
  Setup complete.

  Starting GlassBox with live Binance market data...
  Your browser will open automatically in a moment.
  Press Ctrl+C to stop.

══════════════════════════════════════════════════════════════
  GlassBox is running
══════════════════════════════════════════════════════════════
  Dashboard     http://127.0.0.1:8787
  Mode          paper
  Live data     on — real Binance prices and charts
  Engine        started automatically
══════════════════════════════════════════════════════════════
```

By the time the browser tab opens, real Binance prices are already flowing
and the engine is already running — no button to press, no environment
variable to set. Trading itself stays in **paper mode**: fills are simulated,
nothing reaches a real account, no credentials exist anywhere.

**To stop it:** `Ctrl+C` in the terminal. Shutdown is bounded and clean —
verified at roughly 0.2 seconds from signal to process exit, including
properly tearing down the background WebSocket connection to Binance rather
than leaving it running.

**Next time:** run `./start.sh` again. Since everything is already installed,
it skips straight to launching — ready in a few seconds, with zero network
calls for anything already present, so it works even without internet access
(aside from needing it to actually reach Binance).

If `./start.sh` isn't executable (`Permission denied`), run
`chmod +x start.sh` once.

> **Prefer typing every command yourself?** `start.sh` runs exactly the steps
> below — nothing about it is hidden:
> ```bash
> cd backend
> python3 -m venv .venv && source .venv/bin/activate
> pip install -r requirements.txt
> python -m glassbox serve
> ```

---

## Part 3 — Everything that used to be a shell command now has a button

This matters most on Linux servers, containers, and anywhere you'd rather not
juggle terminal windows. Every operation below is also reachable from the
**Control Center** tab in the dashboard — see [Part 5](#part-5--the-control-center-doing-all-of-this-without-a-shell).
The commands here remain useful for scripting, CI, and cron jobs; the
dashboard is the recommended path for everyday use.

### Run the safety drills

```bash
python -m glassbox drill
```

Twelve adversarial checks — oversized orders, unlisted symbols, a runaway data
spend loop, a planted API key, a deliberately corrupted ledger record. Expect
`12/12 drills passed`.

**Safety note:** this command builds its own disposable engine in a temporary
directory (`/tmp/glassbox-test-*`) and never touches `~/.glassbox`. One of the
drills intentionally corrupts a ledger record to prove tamper detection works
— that record lives in the throwaway directory, not your real audit trail.
This is true whether you run it from the shell or click **Run 12 drills** in
the dashboard.

### Backtest on real Binance history

```bash
python -m glassbox replay --interval 1d --bars 400
```

Downloads genuine candles and replays them bar by bar with no look-ahead —
analysts only see candles up to the current bar, and fills use the *next*
bar's open. Try the other windows:

```bash
python -m glassbox replay --interval 4h --bars 1000
python -m glassbox replay --interval 1h --bars 1500
```

### Score the analysts against real outcomes

```bash
python -m glassbox calibrate
```

Grades every analyst call over real history and saves hit rate, Brier score,
and a vote weight to `~/.glassbox/data/calibration.json`. Unlike drills and
scenario backtests, this one *is* meant to update your real installation —
run it once after a fresh checkout so the Track Record tab means something
immediately instead of waiting a day for live grading horizons to elapse.

### Stress-test against the seeded simulator

```bash
python -m glassbox backtest --ticks 500 --scenario crash
python -m glassbox backtest --ticks 500 --scenario bull
python -m glassbox backtest --ticks 500 --scenario bear
python -m glassbox backtest --ticks 500 --scenario chop
```

The crash scenario is the one to watch: buy-and-hold loses roughly 90%+ while
GlassBox finishes near flat because the Guardian quarantines capital. This
also builds an isolated engine — it never touches your real portfolio state.

### Verify the audit chain

```bash
python -m glassbox verify
```

Re-derives every hash from genesis against **your real ledger** (this is the
one command in this list that's supposed to touch it — verifying is
read-only). To prove it detects tampering, edit one character inside any line
of `~/.glassbox/data/ledger.jsonl` and run it again:

```bash
nano ~/.glassbox/data/ledger.jsonl   # or vim, or sed -i, however you like
python -m glassbox verify
```

It names the exact record whose hash no longer matches.

### Rank live USDT pairs by real volume

```bash
python -m glassbox discover --size 10
```

Pulls Binance's full symbol list, ranks by live 24-hour volume, filters out
anything with a spread wider than 8 basis points or under $20M of daily
volume. Add the results to `constitution.yaml` under `symbol_allowlist` to
make them tradable — watching and permission are separate; see Part 4.

---

## Part 4 — Real Binance market data (already on by default)

```bash
./start.sh
```

Live data has been on since Part 2 — nothing further to enable. This uses
Binance's public REST and WebSocket endpoints. No API key, no account
scope, no write path — this is the read-only half of Agent OS and is safe to
run against production from anywhere. Check the **Pairs** tab for the data
health panel: expect the websocket to show `live` with several hundred
symbols streaming within about 20 seconds, and API weight utilisation around
10–20% of budget.

### Connect a real MCP client against the bundled mock server

```bash
python -m glassbox serve --mode mock
```

This runs the *same* `BinanceMCPClient`, the same OAuth-shaped handshake, the
same JSON-RPC over Streamable HTTP, and the same tool discovery as talking to
the real Binance server — just pointed at a local stand-in instead of
`agent.binance.com`. Open the **Binance** tab and click **Connect**; you
should see 7 tools discovered and a $5,000 mock Agentic sub-account. Orders
placed here move real Binance prices through a fake account, so the demo is
honest but nothing is at risk.

### Connect to your real Binance account

```bash
python -m glassbox connect
```

Opens a browser for OAuth 2.1 + PKCE authorization against Binance's real
endpoints. If Binance hasn't issued you a `client_id_metadata_document`, this
prints the Claude Code command that handles it instead:

```bash
claude mcp add binance-mcp-server --transport http https://agent.binance.com/mcp/agentic
```

Once connected, go live:

```bash
GLASSBOX_MODE=live python -m glassbox serve
```

**Before doing this:** fund your Agentic sub-account with an amount you'd be
relaxed about losing entirely, and know where Binance's own emergency stop is
— **Profile → Dashboard → Sub-account → Account Management → Emergency
stop**. That's the authoritative kill switch; GlassBox's own kill switch stops
*GlassBox* from proposing new risk, but it can't cancel orders already placed
on Binance.

---

## Part 5 — The Control Center: doing all of this without a shell

Everything above except installation itself is also a dashboard button. Open
the **Control Center** tab:

| What you'd otherwise type | What to click instead |
|---|---|
| `serve --mode X`, `GLASSBOX_LIVE_DATA=1` | Pick a mode card, toggle live data, **Apply** |
| `connect` | **Authorize with Binance** (appears when Live mode is selected) |
| `drill` | **Run 12 drills** |
| `backtest --scenario X --ticks N` | Pick a scenario, set ticks/equity, **Run backtest** |
| `calibrate --interval X --bars N` | Set interval/bars/horizon, **Run calibration** |
| `verify` | Ledger tab → **Verify chain** |

Switching to **Live** mode requires ticking an explicit confirmation checkbox
before **Apply** does anything — a single click cannot put real money at risk
by accident. Everything you click here calls exactly the same backend code
the CLI commands do, so the two surfaces can never quietly test different
things.

---

## Running the unit tests

```bash
pip install pytest pytest-asyncio
pytest tests -q
```

Expect `200 passed`. These test the same properties as the drills, in
isolation, so a failure points at one component rather than the whole system:
ledger tamper detection, secret redaction, policy rules, rate limiting,
calibration scoring, the OAuth begin/poll mechanics (against Binance's real
discovery endpoints — a fake authorization code is expected to be rejected by
Binance's real token endpoint, which is itself proof the whole chain works),
and — critically — that drills and scenario backtests never touch a real
ledger:

```bash
pytest tests/test_glassbox.py -k "isolated or isolation" -v
```

---

## Settings reference

| Variable | Default | Meaning |
|---|---|---|
| `GLASSBOX_MODE` | `paper` | `paper`, `shadow`, `mock`, `live`, or `bridge` |
| `GLASSBOX_LIVE_DATA` | `1` | Real Binance market data by default; set to `0` for the seeded simulator |
| `GLASSBOX_EQUITY` | `10000` | Starting paper equity in USDT |
| `GLASSBOX_TICK` | `3.0` | Seconds between engine ticks |
| `GLASSBOX_HOME` | `~/.glassbox` | Where the ledger and device key live |
| `GLASSBOX_POLICY` | `constitution.yaml` | Path to your rulebook |
| `GLASSBOX_INTERVAL` | `5m` | Candle size the analysts read |
| `GLASSBOX_MCP_ENDPOINT` | `https://agent.binance.com/mcp/agentic` | MCP server URL |
| `GLASSBOX_MCP_CLIENT_ID` | *(none)* | CIMD URL for OAuth, if Binance issued one |
| `GLASSBOX_ANCHOR_WEBHOOK` | *(none)* | URL to POST ledger checkpoints to |

---

## Troubleshooting

**`python3: command not found`** — install Python via your package manager
(see Part 1).

**`externally-managed-environment` error from pip** — you're installing
outside a venv on a distro that blocks it (Debian 12+, Ubuntu 23.04+). Use the
venv from Part 2; don't `pip install --break-system-packages` for this
project, since an isolated environment is simpler to reason about and to
tear down.

**Port 8787 already in use** — `python -m glassbox serve --port 8899`, or find
what's using it: `lsof -i :8787`.

**Dashboard loads but the websocket badge says "reconnecting"** — check the
terminal running `serve` for the actual error; the frontend reconnects
automatically once the backend is reachable again.

**`GLASSBOX_LIVE_DATA` is on but Pairs tab shows 0 symbols streaming** — outbound
network access to `data-api.binance.vision` or `stream.binance.com` may be
blocked by a firewall or corporate proxy. Check the Data Health panel's
`last_error` field for specifics.

**Want a clean slate** — `rm -rf ~/.glassbox`. Wipes the ledger, device key,
and calibration history. A new signing key is generated on next run, so
previously written ledger records will no longer verify against it — which is
correct behaviour, not a bug: the key identifies *this* installation.
