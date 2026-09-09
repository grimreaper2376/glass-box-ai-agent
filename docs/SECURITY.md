# Security and privacy

What this system defends against, how, and — more usefully — what it does not.

## Design position

GlassBox drives an agent that can move money. The default posture is that the
agent is untrusted, the model is untrusted, and the operator is the only
authority. Every safety property below follows from that.

Three principles:

1. **No credentials on disk, ever.** GlassBox never asks for a Binance API key or
   secret. Authentication to Agent OS happens through the official MCP server's
   OAuth flow inside your AI client. There is nothing here to steal.
2. **Fail closed.** A rule that throws an exception denies the trade. A budget
   that cannot be checked blocks the purchase. Ambiguity resolves toward
   inaction.
3. **Assume the log will be read by someone hostile.** Everything is redacted
   before it is written.

---

## Layers

### 1. No withdrawal path exists

Binance's MCP server has no withdrawal scope. An agent cannot move funds to an
external address, and cannot pull funds from your main account into the sub-account.
GlassBox inherits that and adds nothing that could circumvent it: it has no
wallet, no signing key for transactions, and no network path to Binance other
than the public market-data API.

The worst case for a total compromise of this process is bad trades inside a
funded sub-account — not theft.

### 2. Sub-account isolation

The agent trades inside a dedicated Agentic sub-account. Fund it with what you
would be relaxed about losing. That first transfer is manual and cannot be
automated, which is a feature.

### 3. The Constitution

Seventeen deterministic rules evaluated on every intent, after the AI has finished
reasoning. Position caps, gross concentration, daily loss limits, drawdown
circuit breaker, trade rate limiting, confidence floor, analyst quorum, spread
guard, volatility sizing, mandatory stop losses, macro blackout windows,
post-loss cooldown, symbol allowlist, human-confirmation threshold, kill switch.

Strictest verdict wins. Size adjustments compound. A rule that raises an
exception denies.

The symbol allowlist deserves specific mention: it is the single most effective
guard against an agent acting on a token it encountered in a data feed. Leave it
populated.

### 4. The Guardian

An independent veto that runs after the Constitution. It can refuse an intent the
rules approved, and it can quarantine all capital.

### 5. Secret redaction

Every payload bound for the ledger, the websocket or a log passes through
`redact.py` first. Patterns cover Binance-shaped keys, OAuth bearer tokens,
Anthropic and OpenAI keys, JWTs, EVM private keys and BIP-39 seed phrases, plus
key-name matching for `api_secret`, `token`, `mnemonic` and similar.

A drill in the test suite plants an API key in a ledger write and asserts it does
not survive to disk.

One subtlety worth recording: the first version of the Binance-key pattern was
`[A-Za-z0-9]{64}`, which also matched every SHA-256 hash the ledger writes — it
would have redacted the audit chain into uselessness. The pattern now requires
mixed case and excludes pure lowercase hex. Over-broad redaction is its own
failure mode.

### 6. The audit ledger

Append-only JSONL. Each record chains `SHA-256(prev_hash || canonical_body)` and
carries an HMAC-SHA256 signature under a device key generated on first run and
stored `0600`.

`verify` walks the chain from genesis, re-derives every hash, and checks every
signature. Editing or deleting any record breaks verification for that record and
all subsequent ones.

The device key is not a wallet key and cannot move funds. It only signs audit
records.

### 7. x402 spend control

Daily budget, per-request cap, provider allowlist, per-analyst attribution, TTL
caching, and a ledger entry per purchase. Enforced locally, underneath Binance's
own daily cap.

### 8. Local-only network surface

The server binds `127.0.0.1` and there is no flag to change it. An operator
console for a system that moves money has no business listening on `0.0.0.0`
because someone wanted a demo to work from another laptop.

Every mutating endpoint requires a per-process session token, printed at startup
and fetched by the frontend from localhost. This stops a stray page in another
browser tab from pressing the buttons. The WebSocket is read-only — no commands
travel over it.

---

## What this does NOT protect you from

Stated plainly, because a security section that only lists strengths is
marketing.

