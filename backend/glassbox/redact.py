"""
GlassBox — redaction.

Every payload bound for the ledger, the websocket, or a log line passes through
here first. An LLM analyst that quotes an environment variable back at us, or a
stack trace carrying a bearer token, must not be able to persist it.

Patterns cover the things that actually show up in this domain: Binance API keys
and secrets, OAuth bearer tokens, Anthropic/OpenAI keys, EVM private keys, and
BIP-39 seed phrases.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "«redacted»"

# Ordered most-specific first so a private key isn't caught by the generic rule.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bip39_seed", re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b")),
    ("evm_private_key", re.compile(r"\b0x[a-fA-F0-9]{64}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*")),
    # Binance keys are 64 mixed-case alphanumerics. A naive [A-Za-z0-9]{64} also
    # matches every SHA-256 hash we write, which would redact our own audit
    # chain into uselessness. Require mixed case and exclude pure lowercase hex.
    (
        "binance_key",
        re.compile(
            r"\b(?![a-f0-9]{64}\b)(?=[A-Za-z0-9]{64}\b)(?=[A-Za-z0-9]*[A-Z])"
            r"(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{64}\b"
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
]

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "password",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "privatekey",
    "mnemonic",
    "seed",
    "seed_phrase",
    "authorization",
    "cookie",
    "device_key",
    "signature_key",
}


def redact_text(text: str) -> str:
    for _name, pattern in PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def redact(obj: Any) -> Any:
    """Recursively mask secrets by key name and by value shape."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower().replace("-", "_") in SENSITIVE_KEYS:
                out[k] = MASK
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def contains_secret(text: str) -> bool:
    return any(p.search(text) for _n, p in PATTERNS)
