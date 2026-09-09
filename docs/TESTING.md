# Reproducing every number

Two ways to run everything below: the shell commands in this document, or the
**Control Center** tab in the dashboard, which calls the exact same backend
functions. Platform-specific setup:

- **Windows** — see [../QUICKSTART_WINDOWS.md](../QUICKSTART_WINDOWS.md).
  Parts 3–5 walk through drills, backtests, calibration, mode switching and
  Binance authorization entirely from the dashboard.
- **Linux / macOS** — see [TESTING_LINUX.md](TESTING_LINUX.md) for the shell
  path, or run `python -m glassbox serve` and use the Control Center exactly
  as the Windows guide describes; the tab looks and works identically on
  every platform.

All shell commands below run from `backend/` with the virtual environment
active.

## Safety drills

```bash
python -m glassbox drill
```

Twelve adversarial drills — the original ten, plus two added when the event-risk
layer and MCP execution surface were built: news can never produce a buy
signal, and the execution surface has no withdrawal capability. Each asserts a
specific safety property rather than describing one. Expect
`12/12 drills passed`.

**This is safe to run against your real installation with no scratch
directory needed.** `testkit.run_drills()` builds its own throwaway engine in
its own temporary directory every time — including for the drill that
deliberately corrupts a ledger record to prove tamper detection works. That
corruption happens to the temp copy, never to `GLASSBOX_HOME`. A dedicated
test (`test_drills_never_touch_a_real_ledger`) asserts this by hashing a real
ledger before and after a drill run and checking it's byte-identical.

## Paper backtests

```bash
python -m glassbox backtest --ticks 500 --scenario crash
```

Scenarios: `calm`, `bull`, `bear`, `chop`, `crash`. Add `--json` for the raw
result object, `--quiet` to suppress progress lines.

Each tick models a 5-minute bar, so 500 ticks is about 41 hours. The simulator is
seeded, so a given scenario reproduces exactly.

### Reference results

Produced by the shipping code. Compared against equal-weight buy-and-hold over
the identical price path.

| Scenario | GlassBox | Buy & hold | Difference | Max DD | Trades | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| crash | −0.28% | −93.55% | +93.27% | 0.28% | 1 | — |
| bear | −4.88% | −30.52% | +25.64% | 4.97% | 62 | 27.4% |
| chop | −4.72% | −8.99% | +4.27% | 5.12% | 72 | 38.9% |
| bull | +17.96% | +36.47% | −18.51% | 11.14% | 28 | 75.0% |
| calm | −0.45% | +0.43% | −0.88% | 1.46% | 15 | 33.3% |

This is a risk-first profile: roughly half the upside, most of the downside
avoided. The underperformance rows are left in deliberately.

The simulator is a seeded random walk, not real history. It exists so the safety
behaviour is reproducible and demonstrable on demand.

## Real historical backtests

```bash
python -m glassbox replay --interval 1d --bars 400
python -m glassbox replay --interval 1h --bars 1500 --symbols BTCUSDT,ETHUSDT
```

Downloads genuine Binance candles and replays them bar by bar. Analysts see only
candles up to the current bar; fills use the next bar's open.

| Window | Interval | GlassBox | Buy & hold | Difference | Max DD | Trades |
|---|---|---:|---:|---:|---:|---:|
| 2025-08-03 → 2026-09-06 | 1d | +1.63% | −21.34% | +22.97% | 5.24% | 6 |
| 2026-03-23 → 2026-09-06 | 4h | +1.74% | +16.06% | −14.31% | 7.21% | 82 |
| 2026-07-05 → 2026-09-06 | 1h | +3.26% | +31.27% | −28.01% | 4.17% | 93 |
| 2026-08-21 → 2026-09-06 | 15m | +1.52% | +9.04% | −7.52% | 4.32% | 45 |

Exact figures will drift as the window rolls forward — these were taken on
2026-09-06. The shape should not: strong in drawdowns, lagging in bull runs.

Order-flow and liquidity analysts abstain during replay because historical order
books are not public, so these runs use three of five analysts.

## Analyst calibration

```bash
python -m glassbox calibrate --interval 1h --bars 500 --horizon 6
```

Grades every analyst call over real history. Reference run, 1,665 graded calls:

| Analyst | Calls | Hit rate | Brier | Edge | Weight |
|---|---:|---:|---:|---:|---:|
| regime | 500 | 45.4% | 0.224 | +16.9 bp | 1.08× |
| technical | 500 | 38.0% | 0.287 | −2.6 bp | 0.89× |

Brier below 0.25 beats an uninformed guess. The technical analyst scoring above
it is a real finding, not a bug: the system detected a weak component and cut its
vote automatically.

## Rate limit and stream behaviour

```bash
GLASSBOX_LIVE_DATA=1 python -m glassbox serve
```

Open **Pairs → Data health**. Expect:

- websocket `live`, several hundred symbols streaming within 20 seconds
- API weight utilisation around 10–20% of the soft ceiling
- throttle events at zero

If utilisation climbs toward 100%, the governor will pause calls rather than
earn a 429. You can watch it happen by lowering the ceiling in
`ratelimit.py` and reloading.

## The Control Center (no shell)

```bash
python -m glassbox serve
```

Open the **Control Center** tab. Every command above has a button:
mode switching (with an explicit confirmation step before going live),
**Run 12 drills**, **Run backtest** (seeded scenarios), **Run calibration**
(real history), and an **Authorize with Binance** flow that opens OAuth in a
new tab and polls for completion — no `glassbox connect` required.

The mode-switch and OAuth mechanics are unit-tested against Binance's real
discovery and token endpoints (a fake authorization code is expected to be
rejected by Binance's real server — that rejection is itself proof the whole
chain, from loopback capture to token exchange, is wired correctly).

## Live data check

```bash
GLASSBOX_LIVE_DATA=1 python -m glassbox serve
```

Open the Markets tab: 487 tradable USDT pairs with live prices, spreads and
volumes. Click any pair to see all five analysts examine it in real time. The
badge in the top bar shows which Binance host is serving data, and whether
futures endpoints are reachable.

## Ledger verification

```bash
python -m glassbox verify
```

To prove it detects tampering, edit one character inside any line of
`~/.glassbox/data/ledger.jsonl` and run it again. It names the record whose hash
no longer matches, and every record after it fails too.

## Live public market data

```bash
GLASSBOX_LIVE_DATA=1 python -m glassbox serve
```

Uses Binance's public REST endpoints. No credentials, no account scope, no write
path. Safe to point at production.

## Manual dashboard checks

With `python -m glassbox serve` running and the engine started:

1. **Capital lifecycle** — the bar shifts as positions open and close.
2. **Council** — all four analysts, their confidence, and the losing arguments.
3. **Sentinel → Crash the whole book** — threat crosses 70 within a few ticks;
   quarantine engages near 88; defensive actions table fills.
4. **Ledger → Verify chain** — re-derives every hash from genesis.
5. **Constitution → Reload from file** — edit `constitution.yaml`, reload, and
   confirm a `constitution_change` record with a before/after diff appears.
6. **Kill switch** — engage it and confirm every subsequent intent is denied.
7. **Theme toggle** — dark and light, persisted across reloads.
