"""
GlassBox — US macro regime.

Crypto does not trade in a vacuum. It trades against the price of dollars.
When the front end of the US curve moves, the discount rate on every risky
asset moves with it, and when the Fed is about to speak, the tape gaps.

This module answers a question the news layer cannot: **not "what was said"
but "what is the actual monetary environment, and what is scheduled next."**

Three signals, all from primary government sources
--------------------------------------------------
1. **The level and direction of US rates** — the Fed's own H.15 release,
   which is the authoritative daily series for Treasury yields. A front end
   that has risen sharply in a week is liquidity being withdrawn, and that is
   a headwind for risk assets regardless of what any individual says about it.

2. **The shape of the curve** — 2s10s. An inversion that is deepening is the
   bond market pricing a worsening outlook. This is not a trading signal here;
   it is a reason to carry less risk into an environment where the largest
   market in the world disagrees with the optimism.

3. **Scheduled FOMC meetings** — published months in advance on
   federalreserve.gov.

Why the third one is the interesting one
----------------------------------------
Every other event system in this project is **reactive**: something happens,
the agent responds. A scheduled macro event is the one case where the agent
can know about the risk *before* it arrives, because the calendar is public.

So this layer de-risks **ahead** of an FOMC decision rather than after it —
position sizes shrink as the meeting approaches, and trading pauses entirely
in the window around the statement, when spreads gap and every model in the
market is least reliable. Reacting to a rate decision after the fact is
usually reacting to a 3% candle that has already happened.

Why this is not "trading the news"
-----------------------------------
The same constraint as `news.py` applies, deliberately and for the same
reason: **this layer can only reduce risk.** It can shrink position size, open
a blackout, or raise the conviction bar. There is no code path from "rates
fell, so buy" — that would be a macro-economic view, and a trading agent
inferring one from a yield print is exactly the kind of confident nonsense
that loses money. The bond market is used here as a *risk thermometer*, not as
an oracle.

On public figures and their statements
---------------------------------------
This is the honest answer to "what about what leaders say": the statements
that actually move rates are published on `federalreserve.gov` — FOMC
statements, the Chair's speeches, testimony. Those are cryptographically
unambiguous primary sources over TLS from the institution itself. A
screenshot of a post attributed to someone is not, and cannot be verified by
this process. So the leader-statement signal here is real, it is just sourced
from the podium rather than the timeline.
"""

from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

# The Fed's own H.15 daily series — the authoritative source for US Treasury
# constant-maturity yields. Column ids are the Fed's, not ours.
H15_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx"
    "?rel=H15&series=bf17364827e38702b42a58cf8eaa3f78&lastobs=30"
    "&from=&to=&filetype=csv&label=include&layout=seriescolumn"
)
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# The full constant-maturity curve, not a three-point sample. Column ids are
# the Fed's own H.15 series identifiers.
SERIES = {
    "1m": "RIFLGFCM01_N.B", "3m": "RIFLGFCM03_N.B", "6m": "RIFLGFCM06_N.B",
    "1y": "RIFLGFCY01_N.B", "2y": "RIFLGFCY02_N.B", "3y": "RIFLGFCY03_N.B",
    "5y": "RIFLGFCY05_N.B", "7y": "RIFLGFCY07_N.B", "10y": "RIFLGFCY10_N.B",
    "20y": "RIFLGFCY20_N.B", "30y": "RIFLGFCY30_N.B",
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


@dataclass
class RateSnapshot:
    as_of: str = ""
    three_month: float | None = None
    two_year: float | None = None
    ten_year: float | None = None
    two_year_change_1w_bps: float | None = None
    ten_year_change_1w_bps: float | None = None
    curve_2s10s_bps: float | None = None
    curve_change_1w_bps: float | None = None
    # Every tenor on the curve, plus each one's weekly change, so the operator
    # sees the whole term structure rather than a three-point sample.
    curve: dict[str, float] = field(default_factory=dict)
    curve_changes_1w_bps: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class RateExpectation:
    """
    What the market itself is pricing for future policy.

    This is *derived*, not predicted. The short end of the Treasury curve is
    the market's own consensus forecast of the average policy rate over that
    horizon — billions of dollars of positioning, continuously updated. If the
    2-year yields more than the 3-month bill, the market is pricing hikes over
    that window; less, and it is pricing cuts.

    Deliberately not "sentiment analysis." Asking a language model to guess
    the next Fed move from headlines produces a confident number with no
    predictive content. Reading what the bond market has already priced is a
    measurement, and it can be checked against what actually happens — which
    is what `surprise_bps` below does after a decision lands.
    """

    horizon: str = "2y"
    implied_change_bps: float | None = None   # + = hikes priced, − = cuts priced
    implied_25bp_moves: float | None = None
    direction: str = "unknown"                # hikes | cuts | on hold | unknown
    confidence: str = "low"                   # how decisively the curve is priced
    basis: str = ""                           # the arithmetic, stated plainly

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "implied_change_bps": self.implied_change_bps,
            "implied_25bp_moves": self.implied_25bp_moves,
            "direction": self.direction,
            "confidence": self.confidence,
            "basis": self.basis,
            "method": (
                "Derived from the Treasury curve — the market's own pricing — "
                "not inferred from sentiment or headlines."
            ),
        }