**A compromised machine.** An attacker with your filesystem has your device key
and can forge a chain that verifies. The ledger detects tampering by anyone
*without* local access. If you need more, anchor the head hash externally.

**A bad decision that was honestly reached.** The ledger proves the record was
not altered. It does not prove the reasoning was sound. A confidently wrong
analyst produces a perfectly verifiable receipt for a losing trade.

**Market risk.** No amount of policy prevents loss. The crash scenario shows
capital preserved; a different crash shape, a gap through your stop, or an
exchange outage all produce different outcomes.

**Prompt injection into data feeds, in the general case.** If you wire in real
sentiment providers, a crafted social post could try to steer an analyst. Three
things blunt it: the Constitution does not read model output, the symbol
allowlist bounds what could be bought, and the quorum rule means one captured
analyst cannot carry a decision. This is mitigation, not immunity.

**Your own confirmation fatigue.** If you set the confirmation threshold so low
that everything asks, you will approve everything without reading. The default is
deliberately not zero, and this was tuned during testing after the first
configuration queued five confirmations before a single trade filled.

**Anything about the LLM path.** With `ANTHROPIC_API_KEY` set, analysts ask a
model to critique their heuristic conclusion. The critique can lower confidence
but never create a position from nothing. That asymmetry is enforced in code, not
in a prompt — but the model still sees market data you send it.

---

## Reporting

Found a hole? Open an issue with reproduction steps. Do not include your ledger
file — it contains your trading history.

---

## MCP-specific hardening (added after reviewing the 2026 spec and CVE record)

The MCP specification's Security Best Practices document (2026-07-28) names
several attacks against *clients* like this one. Each mitigation below is
implemented in `backend/glassbox/security.py` and covered by tests in
`tests/test_glassbox.py` (`pytest tests -k "ssrf or security or scheme"`).

### SSRF during OAuth discovery — the important one

`BinanceMCPClient.discover()` deliberately *follows* URLs rather than
hardcoding endpoints: it reads `/.well-known/oauth-protected-resource`, takes
the `authorization_servers` URL from that response body, fetches it, and takes
`authorization_endpoint` and `token_endpoint` from the result. That design is
correct — it survives Binance moving their endpoints — but it means a hostile
or spoofed response controls which URLs this process fetches next.

CVE-2025-6514 was exactly this class of bug in `mcp-remote`: a malicious
`authorization_endpoint` used to intercept OAuth tokens before a session was
even established.

Every URL in that chain is now validated before it is fetched:

| Blocked | Why |
|---|---|
| `169.254.0.0/16` | Cloud metadata endpoint — leaks IAM credentials on AWS/GCP/Azure |
| `10/8`, `172.16/12`, `192.168/16` | Internal network reconnaissance |
| `127.0.0.0/8`, `::1`, `localhost` | Services bound to loopback that assume nothing external can reach them |
| Reserved / multicast / unspecified | No legitimate OAuth endpoint lives there |
| Plain `http://` to a public host | Authorization codes and tokens must not be readable in transit |

Validation uses the standard library's `ipaddress` module, not string
matching — the spec explicitly warns that hand-rolled parsers miss octal, hex
and IPv4-mapped-IPv6 encodings. **Every** address a hostname resolves to is
checked, not just the first, because a DNS-rebinding attacker can return one
public and one private address.

Loopback is permitted in exactly one case: the bundled mock MCP server, whose
address this process chose itself rather than receiving from a remote
response.

### Dangerous URL schemes

An authorization URL ends up in `webbrowser.open()` on the backend and
`window.open()` in the dashboard. A `javascript:` URL there is code execution
in the page's origin, not navigation. Only `https` (and `http` on loopback) is
accepted — an allowlist, not a blocklist, because a blocklist is always one
scheme behind. This is enforced twice: once in `security.py` before the URL is
returned, and again in the frontend immediately before `window.open()`.

### SSRF *against Binance*, via the Client ID document

Binance's authorization server fetches whatever URL is configured as the
Client ID (a Client ID Metadata Document URL). An unvalidated value would make
GlassBox the instrument of an SSRF attack against Binance's own
infrastructure. The same validation applies, so that cannot happen — verified
by test and by live API check (`http://169.254.169.254/meta.json` → HTTP 400).

