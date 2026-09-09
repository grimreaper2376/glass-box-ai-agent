"""
GlassBox — configuration and core domain types.

Nothing in this module talks to the network. It defines the vocabulary the rest
of the system uses: capital states, intents, verdicts, and where files live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

BASE_DIR = Path(os.environ.get("GLASSBOX_HOME", Path.home() / ".glassbox"))
DATA_DIR = BASE_DIR / "data"
LEDGER_PATH = DATA_DIR / "ledger.jsonl"
KEY_PATH = BASE_DIR / "device.key"
TOKEN_PATH = BASE_DIR / "binance_session.enc"
MCP_CLIENT_ID_PATH = DATA_DIR / "mcp_client_id.txt"
STATE_PATH = DATA_DIR / "state.json"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "constitution.yaml"
FRONTEND_DIR = REPO_ROOT / "frontend"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_persisted_client_id() -> str:
    """
    The MCP client id (a URL to a hosted OAuth client-metadata document) set
    once through the dashboard and remembered from then on, so it survives a
    restart without needing an environment variable — see
    `save_persisted_client_id` for the write side, and the `/api/mcp/client-id`
    endpoints in server.py for how the dashboard reaches this.
    """
    try:
        return MCP_CLIENT_ID_PATH.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def save_persisted_client_id(url: str) -> None:
    ensure_dirs()
    MCP_CLIENT_ID_PATH.write_text(url.strip(), encoding="utf-8")


def clear_persisted_client_id() -> None:
    try:
        MCP_CLIENT_ID_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class Clock:
    """
    Time, as the system experiences it.

    In a live session this is the wall clock. In a backtest it advances by the
    modelled tick duration instead, because time-based rules — cooldowns, rate
    limits, daily loss windows — are meaningless if 500 ticks of market action
    occupy 800 milliseconds of real time. Getting this wrong silently disables
    half the Constitution, which is exactly the sort of bug a backtest is
    supposed to catch rather than cause.
    """

    def __init__(self, simulated: bool = False, step_seconds: float = 3.0):
        import time as _t

        self.simulated = simulated
        self.step_seconds = step_seconds
        self._t = _t.time()

    def now(self) -> float:
        import time as _t

        return self._t if self.simulated else _t.time()

    def set_time(self, epoch_seconds: float) -> None:
        """
        Pin the simulated clock to a specific moment.

        Used by isolated test engines so a drill's outcome never depends on
        what real wall-clock time happens to be when someone runs it — a
        blackout-window rule, for instance, would otherwise spuriously deny
        every single trade for the roughly 1% of the day the test happened to
        land inside a configured window, which is exactly the kind of
        flaky-by-time-of-day failure a safety drill must never have.
        """
        self._t = epoch_seconds

    def advance(self, seconds: float | None = None) -> None:
        if self.simulated:
            self._t += seconds if seconds is not None else self.step_seconds


# --------------------------------------------------------------------------
# Capital state machine
# --------------------------------------------------------------------------


class CapitalState(str, Enum):
    """Every unit of capital is always in exactly one of these states."""

    IDLE = "IDLE"  # sitting in stables, eligible for yield routing
    DEPLOYED = "DEPLOYED"  # in a conviction position
    HEDGED = "HEDGED"  # position held but offset by a defensive leg
    QUARANTINED = "QUARANTINED"  # frozen by the Guardian, no module may touch it


class Mode(str, Enum):
    """Execution surface. Paper is the default and is never overridden silently."""

    PAPER = "paper"    # simulated fills, no Binance write calls at all
    SHADOW = "shadow"  # live market data, simulated fills, intents logged
    MOCK = "mock"      # real MCP client against a local mock Binance server
    LIVE = "live"      # real MCP client against agent.binance.com
    BRIDGE = "bridge"  # emits signed instructions for a human to run


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    DENY = "DENY"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


# --------------------------------------------------------------------------
# Intents and verdicts
# --------------------------------------------------------------------------


@dataclass
class Signal:
    """One analyst's contribution to a decision."""

    agent: str
    symbol: str
    stance: Literal["bullish", "bearish", "neutral"]
    confidence: float  # 0..1
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0  # x402 metered data spend attributable to this signal

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Intent:
    """
    A proposed state transition for capital. This is the only object that can
    ever become an order, and it cannot reach an exchange without a Verdict.
    """

    intent_id: str
    ts: float
    module: str  # council | sentinel | narrative | compass
    symbol: str
    side: Side
    notional_usd: float
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None
    from_state: CapitalState
    to_state: CapitalState
    thesis: str
    signals: list[Signal] = field(default_factory=list)
    stop_loss: float | None = None
    take_profit: float | None = None
    urgency: Literal["routine", "elevated", "critical"] = "routine"
    reference_price: float | None = None  # price at the moment this intent was
    # proposed. Execution compares the live price against this before sending
    # an order — see execution.py's price collar — so a decision that sat
    # waiting for a human confirmation, or was proposed just before a fast
    # move, is not blindly executed at whatever price the market has since
    # reached.
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["from_state"] = self.from_state.value
        d["to_state"] = self.to_state.value
        return d


