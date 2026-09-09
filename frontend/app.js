/* =========================================================================
   GlassBox console

   No framework, no build step. A judge clones the repo, runs one command and
   the dashboard is there. Adding a toolchain to a two-day submission buys
   nothing and costs a demo failure.
   ========================================================================= */

let TOKEN = null;
let STATE = null;
let LAST_THREAT_LEVEL = "normal";

/* ---------------- helpers ---------------- */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Multi-column, click-to-sort table state.
 *
 * Each sortable table keeps an ordered list of active sort keys. Clicking a
 * column header cycles it: not-sorted → descending → ascending → off. Because
 * the list is ordered and additive, clicking a second column sorts by the
 * first and *then* the second as a tie-breaker, exactly as asked — and
 * clicking a column back off leaves the remaining columns still applied in
 * their existing order. The most-recently-activated column takes priority, so
 * the newest click leads. */
const sortState = {};  // tableId -> [{ key, dir }]

function cycleSort(tableId, key) {
  const list = sortState[tableId] || (sortState[tableId] = []);
  const existing = list.find((s) => s.key === key);
  if (!existing) {
    list.unshift({ key, dir: "desc" });        // new column leads, descending first
  } else if (existing.dir === "desc") {
    existing.dir = "asc";
  } else {
    // was ascending → remove it entirely (third click turns the column off)
    sortState[tableId] = list.filter((s) => s.key !== key);
  }
}

function applySort(tableId, rows) {
  const list = sortState[tableId] || [];
  if (!list.length) return rows;
  const sorted = [...rows];
  sorted.sort((a, b) => {
    for (const { key, dir } of list) {
      let av = a[key], bv = b[key];
      if (typeof av === "string" || typeof bv === "string") {
        av = String(av ?? "").toLowerCase();
        bv = String(bv ?? "").toLowerCase();
        if (av !== bv) return (av < bv ? -1 : 1) * (dir === "asc" ? 1 : -1);
      } else {
        av = Number(av) || 0; bv = Number(bv) || 0;
        if (av !== bv) return (av - bv) * (dir === "asc" ? 1 : -1);
      }
    }
    return 0;
  });
  return sorted;
}

function sortIndicator(tableId, key) {
  const s = (sortState[tableId] || []).find((x) => x.key === key);
  if (!s) return `<span class="sort-ind">↕</span>`;
  const rank = (sortState[tableId] || []).findIndex((x) => x.key === key) + 1;
  const many = (sortState[tableId] || []).length > 1;
  return `<span class="sort-ind active">${s.dir === "asc" ? "▲" : "▼"}${many ? `<sup>${rank}</sup>` : ""}</span>`;
}

const usd = (n, d = 2) =>
  (n < 0 ? "-$" : "$") + Math.abs(Number(n) || 0).toLocaleString("en-US",
    { minimumFractionDigits: d, maximumFractionDigits: d });

const pct = (n, d = 2) => `${Number(n) >= 0 ? "+" : ""}${(Number(n) || 0).toFixed(d)}%`;
const cls = (n) => (Number(n) > 0 ? "up" : Number(n) < 0 ? "down" : "dim");
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });
const short = (h, n = 10) => (h ? `${h.slice(0, n)}…${h.slice(-4)}` : "—");

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (TOKEN) opts.headers["X-Glassbox-Token"] = TOKEN;
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

/* ---------------- theme ---------------- */

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("gb-theme", t); } catch {}
  document.querySelectorAll("[data-theme-set]").forEach((b) =>
    b.classList.toggle("active", b.dataset.themeSet === t));
  if (STATE) renderSpark(STATE);
}
document.querySelectorAll("[data-theme-set]").forEach((b) =>
  b.addEventListener("click", () => setTheme(b.dataset.themeSet)));
try { setTheme(localStorage.getItem("gb-theme") || "dark"); } catch { setTheme("dark"); }

/* ---------------- navigation ---------------- */

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    $(`view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "ledger") { loadLedger(); loadAnchor(); }
    if (btn.dataset.view === "rules") loadRules();
    if (btn.dataset.view === "markets") loadMarkets();
    if (btn.dataset.view === "coins") loadCoins();
    if (btn.dataset.view === "binance") loadMCP();
    if (btn.dataset.view === "payments") loadPayments();
    if (btn.dataset.view === "onchain") loadOnchain();
    if (btn.dataset.view === "news") { loadNews(); loadMacro(); startNewsAutoRefresh(); }
    else stopNewsAutoRefresh();
    if (btn.dataset.view === "control") loadControlCenter();
    if (window.retranslate) setTimeout(window.retranslate, 80);
  });
});


/* Events auto-refresh.
 *
 * The backend re-fetches headlines every 5 minutes, but the Events tab used
 * to render once on click and then sit frozen — so a tab left open showed
 * stale news indefinitely and looked broken. This polls while the tab is
 * visible and stops when you navigate away, so it never burns requests in
 * the background. */
let newsRefreshTimer = null;

function startNewsAutoRefresh() {
  stopNewsAutoRefresh();
  newsRefreshTimer = setInterval(() => {
    if ($("view-news").classList.contains("active")) loadNews();
    else stopNewsAutoRefresh();
  }, 60000);
}

function stopNewsAutoRefresh() {
  if (newsRefreshTimer) { clearInterval(newsRefreshTimer); newsRefreshTimer = null; }
}

/* ---------------- top strip ---------------- */

function renderTop(s) {
  const p = s.portfolio;
  $("t-equity").textContent = usd(p.equity_usd);
  const pn = $("t-pnl");
  pn.textContent = `${usd(p.total_pnl_usd)} (${pct(p.total_pnl_pct)})`;
  pn.className = `stat-v num ${cls(p.total_pnl_usd)}`;
  $("t-dd").textContent = `${p.drawdown_pct.toFixed(2)}%`;
  $("t-dd").className = `stat-v num ${p.drawdown_pct > 5 ? "down" : "dim"}`;

  const a = s.guardian.assessment || { score: 0, level: "normal" };
  const tl = $("t-threat");
  tl.textContent = `${a.score.toFixed(0)}/100`;
  tl.className = `stat-v num ${a.level === "critical" ? "down" : a.level === "elevated" ? "" : "up"}`;
  if (a.level === "elevated") tl.style.color = "var(--warn)";
  else tl.style.color = "";

  $("t-ledger").textContent = `#${s.ledger.height}`;
  const m = $("t-mode");
  m.textContent = s.mode;
  m.className = `pill ${s.mode === "paper" ? "paper" : "bridge"}`;

  const rl = s.ratelimit, stm = s.stream;
  if (rl) {
    const w = $("t-weight");
    w.textContent = `API ${rl.utilisation_pct.toFixed(0)}%`;
    w.className = `pill ${rl.health === "healthy" ? "ok" : rl.health === "banned" ? "bad" : ""}`;
    w.title = `${rl.used_1m} of ${rl.soft_ceiling} weight used this minute `
      + `(Binance hard limit ${rl.limit_1m}). Throttled ${rl.throttle_events} times.`;
  }
  if (stm) {
    const w = $("t-stream");
    w.textContent = stm.health === "live" ? `${stm.symbols_streaming} live` : stm.health;
    w.className = `pill ${stm.health === "live" ? "ok" : "bad"}`;
    w.title = stm.connected
      ? `Streaming ${stm.symbols_streaming} symbols from ${stm.host}. ${stm.reconnects} reconnects.`
      : (stm.last_error || "websocket disconnected");
  }

  const mcp = s.mcp;
  if (mcp) {
    const m = $("t-mcp");
    m.textContent = mcp.connected ? `MCP ${mcp.tool_count}` : "MCP off";
    m.className = `pill ${mcp.connected ? "ok" : ""}`;
    m.title = mcp.connected
      ? `Connected to ${mcp.server} at ${mcp.endpoint}. ${mcp.tool_count} tools, ${mcp.calls} calls.`
      : (mcp.last_error || "No Binance MCP session. Open the Binance tab to connect.");
  }

  // Load the full tradable universe once, lazily, for the trade-symbol
  // combobox (see setupSymbolCombo below).
  if (!window._mtSymbolsLoaded && !window._mtSymbolsLoading) {
    window._mtSymbolsLoading = true;
    api("/api/markets?quote=USDT&limit=600")
      .then((d) => {
        window._mtSymbols = (d.rows || []).map((r) => ({
          symbol: r.symbol, base: r.base, vol: r.volume_24h_usd,
        }));
        window._mtSymbolsLoaded = true;
      })
      .catch(() => {
        window._mtSymbols = Object.keys(s.market || {}).map((k) => ({ symbol: k, base: k, vol: 0 }));
        window._mtSymbolsLoaded = true;
      })
      .finally(() => { window._mtSymbolsLoading = false; });
  }

  const f = s.feeds || {};
  const fb = $("t-feed");
  if (s.live_market_data) {
    fb.textContent = f.spot_ok ? "Binance live" : "data offline";
    fb.className = `pill ${f.spot_ok ? "ok" : "bad"}`;
    fb.title = f.spot_ok
      ? `Spot: ${f.spot_host}\nFutures: ${f.futures_ok ? f.futures_host : "unavailable — derivatives analyst abstains"}`
      : (f.last_error || "no market data host reachable");
  } else {
    fb.textContent = "simulator";
    fb.className = "pill";
    fb.title = "Seeded simulated market. Set GLASSBOX_LIVE_DATA=1 for real Binance data.";
  }

  $("run").textContent = s.running ? "Pause engine" : "Start engine";
  $("run").className = s.running ? "btn" : "btn btn-primary";

  const bt = $("badge-threat");
  bt.hidden = a.level === "normal";
  bt.textContent = a.level === "critical" ? "!" : "•";

  const bp = $("badge-pending");
  bp.hidden = !s.pending.length;
  bp.textContent = s.pending.length;

  // Banners
  const banners = [];
  if (s.guardian.quarantined)
    banners.push(`<div class="banner crit"><strong>Capital is quarantined.</strong>
      The Guardian has frozen every module out at threat ${a.score.toFixed(0)}/100.
      Nothing will open until conditions recover or you clear it from the Sentinel view.</div>`);
  else if (a.level === "critical")
    banners.push(`<div class="banner crit"><strong>Threat is critical.</strong>
      ${esc(a.recommendation)}</div>`);
  else if (a.level === "elevated")
    banners.push(`<div class="banner warn"><strong>Threat is elevated.</strong>
      ${esc(a.recommendation)}</div>`);
  if (s.mode === "paper" && !s.pending.length)
    banners.push(`<div class="banner warn">Paper mode. Fills are simulated and no order
      reaches Binance. Switch to bridge mode to have GlassBox hand signed instructions
      to the Binance MCP server for your confirmation.</div>`);
  $("banners").innerHTML = banners.join("");

  if (a.level === "critical" && LAST_THREAT_LEVEL !== "critical")
    toast(`Threat critical at ${a.score.toFixed(0)}/100 — ${a.factors[0]?.detail || ""}`, "bad");
  LAST_THREAT_LEVEL = a.level;
}

/* ---------------- cockpit ---------------- */

function renderCSM(s) {
  const c = s.capital_states;
  const total = Object.values(c).reduce((x, y) => x + y, 0) || 1;
  $("csm").innerHTML = Object.entries(c)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => {
      const share = (v / total) * 100;
      return `<div class="csm-seg csm-${k}" style="flex:${share}"
        title="${k}: ${usd(v)}">${share > 11 ? `${k} ${share.toFixed(0)}%` : ""}</div>`;
    }).join("") || `<div class="csm-seg csm-IDLE" style="flex:1">IDLE 100%</div>`;
}

function renderSpark(s) {
  const svg = $("spark");
  const pts = s.equity_curve || [];
  if (pts.length < 2) { svg.innerHTML = ""; return; }
  const w = svg.clientWidth || 600, h = svg.clientHeight || 200, pad = 6;
  const vals = pts.map((p) => p.equity);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const range = hi - lo || 1;
  const x = (i) => pad + (i / (pts.length - 1)) * (w - pad * 2);
  const y = (v) => h - pad - ((v - lo) / range) * (h - pad * 2);
  const line = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= s.portfolio.starting_equity_usd;
  const col = up ? "var(--up)" : "var(--down)";
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = `
    <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${col}" stop-opacity=".22"/>
      <stop offset="100%" stop-color="${col}" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${line} L${x(vals.length - 1)},${h} L${x(0)},${h} Z" fill="url(#sg)"/>
    <path d="${line}" fill="none" stroke="${col}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/>`;

  const b = s.benchmark || {};
  $("bench-note").innerHTML = b.available
    ? `Buy &amp; hold over the same path: <span class="num ${cls(b.buy_and_hold_pnl_pct)}">${pct(b.buy_and_hold_pnl_pct)}</span>
       &nbsp;·&nbsp; difference <span class="num ${cls(b.excess_return_pct)}">${pct(b.excess_return_pct)}</span>`
    : "";
}

function renderSession(s) {
  const p = s.portfolio, f = s.performance, c = s.counters || {};
  const rows = [
    ["Cash", usd(p.cash_usd)],
    ["In yield", usd(p.yield_deployed_usd)],
    ["Gross exposure", `${p.gross_exposure_pct.toFixed(1)}%`],
    ["Closed trades", f.closed_trades],
    ["Win rate", `${f.win_rate_pct.toFixed(0)}%`],
    ["Profit factor", f.profit_factor == null ? "—" : f.profit_factor.toFixed(2)],
    ["Fees paid", usd(p.total_fees_usd)],
    ["Yield earned", usd(p.yield_earned_usd, 4)],
    ["Intents raised", c.intents ?? 0],
    ["Blocked by rules", c.denied ?? 0],
    ["Guardian vetoes", s.guardian.veto_count],
  ];
  $("session-stats").innerHTML = rows.map(
    ([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`
  ).join("");
}

function renderPositions(s) {
  const ps = Object.values(s.portfolio.positions);
  // Keep the manual-trade panel's Sell logic in sync with what's actually held.
  _heldPositions = s.portfolio.positions || {};
  updateSellAvailability();
  if (!ps.length) {
    // "No positions" on its own reads like a broken engine. Showing which
    // symbol came closest, and by how much it missed the bar, turns silence
    // into a real answer.
    const c = s.council && s.council.closest_to_trading;
    let why = "";
    if (c && c.blocked_reason === "not_actionable_spot_only") {
      why = `<div class="split-note" style="margin-top:10px;text-align:left">
        <strong>${esc(c.symbol)}</strong> looks <strong>${esc(c.direction)}</strong> at
        <span class="num">${(c.conviction * 100).toFixed(0)}%</span> conviction, but GlassBox
        is spot-only and holds no ${esc(c.symbol)} to exit — a bearish view on something
        unheld can never become a trade here, however high its conviction runs.</div>`;
    } else if (c && c.blocked_reason === "quorum_not_met") {
      why = `<div class="split-note" style="margin-top:10px;text-align:left">
        Closest so far: <strong>${esc(c.symbol)}</strong> cleared the conviction bar
        (<span class="num">${(c.conviction * 100).toFixed(0)}%</span> vs
        <span class="num">${(c.min_conviction * 100).toFixed(0)}%</span> required) — but only
        <span class="num">${c.agreeing_analysts}</span> of the analysts actually agree, and
        <span class="num">${c.analysts_required}</span> are required. One confident voice isn't
        quorum; genuine agreement is.</div>`;
    } else if (c) {
      why = `<div class="split-note" style="margin-top:10px;text-align:left">
        Closest so far: <strong>${esc(c.symbol)}</strong> looked
        <strong>${esc(c.direction)}</strong> at
        <span class="num">${(c.conviction * 100).toFixed(0)}%</span> conviction —
        <span class="num">${(c.short_by * 100).toFixed(0)}</span> points under the
        <span class="num">${(c.min_conviction * 100).toFixed(0)}%</span> bar required to
        open a position. The engine is running and evaluating every tick; it just
        hasn't seen a case strong enough to act on.</div>`;
    } else if (s.running) {
      why = `<div class="split-note" style="margin-top:10px;text-align:left">
        Every analyst is currently neutral — no directional view on any watched pair.</div>`;
    }
    $("positions").innerHTML = `<div class="empty"><strong>No open positions</strong>
      Capital is idle. The Council opens one when enough analysts agree and the rules allow it.
      ${why}</div>`;
    return;
  }
  const totalUnreal = ps.reduce((sum, p) => sum + (p.unrealised_pnl_usd || 0), 0);

  // Live warning: positions the Council has since turned against. This is the
  // feedback loop for "I opened on a bullish check, then the Council flipped."
  const opposed = (s.council && s.council.positions_opposed) || [];
  const opposedBanner = opposed.length
    ? `<div class="banner warn" style="margin-bottom:10px">
        <strong>The system has changed its mind on ${opposed.length === 1 ? "a position you hold" : opposed.length + " positions you hold"}.</strong>
        ${opposed.map((o) => `<div class="split-note" style="margin-top:4px">${esc(o.message)}
          <button class="btn btn-sm btn-danger close-pos" data-symbol="${esc(o.symbol)}" style="margin-left:8px">Close ${esc(o.symbol)}</button></div>`).join("")}
      </div>`
    : "";

  $("positions").innerHTML = opposedBanner + `<table><thead><tr>
    <th>Asset</th><th>Side</th><th class="ta-r">Size</th><th class="ta-r">Entry</th>
    <th class="ta-r">Mark</th><th class="ta-r">P&amp;L (this position)</th><th class="ta-r">Stop</th>
    <th>Mode</th><th class="ta-r">Close</th>
  </tr></thead><tbody>` + ps.map((p) => `<tr>
    <td><strong>${esc(p.symbol)}</strong></td>
    <td><span class="pill ok" title="GlassBox is spot-only — every held position was bought, never sold short">Long (bought)</span></td>
    <td class="ta-r num">${usd(p.notional_usd, 0)}</td>
    <td class="ta-r num dim">${p.avg_price.toLocaleString()}</td>
    <td class="ta-r num">${p.mark_price.toLocaleString()}</td>
    <td class="ta-r num ${cls(p.unrealised_pnl_usd)}">${usd(p.unrealised_pnl_usd)}<br>
      <span style="font-size:12px">${pct(p.unrealised_pct)}</span></td>
    <td class="ta-r num dim">${p.stop_loss ? p.stop_loss.toLocaleString() : "—"}</td>
    <td><span class="pill" style="text-transform:capitalize">${esc(p.opened_mode || "paper")}</span></td>
    <td class="ta-r"><button class="btn btn-sm btn-danger close-pos" data-symbol="${esc(p.symbol)}">Close</button></td>
  </tr>`).join("")
    + `<tr style="border-top:2px solid var(--border)">
        <td colspan="5" class="dim"><strong>Total across all ${ps.length} open position${ps.length === 1 ? "" : "s"}</strong></td>
        <td class="ta-r num ${cls(totalUnreal)}"><strong>${usd(totalUnreal)}</strong></td>
        <td colspan="3"></td>
      </tr>`
    + "</tbody></table>";

  // Wire each Close button to the manual-exit endpoint.
  document.querySelectorAll(".close-pos").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sym = btn.dataset.symbol;
      btn.disabled = true; btn.textContent = "Closing…";
      try {
        const r = await api("/api/position/close", "POST", { symbol: sym });
        toast(r.ok ? `Closed ${sym}.` : (r.error || "Close failed."), r.ok ? "good" : "bad");
      } catch (e) { toast(e.message, "bad"); }
      finally { btn.disabled = false; btn.textContent = "Close"; }
    });
  });
}

