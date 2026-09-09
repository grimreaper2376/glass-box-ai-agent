"""
GlassBox — calibration.

The original ledger proved *integrity*: that nobody edited the record. It could
not prove the reasoning was any good. This module closes that gap.

Every signal an analyst emits is recorded with the price at the time and a
horizon. When the horizon elapses, the call is graded against what actually
happened, and each analyst accumulates a track record:

  hit rate     how often a directional call was right
  Brier score  how well-calibrated the confidence was, not just the direction
  edge         average forward return in the direction called, in basis points

Brier score is the important one. An analyst that says "bullish, 90% confident"
and is right 55% of the time is *worse* than one that says "bullish, 55%
confident" and is right 55% of the time, even though their hit rates are
identical. The first one is lying to the position sizer.

Reliability feeds back into the Council: an analyst that has been consistently
wrong has its vote down-weighted automatically, without anyone editing code.
This makes the system get better at knowing which of its own voices to trust —
which is the thing an audit trail alone cannot give you.

Scores are only recorded for signals made with `data_quality == "live"`. An
analyst is never penalised for a call it made while a feed was down, because it
abstained rather than guessed.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# A new analyst starts at neutral reliability and needs this many graded calls
# before its score is allowed to move the position sizer at all. Without it, one
# lucky call would hand an analyst a 100% hit rate and double weight.
MIN_SAMPLES_FOR_WEIGHT = 20

# Circuit breaker. Soft Brier down-weighting is deliberately slow: it needs many
# graded calls to move, and by design it never removes a vote entirely (the
# reliability floor is 0.25). A run of consecutive wrong *live* calls is a
# different signal — the analyst is not merely miscalibrated over the long run,
# it is actively misreading the regime right now. When that happens the analyst
# is benched and its vote drops to zero until it earns its way back. Only
# hindsight-free live calls count, so an analyst is never benched for a call it
# declined to make while a feed was down. Reinstatement is automatic and
# evidence-based rather than a manual reset: the benched analyst keeps being
# graded in the background and returns the moment it strings together enough
# correct calls again. Every bench and every reinstatement is written to the
# ledger as a first-class event.
BENCH_MISS_STREAK = 3   # consecutive wrong live calls that bench an analyst
RESUME_HIT_STREAK = 2   # consecutive correct calls while benched that reinstate it


@dataclass
class OpenCall:
    analyst: str
    symbol: str
    stance: str
    confidence: float
    entry_price: float
    made_ts: float
    horizon_seconds: float
    ledger_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GradedCall:
    analyst: str
    symbol: str
    stance: str
    confidence: float
    entry_price: float
    exit_price: float
    forward_return_bps: float
    correct: bool
    brier: float
    made_ts: float
    graded_ts: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CalibrationTracker:
    """Grades analyst calls against realised price and maintains reliability."""

    # A move smaller than this is noise, not a correct call. Without a deadband
    # a coin-flip on a flat tape scores ~50% and looks like skill.
    DEADBAND_BPS = 8.0

    def __init__(self, horizon_seconds: float = 1800.0, path: Path | None = None):
        self.horizon_seconds = horizon_seconds
        self.path = path
        self.open_calls: list[OpenCall] = []
        self.graded: dict[str, deque[GradedCall]] = defaultdict(
            lambda: deque(maxlen=500)
        )
        # Circuit-breaker state: analyst -> {since_ts, miss_streak, reason}.
        # An analyst present in this map is benched (reliability 0).
        self.bench: dict[str, dict[str, Any]] = {}
        # Bench/reinstate events produced by the most recent grade_due call, for
        # the engine to write to the ledger. Reset on every grade_due.
        self.last_circuit_events: list[dict[str, Any]] = []
        if path and Path(path).exists():
            self.load()

    # -- recording ---------------------------------------------------------

    def record(self, signals, prices: dict[str, float], ledger_seq: int | None = None) -> int:
        """Log directional calls made on live data. Returns how many were logged."""
        n = 0
        for s in signals:
            if s.stance == "neutral" or s.confidence <= 0:
                continue
            if s.evidence.get("data_quality") != "live":
                continue  # never grade a call made while blind
            price = prices.get(s.symbol, 0.0)
            if price <= 0:
                continue
            self.open_calls.append(
                OpenCall(
                    analyst=s.agent, symbol=s.symbol, stance=s.stance,
                    confidence=s.confidence, entry_price=price,
                    made_ts=time.time(), horizon_seconds=self.horizon_seconds,
                    ledger_seq=ledger_seq,
                )
            )
            n += 1
        # Bound memory if the engine runs for weeks without grading.
        if len(self.open_calls) > 20_000:
            self.open_calls = self.open_calls[-10_000:]
        return n

    # -- grading -----------------------------------------------------------

    def grade_due(self, prices: dict[str, float], now: float | None = None) -> list[GradedCall]:
        """Grade every call whose horizon has elapsed and we have a price for."""
        now = now or time.time()
        still_open: list[OpenCall] = []
        newly: list[GradedCall] = []

        for call in self.open_calls:
            if now - call.made_ts < call.horizon_seconds:
                still_open.append(call)
                continue
            exit_price = prices.get(call.symbol, 0.0)
            if exit_price <= 0:
                still_open.append(call)  # keep waiting for a price
                continue

            ret_bps = (exit_price / call.entry_price - 1) * 10000
            directional = ret_bps if call.stance == "bullish" else -ret_bps

            if abs(ret_bps) < self.DEADBAND_BPS:
                correct = False  # flat is not a win for a directional call
                outcome = 0.5
            else:
                correct = directional > 0
                outcome = 1.0 if correct else 0.0

            # Brier: squared error between stated confidence and what happened.
            brier = (call.confidence - outcome) ** 2

            g = GradedCall(
                analyst=call.analyst, symbol=call.symbol, stance=call.stance,
                confidence=call.confidence, entry_price=call.entry_price,
                exit_price=exit_price, forward_return_bps=round(directional, 2),
                correct=correct, brier=round(brier, 4),
                made_ts=call.made_ts, graded_ts=now,
            )
            self.graded[call.analyst].append(g)
            newly.append(g)

        self.open_calls = still_open
        self.last_circuit_events = (
            self._apply_circuit_breaker({g.analyst for g in newly}, now)
            if newly else []
        )
        if newly and self.path:
            self.save()
        return newly

    # -- circuit breaker ---------------------------------------------------

    @staticmethod
    def _trailing_streaks(calls: list[GradedCall]) -> tuple[int, int]:
        """(consecutive misses, consecutive hits) counting back from the latest
        graded call. At most one of the two is non-zero."""
        miss = hit = 0
        for c in reversed(calls):
            if c.correct:
                if miss:
                    break
                hit += 1
            else:
                if hit:
                    break
                miss += 1
        return miss, hit

    def _apply_circuit_breaker(self, analysts, now: float) -> list[dict[str, Any]]:
        """Bench an analyst on a fresh run of wrong live calls; reinstate one
        that has strung together enough correct calls again. Returns the
        transitions so the caller can record them to the ledger."""
        events: list[dict[str, Any]] = []
        for analyst in analysts:
            calls = list(self.graded.get(analyst, []))
            miss, hit = self._trailing_streaks(calls)
            benched = analyst in self.bench
            if not benched and miss >= BENCH_MISS_STREAK:
                self.bench[analyst] = {
                    "since_ts": now,
                    "miss_streak": miss,
                    "reason": f"{miss} live calls wrong in a row",
                }
                events.append({
                    "type": "analyst_benched", "analyst": analyst,
                    "miss_streak": miss, "ts": now,
                })
            elif benched and hit >= RESUME_HIT_STREAK:
                info = self.bench.pop(analyst)
                events.append({
                    "type": "analyst_reinstated", "analyst": analyst,
                    "hit_streak": hit,
                    "benched_seconds": round(now - info.get("since_ts", now)),
                    "ts": now,
                })
        return events

    # -- scoring -----------------------------------------------------------

    def score(self, analyst: str) -> dict[str, Any]:
        calls = list(self.graded.get(analyst, []))
        if not calls:
            return {
                "analyst": analyst, "samples": 0, "hit_rate": None,
                "brier": None, "edge_bps": None, "reliability": 1.0,
                "status": "no track record yet", "benched": False,
                "miss_streak": 0, "hit_streak": 0,
            }

        hits = sum(1 for c in calls if c.correct)
        hit_rate = hits / len(calls)
        brier = statistics.fmean(c.brier for c in calls)
        edge = statistics.fmean(c.forward_return_bps for c in calls)

        # Reliability multiplies the analyst's vote. Anchored at 1.0 and moved
        # by Brier skill relative to an uninformed 0.25 baseline, then blended
        # toward 1.0 until there is enough evidence to justify moving at all.
        skill = (0.25 - brier) / 0.25  # +1 perfect, 0 uninformed, negative worse
        raw = max(0.25, min(1.75, 1.0 + skill * 0.75))
        confidence_in_score = min(len(calls) / MIN_SAMPLES_FOR_WEIGHT, 1.0)
        reliability = 1.0 + (raw - 1.0) * confidence_in_score

        if len(calls) < MIN_SAMPLES_FOR_WEIGHT:
            status = f"warming up ({len(calls)}/{MIN_SAMPLES_FOR_WEIGHT} calls)"
        elif reliability > 1.15:
            status = "outperforming"
        elif reliability < 0.85:
            status = "underperforming, vote down-weighted"
        else:
            status = "in line with baseline"

        miss_streak, hit_streak = self._trailing_streaks(calls)
        benched = analyst in self.bench
        if benched:
            # A benched analyst is out of the vote entirely, regardless of what
            # its long-run Brier reliability would otherwise be.
            reliability = 0.0
            wrong = self.bench[analyst].get("miss_streak", miss_streak)
            status = f"benched — {wrong} live calls wrong in a row"

        return {
            "analyst": analyst,
            "samples": len(calls),
            "hit_rate": round(hit_rate, 3),
            "brier": round(brier, 4),
            "edge_bps": round(edge, 2),
            "reliability": round(reliability, 3),
            "status": status,
            "benched": benched,
            "miss_streak": miss_streak,
            "hit_streak": hit_streak,
            "recent": [c.to_dict() for c in calls[-8:]],
        }

    def reliability(self, analyst: str) -> float:
        return self.score(analyst)["reliability"]

    def summary(self, analysts: list[str] | None = None) -> dict[str, Any]:
        names = analysts or sorted(self.graded)
        scores = [self.score(n) for n in names]
        graded_total = sum(s["samples"] for s in scores)
        rated = [s for s in scores if s["samples"] >= MIN_SAMPLES_FOR_WEIGHT]
        return {
            "horizon_minutes": round(self.horizon_seconds / 60, 1),
            "open_calls": len(self.open_calls),
            "graded_calls": graded_total,
            "deadband_bps": self.DEADBAND_BPS,
            "min_samples_for_weight": MIN_SAMPLES_FOR_WEIGHT,
            "analysts": scores,
            "benched": [s["analyst"] for s in scores if s.get("benched")],
            "best": max(rated, key=lambda s: s["reliability"])["analyst"] if rated else None,
            "worst": min(rated, key=lambda s: s["reliability"])["analyst"] if rated else None,
        }

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        if not self.path:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text(
            json.dumps(
                {
                    "horizon_seconds": self.horizon_seconds,
                    "open_calls": [c.to_dict() for c in self.open_calls[-5000:]],
                    "graded": {
                        k: [g.to_dict() for g in v] for k, v in self.graded.items()
                    },
                    "bench": self.bench,
                },
                default=str,
            ),
            encoding="utf-8",
        )

    def load(self) -> None:
        try:
            d = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.horizon_seconds = d.get("horizon_seconds", self.horizon_seconds)
        self.open_calls = [OpenCall(**c) for c in d.get("open_calls", [])]
        for name, calls in d.get("graded", {}).items():
            self.graded[name] = deque(
                (GradedCall(**c) for c in calls), maxlen=500
            )
        self.bench = d.get("bench", {})