@dataclass
class Verdict:
    """The Constitution's ruling on an Intent."""

    decision: Decision
    reasons: list[str] = field(default_factory=list)
    triggered_rules: list[str] = field(default_factory=list)
    adjusted_notional_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


@dataclass
class Receipt:
    """
    The artifact that makes this system different: a signed, chained,
    human-readable record of why a trade happened.
    """

    receipt_id: str
    intent: dict[str, Any]
    verdict: dict[str, Any]
    guardian: dict[str, Any]
    market_snapshot: dict[str, Any]
    outcome: dict[str, Any]
    ledger_seq: int
    ledger_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Runtime settings
# --------------------------------------------------------------------------


@dataclass
class Settings:
    mode: Mode = Mode.PAPER
    starting_equity_usd: float = 10_000.0
    base_currency: str = "USDT"
    symbols: list[str] = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    )
    tick_seconds: float = 3.0          # wall-clock pause between ticks
    sim_seconds_per_tick: float = 3.0  # modelled elapsed time per tick
    simulated_clock: bool = False      # backtests advance time; live runs do not
    policy_path: Path = DEFAULT_POLICY_PATH
    live_market_data: bool = False  # public Binance REST; no credentials involved
    llm_enabled: bool = False  # heuristic analysts unless a key is present
    x402_daily_budget_usd: float = 20.0  # mirrors Binance's documented x402 cap
    kline_interval: str = "5m"           # candle size the analysts read
    calibration_horizon_seconds: float = 1800.0  # how far ahead a call is graded
    checkpoint_interval_seconds: float = 900.0   # how often the ledger is anchored
    anchor_webhook_url: str | None = None        # optional external anchor
    mcp_endpoint: str = "https://agent.binance.com/mcp/agentic"
    mcp_client_id: str = ""                      # CIMD URL for OAuth
    auto_discover_symbols: bool = True           # top USDT pairs from the API
    universe_size: int = 8                       # how many pairs to watch

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.mode = Mode(os.environ.get("GLASSBOX_MODE", "paper"))
        s.starting_equity_usd = float(
            os.environ.get("GLASSBOX_EQUITY", s.starting_equity_usd)
        )
        s.tick_seconds = float(os.environ.get("GLASSBOX_TICK", s.tick_seconds))
        s.live_market_data = os.environ.get("GLASSBOX_LIVE_DATA", "1") == "1"
        s.llm_enabled = bool(os.environ.get("ANTHROPIC_API_KEY"))
        s.kline_interval = os.environ.get("GLASSBOX_INTERVAL", s.kline_interval)
        s.anchor_webhook_url = os.environ.get("GLASSBOX_ANCHOR_WEBHOOK") or None
        s.mcp_endpoint = os.environ.get("GLASSBOX_MCP_ENDPOINT", s.mcp_endpoint)
        s.mcp_client_id = os.environ.get("GLASSBOX_MCP_CLIENT_ID", "") or _load_persisted_client_id()
        s.universe_size = int(os.environ.get("GLASSBOX_UNIVERSE", s.universe_size))
        s.auto_discover_symbols = os.environ.get("GLASSBOX_AUTODISCOVER", "1") == "1"
        if os.environ.get("GLASSBOX_POLICY"):
            s.policy_path = Path(os.environ["GLASSBOX_POLICY"])
        return s
