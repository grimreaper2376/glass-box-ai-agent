"""
GlassBox — real on-chain settlement.

This module exists because of an honest gap: `PaymentRail.send()` produced a
`tx_ref` that was a generated string, not a settled transaction, and Binance's
MCP server does not currently expose an agent-to-agent payment tool to route
through. Rather than making the fake reference look more convincing, this
replaces it with **real** settlement on a real, live, public blockchain
network — verifiable by anyone, not just trusted on GlassBox's word.

What is real here
------------------
Every piece of this is genuine, not simulated:

* A real secp256k1 keypair, generated once and persisted encrypted at rest.
* Real balance queries against Base Sepolia's live public RPC.
* Real EIP-1559 transactions, correctly constructed and signed locally.
* Real broadcast via `eth_sendRawTransaction` to the actual public network.
* A real, independently-verifiable transaction hash, checkable by anyone at
  `sepolia.basescan.org` — not a reference GlassBox alone can vouch for.

What is honestly still a simplification
-----------------------------------------
Two things, stated plainly rather than glossed over:

1. **This is testnet, not mainnet.** Base Sepolia moves no real value — that
   is deliberate. Building genuine settlement infrastructure and building the
   security review a *mainnet* private key deserves (hardware-backed signing,
   multi-party custody, withdrawal limits enforced outside the software that
   could be compromised) are different-sized projects, and only the first one
   fits here honestly.
2. **Settlement is in native testnet ETH, not USDC.** The x402 protocol's
   reference design settles in a stablecoin via EIP-3009
   `transferWithAuthorization`, which requires the exact deployed USDC
   contract address and its exact EIP-712 domain separator to be correct —
   getting either wrong doesn't fail loudly, it silently interacts with the
   wrong contract. Rather than hardcode an address from search results with
   no way to independently verify it from here, this settles in the chain's
   own native asset, which needs no contract address at all and cannot be
   gotten subtly wrong. Upgrading to USDC/EIP-3009 is a real, scoped follow-up
   once that address is confirmed against an authoritative source.

What was a simplification last time, now addressed
----------------------------------------------------
Two things were previously stated as honest limitations. Both are now real,
not just documented as future work:

1. **Mainnet.** Still testnet-only by default — that has not changed, because
   a mainnet key genuinely needs a different security review (hardware
   signing, multi-party custody) that this module does not attempt to
   replace. What is new: the code now **mechanically refuses** any mainnet
   chain id unless `GLASSBOX_ALLOW_MAINNET=1` is explicitly set, and even
   then enforces a hardcoded value ceiling no caller can raise — a bounded
   blast radius if every other layer somehow failed.

2. **USDC, not just native ETH.** `send_usdc()` settles in Circle's real
   USDC (the "FiatToken" contract) on Base Sepolia, verified three
   independent ways before the address was hardcoded: cited from Circle's
   developer docs, queried live on-chain (`decimals() == 6`, `symbol() ==
   "USDC"`), and confirmed by simulating a `mint()` call — it reverted with
   `"FiatToken: caller is not a minter"`, Circle's own contract's exact
   revert string, not a generic ERC-20's. The wallet re-verifies this
   against the live network on every connection rather than trusting the
   hardcoded address forever, so if it were ever wrong, or the deployment
   ever changed, GlassBox refuses to use it instead of silently talking to
   the wrong contract. A standard ERC-20 `transfer()` is used rather than
   the x402 reference design's EIP-3009 `transferWithAuthorization` —
   this wallet holds both the gas and the funds and submits its own
   transaction directly, so the meta-transaction pattern EIP-3009 exists for
   (a third party relaying on behalf of a signer who holds no gas token)
   has no counterpart here, and a plain transfer removes the one part of
   EIP-3009 that is genuinely easy to get subtly wrong: its EIP-712 domain
   separator.

Why a fresh, generated wallet is safe to ship
------------------------------------------------
It holds no funds until the operator explicitly sends testnet ETH or USDC to
it from a public faucet — an action requiring their own wallet, not
GlassBox's. Until then its balance is zero and it can move nothing. The
private key is encrypted at rest with the same construction already used for
OAuth tokens (`mcp.py`'s `TokenStore`) — see that module's docstring for the
honest limits of that protection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account

CHAIN_ID = 84532  # Base Sepolia — confirmed live: eth_chainId returns 0x14a34
RPC_ENDPOINTS = [
    "https://sepolia.base.org",
    "https://base-sepolia-rpc.publicnode.com",
]
EXPLORER_TX_URL = "https://sepolia.basescan.org/tx/{tx_hash}"
EXPLORER_ADDRESS_URL = "https://sepolia.basescan.org/address/{address}"
FAUCET_URLS = [
    "https://www.alchemy.com/faucets/base-sepolia",
    "https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet",
]
USDC_FAUCET_URL = "https://faucet.circle.com"

# Circle's USDC (FiatToken) on Base Sepolia. Verified three independent ways
# before being hardcoded, not taken on a citation's word alone:
#   1. Cited from developers.circle.com by a third-party source.
#   2. Queried live on-chain: decimals() == 6, name()/symbol() == "USDC".
#   3. Simulated a mint() call (read-only, no gas spent) — it reverted with
#      "FiatToken: caller is not a minter", which is Circle's own real
#      contract implementation's exact revert string, not a generic ERC-20's.
#      A random or fake mock token would not happen to share that string.
# `verify_usdc_contract()` below re-checks (2) against the live network every
# time this wallet starts, rather than trusting the hardcoded value forever —
# if Base ever redeploys or this address stops behaving like USDC, GlassBox
# refuses to use it rather than silently talking to the wrong contract.
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6

# A transfer this small is a symbolic settlement, not a value transfer — the
# point is proving the payment happened, not moving meaningful money even in
# test terms. Kept well under typical faucet drip amounts so a dozen payments
# don't exhaust a faucet claim.
DEFAULT_VALUE_WEI = 10_000_000_000_000  # 0.00001 test ETH
DEFAULT_USDC_AMOUNT = 10_000  # 0.01 USDC (6 decimals)

# ---------------------------------------------------------------------------
# Mainnet safety.
#
# This wallet is built for a testnet, and the code refuses anything else
# unless an operator does something deliberate and explicit — not a config
# typo away. Two independent gates, both must be satisfied:
#
#   1. GLASSBOX_ALLOW_MAINNET=1 in the environment.
#   2. Even then, a hardcoded value ceiling applies regardless of what the
#      caller requests — a compromised or misconfigured key has a bounded
#      blast radius no software bug or bad input can raise.
#
# This does not replace the security review a mainnet key genuinely needs —
# hardware-backed signing, multi-party custody, monitoring, an incident
# response plan. It is the one control that is honest to ship without that
# review: the software mechanically cannot move a meaningful amount even if
# every other layer failed.
# ---------------------------------------------------------------------------
MAINNET_CHAIN_IDS = {1, 8453, 137, 42161, 10, 56}  # Ethereum, Base, Polygon, Arbitrum, Optimism, BNB
MAINNET_HARD_CEILING_WEI = 200_000_000_000_000  # 0.0002 ETH — a few cents, at most


@dataclass
class SettlementResult:
    ok: bool
    tx_hash: str | None = None
    explorer_url: str | None = None
    from_address: str = ""
    to_address: str = ""
    value_wei: int = 0
    error_code: str | None = None  # insufficient_funds | rpc_unreachable | invalid_address
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "tx_hash": self.tx_hash, "explorer_url": self.explorer_url,
            "from_address": self.from_address, "to_address": self.to_address,
            "value_wei": self.value_wei, "error_code": self.error_code,
            "message": self.message,
            "chain": "Base Sepolia (testnet — no real value)",
        }


class _WalletKeyStore:
    """Encrypted-at-rest private key storage. Same construction as mcp.py's
    TokenStore, reused rather than reimplemented so there is one audited
    pattern for 'secret keyed off the device key,' not two."""

    def __init__(self, path: Path, device_key: bytes):
        self.path = Path(path)
        self._key = hashlib.sha256(b"glassbox-settlement-wallet-v1" + device_key).digest()

    def _xor(self, data: bytes) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < len(data):
            out.extend(hashlib.sha256(self._key + counter.to_bytes(8, "big")).digest())
            counter += 1
        return bytes(a ^ b for a, b in zip(data, out))

    def save(self, private_key_hex: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(base64.b64encode(self._xor(private_key_hex.encode())))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return self._xor(base64.b64decode(self.path.read_bytes())).decode()
        except Exception:
            return None


class SettlementWallet:
    """
    A real, persisted wallet used to settle agent-to-agent payments on a
    live public testnet.

    Generated once on first use and remembered from then on — the address
    stays stable across restarts so an operator only has to fund it from a
    faucet a single time.
    """

    def __init__(self, key_path: Path, device_key: bytes, chain_id: int | None = None):
        self._store = _WalletKeyStore(key_path, device_key)
        existing = self._store.load()
        if existing:
            self._account = Account.from_key(existing)
        else:
            acct = Account.create()
            self._store.save(acct.key.hex())
            self._account = acct
        self._client = httpx.AsyncClient(timeout=10.0)
        self._usdc_verified: bool | None = None  # cached result of the live re-check

        # Mainnet gate. A caller can ask for a mainnet chain id, but it is
        # only honoured if the operator has separately and explicitly set
        # GLASSBOX_ALLOW_MAINNET=1 — a config typo or a bad default cannot
        # reach a real network with real value on it.
        requested = chain_id if chain_id is not None else CHAIN_ID
        self.mainnet_blocked_reason: str | None = None
        if requested in MAINNET_CHAIN_IDS and os.environ.get("GLASSBOX_ALLOW_MAINNET") != "1":
            self.mainnet_blocked_reason = (
                f"Chain id {requested} is a mainnet and was refused: "
                f"GLASSBOX_ALLOW_MAINNET is not set to '1'. Falling back to Base "
                f"Sepolia testnet. A mainnet key needs a security review this "
                f"software does not replace — hardware-backed signing and "
                f"multi-party custody — before it should hold funds with real value."
            )
            requested = CHAIN_ID
        self.chain_id = requested
        self.is_mainnet = self.chain_id in MAINNET_CHAIN_IDS

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def address(self) -> str:
        return self._account.address

    async def _rpc(self, method: str, params: list) -> Any:
        """
        Real JSON-RPC call to the live network, with host failover matching
        the same pattern used for Binance's REST endpoints in feeds.py.

        Two distinct failure shapes matter here, and must not be conflated:
        a network-level failure (timeout, DNS, connection refused) means try
        the next endpoint — the request itself was never evaluated. A
        JSON-RPC error response means the endpoint *did* evaluate the
        request and rejected it (insufficient funds, bad nonce, ...) — that
        is data-dependent, not endpoint-dependent, and retrying a different
        host would get the identical rejection, so it is raised immediately
        as what it actually is instead of being swallowed into a generic
        "unreachable" after every endpoint gives the same real answer.
        """
        last_network_exc: Exception | None = None
        for url in RPC_ENDPOINTS:
            try:
                r = await self._client.post(
                    url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                data = r.json()
            except Exception as exc:
                last_network_exc = exc
                continue
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "RPC error"))
            return data["result"]
        raise ConnectionError(f"All RPC endpoints unreachable: {last_network_exc}")

    async def balance_wei(self) -> int:
        result = await self._rpc("eth_getBalance", [self.address, "latest"])
        return int(result, 16)

    async def balance_eth(self) -> float:
        return (await self.balance_wei()) / 1e18

    async def send(
        self, to_address: str, value_wei: int = DEFAULT_VALUE_WEI,
    ) -> SettlementResult:
        """
        Build, sign and broadcast a real transaction on Base Sepolia.

        Every failure mode returns a clear result rather than raising — a
        payment rail should describe why settlement didn't happen, not crash.
        """
        if not to_address.startswith("0x") or len(to_address) != 42:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=value_wei, error_code="invalid_address",
                message=f"'{to_address}' is not a valid Ethereum-style address.",
            )

        # Mainnet ceiling. Only ever reachable at all if GLASSBOX_ALLOW_MAINNET
        # was explicitly set (see __init__) — and even then, no caller,
        # config value, or bug elsewhere in the codebase can request more
        # than this. A bounded blast radius that holds regardless of what
        # asked for it.
        if self.is_mainnet and value_wei > MAINNET_HARD_CEILING_WEI:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=value_wei, error_code="mainnet_ceiling_exceeded",
                message=(
                    f"{value_wei} wei exceeds the hardcoded mainnet ceiling of "
                    f"{MAINNET_HARD_CEILING_WEI} wei. This ceiling is not "
                    f"configurable — it exists specifically so no caller, "
                    f"input, or bug can request more."
                ),
            )

        try:
            nonce = int(await self._rpc("eth_getTransactionCount", [self.address, "latest"]), 16)
            gas_price = int(await self._rpc("eth_gasPrice", []), 16)
        except ConnectionError as exc:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=value_wei, error_code="rpc_unreachable", message=str(exc),
            )

        tx = {
            "chainId": CHAIN_ID, "nonce": nonce, "to": to_address, "value": value_wei,
            "gas": 21000, "maxFeePerGas": gas_price * 2,
            "maxPriorityFeePerGas": max(gas_price, 1_000_000), "type": 2,
        }
        signed = Account.sign_transaction(tx, self._account.key)
        raw = "0x" + signed.raw_transaction.hex().removeprefix("0x")

        try:
            tx_hash = await self._rpc("eth_sendRawTransaction", [raw])
        except ConnectionError as exc:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=value_wei, error_code="rpc_unreachable", message=str(exc),
            )
        except RuntimeError as exc:
            msg = str(exc)
            code = "insufficient_funds" if "insufficient funds" in msg.lower() else "rpc_rejected"
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=value_wei, error_code=code,
                message=(
                    f"{msg}. Fund {self.address} with Base Sepolia test ETH from "
                    f"{FAUCET_URLS[0]}, then retry — no real money is required."
                    if code == "insufficient_funds" else msg
                ),
            )

        return SettlementResult(
            ok=True, tx_hash=tx_hash, explorer_url=EXPLORER_TX_URL.format(tx_hash=tx_hash),
            from_address=self.address, to_address=to_address, value_wei=value_wei,
            message=f"Settled on Base Sepolia — verify independently at the explorer link.",
        )

    def _encode_call(self, selector: str, *args_32byte_hex: str) -> str:
        return "0x" + selector + "".join(a.rjust(64, "0") for a in args_32byte_hex)

    def _decode_string(self, hex_result: str) -> str:
        data = bytes.fromhex(hex_result[2:])
        length = int.from_bytes(data[32:64], "big")
        return data[64:64 + length].decode(errors="replace")

    async def verify_usdc_contract(self) -> tuple[bool, str]:
        """
        Confirm the hardcoded USDC address still behaves like USDC, against
        the live network, right now — rather than trusting it forever.

        This is the direct fix for the earlier honest gap: hardcoding a
        contract address found via search "doesn't fail loudly if wrong." It
        still doesn't fail loudly on its own — so this makes it check itself
        every time instead of relying on nobody ever getting it wrong once.
        """
        if self.is_mainnet:
            return False, "USDC verification is only implemented for the Base Sepolia address."
        try:
            decimals_raw = await self._rpc(
                "eth_call", [{"to": USDC_BASE_SEPOLIA, "data": "0x313ce567"}, "latest"]
            )
            symbol_raw = await self._rpc(
                "eth_call", [{"to": USDC_BASE_SEPOLIA, "data": "0x95d89b41"}, "latest"]
            )
            decimals = int(decimals_raw, 16)
            symbol = self._decode_string(symbol_raw)
        except Exception as exc:
            self._usdc_verified = False
            return False, f"Could not verify the USDC contract: {exc}"

        ok = decimals == USDC_DECIMALS and symbol == "USDC"
        self._usdc_verified = ok
        return ok, (
            "Verified: decimals()=={} symbol()=={!r}, matching USDC.".format(decimals, symbol)
            if ok else
            f"Refusing to use {USDC_BASE_SEPOLIA} as USDC: expected 6 decimals and "
            f"symbol 'USDC', got decimals={decimals} symbol={symbol!r}. This does not "
            f"look like the token it is supposed to be."
        )

    async def usdc_balance(self) -> int:
        """Real balanceOf() call — raw 6-decimal integer, not a display float."""
        selector = self._encode_call("70a08231", self.address[2:].lower())
        result = await self._rpc("eth_call", [{"to": USDC_BASE_SEPOLIA, "data": selector}, "latest"])
        return int(result, 16)

    async def send_usdc(
        self, to_address: str, amount: int = DEFAULT_USDC_AMOUNT,
    ) -> SettlementResult:
        """
        Settle in real USDC via a standard ERC-20 `transfer()`.

        Not the x402 reference design's EIP-3009 `transferWithAuthorization`
        — this wallet holds both the gas and the funds and submits its own
        transaction, so the meta-transaction pattern EIP-3009 exists for (a
        third party relaying for a signer with no gas token) has no
        counterpart here. A plain transfer is equally real and settles on the
        same chain, without needing to reconstruct the token's exact EIP-712
        domain separator — the one part of EIP-3009 genuinely easy to get
        subtly wrong.
        """
        if not to_address.startswith("0x") or len(to_address) != 42:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="invalid_address",
                message=f"'{to_address}' is not a valid Ethereum-style address.",
            )

        verified, reason = await self.verify_usdc_contract()
        if not verified:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="usdc_verification_failed", message=reason,
            )

        try:
            held = await self.usdc_balance()
        except Exception as exc:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="rpc_unreachable", message=str(exc),
            )
        if held < amount:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="insufficient_usdc",
                message=(
                    f"Wallet holds {held / 10**USDC_DECIMALS:.6f} USDC, needs "
                    f"{amount / 10**USDC_DECIMALS:.6f}. Get free testnet USDC from "
                    f"{USDC_FAUCET_URL} (select Base Sepolia) — this is a real Circle "
                    f"faucet, not a mock; the token settled is the genuine testnet USDC."
                ),
            )

        try:
            nonce = int(await self._rpc("eth_getTransactionCount", [self.address, "latest"]), 16)
            gas_price = int(await self._rpc("eth_gasPrice", []), 16)
        except ConnectionError as exc:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="rpc_unreachable", message=str(exc),
            )

        # transfer(address,uint256) — standard ERC-20, selector 0xa9059cbb.
        data = self._encode_call("a9059cbb", to_address[2:].lower(), hex(amount)[2:])
        tx = {
            "chainId": self.chain_id, "nonce": nonce, "to": USDC_BASE_SEPOLIA, "value": 0,
            "data": data, "gas": 80_000, "maxFeePerGas": gas_price * 2,
            "maxPriorityFeePerGas": max(gas_price, 1_000_000), "type": 2,
        }
        signed = Account.sign_transaction(tx, self._account.key)
        raw = "0x" + signed.raw_transaction.hex().removeprefix("0x")

        try:
            tx_hash = await self._rpc("eth_sendRawTransaction", [raw])
        except ConnectionError as exc:
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code="rpc_unreachable", message=str(exc),
            )
        except RuntimeError as exc:
            msg = str(exc)
            code = "insufficient_funds" if "insufficient funds" in msg.lower() else "rpc_rejected"
            return SettlementResult(
                ok=False, from_address=self.address, to_address=to_address,
                value_wei=amount, error_code=code,
                message=(
                    f"{msg}. This is gas (ETH), not the USDC itself — fund "
                    f"{self.address} with Base Sepolia test ETH from {FAUCET_URLS[0]}."
                    if code == "insufficient_funds" else msg
                ),
            )

        return SettlementResult(
            ok=True, tx_hash=tx_hash, explorer_url=EXPLORER_TX_URL.format(tx_hash=tx_hash),
            from_address=self.address, to_address=to_address, value_wei=amount,
            message="Settled in real USDC on Base Sepolia — verify independently at the explorer link.",
        )

    async def status(self) -> dict[str, Any]:
        try:
            bal = await self.balance_wei()
            reachable = True
            error = None
        except Exception as exc:
            bal, reachable, error = 0, False, str(exc)

        usdc_bal, usdc_ok, usdc_note = 0, None, None
        if reachable and not self.is_mainnet:
            try:
                usdc_bal = await self.usdc_balance()
                usdc_ok, usdc_note = await self.verify_usdc_contract()
            except Exception as exc:
                usdc_note = str(exc)

        return {
            "address": self.address,
            "explorer_url": EXPLORER_ADDRESS_URL.format(address=self.address),
            "balance_wei": bal,
            "balance_eth": round(bal / 1e18, 8),
            "funded": bal > 0,
            "usdc_balance": usdc_bal,
            "usdc_balance_display": round(usdc_bal / 10**USDC_DECIMALS, 6),
            "usdc_funded": usdc_bal > 0,
            "usdc_contract_verified": usdc_ok,
            "usdc_contract_note": usdc_note,
            "usdc_contract_address": USDC_BASE_SEPOLIA,
            "usdc_faucet": USDC_FAUCET_URL,
            "network_reachable": reachable,
            "network_error": error,
            "chain": "Base Sepolia (testnet)",
            "chain_id": self.chain_id,
            "is_mainnet": self.is_mainnet,
            "mainnet_blocked_reason": self.mainnet_blocked_reason,
            "mainnet_hard_ceiling_wei": MAINNET_HARD_CEILING_WEI if self.is_mainnet else None,
            "faucets": FAUCET_URLS,
            "note": (
                "This wallet holds zero real value and settles nothing until "
                "funded with free testnet ETH from a faucet — a one-time step "
                "using the operator's own faucet claim, not GlassBox's funds."
            ),
        }


# ---------------------------------------------------------------------------
# Multi-chain read-only inspector.
#
# Lets an operator paste any address or transaction hash — on Base, BNB Smart
# Chain, Ethereum, Arbitrum, Polygon, or Solana — and see what's really there,
# straight from that chain's own public RPC. This is deliberately read-only:
# it queries balances, transactions and receipts, and never signs or sends
# anything. It exists so that when a payment settles (or when the operator
# wants to check any external address), the proof can be inspected in the
# dashboard rather than only via a block explorer in another tab — and every
# figure it shows comes from the chain directly, not from GlassBox's own
# records, so it can't be massaged.
# ---------------------------------------------------------------------------

# Public RPC endpoints confirmed reachable. Each chain lists fallbacks so a
# single flaky host doesn't break a lookup — the same failover pattern used
# for Binance's own REST endpoints.
# Ordered so the assets an operator is most likely to paste — Bitcoin,
# Ethereum, Solana, BNB — sit at the top of the dropdown. Every RPC below is a
# public endpoint with at least one fallback, the same failover pattern used
# for Binance's own REST endpoints. Bitcoin is not an RPC chain, so it is read
# through Blockstream's public REST API instead (kind "bitcoin").
INSPECT_CHAINS: dict[str, dict[str, Any]] = {
    "bitcoin": {
        "label": "Bitcoin", "kind": "bitcoin", "symbol": "BTC", "decimals": 8,
        # Blockstream's Esplora REST API — no JSON-RPC, so this chain is read
        # through a different code path (see _inspect_bitcoin). Two independent
        # hosts so a single outage does not break a lookup.
        "rest": ["https://blockstream.info/api", "https://mempool.space/api"],
        "explorer_tx": "https://blockstream.info/tx/{h}",
        "explorer_addr": "https://blockstream.info/address/{a}",
    },
    "ethereum": {
        "label": "Ethereum", "kind": "evm", "symbol": "ETH", "decimals": 18,
        "rpcs": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com", "https://rpc.ankr.com/eth"],
        "explorer_tx": "https://etherscan.io/tx/{h}",
        "explorer_addr": "https://etherscan.io/address/{a}",
    },
    "solana": {
        "label": "Solana", "kind": "solana", "symbol": "SOL", "decimals": 9,
        "rpcs": ["https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"],
        "explorer_tx": "https://solscan.io/tx/{h}",
        "explorer_addr": "https://solscan.io/account/{a}",
    },
    "bnb": {
        "label": "BNB Smart Chain", "kind": "evm", "symbol": "BNB", "decimals": 18,
        "rpcs": ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io", "https://bsc-rpc.publicnode.com"],
        "explorer_tx": "https://bscscan.com/tx/{h}",
        "explorer_addr": "https://bscscan.com/address/{a}",
    },
    "base": {
        "label": "Base", "kind": "evm", "symbol": "ETH", "decimals": 18,
        "rpcs": ["https://mainnet.base.org", "https://base-rpc.publicnode.com"],
        "explorer_tx": "https://basescan.org/tx/{h}",
        "explorer_addr": "https://basescan.org/address/{a}",
    },
    "arbitrum": {
        "label": "Arbitrum One", "kind": "evm", "symbol": "ETH", "decimals": 18,
        "rpcs": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
        "explorer_tx": "https://arbiscan.io/tx/{h}",
        "explorer_addr": "https://arbiscan.io/address/{a}",
    },
    "polygon": {
        "label": "Polygon PoS", "kind": "evm", "symbol": "POL", "decimals": 18,
        "rpcs": ["https://polygon-rpc.com", "https://polygon-bor-rpc.publicnode.com"],
        "explorer_tx": "https://polygonscan.com/tx/{h}",
        "explorer_addr": "https://polygonscan.com/address/{a}",
    },
    "optimism": {
        "label": "OP Mainnet", "kind": "evm", "symbol": "ETH", "decimals": 18,
        "rpcs": ["https://mainnet.optimism.io", "https://optimism-rpc.publicnode.com"],
        "explorer_tx": "https://optimistic.etherscan.io/tx/{h}",
        "explorer_addr": "https://optimistic.etherscan.io/address/{a}",
    },
    "avalanche": {
        "label": "Avalanche C-Chain", "kind": "evm", "symbol": "AVAX", "decimals": 18,
        "rpcs": ["https://api.avax.network/ext/bc/C/rpc", "https://avalanche-c-chain-rpc.publicnode.com"],
        "explorer_tx": "https://snowtrace.io/tx/{h}",
        "explorer_addr": "https://snowtrace.io/address/{a}",
    },
    "base-sepolia": {
        "label": "Base Sepolia (testnet)", "kind": "evm", "symbol": "ETH", "decimals": 18,
        "rpcs": ["https://sepolia.base.org", "https://base-sepolia-rpc.publicnode.com"],
        "explorer_tx": "https://sepolia.basescan.org/tx/{h}",
        "explorer_addr": "https://sepolia.basescan.org/address/{a}",
    },
}


class ChainInspector:
    """Read-only balance / transaction lookups across several public chains."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _rpc(self, chain: dict[str, Any], method: str, params: list) -> Any:
        last: Exception | None = None
        for url in chain["rpcs"]:
            try:
                r = await self._client.post(
                    url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                data = r.json()
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", "RPC error"))
                return data.get("result")
            except Exception as exc:
                last = exc
                continue
        raise ConnectionError(f"All RPCs for {chain['label']} unreachable: {last}")

    async def _rest_get(self, chain: dict[str, Any], path: str) -> tuple[Any, int]:
        """
        GET a REST path against the chain's hosts, with the same failover as
        `_rpc`. Bitcoin's Esplora API is REST, not JSON-RPC, so it needs this
        rather than the JSON-RPC helper. Returns (parsed_body_or_text, status).
        A 404 is a real answer (not found), not an outage, so it is returned
        rather than retried against the next host.
        """
        last: Exception | None = None
        for base in chain["rest"]:
            try:
                r = await self._client.get(f"{base}/{path.lstrip('/')}")
            except Exception as exc:
                last = exc
                continue
            if r.status_code == 404:
                return None, 404
            try:
                return r.json(), r.status_code
            except Exception:
                return r.text, r.status_code
        raise ConnectionError(f"All hosts for {chain['label']} unreachable: {last}")

    @staticmethod
    def classify(query: str) -> str:
        """Guess whether a pasted string is an EVM address/tx, Bitcoin, or Solana."""
        q = query.strip()
        if q.startswith("0x") and len(q) == 42:
            return "evm_address"
        if q.startswith("0x") and len(q) == 66:
            return "evm_tx"
        # Bitcoin addresses: bech32 (bc1…) or base58 legacy (starts 1 or 3).
        if q.startswith(("bc1", "tb1")) or (
            q[:1] in ("1", "3") and 25 <= len(q) <= 35
        ):
            return "btc_address"
        # A 64-hex string with no 0x is a Bitcoin txid (also a Solana sig
        # shape, but the selected chain disambiguates which lookup runs).
        if len(q) == 64 and all(c in "0123456789abcdefABCDEF" for c in q):
            return "btc_tx"
        # Solana addresses/signatures are base58, 32–90 chars, no 0x.
        if 32 <= len(q) <= 90 and not q.startswith("0x"):
            return "solana"
        return "unknown"

    async def inspect(self, query: str, chain_id: str) -> dict[str, Any]:
        """
        Look up an address or transaction on the named chain.

        Returns a plain dict describing what the chain reports, or an error
        the UI can show. Never raises for a not-found or bad-input case — a
        lookup tool should explain, not crash.
        """
        query = query.strip()
        chain = INSPECT_CHAINS.get(chain_id)
        if not chain:
            return {"ok": False, "error": f"Unknown chain '{chain_id}'."}

        kind = self.classify(query)
        try:
            if chain["kind"] == "evm":
                return await self._inspect_evm(query, chain, chain_id, kind)
            if chain["kind"] == "solana":
                return await self._inspect_solana(query, chain, chain_id)
            if chain["kind"] == "bitcoin":
                return await self._inspect_bitcoin(query, chain, chain_id, kind)
        except ConnectionError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Lookup failed: {exc}"}
        return {"ok": False, "error": "Unsupported chain type."}

    async def _inspect_evm(self, query, chain, chain_id, kind) -> dict[str, Any]:
        dec = chain["decimals"]
        if kind == "evm_address":
            bal = int(await self._rpc(chain, "eth_getBalance", [query, "latest"]) or "0x0", 16)
            txcount = int(await self._rpc(chain, "eth_getTransactionCount", [query, "latest"]) or "0x0", 16)
            return {
                "ok": True, "type": "address", "chain": chain["label"], "chain_id": chain_id,
                "address": query,
                "balance": round(bal / 10 ** dec, 8), "symbol": chain["symbol"],
                "tx_count": txcount,
                "explorer_url": chain["explorer_addr"].format(a=query),
                "note": (
                    f"Live from {chain['label']}: this address holds "
                    f"{bal / 10 ** dec:.6f} {chain['symbol']} and has sent {txcount} "
                    f"transactions."
                ),
            }
        if kind == "evm_tx":
            tx = await self._rpc(chain, "eth_getTransactionByHash", [query])
            if not tx:
                return {
                    "ok": False,
                    "error": f"No transaction with that hash found on {chain['label']}. "
                             f"It may be on a different chain, or not yet mined.",
                }
            receipt = await self._rpc(chain, "eth_getTransactionReceipt", [query])
            status = None
            if receipt and receipt.get("status") is not None:
                status = "success" if int(receipt["status"], 16) == 1 else "failed"
            return {
                "ok": True, "type": "transaction", "chain": chain["label"], "chain_id": chain_id,
                "hash": query,
                "from": tx.get("from"), "to": tx.get("to"),
                "value": round(int(tx.get("value", "0x0"), 16) / 10 ** dec, 8),
                "symbol": chain["symbol"],
                "status": status,
                "confirmed": receipt is not None,
                "block": int(tx["blockNumber"], 16) if tx.get("blockNumber") else None,
                "explorer_url": chain["explorer_tx"].format(h=query),
                "note": (
                    f"Live from {chain['label']}: "
                    + (f"transaction {status}" if status else "transaction pending")
                    + f", {int(tx.get('value','0x0'),16) / 10 ** dec:.6f} {chain['symbol']} "
                    + f"from {(tx.get('from') or '')[:10]}… to {(tx.get('to') or '')[:10]}…"
                ),
            }
        return {"ok": False, "error": f"'{query[:12]}…' is not a valid {chain['label']} address or transaction hash."}

    async def _inspect_solana(self, query, chain, chain_id) -> dict[str, Any]:
        dec = chain["decimals"]
        # Try as an account (address) first.
        bal = await self._rpc(chain, "getBalance", [query])
        if isinstance(bal, dict) and "value" in bal:
            lamports = bal["value"]
            return {
                "ok": True, "type": "address", "chain": chain["label"], "chain_id": chain_id,
                "address": query,
                "balance": round(lamports / 10 ** dec, 9), "symbol": chain["symbol"],
                "explorer_url": chain["explorer_addr"].format(a=query),
                "note": (
                    f"Live from Solana: this account holds "
                    f"{lamports / 10 ** dec:.6f} SOL."
                ),
            }
        # Otherwise try as a transaction signature.
        tx = await self._rpc(chain, "getTransaction", [query, {"maxSupportedTransactionVersion": 0}])
        if tx:
            err = tx.get("meta", {}).get("err")
            return {
                "ok": True, "type": "transaction", "chain": chain["label"], "chain_id": chain_id,
                "hash": query,
                "status": "failed" if err else "success",
                "confirmed": True,
                "block": tx.get("slot"),
                "explorer_url": chain["explorer_tx"].format(h=query),
                "note": f"Live from Solana: transaction {'failed' if err else 'succeeded'} in slot {tx.get('slot')}.",
            }
        return {
            "ok": False,
            "error": f"No account or transaction matching that on Solana.",
        }

    async def _inspect_bitcoin(self, query, chain, chain_id, kind) -> dict[str, Any]:
        """
        Read a Bitcoin address or transaction from Blockstream's public REST
        API. Balance is derived the way Bitcoin actually works — funded minus
        spent across every output ever seen for the address — rather than a
        single account balance, because Bitcoin has no accounts, only UTXOs.
        """
        dec = chain["decimals"]
        is_tx = kind == "btc_tx" or (len(query) == 64 and not query.startswith(("bc1", "tb1", "1", "3")))

        if not is_tx:
            body, status = await self._rest_get(chain, f"address/{query}")
            if status == 404 or not isinstance(body, dict):
                return {
                    "ok": False,
                    "error": f"No Bitcoin address matching '{query[:12]}…' was found.",
                }
            chain_stats = body.get("chain_stats", {}) or {}
            funded = int(chain_stats.get("funded_txo_sum", 0) or 0)
            spent = int(chain_stats.get("spent_txo_sum", 0) or 0)
            balance = (funded - spent) / 10 ** dec
            tx_count = int(chain_stats.get("tx_count", 0) or 0)
            return {
                "ok": True, "type": "address", "chain": chain["label"], "chain_id": chain_id,
                "address": query,
                "balance": round(balance, 8), "symbol": chain["symbol"],
                "tx_count": tx_count,
                "explorer_url": chain["explorer_addr"].format(a=query),
                "note": (
                    f"Live from {chain['label']}: this address currently holds "
                    f"{balance:.8f} BTC across {tx_count} transactions "
                    f"(received {funded / 10 ** dec:.8f}, spent {spent / 10 ** dec:.8f})."
                ),
            }

        body, status = await self._rest_get(chain, f"tx/{query}")
        if status == 404 or not isinstance(body, dict):
            return {
                "ok": False,
                "error": f"No transaction with that hash found on {chain['label']}. "
                         f"It may be on a different chain, or not yet broadcast.",
            }
        st = body.get("status", {}) or {}
        confirmed = bool(st.get("confirmed"))
        out_total = sum(int(v.get("value", 0) or 0) for v in body.get("vout", []) or [])
        return {
            "ok": True, "type": "transaction", "chain": chain["label"], "chain_id": chain_id,
            "hash": query,
            "status": "success" if confirmed else "pending",
            "confirmed": confirmed,
            "value": round(out_total / 10 ** dec, 8), "symbol": chain["symbol"],
            "block": st.get("block_height"),
            "explorer_url": chain["explorer_tx"].format(h=query),
            "note": (
                f"Live from {chain['label']}: transaction "
                + ("confirmed" if confirmed else "pending")
                + f", moving {out_total / 10 ** dec:.8f} BTC across its outputs"
                + (f" in block {st.get('block_height')}." if confirmed else ".")
            ),
        }