function renderMarket(s) {
  const rows = Object.values(s.market);
  $("market-src").textContent = s.live_market_data
    ? "Binance public API" : `simulated · ${s.scenario}`;
  $("market").innerHTML = `<table><thead><tr>
    <th>Symbol</th><th class="ta-r">Price</th><th class="ta-r">Change</th>
    <th class="ta-r">Spread</th><th class="ta-r">ATR</th>
  </tr></thead><tbody>` + rows.map((q) => `<tr>
    <td><strong>${esc(q.symbol)}</strong></td>
    <td class="ta-r num">${q.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
    <td class="ta-r num ${cls(q.change_24h_pct)}">${pct(q.change_24h_pct, 2)}</td>
    <td class="ta-r num dim">${q.spread_bps.toFixed(1)}bp</td>
    <td class="ta-r num dim">${q.atr_pct.toFixed(2)}%</td>
  </tr>`).join("") + "</tbody></table>";
}

function renderPending(s) {
  if (!s.pending.length) { $("pending-host").innerHTML = ""; return; }
  $("pending-host").innerHTML = s.pending.map((p) => {
    const i = p.intent, v = p.verdict;
    const notional = v.adjusted_notional_usd ?? i.notional_usd;
    return `<div class="confirm-card">
      <div class="confirm-head">
        <span class="pill ${i.side === "BUY" ? "ok" : "bad"}">${i.side}</span>
        <span class="confirm-order">${usd(notional, 0)} of ${esc(i.symbol)}</span>
        <span class="dim">· proposed by ${esc(i.module)}</span>
        <span class="right" style="margin-left:auto" class="dim">needs your decision</span>
      </div>
      <div class="confirm-why">${esc(i.thesis)}</div>
      <div class="confirm-rules">
        ${v.triggered_rules.map((r) => `<span class="rule-chip">${esc(r)}</span>`).join("")}
      </div>
      <details class="reveal"><summary>What the Constitution said, and the exact MCP call</summary>
        <div>
          <pre class="code">${esc(v.reasons.join("\n"))}</pre>
          <pre class="code">${esc(JSON.stringify(p.mcp_instruction, null, 2))}</pre>
        </div>
      </details>
      <div class="confirm-actions">
        <button class="btn btn-up" data-confirm="${esc(i.intent_id)}" data-approve="1">Approve</button>
        <button class="btn btn-down" data-confirm="${esc(i.intent_id)}" data-approve="0">Reject</button>
      </div>
    </div>`;
  }).join("");

  document.querySelectorAll("[data-confirm]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api("/api/confirm", "POST",
          { intent_id: b.dataset.confirm, approve: b.dataset.approve === "1" });
        toast(b.dataset.approve === "1" ? "Approved and executed." : "Rejected.",
          b.dataset.approve === "1" ? "good" : "");
      } catch (e) { toast(e.message, "bad"); b.disabled = false; }
    }));
}

const TAGS = { fill: "tag-fill", deny: "tag-deny", warn: "tag-warn",
  critical: "tag-critical", confirm: "tag-confirm", info: "tag-info", error: "tag-deny" };

function renderActivity(s) {
  const items = [...s.activity].reverse().slice(0, 60);
  if (!items.length) {
    $("activity").innerHTML = `<div class="empty"><strong>Nothing yet</strong>Start the engine to watch decisions arrive.</div>`;
    return;
  }
  $("activity").innerHTML = items.map((a) => `<div class="feed-row">
    <span class="feed-time">${clock(a.ts)}</span>
    <span class="feed-tag ${TAGS[a.level] || "tag-info"}">${esc(a.module)}</span>
    <span class="feed-msg">${esc(a.message)}</span>
  </div>`).join("");
}

let _openReceiptSeq = null;  // which receipt's reasoning is expanded, by stable ledger_seq

function renderReceipts(s) {
  const rs = [...s.receipts].reverse().slice(0, 25);
  if (!rs.length) {
    $("receipts").innerHTML = `<div class="empty"><strong>No decisions yet</strong>
      Every decision — taken or refused — will appear here with its ledger hash.</div>`;
    return;
  }
  $("receipts").innerHTML = rs.map((r) => {
    const i = r.intent, st = r.outcome.status;
    const badge = st === "filled" ? "ok" : st === "denied" ? "bad" : "";
    const rid = `receipt-${r.ledger_seq}`;
    // Persist the expanded panel across the ~2s live re-render, keyed by the
    // ledger sequence which never changes for a given decision. Without this,
    // every poll rebuilt the DOM and silently collapsed whatever the operator
    // had open — the exact bug this replaces.
    const isOpen = r.ledger_seq === _openReceiptSeq;

    const rulesHtml = (r.verdict.reasons && r.verdict.reasons.length)
      ? r.verdict.reasons.map((reason) => `<div class="split-note">${esc(reason)}</div>`).join("")
      : `<div class="split-note up">No rule objected to this trade.</div>`;

    // Same visual language as the Council tab's transcript, so a decision's
    // reasoning reads as prose from a named analyst rather than a raw
    // pipe-delimited dump.
    const signalsHtml = (i.signals && i.signals.length)
      ? i.signals.map((sg) => `
          <div style="padding:7px 0;border-bottom:1px solid var(--border-soft)">
            <div style="display:flex;gap:8px;align-items:baseline;margin-bottom:2px">
              <strong style="font-size:13px">${esc(sg.agent)}</strong>
              <span class="pill ${sg.stance === "bullish" ? "ok" : sg.stance === "bearish" ? "bad" : ""}"
                style="font-size:10.5px;padding:1px 6px">${esc(sg.stance)}</span>
              <span class="num dim" style="font-size:11.5px;margin-left:auto">${(sg.confidence * 100).toFixed(0)}%</span>
            </div>
            <div class="dim" style="font-size:12.5px">${esc(sg.rationale)}</div>
          </div>`).join("")
      : `<div class="split-note">No analyst signals were recorded for this decision.</div>`;

    return `<div class="receipt" id="${rid}">
      <div class="receipt-top">
        <span class="pill ${badge}">${esc(st)}</span>
        <strong>${esc(i.side)} ${esc(i.symbol)}</strong>
        <span class="dim">${usd(i.notional_usd, 0)}</span>
        <span class="receipt-id" style="margin-left:auto">#${r.ledger_seq}</span>
      </div>
      <div class="dim" style="font-size:13.5px;margin-bottom:6px">${esc(i.thesis.slice(0, 220))}${i.thesis.length > 220 ? "…" : ""}</div>
      <div class="chain-link">
        <span class="hash">${short(r.prev_hash, 8)}</span>
        <span class="chain-arrow">→</span>
        <span class="hash">${short(r.ledger_hash, 8)}</span>
      </div>
      <button class="btn btn-sm reasoning-toggle ${isOpen ? "btn-primary" : ""}"
        data-seq="${r.ledger_seq}" data-target="${rid}-body" style="margin-top:8px">
        Full reasoning and rules applied
      </button>
      <div class="reasoning-body" id="${rid}-body" ${isOpen ? "" : "hidden"}>
        <div class="split-note" style="margin-top:10px;margin-bottom:4px"><strong>Rules applied</strong></div>
        ${rulesHtml}
        <div class="split-note" style="margin-top:12px;margin-bottom:4px"><strong>What each analyst saw</strong></div>
        ${signalsHtml}
      </div>
    </div>`;
  }).join("");

  // Accordion behaviour: only one reasoning panel open at a time, and that
  // choice survives the live re-render because it's stored by ledger sequence
  // (see _openReceiptSeq) rather than in DOM state that gets rebuilt.
  document.querySelectorAll(".reasoning-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const seq = Number(btn.dataset.seq);
      const target = $(btn.dataset.target);
      const wasOpen = seq === _openReceiptSeq;
      // Collapse everything first.
      document.querySelectorAll(".reasoning-body").forEach((el) => { el.hidden = true; });
      document.querySelectorAll(".reasoning-toggle").forEach((b) => b.classList.remove("btn-primary"));
      if (wasOpen) {
        _openReceiptSeq = null;  // second click on the open one closes it
      } else {
        _openReceiptSeq = seq;
        target.hidden = false;
        btn.classList.add("btn-primary");
        target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
  });
}

/* ---------------- council ---------------- */

function renderCouncil(s) {
  $("roster").innerHTML = `<table><thead><tr>
    <th>Analyst</th><th>Looks at</th><th>Data source</th><th class="ta-r">Cost / call</th>
  </tr></thead><tbody>` + s.council.roster.map((a) => `<tr>
    <td><strong>${esc(a.name)}</strong></td>
    <td class="dim">${esc(a.description)}</td>
    <td class="num dim">${esc(a.data_provider)}</td>
    <td class="ta-r num">${a.cost_per_call_usd ? usd(a.cost_per_call_usd, 3) : "free"}</td>
  </tr>`).join("") + "</tbody></table>";

  const vs = s.council.verdicts || [];
  if (!vs.length) {
    $("verdicts").innerHTML = `<div class="panel"><div class="panel-body">
      <div class="empty"><strong>The Council has not met yet</strong>Start the engine.</div>
    </div></div>`;
    return;
  }
  $("verdicts").innerHTML = `<div class="grid g2">` + vs.map((v) => {
    const dirClass = v.direction === "bullish" ? "up" : v.direction === "bearish" ? "down" : "dim";
    return `<div class="panel">
      <div class="panel-head">
        <span class="panel-title">${esc(v.symbol)}</span>
        <span class="pill ${v.direction === "bullish" ? "ok" : v.direction === "bearish" ? "bad" : ""}">${esc(v.direction)}</span>
        <span class="right" style="margin-left:auto">
          <span class="panel-note">conviction <span class="num ${dirClass}">${(v.conviction * 100).toFixed(0)}%</span>
          · dissent <span class="num">${(v.dissent_ratio * 100).toFixed(0)}%</span></span>
        </span>
      </div>
      <div class="panel-body">
        ${v.transcript.map((t) => `
          <div style="padding:8px 0;border-bottom:1px solid var(--border-soft)">
            <div style="display:flex;gap:8px;align-items:baseline;margin-bottom:3px">
              <strong style="font-size:13.5px">${esc(t.agent)}</strong>
              <span class="pill ${t.stance === "bullish" ? "ok" : t.stance === "bearish" ? "bad" : ""}"
                style="font-size:11.5px;padding:1px 7px">${esc(t.stance)}</span>
              <span class="num dim" style="font-size:12.5px;margin-left:auto">${(t.confidence * 100).toFixed(0)}%</span>
            </div>
            <div class="dim" style="font-size:13.5px">${esc(t.rationale)}</div>
          </div>`).join("")}
        <div class="split-note">Position size is reduced by ${(v.dissent_ratio * 80).toFixed(0)}%
          to reflect the disagreement above. ${v.data_cost_usd > 0
            ? `Paid feeds for this scan cost ${usd(v.data_cost_usd, 4)}.`
            : "Paid feeds were served from cache, so this scan cost nothing."}</div>
      </div>
    </div>`;
  }).join("") + `</div>`;
}

/* ---------------- sentinel ---------------- */

function renderGauge(a) {
  const score = a.score, r = 44, c = 2 * Math.PI * r;
  const col = a.level === "critical" ? "var(--down)"
    : a.level === "elevated" ? "var(--warn)" : "var(--up)";
  $("gauge").innerHTML = `
    <svg width="104" height="104" viewBox="0 0 104 104">
      <circle cx="52" cy="52" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="8"/>
      <circle cx="52" cy="52" r="${r}" fill="none" stroke="${col}" stroke-width="8"
        stroke-linecap="round" stroke-dasharray="${c}"
        stroke-dashoffset="${c * (1 - score / 100)}"
        style="transition:stroke-dashoffset .6s ease,stroke .3s"/>
    </svg>
    <div class="gauge-val">
      <div class="gauge-num" style="color:${col}">${score.toFixed(0)}</div>
      <div class="gauge-lbl">${esc(a.level)}</div>
    </div>`;
}

