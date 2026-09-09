"""
GlassBox — news, macro and event risk.

The design decision here is the important part, so it goes first.

**News moves risk posture, never direction.**

An agent that reads a headline and buys is an agent anyone can trade against by
publishing a headline. Fake press releases, spoofed accounts, and deliberately
ambiguous wording are cheap; an LLM confidently parsing them into a market view
is a manipulation surface, not an edge. Several real incidents have moved markets
billions of dollars on a single fabricated post.

So this module can do exactly three things:

    1. hard-block a symbol          (Binance delisting — authoritative, from Binance)
    2. open a volatility blackout   (scheduled macro event, or a news velocity spike)
    3. reduce position size         (elevated headline risk)

It can never say "buy". There is no code path from a headline to a long position.

Sources, in order of how much they are trusted
----------------------------------------------
* **Binance's own announcement feed** — authoritative for listings, delistings,
  suspensions and maintenance. A delisting is the single largest predictable
  move a token makes, and Binance publishes it first. This is the only source
  allowed to trigger a hard block.
* **Federal Reserve press releases** — authoritative for the macro events that
  gap crypto. Used only to open blackout windows.
* **Crypto news RSS (CoinDesk, Cointelegraph)** — used for headline *volume*
  and keyword risk, never for direction. Ten urgent stories in an hour means
  size down, regardless of what they say.
* **Social / leaders' posts** — deliberately not enabled by default. See
  `SocialAdapter` at the bottom for why, and what it would take.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

BINANCE_CMS = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo=1&pageSize=20"
)
RSS_SOURCES = {
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
}

# Words that reliably precede a violent move, in either direction. Direction is
# deliberately not inferred — only that the tape is about to be dangerous.
RISK_WORDS = [
    "hack", "exploit", "breach", "stolen", "drained",
    "halt", "suspend", "suspension", "freeze", "frozen",
    "bankrupt", "insolvency", "liquidation", "default",
    "lawsuit", "sec ", "sue", "indicted", "charges", "fraud", "investigation",
    "ban", "banned", "crackdown", "sanction", "seizure",
    "war", "strike", "conflict", "invasion", "escalation", "tariff",
    "emergency", "collapse", "crash", "contagion",
    "rate decision", "fomc", "cpi", "inflation", "nonfarm", "payrolls",
]

DELIST_WORDS = ["delist", "will remove", "removal of", "cease trading", "terminate"]
HALT_WORDS = ["suspend", "halt", "maintenance", "pause deposit", "pause withdrawal"]


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    published: float
    symbols: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    matched: list[str] = field(default_factory=list)
    category: str = "general"  # general | delisting | halt | macro | security

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "title": self.title, "url": self.url,
            "published": self.published,
            "age_minutes": round((time.time() - self.published) / 60, 1),
            "symbols": self.symbols, "risk_score": round(self.risk_score, 2),
            "matched": self.matched, "category": self.category,
        }


@dataclass
class EventRisk:
    """The only thing news is allowed to produce."""

    level: str  # normal | elevated | high
    score: float
    blocked_symbols: list[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    blackout: bool = False
    reasons: list[str] = field(default_factory=list)
    headlines: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level, "score": round(self.score, 1),
            "blocked_symbols": self.blocked_symbols,
            "size_multiplier": round(self.size_multiplier, 2),
            "blackout": self.blackout, "reasons": self.reasons,
            "headlines": self.headlines,
            "policy": (
                "News adjusts risk posture only. There is no code path from a "
                "headline to a buy — that is a manipulation surface, not an edge."
            ),
        }


class NewsMonitor:
    def __init__(self, symbols: list[str] | None = None, refresh_seconds: float = 120.0):
        self.symbols = symbols or []
        self.refresh_seconds = refresh_seconds
        self.items: list[NewsItem] = []
        self.last_refresh = 0.0
        self.source_health: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=12.0, follow_redirects=True,
                headers={"User-Agent": "GlassBox/1.0 (market risk monitor)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- ingestion ---------------------------------------------------------

    def _assets(self) -> dict[str, str]:
        """Base asset → symbol, so 'SOL' in a headline maps to SOLUSDT."""
        out = {}
        for s in self.symbols:
            for quote in ("USDT", "FDUSD", "USDC", "BTC"):
                if s.endswith(quote):
                    out[s[: -len(quote)]] = s
                    break
        return out

    def _classify(self, title: str, source: str) -> tuple[float, list[str], str, list[str]]:
        low = title.lower()
        matched = [w.strip() for w in RISK_WORDS if w in low]
        score = min(len(matched) * 18, 60)

        category = "general"
        if source == "binance" and any(w in low for w in DELIST_WORDS):
            category, score = "delisting", 100.0
        elif source == "binance" and any(w in low for w in HALT_WORDS):
            category, score = "halt", max(score, 55.0)
        elif source == "federal_reserve":
            category, score = "macro", max(score, 45.0)
        elif any(w in low for w in ("hack", "exploit", "breach", "drained", "stolen")):
            category, score = "security", max(score, 70.0)

        # Ticker extraction is deliberately conservative: uppercase words of 2–6
        # characters that we actually trade. Loose matching turns the word "IT"
        # or "ON" into a token and blocks half the book.
        assets = self._assets()
        found = []
        for token in re.findall(r"\b[A-Z]{2,6}\b", title):
            if token in assets and assets[token] not in found:
                found.append(assets[token])
        return score, matched, category, found

    async def _fetch_binance(self) -> list[NewsItem]:
        """Binance's own announcements. The only source trusted to hard-block."""
        try:
            client = await self._http()
            resp = await client.get(BINANCE_CMS)
            data = resp.json()
        except Exception as exc:
            self.source_health["binance"] = f"unreachable ({type(exc).__name__})"
            return []

        out: list[NewsItem] = []
        catalogs = (data.get("data") or {}).get("catalogs") or []
        for cat in catalogs:
            for art in cat.get("articles", []) or []:
                title = art.get("title", "")
                if not title:
                    continue
                score, matched, category, syms = self._classify(title, "binance")
                out.append(NewsItem(
                    source="binance", title=title,
                    url=f"https://www.binance.com/en/support/announcement/{art.get('code','')}",
                    published=float(art.get("releaseDate", time.time() * 1000)) / 1000,
                    symbols=syms, risk_score=score, matched=matched, category=category,
                ))
        self.source_health["binance"] = f"ok ({len(out)} items)"
        return out

    async def _fetch_rss(self, name: str, url: str) -> list[NewsItem]:
        try:
            client = await self._http()
            resp = await client.get(url)
            root = ET.fromstring(resp.content)
        except Exception as exc:
            self.source_health[name] = f"unreachable ({type(exc).__name__})"
            return []

        out: list[NewsItem] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub = time.time()
            raw = item.findtext("pubDate")
            if raw:
                try:
                    from email.utils import parsedate_to_datetime

                    pub = parsedate_to_datetime(raw).timestamp()
                except Exception:
                    pass
            score, matched, category, syms = self._classify(title, name)
            out.append(NewsItem(
                source=name, title=title, url=(item.findtext("link") or "").strip(),
                published=pub, symbols=syms, risk_score=score,
                matched=matched, category=category,
            ))
        self.source_health[name] = f"ok ({len(out)} items)"
        return out[:25]

    async def refresh(self, force: bool = False) -> list[NewsItem]:
        if not force and (time.time() - self.last_refresh) < self.refresh_seconds:
            return self.items
        results = await asyncio.gather(
            self._fetch_binance(),
            *[self._fetch_rss(n, u) for n, u in RSS_SOURCES.items()],
            return_exceptions=True,
        )
        items: list[NewsItem] = []
        for r in results:
            if isinstance(r, list):
                items.extend(r)
        items.sort(key=lambda i: -i.published)
        self.items = items[:120]
        self.last_refresh = time.time()
        return self.items

    # -- the only output that matters --------------------------------------

    def assess(self, window_hours: float = 6.0) -> EventRisk:
        cutoff = time.time() - window_hours * 3600
        recent = [i for i in self.items if i.published >= cutoff]

        blocked: list[str] = []
        reasons: list[str] = []
        score = 0.0

        # 1. Delistings. Authoritative, from Binance, and non-negotiable — the
        #    agent must not be holding a token on its way off the exchange.
        for i in recent:
            if i.category == "delisting" and i.symbols:
                for s in i.symbols:
                    if s not in blocked:
                        blocked.append(s)
                        reasons.append(f"Binance announced a delisting affecting {s}.")
                score = 100.0

        # 2. Security events naming something we hold.
        for i in recent:
            if i.category == "security" and i.symbols:
                score = max(score, 75.0)
                reasons.append(f"Security incident referencing {', '.join(i.symbols)}.")

        # 3. Scheduled macro. Blackout rather than a view.
        macro = [i for i in recent if i.category == "macro" and i.published > time.time() - 7200]
        if macro:
            score = max(score, 50.0)
            reasons.append(
                f"{len(macro)} Federal Reserve release(s) in the last two hours."
            )

        # 4. Headline velocity. Ten urgent stories in six hours means the tape is
        #    about to be violent, whatever they say.
        urgent = [i for i in recent if i.risk_score >= 30]
        if len(urgent) >= 8:
            score = max(score, 55.0)
            reasons.append(f"{len(urgent)} elevated-risk headlines in {window_hours:.0f}h.")
        elif len(urgent) >= 4:
            score = max(score, 35.0)
            reasons.append(f"{len(urgent)} elevated-risk headlines in {window_hours:.0f}h.")

        level = "high" if score >= 70 else "elevated" if score >= 35 else "normal"
        size_mult = 0.4 if level == "high" else 0.7 if level == "elevated" else 1.0

        if not reasons:
            reasons.append("No elevated event risk in the monitored sources.")

        return EventRisk(
            level=level, score=score, blocked_symbols=blocked,
            size_multiplier=size_mult, blackout=score >= 70,
            reasons=reasons,
            headlines=[i.to_dict() for i in sorted(recent, key=lambda x: -x.risk_score)[:12]],
        )

    def status(self) -> dict[str, Any]:
        risk = self.assess()
        return {
            "sources": self.source_health,
            "items_tracked": len(self.items),
            "last_refresh_age_s": round(time.time() - self.last_refresh, 1)
            if self.last_refresh else None,
            "risk": risk.to_dict(),
            "recent": [i.to_dict() for i in self.items[:25]],
        }