### Content Security Policy on the console

Applied to every response as defence in depth. The console is already
loopback-only and token-gated for writes, so this is not the primary control —
but `frame-ancestors 'none'` stops another page framing the console to trick
someone into clicking through a live-trading confirmation, and a strict CSP
means an injection point introduced by some future change still could not
execute or exfiltrate.

Headers set: `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`Permissions-Policy` (geolocation/microphone/camera denied), `Cache-Control:
no-store`.

### Where GlassBox was already aligned with the spec

- **Token passthrough** — not applicable; GlassBox is a client, and it binds
  tokens to the resource with RFC 8707 `resource` on both the authorization
  and token requests.
- **Scope minimization** — no withdrawal scope exists on Agent OS and this
  client never requests one.
- **Local HTTP transport** — the spec says a locally-bound HTTP transport
  should "require an authorization token"; every write endpoint does.
- **PKCE + state** — S256 mandatory, `state` verified on return, verifier
  never leaves the process.

### Honest limits

- **Localhost redirect URI impersonation** is a real, unfixed weakness of the
  whole native-OAuth pattern: a metadata document proves control of a domain
  but cannot prove which local process is listening on a loopback port. The
  spec places the countermeasure on the *authorization server*, not the
  client. Nothing in GlassBox can close it.
- **DNS rebinding (TOCTOU)** is narrowed by checking every resolved address,
  not eliminated — the address could change between validation and the actual
  request. Full mitigation needs DNS pinning or an egress proxy, which is an
  operator-environment decision rather than something this code can impose.
- The CSP allows `'unsafe-inline'` for scripts and styles, because the
  dashboard uses inline handlers. Removing that would need a nonce-based
  refactor of `app.js`, which is worth doing but is not done.

---

## Real on-chain settlement (payment workflows)

An earlier version of the payment rail recorded `tx_ref` as a generated
string — honest about not being real, but not real either. It now settles
genuinely on Base Sepolia, a live public Ethereum testnet, through
`backend/glassbox/settlement.py`.

**What is real:** a genuine secp256k1 keypair, encrypted at rest with the
same construction as the OAuth token store; real balance queries against the
live public RPC; real EIP-1559 transactions, signed locally and broadcast via
`eth_sendRawTransaction`; a real, independently-verifiable transaction hash —
checkable by anyone at `sepolia.basescan.org`, not merely asserted by GlassBox.

**What is a stated simplification, not a hidden one:**

1. **Testnet, not mainnet.** Base Sepolia moves no real value, deliberately.
   A mainnet private key deserves a materially different security review —
   hardware-backed signing, multi-party custody, externally-enforced
   withdrawal limits — and building that review honestly is a different-sized
   project than this one.
2. **Settlement is native testnet ETH, not USDC.** The x402 reference design
   settles in a stablecoin via EIP-3009 `transferWithAuthorization`, which
   needs the exact deployed USDC contract address and its exact EIP-712
   domain separator — get either wrong and it doesn't fail loudly, it
   silently talks to the wrong contract. Native ETH needs no contract address
   at all and cannot be gotten subtly wrong that way. The policy engine's USD
   figures (allowlist, per-payment cap, daily budget) are real and enforced;
   the settled transfer proves the payment mechanism works end-to-end, not a
   value-equivalent transfer, since a valueless test network has no real
   dollar-equivalent to send.

**Verify it yourself:** `GET /api/payments/wallet` returns a real address and
its real balance. `pytest tests -k settlement` runs 5 tests including one
that hits Base Sepolia's live network directly — a fresh wallet is expected
to be genuinely rejected for insufficient funds, and that specific,
correctly-classified rejection is the proof that key generation, signing, and
broadcast against a real chain all work, not a mock of them.

**To complete a fully settled payment:** fund the address `GET
/api/payments/wallet` returns with free Base Sepolia test ETH from
`https://www.alchemy.com/faucets/base-sepolia` (no real money, one-time), then
retry — the transaction will broadcast for real and return a transaction
hash you can look up yourself.