function renderSentinel(s) {
  const g = s.guardian, a = g.assessment;
  if (!a) return;
  renderGauge(a);
  $("threat-rec").textContent = a.recommendation;
  $("threat-factors").innerHTML = a.factors.map((f) => `<div class="factor">
    <span class="factor-name">${esc(f.factor)}</span>
    <span class="factor-detail">${esc(f.detail)}</span>
    <span class="factor-pts">${f.contribution > 0 ? "+" + f.contribution.toFixed(0) : "0"}</span>
  </div>`).join("");

  $("guardian-stats").innerHTML = [
    ["Status", g.quarantined ? "<span class='down'>quarantined</span>" : "<span class='up'>active</span>"],
    ["Intents vetoed", g.veto_count],
    ["Hedges triggered", g.hedges.length],
    ["Elevated at", `${g.thresholds.elevated}/100`],
    ["Critical at", `${g.thresholds.critical}/100`],
    ["Quarantine at", `${g.thresholds.quarantine}/100`],
  ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("");

  const d = s.defensive_actions || [];
  $("defensive").innerHTML = d.length
    ? `<table><thead><tr><th>Time</th><th>Action</th><th>Asset</th><th class="ta-r">Size</th><th class="ta-r">Result</th></tr></thead><tbody>`
      + [...d].reverse().map((x) => `<tr>
        <td class="num dim">${clock(x.ts)}</td>
        <td><span class="pill ${x.kind === "guardian_hedge" ? "bad" : ""}">${esc(x.kind.replace("_", " "))}</span></td>
        <td><strong>${esc(x.symbol)}</strong></td>
        <td class="ta-r num">${usd(x.notional_usd, 0)}</td>
        <td class="ta-r num ${cls(x.pnl_usd ?? 0)}">${x.pnl_usd != null ? usd(x.pnl_usd) : `threat ${x.threat_score?.toFixed(0)}`}</td>
      </tr>`).join("") + "</tbody></table>"
    : `<div class="empty"><strong>Nothing has needed defending</strong>
       Stops, hedges and quarantines appear here. Use the drill controls above to force one.</div>`;

  const sel = $("shock-symbol");
  if (sel.options.length !== Object.keys(s.market).length) {
    sel.innerHTML = Object.keys(s.market).map((k) => `<option>${esc(k)}</option>`).join("");
  }
  $("scenario").value = s.scenario in { calm: 1, bull: 1, bear: 1, chop: 1, crash: 1 } ? s.scenario : "calm";
}

/* ---------------- narratives ---------------- */

function renderNarratives(s) {
  const ns = s.narrative.narratives || [];
  if (!ns.length) {
    $("narratives").innerHTML = s.running
      ? `<div class="empty">Scanning themes…<span class="dim">The first read appears within a tick.</span></div>`
      : `<div class="empty"><strong>Engine not started</strong>Press <strong>Start engine</strong> in the top bar — narrative strength is scored every tick once it's running, whether or not autonomous trading is on.</div>`;
    return;
  }
  $("narratives").innerHTML = ns.map((n) => {
    const hot = n.strength >= s.narrative.threshold && n.velocity > 0.01;
    return `<div class="bar-row">
      <span class="bar-label">${esc(n.label)}
        ${hot ? `<span class="pill ok" style="font-size:11px;padding:0 6px;margin-left:5px">tradable</span>` : ""}
        <div class="faint" style="font-size:12px">${n.symbols.join(", ")}</div>
      </span>
      <span class="bar-track"><span class="bar-fill ${hot ? "hot" : ""}" style="width:${(n.strength * 100).toFixed(0)}%"></span></span>
      <span class="bar-val">${(n.strength * 100).toFixed(0)}%
        <div class="${cls(n.velocity)}" style="font-size:11.5px">${n.velocity >= 0 ? "▲" : "▼"} ${Math.abs(n.velocity * 100).toFixed(1)}</div>
      </span>
    </div>`;
  }).join("") + `<div class="split-note">A theme becomes tradable only above
    ${(s.narrative.threshold * 100).toFixed(0)}% strength <em>and</em> still rising, and only while
    the mapped asset has not already moved more than 6% in 24 hours. Strength alone is news;
    strength that is still building ahead of price is the edge.</div>`;
}

/* ---------------- yield ---------------- */

function renderYield(s) {
  const plan = s.compass.plan || {};
  const ranked = plan.ranked || [];
  $("venues").innerHTML = `<table><thead><tr>
    <th>Venue</th><th>Type</th><th class="ta-r">APY</th>
    <th class="ta-r">Risk-adjusted</th><th class="ta-r">Lockup</th><th class="ta-r">Risk</th>
  </tr></thead><tbody>` + ranked.map((v, idx) => `<tr>
    <td>${idx === 0 ? "<strong>" : ""}${esc(v.venue)}${idx === 0 ? "</strong>" : ""}
      ${idx === 0 ? `<span class="pill ok" style="font-size:11px;padding:0 6px;margin-left:6px">chosen</span>` : ""}</td>
    <td class="dim">${v.kind === "binance_earn" ? "Binance Earn" : "DeFi"}</td>
    <td class="ta-r num dim">${v.apy_pct.toFixed(2)}%</td>
    <td class="ta-r num ${idx === 0 ? "up" : ""}">${v.risk_adjusted_apy_pct.toFixed(2)}%</td>
    <td class="ta-r num dim">${v.lockup_days ? v.lockup_days + "d" : "none"}</td>
    <td class="ta-r num dim">${(v.risk_score * 100).toFixed(0)}</td>
  </tr>`).join("") + "</tbody></table>";

  $("yield-stats").innerHTML = [
    ["Deployed", usd(s.compass.deployed_usd)],
    ["Earned", usd(s.compass.earned_usd, 4)],
    ["Kept as dry powder", usd(plan.reserve_usd || 0)],
    ["Minimum to move", usd(s.compass.min_idle_usd, 0)],
  ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("")
    + `<div class="split-note">${esc(plan.reason || "")}</div>`;

  const x = s.x402;
  $("x402").innerHTML = `
    <div class="bar-row">
      <span class="bar-label">Daily budget used</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.min(100, (x.spent_today_usd / x.daily_budget_usd) * 100).toFixed(0)}%"></span></span>
      <span class="bar-val">${usd(x.spent_today_usd, 3)}</span>
    </div>
    <div class="kv"><span class="kv-k">Cap per request</span><span class="kv-v">${usd(x.max_per_request_usd, 2)}</span></div>
    <div class="kv"><span class="kv-k">Remaining today</span><span class="kv-v">${usd(x.remaining_usd, 3)}</span></div>
    <div class="kv"><span class="kv-k">Requests paid</span><span class="kv-v">${x.purchase_count}</span></div>
    <div class="split-note">Autonomous spending is the least-watched surface in agentic finance.
      Every purchase here is attributed to the analyst that asked for it and written to the ledger,
      and the budget is enforced locally underneath Binance's own daily cap.</div>`;
}

/* ---------------- ledger ---------------- */

async function loadLedger() {
  try {
    const d = await api("/api/ledger?limit=150");
    $("ledger-count").textContent = `${d.height} records · head ${short(d.head, 12)}`;
    $("ledger").innerHTML = [...d.records].reverse().map((r) => `<div class="feed-row">
      <span class="feed-time">#${r.seq}</span>
      <span class="feed-tag tag-info">${esc(r.kind)}</span>
      <span class="feed-msg">
        <span class="dim">${clock(r.ts)}</span>
        <div class="chain-link" style="margin-top:3px">
          <span class="hash">${short(r.prev_hash, 8)}</span>
          <span class="chain-arrow">→</span>
          <span class="hash">${short(r.hash, 8)}</span>
        </div>
        <details class="reveal"><summary>payload</summary>
          <div><pre class="code">${esc(JSON.stringify(r.payload, null, 2).slice(0, 4000))}</pre></div>
        </details>
      </span>
    </div>`).join("");
  } catch (e) { toast(e.message, "bad"); }
}

$("verify").addEventListener("click", async () => {
  $("verify-out").innerHTML = `<div class="empty">Re-deriving every hash from genesis…</div>`;
  try {
    const v = await api("/api/ledger/verify");
    $("verify-out").innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
        <span class="pill ${v.valid ? "ok" : "bad"}">${v.valid ? "chain intact" : "chain broken"}</span>
        <span class="dim">${v.records_checked} records re-hashed and signature-checked</span>
      </div>
      <div class="kv"><span class="kv-k">Head</span><span class="kv-v hash">${esc(v.head)}</span></div>
      ${v.problems.length ? `<pre class="code">${esc(JSON.stringify(v.problems, null, 2))}</pre>` : `
        <div class="split-note">Every record's hash was recomputed from its predecessor and its
        signature checked against this installation's device key. Editing or deleting any past
        record would break this check for that record and every one after it.</div>`}`;
    toast(v.valid ? "Chain verified intact." : "Chain verification failed.", v.valid ? "good" : "bad");
  } catch (e) { toast(e.message, "bad"); }
});

/* ---------------- constitution ---------------- */

async function loadRules() {
  try {
    const d = await api("/api/constitution");
    $("rules-path").textContent = d.path;
    $("rules-raw").textContent = d.raw;
    $("rules").innerHTML = `<table><thead><tr>
      <th>Rule</th><th>Setting</th><th class="ta-r">Status</th>
    </tr></thead><tbody>` + d.rules.map((r) => `<tr>
      <td><strong class="num">${esc(r.name)}</strong></td>
      <td class="dim num" style="font-size:13px">${esc(JSON.stringify(r.config))}</td>
      <td class="ta-r"><span class="pill ${r.enabled && r.implemented ? "ok" : "bad"}">
        ${!r.implemented ? "not implemented" : r.enabled ? "enforced" : "off"}</span></td>
    </tr>`).join("") + "</tbody></table>";
  } catch (e) { toast(e.message, "bad"); }
}

$("reload-rules").addEventListener("click", async () => {
  try { await api("/api/constitution/reload", "POST"); await loadRules(); toast("Constitution reloaded and the change recorded.", "good"); }
  catch (e) { toast(e.message, "bad"); }
});

/* ---------------- controls ---------------- */

$("run").addEventListener("click", async () => {
  try {
    const on = STATE && STATE.running;
    await api(on ? "/api/engine/stop" : "/api/engine/start", "POST");
    toast(on ? "Engine paused. Open positions untouched." : "Engine running.", "good");
  } catch (e) { toast(e.message, "bad"); }
});

$("kill").addEventListener("click", async () => {
  const engaged = $("kill").classList.contains("engaged");
  try {
    await api("/api/kill-switch", "POST", { on: !engaged });
    $("kill").classList.toggle("engaged", !engaged);
    $("kill").textContent = !engaged ? "Kill switch engaged" : "Stop all new risk";
    toast(!engaged ? "Kill switch engaged. No new risk will be taken." : "Kill switch released.",
      !engaged ? "bad" : "good");
  } catch (e) { toast(e.message, "bad"); }
});

$("apply-scenario").addEventListener("click", async () => {
  try { await api("/api/scenario", "POST", { scenario: $("scenario").value }); toast(`Regime set to ${$("scenario").value}.`); }
  catch (e) { toast(e.message, "bad"); }
});

$("apply-shock").addEventListener("click", async () => {
  try {
    await api("/api/shock", "POST", { symbol: $("shock-symbol").value, pct: parseFloat($("shock-pct").value) });
    toast(`Shock injected into ${$("shock-symbol").value}.`, "bad");
  } catch (e) { toast(e.message, "bad"); }
});

$("crash-all").addEventListener("click", async () => {
  try {
    await api("/api/scenario", "POST", { scenario: "crash" });
    for (const s of Object.keys(STATE.market)) {
      await api("/api/shock", "POST", { symbol: s, pct: parseFloat($("shock-pct").value) });
    }
    toast("Market-wide crash injected. Watch the Guardian.", "bad");
  } catch (e) { toast(e.message, "bad"); }
});

$("clear-q").addEventListener("click", async () => {
  try { await api("/api/quarantine/clear", "POST"); toast("Quarantine cleared.", "good"); }
  catch (e) { toast(e.message, "bad"); }
});

/* ---------------- markets ---------------- */

const compact = (n) =>
  n >= 1e9 ? `$${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `$${(n / 1e6).toFixed(0)}M`
  : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}K` : `$${n.toFixed(0)}`;

const priceFmt = (p) =>
  p >= 1000 ? p.toLocaleString(undefined, { maximumFractionDigits: 2 })
  : p >= 1 ? p.toFixed(4) : p.toPrecision(4);

let marketsBusy = false;

async function loadMarkets() {
  if (marketsBusy) return;
  marketsBusy = true;
  const host = $("markets");
  host.innerHTML = `<div class="empty">Loading live pairs from Binance…</div>`;
  try {
    const params = new URLSearchParams({
      quote: $("mkt-quote").value,
      search: $("mkt-search").value,
      sort: $("mkt-sort").value,
      limit: "300",
    });
    const d = await api(`/api/markets?${params}`);
    $("markets-note").textContent =
      `${d.total_pairs} tradable ${$("mkt-quote").value} pairs, live from ${d.feeds.spot_host || "Binance"}`;
    $("mkt-count").textContent = `${d.matched} matched`;
    const bp = $("badge-pairs");
    bp.hidden = false; bp.textContent = d.total_pairs;

    if (!d.rows.length) {
      host.innerHTML = `<div class="empty"><strong>Nothing matched</strong>Try a different search or quote asset.</div>`;
      return;
    }
    window._marketRows = d.rows;   // cached so header clicks re-sort without refetching
    renderMarketTable();
  } catch (e) {
    host.innerHTML = `<div class="empty"><strong>Could not reach Binance</strong>${esc(e.message)}</div>`;
  } finally { marketsBusy = false; }
}

function renderMarketTable() {
  const host = $("markets");
  const rows = applySort("markets", window._marketRows || []);
  const cols = [
    { key: "base", label: "Pair", align: "" },
    { key: "price", label: "Price", align: "ta-r" },
    { key: "change_24h_pct", label: "24h", align: "ta-r" },
    { key: "volume_24h_usd_normalised", label: "Volume", align: "ta-r" },
    { key: "spread_bps", label: "Spread", align: "ta-r" },
    { key: "on_allowlist", label: "Status", align: "ta-r" },
  ];
  host.innerHTML = `<table><thead><tr>`
    + cols.map((c) => `<th class="${c.align} sortable" data-key="${c.key}">${c.label} ${sortIndicator("markets", c.key)}</th>`).join("")
    + `</tr></thead><tbody>` + rows.map((r) => `<tr data-sym="${esc(r.symbol)}" style="cursor:pointer">
      <td><strong>${esc(r.base)}</strong><span class="faint">/${esc(r.quote)}</span></td>
      <td class="ta-r num">${priceFmt(r.price)}</td>
      <td class="ta-r num ${cls(r.change_24h_pct)}">${pct(r.change_24h_pct, 2)}</td>
      <td class="ta-r num dim">${compact(r.volume_24h_usd)}</td>
      <td class="ta-r num dim">${r.spread_bps.toFixed(2)}bp</td>
      <td class="ta-r">${r.on_allowlist
        ? `<span class="pill ok" style="font-size:11px;padding:0 6px">tradable</span>`
        : r.watched ? `<span class="pill" style="font-size:11px;padding:0 6px">watched</span>`
        : `<span class="faint" style="font-size:12px">not permitted</span>`}</td>
    </tr>`).join("") + "</tbody></table>";

  host.querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => { cycleSort("markets", th.dataset.key); renderMarketTable(); }));
  host.querySelectorAll("[data-sym]").forEach((tr) =>
    tr.addEventListener("click", () => loadSymbol(tr.dataset.sym)));
}

async function loadSymbol(symbol) {
  const host = $("symbol-detail");
  host.innerHTML = `<div class="empty">Running every analyst on ${esc(symbol)}…</div>`;
  try {
    const d = await api(`/api/symbol/${encodeURIComponent(symbol)}`);
    const t = d.ticker || {};

    host.innerHTML = `
      <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px">
        <span style="font-size:18px;font-weight:650">${esc(d.symbol)}</span>
        <span class="num" style="font-size:16px">${priceFmt(d.price)}</span>
        <span class="num ${cls(t.priceChangePercent)}">${pct(parseFloat(t.priceChangePercent || 0))}</span>
      </div>
      <div id="pair-chart" style="margin-bottom:12px"></div>
      <div class="kv"><span class="kv-k">24h range</span><span class="kv-v">${priceFmt(parseFloat(t.lowPrice || 0))} – ${priceFmt(parseFloat(t.highPrice || 0))}</span></div>
      <div class="kv"><span class="kv-k">24h volume</span><span class="kv-v">${compact(parseFloat(t.quoteVolume || 0))}</span></div>
      <div class="kv"><span class="kv-k">Trades</span><span class="kv-v">${Number(t.count || 0).toLocaleString()}</span></div>
      <div class="kv"><span class="kv-k">Book depth shown</span><span class="kv-v">${d.book.bids.length} / ${d.book.asks.length}</span></div>
      <div style="margin-top:12px;font-weight:600;font-size:13.5px">What the analysts see right now</div>
      ${d.signals.map((sg) => {
        const q = sg.evidence?.data_quality || "live";
        return `<div style="padding:8px 0;border-bottom:1px solid var(--border-soft)">
          <div style="display:flex;gap:8px;align-items:baseline">
            <strong style="font-size:13.5px">${esc(sg.agent)}</strong>
            <span class="pill ${sg.stance === "bullish" ? "ok" : sg.stance === "bearish" ? "bad" : ""}"
              style="font-size:11.5px;padding:1px 7px">${esc(sg.stance)}</span>
            ${q !== "live" ? `<span class="pill bad" style="font-size:11px;padding:0 6px">${esc(q)}</span>` : ""}
            <span class="num dim" style="font-size:12.5px;margin-left:auto">${(sg.confidence * 100).toFixed(0)}%</span>
          </div>
          <div class="dim" style="font-size:13px;margin-top:3px">${esc(sg.rationale)}</div>
        </div>`;
      }).join("")}`;
    drawCandles(symbol, "1h", "pair-chart");
  } catch (e) {
    host.innerHTML = `<div class="empty"><strong>Could not load ${esc(symbol)}</strong>${esc(e.message)}</div>`;
  }
}

function sparkPath(vals, w, h) {
  if (!vals || vals.length < 2) return "";
  const lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const x = (i) => (i / (vals.length - 1)) * w;
  const y = (v) => h - 3 - ((v - lo) / r) * (h - 6);
  const dpath = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  const col = up ? "var(--up)" : "var(--down)";
  return `<path d="${dpath}" fill="none" stroke="${col}" stroke-width="1.4"/>`;
}

["mkt-search", "mkt-quote", "mkt-sort"].forEach((id) => {
  const el = $(id);
  el.addEventListener(id === "mkt-search" ? "input" : "change", () => {
    clearTimeout(el._t);
    el._t = setTimeout(loadMarkets, id === "mkt-search" ? 400 : 0);
  });
});
$("mkt-refresh").addEventListener("click", loadMarkets);

/* ---------------- binance / MCP ---------------- */

// Binance's tool ids are snake_case protocol identifiers. Show a human label,
// keep the real id underneath so the protocol stays inspectable.
const TOOL_LABELS = {
  get_ticker: "Check price",
  get_account_balance: "Read balances",
  place_spot_order: "Place spot order",
  cancel_order: "Cancel order",
  get_open_orders: "List open orders",
  get_order_history: "Order history",
  transfer_between_wallets: "Move funds between wallets",
};

function prettyTool(name) {
  if (TOOL_LABELS[name]) return TOOL_LABELS[name];
  return String(name || "")
    .replace(/^binance_/, "")
    .replace(/_v\d+$/, "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

/* ---------------- payments & settlement ---------------- */

async function loadPayments() {
  // Wallet status
  try {
    const w = await api("/api/payments/wallet");
    $("pay-wallet").innerHTML = [
      ["Address", `<span class="hash">${esc(w.address)}</span>
        ${w.explorer_url ? ` <a href="${esc(w.explorer_url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">view on explorer</a>` : ""}`],
      ["Network reachable", w.network_reachable ? `<span class="up">yes</span>` : `<span class="down">no</span>`],
      ["Test ETH balance", `${w.balance_eth ?? 0} ETH ${w.funded ? "" : `<span class="dim">(unfunded)</span>`}`],
      ["Test USDC balance", `${w.usdc_balance_display ?? 0} USDC`],
      ["USDC contract verified", w.usdc_contract_verified ? `<span class="up">yes, live-checked</span>` : `<span class="down">no</span>`],
      ["Chain", esc(w.chain || "")],
    ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("")
      + `<div class="split-note">${esc(w.note || "")}</div>`
      + (w.faucets && w.faucets.length
          ? `<div class="split-note">Fund it (free, one-time) from a faucet:
             ${w.faucets.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">${esc(u.replace("https://",""))}</a>`).join(" · ")}
             ${w.usdc_faucet ? ` · USDC: <a href="${esc(w.usdc_faucet)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">${esc(w.usdc_faucet.replace("https://",""))}</a>` : ""}</div>`
          : "");
  } catch (e) {
    $("pay-wallet").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }

  // Chain list for the inspector
  const chainSel = $("insp-chain");
  if (chainSel && !chainSel.options.length) {
    try {
      const d = await api("/api/chain/chains");
      chainSel.innerHTML = d.chains.map((c) =>
        `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
      chainSel.value = "bnb";
    } catch { /* leave empty */ }
  }

  // Allowlist + payment history
  try {
    const st = await api("/api/status");
    const pay = st.payments || {};
    const allow = pay.allowlist || {};
    const names = Object.keys(allow);
    $("pay-allowlist").innerHTML = names.length
      ? `<table><thead><tr><th>Name</th><th>Address</th><th class="ta-r">Action</th></tr></thead><tbody>`
        + names.map((n) => `<tr>
            <td><strong>${esc(n)}</strong></td>
            <td><span class="hash">${esc(allow[n])}</span></td>
            <td class="ta-r"><button class="btn btn-sm pay-cp-remove" data-name="${esc(n)}">Remove</button></td>
          </tr>`).join("") + "</tbody></table>"
      : `<div class="empty"><strong>No counterparties yet</strong>Add one above before any payment can be made.</div>`;

    $("pay-allowlist").querySelectorAll(".pay-cp-remove").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try {
          await api("/api/payments/allowlist", "POST", { counterparty: btn.dataset.name, allow: false });
          toast("Removed.", "good"); loadPayments();
        } catch (e) { toast(e.message, "bad"); }
      }));

    const recent = pay.recent || [];
    $("pay-history").innerHTML = recent.length
      ? `<table><thead><tr><th>To</th><th class="ta-r">Amount</th><th>Status</th><th>Proof</th></tr></thead><tbody>`
        + [...recent].reverse().map((p) => `<tr>
            <td><strong>${esc(p.counterparty)}</strong></td>
            <td class="ta-r num">${usd(p.amount_usd)}</td>
            <td><span class="pill ${p.status === "sent" ? "ok" : "bad"}">${esc(p.status)}</span></td>
            <td>${p.explorer_url
              ? `<a href="${esc(p.explorer_url)}" target="_blank" rel="noopener" class="hash" style="text-decoration:underline">${short(p.tx_ref || "", 10)}</a>`
              : `<span class="dim">${esc((p.message || "").slice(0, 60))}</span>`}</td>
          </tr>`).join("") + "</tbody></table>"
      : `<div class="empty"><strong>No payments made yet</strong>Add a counterparty, then payments will settle on-chain and appear here.</div>`;
  } catch (e) {
    $("pay-history").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

$("insp-run").addEventListener("click", async () => {
  const query = $("insp-query").value.trim();
  const chain = $("insp-chain").value;
  if (!query) { toast("Enter an address or transaction hash.", "bad"); return; }
  $("insp-status").textContent = "Looking up…";
  $("insp-result").innerHTML = "";
  try {
    const r = await api("/api/chain/inspect", "POST", { query, chain });
    if (!r.ok) {
      $("insp-result").innerHTML = `<div class="banner warn">${esc(r.error)}</div>`;
    } else if (r.type === "address") {
      $("insp-result").innerHTML = `<div class="banner"><strong>Address on ${esc(r.chain)}</strong></div>
        <div class="kv"><span class="kv-k">Balance</span><span class="kv-v num">${r.balance} ${esc(r.symbol)}</span></div>
        ${r.tx_count != null ? `<div class="kv"><span class="kv-k">Transactions sent</span><span class="kv-v num">${r.tx_count}</span></div>` : ""}
        <div class="split-note">${esc(r.note)}</div>
        <a class="btn btn-sm" href="${esc(r.explorer_url)}" target="_blank" rel="noopener" style="margin-top:8px">Open in explorer</a>`;
    } else {
      $("insp-result").innerHTML = `<div class="banner ${r.status === "failed" ? "crit" : ""}"><strong>Transaction on ${esc(r.chain)}</strong></div>
        ${r.status ? `<div class="kv"><span class="kv-k">Status</span><span class="kv-v ${r.status === "success" ? "up" : "down"}">${esc(r.status)}</span></div>` : ""}
        ${r.value != null ? `<div class="kv"><span class="kv-k">Value</span><span class="kv-v num">${r.value} ${esc(r.symbol || "")}</span></div>` : ""}
        ${r.from ? `<div class="kv"><span class="kv-k">From</span><span class="kv-v"><span class="hash">${esc(r.from)}</span></span></div>` : ""}
        ${r.to ? `<div class="kv"><span class="kv-k">To</span><span class="kv-v"><span class="hash">${esc(r.to)}</span></span></div>` : ""}
        ${r.block != null ? `<div class="kv"><span class="kv-k">Block / slot</span><span class="kv-v num">${r.block}</span></div>` : ""}
        <div class="split-note">${esc(r.note)}</div>
        <a class="btn btn-sm" href="${esc(r.explorer_url)}" target="_blank" rel="noopener" style="margin-top:8px">Open in explorer</a>`;
    }
  } catch (e) {
    $("insp-result").innerHTML = `<div class="banner warn">${esc(e.message)}</div>`;
  } finally { $("insp-status").textContent = ""; }
});

$("pay-cp-add").addEventListener("click", async () => {
  const name = $("pay-cp-name").value.trim();
  const address = $("pay-cp-addr").value.trim();
  if (!name || !address) { toast("Enter both a name and an address.", "bad"); return; }
  try {
    await api("/api/payments/allowlist", "POST", { counterparty: name, address, allow: true });
    toast("Counterparty added.", "good");
    $("pay-cp-name").value = ""; $("pay-cp-addr").value = "";
    loadPayments();
  } catch (e) { toast(e.message, "bad"); }
});

async function loadMCP() {
  try {
    const d = await api("/api/mcp");
    const st = d.status;
    const modeNote = {
      paper: "Paper mode: fills are simulated locally and no order reaches Binance.",
      shadow: "Shadow mode: live Binance data, simulated fills.",
      mock: "Mock mode: a real MCP client against a local stand-in for Binance. "
          + "Same protocol, same client, same error handling — no money at risk.",
      live: "Live mode: orders are placed in your Agentic sub-account on Binance.",
      bridge: "Bridge mode: signed instructions are emitted for you to run yourself.",
    }[d.mode] || "";

    $("mcp-banner").innerHTML = `<div class="banner ${d.mode === "live" ? "crit" : "warn"}">
      <strong>${esc(d.mode)} mode.</strong> ${esc(modeNote)}</div>`;

    // The mock server needs no OAuth and connects instantly; every other mode
    // points at Binance's real server, which requires the full Authorize
    // flow. Clicking the plain "Connect" button against a real endpoint with
    // no session can only ever fail — previously with an error that told the
    // person to run a shell command. This routes to the right flow instead
    // of failing.
    const isRealEndpoint = !d.endpoint.includes("127.0.0.1");
    const needsSetup = isRealEndpoint && !st.connected;
    $("mcp-connect").hidden = needsSetup;
    $("mcp-clientid-panel").hidden = !needsSetup;
    $("mcp-authorize-panel").hidden = true; // decided below, once we know client-id status

    if (needsSetup) {
      await renderClientIdPanel("mcp-clientid-body");
      const clientIdInfo = await api("/api/mcp/client-id").catch(() => ({ configured: false }));
      $("mcp-authorize-panel").hidden = !clientIdInfo.configured;
      if (clientIdInfo.configured) await renderAuthorizePanel("mcp-authorize-body");
    }

    $("mcp-detail").innerHTML = [
      ["Session", st.connected
        ? `<span class="up">connected</span>` : `<span class="down">not connected</span>`],
      ["Endpoint", `<span class="hash">${esc(d.endpoint)}</span>`],
      ["Server", esc(st.server || "—")],
      ["Protocol", esc(st.protocol_version || "—")],
      ["Tools available", st.tool_count],
      ["Granted scope", esc(st.scope || "—")],
      ["Session expires in", st.expires_in_s ? `${Math.round(st.expires_in_s / 60)} min` : "—"],
      ["Calls / failures", `${st.calls} / ${st.failures}`],
    ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("")
      + (st.last_error ? `<div class="split-note down">${esc(st.last_error)}</div>` : "")
      + `<div class="split-note">There is no withdrawal scope on Binance Agent OS, and this
         client never asks for one. An agent cannot move funds out of the sub-account, and
         cannot pull funds in from your main account — that first transfer is always
         yours to make.</div>`;

    $("mcp-tools").innerHTML = st.tool_count
      ? `<table><thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>`
        + st.tools.map((t) => `<tr>
            <td><strong>${esc(prettyTool(t.name))}</strong>
              <div class="faint num" style="font-size:11.5px">${esc(t.name)}</div></td>
            <td class="dim">${esc(t.description)}</td></tr>`).join("") + "</tbody></table>"
      : `<div class="empty"><strong>No session</strong>Connect to see what Binance exposes.</div>`;

    const ex = d.execution;
    $("mcp-orders").innerHTML = ex.orders_sent
      ? `<table><thead><tr><th>Status</th><th>Side</th><th>Pair</th>
          <th class="ta-r">Filled</th><th class="ta-r">Price</th><th class="ta-r">Latency</th>
          <th>Order</th><th>Justification</th></tr></thead><tbody>`
        + [...ex.recent].reverse().map((o) => `<tr>
            <td><span class="pill ${o.status === "filled" ? "ok" : "bad"}">${esc(o.status)}</span></td>
            <td class="${o.side === "BUY" ? "up" : "down"}">${esc(o.side)}</td>
            <td><strong>${esc(o.symbol)}</strong></td>
            <td class="ta-r num">${usd(o.filled_usd)}</td>
            <td class="ta-r num dim">${o.price ? o.price.toLocaleString() : "—"}</td>
            <td class="ta-r num dim">${o.latency_ms}ms</td>
            <td class="num faint">${esc(o.order_id || "—")}</td>
            <td class="hash">${short(o.justification_hash, 10)}</td>
          </tr>`).join("") + "</tbody></table>"
      : `<div class="empty"><strong>No orders sent yet</strong>
         Orders appear here once the Council proposes one and the Constitution allows it.</div>`;

    if (st.connected) loadBalances();
    else $("mcp-balances").innerHTML =
      `<div class="empty"><strong>Not connected</strong>Connect to read your balances.</div>`;

    const b = $("badge-mcp");
    b.hidden = !st.connected; b.className = "nav-badge";
  } catch (e) {
    $("mcp-detail").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function loadBalances() {
  try {
    const b = await api("/api/mcp/balances");
    if (!b.available) {
      $("mcp-balances").innerHTML = `<div class="empty"><strong>Balances unavailable</strong>${esc(b.reason || "")}</div>`;
      return;
    }
    $("mcp-balances").innerHTML = `
      <div style="font-size:23px;font-weight:650" class="num">${usd(b.totalUsdValue || 0)}</div>
      <div class="metric-sub" style="margin-bottom:10px">${esc(b.accountType || "AGENTIC_SUB")}</div>
      ${(b.balances || []).map((r) => `<div class="kv">
        <span class="kv-k"><strong>${esc(r.asset)}</strong></span>
        <span class="kv-v">${r.free} <span class="dim">(${usd(r.usdValue || 0)})</span></span>
      </div>`).join("")}
      <div class="split-note">Read straight from Binance over MCP. This is authoritative —
      the local book is reconciled against it, not the other way round.</div>`;
  } catch (e) {
    $("mcp-balances").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

$("mcp-connect").addEventListener("click", async () => {
  const b = $("mcp-connect"); b.disabled = true; b.textContent = "Connecting…";
  try {
    const r = await api("/api/mcp/connect", "POST");
    toast(r.ok ? `Connected — ${r.tool_count} tools available.` : r.error, r.ok ? "good" : "bad");
    await loadMCP();
  } catch (e) { toast(e.message, "bad"); }
  finally { b.disabled = false; b.textContent = "Connect"; }
});

$("mcp-disconnect").addEventListener("click", async () => {
  try { await api("/api/mcp/disconnect", "POST"); toast("Disconnected and session cleared."); await loadMCP(); }
  catch (e) { toast(e.message, "bad"); }
});

/* ---------------- coins ---------------- */

let coinsBusy = false;

async function loadCoins() {
  if (coinsBusy) return;
  coinsBusy = true;
  const host = $("coins");
  host.innerHTML = `<div class="empty">Aggregating every coin across all its pairs…</div>`;
  try {
    const d = await api(`/api/coins?search=${encodeURIComponent($("coin-search").value)}&limit=150`);
    $("coin-count").textContent = `${d.matched} of ${d.total_assets} assets`;
    const bc = $("badge-coins");
    bc.hidden = false; bc.textContent = d.total_assets;
    if (!d.rows.length) {
      host.innerHTML = `<div class="empty"><strong>No coin matched</strong>Try a different search.</div>`;
      return;
    }
    window._coinRows = d.rows;
    renderCoinTable();
  } catch (e) {
    host.innerHTML = `<div class="empty"><strong>Could not load coins</strong>${esc(e.message)}</div>`;
  } finally { coinsBusy = false; }
}

function renderCoinTable() {
  const host = $("coins");
  const rows = applySort("coins", window._coinRows || []);
  const cols = [
    { key: "asset", label: "Asset", align: "" },
    { key: "price_usd", label: "Price", align: "ta-r" },
    { key: "change_24h_pct", label: "24h", align: "ta-r" },
    { key: "volume_24h_usd", label: "Volume, all pairs", align: "ta-r" },
    { key: "pair_count", label: "Pairs", align: "ta-r" },
  ];
  host.innerHTML = `<table><thead><tr>`
    + cols.map((c) => `<th class="${c.align} sortable" data-key="${c.key}">${c.label} ${sortIndicator("coins", c.key)}</th>`).join("")
    + `<th>Trades against</th></tr></thead><tbody>` + rows.map((r) => `<tr data-asset="${esc(r.asset)}" style="cursor:pointer">
      <td><strong>${esc(r.asset)}</strong>${r.is_quote_asset
        ? `<span class="faint" style="font-size:11.5px;margin-left:6px">quote asset</span>` : ""}</td>
      <td class="ta-r num">${priceFmt(r.price_usd)}</td>
      <td class="ta-r num ${cls(r.change_24h_pct)}">${pct(r.change_24h_pct, 2)}</td>
      <td class="ta-r num dim">${compact(r.volume_24h_usd)}</td>
      <td class="ta-r num dim">${r.pair_count}</td>
      <td class="faint" style="font-size:12px">${r.quotes.slice(0, 6).join(" ")}</td>
    </tr>`).join("") + "</tbody></table>";

  host.querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => { cycleSort("coins", th.dataset.key); renderCoinTable(); }));
  host.querySelectorAll("[data-asset]").forEach((tr) =>
    tr.addEventListener("click", () => {
      $("mkt-search").value = tr.dataset.asset;
      document.querySelector("[data-view='markets']").click();
    }));
}

$("coin-refresh").addEventListener("click", loadCoins);
$("coin-search").addEventListener("input", () => {
  clearTimeout($("coin-search")._t);
  $("coin-search")._t = setTimeout(loadCoins, 400);
});

/* ---------------- data health ---------------- */

function renderHealth(s) {
  const rl = s.ratelimit, st = s.stream;
  const host = $("data-health");
  if (!host || !rl) return;
  const pctUsed = rl.utilisation_pct;
  // Three tiers against GlassBox's own self-imposed ceiling (not Binance's
  // hard limit): green while comfortably under it, amber approaching it,
  // red only if the ceiling itself is nearly exhausted — which should be
  // rare, since the ceiling is already 60% of what Binance allows.
  const barClass = pctUsed >= 90 ? "critical" : pctUsed >= 70 ? "elevated" : "";
  host.innerHTML = `
    <div class="bar-row">
      <span class="bar-label">Binance API weight this minute</span>
      <span class="bar-track"><span class="bar-fill ${barClass}"
        style="width:${Math.min(pctUsed, 100).toFixed(0)}%"></span></span>
      <span class="bar-val">${rl.used_1m} / ${rl.soft_ceiling}</span>
    </div>
    <div class="split-note" style="margin:-2px 0 8px">GlassBox's own budget for itself this
      minute — deliberately set well under Binance's real limit (below), not the limit itself.</div>
    <div class="kv"><span class="kv-k">Budget health</span>
      <span class="kv-v ${rl.health === "healthy" ? "up" : rl.health === "banned" ? "down" : ""}">${esc(rl.health)}</span></div>
    <div class="kv"><span class="kv-k">Hard limit</span><span class="kv-v">${rl.limit_1m} / min</span></div>
    <div class="kv"><span class="kv-k">Times throttled</span><span class="kv-v">${rl.throttle_events}</span></div>
    <div class="kv"><span class="kv-k">Websocket</span>
      <span class="kv-v ${st.health === "live" ? "up" : "down"}">${esc(st.health)}</span></div>
    <div class="kv"><span class="kv-k">Symbols streaming</span><span class="kv-v">${st.symbols_streaming} (${st.symbols_fresh} fresh)</span></div>
    <div class="kv"><span class="kv-k">Reconnects</span><span class="kv-v">${st.reconnects}</span></div>
    <div class="split-note">Binance meters requests by weight, not count: one all-symbol ticker
      call costs 80, an order book costs 5. Exceeding 6,000 per minute earns a 429, and pushing
      through that earns an IP ban. GlassBox reserves budget before each call and refuses to
      exceed ${rl.soft_ceiling} — 60% of the limit — leaving headroom for anything else sharing
      this address. Prices come from one websocket carrying every symbol on Binance, which costs
      no request weight at all.</div>`;
}

/* ---------------- candlestick chart ---------------- */

function candleChart(candles, w, h) {
  if (!candles || candles.length < 2) return "";
  const pad = { l: 4, r: 52, t: 8, b: 22 };
  const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;
  const hi = Math.max(...candles.map((c) => c.h));
  const lo = Math.min(...candles.map((c) => c.l));
  const range = hi - lo || 1;
  const y = (v) => pad.t + ch - ((v - lo) / range) * ch;
  const step = cw / candles.length;
  const bw = Math.max(step * 0.62, 1);

  const bars = candles.map((c, i) => {
    const x = pad.l + i * step + step / 2;
    const up = c.c >= c.o;
    const col = up ? "var(--up)" : "var(--down)";
    const top = y(Math.max(c.o, c.c));
    const bot = y(Math.min(c.o, c.c));
    return `<line x1="${x.toFixed(1)}" y1="${y(c.h).toFixed(1)}" x2="${x.toFixed(1)}"
              y2="${y(c.l).toFixed(1)}" stroke="${col}" stroke-width="1"/>
            <rect x="${(x - bw / 2).toFixed(1)}" y="${top.toFixed(1)}"
              width="${bw.toFixed(1)}" height="${Math.max(bot - top, 1).toFixed(1)}"
              fill="${col}"/>`;
  }).join("");

  // Price gridlines with labels on the right, the way a trading terminal reads.
  const lines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = lo + range * (1 - f);
    const yy = pad.t + ch * f;
    return `<line x1="${pad.l}" y1="${yy.toFixed(1)}" x2="${pad.l + cw}" y2="${yy.toFixed(1)}"
              stroke="var(--border-soft)" stroke-width="1"/>
            <text x="${pad.l + cw + 5}" y="${(yy + 3).toFixed(1)}" font-size="9"
              fill="var(--text-faint)" font-family="var(--mono)">${priceFmt(v)}</text>`;
  }).join("");

  const first = new Date(candles[0].t).toISOString().slice(5, 16).replace("T", " ");
  const last = new Date(candles[candles.length - 1].t).toISOString().slice(5, 16).replace("T", " ");
  return `${lines}${bars}
    <text x="${pad.l}" y="${h - 6}" font-size="9" fill="var(--text-faint)"
      font-family="var(--mono)">${first}</text>
    <text x="${pad.l + cw}" y="${h - 6}" font-size="9" fill="var(--text-faint)"
      font-family="var(--mono)" text-anchor="end">${last}</text>`;
}

/* Charts refresh themselves.
 *
 * They used to render once and then sit frozen — on a 15m interval that meant
 * a "live" chart could be a quarter of an hour stale while the price ticker
 * above it moved. The cadence is tied to the candle interval, because polling
 * a 1d chart every 10 seconds is wasted request weight for a bar that changes
 * four times an hour at most. */
let chartTimer = null;
const CHART_REFRESH_MS = { "15m": 15000, "1h": 30000, "4h": 60000, "1d": 120000 };

function stopChartRefresh() {
  if (chartTimer) { clearInterval(chartTimer); chartTimer = null; }
}

async function drawCandles(symbol, interval, hostId, isRefresh = false) {
  const host = $(hostId);
  if (!host) { stopChartRefresh(); return; }
  // Only show the loading state on a real (re)draw. A background refresh
  // that blanked the chart every 15 seconds would be worse than a stale one.
  if (!isRefresh) host.innerHTML = `<div class="empty">Loading ${esc(symbol)} candles…</div>`;
  try {
    const d = await api(`/api/candles/${encodeURIComponent(symbol)}?interval=${interval}&limit=160`);
    const c = d.candles;
    const last = c[c.length - 1], first = c[0];
    const chg = (last.c / first.o - 1) * 100;
    host.innerHTML = `
      <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;flex-wrap:wrap">
        <strong style="font-size:16px">${esc(d.symbol)}</strong>
        <span class="num" style="font-size:16px">${priceFmt(last.c)}</span>
        <span class="num ${cls(chg)}">${pct(chg)}</span>
        <span class="dim" style="font-size:12.5px">over ${c.length} × ${esc(interval)}</span>
        <span class="dim" style="font-size:12px">· live, updated ${new Date().toLocaleTimeString()}</span>
        <span style="margin-left:auto" class="ctl-row" id="iv-${esc(symbol)}">
          ${["15m", "1h", "4h", "1d"].map((iv) => `<button class="btn btn-sm ${iv === interval ? "btn-primary" : ""}"
            data-iv="${iv}" data-sym="${esc(symbol)}" data-host="${hostId}">${iv}</button>`).join("")}
        </span>
      </div>
      <svg viewBox="0 0 700 260" style="width:100%;height:260px;display:block">
        ${candleChart(c, 700, 260)}
      </svg>`;
    host.querySelectorAll("[data-iv]").forEach((b) =>
      b.addEventListener("click", () => drawCandles(b.dataset.sym, b.dataset.iv, b.dataset.host)));

    if (!isRefresh) {
      stopChartRefresh();
      chartTimer = setInterval(() => {
        // Stop as soon as the chart leaves the DOM (tab switched, different
        // pair selected) so we never poll for something nobody is looking at.
        if (!document.body.contains($(hostId))) { stopChartRefresh(); return; }
        drawCandles(symbol, interval, hostId, true);
      }, CHART_REFRESH_MS[interval] || 30000);
    }
  } catch (e) {
    if (!isRefresh) host.innerHTML = `<div class="empty"><strong>No chart</strong>${esc(e.message)}</div>`;
    // A failed background refresh leaves the last good chart on screen rather
    // than replacing real data with an error box.
  }
}

/* ---------------- news / events ---------------- */

async function loadNews(force = false) {
  try {
    const d = await api(`/api/news${force ? "?force=true" : ""}`);
    // Visible proof the feed is alive — a frozen timestamp is the symptom
    // that made this look broken before.
    const ageEl = $("news-updated");
    if (ageEl) {
      const age = d.last_refresh_age_s;
      ageEl.textContent = age == null ? "" :
        age < 60 ? `updated ${Math.round(age)}s ago`
                 : `updated ${Math.round(age / 60)}m ago`;
    }
    const r = d.risk;
    const tone = r.level === "high" ? "crit" : r.level === "elevated" ? "warn" : "warn";
    $("news-banner").innerHTML = `<div class="banner ${tone}">
      <strong>Event risk: ${esc(r.level)} (${r.score}/100).</strong>
      ${r.blackout ? "Trading is in blackout. " : ""}
      Position sizes are at ${(r.size_multiplier * 100).toFixed(0)}% of normal.</div>`;

    $("news-risk").innerHTML = [
      ["Risk level", `<span class="${r.level === "normal" ? "up" : "down"}">${esc(r.level)}</span>`],
      ["Score", `${r.score}/100`],
      ["Position sizing", `${(r.size_multiplier * 100).toFixed(0)}% of normal`],
      ["Blackout", r.blackout ? `<span class="down">yes</span>` : "no"],
      ["Blocked symbols", r.blocked_symbols.length ? r.blocked_symbols.join(", ") : "none"],
    ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("")
      + r.reasons.map((x) => `<div class="split-note">${esc(x)}</div>`).join("")
      + `<div class="split-note"><strong>${esc(r.policy)}</strong></div>`;

    $("news-sources").innerHTML = Object.entries(d.sources).map(([k, v]) =>
      `<div class="kv"><span class="kv-k">${esc(k.replace(/_/g, " "))}</span>
        <span class="kv-v ${v.startsWith("ok") ? "up" : "down"}">${esc(v)}</span></div>`).join("")
      + `<div class="kv"><span class="kv-k">Headlines tracked</span><span class="kv-v">${d.items_tracked}</span></div>`
      + `<div class="split-note">Only Binance's own announcements can hard-block a symbol.
         A delisting is the largest predictable move a token makes, and Binance publishes it
         first. Everything else can widen a blackout or cut size — nothing can open a position.</div>`;

    const cat = { delisting: "tag-critical", security: "tag-deny", halt: "tag-warn",
                  macro: "tag-confirm", general: "tag-info" };
    $("news-list").innerHTML = d.recent.length
      ? d.recent.map((i) => `<div class="feed-row">
          <span class="feed-time">${i.age_minutes < 60
            ? Math.round(i.age_minutes) + "m" : Math.round(i.age_minutes / 60) + "h"}</span>
          <span class="feed-tag ${cat[i.category] || "tag-info"}">${esc(i.category)}</span>
          <span class="feed-msg">
            ${i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener"
              style="color:inherit">${esc(i.title)}</a>` : esc(i.title)}
            <div class="faint" style="font-size:12px;margin-top:2px">
              ${esc(i.source.replace(/_/g, " "))}
              ${i.symbols.length ? " · " + i.symbols.join(", ") : ""}
              ${i.risk_score > 0 ? ` · risk ${i.risk_score}` : ""}
              ${i.matched.length ? " · " + i.matched.slice(0, 4).join(", ") : ""}
            </div></span>
        </div>`).join("")
      : `<div class="empty">No headlines retrieved.</div>`;

    const b = $("badge-news");
    b.hidden = r.level === "normal";
    b.textContent = r.level === "high" ? "!" : "•";
  } catch (e) {
    $("news-banner").innerHTML = `<div class="banner warn">${esc(e.message)}</div>`;
  }
}

$("news-refresh").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  b.textContent = "Refreshing…";
  try {
    await loadNews(true);  // bypass the 5-minute cache
    toast("Headlines refreshed.", "good");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    b.disabled = false;
    b.textContent = "Refresh now";
  }
});

async function loadMacro() {
  try {
    const d = await api("/api/macro");
    const p = d.posture, r = p.rates || {};

    // Imminent-event alerts, at the top where they can't be missed.
    const alerts = d.alerts || [];
    const alertHtml = alerts.length
      ? alerts.map((a) => `<div class="banner ${a.severity === "critical" ? "crit" : "warn"}"
          style="margin-bottom:10px">
          <strong>${esc(a.headline)}</strong> — ${esc(a.agent_action)}.
          <span class="dim">${esc(a.note || "")}</span></div>`).join("")
      : "";
    const f = p.next_fomc;
    const regimeClass = p.regime === "stressed" ? "down"
      : p.regime === "tightening" ? "down" : p.regime === "easing" ? "up" : "dim";

    $("macro-body").innerHTML = alertHtml + [
      ["Regime", `<span class="${regimeClass}">${esc(p.regime)}</span>`],
      ["Position sizing", `${(p.size_multiplier * 100).toFixed(0)}% of normal`],
      ["Trading paused", p.blackout ? `<span class="down">yes</span>` : "no"],
      ["US 2-year", r.two_year != null
        ? `${r.two_year.toFixed(2)}% <span class="dim">(${r.two_year_change_1w_bps >= 0 ? "+" : ""}${r.two_year_change_1w_bps ?? 0}bp / 1w)</span>` : "—"],
      ["US 10-year", r.ten_year != null
        ? `${r.ten_year.toFixed(2)}% <span class="dim">(${r.ten_year_change_1w_bps >= 0 ? "+" : ""}${r.ten_year_change_1w_bps ?? 0}bp / 1w)</span>` : "—"],
      ["2s10s curve", r.curve_2s10s_bps != null
        ? `<span class="${r.curve_2s10s_bps < 0 ? "down" : ""}">${r.curve_2s10s_bps}bp</span>` : "—"],
      ["Next FOMC decision", f
        ? `${new Date(f.date).toISOString().slice(0, 10)} <span class="dim">(${f.days_away} days)</span>` : "—"],
      ["Rates as of", esc(r.as_of || "—")],
    ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("")
      + (() => {
          const x = p.expectation || {};
          if (x.implied_change_bps == null) return "";
          const dirClass = x.direction === "hikes" ? "down" : x.direction === "cuts" ? "up" : "dim";
          return `<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
            <div class="kv"><span class="kv-k">Market is pricing</span>
              <span class="kv-v"><span class="${dirClass}">${esc(x.direction)}</span>
              — ${Math.abs(x.implied_25bp_moves).toFixed(1)} × 25bp over ${esc(x.horizon)}
              <span class="dim">(${esc(x.confidence)} conviction)</span></span></div>
            <div class="split-note">${esc(x.basis)}</div>
            <div class="split-note"><strong>${esc(x.method)}</strong></div>
          </div>`;
        })()
      + (() => {
          const c = r.curve || {}, ch = r.curve_changes_1w_bps || {};
          const tenors = Object.keys(c);
          if (!tenors.length) return "";
          return `<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
            <div class="kv-k" style="margin-bottom:6px">Full US Treasury curve</div>
            <table><thead><tr><th>Tenor</th><th class="ta-r">Yield</th>
              <th class="ta-r">1w change</th></tr></thead><tbody>`
            + tenors.map((t) => {
                const dv = ch[t];
                return `<tr><td class="num"><strong>${esc(t)}</strong></td>
                  <td class="ta-r num">${c[t].toFixed(2)}%</td>
                  <td class="ta-r num ${dv > 0 ? "down" : dv < 0 ? "up" : "dim"}">${
                    dv == null ? "—" : (dv >= 0 ? "+" : "") + dv + "bp"}</td></tr>`;
              }).join("") + `</tbody></table></div>`;
        })()
      + (() => {
          const cal = p.calendar || [];
          if (!cal.length) return "";
          return `<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
            <div class="kv-k" style="margin-bottom:6px">Scheduled events ahead</div>
            <table><thead><tr><th>Date</th><th>Event</th>
              <th>Date confidence</th><th class="ta-r">In</th>
              <th>What the agent will do</th></tr></thead><tbody>`
            + cal.map((c) => `<tr>
                <td class="num">${esc(c.date.slice(0, 10))}</td>
                <td><strong>${esc(c.event)}</strong>
                  <div class="faint" style="font-size:11.5px">${esc(c.source)}</div></td>
                <td><span class="pill ${c.confidence === "confirmed" ? "ok" : ""}"
                  style="font-size:11px" title="${esc(c.note || "")}">${esc(c.confidence || "")}</span></td>
                <td class="ta-r num">${c.days_away}d</td>
                <td class="dim">${esc(c.agent_action)}</td>
              </tr>`).join("") + `</tbody></table>
            <div class="split-note">Dates marked <strong>estimated</strong> are computed from
            the BLS scheduling convention because the agency blocks automated access to its
            calendar. They are usually right but can shift around federal holidays — verify
            before relying on one. FOMC dates are read from the Fed's published calendar.</div>
            </div>`;
        })()
      + p.reasons.map((x) => `<div class="split-note">${esc(x)}</div>`).join("")
      + `<div class="split-note"><strong>${esc(p.policy)}</strong></div>`;
  } catch (e) {
    $("macro-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

/* ---------------- calibration ---------------- */

function renderCalibration(s) {
  const c = s.calibration;
  if (!c) return;
  const rows = c.analysts || [];
  $("calibration").innerHTML = `<table><thead><tr>
    <th>Analyst</th><th class="ta-r">Graded</th><th class="ta-r">Hit rate</th>
    <th class="ta-r">Brier</th><th class="ta-r">Edge</th>
    <th class="ta-r">Vote weight</th><th>Standing</th>
  </tr></thead><tbody>` + rows.map((r) => `<tr>
    <td><strong>${esc(r.analyst)}</strong></td>
    <td class="ta-r num dim">${r.samples}</td>
    <td class="ta-r num">${r.hit_rate == null ? "—" : (r.hit_rate * 100).toFixed(0) + "%"}</td>
    <td class="ta-r num ${r.brier == null ? "dim" : r.brier < 0.25 ? "up" : "down"}">${r.brier == null ? "—" : r.brier.toFixed(3)}</td>
    <td class="ta-r num ${r.edge_bps == null ? "dim" : cls(r.edge_bps)}">${r.edge_bps == null ? "—" : r.edge_bps.toFixed(1) + "bp"}</td>
    <td class="ta-r num ${r.benched ? "down" : r.reliability > 1.05 ? "up" : r.reliability < 0.95 ? "down" : ""}">${r.reliability.toFixed(2)}×</td>
    <td style="font-size:13px">${r.benched ? `<span class="pill bad">${esc(r.status)}</span>` : `<span class="dim">${esc(r.status)}</span>`}</td>
  </tr>`).join("") + "</tbody></table>";

  $("calib-explainer").innerHTML = `
    <div class="kv"><span class="kv-k">Grading horizon</span><span class="kv-v">${c.horizon_minutes} min</span></div>
    <div class="kv"><span class="kv-k">Calls awaiting grade</span><span class="kv-v">${c.open_calls}</span></div>
    <div class="kv"><span class="kv-k">Calls graded so far</span><span class="kv-v">${c.graded_calls}</span></div>
    <div class="kv"><span class="kv-k">Deadband</span><span class="kv-v">${c.deadband_bps} bp</span></div>
    <div class="split-note">
      Each directional call is recorded with the price at the time, then graded
      ${c.horizon_minutes} minutes later against what the market actually did. A move
      smaller than ${c.deadband_bps} basis points counts as no call rather than a win,
      because a coin flip on a flat tape otherwise scores 50% and looks like skill.
      <br><br>
      <strong>Brier score</strong> is the one that matters. It measures whether the stated
      confidence was honest, not just whether the direction was right. An analyst that says
      "bullish, 90% sure" and is right 55% of the time is worse than one that says
      "bullish, 55% sure" and is right 55% of the time — the first is lying to the position
      sizer. Below 0.25 beats an uninformed guess.
      <br><br>
      <strong>Vote weight</strong> multiplies that analyst's influence on the Council. It only
      moves once an analyst has ${c.min_samples_for_weight} graded calls, so one lucky
      prediction cannot buy influence. Calls made while a feed was down are never graded —
      an analyst is not penalised for abstaining honestly.
    </div>`;
}

/* ---------------- anchors ---------------- */

async function loadAnchor() {
  try {
    const a = await api("/api/anchor");
    const w = a.latest_witness || {};
    $("anchor-out").innerHTML = a.count === 0
      ? `<div class="empty"><strong>No anchors yet</strong>The first checkpoint is written shortly after the engine starts.</div>`
      : `<div class="kv"><span class="kv-k">Checkpoints</span><span class="kv-v">${a.count}</span></div>
         <div class="kv"><span class="kv-k">Ledger head anchored</span><span class="kv-v hash">${short(a.latest_head, 16)}</span></div>
         <div class="kv"><span class="kv-k">Witness</span><span class="kv-v">${esc(w.source || "—")}</span></div>
         ${w.btc_price ? `<div class="kv"><span class="kv-k">BTC at that moment</span><span class="kv-v">$${Number(w.btc_price).toLocaleString()}</span></div>` : ""}
         <div class="split-note">Each checkpoint binds the ledger head to Binance's own server
         clock and the market price at that instant. Rewriting history now means also producing
         a chain consistent with prices Binance publicly recorded — which is checkable by anyone,
         afterwards, without trusting this machine.</div>`;
  } catch (e) { $("anchor-out").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

$("anchor-now").addEventListener("click", async () => {
  try { await api("/api/anchor/create", "POST"); await loadAnchor(); toast("Ledger anchored.", "good"); }
  catch (e) { toast(e.message, "bad"); }
});

$("anchor-verify").addEventListener("click", async () => {
  try {
    const v = await api("/api/anchor/verify");
    toast(v.valid ? `All ${v.checkpoints} anchors verified.` : "Anchor verification failed.",
      v.valid ? "good" : "bad");
    if (!v.valid) $("anchor-out").innerHTML += `<pre class="code">${esc(JSON.stringify(v.problems, null, 2))}</pre>`;
  } catch (e) { toast(e.message, "bad"); }
});

/* ---------------- replay ---------------- */

$("rp-run").addEventListener("click", async () => {
  const btn = $("rp-run");
  btn.disabled = true;
  $("rp-status").textContent = "Downloading real candles from Binance…";
  $("replay-out").innerHTML = "";
  try {
    const d = await api("/api/replay", "POST", {
      interval: $("rp-interval").value,
      bars: parseInt($("rp-bars").value, 10),
    });
    const from = new Date(d.start_ts * 1000).toISOString().slice(0, 10);
    const to = new Date(d.end_ts * 1000).toISOString().slice(0, 10);
    $("rp-status").textContent = "";
    $("replay-out").innerHTML = `
      <div class="grid g-2-1" style="margin-bottom:14px">
        <div class="panel">
          <div class="panel-head"><span class="panel-title">${from} → ${to}</span>
            <span class="panel-note">${d.bars} × ${d.interval} bars on ${d.symbols.join(", ")}</span></div>
          <div class="panel-body">
            <svg viewBox="0 0 600 150" style="width:100%;height:150px">${sparkPath(d.equity_curve, 600, 150)}</svg>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="panel-title">Result</span></div>
          <div class="panel-body">
            ${[["Net P&L", pct(d.pnl_pct), cls(d.pnl_pct)],
               ["Buy & hold", pct(d.buy_hold_pct), cls(d.buy_hold_pct)],
               ["Difference", pct(d.excess_pct), cls(d.excess_pct)],
               ["Max drawdown", d.max_drawdown_pct.toFixed(2) + "%", ""],
               ["Trades", d.trades, ""],
               ["Win rate", d.win_rate_pct.toFixed(1) + "%", ""],
               ["Sharpe", d.sharpe.toFixed(2), ""],
               ["Intents / denied", `${d.intents} / ${d.denied}`, ""],
              ].map(([k, v, c]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v ${c}">${v}</span></div>`).join("")}
          </div>
        </div>
      </div>
      <div class="panel"><div class="panel-head"><span class="panel-title">How to read this</span></div>
        <div class="panel-body">
          <div class="ctl-row" style="margin-bottom:8px">
            ${d.analysts_active.map((a) => `<span class="pill ok" style="font-size:11.5px">${esc(a)} active</span>`).join("")}
            ${d.analysts_abstained.map((a) => `<span class="pill bad" style="font-size:11.5px">${esc(a)} abstained</span>`).join("")}
          </div>
          ${d.notes.map((n) => `<div class="split-note" style="margin-top:4px">${esc(n)}</div>`).join("")}
        </div></div>`;
  } catch (e) {
    $("rp-status").textContent = "";
    $("replay-out").innerHTML = `<div class="panel"><div class="panel-body"><div class="empty">
      <strong>Backtest could not run</strong>${esc(e.message)}</div></div></div>`;
  } finally { btn.disabled = false; }
});

/* ---------------- manual trading ---------------- */

let mtLastOpinion = null;

function renderOpinion(o, extra = "") {
  const tone = o.verdict === "strongly_against" ? "crit"
    : o.verdict === "caution" ? "warn"
    : o.verdict === "aligned" ? "" : "warn";
  const bar = `<div style="height:6px;border-radius:3px;background:var(--surface-2);overflow:hidden;margin:8px 0">
      <div style="height:100%;width:${Math.min(o.disagreement_pct, 100)}%;
        background:${o.disagreement_pct >= 60 ? "var(--down)"
          : o.disagreement_pct >= 30 ? "var(--bnb-yellow)" : "var(--up)"}"></div></div>`;
  return `<div class="banner ${tone}"><strong>${esc(o.headline)}</strong></div>
    ${bar}
    <div class="dim" style="font-size:12.5px;margin-bottom:8px">
      Disagreement score: <strong>${o.disagreement_pct}%</strong> — how much of the
      system's own analysis leans against this trade.</div>
    ${o.concerns.map((c) => `<div class="split-note">
      <span class="pill ${c.severity >= 0.7 ? "bad" : ""}" style="font-size:11px">${esc(c.source)}</span>
      ${esc(c.message)}</div>`).join("")}
    ${o.confirmations.map((c) => `<div class="split-note up">${esc(c)}</div>`).join("")}
    <div class="split-note"><strong>${esc(o.policy)}</strong></div>${extra}`;
}

/* Make the Sell option context-aware.
 *
 * GlassBox is spot-only: a Sell is only ever an exit of something already
 * held, never a fresh short. So when the typed symbol isn't in the book,
 * Sell is disabled with a plain-language reason and the choice snaps back to
 * Buy — rather than letting someone pick Sell, place it, and only then learn
 * it couldn't have worked. When the symbol *is* held, Sell is enabled and the
 * hint says how much is available to exit. */
let _heldPositions = {};

function updateSellAvailability() {
  const sym = ($("mt-symbol")?.value || "").trim().toUpperCase();
  const sideSel = $("mt-side");
  const hint = $("mt-side-hint");
  const sellOpt = sideSel?.querySelector('option[value="SELL"]');
  if (!sideSel || !sellOpt || !hint) return;

  const held = _heldPositions[sym];
  const isHeld = held && held.notional_usd > 0;
  sellOpt.disabled = !isHeld;

  if (sideSel.value === "BUY") {
    // Buy selected: only note that spot buys are always available.
    hint.textContent = "";
    hint.className = "dim";
  } else if (!isHeld) {
    if (sideSel.value === "SELL") sideSel.value = "BUY";  // don't leave an impossible choice selected
    hint.textContent = sym ? `Sell is an exit — you hold no ${sym} to sell` : "";
    hint.className = "dim";
  } else {
    hint.textContent = `Exits your ${usd(held.notional_usd, 0)} ${sym} position`;
    hint.className = "dim up";
  }
}

/* Searchable combobox for the trade symbol.
 *
 * A native <datalist> only filters as you type and won't show the whole list
 * on a click into a full box — which is exactly the behaviour that felt
 * broken. This is a real combobox: clicking the box opens the full scrollable
 * list, typing filters it in place, held positions are marked, and both the
 * list and the search live in the same box. */
function setupSymbolCombo() {
  const input = $("mt-symbol");
  const list = $("mt-symbol-list");
  if (!input || !list) return;
  let active = -1;

  function optionsFor(query) {
    const all = window._mtSymbols || [];
    const q = query.trim().toUpperCase();
    const held = _heldPositions || {};
    const matched = q
      ? all.filter((o) => o.symbol.includes(q) || o.base.includes(q))
      : all;
    // Held positions float to the top so exiting is quick to find.
    return [...matched].sort((a, b) => {
      const ah = held[a.symbol] ? 1 : 0, bh = held[b.symbol] ? 1 : 0;
      if (ah !== bh) return bh - ah;
      return (b.vol || 0) - (a.vol || 0);
    }).slice(0, 200);
  }

  function render(query) {
    const opts = optionsFor(query);
    if (!opts.length) {
      list.innerHTML = `<div class="combo-opt dim">No matching pair</div>`;
    } else {
      const held = _heldPositions || {};
      list.innerHTML = opts.map((o, idx) => `
        <div class="combo-opt ${held[o.symbol] ? "held" : ""} ${idx === active ? "active" : ""}"
          data-sym="${esc(o.symbol)}">
          <span><strong>${esc(o.base)}</strong>${held[o.symbol] ? ' <span class="up" style="font-size:11px">· held</span>' : ""}</span>
          <span class="combo-sub">${o.vol ? compact(o.vol) + " vol" : ""}</span>
        </div>`).join("");
      list.querySelectorAll(".combo-opt[data-sym]").forEach((el) =>
        el.addEventListener("mousedown", (e) => {   // mousedown fires before blur
          e.preventDefault();
          input.value = el.dataset.sym;
          list.hidden = true;
          updateSellAvailability();
        }));
    }
    list.hidden = false;
  }

  input.addEventListener("focus", () => { active = -1; render(input.value === "BTCUSDT" ? "" : input.value); });
  input.addEventListener("click", () => { if (list.hidden) { active = -1; render(""); } });
  input.addEventListener("input", () => { active = -1; render(input.value); updateSellAvailability(); });
  input.addEventListener("keydown", (e) => {
    const opts = list.querySelectorAll(".combo-opt[data-sym]");
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, opts.length - 1); render(input.value); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(input.value); }
    else if (e.key === "Enter" && active >= 0 && opts[active]) { e.preventDefault(); input.value = opts[active].dataset.sym; list.hidden = true; updateSellAvailability(); }
    else if (e.key === "Escape") { list.hidden = true; }
  });
  // Close when focus leaves the box.
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
}

async function mtCheck() {
  const body = {
    symbol: $("mt-symbol").value,
    side: $("mt-side").value,
    notional_usd: parseFloat($("mt-amount").value),
  };
  const o = await api("/api/second-opinion", "POST", body);
  mtLastOpinion = o;
  $("mt-opinion").innerHTML = renderOpinion(o);
  return o;
}

setupSymbolCombo();
$("mt-side").addEventListener("change", updateSellAvailability);

$("mt-check").addEventListener("click", async () => {
  $("mt-status").textContent = "Checking…";
  try { await mtCheck(); $("mt-status").textContent = ""; }
  catch (e) { $("mt-status").textContent = ""; toast(e.message, "bad"); }
});

async function mtPlace(acknowledged) {
  const body = {
    symbol: $("mt-symbol").value,
    side: $("mt-side").value,
    notional_usd: parseFloat($("mt-amount").value),
    acknowledged_risk: !!acknowledged,
  };
  const r = await api("/api/manual-trade", "POST", body);

  if (r.needs_acknowledgement) {
    // The system objects. Show why, and make proceeding a separate,
    // deliberate second click rather than something you can do by accident.
    $("mt-opinion").innerHTML = renderOpinion(r.second_opinion, `
      <div class="ctl-row" style="margin-top:10px">
        <button class="btn btn-sm btn-danger" id="mt-override">
          I understand — place it anyway at my own risk</button>
        <button class="btn btn-sm" id="mt-cancel">Cancel</button>
      </div>`);
    $("mt-override").addEventListener("click", () => mtPlace(true));
    $("mt-cancel").addEventListener("click", () => {
      $("mt-opinion").innerHTML = "";
      toast("Trade cancelled.", "good");
    });
    return;
  }

  if (!r.ok) { toast(r.error || "Trade was not placed.", "bad"); return; }

  const status = r.status || "submitted";
  toast(
    status === "filled" ? "Filled — see Open positions and the Ledger."
      : status === "denied" ? "The Constitution denied this trade — see the Ledger for which rule."
      : status === "pending" ? "Queued: this size needs your confirmation on the Cockpit."
      : `Trade ${status}.`,
    status === "filled" ? "good" : "bad",
  );
  if (r.second_opinion) $("mt-opinion").innerHTML = renderOpinion(r.second_opinion);
}

$("mt-place").addEventListener("click", async () => {
  const b = $("mt-place");
  b.disabled = true; $("mt-status").textContent = "Placing…";
  try { await mtPlace(false); }
  catch (e) { toast(e.message, "bad"); }
  finally { b.disabled = false; $("mt-status").textContent = ""; }
});

/* ---------------- futures (spot/futures toggle + long/short) ---------------- */

let MT_MARKET = "spot";

function setMarket(market) {
  MT_MARKET = market;
  const isFut = market === "futures";
  document.querySelectorAll("#mt-market .seg-btn").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.market === market));
  // Show/hide the two control sets. Spot controls use display:contents so they
  // sit inline in the flex row; hidden is plain none.
  $("mt-spot-controls").style.display = isFut ? "none" : "contents";
  $("mt-spot-actions").style.display = isFut ? "none" : "contents";
  $("mt-futures-controls").style.display = isFut ? "inline-flex" : "none";
  $("mt-futures-actions").style.display = isFut ? "inline-flex" : "none";
  $("mt-amount-label").textContent = isFut ? "Margin $" : "Amount $";
  $("mt-futures-note").style.display = isFut ? "block" : "none";
  $("mt-opinion").innerHTML = "";
  updateFuturesNote();
  if (!isFut) updateSellAvailability();
}

function updateFuturesNote() {
  if (MT_MARKET !== "futures") return;
  const margin = parseFloat($("mt-amount").value) || 0;
  const lev = parseFloat($("mt-leverage").value) || 1;
  const notional = margin * lev;
  const liqMove = (100 / lev).toFixed(1);
  $("mt-futures-note").innerHTML =
    `Position notional <strong>${usd(notional, 0)}</strong> = ${usd(margin, 0)} margin × ${lev}x. ` +
    `A default stop is attached; an adverse move of roughly <strong>${liqMove}%</strong> would liquidate an unstopped position. ` +
    `Long profits when price rises, short when it falls.`;
}

document.querySelectorAll("#mt-market .seg-btn").forEach((btn) =>
  btn.addEventListener("click", () => setMarket(btn.dataset.market)));
$("mt-leverage").addEventListener("change", updateFuturesNote);
$("mt-amount").addEventListener("input", () => { if (MT_MARKET === "futures") updateFuturesNote(); });

async function mtfCheck() {
  const body = {
    symbol: $("mt-symbol").value,
    action: "OPEN_LONG",          // direction doesn't change most of the read; the panel shows both
    margin_usd: parseFloat($("mt-amount").value),
    leverage: parseFloat($("mt-leverage").value),
  };
  const o = await api("/api/futures/second-opinion", "POST", body);
  $("mt-opinion").innerHTML = renderOpinion(o);
  return o;
}

async function mtfPlace(action, acknowledged) {
  const body = {
    symbol: $("mt-symbol").value,
    action,
    margin_usd: parseFloat($("mt-amount").value),
    leverage: parseFloat($("mt-leverage").value),
    acknowledged_risk: !!acknowledged,
  };
  const r = await api("/api/futures/trade", "POST", body);

  if (r.needs_acknowledgement) {
    $("mt-opinion").innerHTML = renderOpinion(r.second_opinion, `
      <div class="ctl-row" style="margin-top:10px">
        <button class="btn btn-sm ${action === "OPEN_SHORT" ? "btn-short" : "btn-long"}" id="mtf-override">
          I understand — open the ${action === "OPEN_SHORT" ? "short" : "long"} anyway at my own risk</button>
        <button class="btn btn-sm" id="mtf-cancel">Cancel</button>
      </div>`);
    $("mtf-override").addEventListener("click", () => mtfPlace(action, true));
    $("mtf-cancel").addEventListener("click", () => {
      $("mt-opinion").innerHTML = "";
      toast("Futures order cancelled.", "good");
    });
    return;
  }

  if (!r.ok) { toast(r.error || r.note || "Futures order was not placed.", "bad"); return; }

  const status = r.status || "submitted";
  toast(
    status === "filled" ? "Filled — see Futures positions and the Ledger."
      : status === "denied" ? "The Constitution denied this — see the Ledger for which rule."
      : status === "pending" ? "Queued: this size needs your confirmation on the Cockpit."
      : `Futures order ${status}.`,
    status === "filled" ? "good" : "bad",
  );
  if (r.second_opinion) $("mt-opinion").innerHTML = renderOpinion(r.second_opinion);
}

$("mtf-check").addEventListener("click", async () => {
  $("mt-status").textContent = "Checking…";
  try { await mtfCheck(); $("mt-status").textContent = ""; }
  catch (e) { $("mt-status").textContent = ""; toast(e.message, "bad"); }
});
["mtf-long", "mtf-short"].forEach((id) =>
  $(id).addEventListener("click", async () => {
    const action = id === "mtf-long" ? "OPEN_LONG" : "OPEN_SHORT";
    const b = $(id); b.disabled = true; $("mt-status").textContent = "Placing…";
    try { await mtfPlace(action, false); }
    catch (e) { toast(e.message, "bad"); }
    finally { b.disabled = false; $("mt-status").textContent = ""; }
  }));

function renderFutures(s) {
  const fut = (s.portfolio && s.portfolio.futures) || null;
  const panel = $("futures-panel");
  const host = $("futures-positions");
  if (!panel || !host) return;
  const positions = fut ? Object.values(fut.positions || {}) : [];

  // Show the panel whenever there are futures positions, or the user is in
  // futures mode (so they can see the empty state while trading).
  const show = positions.length > 0 || MT_MARKET === "futures";
  panel.style.display = show ? "" : "none";
  if (!show) return;

  if (!positions.length) {
    host.innerHTML = `<div class="empty"><strong>No open futures positions</strong>
      Use <span class="up">Open Long</span> or <span class="down">Open Short</span> above. Each posts
      margin (size ÷ leverage) from your cash and can be liquidated if it moves far enough against you.</div>`;
    return;
  }

  const totalU = positions.reduce((a, p) => a + (p.unrealised_pnl_usd || 0), 0);
  host.innerHTML = `<table><thead><tr>
    <th>Asset</th><th>Side</th><th class="ta-r">Notional</th><th class="ta-r">Lev</th>
    <th class="ta-r">Entry</th><th class="ta-r">Mark</th><th class="ta-r">Liq. price</th>
    <th class="ta-r">Margin</th><th class="ta-r">uPnL (ROE)</th><th class="ta-r">Close</th>
  </tr></thead><tbody>` + positions.map((p) => `<tr>
    <td><strong>${esc(p.symbol)}</strong></td>
    <td><span class="pill ${p.side === "LONG" ? "long" : "short"}">${p.side === "LONG" ? "Long" : "Short"}</span></td>
    <td class="ta-r num">${usd(p.notional_usd, 0)}</td>
    <td class="ta-r num dim">${p.leverage}x</td>
    <td class="ta-r num dim">${Number(p.entry_price).toLocaleString()}</td>
    <td class="ta-r num">${Number(p.mark_price).toLocaleString()}</td>
    <td class="ta-r num" style="color:var(--down)">${Number(p.liq_price).toLocaleString()}</td>
    <td class="ta-r num dim">${usd(p.margin_usd, 0)}</td>
    <td class="ta-r num ${cls(p.unrealised_pnl_usd)}">${usd(p.unrealised_pnl_usd)}<br>
      <span style="font-size:12px">${pct(p.roe_pct)}</span></td>
    <td class="ta-r"><button class="btn btn-sm btn-danger close-fut" data-symbol="${esc(p.symbol)}">Close</button></td>
  </tr>`).join("")
    + `<tr style="border-top:2px solid var(--border)">
        <td colspan="8" class="dim"><strong>Total uPnL across ${positions.length} futures position${positions.length === 1 ? "" : "s"}</strong>
          · funding paid ${usd(fut.funding_paid_usd || 0)} · realised ${usd(fut.realised_pnl_usd || 0)}</td>
        <td class="ta-r num ${cls(totalU)}"><strong>${usd(totalU)}</strong></td>
        <td></td>
      </tr>`
    + "</tbody></table>";

  host.querySelectorAll(".close-fut").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const sym = btn.dataset.symbol;
      btn.disabled = true; btn.textContent = "Closing…";
      try {
        const r = await api("/api/futures/close", "POST", { symbol: sym });
        toast(r.ok ? `Closed ${sym} futures.` : (r.error || "Close failed."), r.ok ? "good" : "bad");
      } catch (e) { toast(e.message, "bad"); }
      finally { btn.disabled = false; btn.textContent = "Close"; }
    }));
}

/* ---------------- autonomy toggle ---------------- */

function renderAutonomy(s) {
  const on = !!s.autonomous;
  const pill = $("autonomy-state");
  const btn = $("autonomy-toggle");
  if (!pill || !btn) return;
  pill.textContent = on ? "ON — trading by itself" : "OFF — manual only";
  pill.className = `pill ${on ? "bad" : "ok"}`;  // "on" is the riskier state, flagged
  btn.textContent = on ? "Disable autonomous trading" : "Enable autonomous trading";
  btn.classList.toggle("btn-danger", on);
  btn.classList.toggle("btn-primary", !on);
}

$("autonomy-toggle").addEventListener("click", async () => {
  const turningOn = $("autonomy-toggle").textContent.startsWith("Enable");
  try {
    let r = await api("/api/engine/autonomy", "POST", { enabled: turningOn });
    if (r.needs_acknowledgement) {
      if (!confirm(r.message)) return;
      r = await api("/api/engine/autonomy", "POST", { enabled: true, acknowledged: true });
    }
    if (r.ok) toast(r.autonomous ? "Autonomous trading enabled." : "Autonomous trading disabled.", "good");
  } catch (e) { toast(e.message, "bad"); }
});

/* ---------------- control center ---------------- */

const MODE_INFO = {
  paper: { title: "Paper", desc: "Simulated fills. Nothing reaches Binance." },
  shadow: { title: "Shadow", desc: "Real Binance data, simulated fills." },
  mock: { title: "Mock", desc: "A real MCP client against a local stand-in for Binance. No money at risk." },
  live: { title: "Live", desc: "Real orders in your Agentic sub-account on Binance." },
  bridge: { title: "Bridge", desc: "Signed instructions emitted for you to run yourself." },
};

let ccSelectedMode = null;
let ccAuthorizePoll = null;

async function loadControlCenter() {
  try {
    const st = await api("/api/status");
    ccSelectedMode = st.mode;
    renderModeGrid(st.mode);
    $("cc-live-data").checked = !!st.live_market_data;
    $("cc-live-data").disabled = ["mock", "live"].includes(st.mode);
    updateLiveDataNote(st.mode);
    updateLiveConfirmVisibility();
    renderCCBanner(st.mode);

    const mcp = st.mcp || {};
    const showLiveControls = st.mode === "live";
    $("cc-clientid-panel").hidden = !showLiveControls;
    if (showLiveControls) await renderClientIdPanel();

    const clientIdInfo = await api("/api/mcp/client-id").catch(() => ({ configured: false }));
    $("cc-authorize-panel").hidden = !showLiveControls || !clientIdInfo.configured;
    if (showLiveControls && clientIdInfo.configured && !mcp.connected) {
      await renderAuthorizePanel();
    }
  } catch (e) { toast(e.message, "bad"); }
}

async function renderClientIdPanel(bodyId = "cc-clientid-body") {
  const host = $(bodyId);
  const ids = {
    url: bodyId + "-hosted-url", gen: bodyId + "-generate", genOut: bodyId + "-generated",
    save: bodyId + "-save", clear: bodyId + "-clear", status: bodyId + "-status",
    copy: bodyId + "-copy",
  };
  let info;
  try {
    info = await api("/api/mcp/client-id");
  } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }

  if (info.env_locked) {
    host.innerHTML = `
      <div class="kv"><span class="kv-k">Status</span>
        <span class="kv-v up">set via GLASSBOX_MCP_CLIENT_ID</span></div>
      <div class="split-note">${esc(info.note)}</div>`;
    return;
  }

  host.innerHTML = `
    <div class="split-note" style="margin-bottom:10px">
      Binance's Agent OS requires the OAuth client id to be a URL pointing at a
      small hosted JSON document, rather than a plain registered string. This
      is a one-time setup per machine — <strong>free, no shell required</strong>
      if you host it on GitHub Pages (see
      <a href="https://github.com/" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">docs/GITHUB_UPLOAD.md</a>).
    </div>
    <div class="ctl-row" style="margin-bottom:8px">
      <input class="ctl" id="${ids.url}" type="text" style="flex:1;min-width:280px"
        placeholder="https://yourname.github.io/glassbox/client-metadata.json"
        value="${esc(info.value || "")}">
      <button class="btn btn-sm" id="${ids.gen}">Generate the JSON to host</button>
    </div>
    <div id="${ids.genOut}" style="margin-bottom:8px"></div>
    <div class="ctl-row">
      <button class="btn btn-primary btn-sm" id="${ids.save}">Save Client ID</button>
      <button class="btn btn-sm" id="${ids.clear}">Clear</button>
      <span class="dim" id="${ids.status}">
        ${info.configured ? `<span class="up">configured</span>` : `<span class="down">not configured — Authorize with Binance is unavailable until this is set</span>`}
      </span>
    </div>`;

  $(ids.gen).addEventListener("click", async () => {
    const url = $(ids.url).value.trim();
    if (!url) { toast("Enter the URL you plan to host the document at first.", "bad"); return; }
    try {
      const r = await api("/api/mcp/client-id/generate", "POST", { hosted_at: url });
      const json = JSON.stringify(r.document, null, 2);
      $(ids.genOut).innerHTML = `
        <div class="split-note" style="margin-bottom:6px">${esc(r.instructions)}</div>
        <pre style="background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-md);
          padding:10px;font-size:12.5px;overflow-x:auto;white-space:pre-wrap">${esc(json)}</pre>
        <button class="btn btn-sm" id="${ids.copy}">Copy JSON</button>`;
      $(ids.copy).addEventListener("click", () => {
        navigator.clipboard?.writeText(json);
        toast("Copied. Host this exact file at the URL above, then click Save Client ID.", "good");
      });
    } catch (e) { toast(e.message, "bad"); }
  });

  $(ids.save).addEventListener("click", async () => {
    const url = $(ids.url).value.trim();
    try {
      await api("/api/mcp/client-id", "POST", { url });
      toast(url ? "Client ID saved." : "Client ID cleared.", "good");
      if (bodyId.startsWith("cc-")) await loadControlCenter();
      else await loadMCP();
    } catch (e) { toast(e.message, "bad"); }
  });

  $(ids.clear).addEventListener("click", async () => {
    $(ids.url).value = "";
    try {
      await api("/api/mcp/client-id", "POST", { url: "" });
      toast("Client ID cleared.", "good");
      if (bodyId.startsWith("cc-")) await loadControlCenter();
      else await loadMCP();
    } catch (e) { toast(e.message, "bad"); }
  });
}