class SocialAdapter:
    """
    Placeholder for social and public-figure posts. Disabled by default.

    Why it is off rather than merely unimplemented:

    A crypto agent that trades on a public figure's post can be traded against
    by anyone who can produce something that looks like one. Spoofed handles,
    compromised accounts and screenshot forgeries are cheap, and the market has
    already been moved several times by exactly that. Wiring an LLM directly
    from an unverified post to a position size is not a feature, it is an
    invitation.

    If you enable this, the design constraints below are not optional:

    * Verify at the API level, never by scraping or by screenshot. Only the
      official X API with a verified author id is acceptable evidence.
    * Treat every post as **risk only**, exactly like the news layer. A post may
      widen a blackout or cut position size. It may never open a position.
    * Require corroboration before acting: a single post is a rumour; a post
      plus a confirming price move plus a second source is an event.
    * Log the raw post to the ledger so a bad decision can be traced to the
      thing that caused it.

    Set GLASSBOX_X_BEARER_TOKEN and implement `fetch()` to enable.
    """

    enabled = False

    def __init__(self, bearer_token: str | None = None, accounts: list[str] | None = None):
        self.bearer_token = bearer_token
        self.accounts = accounts or []
        self.enabled = bool(bearer_token)

    async def fetch(self) -> list[NewsItem]:
        if not self.enabled:
            return []
        raise NotImplementedError(
            "Provide an X API bearer token and implement verified-author fetching. "
            "Scraping is not an acceptable source for a system that moves money."
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "accounts": self.accounts,
            "policy": (
                "Disabled by default. A post can be faked; a position cannot be "
                "unfaked. If enabled, posts may only reduce risk, never open a "
                "position, and must be verified through the official API."
            ),
        }
