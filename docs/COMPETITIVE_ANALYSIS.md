# GlassBox — Competitive Analysis (Binance Agent OS Hackathon, Track A)

Three peer submissions were reviewed feature-by-feature against GlassBox. The
goal was not to copy but to find ideas GlassBox genuinely lacked, then implement
a stronger, audited version.

## The field

| Submission | Shape | Core idea |
|---|---|---|
| **favorian1/binance-sentinel-os** | 9 Markdown skills for Antigravity/Claude/Cursor over the Binance MCP | Exchange-native order validation; liquidation-distance guardian; TWAP whale slicing; Binance AI token report |
| **smartvibeio/smartvibe-agent-os** | Local TS coach ("doppelganger") | Information-isolation drills (hide the future, lock the call, reveal); decision-first feedback; sample-size honesty |
| **mjxxbt/aether** | Deterministic TS core + Next.js dashboard | Confirm-before-execute audit log; adaptive strategy weighting with **auto-pause after 3 losses**; delta-neutral legs; prediction markets |

## Feature-by-feature vs GlassBox

| Competitor feature | Already in GlassBox? | Where / how it compares |
|---|---|---|
| Exchange-native filters (LOT_SIZE, tickSize, MIN_NOTIONAL) | **Yes, stronger** | `precision.py`: exact `Decimal` grid snapping, quantity rounded **down only**, below-minimum orders refused rather than bumped up |
| Performance-weighted analysts | **Yes, stronger** | `calibration.py`: Brier-skill weighting vs a 0.25 baseline, not Aether's naive ±0.05/0.08 |
| Sample-size honesty | **Yes** | `MIN_SAMPLES_FOR_WEIGHT = 20`; a lucky call cannot move the sizer |
| Hindsight-free grading | **Yes** | Calls graded forward after a horizon; live-only, so an abstention while blind is never punished |
| Confirm-before-execute + durable audit log | **Yes** | Intents → Constitution → Guardian veto → signed, hash-chained ledger with anchor checkpoints |
| Auto-pause a losing strategy/analyst | **No → adopted** | See below |
| Information-isolation drills | No → recommended next | See roadmap |
| Delta-neutral legs, prediction markets, liquidation auto-deleverage | Out of scope | GlassBox is spot-first and paper-default |

## What was adopted this round — the analyst circuit breaker

Aether auto-pauses a strategy after three losses and resets it on command.
GlassBox's soft Brier down-weighting is deliberately slow and, by design, never
removes a vote entirely (the reliability floor is 0.25), so a hot-wrong analyst
keeps voting. That is the real gap. GlassBox now adds a circuit breaker on top of
the existing calibration:

- A run of consecutive wrong **live** graded calls (`BENCH_MISS_STREAK = 3`)
  benches an analyst: its vote weight drops to zero and it is fully sidelined
  from the Council, which already sizes each vote as `confidence × reliability`.
- Only hindsight-free live calls count, so an analyst is never benched for a call
  it declined to make while a feed was down.
- Reinstatement is **evidence-based, not a manual reset**: the benched analyst
  keeps being graded in the background and returns once it strings together
  `RESUME_HIT_STREAK = 2` correct calls, re-entering at a probationary weight that
  ordinary Brier calibration then governs.
- Every bench and reinstatement is a first-class, signed **ledger event**
  (`analyst_benched` / `analyst_reinstated`) and is surfaced in the Track Record
  tab, localised in all eight languages.

This is stronger than the source idea on three axes: it sits on principled Brier
calibration rather than fixed step sizes, it self-heals on evidence rather than
waiting for a human reset, and it is fully auditable.

Implementation: `calibration.py` (state, streak logic, persistence), `engine.py`
(ledger events + operator log), `frontend/app.js` (Track Record highlighting),
`i18n.js` (statuses + two patterns). Covered by
`test_analyst_circuit_breaker_benches_and_reinstates` and
`test_circuit_breaker_ignores_blind_calls_and_non_streaks`.

## Recommended next (designed, not yet built)

- **Blind drill (from SmartVibe), audited GlassBox-style.** Pick a real Binance
  historical window; feed the Council only the pre-decision candles; lock its
  verdict; reveal the hidden candles and grade with the same deadband/Brier
  logic; feed the clean, hindsight-free sample straight into calibration and log
  the drill to the ledger. This turns the 30-minute live grading horizon into an
  on-demand way to build trust and demonstrate the no-hindsight discipline.
- **TWAP slicing preview (from Sentinel-OS).** For any proposed order past a
  depth-based impact threshold, show the sliced schedule in the trade receipt.
- **Binance AI token report** (`analysis.getTokenAiReport`) as an additional,
  clearly-labelled input to the sentiment analyst when the live MCP is connected.