@dataclass
class MacroPosture:
    """The only thing this module is allowed to output: less risk, or the same."""

    size_multiplier: float = 1.0        # never above 1.0
    conviction_uplift: float = 0.0      # raises the bar the Council must clear
    blackout: bool = False
    reasons: list[str] = field(default_factory=list)
    rates: dict[str, Any] = field(default_factory=dict)
    expectation: dict[str, Any] = field(default_factory=dict)
    calendar: list[dict[str, Any]] = field(default_factory=list)
    next_fomc: dict[str, Any] | None = None
    regime: str = "neutral"             # easing | neutral | tightening | stressed

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_multiplier": round(self.size_multiplier, 2),
            "conviction_uplift": round(self.conviction_uplift, 3),
            "blackout": self.blackout,
            "regime": self.regime,
            "reasons": self.reasons,
            "rates": self.rates,
            "expectation": self.expectation,
            "calendar": self.calendar,
            "next_fomc": self.next_fomc,
            "policy": (
                "Macro can only reduce risk — shrink size, raise the conviction "
                "bar, or pause trading. There is no path from a yield print to a "
                "buy signal."
            ),
        }


class MacroMonitor:
    # A front-end move this large in a week is a genuine liquidity shift, not
    # noise. 25bp is one full Fed move priced in or out.
    RATE_SHOCK_BPS = 25.0
    # How long before an FOMC decision to begin de-risking, and how long
    # around it to stop entirely.
    FOMC_DERISK_HOURS = 48.0
    FOMC_BLACKOUT_HOURS = 4.0
    # CPI and payrolls gap the tape hard but briefly, so the window is tighter
    # than an FOMC meeting's.
    BLS_DERISK_HOURS = 12.0
    BLS_BLACKOUT_HOURS = 1.5

    def __init__(self, refresh_seconds: float = 3600.0):
        self.refresh_seconds = refresh_seconds
        self.last_refresh = 0.0
        self.rates = RateSnapshot()
        self.fomc_dates: list[datetime] = []
        self.source_health: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
                headers={"User-Agent": "GlassBox/1.0 (macro risk monitor)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- ingestion ---------------------------------------------------------

    async def _fetch_rates(self) -> RateSnapshot:
        try:
            client = await self._http()
            r = await client.get(H15_URL)
            rows = list(csv.reader(io.StringIO(r.text)))
        except Exception as exc:
            self.source_health["treasury_yields"] = f"unreachable ({type(exc).__name__})"
            return self.rates

        header: list[str] = []
        data: list[list[str]] = []
        for row in rows:
            if not row:
                continue
            if row[0] == "Time Period":
                header = row
            elif header and re.match(r"^\d{4}-\d{2}-\d{2}$", row[0]):
                data.append(row)

        if not header or not data:
            self.source_health["treasury_yields"] = "no parsable rows"
            return self.rates

        idx = {name: header.index(col) for name, col in SERIES.items() if col in header}

        def value(row: list[str], name: str) -> float | None:
            i = idx.get(name)
            if i is None or i >= len(row):
                return None
            try:
                return float(row[i])
            except ValueError:
                return None  # the Fed writes "ND" on holidays

        # Walk back to the most recent row that actually has data.
        latest = next(
            (r for r in reversed(data) if value(r, "2y") is not None), None
        )
        if latest is None:
            self.source_health["treasury_yields"] = "no numeric observations"
            return self.rates

        # Roughly one week earlier — 5 business days back in this series.
        li = data.index(latest)
        prior = next(
            (r for r in reversed(data[: max(li - 4, 0) + 1]) if value(r, "2y") is not None),
            None,
        )

        snap = RateSnapshot(
            as_of=latest[0],
            three_month=value(latest, "3m"),
            two_year=value(latest, "2y"),
            ten_year=value(latest, "10y"),
        )
        for tenor in SERIES:
            v = value(latest, tenor)
            if v is not None:
                snap.curve[tenor] = v
        if snap.two_year is not None and snap.ten_year is not None:
            snap.curve_2s10s_bps = round((snap.ten_year - snap.two_year) * 100, 1)

        if prior is not None:
            p2, p10 = value(prior, "2y"), value(prior, "10y")
            if p2 is not None and snap.two_year is not None:
                snap.two_year_change_1w_bps = round((snap.two_year - p2) * 100, 1)
            if p10 is not None and snap.ten_year is not None:
                snap.ten_year_change_1w_bps = round((snap.ten_year - p10) * 100, 1)
            for tenor in SERIES:
                now_v, prev_v = value(latest, tenor), value(prior, tenor)
                if now_v is not None and prev_v is not None:
                    snap.curve_changes_1w_bps[tenor] = round((now_v - prev_v) * 100, 1)
            if None not in (p2, p10) and snap.curve_2s10s_bps is not None:
                snap.curve_change_1w_bps = round(
                    snap.curve_2s10s_bps - (p10 - p2) * 100, 1
                )

        self.source_health["treasury_yields"] = f"ok (as of {snap.as_of})"
        return snap

    async def _fetch_fomc_dates(self) -> list[datetime]:
        """
        Scheduled FOMC meeting dates, published months ahead.

        This is what makes pre-emptive de-risking possible: the risk is on a
        calendar, so the agent does not have to be surprised by it.
        """
        try:
            client = await self._http()
            html = (await client.get(FOMC_CALENDAR_URL)).text
        except Exception as exc:
            self.source_health["fomc_calendar"] = f"unreachable ({type(exc).__name__})"
            return self.fomc_dates

        dates: list[datetime] = []
        # The page groups meetings under a per-year panel; parse each panel so
        # a month is attributed to the right year.
        for year_match in re.finditer(
            r"(20\d\d)\s+FOMC Meetings(.*?)(?=(?:20\d\d\s+FOMC Meetings)|\Z)",
            html, re.DOTALL,
        ):
            year = int(year_match.group(1))
            block = year_match.group(2)
            for m in re.finditer(
                r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z]+)'
                r'.*?fomc-meeting__date[^>]*>\s*([0-9]{1,2})(?:-([0-9]{1,2}))?',
                block, re.DOTALL,
            ):
                month = _MONTHS.get(m.group(1).strip().lower())
                if not month:
                    continue
                # A two-day meeting decides on the *second* day — that is when
                # the statement lands and the tape moves.
                day = int(m.group(3) or m.group(2))
                try:
                    # 18:00 UTC ≈ 2pm ET, the usual statement time.
                    dates.append(datetime(year, month, day, 18, 0, tzinfo=timezone.utc))
                except ValueError:
                    continue

        dates.sort()
        self.source_health["fomc_calendar"] = f"ok ({len(dates)} meetings parsed)"
        return dates

    async def refresh(self, force: bool = False) -> None:
        if not force and (time.time() - self.last_refresh) < self.refresh_seconds:
            return
        self.rates = await self._fetch_rates()
        self.fomc_dates = await self._fetch_fomc_dates()
        self.last_refresh = time.time()

    # -- the only output that matters --------------------------------------

    def next_fomc(self, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or datetime.now(timezone.utc)
        upcoming = [d for d in self.fomc_dates if d > now]
        if not upcoming:
            return None
        nxt = upcoming[0]
        hours = (nxt - now).total_seconds() / 3600
        return {
            "date": nxt.isoformat(),
            "hours_away": round(hours, 1),
            "days_away": round(hours / 24, 1),
        }

    def assess(self, now: datetime | None = None) -> MacroPosture:
        now = now or datetime.now(timezone.utc)
        posture = MacroPosture(rates=self.rates.to_dict())
        posture.next_fomc = self.next_fomc(now)
        expectation = self.rate_expectation()
        posture.expectation = expectation.to_dict()
        posture.calendar = self.economic_calendar(now)

        # 1. Scheduled FOMC. The pre-emptive branch — the whole point of
        #    reading a calendar rather than a headline.
        if posture.next_fomc:
            hours = posture.next_fomc["hours_away"]
            if hours <= self.FOMC_BLACKOUT_HOURS:
                posture.blackout = True
                posture.size_multiplier = min(posture.size_multiplier, 0.0)
                posture.reasons.append(
                    f"FOMC decision in {hours:.1f}h — trading paused through the "
                    f"statement window, when spreads gap and every model is at "
                    f"its least reliable."
                )
            elif hours <= self.FOMC_DERISK_HOURS:
                # Taper size down as the meeting approaches rather than
                # switching off at a cliff edge.
                scale = 0.45 + 0.55 * (hours / self.FOMC_DERISK_HOURS)
                posture.size_multiplier = min(posture.size_multiplier, scale)
                posture.conviction_uplift = max(posture.conviction_uplift, 0.05)
                posture.reasons.append(
                    f"FOMC decision in {hours / 24:.1f} days — position sizes "
                    f"reduced to {scale:.0%} and the conviction bar raised ahead "
                    f"of a scheduled, known volatility event."
                )

        # 1b. Scheduled BLS releases. Same pre-emptive logic as FOMC, tighter
        #     window: CPI and payrolls gap the tape hard but briefly.
        for row in self.bls_release_dates(now, weeks=2):
            hours = row["days_away"] * 24
            if hours <= self.BLS_BLACKOUT_HOURS:
                posture.blackout = True
                posture.size_multiplier = 0.0
                posture.reasons.append(
                    f"{row['event']} releases in {hours:.1f}h — trading paused "
                    f"across the print. ({row['note']})"
                )
                break
            if hours <= self.BLS_DERISK_HOURS:
                scale = 0.5 + 0.5 * (hours / self.BLS_DERISK_HOURS)
                posture.size_multiplier = min(posture.size_multiplier, scale)
                posture.conviction_uplift = max(posture.conviction_uplift, 0.05)
                posture.reasons.append(
                    f"{row['event']} in {hours:.0f}h — sizes reduced to "
                    f"{scale:.0%} ahead of a scheduled release. ({row['note']})"
                )
                break

        # 2. Front-end rate shock. A sharp 2y move is liquidity repricing.
        d2 = self.rates.two_year_change_1w_bps
        if d2 is not None and abs(d2) >= self.RATE_SHOCK_BPS:
            posture.size_multiplier = min(posture.size_multiplier, 0.6)
            posture.conviction_uplift = max(posture.conviction_uplift, 0.05)
            posture.regime = "tightening" if d2 > 0 else "easing"
            direction = "risen" if d2 > 0 else "fallen"
            posture.reasons.append(
                f"US 2-year yield has {direction} {abs(d2):.0f}bp in a week to "
                f"{self.rates.two_year:.2f}% — the discount rate on every risky "
                f"asset is moving, so size is cut regardless of direction."
            )
        elif d2 is not None:
            posture.regime = "tightening" if d2 > 8 else "easing" if d2 < -8 else "neutral"

        # 3. Curve stress. A rapidly deepening inversion is the bond market
        #    disagreeing with risk appetite.
        curve = self.rates.curve_2s10s_bps
        dcurve = self.rates.curve_change_1w_bps
        if curve is not None and curve < 0 and dcurve is not None and dcurve < -10:
            posture.size_multiplier = min(posture.size_multiplier, 0.7)
            posture.regime = "stressed"
            posture.reasons.append(
                f"2s10s inverted at {curve:.0f}bp and steepening downward "
                f"({dcurve:+.0f}bp this week) — the bond market is pricing a "
                f"worsening outlook."
            )

        if expectation.direction in ("hikes", "cuts") and expectation.confidence != "low":
            posture.reasons.append(
                f"Market pricing: {expectation.basis} This is the market's own "
                f"expectation read from the curve, not a forecast made here."
            )

        if not posture.reasons:
            posture.reasons.append(
                "No scheduled Fed event nearby and rates are stable. Macro is "
                "not constraining position size."
            )
        return posture

    def rate_expectation(self) -> RateExpectation:
        """
        Read the market's priced expectation for policy out of the curve.

        3-month bill = roughly today's policy rate. 2-year = the average
        expected policy rate over two years. The gap is what the market has
        already priced in.
        """
        m3, y2 = self.rates.three_month, self.rates.two_year
        if m3 is None or y2 is None:
            return RateExpectation(basis="Rate data unavailable.")

        spread_bps = round((y2 - m3) * 100, 1)
        moves = round(spread_bps / 25.0, 2)

        if spread_bps > 15:
            direction = "hikes"
        elif spread_bps < -15:
            direction = "cuts"
        else:
            direction = "on hold"

        magnitude = abs(spread_bps)
        confidence = "high" if magnitude > 50 else "moderate" if magnitude > 20 else "low"

        return RateExpectation(
            horizon="2y",
            implied_change_bps=spread_bps,
            implied_25bp_moves=moves,
            direction=direction,
            confidence=confidence,
            basis=(
                f"2-year at {y2:.2f}% versus 3-month at {m3:.2f}% is a "
                f"{spread_bps:+.0f}bp spread — the market is pricing roughly "
                f"{abs(moves):.1f} × 25bp {direction} over two years."
            ),
        )

    @staticmethod
    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
        """The nth given weekday of a month (weekday: Mon=0 … Sun=6)."""
        d = datetime(year, month, 1, tzinfo=timezone.utc)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def bls_release_dates(self, now: datetime, weeks: int) -> list[dict[str, Any]]:
        """
        CPI and Employment Situation release dates.

        The BLS blocks automated access to its schedule pages (HTTP 403 on
        every endpoint tested), so these are **computed from the agency's
        published scheduling convention** rather than fetched:

        * Employment Situation — first Friday of the month, 08:30 ET
        * CPI — roughly the second Wednesday of the following month, 08:30 ET

        Those conventions hold the large majority of the time but BLS does
        shift releases around federal holidays. So every BLS row is marked
        `confidence: "estimated"` and the rule used is stated on the row
        itself, while FOMC rows — parsed from the Fed's actual published
        calendar — are marked `"confirmed"`. Showing an estimate labelled as
        an estimate is useful; showing one dressed up as a fact is not.
        """
        out: list[dict[str, Any]] = []
        horizon = now + timedelta(weeks=weeks)
        cursor = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        while cursor <= horizon + timedelta(days=40):
            # 12:30 UTC ≈ 08:30 ET, the standard BLS release time.
            nfp = self._nth_weekday(cursor.year, cursor.month, 4, 1).replace(hour=12, minute=30)
            cpi = self._nth_weekday(cursor.year, cursor.month, 2, 2).replace(hour=12, minute=30)
            for when, name, note in (
                (nfp, "Employment Situation (payrolls)",
                 "Estimated: BLS convention is the first Friday of the month."),
                (cpi, "CPI inflation report",
                 "Estimated: BLS convention is around the second Wednesday."),
            ):
                if now < when <= horizon:
                    out.append({
                        "event": name,
                        "source": "bls.gov (scheduling convention)",
                        "date": when.isoformat(),
                        "days_away": round((when - now).total_seconds() / 86400, 1),
                        "impact": "high",
                        "confidence": "estimated",
                        "note": note,
                    })
            cursor = (cursor.replace(day=28) + timedelta(days=8)).replace(day=1)
        return out

    def economic_calendar(self, now: datetime | None = None, weeks: int = 12) -> list[dict[str, Any]]:
        """
        Every scheduled macro event ahead, with what the agent will do at each.

        FOMC decisions come from the Fed's published calendar and are marked
        confirmed. CPI and payrolls are computed from BLS scheduling
        convention and marked estimated — see `bls_release_dates`.
        """
        now = now or datetime.now(timezone.utc)
        horizon = now + timedelta(weeks=weeks)
        out: list[dict[str, Any]] = []

        for d in self.fomc_dates:
            if now < d <= horizon:
                hours = (d - now).total_seconds() / 3600
                out.append({
                    "event": "FOMC rate decision",
                    "source": "federalreserve.gov",
                    "date": d.isoformat(),
                    "days_away": round(hours / 24, 1),
                    "impact": "high",
                    "confidence": "confirmed",
                    "note": "Parsed from the Fed's own published calendar.",
                    "agent_action": (
                        "Trading paused around the statement"
                        if hours <= self.FOMC_BLACKOUT_HOURS else
                        "Position sizes reduced ahead of it"
                        if hours <= self.FOMC_DERISK_HOURS else
                        "No constraint yet — de-risking begins 48h out"
                    ),
                })

        for row in self.bls_release_dates(now, weeks):
            hours = row["days_away"] * 24
            row["agent_action"] = (
                "Trading paused across the release"
                if hours <= self.BLS_BLACKOUT_HOURS else
                "Position sizes reduced ahead of it"
                if hours <= self.BLS_DERISK_HOURS else
                "No constraint yet — de-risking begins 12h out"
            )
            out.append(row)

        out.sort(key=lambda r: r["date"])
        return out

    def alerts(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """
        Events close enough that the operator should be told now.

        Deliberately quiet: an alert that fires constantly is an alert nobody
        reads. Only events inside their own de-risking window appear, because
        those are the ones already changing what the agent is allowed to do.
        """
        now = now or datetime.now(timezone.utc)
        out = []
        for row in self.economic_calendar(now, weeks=2):
            hours = row["days_away"] * 24
            is_fomc = row["event"].startswith("FOMC")
            derisk = self.FOMC_DERISK_HOURS if is_fomc else self.BLS_DERISK_HOURS
            blackout = self.FOMC_BLACKOUT_HOURS if is_fomc else self.BLS_BLACKOUT_HOURS
            if hours > derisk:
                continue
            out.append({
                **row,
                "hours_away": round(hours, 1),
                "severity": "critical" if hours <= blackout else "warning",
                "headline": (
                    f"{row['event']} in "
                    + (f"{hours * 60:.0f} minutes" if hours < 1 else f"{hours:.1f} hours")
                ),
            })
        return out

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "alerts": self.alerts(now),
            "calendar": self.economic_calendar(now),
            "sources": self.source_health,
            "last_refresh_age_s": round(time.time() - self.last_refresh, 1)
            if self.last_refresh else None,
            "posture": self.assess(now).to_dict(),
            "fomc_meetings_known": len(self.fomc_dates),
        }