function renderCCBanner(mode) {
  const info = MODE_INFO[mode] || {};
  $("cc-live-banner").innerHTML = `<div class="banner ${mode === "live" ? "crit" : "warn"}">
    <strong>Currently running in ${esc(info.title || mode)} mode.</strong> ${esc(info.desc || "")}</div>`;
}

function renderModeGrid(currentMode) {
  $("mode-grid").innerHTML = Object.entries(MODE_INFO).map(([key, v]) => `
    <div class="mode-card ${key === (ccSelectedMode || currentMode) ? "selected" : ""}" data-mode="${key}">
      ${key === currentMode ? `<span class="mode-card-current">current</span><br>` : ""}
      <div class="mode-card-title">${esc(v.title)}</div>
      <div class="mode-card-desc">${esc(v.desc)}</div>
    </div>`).join("");

  $("mode-grid").querySelectorAll("[data-mode]").forEach((card) =>
    card.addEventListener("click", async () => {
      ccSelectedMode = card.dataset.mode;
      $("mode-grid").querySelectorAll(".mode-card").forEach((c) =>
        c.classList.toggle("selected", c.dataset.mode === ccSelectedMode));
      const forced = ["mock", "live"].includes(ccSelectedMode);
      $("cc-live-data").checked = forced || $("cc-live-data").checked;
      $("cc-live-data").disabled = forced;
      updateLiveDataNote(ccSelectedMode);
      updateLiveConfirmVisibility();

      // Selecting Live should surface the client-id setup immediately —
      // waiting until after Apply would mean showing the "Authorize with
      // Binance" error this whole panel exists to prevent.
      const isLive = ccSelectedMode === "live";
      $("cc-clientid-panel").hidden = !isLive;
      if (isLive) await renderClientIdPanel();
      if (!isLive) $("cc-authorize-panel").hidden = true;
    }));
}

