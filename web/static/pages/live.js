/* Live terminal (#/live): Angel live feed in market hours, or a replay of a saved session.
   Top strip (indices, VIX) + breadth; watch list (Stock Desk + Market Lab picks + paper takes) with ticks; live 5-min chart
   (VWAP, opening range, plan legs, ±1% bands); alerts feed with sound; paper takes live P&L; Market Lab picks live; quick notes.
   Data: /api/live/*. Read only: no orders. */
(function () {
  const { api, esc, num, $, $$, table } = MX;
  const COL = { E1: "up", SL: "down", T1: "blue", T2: "blue" };
  const C = (x, d = 2) => x == null ? "—" : `<span class="${x > 0 ? "up" : x < 0 ? "down" : "muted"}">${x > 0 ? "+" : ""}${num(x, d)}%</span>`;
  const P = (x) => x == null ? "—" : num(x, x >= 1000 ? 1 : 2);
  const AR = (d) => d === "up" ? `<span class="up">▲</span>` : d === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
  const LBL = (d) => d === "up" ? `<span class="up">▲ up</span>` : d === "down" ? `<span class="down">▼ down</span>` : `<span class="muted">${esc(d || "—")}</span>`;
  const MV = { cc: "close-to-close", oc: "open-to-close" };
  function chips(r) {
    const out = [];
    if (r.ml) out.push(`<span class="lv-chip" title="Market Lab: chance of a ≥1% ${MV[r.ml.move] || r.ml.move} move vs usual; lean; confidence">ML ${num(100 * r.ml.chance, 0)}%/${num(100 * r.ml.usual, 0)}% ${AR(r.ml.lean)}</span>`);
    if (r.ta) out.push(`<span class="lv-chip" title="Technical read (score)">TA ${AR(r.ta)}${r.ta_score != null ? ` ${r.ta_score > 0 ? "+" : ""}${r.ta_score}` : ""}</span>`);
    if (r.fund && r.fund !== "no data") out.push(`<span class="lv-chip" title="Fundamentals">F ${AR(r.fund)}</span>`);
    if (r.lean && r.lean !== "unclear") out.push(`<span class="lv-chip" title="Tonight's plan lean">plan ${AR(r.lean)}</span>`);
    r.crossed.forEach((c) => out.push(`<span class="lv-chip ${c[0] === "+" ? "up" : "down"}">${c}</span>`));
    r.fired.forEach((x) => out.push(`<span class="lv-chip hit">${esc(x)}</span>`));
    return out.join("");
  }
  function callPanel(c) {
    if (!c || !c.has_plan) return `<p class="muted small">No plan saved for this stock last night.</p>`;
    const legs = (p) => { if (!p) return "—"; const sd = (side) => { const g = (k) => p.legs.find((x) => x.leg === k && x.side === side);
        const e = g("E1"); if (!e) return ""; return `<div class="small"><b class="${side === "long" ? "up" : "down"}">${side === "long" ? "▲ Long" : "▼ Short"}</b> E1 ${P(e.price)} · SL ${P(g("SL")?.price)} · T1 ${P(g("T1")?.price)} · T2 ${P(g("T2")?.price)}</div>`; };
      return `<div class="small">lean ${LBL(p.lean)} · ${esc(p.hold || "")}</div>${sd("long")}${sd("short")}${(p.flags || []).map((f) => `<div class="muted small">⚠ ${esc(f)}</div>`).join("")}`; };
    const ml = c.picks.length ? c.picks.map((x) => `<div class="small">${esc(MV[x.move] || x.move)} ≥${x.size}%: <b>${num(100 * x.chance, 0)}%</b> <span class="muted">vs usual ${num(100 * x.usual, 0)}%</span> · lean ${LBL(x.lean)} · conf ${esc(x.confidence)}</div>`).join("")
      : `<div class="muted small">Not among today's picks.</div>`;
    const why = (o) => (o && o.why || []).slice(0, 4).map((w) => `<li>${esc(w)}</li>`).join("");
    return `<div class="lv-call">
      <div><div class="mllab">Market Lab</div>${ml}</div>
      <div><div class="mllab">TA ${c.ta ? `· ${LBL(c.ta.dir)}${c.ta.score != null ? ` <span class="muted">score ${c.ta.score}</span>` : ""}` : ""}</div><ul class="lv-why">${why(c.ta)}</ul>
        ${(c.patterns || []).slice(0, 3).map((p) => `<div class="muted small">candle: ${esc(p.label || p.pattern || "")} ${esc(p.direction || "")}</div>`).join("")}</div>
      <div><div class="mllab">Fundamentals ${c.fund ? `· ${LBL(c.fund.dir)}` : ""}</div><ul class="lv-why">${why(c.fund)}</ul></div>
      <div><div class="mllab">Plan · intraday</div>${legs(c.intraday)}</div>
    </div>`;
  }
  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private window */ } } };

  let actx = null;
  function beep(tone) {
    try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); const o = actx.createOscillator(), g = actx.createGain();
      o.frequency.value = tone === "down" ? 440 : tone === "accent" ? 990 : 740; g.gain.setValueAtTime(0.08, actx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.25); o.connect(g); g.connect(actx.destination); o.start(); o.stop(actx.currentTime + 0.26); } catch (e) { /* no audio */ }
  }

  async function page(el, arg) {
    el.innerHTML = `<div class="lv">
      <div class="lv-top"><h1 style="margin:0">Live terminal</h1><span class="lv-pill" id="lvst">…</span><span class="muted small" id="lvmsg"></span><span style="flex:1"></span>
        <button class="btn primary" id="lvgo">Start live</button>
        <span class="lv-rep"><input class="in" type="date" id="lvday" title="session to replay (blank = last saved)"><select class="in" id="lvspd" title="replay speed">
          <option value="1">1 bar/s</option><option value="4" selected>4 bars/s</option><option value="20">20 bars/s</option></select><button class="btn" id="lvrep">Replay</button></span>
        <button class="btn" id="lvstop">Stop</button><button class="btn" id="lvsnd" title="sound on alerts"></button>
        <a class="btn" href="#/tally" target="_blank" rel="noopener" title="projected vs what happened">Tally ↗</a><a class="btn" href="#/live" target="_blank" rel="noopener" title="open in a new browser tab">↗</a></div>
      <div class="lv-strip" id="lvstrip"></div><div class="lv-breadth" id="lvbr"></div>
      <div class="lv-grid">
        <div class="card lv-watch"><div class="ct"><h2>Watch</h2><span class="aside"><input class="in" id="lvadd" placeholder="add stock" style="width:120px"></span></div><div id="lvw"></div></div>
        <div class="card lv-chart"><div class="ct"><h2 id="lvsym">—</h2><span class="aside small" id="lvlinks"></span></div><div id="lvcall"></div><div class="small muted" id="lvci" style="margin:6px 0"></div><div id="lvc" style="min-height:420px"></div>
          <div class="row" style="gap:6px;margin-top:8px"><input class="in" id="lvnote" placeholder="quick note → saved to this stock's Stock Desk notes with time and price" style="flex:1"><button class="btn" id="lvnb">Save note</button></div></div>
        <div class="card lv-alerts"><div class="ct"><h2>Alerts</h2><span class="aside small muted" id="lvac"></span></div><div id="lva" class="lv-feed"></div></div>
      </div>
      <div class="tabs" id="lvtabs"><button data-t="picks" class="on">Market Lab live</button><button data-t="takes">Paper takes</button></div><div id="lvtab"></div></div>`;

    let callFor = null, sel = arg ? String(arg).toUpperCase() : store.get("lv-sel", null), seq = 0, tab = "picks", snd = store.get("lv-sound", true), last = {}, alerts = [], busy = false, data = null, firstLoad = true;
    const sndBtn = $("#lvsnd", el); const drawSnd = () => { sndBtn.textContent = snd ? "🔔" : "🔕"; }; drawSnd();
    sndBtn.addEventListener("click", () => { snd = !snd; store.set("lv-sound", snd); drawSnd(); if (snd) beep("up"); });
    const act = async (path, body, msg) => { try { await MX.post(path, body, msg); seq = 0; alerts = []; firstLoad = true; tickNow(); } catch (e) { /* toast */ } };
    $("#lvgo", el).addEventListener("click", () => act("live/start", {}, "Connecting to Angel live feed"));
    $("#lvrep", el).addEventListener("click", () => act("live/start", { replay: true, day: $("#lvday", el).value || undefined, speed: +$("#lvspd", el).value }, "Replay starting (≈20 s to load bars)"));
    $("#lvstop", el).addEventListener("click", () => act("live/stop", {}));
    const addW = async () => { const s = $("#lvadd", el).value.trim().toUpperCase(); if (!s) return; try { await MX.post("live/watch", { symbol: s }); $("#lvadd", el).value = ""; pick(s); } catch (e) { /* toast */ } };
    $("#lvadd", el).addEventListener("keydown", (e) => { if (e.key === "Enter") addW(); });
    MX.suggest && MX.suggest($("#lvadd", el), { kinds: "stocks", recent: "stock", onPick: () => addW() });
    const saveNote = async () => { const t = $("#lvnote", el).value.trim(); if (!t || !sel) return MX.toast(sel ? "Write a note first" : "Pick a stock", true);
      try { const r = await MX.post("live/note", { symbol: sel, text: t }); $("#lvnote", el).value = ""; MX.toast(`Note saved to ${sel} (${r.stamp})`); } catch (e) { /* toast */ } };
    $("#lvnb", el).addEventListener("click", saveNote); $("#lvnote", el).addEventListener("keydown", (e) => { if (e.key === "Enter") saveNote(); });
    $("#lvtabs", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; tab = b.dataset.t; $$("#lvtabs button", el).forEach((x) => x.classList.toggle("on", x === b)); drawTab(); });

    function pick(s) { sel = s; store.set("lv-sel", s); callFor = null;
      if (data && data.watch && data.status.state !== "stopped" && !data.watch.some((r) => r.symbol === s)) MX.post("live/watch", { symbol: s }).catch(() => {}); $$(".lv-row", el).forEach((r) => r.classList.toggle("sel", r.dataset.s === s)); drawChart(); }

    function drawTop(d) {
      const s = d.status, pill = $("#lvst", el);
      pill.textContent = s.state === "live" ? "● LIVE" : s.state === "replay" ? "▶ REPLAY" : s.state; pill.className = `lv-pill ${s.state}`;
      $("#lvmsg", el).textContent = [s.msg, d.last ? `last tick ${d.last.slice(11)}` : "", d.ticks ? `${num(d.ticks, 0)} ticks` : "", s.reconnects ? `${s.reconnects} reconnects` : "",
        d.plan_day ? `plan of ${d.plan_day}` : "", d.fill && (d.fill.done || d.fill.failed) ? `earlier bars ${d.fill.done} loaded${d.fill.failed ? `, ${d.fill.failed} failed` : ""}` : "", d.fill && d.fill.error ? `last error: ${d.fill.error}` : "", !s.market_open && s.state === "stopped" ? "market closed: use Replay to try it" : ""].filter(Boolean).join(" · ");
      $("#lvstrip", el).innerHTML = (d.strip || []).map((x) => `<div class="lv-ix"><div class="muted small">${esc(x.label)}</div><b>${P(x.ltp)}</b> ${C(x.chg)}</div>`).join("") || `<span class="muted small">Indices appear once the feed runs.</span>`;
      const b = d.breadth; if (b && b.n) { const a = 100 * b.adv / b.n;
        $("#lvbr", el).innerHTML = `<div class="lv-bar"><span class="up" style="width:${a}%"></span><span class="down" style="width:${100 - a}%"></span></div>
          <span class="small"><b class="up">${b.adv}</b> up · <b class="down">${b.dec}</b> down of ${b.n} · above VWAP ${b.above_vwap} · ≥+1% <b class="up">${b.up1}</b> · ≤−1% <b class="down">${b.down1}</b></span>`; }
      else $("#lvbr", el).innerHTML = "";
    }

    function drawWatch(d) {
      const rows = d.watch || [], host = $("#lvw", el);
      if (!rows.length) { host.innerHTML = `<p class="muted small">Start the feed: watched = Stock Desk list + Market Lab picks + paper takes.</p>`; return; }
      host.innerHTML = `<table class="tbl lv-tbl"><thead><tr><th>Stock</th><th class="r">Last</th><th class="r">Chg</th><th class="r">vs VWAP</th></tr></thead><tbody>${rows.map((r) => {
        const f = last[r.symbol] != null && r.ltp != null && r.ltp !== last[r.symbol] ? (r.ltp > last[r.symbol] ? "fl-up" : "fl-down") : "";
        return `<tr class="lv-row ${r.symbol === sel ? "sel" : ""}" data-s="${esc(r.symbol)}"><td><b>${esc(r.symbol)}</b> <span class="muted small">${r.src.map(esc).join(" · ")}</span><div class="lv-chips">${chips(r)}</div></td>
          <td class="r num ${f}">${P(r.ltp)}</td><td class="r">${C(r.chg)}</td><td class="r">${C(r.vs_vwap)}</td></tr>`; }).join("")}</tbody></table>`;
      rows.forEach((r) => { last[r.symbol] = r.ltp; });
      $$(".lv-row", host).forEach((tr) => tr.addEventListener("click", () => pick(tr.dataset.s)));
      if (!sel && rows.length) pick(rows[0].symbol);
    }

    function drawAlerts(d) {
      const nw = d.alerts || [];
      if (nw.length && !firstLoad && snd) beep(nw[nw.length - 1].tone);
      alerts = alerts.concat(nw).slice(-300); seq = d.seq || seq; firstLoad = false;
      $("#lvac", el).textContent = alerts.length ? `${alerts.length}` : "";
      $("#lva", el).innerHTML = alerts.length ? alerts.slice().reverse().map((a) => `<div class="lv-al ${esc(a.tone)} ${nw.includes(a) ? "new" : ""}"><span class="muted small">${esc(a.at)}</span>
        <button class="lv-sym" data-s="${esc(a.symbol)}">${esc(a.symbol)}</button> ${esc(a.text.replace(a.symbol + " ", ""))}</div>`).join("") : `<p class="muted small">±1% / ±2% crosses, plan E1 / SL / T1 / T2 and paper fills show here.</p>`;
      $$(".lv-sym", el).forEach((b) => b.addEventListener("click", () => pick(b.dataset.s)));
    }

    function drawTab() {
      const host = $("#lvtab", el), d = data || {};
      if (tab === "picks") {
        const p = d.picks || [];
        if (!p.length) { host.innerHTML = `<p class="muted">No Market Lab picks loaded (build tonight's radar, then start the feed).</p>`; return; }
        const done = p.filter((x) => x.moved), right = p.filter((x) => x.lean_right != null), ok = right.filter((x) => x.lean_right);
        host.innerHTML = `<p class="small">So far: <b>${done.length}/${p.length}</b> picks are ≥1% away now · ${p.filter((x) => x.touched).length} touched 1% · lean right <b>${ok.length}/${right.length}</b> (only picks with a shown lean).</p>` +
          table(p, [["symbol", "Stock", (v) => `<button class="lv-sym" data-s="${esc(v)}">${esc(v)}</button>`], ["move", "Move", (v) => v === "cc" ? "close-to-close" : "open-to-close"],
            ["chance", "Chance ≥1%", (v, r) => `${num(100 * v, 0)}% <span class="muted small">usual ${num(100 * r.usual, 0)}%</span>`], ["lean", "Lean", (v) => v === "up" ? `<span class="up">▲ up</span>` : v === "down" ? `<span class="down">▼ down</span>` : `<span class="muted">unclear</span>`],
            ["move_now", "Now", (v) => C(v)], ["moved", "≥1% now", (v) => v == null ? "—" : v ? "✓" : "·"], ["touched", "Touched", (v) => v == null ? "—" : v ? "✓" : "·"],
            ["beyond_min", "Min beyond 1%", (v, r) => v ? `${num(v, 0)} <span class="muted small">from ${esc(r.first || "")}</span>` : "—"], ["lean_right", "Lean right?", (v) => v == null ? "—" : v ? `<span class="up">yes</span>` : `<span class="down">no</span>`],
            ["confidence", "Conf."]]);
      } else {
        const t = d.takes || [];
        if (!t.length) { host.innerHTML = `<p class="muted">No paper takes for this plan (Trade plan → Paper take).</p>`; return; }
        const tot = t.reduce((a, x) => a + (x.pnl || 0), 0);
        const cst = t.reduce((a, x) => a + (x.costs || 0), 0);
        host.innerHTML = `<p class="small">Total net paper P&amp;L <b class="${tot >= 0 ? "up" : "down"}">₹${num(tot, 0)}</b> <span class="muted">after ₹${num(cst, 0)} costs (brokerage, STT, exchange, stamp, GST, 0.03% slippage per side; open trades include the exit)</span></p>` +
          table(t, [["symbol", "Stock", (v) => `<button class="lv-sym" data-s="${esc(v)}">${esc(v)}</button>`], ["mode", "Mode"], ["state", "State"], ["side", "Side", (v) => v || "—"],
            ["entry", "Entry", (v) => P(v)], ["left", "Open qty", (v, r) => `${v}/${r.qty}`], ["stop", "Stop", (v) => P(v)], ["last", "Last", (v) => P(v)],
            ["gross", "Gross ₹", (v) => num(v, 0)], ["costs", "Costs ₹", (v) => v ? `<span class="muted">−${num(v, 0)}</span>` : "—"],
            ["pnl", "Net P&L ₹", (v) => `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${num(v, 0)}</b>`], ["r", "Net R", (v) => v == null ? "—" : num(v, 2)],
            ["events", "Fills", (v) => (v || []).map((e) => `<div class="small">${esc(e.at)} ${esc(e.what)} @ ${P(e.price)}</div>`).join("")]]);
      }
      $$(".lv-sym", host).forEach((b) => b.addEventListener("click", () => pick(b.dataset.s)));
    }

    async function drawChart() {
      const host = $("#lvc", el); if (!sel) return; $("#lvsym", el).textContent = sel;
      if (callFor !== sel) { $("#lvcall", el).innerHTML = ""; $("#lvlinks", el).innerHTML = ""; }
      if (!data || !data.status || data.status.state === "stopped" && !data.ticks) { host.innerHTML = `<div class="empty">Start the feed to see ${esc(sel)} live.</div>`; return; }
      let c; try { c = await api(`live/chart?${MX.qs({ symbol: sel })}`); } catch (e) { return; }
      if (!el.isConnected || c.symbol !== sel) return;
      if (callFor !== sel) { callFor = sel; $("#lvcall", el).innerHTML = callPanel(c.call);
        $("#lvlinks", el).innerHTML = `<a href="#/ta/${encodeURIComponent(sel)}" target="_blank" rel="noopener">TA workspace ↗</a> · <a href="#/desk/${encodeURIComponent(sel)}" target="_blank" rel="noopener">Stock Desk ↗</a>`; }
      const levels = [];
      if (c.prev) { levels.push({ price: c.prev, color: "faint", label: "prev", width: 1.2 }); levels.push({ price: c.prev * 1.01, color: "faint", dash: "2 4", label: "+1%" }); levels.push({ price: c.prev * 0.99, color: "faint", dash: "2 4", label: "−1%" }); }
      if (c.or_high != null) { levels.push({ price: c.or_high, color: "accent", dash: "5 3", label: "OR hi" }); levels.push({ price: c.or_low, color: "accent", dash: "5 3", label: "OR lo" }); }
      (c.legs || []).forEach((x) => levels.push({ price: x.price, color: COL[x.leg] || "faint", label: `${x.leg}${x.side === "short" ? "↓" : "↑"}`, width: x.leg === "SL" ? 1.6 : 1.3 }));
      if (!c.bars.t.length) { host.innerHTML = `<div class="empty">No bars for ${esc(sel)} yet.</div>`; return; }
      MXCharts.priceChart(host, c.bars, { mode: "candle", intraday: true, height: 420, levels });
      $("#lvci", el).innerHTML = `${P(c.ltp)} · line = VWAP · OR = 09:15–09:30 range · dashed = prev close ±1% · E1/SL/T1/T2 = plan${c.bars.t.length && c.bars.t[0].slice(11, 16) > "09:20" ? ` · <span class="down">bars from ${c.bars.t[0].slice(11, 16)} only (earlier bars not loaded)</span>` : ""}`;
    }

    async function tickNow() {
      if (busy || !el.isConnected) return; busy = true;
      try { data = await api(`live/state?since=${seq}`); drawTop(data); drawWatch(data); drawAlerts(data); drawTab(); } catch (e) { /* server restarting */ } finally { busy = false; }
    }
    await tickNow(); drawChart();
    const t1 = setInterval(() => { if (!el.isConnected) return clearInterval(t1); tickNow(); }, 1500);
    const t2 = setInterval(() => { if (!el.isConnected) return clearInterval(t2); if (data && ["live", "replay", "reconnecting"].includes(data.status.state)) drawChart(); }, 5000);
  }

  MX.page("live", { title: "Live terminal", render: page });
})();
