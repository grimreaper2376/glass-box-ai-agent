---
title: Decision Receipt
description: Produce a signed, auditable justification record before an AI agent places any trade, so the reasoning behind an order can be reviewed after the fact. Use whenever an agent is about to execute a trade on a user's behalf.
metadata:
  version: 1.0.0
  author: glassbox
license: MIT
---

# Decision Receipt

Binance can monitor the orders an agent places, but not the reasoning that
produced them — that runs inside the user's own AI client and leaves no record.
This skill closes the gap: before executing, the agent emits a structured receipt
that binds the order to its justification.

## When to use this

Before **any** state-changing action on a user's account: opening or closing a
position, resizing, hedging, or moving funds between wallets in an Agentic
sub-account. Read-only market queries do not need a receipt.

## What to produce

Emit this before calling the order tool, and keep it alongside the order id.

```json
{
  "receipt_version": 1,
  "ts": "2026-09-06T09:15:00Z",
  "intent": {
    "symbol": "BNBUSDT",
    "side": "BUY",
    "order_type": "MARKET",
    "notional_usd": 851.00,
    "stop_loss": 583.13,
    "take_profit": 665.22
  },
  "thesis": "One paragraph a human can read, in plain language.",
  "evidence": [
    {"source": "technical", "stance": "bullish", "confidence": 0.71,
     "detail": "Fast EMA 0.36% above slow; RSI 33"}
  ],
  "dissent": [
    {"source": "funding", "stance": "bearish", "confidence": 0.63,
     "detail": "Perp funding 4.0 bps, longs crowded"}
  ],
  "risk_checks": [
    {"rule": "max_position_pct", "outcome": "pass", "detail": "8.5% of equity, cap 20%"},
    {"rule": "require_stop_loss", "outcome": "pass", "detail": "stop attached"}
  ],
  "market_snapshot": {"price": 610.79, "spread_bps": 1.2, "atr_pct": 1.26},
  "prev_receipt_hash": "b7409982…",
  "hash": "4fa4eec8…"
}
```

## Rules

1. **Record the dissent.** Evidence that argued *against* the trade is the most
   valuable part of the record and the first thing dropped by accident. Keep it.
2. **Write the thesis for a person.** Not for a log parser. Someone reviewing a
   loss six weeks later must be able to follow the argument.
3. **Never write a receipt after the fact.** A receipt produced post-execution to
   explain what already happened is a rationalisation, not a justification.
4. **Chain the hashes.** `hash = SHA256(prev_receipt_hash || canonical_json(body))`
   with sorted keys and no whitespace. This makes silent edits detectable.
5. **Redact before writing.** No API keys, bearer tokens, seed phrases or private
   keys, including inside free-text rationale.
6. **Emit a receipt for refusals too.** A trade you declined to make is a
   decision. Set `intent.side` as normal and record the blocking rule under
   `risk_checks`.

## Verification

To check a chain: walk it from the first record, recompute each hash from its
predecessor and body, and confirm it matches. A mismatch means that record was
altered, and every record after it will also fail.

## Reference implementation

<https://github.com/your-org/glassbox> — `backend/glassbox/ledger.py`