function updateLiveDataNote(mode) {
  $("cc-live-data-note").textContent = ["mock", "live"].includes(mode)
    ? "Required for this mode — a real order needs a real price."
    : "Optional. Off uses the seeded simulator instead of real Binance prices.";
}

function updateLiveConfirmVisibility() {
  $("cc-live-confirm").hidden = ccSelectedMode !== "live";
}

$("cc-live-data").addEventListener("change", () => {
  if (!["mock", "live"].includes(ccSelectedMode)) return;
  $("cc-live-data").checked = true; // cannot be turned off for these modes
});

$("cc-apply-mode").addEventListener("click", async () => {
  if (!ccSelectedMode) return;
  if (ccSelectedMode === "live" && !$("cc-confirm-live").checked) {
    toast("Tick the confirmation checkbox before going live.", "bad");
    return;
  }
  const btn = $("cc-apply-mode");
  btn.disabled = true;
  $("cc-mode-status").textContent = "Applying…";
  try {
    const r = await api("/api/mode", "POST", {
      mode: ccSelectedMode,
      live_data: $("cc-live-data").checked,
      confirm_live: ccSelectedMode === "live",
    });
    $("cc-mode-status").textContent = "";
    toast(`Mode switched to ${r.mode}.`, "good");
    await loadControlCenter();
  } catch (e) {
    $("cc-mode-status").textContent = "";
    toast(e.message, "bad");
  } finally { btn.disabled = false; }
});

