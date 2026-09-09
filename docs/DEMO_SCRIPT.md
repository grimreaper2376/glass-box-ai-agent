# Demo video script — 3 minutes

Record at 1920×1080. Dark theme. Have the engine warmed up for about two minutes
before you start so there is real history on screen.

Do not narrate the architecture. Show the thing, and let one sentence land per
shot.

---

## 0:00–0:20 — The problem

**On screen:** the Binance launch quote, as text.

> "Binance can monitor resulting trading activity, including orders, but not the
> agent's broader workflow or reasoning."

**Say:**

> Binance built the rails for AI agents to trade. In its own launch
> announcement, it says it can see the orders an agent places, but not the
> reasoning behind them. So when an agent asks you to approve a trade, you're
> being asked to sign something you can't read. GlassBox fixes that.

---

## 0:20–0:50 — The Council

**On screen:** Council tab. Scroll slowly through one asset's transcript.

**Say:**

> Four analysts look at every asset independently. Technical, sentiment,
> on-chain, funding. They don't see each other's answers before speaking, so
> when they agree it means something.
>
> Here three are bearish on SOL and one is bullish. GlassBox doesn't average
> that away — it takes the trade smaller, and writes the disagreement into the
> record. That dissenting argument is kept, not discarded.

---

## 0:50–1:20 — The Constitution

**On screen:** Constitution tab, then `constitution.yaml` in an editor.

**Say:**

> This is your rulebook. Plain YAML. Position caps, daily loss limits, mandatory
> stop losses, a symbol allowlist, macro blackout windows, a cooldown so it
> can't revenge-trade.
>
> This runs as deterministic code, after the AI has finished reasoning. The
> agents never see it and can't argue with it. If a rule says no, the trade does
> not happen.

**Do:** change `max_position_pct` from 20 to 5, save, press **Reload from file**.

> Change it live, and the change itself goes into the audit log.

---

## 1:20–2:00 — The Guardian *(the money shot)*

**On screen:** Sentinel tab.

**Say:**

> Now let's break it.

**Do:** press **Crash the whole book**. Stay silent for three seconds and let the
gauge climb.

> Threat score crosses seventy. The Guardian hedges the largest positions, vetoes
> every new entry the Council proposes, and at eighty-eight it quarantines
> capital entirely — no module can touch the book.

**Do:** switch to Cockpit. Point at equity.

> Buy and hold lost ninety-three percent on this path. We finished flat. Not
> because the agent predicted the crash — because it was allowed to stop itself.

---

## 2:00–2:30 — The receipt

**On screen:** Ledger tab. Press **Verify chain**.

**Say:**

> Every decision — taken or refused — is a signed, hash-chained record. Here's
> verification re-deriving every hash from genesis.

**Do:** open a receipt and expand the reasoning.

> This is the artifact Binance said it couldn't see: the full argument, the rules
> that fired, the Guardian's assessment, all bound to one hash. Edit any past
> record and verification fails from that point on.

---

## 2:30–3:00 — Bridge mode and close

**On screen:** a pending confirmation card with the MCP instruction expanded.

**Say:**

> Binance requires you to confirm before anything executes, and there's no
> withdrawal scope, ever. So GlassBox doesn't fight that. It does the analysis
> autonomously, then hands you one pre-vetted instruction — the exact MCP call,
> with the hash of the reasoning that produced it attached.
>
> You're never asked to approve something you can't read.
>
> GlassBox. A trading agent that can veto itself, and proves what it was
> thinking when it did.

---

## Shot list

| Time | Tab | Action |
|---|---|---|
| 0:00 | — | Quote card |
| 0:20 | Council | Scroll one transcript |
| 0:50 | Constitution | Edit YAML, reload |
| 1:20 | Sentinel | Crash the whole book |
| 1:45 | Cockpit | Equity vs benchmark |
| 2:00 | Ledger | Verify chain, open a receipt |
| 2:30 | Cockpit | Pending confirmation, MCP call |

## Notes

- Let the crash sequence breathe. Three seconds of silence while the gauge moves
  is more convincing than narration over it.
- Don't say "as you can see". Show it.
- If a take goes wrong, `python -m glassbox backtest --scenario crash` gives you
  the same story in the terminal as a fallback.

## Optional beats for a longer cut

The 3-minute cut above is the safety story end-to-end and should stay the spine.
If you are cutting a longer version, two beats show breadth without diluting it:

- **Futures desk (~20s).** Cockpit → **Futures** → pick 5× → **Open Short**. Point out
  that the second opinion, Constitution and Guardian run on it exactly as on a spot
  trade, and that a large leveraged open routes to the same human confirmation. One
  line worth saying: the ceiling on leverage is a *denial*, not a trim — the rules
  treat leverage as the risk it is. Then hit the red **Close** to flatten.
- **Chain inspector (~15s).** Payments → inspector → switch the network to Bitcoin,
  paste an address, and show it resolve. The point is that settlement is honestly
  scoped to Base Sepolia while the read-only inspector spans ten chains.
- **Analyst Desk (~25s).** Council tab → "Ask the desk" → type "what do you think of
  SOL right now?" The strong beat is to read one sentence aloud where a number does
  real work — e.g. funding is positive so the crowd is paying to be long — then point
  out that every figure was just computed live, and that if you asked it to size a
  trade it would tell you exactly what the Constitution would allow. Optionally type a
  pair the engine doesn't trade into "Analyze any pair" to show the Council isn't
  limited to the six symbols on screen.
