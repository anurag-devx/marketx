/* Technical analysis. MX.planView(host, symbol): compact summary (Stock tab, Market Lab → Stock analysis) with a button to
   the full TA workspace (#/ta/SYMBOL): evidence list, big chart that highlights the evidence you click, RSI / MACD panels,
   BUY / SHORT plan, fundamentals, candle patterns with reading rules and history, TA agent chat. Data: /api/tradeplan. */
(function () {
  const { api, esc, num, inr, day, $, $$, note, startJob } = MX;
  const COL = { E1: "up", E2: "up", SL: "down", T1: "blue", T2: "blue" };
  let mode = (() => { try { return sessionStorage.getItem("mx-tp-mode") || "intraday"; } catch (e) { return "intraday"; } })();
  const setMode = (m) => { mode = m; try { sessionStorage.setItem("mx-tp-mode", m); } catch (e) { /* ignore */ } };
  const icon = (v) => v > 0 ? `<b class="up">▲</b>` : v < 0 ? `<b class="down">▼</b>` : `<span class="muted">•</span>`;
  const ar = (x) => x === "up" ? `<span class="up">▲ up</span>` : x === "down" ? `<span class="down">▼ down</span>` : `<span class="muted">• ${esc(x || "no data")}</span>`;
  const cr = (v) => v == null ? "—" : `₹${num(v, 0)} cr`;
  const yo = (v) => v == null ? "—" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "▲" : v < 0 ? "▼" : "•"} ${num(100 * v, 1)}%</span>`;
  const modeSeg = (id) => `<div class="seg" id="${id}"><button data-v="intraday" class="${mode === "intraday" ? "on" : ""}">Intraday</button><button data-v="swing" class="${mode === "swing" ? "on" : ""}">Swing</button></div>`;

  /* ---------- pieces ---------- */
  function badges(d) {
    const p = d.plan, leg = (k, sd) => (p.legs.find((x) => x.leg === k && x.side === sd) || {}).price;
    const one = (sd) => { const buy = sd === "long";
      return `<div class="tp-badge ${buy ? "buy" : "short"} ${p.tradeable ? "" : "off"}"><div class="act">${buy ? "▲ BUY" : "▼ SHORT"} ${buy ? "above" : "below"} ${inr(leg("E1", sd))}</div>
        <div class="lv">stop <b>${inr(leg("SL", sd))}</b> · targets <b>${inr(leg("T1", sd))}</b> / <b>${inr(leg("T2", sd))}</b>${leg("E2", sd) != null ? ` · add ${inr(leg("E2", sd))}` : ""}</div>
        ${!buy && p.mode === "swing" ? `<div class="lv muted">overnight short needs F&O</div>` : ""}</div>`; };
    const sides = p.lean === "up" ? ["long"] : p.lean === "down" ? ["short"] : ["long", "short"];
    return `<div class="tp-badges">${sides.map(one).join("")}</div>
      ${p.lean === "unclear" ? `<div class="small muted" style="margin:-4px 0 6px">No tested direction: whichever breaks first.</div>` : ""}
      ${p.tradeable ? "" : `<div class="tp-nt" style="margin:-2px 0 6px">NO TRADE: ${esc(p.flags[0] || "checks failed")}</div>`}`;
  }

  function planDetail(d) {
    const p = d.plan, legs = p.legs.filter((x) => x.price != null);
    return `<div class="tp-sum">${(p.sides || []).map((s) => `${esc(s.side)} ${num(s.shares, 0)} sh · risk ${inr(s.risk_rs, 0)} · R:R <b>${num(s.rr, 1)}</b> · stop ${num(s.stop_atr, 1)}× ATR`).join("<br>")}</div>
      <div class="tp-legs">${legs.map((x) => `<span class="tp-leg tp-${COL[x.leg] || "faint"}" title="${esc(x.condition || "")}">${x.leg}${p.lean === "unclear" ? (x.side === "short" ? "↓" : "↑") : ""} ${inr(x.price)}</span>`).join("")}<span class="tp-leg tp-faint">TX ${esc(p.hold)}</span></div>
      ${p.flags.length ? `<p class="small muted" style="margin:4px 0 0">${p.flags.map(esc).join(" · ")}</p>` : ""}`;
  }

  function topReasons(ev, k = 3) {
    if (!ev) return "";
    const strong = ev.checks.filter((x) => x.verdict !== 0 && Math.sign(x.verdict) === (ev.dir === "up" ? 1 : ev.dir === "down" ? -1 : Math.sign(ev.net || 1)));
    return (strong.length ? strong : ev.checks).slice(0, k).map((x) => `<div class="small">${icon(x.verdict)} <b>${esc(x.id)}</b> ${esc(x.reason)}</div>`).join("");
  }

  function readLine(ev) {
    return ev ? `${ar(ev.dir)} <span class="muted small">· ${ev.up} up · ${ev.down} down · net ${ev.net >= 0 ? "+" : ""}${ev.net}</span>` : `<span class="muted">—</span>`;
  }

  function fundCard(d) {
    const f = d.fund || {}, v = f.val || {}, q = f.quarter, lab = f.label || {}, kv = (k, x) => `<dt>${k}</dt><dd>${x ?? "—"}</dd>`;
    return `<div class="card"><div class="ct"><h2>Fundamentals</h2><span class="aside" title="${esc((lab.why || []).join(" · "))}">${ar(lab.dir)}</span></div>
      <dl class="kv">${kv("Market cap", cr(v.market_cap_cr))}${kv("P/E", v.pe == null ? "—" : num(v.pe, 1))}${kv("P/B", v.pb == null ? "—" : num(v.pb, 1))}
        ${kv("ROE", v.roe_pct == null ? "—" : `${num(v.roe_pct, 1)}%`)}${kv("Debt / equity", v.debt_to_equity == null ? "—" : num(v.debt_to_equity, 2))}${kv("Free cash flow", cr(v.fcf_cr))}</dl>
      ${q ? `<p class="small" style="margin:6px 0 0">${esc(day(q.period))} quarter: revenue ${cr(q.revenue_cr)} (${yo(q.revenue_yoy)}), profit ${cr(q.profit_cr)} (${yo(q.profit_yoy)})</p>` : `<p class="muted small">No quarterly result yet.</p>`}</div>`;
  }

  const SPAN = { engulfing: 2, harami: 2, piercing: 2, dark_cloud: 2, tweezer_bottom: 2, tweezer_top: 2, kicker: 2, doji_star: 2, meeting_lines: 2, separating_lines: 2, inside_bar: 2,
    morning_star: 3, evening_star: 3, three_soldiers: 3, three_crows: 3, abandoned_baby: 3, three_inside: 3, three_outside: 3, upside_gap_two_crows: 3,
    concealing_baby_swallow: 4, three_line_strike: 4, ladder_bottom: 5, rise_fall_three: 5, mat_hold: 5 };
  function patternsTable(d) {
    const t = (r) => !r ? `<span class="muted small">—</span>` : `<span class="small">${num(100 * r.win5, 0)}% vs ${num(100 * r.base5, 0)}% · n ${num(r.n, 0)}<br><b class="${r.verdict === "edge" ? "up" : r.verdict === "against" ? "down" : "muted"}">${esc(r.verdict)}</b></span>`;
    const chk = (v) => !v ? "—" : `<span class="small" style="white-space:nowrap">${[["trend", "trend"], ["location", "level"], ["volume", "volume"], ["confirmed", "confirm"]].map(([k, l]) => `${v[k] === true ? "✓" : v[k] === false ? "✗" : "…"} ${l}`).join(" · ")}</span>`;
    return d.patterns.length ? MX.table(d.patterns, [["date", "Date", "date"],
      ["label", "Pattern", (v, r) => `<b class="${r.direction === "bullish" ? "up" : r.direction === "bearish" ? "down" : "muted"}" title="${esc(r.meaning || "")}">${r.direction === "bullish" ? "▲" : r.direction === "bearish" ? "▼" : "•"} ${esc(v)}</b> <span class="muted small">${esc(r.kind || "")}</span>`],
      ["strength", "Strength", (v) => v ? `<span class="tag">${esc(v)}</span>` : "—"], ["checks", "Reading rules", chk], ["tested", "History (5 days, its way vs normal)", t],
      ["meaning", "Meaning", (v) => `<span class="small">${esc(v || "")}</span>`]]) : `<p class="muted">No patterns in the last 5 sessions.</p>`;
  }

  function candlesList(d) {
    return (d.candles || []).slice().reverse().map((c) => `<div class="small" style="margin:0 0 5px"><b>${esc(day(c.date))}</b> <span class="${c.change_pct >= 0 ? "up" : "down"}">${c.change_pct >= 0 ? "▲ +" : "▼ "}${num(c.change_pct, 2)}%</span> · ${esc(c.words)}</div>`).join("") || `<p class="muted">—</p>`;
  }

  function chartSpec(d, bars, show, height = 420) {
    const p = d.plan, n = bars.t.length, from = Math.max(0, n - 12), lv = p.levels, levels = [], zones = [], marks = [];
    if (show.plan) {
      p.legs.filter((x) => x.price != null).forEach((x) => levels.push({ price: x.price, color: COL[x.leg], from, label: p.lean === "unclear" ? `${x.leg}${x.side === "short" ? "↓" : "↑"}` : x.leg, fit: true, width: x.leg === "SL" ? 1.8 : 1.4 }));
      [...new Set(p.legs.filter((x) => x.side !== "both").map((x) => x.side))].forEach((sd) => { const g = (k) => p.legs.find((x) => x.leg === k && x.side === sd);
        const e1 = g("E1"), sl = g("SL"), t2 = g("T2") || g("T1");
        if (e1 && t2) zones.push({ lo: e1.price, hi: t2.price, color: "up", from, opacity: 0.07 });
        if (e1 && sl) zones.push({ lo: e1.price, hi: sl.price, color: "down", from, opacity: 0.08 }); });
    }
    if (show.levels) [["P", lv.P], ["R1", lv.R1], ["S1", lv.S1], ["20d hi", lv.hi20], ["20d lo", lv.lo20]].forEach(([k, v]) => levels.push({ price: v, color: "faint", dash: "3 4", width: 1.2, label: k, from: Math.max(0, n - 40) }));
    if (show.patterns) (d.patterns || []).forEach((pt) => { const i = bars.t.indexOf(pt.date); if (i < 0) return;
      const bull = pt.direction === "bullish", bear = pt.direction === "bearish";
      marks.push({ i, price: bear ? bars.h[i] : bars.l[i], dir: bear ? "down" : "up", color: bull ? "up" : bear ? "down" : "faint", label: pt.label + (pt.strength && pt.strength !== "neutral" ? ` · ${pt.strength}` : "") }); });
    return { mode: "candle", height, levels, zones, marks };
  }

  function layerChips(id, show) {
    return `<div class="lchips" id="${id}">${[["plan", "Plan"], ["levels", "Levels"], ["patterns", "Patterns"], ["avgs", "Averages"]].filter(([k]) => k in show)
      .map(([k, l]) => `<button class="lchip ${show[k] ? "on" : ""}" data-v="${k}">${show[k] ? "✓ " : ""}${l}</button>`).join("")}</div>`;
  }
  function wireChips(host, id, show, redraw) {
    $(`#${id}`, host)?.addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; const k = x.dataset.v; show[k] = !show[k];
      x.classList.toggle("on", show[k]); x.textContent = (show[k] ? "✓ " : "") + x.textContent.replace(/^✓ /, ""); redraw(); });
  }

  function dailyBars(d, n) {
    const cut = (a) => a.slice(-n), bb = { t: cut(d.bars.t), o: cut(d.bars.o), h: cut(d.bars.h), l: cut(d.bars.l), c: cut(d.bars.c), v: cut(d.bars.v) };
    if (d.evidence) { const S = d.evidence.series, ix = new Map(S.t.map((t, i) => [t, i])), pick = (k) => bb.t.map((t) => ix.has(t) ? S[k][ix.get(t)] : null);
      Object.assign(bb, { sma20: pick("sma20"), sma50: pick("sma50"), sma200: pick("sma200"), rsi: pick("rsi"), mh: pick("macd_hist") }); }
    return bb;
  }

  function paperButton(host, symbol, d) {
    const b = $("#tppt", host); if (!b) return;
    const set = (on) => { b.textContent = on ? "✓ Paper taken" : "Paper take"; b.classList.toggle("primary", !on); };
    api("tradeplan/paper").then((r) => set(r.items.some((x) => x.symbol === symbol && x.mode === mode && x.day === d.day))).catch(() => {});
    b.addEventListener("click", async () => { try { const r = await MX.post("tradeplan/paper", { symbol, mode }, ""); set(r.taken); MX.toast(r.taken ? "Paper take saved: graded in Market Lab → Review" : "Paper take removed"); } catch (e) { /* toast shown */ } });
  }

  /* ---------- compact view: Stock tab and Market Lab ---------- */
  async function planView(host, symbol) {
    const load = async () => {
      host.innerHTML = MX.spinner("Loading …");
      const d = await api(`tradeplan?${MX.qs({ symbol, mode })}`);
      if (!d.ready) { host.innerHTML = note(esc(d.why), "warn"); return; }
      host.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:8px">${modeSeg("tpm")}
          <span class="row" style="gap:6px"><button class="btn small" id="tppt">Paper take</button><a class="btn primary" href="#/ta/${encodeURIComponent(symbol)}">Open TA workspace →</a></span></div>
        <div class="card">${badges(d)}
          <div class="row" style="gap:14px;align-items:flex-start;flex-wrap:wrap"><div style="flex:1;min-width:260px"><div class="mllab">Chart read</div>${readLine(d.evidence)}<div style="margin-top:4px">${topReasons(d.evidence)}</div></div>
            <div style="min-width:220px"><div class="mllab">Fundamentals</div>${ar((d.fund && d.fund.label || {}).dir)}</div>
            <div style="min-width:220px"><div class="mllab">Candles (5 sessions)</div>${d.patterns.length ? d.patterns.slice(0, 3).map((p) => `<div class="small">${p.direction === "bullish" ? "▲" : p.direction === "bearish" ? "▼" : "•"} ${esc(p.label)} ${p.strength && p.strength !== "neutral" ? `<span class="tag">${esc(p.strength)}</span>` : ""}</div>`).join("") : `<span class="muted small">none</span>`}</div></div></div>`;
      $("#tpm", host).addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; setMode(x.dataset.v); load(); });
      paperButton(host, symbol, d);
    };
    await load();
  }

  /* ---------- evidence highlights on the chart ---------- */
  const NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, parent) { const e = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); parent.appendChild(e); return e; }
  function highlight(host, d, bb, check) {
    const m = $("#taw-c", host)._map; if (!m) return;
    m.svg.querySelector("g.hl")?.remove();
    $$(".taw-panel", host).forEach((x) => x.classList.remove("hl"));
    const cap = $("#taw-cap", host);
    if (!check) { cap.innerHTML = `<span class="muted small">Click a check on the left to see it on the chart.</span>`; return; }
    cap.innerHTML = `${icon(check.verdict)} <b>${esc(check.id)} · ${esc(check.metric)}</b> = ${esc(check.value)} · ${esc(check.reason)} <span class="muted small">(rule: ${esc(check.rule)})</span>`;
    const g = svgEl("g", { class: "hl" }, m.svg), n = bb.t.length, acc = "var(--accent)";
    const w = Math.max(4, (m.x(1) - m.x(0)) * 0.9);
    const line = (s, from = 0) => { const pts = s.map((v, i) => i >= from && v != null ? `${m.x(i)},${m.y(v)}` : null).filter(Boolean).join(" "); if (pts) svgEl("polyline", { points: pts, fill: "none", stroke: acc, "stroke-width": 3.2, opacity: 0.9 }, g); };
    const box = (i0, i1, lo, hi, label) => { i0 = Math.max(0, i0); const x0 = m.x(i0) - w / 2, x1 = m.x(i1) + w / 2;
      svgEl("rect", { x: x0, y: m.y(hi) - 3, width: Math.max(4, x1 - x0), height: Math.max(6, m.y(lo) - m.y(hi) + 6), fill: "none", stroke: acc, "stroke-width": 2, rx: 4 }, g);
      if (label) { const t = svgEl("text", { x: x0, y: m.y(hi) - 7, "font-size": 11.5, "font-weight": 700, style: "fill:var(--accent)" }, g); t.textContent = label; } };
    const hl = (a, b) => [Math.min(...bb.l.slice(a, b + 1)), Math.max(...bb.h.slice(a, b + 1))];
    const hline = (price, from, label) => { svgEl("line", { x1: m.x(from), x2: m.x(n - 1) + w, y1: m.y(price), y2: m.y(price), stroke: acc, "stroke-width": 2.6 }, g);
      if (label) { const t = svgEl("text", { x: m.x(from), y: m.y(price) - 5, "font-size": 11.5, "font-weight": 700, style: "fill:var(--accent)" }, g); t.textContent = label; } };
    const panel = (id) => { const x = $(id, host); if (x) { x.classList.add("hl"); x.scrollIntoView({ behavior: "smooth", block: "nearest" }); } };
    const id = check.id, lv = d.plan.levels;
    if (id === "E1" && bb.sma20) line(bb.sma20);
    else if (id === "E2") { bb.sma20 && line(bb.sma20); bb.sma50 && line(bb.sma50); }
    else if (id === "E3" && bb.sma200) line(bb.sma200);
    else if (id === "E4" && n >= 20) { const [a1, b1] = hl(n - 20, n - 11), [a2, b2] = hl(n - 10, n - 1); box(n - 20, n - 11, a1, b1, "previous 10"); box(n - 10, n - 1, a2, b2, "last 10"); }
    else if (id === "E5" && bb.sma20) line(bb.sma20, n - 6);
    else if (id === "E6") panel("#taw-rsi");
    else if (id === "E7") panel("#taw-macd");
    else if (id === "E8") box(n - 1, n - 1, bb.l[n - 1], bb.h[n - 1], `volume ${check.value}`);
    else if (id === "E9") { const [a, b] = hl(n - 10, n - 1); box(n - 10, n - 1, a, b, "10 sessions"); }
    else if (id === "E10") { const [a, b] = hl(n - 14, n - 1); box(n - 14, n - 1, a, b, "ATR window"); }
    else if (id === "E11") hline(lv.P, Math.max(0, n - 15), `pivot ${inr(lv.P)}`);
    else if (id === "E12") { const [a, b] = hl(n - 21, n - 2); box(n - 21, n - 2, a, b, "previous 20 days"); box(n - 1, n - 1, bb.l[n - 1], bb.h[n - 1]); }
    else if (check.group === "Candles") { const i = bb.t.indexOf(check.value), p = (d.patterns || []).find((x) => x.date === check.value && x.label === check.metric);
      if (i >= 0) { const s = SPAN[p && p.pattern] || 1, [a, b] = hl(i - s + 1, i); box(i - s + 1, i, a, b, `${check.metric}${p && p.strength ? ` · ${p.strength}` : ""}`); } }
  }

  /* ---------- the workspace page ---------- */
  MX.page("ta", {
    title: "TA workspace",
    async render(el, arg) {
      const symbol = String(arg || "").toUpperCase();
      if (!symbol) { el.innerHTML = MX.head("TA workspace", "Open it from a stock: Stock → Technical analysis → Open TA workspace."); return; }
      const show = { plan: true, levels: true, patterns: true, avgs: true };
      let tf = "daily", pick = null;
      const load = async () => {
        el.innerHTML = MX.spinner("Loading …");
        const d = await api(`tradeplan?${MX.qs({ symbol, mode, bars: 150 })}`);
        if (!d.ready) { el.innerHTML = MX.head(`${symbol} · TA workspace`, "") + note(esc(d.why), "warn"); return; }
        const ev = d.evidence;
        el.innerHTML = `<div class="row" style="justify-content:space-between;align-items:center;margin-bottom:10px">
            <div><a href="#/stock/${encodeURIComponent(symbol)}" class="muted small">← ${esc(symbol)}</a><h1 style="margin:2px 0 0">${esc(symbol)} · Technical analysis</h1>
              <div class="muted small">levels from the ${esc(day(d.plan.levels.date))} bar · ${esc(d.patterns_source || "")}</div></div>
            <div class="row" style="gap:8px;flex-wrap:wrap">${modeSeg("tawm")}<div class="seg" id="tawtf"><button data-v="daily" class="${tf === "daily" ? "on" : ""}">Daily</button><button data-v="5min" class="${tf === "5min" ? "on" : ""}">5-min</button></div>
              ${layerChips("tawl", show)}<button class="btn small" id="tpsv">Save chart</button><button class="btn small" id="tppt">Paper take</button></div></div>
          <div class="taw">
            <div class="card taw-left"><div class="ct"><h2>Evidence</h2><span class="aside">${readLine(ev)}</span></div>
              ${ev ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:6px">${Object.entries(ev.by_group).filter(([, x]) => x.n).map(([g, x]) => `<span class="tag">${esc(g)} <span class="up">▲${x.up}</span> <span class="down">▼${x.down}</span></span>`).join("")}</div>
                ${["Trend", "Momentum", "Volume", "Volatility", "Levels", "Candles"].map((g) => { const cs = ev.checks.filter((x) => x.group === g); return cs.length ? `<div class="mllab" style="margin-top:8px">${g}</div>` +
                  cs.map((x) => `<div class="ev-row" data-id="${esc(x.id)}" title="${esc(x.rule)}">${icon(x.verdict)} <b>${esc(x.id)}</b> ${esc(x.metric)} <span class="muted">${esc(x.value)}</span><div class="small muted">${esc(x.reason)}</div></div>`).join("") : ""; }).join("")}
                <p class="muted small">Read: net ≥ +3 ▲, ≤ −3 ▼. Labels, graded in Market Lab → Review.</p>` : `<p class="muted">Not enough history.</p>`}</div>
            <div class="taw-mid"><div class="card"><div id="taw-cap" style="min-height:22px;margin-bottom:6px"></div><div id="taw-c"></div>
                <div class="taw-panel" id="taw-rsi"><div class="mllab">RSI(14) · 30 / 70</div><div id="taw-rsic"></div></div>
                <div class="taw-panel" id="taw-macd"><div class="mllab">MACD histogram</div><div id="taw-macdc"></div></div></div>
              <div class="card section"><div class="ct"><h2>Candle patterns</h2></div>${patternsTable(d)}</div>
              <div class="card section"><div class="ct"><h2>Last 5 candles</h2></div>${candlesList(d)}</div></div>
            <div class="taw-right"><div class="card">${badges(d)}${planDetail(d)}</div>
              <div class="section">${fundCard(d)}</div>
              <div class="card section"><div class="ct"><h2>TA agent</h2><button class="btn small" id="tpx">Explain</button></div>
                <div id="tpxo" class="small" style="white-space:pre-line">${esc(d.summary || "")}</div><span class="muted small" id="tpxs"></span>
                <div id="tpch" style="margin-top:8px"></div><div class="row" style="gap:6px;margin-top:6px"><input class="in" id="tpq" maxlength="500" style="flex:1" placeholder="Ask about this plan"><button class="btn small" id="tpqa">Ask</button><button class="btn small" id="tpqc">Clear</button></div></div></div></div>`;
        const bb = dailyBars(d, 120);
        let ib = null;
        const drawAll = async () => {
          if (!document.body.contains(el)) return;
          const c = $("#taw-c", el);
          if (tf === "5min") {
            if (!ib) { const r = await api(`stock/intraday?${MX.qs({ symbol, days: 2, bar: 5 })}`).catch(() => ({ empty: true })); ib = r.empty ? { empty: true, note: r.note } : r.bars; }
            if (ib.empty) { c.innerHTML = note(esc(ib.note || "No 5-minute bars."), "warn"); return; }
            MXCharts.priceChart(c, ib, { ...chartSpec({ ...d, patterns: [] }, ib, { ...show, levels: false, patterns: false }, 480), intraday: true });
            $("#taw-cap", el).innerHTML = `<span class="muted small">5-minute bars, last 2 sessions, with VWAP and tonight's plan. The evidence uses daily bars: switch to Daily to see it on the chart.</span>`;
            $("#taw-rsic", el).innerHTML = $("#taw-macdc", el).innerHTML = ""; return;
          }
          const bars = show.avgs ? bb : { ...bb, sma20: null, sma50: null, sma200: null };
          MXCharts.priceChart(c, bars, chartSpec(d, bars, show, 480));
          if (bb.rsi) { MXCharts.lineChart($("#taw-rsic", el), { x: bb.t, height: 110, hlines: [30, 70], series: [{ name: "RSI(14)", y: bb.rsi, color: "#8b5cf6" }] });
            MXCharts.lineChart($("#taw-macdc", el), { x: bb.t, height: 90, zero: true, hlines: [0], series: [{ name: "MACD hist", y: bb.mh, color: "#0ea5e9" }] }); }
          highlight(el, d, bb, pick && ev ? ev.checks.find((x) => x.id === pick) : null);
        };
        requestAnimationFrame(drawAll);
        const onResize = () => drawAll(); window.addEventListener("mx-resize", onResize);
        $$(".ev-row", el).forEach((r) => r.addEventListener("click", () => { pick = pick === r.dataset.id ? null : r.dataset.id;
          $$(".ev-row", el).forEach((x) => x.classList.toggle("on", x.dataset.id === pick)); if (tf !== "daily") { tf = "daily"; $$("#tawtf button", el).forEach((x) => x.classList.toggle("on", x.dataset.v === "daily")); } drawAll(); }));
        wireChips(el, "tawl", show, drawAll);
        $("#tawm", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) { setMode(x.dataset.v); load(); } });
        $("#tawtf", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; tf = x.dataset.v; $$("#tawtf button", el).forEach((y) => y.classList.toggle("on", y === x)); drawAll(); });
        $("#tpsv", el).addEventListener("click", () => MX.saveChart({ symbol, timeframe: tf === "5min" ? "5min" : "daily", days: tf === "5min" ? 3 : 120, source: `TA workspace (${mode})`, tags: ["ta"] }));
        paperButton(el, symbol, d);
        // TA agent: explain + chat; [E1, E6] in answers are clickable
        const linkIds = (t) => esc(t).replace(/\[((?:E\d+)(?:\s*,\s*E\d+)*)\]/g, (_, ids) => "[" + ids.split(/\s*,\s*/).map((x) => `<button type="button" class="evlink" data-id="${x}">${x}</button>`).join(", ") + "]");
        const wireLinks = (box) => $$(".evlink", box).forEach((a) => a.addEventListener("click", () => { const r = $(`.ev-row[data-id="${a.dataset.id}"]`, el); r ? r.click() : null; }));
        $("#tpx", el).addEventListener("click", async () => { const b = $("#tpx", el); b.disabled = true;
          try { const r = await MX.post("tradeplan/explain", { symbol, mode }); const out = await MX.pollTask(r.task, (t) => ($("#tpxs", el).textContent = t.msg || "…"));
            $("#tpxo", el).innerHTML = linkIds(out.text); wireLinks($("#tpxo", el)); $("#tpxs", el).textContent = out.writer === "model" ? "local model, numbers checked" : "template"; }
          catch (e) { $("#tpxs", el).textContent = e.message; } b.disabled = false; });
        const ck = `mx-tp-chat-${symbol}-${mode}`, hist = (() => { try { return JSON.parse(sessionStorage.getItem(ck) || "[]"); } catch (e) { return []; } })();
        const drawChat = () => { const h = $("#tpch", el); h.innerHTML = hist.map((x) => `<div class="small" style="margin:6px 0"><b>You:</b> ${esc(x.q)}</div><div class="small" style="white-space:pre-line;margin:0 0 8px 10px"><b>TA:</b> ${linkIds(x.a)}</div>`).join(""); wireLinks(h); };
        drawChat();
        const ask = async () => { const qi = $("#tpq", el), q = qi.value.trim(); if (!q) return; $("#tpqa", el).disabled = true;
          try { const r = await MX.post("tradeplan/ask", { symbol, mode, question: q, history: hist.map(({ q, a }) => ({ q, a })) }); const out = await MX.pollTask(r.task, (t) => ($("#tpxs", el).textContent = t.msg || "…"));
            hist.push({ q, a: out.text, w: out.writer }); try { sessionStorage.setItem(ck, JSON.stringify(hist.slice(-10))); } catch (e) { /* ignore */ } qi.value = ""; drawChat(); $("#tpxs", el).textContent = ""; }
          catch (e) { $("#tpxs", el).textContent = e.message; } $("#tpqa", el).disabled = false; };
        $("#tpqa", el).addEventListener("click", ask); $("#tpq", el).addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
        $("#tpqc", el).addEventListener("click", () => { hist.length = 0; try { sessionStorage.removeItem(ck); } catch (e) { /* ignore */ } drawChat(); });
      };
      await load();
    },
  });

  MX.planView = planView;
})();