/* -- authorize with Binance, no shell required -- */

async function renderAuthorizePanel(bodyId = "cc-authorize-body") {
  const host = $(bodyId);
  const btnId = bodyId + "-start-auth";
  host.innerHTML = `<button class="btn btn-primary btn-sm" id="${btnId}">Authorize with Binance</button>
    <div class="split-note" style="margin-top:8px">Opens Binance's real login in a new tab.
    PKCE-protected, loopback-only redirect — the authorization code never leaves this machine.</div>`;
  $(btnId).addEventListener("click", () => startAuthorize(bodyId));
}

async function startAuthorize(bodyId = "cc-authorize-body") {
  const host = $(bodyId);
  const cancelId = bodyId + "-cancel-auth";
  try {
    const r = await api("/api/mcp/authorize/start", "POST");

    // The backend already validates this URL's scheme (see security.py), but
    // it is re-checked here because this is the line that actually hands a
    // URL to the browser. A javascript: or data: URL reaching window.open()
    // is code execution in this page's origin, not navigation — and this is
    // the last point at which that can be stopped.
    let parsed;
    try {
      parsed = new URL(r.authorize_url);
    } catch {
      throw new Error("Binance returned an authorization URL that could not be parsed.");
    }
    if (parsed.protocol !== "https:") {
      throw new Error(
        `Refusing to open an authorization URL using "${parsed.protocol}" — only https is permitted.`
      );
    }

    host.innerHTML = `
      <div class="kv"><span class="kv-k">Status</span>
        <span class="kv-v"><span class="spinner"></span>waiting for you to authorize…</span></div>
      <div class="ctl-row" style="margin-top:8px">
        <a class="btn btn-primary btn-sm" href="${esc(r.authorize_url)}" target="_blank" rel="noopener">
          Open Binance login</a>
        <button class="btn btn-sm" id="${cancelId}">Cancel</button>
      </div>
      <div class="split-note" style="margin-top:8px">If the tab didn't open automatically, use the button above.</div>`;
    window.open(r.authorize_url, "_blank");
    $(cancelId).addEventListener("click", async () => {
      clearInterval(ccAuthorizePoll);
      await api("/api/mcp/authorize/cancel", "POST");
      renderAuthorizePanel(bodyId);
    });
    clearInterval(ccAuthorizePoll);
    ccAuthorizePoll = setInterval(() => pollAuthorize(bodyId), 1500);
  } catch (e) {
    host.innerHTML = `<div class="empty"><strong>Could not start authorization</strong>${esc(e.message)}</div>`;
  }
}

