"""
GlassBox — Payment and on-chain action workflows (paper / testnet-grade).

This adds the two workflow categories the trading engine did not yet cover:
agent payments (agent-to-agent USDC transfers) and on-chain actions
(staking, unstaking, claiming rewards, and swaps). It is deliberately a *paper*
book with its own simulated USDC wallet, kept separate from the trading
portfolio so it can never disturb live positions or their accounting. Every
action is screened by a spending policy first and then written to the same
signed, hash-chained ledger the rest of the system uses, so the audit story —
you can see exactly what the agent did and verify it wasn't altered — extends to
payments and on-chain work unchanged.

Nothing here touches real mainnet funds. Real settlement in GlassBox remains
scoped to the Base Sepolia testnet; this module demonstrates the workflows and
their governance, honestly labelled as a simulation, in the same spirit as the
engine's default paper trading mode.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

# Staking assets offered in the paper book, with indicative annual yields.
STAKE_ASSETS = {
    "ETH": {"apy": 0.035, "name": "Ethereum"},
    "SOL": {"apy": 0.068, "name": "Solana"},
    "BNB": {"apy": 0.028, "name": "BNB"},
    "USDC": {"apy": 0.045, "name": "USD Coin"},
}
# Rough USDC reference prices for paper swaps (only relative size matters here).
SWAP_PRICES = {"USDC": 1.0, "ETH": 2500.0, "SOL": 150.0, "BNB": 600.0, "BTC": 68000.0}

STARTING_USDC = 5000.0
MAX_ACTION_USD = 2000.0          # a single action can't exceed this
MAX_ACTION_FRACTION = 0.5        # …or half the wallet, whichever is smaller
SECONDS_PER_YEAR = 365 * 24 * 3600


class OnchainActions:
    """A self-contained paper wallet for payment and on-chain workflows."""

    def __init__(self, engine):
        self.engine = engine
        self.usdc = STARTING_USDC
        # asset -> {"amount": usd_value_staked, "apy": rate, "since": ts, "rewards": accrued}
        self.stakes: dict[str, dict] = {}
        self.tokens: dict[str, float] = {}      # swapped token holdings, in token units
        self.receipts: list[dict] = []          # recent action receipts (newest first)

    # -- policy screen ----------------------------------------------------- #

    def _kill_switch_on(self) -> bool:
        try:
            return bool(getattr(self.engine.constitution, "kill_switch", False))
        except Exception:  # noqa: BLE001
            return False

    def _screen(self, amount_usd: float) -> tuple[bool, str]:
        """The same idea as the trading Constitution: a hard, code-side check
        before anything moves. Returns (allowed, reason)."""
        if self._kill_switch_on():
            return False, "The kill switch is engaged — all outgoing actions are frozen."
        if amount_usd is None or amount_usd <= 0:
            return False, "Amount must be greater than zero."
        cap = min(MAX_ACTION_USD, self.usdc * MAX_ACTION_FRACTION)
        if amount_usd > self.usdc:
            return False, f"Insufficient balance: wallet holds {self.usdc:,.2f} USDC."
        if amount_usd > cap and cap > 0:
            return False, (f"Blocked by the spending policy: a single action is capped at "
                           f"{cap:,.2f} USDC ({int(MAX_ACTION_FRACTION*100)}% of the wallet, "
                           f"max {MAX_ACTION_USD:,.0f}).")
        return True, "within policy"

    def _record(self, kind: str, payload: dict) -> dict:
        """Write a signed ledger entry (best-effort) and keep a local receipt."""
        rec = {"kind": kind, "ts": time.time(), **payload}
        try:
            signed = self.engine.ledger.append(kind, payload)
            if isinstance(signed, dict):
                rec["seq"] = signed.get("seq")
                rec["hash"] = (signed.get("hash") or signed.get("this_hash") or "")[:16]
        except Exception:  # noqa: BLE001
            pass
        self.receipts.insert(0, rec)
        self.receipts = self.receipts[:20]
        return rec

    # -- reward accrual ---------------------------------------------------- #

    def _accrue(self):
        now = time.time()
        for a, s in self.stakes.items():
            dt = now - s.get("since", now)
            if dt > 0 and s["amount"] > 0:
                s["rewards"] = s.get("rewards", 0.0) + s["amount"] * s["apy"] * (dt / SECONDS_PER_YEAR)
                s["since"] = now

    # -- actions ----------------------------------------------------------- #

    def stake(self, asset: str, amount_usd: float) -> dict:
        asset = (asset or "").upper().strip()
        if asset not in STAKE_ASSETS:
            return {"ok": False, "error": f"Can't stake {asset or '—'}. Options: {', '.join(STAKE_ASSETS)}."}
        amount_usd = _num(amount_usd)
        ok, reason = self._screen(amount_usd)
        if not ok:
            self._record("onchain_stake_denied", {"asset": asset, "amount_usd": amount_usd, "reason": reason})
            return {"ok": False, "error": reason}
        self._accrue()
        self.usdc -= amount_usd
        s = self.stakes.setdefault(asset, {"amount": 0.0, "apy": STAKE_ASSETS[asset]["apy"],
                                           "since": time.time(), "rewards": 0.0})
        s["amount"] += amount_usd
        s["since"] = time.time()
        rec = self._record("onchain_stake", {"action": "stake", "asset": asset,
                                              "amount_usd": round(amount_usd, 2),
                                              "apy": s["apy"]})
        return {"ok": True, "message": f"Staked {amount_usd:,.2f} USDC into {asset} at "
                f"{s['apy']*100:.1f}% APY.", "receipt": rec, "state": self.state()}

    def unstake(self, asset: str, amount_usd: float) -> dict:
        asset = (asset or "").upper().strip()
        self._accrue()
        s = self.stakes.get(asset)
        if not s or s["amount"] <= 0:
            return {"ok": False, "error": f"No active {asset} stake to withdraw."}
        amount_usd = _num(amount_usd)
        if amount_usd <= 0 or amount_usd > s["amount"]:
            return {"ok": False, "error": f"Enter an amount up to {s['amount']:,.2f} USDC staked."}
        s["amount"] -= amount_usd
        self.usdc += amount_usd
        rec = self._record("onchain_unstake", {"action": "unstake", "asset": asset,
                                                "amount_usd": round(amount_usd, 2)})
        return {"ok": True, "message": f"Unstaked {amount_usd:,.2f} USDC from {asset}.",
                "receipt": rec, "state": self.state()}

    def claim(self, asset: str) -> dict:
        asset = (asset or "").upper().strip()
        self._accrue()
        s = self.stakes.get(asset)
        if not s or s.get("rewards", 0.0) <= 0:
            return {"ok": False, "error": f"No {asset} rewards to claim yet."}
        reward = s["rewards"]
        s["rewards"] = 0.0
        self.usdc += reward
        rec = self._record("onchain_claim", {"action": "claim", "asset": asset,
                                              "rewards_usd": round(reward, 6)})
        return {"ok": True, "message": f"Claimed {reward:,.4f} USDC of {asset} staking rewards.",
                "receipt": rec, "state": self.state()}

    def swap(self, from_sym: str, to_sym: str, amount_usd: float) -> dict:
        from_sym = (from_sym or "").upper().strip()
        to_sym = (to_sym or "").upper().strip()
        if from_sym == to_sym:
            return {"ok": False, "error": "Pick two different assets to swap."}
        if to_sym not in SWAP_PRICES:
            return {"ok": False, "error": f"Can't swap into {to_sym}. Options: {', '.join(SWAP_PRICES)}."}
        amount_usd = _num(amount_usd)
        # Only USDC-funded swaps in the paper book (keeps balances simple and honest).
        if from_sym != "USDC":
            return {"ok": False, "error": "Paper swaps are funded from your USDC balance — set 'from' to USDC."}
        ok, reason = self._screen(amount_usd)
        if not ok:
            self._record("onchain_swap_denied", {"from": from_sym, "to": to_sym,
                                                  "amount_usd": amount_usd, "reason": reason})
            return {"ok": False, "error": reason}
        price = SWAP_PRICES.get(to_sym, 1.0)
        fee = amount_usd * 0.001
        units = (amount_usd - fee) / price
        self.usdc -= amount_usd
        self.tokens[to_sym] = self.tokens.get(to_sym, 0.0) + units
        rec = self._record("onchain_swap", {"action": "swap", "from": from_sym, "to": to_sym,
                                             "amount_usd": round(amount_usd, 2),
                                             "received": round(units, 8), "fee_usd": round(fee, 4)})
        return {"ok": True, "message": f"Swapped {amount_usd:,.2f} USDC for {units:.6f} {to_sym} "
                f"(fee {fee:,.2f} USDC).", "receipt": rec, "state": self.state()}

    def pay(self, to: str, amount_usd: float, memo: str = "") -> dict:
        to = (to or "").strip()
        if not to:
            return {"ok": False, "error": "Name a recipient (an agent id, label, or address)."}
        amount_usd = _num(amount_usd)
        ok, reason = self._screen(amount_usd)
        if not ok:
            self._record("agent_payment_denied", {"to": to, "amount_usd": amount_usd, "reason": reason})
            return {"ok": False, "error": reason}
        self.usdc -= amount_usd
        txid = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:24]  # paper tx id, 64-hex shape
        rec = self._record("agent_payment", {"action": "pay", "to": to,
                                             "amount_usd": round(amount_usd, 2),
                                             "memo": memo[:120], "tx": txid,
                                             "network": "Base Sepolia (paper)"})
        return {"ok": True, "message": f"Paid {amount_usd:,.2f} USDC to {to}.",
                "receipt": rec, "state": self.state()}

    # -- state ------------------------------------------------------------- #

    def state(self) -> dict:
        self._accrue()
        stakes = [{"asset": a, "staked_usd": round(s["amount"], 2),
                   "apy": s["apy"], "rewards_usd": round(s.get("rewards", 0.0), 6)}
                  for a, s in self.stakes.items() if s["amount"] > 0 or s.get("rewards", 0.0) > 0]
        tokens = [{"asset": k, "amount": round(v, 8)} for k, v in self.tokens.items() if v > 0]
        total_staked = sum(x["staked_usd"] for x in stakes)
        return {
            "usdc": round(self.usdc, 2),
            "total_staked_usd": round(total_staked, 2),
            "stakes": stakes,
            "tokens": tokens,
            "receipts": self.receipts,
            "assets": list(STAKE_ASSETS.keys()),
            "swap_targets": [s for s in SWAP_PRICES if s != "USDC"],
            "policy": {"max_action_usd": MAX_ACTION_USD,
                       "max_fraction": MAX_ACTION_FRACTION},
        }


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