async function pollAuthorize(bodyId = "cc-authorize-body") {
  const panelId = bodyId.replace("-body", "-panel");
  try {
    const r = await api("/api/mcp/authorize/status");
    if (r.status === "connected") {
      clearInterval(ccAuthorizePoll);
      toast(`Connected to Binance — ${r.session?.tool_count ?? "?"} tools available.`, "good");
      $(bodyId).innerHTML = `<div class="empty"><strong>Connected</strong>
        Open the Binance tab to see your session and balances.</div>`;
      if ($(panelId)) $(panelId).hidden = true;
      if (typeof loadMCP === "function") loadMCP();
    } else if (r.status === "error" || r.status === "timeout") {
      clearInterval(ccAuthorizePoll);
      toast(r.detail || "Authorization did not complete.", "bad");
      renderAuthorizePanel(bodyId);
    }
    // "pending" — keep polling silently
  } catch (e) { /* transient network hiccup while polling; try again next tick */ }
}

/* -- run drills -- */

$("cc-run-drill").addEventListener("click", async () => {
  const btn = $("cc-run-drill");
  btn.disabled = true;
  $("cc-drill-status").innerHTML = `<span class="spinner"></span>Running in an isolated engine…`;
  $("cc-drill-out").innerHTML = "";
  try {
    const r = await api("/api/ops/drill", "POST");
    $("cc-drill-status").textContent = "";
    $("cc-drill-out").innerHTML = `
      <div style="margin-bottom:10px">
        <span class="pill ${r.all_passed ? "ok" : "bad"}">${r.passed} / ${r.total} passed</span>
      </div>` + r.results.map((d) => `<div class="drill-row">
        <span class="drill-mark ${d.passed ? "pass" : "fail"}">${d.passed ? "✓" : "✗"}</span>
        <span class="drill-name">${esc(d.drill)}</span>
        <span class="drill-detail">${esc(d.detail)}</span>
      </div>`).join("");
    toast(r.all_passed ? "All drills passed." : "Some drills failed — see details.",
      r.all_passed ? "good" : "bad");
  } catch (e) {
    $("cc-drill-status").textContent = "";
    $("cc-drill-out").innerHTML = `<div class="empty"><strong>Could not run drills</strong>${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* -- run scenario backtest -- */

$("cc-run-backtest").addEventListener("click", async () => {
  const btn = $("cc-run-backtest");
  btn.disabled = true;
  $("cc-bt-status").innerHTML = `<span class="spinner"></span>Running…`;
  $("cc-bt-out").innerHTML = "";
  try {
    const r = await api("/api/ops/backtest", "POST", {
      scenario: $("cc-bt-scenario").value,
      ticks: parseInt($("cc-bt-ticks").value, 10),
      equity: parseFloat($("cc-bt-equity").value),
    });
    $("cc-bt-status").textContent = "";
    const p = r.portfolio, b = r.benchmark;
    $("cc-bt-out").innerHTML = [
      ["Final equity", usd(p.equity_usd)],
      ["Net P&L", `${usd(p.total_pnl_usd)} (${pct(p.total_pnl_pct)})`],
      ["Buy & hold benchmark", pct(b.buy_and_hold_pnl_pct)],
      ["Difference", pct(b.excess_return_pct)],
      ["Guardian vetoes", r.guardian.vetoes],
      ["Guardian hedges", r.guardian.hedges],
      ["Decision receipts", r.receipts],
    ].map(([k, v]) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v">${v}</span></div>`).join("");
  } catch (e) {
    $("cc-bt-status").textContent = "";
    $("cc-bt-out").innerHTML = `<div class="empty"><strong>Could not run backtest</strong>${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* -- run calibration -- */

$("cc-run-calibrate").addEventListener("click", async () => {
  const btn = $("cc-run-calibrate");
  btn.disabled = true;
  $("cc-cal-status").innerHTML = `<span class="spinner"></span>Downloading real candles and grading…`;
  $("cc-cal-out").innerHTML = "";
  try {
    const r = await api("/api/ops/calibrate", "POST", {
      interval: $("cc-cal-interval").value,
      bars: parseInt($("cc-cal-bars").value, 10),
      horizon: parseInt($("cc-cal-horizon").value, 10),
      apply: true,
    });
    $("cc-cal-status").textContent = "";
    $("cc-cal-out").innerHTML = `
      <div class="dim" style="margin-bottom:8px">Graded ${r.graded_calls.toLocaleString()} calls across
        ${r.symbols_used.join(", ")}. Applied to the Track Record tab.</div>
      <table><thead><tr><th>Analyst</th><th class="ta-r">Calls</th>
        <th class="ta-r">Hit rate</th><th class="ta-r">Brier</th><th class="ta-r">Weight</th></tr></thead>
        <tbody>` + r.summary.analysts.filter((a) => a.samples).map((a) => `<tr>
          <td><strong>${esc(a.analyst)}</strong></td>
          <td class="ta-r num">${a.samples}</td>
          <td class="ta-r num">${(a.hit_rate * 100).toFixed(0)}%</td>
          <td class="ta-r num ${a.brier < 0.25 ? "up" : "down"}">${a.brier.toFixed(3)}</td>
          <td class="ta-r num">${a.reliability.toFixed(2)}×</td>
        </tr>`).join("") + "</tbody></table>";
    toast("Calibration applied — check the Track Record tab.", "good");
  } catch (e) {
    $("cc-cal-status").textContent = "";
    $("cc-cal-out").innerHTML = `<div class="empty"><strong>Could not calibrate</strong>${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* ---------------- render + socket ---------------- */

function render(s) {
  STATE = s;
  renderTop(s); renderCSM(s); renderSpark(s); renderSession(s);
  renderPositions(s); renderFutures(s); renderMarket(s); renderPending(s); renderAutonomy(s);
  renderActivity(s); renderReceipts(s);
  renderCouncil(s); renderSentinel(s); renderNarratives(s); renderYield(s);
  renderCalibration(s); renderHealth(s);
  if (window.retranslate) window.retranslate();
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  const dot = $("t-dot"), txt = $("t-conn-text");

  ws.onopen = () => { dot.className = "livedot on"; txt.textContent = "live"; };
  ws.onmessage = (e) => {
    const { kind, payload } = JSON.parse(e.data);
    if (kind === "tick") render(payload);
  };
  ws.onclose = () => {
    dot.className = "livedot off"; txt.textContent = "reconnecting";
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();

  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
}

/* ---------------- Analyst Desk: chat + analyze any pair ---------------- */

const deskHistory = [];

function deskFormat(text) {
  // Safe markdown-lite: escape first, then apply bold/italic and paragraphs.
  let t = esc(text);
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
  return t.split(/\n\n+/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

function deskAppend(role, html) {
  const log = $("desk-log");
  if (!log) return null;
  const wrap = document.createElement("div");
  wrap.className = "desk-msg " + (role === "user" ? "desk-user" : "desk-bot");
  wrap.innerHTML = `<div class="desk-bubble">${html}</div>`;
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function dirBadge(dir, conv) {
  // Below the acting threshold, present it as neutral so the chip matches the
  // desk's own language ("roughly neutral") instead of showing a hollow direction.
  if (conv != null && conv < 0.15) dir = "neutral";
  const cls = dir === "bullish" ? "badge-long" : dir === "bearish" ? "badge-short" : "badge-neutral";
  const label = dir === "bullish" ? "Bullish" : dir === "bearish" ? "Bearish" : "Neutral";
  const pct = conv != null ? ` ${Math.round(conv * 100)}%` : "";
  return `<span class="dbadge ${cls}">${label}${pct}</span>`;
}

function priceStr(p) {
  if (p == null) return "n/a";
  if (p >= 1000) return "$" + p.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (p >= 1) return "$" + p.toFixed(2);
  return "$" + p.toPrecision(4).replace(/\.?0+$/, "");
}

async function deskSend(text) {
  text = (text || "").trim();
  if (!text) return;
  deskAppend("user", esc(text));
  $("desk-input").value = "";
  const thinking = deskAppend("bot", `<span class="desk-typing">Analyzing live data…</span>`);
  try {
    const r = await api("/api/council/chat", "POST", {
      message: text,
      history: deskHistory.slice(-10),
      lang: (typeof currentLang !== "undefined" && currentLang !== "en") ? LANG_NAME[currentLang] : null,
    });
    let html = deskFormat(r.answer || "No answer.");
    // A compact verdict chip when the reply is about one pair.
    const b = r.briefing;
    if (b && b.council && b.symbol) {
      html = `<div class="desk-verdict">${b.symbol} ${dirBadge(b.council.direction, b.council.conviction)}` +
             (r.llm_provider ? ` <span class="dim" style="font-size:11px">· via ${esc(r.llm_provider)}</span>`
              : r.used_llm ? ` <span class="dim" style="font-size:11px">· narrated</span>` : ``) +
             `</div>` + html;
    }
    thinking.querySelector(".desk-bubble").innerHTML = html;
    $("desk-log").scrollTop = $("desk-log").scrollHeight;
    deskHistory.push({ role: "user", content: text });
    deskHistory.push({ role: "assistant", content: r.answer || "" });
    if (deskHistory.length > 12) deskHistory.splice(0, deskHistory.length - 12);
  } catch (e) {
    thinking.querySelector(".desk-bubble").innerHTML =
      `<span class="dim">Couldn't reach the desk (${esc(e.message || "error")}).</span>`;
  }
}

function renderBriefingCard(b) {
  if (!b || !b.ok) {
    return `<div class="empty"><strong>Couldn't analyze that pair</strong>
      <div class="dim" style="margin-top:6px">${esc((b && b.error) || "No data.")}</div></div>`;
  }
  const q = b.quote || {};
  const c = b.council || {};
  const chg = q.change_24h_pct;
  const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "";
  const analysts = (c.transcript || []).map((t) => {
    const sc = t.stance === "bullish" ? "up" : t.stance === "bearish" ? "down" : "dim";
    const conf = t.data_quality === "unavailable" ? "—" : Math.round(t.confidence * 100) + "%";
    const stance = t.data_quality === "unavailable" ? "abstained" : t.stance;
    return `<tr><td>${esc(t.agent)}</td><td class="${sc}">${esc(stance)}</td>
      <td class="mono" style="text-align:right">${conf}</td>
      <td class="dim">${esc(t.rationale)}</td></tr>`;
  }).join("");

  const d = b.derivatives || {};
  const derivBits = [];
  if (d.available) {
    if (d.funding_pct != null) derivBits.push(`funding ${d.funding_pct > 0 ? "+" : ""}${d.funding_pct}%`);
    if (d.long_short_ratio != null) derivBits.push(`L/S ${d.long_short_ratio}`);
  }
  const cp = b.constitution_preview;
  let cpLine = "";
  if (cp) {
    if (cp.decision === "DENY") cpLine = `Constitution would <strong>deny</strong> a base entry here.`;
    else if (cp.decision === "REQUIRE_HUMAN") cpLine = `A base entry (~$${Math.round(cp.allowed_usd).toLocaleString()}, ${cp.allowed_pct}% of equity) would need your confirmation.`;
    else cpLine = `Constitution would allow ~$${Math.round(cp.allowed_usd).toLocaleString()} (${cp.allowed_pct}% of equity) here.`;
  }
  const ind = b.indicators || {};
  const indBits = [];
  if (ind.rsi != null) indBits.push(`RSI ${ind.rsi}`);
  if (ind.adx != null) indBits.push(`ADX ${ind.adx}`);
  if (ind.atr_pct != null) indBits.push(`ATR ${ind.atr_pct}%`);

  return `
    <div class="an-card">
      <div class="an-head">
        <div><span class="an-sym">${esc(b.symbol)}</span> ${dirBadge(c.direction, c.conviction)}</div>
        <div class="an-price">${priceStr(q.price)} <span class="${chgCls}">${chg != null ? (chg > 0 ? "+" : "") + chg.toFixed(2) + "%" : ""}</span></div>
      </div>
      ${indBits.length ? `<div class="an-meta">${indBits.join(" · ")}${derivBits.length ? " · " + derivBits.join(" · ") : ""}</div>` : ""}
      ${analysts ? `<table class="an-table"><thead><tr><th>Analyst</th><th>Read</th><th style="text-align:right">Conf</th><th>Why</th></tr></thead><tbody>${analysts}</tbody></table>` : ""}
      ${cpLine ? `<div class="an-cp">${cpLine}</div>` : ""}
      ${(b.notes && b.notes.length) ? `<div class="dim" style="font-size:11.5px;margin-top:8px">${esc(b.notes.join(" "))}</div>` : ""}
    </div>`;
}

async function analyzePair(symbol) {
  symbol = (symbol || "").toUpperCase().trim();
  if (!symbol) return;
  if (!/USD|BTC$|ETH$|BNB$/.test(symbol)) symbol += "USDT";
  $("ca-status").textContent = "Analyzing…";
  $("ca-result").innerHTML = "";
  try {
    const b = await api("/api/council/analyze", "POST", { symbol });
    $("ca-result").innerHTML = renderBriefingCard(b);
    $("ca-status").textContent = "";
  } catch (e) {
    $("ca-status").textContent = "";
    $("ca-result").innerHTML = `<div class="empty"><strong>Analysis failed</strong>
      <div class="dim" style="margin-top:6px">${esc(e.message || "error")}</div></div>`;
  }
}

if ($("desk-send")) {
  $("desk-send").addEventListener("click", () => deskSend($("desk-input").value));
  $("desk-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); deskSend($("desk-input").value); }
  });
  document.querySelectorAll("#desk-suggest .chip").forEach((c) =>
    c.addEventListener("click", () => deskSend(c.dataset.q)));
}

/* ---- floating chat: open / close ---- */
function deskOpen(show) {
  const p = $("desk-popup"), fab = $("desk-fab");
  if (!p) return;
  const willOpen = show == null ? !p.classList.contains("open") : !!show;
  p.classList.toggle("open", willOpen);
  p.hidden = !willOpen;                 // keep the attribute in sync for a11y
  if (fab) fab.classList.toggle("open", willOpen);
  if (willOpen) { refreshLlmStatus(); setTimeout(() => $("desk-input") && $("desk-input").focus(), 60); }
}
if ($("desk-fab")) {
  $("desk-fab").addEventListener("click", () => deskOpen());
  if ($("desk-close")) $("desk-close").addEventListener("click", () => deskOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("desk-popup") && !$("desk-popup").hidden) deskOpen(false);
  });
}

/* ---- connect a model (free): status + key form ---- */
const LLM_HELP = {
  groq: `<a href="https://console.groq.com/keys" target="_blank" rel="noopener">Get a free Groq key →</a>`,
  gemini: `<a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Get a free Gemini key →</a>`,
  openai: `<a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">Get an OpenAI key →</a> (paid)`,
  anthropic: `<a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">Get an Anthropic key →</a> (paid)`,
};
function llmHelpLink() {
  const p = $("llm-provider") ? $("llm-provider").value : "groq";
  if ($("llm-help-link")) $("llm-help-link").innerHTML = LLM_HELP[p] || "";
}
async function refreshLlmStatus() {
  if (!$("llm-status")) return;
  try {
    const s = await api("/api/desk/llm-status");
    if (s.connected) {
      $("llm-status").textContent = "model: " + s.provider;
      $("llm-status").classList.add("pill-on");
      $("llm-connect-btn").textContent = "Change model";
      if ($("llm-disconnect")) $("llm-disconnect").hidden = false;
    } else {
      $("llm-status").textContent = "grounded desk";
      $("llm-status").classList.remove("pill-on");
      $("llm-connect-btn").textContent = "Connect a model";
      if ($("llm-disconnect")) $("llm-disconnect").hidden = true;
    }
  } catch (e) { /* leave default */ }
}
function llmMsg(text, kind) {
  const el = $("llm-msg");
  if (!el) return;
  el.hidden = false;
  el.textContent = text;
  el.className = "llm-msg " + (kind || "");
}
function populateModels(models, current) {
  const sel = $("llm-model"), row = $("llm-model-row");
  if (!sel || !row) return;
  if (!models || !models.length) { row.hidden = true; return; }
  sel.innerHTML = models.map((m) =>
    `<option value="${esc(m)}"${m === current ? " selected" : ""}>${esc(m)}</option>`).join("");
  row.hidden = false;
}
async function refreshModels() {
  try {
    const r = await api("/api/desk/llm-models");
    if (r.connected) populateModels(r.models, r.current);
    else { const row = $("llm-model-row"); if (row) row.hidden = true; }
  } catch (e) { /* ignore */ }
}
if ($("llm-connect-btn")) {
  $("llm-connect-btn").addEventListener("click", () => {
    const f = $("llm-form");
    f.hidden = !f.hidden;
    if (!f.hidden) { llmHelpLink(); refreshModels(); $("llm-key").focus(); }
  });
  $("llm-cancel").addEventListener("click", () => { $("llm-form").hidden = true; if ($("llm-msg")) $("llm-msg").hidden = true; });
  $("llm-provider").addEventListener("change", llmHelpLink);
  if ($("llm-model")) $("llm-model").addEventListener("change", async () => {
    const model = $("llm-model").value;
    try { await api("/api/desk/llm-model", "POST", { model }); llmMsg("Model set to " + model + ".", "good"); }
    catch (e) { llmMsg("Couldn't switch model (" + esc(e.message || "error") + ").", "bad"); }
  });
  $("llm-save").addEventListener("click", async () => {
    const provider = $("llm-provider").value;
    const key = $("llm-key").value.trim();
    if (!key) { llmMsg("Paste a key first.", "bad"); return; }
    llmMsg("Connecting…", "");
    try {
      const r = await api("/api/desk/llm-key", "POST", { provider, key });
      if (r.ok) {
        llmMsg(r.note || "Connected.", "good");
        $("llm-key").value = "";
        if (r.models && r.models.length) populateModels(r.models, r.current);
        else refreshModels();
        await refreshLlmStatus();
        toast("Model connected — the desk is now fully conversational.", "good");
      } else {
        llmMsg(r.error || "Couldn't connect.", "bad");
      }
    } catch (e) {
      llmMsg("Couldn't connect (" + esc(e.message || "error") + ").", "bad");
    }
  });
  $("llm-key").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("llm-save").click(); } });
  if ($("llm-disconnect")) $("llm-disconnect").addEventListener("click", async () => {
    try { await api("/api/desk/llm-disconnect", "POST", {}); await refreshLlmStatus(); const row = $("llm-model-row"); if (row) row.hidden = true; llmMsg("Disconnected — the grounded desk is answering.", ""); }
    catch (e) { llmMsg("Couldn't disconnect (" + esc(e.message || "error") + ").", "bad"); }
  });
  refreshLlmStatus();
}
if ($("ca-go")) {
  $("ca-go").addEventListener("click", () => analyzePair($("ca-symbol").value));
  $("ca-symbol").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); analyzePair($("ca-symbol").value); }
  });
}

/* ---------------- Onchain & Pay (paper) ---------------- */
let ocSelectsReady = false;
function ocRenderState(st) {
  if (!st) return;
  if ($("oc-usdc")) $("oc-usdc").textContent = Number(st.usdc).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if ($("oc-staked")) $("oc-staked").textContent = Number(st.total_staked_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!ocSelectsReady) {
    const sa = $("oc-stake-asset"); if (sa && st.assets) sa.innerHTML = st.assets.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    const sw = $("oc-swap-to"); if (sw && st.swap_targets) sw.innerHTML = st.swap_targets.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    ocSelectsReady = true;
  }
  const stk = $("oc-stakes");
  if (stk) {
    stk.innerHTML = (st.stakes && st.stakes.length)
      ? `<table class="an-table"><thead><tr><th>Asset</th><th style="text-align:right">Staked</th><th style="text-align:right">APY</th><th style="text-align:right">Rewards</th></tr></thead><tbody>${
          st.stakes.map((s) => `<tr><td>${esc(s.asset)}</td><td class="mono" style="text-align:right">${s.staked_usd.toFixed(2)}</td><td class="mono" style="text-align:right">${(s.apy * 100).toFixed(1)}%</td><td class="mono up" style="text-align:right">${s.rewards_usd.toFixed(4)}</td></tr>`).join("")
        }</tbody></table>`
      : `<div class="dim" style="font-size:12.5px">No active stakes.</div>`;
  }
  const tok = $("oc-tokens");
  if (tok) {
    tok.innerHTML = (st.tokens && st.tokens.length)
      ? `<div class="dim" style="font-size:12.5px">Holdings: ${st.tokens.map((t) => `${t.amount} ${esc(t.asset)}`).join(" · ")}</div>`
      : `<div class="dim" style="font-size:12.5px">No swapped holdings yet.</div>`;
  }
  const rc = $("oc-receipts");
  if (rc) {
    rc.innerHTML = (st.receipts && st.receipts.length)
      ? st.receipts.map((r) => {
          const denied = r.kind.includes("denied");
          const amt = r.amount_usd != null ? `${Number(r.amount_usd).toFixed(2)} USDC` : (r.rewards_usd != null ? `${Number(r.rewards_usd).toFixed(4)} USDC` : "");
          const label = { onchain_stake: "Stake", onchain_unstake: "Unstake", onchain_claim: "Claim rewards", onchain_swap: "Swap", agent_payment: "Agent payment" }[r.kind] || r.kind;
          const detail = r.to ? ` → ${esc(r.to)}` : (r.asset ? ` · ${esc(r.asset)}` : (r.to_asset ? ` → ${esc(r.to)}` : ""));
          return `<div class="oc-receipt${denied ? " denied" : ""}">
            <div><strong>${esc(label)}</strong>${detail} <span class="mono dim">${amt}</span></div>
            <div class="dim mono" style="font-size:11px">${r.seq != null ? "#" + r.seq + " · " : ""}${r.hash ? esc(r.hash) + "…" : ""}${denied ? " · blocked" : ""}${r.tx ? " · " + esc(String(r.tx).slice(0, 14)) + "…" : ""}</div>
          </div>`;
        }).join("")
      : `<div class="empty"><strong>No actions yet</strong><div class="dim" style="margin-top:4px">Stake, swap or pay above — each one is signed to the ledger.</div></div>`;
  }
}
async function loadOnchain() {
  try { ocRenderState(await api("/api/onchain/state")); }
  catch (e) { if ($("oc-msg")) $("oc-msg").textContent = "Couldn't load: " + (e.message || "error"); }
}
function ocMsg(t, ok) { if ($("oc-msg")) { $("oc-msg").textContent = t; $("oc-msg").style.color = ok ? "var(--up)" : "var(--down)"; } }
async function ocAction(path, body, okmsg) {
  try {
    const r = await api(path, "POST", body);
    if (r.ok) { ocMsg(r.message || okmsg || "Done.", true); if (r.state) ocRenderState(r.state); else loadOnchain(); }
    else ocMsg(r.error || "Action failed.", false);
  } catch (e) { ocMsg("Failed: " + (e.message || "error"), false); }
}
if ($("oc-stake")) {
  $("oc-stake").addEventListener("click", () => ocAction("/api/onchain/stake", { asset: $("oc-stake-asset").value, amount: parseFloat($("oc-stake-amt").value) }));
  $("oc-unstake").addEventListener("click", () => ocAction("/api/onchain/unstake", { asset: $("oc-stake-asset").value, amount: parseFloat($("oc-stake-amt").value) }));
  $("oc-claim").addEventListener("click", () => ocAction("/api/onchain/claim", { asset: $("oc-stake-asset").value }));
  $("oc-swap").addEventListener("click", () => ocAction("/api/onchain/swap", { from_asset: "USDC", to_asset: $("oc-swap-to").value, amount: parseFloat($("oc-swap-amt").value) }));
  $("oc-pay").addEventListener("click", () => ocAction("/api/onchain/pay", { to: $("oc-pay-to").value, amount: parseFloat($("oc-pay-amt").value), memo: $("oc-pay-memo").value }));
}

(async function boot() {
  try {
    const s = await api("/api/session");
    TOKEN = s.token;
    render(await api("/api/status"));
    connect();
    window.addEventListener("resize", () => STATE && renderSpark(STATE));
  } catch (e) {
    toast("Could not reach the GlassBox backend. Is it running?", "bad");
  }
})();
