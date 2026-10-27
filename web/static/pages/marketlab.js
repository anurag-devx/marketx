/* Market Lab: Move Radar, Stock analysis (influence map), Scorecard, Debate (classic). Data: /api/marketlab/*. */
(function () {
  const { api, post, esc, num, pct, cls, day, $, $$, head, note, card, dataTable, sections, startJob, qs, md, spinner } = MX;
  const S = (() => { try { return JSON.parse(sessionStorage.getItem("mx-ml") || "{}"); } catch (e) { return {}; } })();
  const st = Object.assign({ tab: "radar", when: "close", move: "cc", size: "1", sym: null, sector: "" }, S);
  const save = () => { try { sessionStorage.setItem("mx-ml", JSON.stringify(st)); } catch (e) { /* ignore */ } };
  const P = (x, d = 0) => (x == null ? "—" : `${num(100 * x, d)}%`);
  const leanHtml = (l) => `<span style="white-space:nowrap" class="${l === "up" ? "up" : l === "down" ? "down" : "muted"}">${l === "up" ? "▲ up-lean" : l === "down" ? "▼ down-lean" : "— unclear"}</span>`;
  const col = (d) => d > 0 ? "var(--up)" : d < 0 ? "var(--down)" : "var(--faint)";
  const ORDER = ["Results", "Volume", "Price action", "Market", "Macro", "Sector", "Volatility", "News", "Filings"];
  const sortNodes = (ns) => [...ns].sort((a, b) => ORDER.indexOf(a.node) - ORDER.indexOf(b.node));

  // ▲ green (up) · ▼ red (down) · • grey (neutral) wherever these marks appear in text
  const arr = (t) => esc(t || "").replace(/▲/g, `<span class="up">▲</span>`).replace(/▼/g, `<span class="down">▼</span>`).replace(/•/g, `<span class="muted">•</span>`);
  const mv = (v, d = 1) => v == null ? "—" : `<span class="${v > 0.003 ? "up" : v < -0.003 ? "down" : "muted"}">${v > 0.003 ? "▲" : v < -0.003 ? "▼" : "•"} ${v > 0 ? "+" : ""}${num(100 * v, d)}%</span>`;
  function mini(nodes) {
    const ns = sortNodes(nodes || []), n = ns.length || 1;
    if (!nodes || !nodes.length) return `<span class="muted">—</span>`;
    const tip = ns.map((x) => `${x.node} ${x.dir > 0 ? "▲" : x.dir < 0 ? "▼" : "•"} ${["", "weak", "medium", "strong"][x.strength]}`).join("\n");
    return `<svg width="46" height="46" viewBox="0 0 54 54" role="img" aria-label="${esc(tip.replace(/\n/g, ", "))}"><title>${esc(tip)}</title>${ns.map((x, i) => { const a = (i / n) * 2 * Math.PI - Math.PI / 2, px = 27 + 21 * Math.cos(a), py = 27 + 21 * Math.sin(a);
      return `<line x1="27" y1="27" x2="${px}" y2="${py}" stroke="${col(x.dir)}" stroke-width="${0.6 + x.strength * 0.9}"/><circle cx="${px}" cy="${py}" r="${2 + x.strength}" fill="${col(x.dir)}"/>`; }).join("")}
      <circle cx="27" cy="27" r="7" fill="var(--accent)"/></svg>`;
  }

  function drawMap(host, d, onPick) {
    const W = 660, H = 470, cx = W / 2, cy = H / 2, R = 182, ns = sortNodes(d.nodes), n = ns.length, v = d.verdict;
    let h = "";
    ns.forEach((x, i) => { const a = (i / n) * 2 * Math.PI - Math.PI / 2; x.px = cx + R * Math.cos(a); x.py = cy + R * Math.sin(a) * 0.92;
      h += `<line x1="${cx}" y1="${cy}" x2="${x.px}" y2="${x.py}" stroke="${col(x.dir)}" stroke-width="${1.5 + x.strength * 2.2}" stroke-dasharray="${x.tested ? "" : "7 5"}" opacity=".85"/>`; });
    ns.forEach((x, i) => { h += `<g class="mlnode" data-i="${i}" tabindex="0" role="button" aria-label="${esc(x.node)}"><circle cx="${x.px}" cy="${x.py}" r="${30 + x.strength * 3}" fill="var(--surface)" stroke="${col(x.dir)}" stroke-width="2"/>
      <text x="${x.px}" y="${x.py + 4}" text-anchor="middle" font-weight="600" font-size="11">${esc(x.node)}</text></g>`; });
    const lc = v.lean === "up" ? "var(--up)" : v.lean === "down" ? "var(--down)" : "var(--muted)";
    h += `<circle cx="${cx}" cy="${cy}" r="82" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="2"/>
      <text x="${cx}" y="${cy - 24}" text-anchor="middle" font-weight="700" font-size="14">${esc(d.symbol)}</text>
      <text x="${cx}" y="${cy + 2}" text-anchor="middle" font-size="24" font-weight="700">${Math.round(100 * v.chance)}%</text>
      <text x="${cx}" y="${cy + 20}" text-anchor="middle" font-size="10.5" style="fill:var(--muted)">chance ≥${d.size}% · usual ${Math.round(100 * v.usual)}%</text>
      <text x="${cx}" y="${cy + 40}" text-anchor="middle" font-weight="600" style="fill:${lc}">${v.lean === "up" ? "▲ up-lean" : v.lean === "down" ? "▼ down-lean" : "— unclear"} · ${esc(v.confidence)}</text>`;
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" class="mlmap">${h}</svg>`;
    $$(".mlnode", host).forEach((g) => { const go = () => onPick(ns[+g.dataset.i]); g.addEventListener("click", go); g.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); });
  }

  function nodePanel(x, d) {
    const dir = x.dir > 0 ? "Pushes the lean up" : x.dir < 0 ? "Pushes the lean down" : "No clear push on direction";
    const t = x.test;
    return `<h3 style="margin:0 0 4px"><span style="color:${col(x.dir)}">●</span> ${esc(x.node)}</h3>
      <div class="small">${dir} · ${["", "weak", "medium", "strong"][x.strength]} · ${x.tested ? "<b>tested: dropping it made the past-years test worse</b>" : "<i>not shown to help on past years (dashed)</i>"}</div>
      <p style="margin:8px 0">Today: ${esc(x.text)}</p>
      <dl class="kv"><dt>Effect on the chance</dt><dd>${x.d_chance >= 0 ? "+" : ""}${num(100 * x.d_chance, 1)} pts</dd><dt>Effect on the up-share</dt><dd>${x.d_up >= 0 ? "+" : ""}${num(100 * x.d_up, 1)} pts</dd></dl>
      <p class="muted small">How: the match was re-run with this node set to a typical day; the difference is its push.</p>
      <details class="fold"><summary>Values used</summary><div class="small" style="padding:0 14px 10px">${x.values.map((y) => `<div>${esc(y.label)}: <b>${y.value ?? "—"}</b></div>`).join("")}</div></details>
      ${t ? `<p class="muted small">Past-years test: without it, chance skill ${t.d_brier >= 0 ? "falls" : "rises"} by ${num(Math.abs(t.d_brier), 4)}; direction hit ${t.d_dir >= 0 ? "falls" : "rises"} by ${num(100 * Math.abs(t.d_dir), 1)} pts.</p>` : ""}`;
  }

  function selectors(d) {
    const seg = (key, opts, label) => `<div><div class="mllab">${label}</div><div class="seg" data-k="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${String(st[key]) === String(v) ? "on" : ""}">${esc(l)}</button>`).join("")}</div></div>`;
    return `<div class="card"><div class="row" style="gap:18px;align-items:flex-end">
      ${seg("when", [["close", "After close"], ["pre", "Before open"], ["intra", "During the day"]], "When")}
      ${seg("move", [["cc", "Close-to-close"], ["oc", "Open-to-close"], ["gp", "Overnight gap"]], "Move")}
      ${seg("size", [["0.5", "≥0.5%"], ["1", "≥1%"], ["2", "≥2%"], ["3", "≥3%"]], "Size")}
      ${d && d.sectors ? `<div><div class="mllab">Sector</div><select class="in" id="mlsec"><option value="">All</option>${d.sectors.map((x) => `<option ${x === st.sector ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></div>` : ""}</div></div>`;
  }

  let RETRO = false;
  async function radarTab(b, go) {
    b.innerHTML = spinner("Loading tonight's radar …");
    const d = await api(`marketlab/radar?${qs({ when: st.when, move: st.move, size: st.size, day: st.vday || undefined })}`);
    const tp = await api(`tradeplan/summary?${qs({ mode: st.move === "oc" ? "intraday" : "swing", day: st.vday || undefined })}`).catch(() => ({ rows: {} }));
    b.innerHTML = selectors(d) + `<div id="mlr" class="section"></div>`;
    $$(".seg[data-k]", b).forEach((sg) => sg.addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; st[sg.dataset.k] = x.dataset.v; save(); radarTab(b, go); }));
    $("#mlsec", b)?.addEventListener("change", (e) => { st.sector = e.target.value; save(); radarTab(b, go); });
    const host = $("#mlr", b);
    if (!d.ready) { host.innerHTML = note(`⏳ ${esc(d.why)}`, "warn") + (st.when === "close" ? `<div class="row section"><button class="btn primary" id="mlb">Build tonight's radar</button></div>` : "");
      $("#mlb", b)?.addEventListener("click", () => startJob("market-lab")); return; }
    const rows = d.rows.filter((r) => !st.sector || r.sector === st.sector).map((r) => { const t = tp.rows[r.symbol] || {};
      return { ...r, chance_pct: 100 * r.chance, usual_pct: 100 * r.usual, edge: 100 * r.lift, plan: t.ok == null ? null : t.ok ? t.act : "no trade", plan_why: t.why, rr: t.rr, second: t.ta ? `${t.ta}|${t.fund || "no data"}` : null, second_why: t.second_why }; });
    host.innerHTML = note(`Radar for ${esc(day(d.day))} → next session · ${esc(d.moves[d.move])} · at least ${esc(d.size)}% · status: <b>${esc(d.status)}</b>.
      Sorted by edge: the chance minus the stock's usual chance. ${RETRO ? "Click a row for its influence map." : "Click a row to open the stock in Stock Desk (new tab)."}`, d.status === "tested" ? "info" : "warn") + (d.day_note ? note(esc(d.day_note), "warn") : "") + `<div class="card flush section" id="mlt"></div>`;
    $("#mlt", host).appendChild(dataTable(rows, [
      ["symbol", "Stock", "sym"], ["nodes", "Map", (v) => mini(v)],
      ["chance_pct", `Chance ≥${d.size}%`, (v, r) => `<span class="mlbar"><i style="width:${Math.min(100, v)}%"></i><b style="left:${Math.min(100, r.usual_pct)}%"></b></span><b>${num(v, 0)}%</b>`],
      ["usual_pct", "Usual", "pct0"], ["edge", "Edge (pts)", "snum1"], ["lean", "Lean", (v) => leanHtml(v)], ["confidence", "Confidence", "tag"],
      ["n_big", "Cases that big", "int"], ["plan", "Plan", (v, r) => v == null ? "—" : v === "no trade" ? `<span title="${esc(r.plan_why || "")}" class="muted">no trade</span>`
        : `<b style="white-space:nowrap">${v.split(" / ").map((x) => x === "BUY" ? `<span class="up">▲ BUY</span>` : `<span class="down">▼ SHORT</span>`).join(" / ")}</b>`], ["rr", "R:R", "num1"],
      ["second", "TA · Fund.", (v, r) => { if (!v) return "—"; const [a, f] = v.split("|"), ic = (x) => x === "up" ? `<span class="up">▲</span>` : x === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
        return `<span title="${esc(r.second_why || "")}" style="white-space:nowrap">${ic(a)} TA · ${ic(f)} Fund.</span>`; }],
      ["why", "Biggest drivers", (v) => `<span class="small">${arr(v)}</span>`], ["sector", "Sector"]],
      { csv: `market_lab_${d.day}_${d.move}_${d.size}`, sort: "edge", desc: true, per: 25, show: ["symbol", "nodes", "chance_pct", "usual_pct", "edge", "lean", "confidence", "plan", "rr", "second", "why"], onRow: (r) => { if (RETRO) { st.sym = r.symbol; save(); go("stock"); } else window.open(`#/desk/${encodeURIComponent(r.symbol)}`, "_blank", "noopener"); }, chart: { x: "usual_pct", y: "chance_pct" } }));
    host.insertAdjacentHTML("beforeend", `<p class="muted small">Bar = chance tomorrow; black tick = the stock's usual chance on any day. Only rows where the bar clearly passes the tick are interesting. Map: green pushes up, red pushes down, grey no clear push.</p>`);
  }

  async function stockTab(b) {
    if (!st.sym) { b.innerHTML = note("Pick a stock in the Move Radar, or type one:") + `<div class="row section"><input class="in" id="mls" placeholder="e.g. TCS"><button class="btn primary" id="mlgo">Open</button></div>`;
      $("#mlgo", b).addEventListener("click", () => { st.sym = $("#mls", b).value.trim().toUpperCase(); save(); stockTab(b); });
      MX.suggest($("#mls", b), { kinds: "stocks", recent: "stock", onPick: (r) => { st.sym = r.value; save(); stockTab(b); } }); return; }
    b.innerHTML = spinner(`Loading ${st.sym} …`);
    let d;
    try { d = await api(`marketlab/stock?${qs({ symbol: st.sym, move: st.move, size: st.size, day: st.vday || undefined })}`); }
    catch (e) { b.innerHTML = note(esc(e.message), "warn"); return; }
    const v = d.verdict;
    b.innerHTML = `<div class="row" style="justify-content:space-between"><div><h2 style="margin:0">${esc(d.symbol)} <a class="small" href="#/stock/${encodeURIComponent(d.symbol)}">stock page →</a> <a class="small" href="#/desk/${encodeURIComponent(d.symbol)}" target="_blank" rel="noopener">Stock Desk ↗</a></h2>
        <div class="muted small">${esc(day(d.day))} → next session · ${esc(d.move_label)} · at least ${esc(d.size)}% · ${esc(d.sector || "")} · ${esc(d.status)}</div></div>
        <div class="row"><input class="in" id="mls" placeholder="Another stock" style="width:140px"><button class="btn small" id="mlgo">Open</button></div></div>
      <div class="split section" style="grid-template-columns:minmax(0,1fr) 320px"><div class="card"><div id="mlmap"></div>
        <div class="legend"><span><i style="background:var(--up)"></i>pushes up</span><span><i style="background:var(--down)"></i>pushes down</span><span><i style="background:var(--faint)"></i>no clear push</span>
        <span>thicker = stronger</span><span>dashed = not shown to help on past years</span></div></div>
        <div class="card" id="mlpanel"><h3 style="margin:0 0 6px">Click a node</h3><p class="muted small">Each node is one group of facts about today. Its colour and thickness come from re-running the match without it, never from the AI.</p>
          <dl class="kv"><dt>Chance</dt><dd><b>${P(v.chance)}</b> (usual ${P(v.usual)}, edge ${v.lift >= 0 ? "+" : ""}${num(100 * v.lift, 1)} pts)</dd><dt>Lean</dt><dd>${leanHtml(v.lean)} · ${num(100 * v.up_share, 0)}% of big moves were up${v.direction_tested ? "" : `<div class="muted small">Direction hasn't beaten a coin flip on past years for this combination, so no lean is shown. Node colours are hints only.</div>`}</dd>
          <dt>Confidence</dt><dd>${esc(v.confidence)}</dd><dt>Cases that big</dt><dd>${v.n_big} of 150 closest</dd></dl></div></div>
      <details class="fold section" open id="mltp"><summary>Technical analysis (secondary: buy / short plan, levels, candles, fundamentals)</summary><div id="mltpb" style="padding:0 14px 14px"></div></details>
      <details class="fold section" open><summary>Explain the past cases (AI, optional)</summary><div style="padding:0 14px 14px"><button class="btn" id="mlx">Explain with past cases</button> <span class="muted small" id="mlxs"></span><div id="mlxo"></div></div></details>
      <details class="fold section"><summary>Closest past cases (${d.cases.length})</summary><div id="mlc" style="padding:0 14px 14px"></div></details>
      <details class="fold section"><summary>Outcome range of the 150 closest cases</summary><div id="mlo" style="padding:0 14px 14px"></div></details>
      <details class="fold section"><summary>Today's evidence: ${d.evidence.news.length} headlines, ${d.evidence.filings.length} filings</summary><div id="mle" style="padding:0 14px 14px"></div></details>`;
    drawMap($("#mlmap", b), d, (x) => ($("#mlpanel", b).innerHTML = nodePanel(x, d)));
    MX.planView($("#mltpb", b), d.symbol, d.past ? d.day : undefined);
    $("#mlgo", b).addEventListener("click", () => { const s = $("#mls", b).value.trim().toUpperCase(); if (s) { st.sym = s; save(); stockTab(b); } });
    MX.suggest($("#mls", b), { kinds: "stocks", recent: "stock", onPick: (r) => { st.sym = r.value; save(); stockTab(b); } });
    $("#mlc", b).appendChild(dataTable(d.cases, [["rank", "#", "int"], ["symbol", "Stock", "symonly"], ["date", "Date", "date"], ["move", `Next ${d.move_label} %`, "snum2"],
      ["cc", "Close-to-close %", "snum2"], ["oc", "Open-to-close %", "snum2"], ["gp", "Gap %", "snum2"], ["distance", "Distance", "num2"]], { tools: false }));
    if (d.spread) { const s = d.spread;
      $("#mlo", b).innerHTML = `<div class="ch"></div><p class="muted small">Middle half of outcomes between ${pct(s.p25, 1)} and ${pct(s.p75, 1)}; worst 10% below ${pct(s.p10, 1)}, best 10% above ${pct(s.p90, 1)}. ${num(100 * s.up_share, 0)}% ended up.</p>`;
      requestAnimationFrame(() => MXCharts.hbar($(".ch", $("#mlo", b)), { labels: ["Worst 10%", "Lower quarter", "Median", "Upper quarter", "Best 10%"], values: [s.p10, s.p25, s.p50, s.p75, s.p90], decimals: 2 })); }
    const ev = d.evidence;
    $("#mle", b).innerHTML = (ev.news.length ? MX.newsList(ev.news.map((n) => ({ ...n, symbols: [] }))) : `<p class="muted">No headlines naming the stock in tonight's window.</p>`) +
      (ev.filings.length ? `<div class="list">${ev.filings.map((f) => `<div class="item"><span class="t">${esc(f.subject)}</span><div class="m"><span class="tag">${esc(f.category)}</span><span>${esc(f.published)}</span>${MX.safeUrl(f.link) ? `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">open</a>` : ""}</div></div>`).join("")}</div>` : `<p class="muted">No filings besides routine compliance.</p>`);
    $("#mlx", b).addEventListener("click", async () => {
      $("#mlx", b).disabled = true;
      try { const r = await post("marketlab/explain", { symbol: d.symbol, move: d.move, size: d.size });
        const out = await MX.pollTask(r.task, (t) => ($("#mlxs", b).textContent = t.msg || ""));
        $("#mlxs", b).textContent = out.writer === "model" ? "written by the local model from the facts above (numbers checked)" : "template (model off or its numbers didn't check out)";
        $("#mlxo", b).innerHTML = md(out.text); }
      catch (e) { $("#mlxs", b).textContent = e.message; }
      $("#mlx", b).disabled = false;
    });
  }

  async function scoreTab(b) {
    b.innerHTML = `<div class="row section"><button class="btn primary" id="mlt1">Run the Scorecard test</button><button class="btn" id="mlb">Build tonight's radar</button>
      <span class="muted small">The test re-runs every combination on past years it never saw (about 20–40 min).</span></div><div id="mls"></div>`;
    $("#mlt1", b).addEventListener("click", () => startJob("market-lab-test"));
    $("#mlb", b).addEventListener("click", () => startJob("market-lab"));
    sections($("#mls", b), (await api("marketlab/score")).sections);
    b.insertAdjacentHTML("beforeend", `<div class="card section" id="mlcand"></div>`);
    const drawCand = async () => {
      const host = $("#mlcand", b); if (!host) return;
      let c; try { c = await api("marketlab/candidates"); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
      host.innerHTML = `<div class="ct"><h2>Candidate signals</h2><span class="aside muted small">tested before they may enter the forecasts${c.built ? ` · test of ${esc(c.built.slice(0, 16))}` : ""}</span></div>
        <p class="small muted">Built into every night's scenario but not used in the chances until the test below shows they help. Data comes from NSE: bhavcopy (delivery) and results filings (valuation), point-in-time.</p>
        ${MX.table(c.items, [["node", "Group", (v, r) => `<b>${esc(v)}</b><div class="muted small">${r.features.map(esc).join(" · ")}</div>`],
          ["tested", "Test", (v, r) => !v ? (r.skipped ? `<span class="muted">not enough data yet</span>` : `<span class="muted">not run</span>`)
            : r.results.map((x) => `<div class="small">${esc(x.move)}: ${x.helps && x.years >= r.min_years ? `<span class="up">helps</span>` : x.helps ? `<span class="muted">too few years (${x.years} of ${r.min_years} needed)</span>` : `<span class="muted">no help</span>`} <span class="muted">chance ${x.d_brier >= 0 ? "+" : ""}${num(x.d_brier, 4)} · direction ${x.d_dir >= 0 ? "+" : ""}${num(100 * x.d_dir, 1)} pts · ${x.years} yrs · data on ${num(100 * x.coverage, 0)}% of rows</span></div>`).join("")],
          ["active", "In forecasts", (v, r) => v ? `${r.helps ? `<span class="up">✓ in use</span>` : `<span class="down">⚠ in use without a passing test</span>`} <button class="btn small mlcand" data-n="${esc(r.node)}" data-on="0">Stop using</button>`
            : r.helps ? `<button class="btn small primary mlcand" data-n="${esc(r.node)}" data-on="1">Use in forecasts</button>` : `<span class="muted">no</span>`]])}
        <div class="row section"><button class="btn" id="mlcandrun">Run the candidate test</button><span class="muted small">about 10–20 min on this Mac · needs the bhavcopy history and results history downloaded first (Data health)</span></div>`;
      $("#mlcandrun", host).addEventListener("click", () => startJob("market-lab-candidates"));
      $$(".mlcand", host).forEach((x) => x.addEventListener("click", async () => { try { const r = await post("marketlab/candidates", { node: x.dataset.n, on: x.dataset.on === "1" }); MX.toast(r.note); drawCand(); } catch (e) { /* toast */ } }));
    };
    drawCand();
  }

  /* ---------- Review: graded calls, calibration, plan results, intraday profile ---------- */
  const rv = Object.assign({ kind: "all", days: 21 }, (() => { try { return JSON.parse(sessionStorage.getItem("mx-ml-rv") || "{}"); } catch (e) { return {}; } })());
  const rvSave = () => { try { sessionStorage.setItem("mx-ml-rv", JSON.stringify(rv)); } catch (e) { /* ignore */ } };
  const P1 = (x) => (x == null ? "—" : `${num(100 * x, 1)}%`);
  const passHtml = (v) => v == null ? `<span class="muted">waiting</span>` : v ? `<span class="up">✓ pass</span>` : `<span class="down">✗ fail</span>`;

  async function reviewTab(b) {
    const seg = (key, opts, label, obj) => `<div><div class="mllab">${label}</div><div class="seg" data-k="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${String(obj[key]) === String(v) ? "on" : ""}">${esc(l)}</button>`).join("")}</div></div>`;
    b.innerHTML = `<div class="card"><div class="row" style="gap:18px;align-items:flex-end;flex-wrap:wrap">
        ${seg("kind", [["all", "All"], ["live", "Live"], ["replay", "Replay"]], "Calls", rv)}
        ${seg("move", [["cc", "Close-to-close"], ["oc", "Open-to-close"], ["gp", "Overnight gap"]], "Move", st)}
        ${seg("size", [["0.5", "≥0.5%"], ["1", "≥1%"], ["2", "≥2%"], ["3", "≥3%"]], "Size", st)}
        <div><div class="mllab">Replay past days</div><div class="row" style="gap:6px"><input class="in" id="rvd" type="number" min="1" max="250" value="${rv.days}" style="width:70px"> <button class="btn" id="rvgo">Replay</button></div></div></div>
        <p class="muted small" style="margin:8px 0 0">Live = what the radar said each night (kept automatically from now on). Replay = the radar rebuilt for past days from data up to each day (about 1 min per day on this Mac).</p></div>
      <div id="rvchat" class="section"></div><div id="rvb">${spinner("Loading the review …")}</div>`;
    MX.gradesChat && MX.gradesChat($("#rvchat", b), () => ({ move: st.move, size: st.size }));
    $$(".seg", b).forEach((g) => g.addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; const k = g.dataset.k;
      if (k === "kind") { rv.kind = x.dataset.v; rvSave(); } else { st[k] = x.dataset.v; save(); } reviewTab(b); }));
    $("#rvgo", b).addEventListener("click", () => { rv.days = Math.max(1, Math.min(250, +$("#rvd", b).value || 21)); rvSave(); startJob("market-lab-replay", [`days=${rv.days}`]); });
    const d = await api(`marketlab/review?${qs({ kind: rv.kind, move: st.move, size: st.size })}`);
    const host = $("#rvb", b);
    if (!d.ready) { host.innerHTML = note(esc(d.why), "info"); return; }
    const kp = `<div class="grid g5 section">${d.kpis.map((k) => `<div class="card kpi"><div class="label">${esc(k.label)}</div><div class="value">${esc(k.value ?? "—")}</div><div class="muted small">${esc(k.note || "")}</div></div>`).join("")}</div>`;
    const pl = d.plans ? `<div class="card section"><div class="ct"><h2>Trade plans (${esc(d.plans.mode)})</h2><span class="aside muted small">E1 entry, stop first when a bar touches both, E2 not graded yet</span></div>
      <p>${d.plans.plans} plans · entered ${d.plans.entered} · no entry ${d.plans.no_entry}${d.plans.ambiguous ? ` · both sides in one bar ${d.plans.ambiguous}` : ""} · exits: ${Object.entries(d.plans.exits).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "—"}</p>
      <p>All entered: average <b>${d.plans.avg_r ?? "—"} R</b>, winners ${P1(d.plans.win)} · plans that passed the checks (${d.plans.tradeable_n}): average <b>${d.plans.tradeable_avg_r ?? "—"} R</b>, winners ${P1(d.plans.tradeable_win)}</p></div>` : "";
    const pp = d.paper;
    const ppHtml = pp ? `<div class="card section"><div class="ct"><h2>Your paper takes</h2><span class="aside muted small">graded on the same rules as every plan (live days)</span></div>
      <p>${pp.n} taken · ${pp.done} finished · average <b>${pp.avg_r ?? "—"} R</b>, winners ${P1(pp.win)}, P&L <b>${pp.pnl_rs == null ? "—" : `₹${num(pp.pnl_rs, 0)}`}</b> · all plans: ${pp.all_avg_r ?? "—"} R</p>
      ${MX.table(pp.rows, [["day", "Day", "date"], ["symbol", "Stock"], ["mode", "Mode", "tag"], ["lean", "Lean", (v) => leanHtml(v)], ["status", "Status", (v) => esc(v || "waiting")],
        ["side", "Side", (v) => v === "long" ? `<span class="up">▲ BUY</span>` : v === "short" ? `<span class="down">▼ SHORT</span>` : "—"], ["exit", "Exit"], ["r", "R", "snum2"], ["pnl_rs", "P&L ₹", "snum2"]])}</div>` : "";
    host.innerHTML = (d.note ? note(esc(d.note), "warn") : "") + kp + ppHtml + pl +
      `<div class="grid g2 section"><div class="card"><div class="ct"><h2>Did the chances hold?</h2><span class="aside muted small">all stocks, graded</span></div><div id="rvcal"></div>
        <p class="muted small">"Said" = the average chance in that band; "happened" = how often the move came. Close = well calibrated.</p></div>
        <div class="card"><div class="ct"><h2>Picks by confidence</h2></div><div id="rvconf"></div></div></div>
       <div class="card section"><div class="ct"><h2>Second opinion: does TA or fundamentals help?</h2><span class="aside muted small">labels only until these numbers show a real difference</span></div><div id="rvsec"></div></div>
       <div class="card section"><div class="ct"><h2>Misses by sector</h2><span class="aside muted small">picks per sector, the sector's median move and the ${st.move === "cc" ? "Nifty" : "market (median stock)"} that session</span></div><div id="rvsecs"></div></div>
       <div class="card flush section"><div class="ct" style="padding:12px 14px 0"><h2>Day by day</h2></div><div id="rvday"></div></div>
       <div class="card flush section"><div class="ct" style="padding:12px 14px 0"><h2>Picks: pass / fail</h2><span class="aside muted small">click a row for its intraday profile</span></div><div id="rvt"></div></div>
       <div id="rvp"></div>`;
    $("#rvcal", host).innerHTML = MX.table(d.calibration, [["bin", "Chance band"], ["n", "Calls", "int"], ["said", "Said", "pct1x"], ["happened", "Happened", "pct1x"]].map((c) => c[2] === "pct1x" ? [c[0], c[1], (v) => P1(v)] : c));
    $("#rvconf", host).innerHTML = d.by_conf.length ? MX.table(d.by_conf, [["confidence", "Confidence", "tag"], ["n", "Picks", "int"], ["hit", "Moved", (v) => P1(v)], ["usual", "Their usual", (v) => P1(v)]]) : `<p class="muted">No graded picks yet.</p>`;
    $("#rvsecs", host).innerHTML = (d.sectors || []).length ? d.sectors.map((x, i) => `<details class="fold" ${i === 0 ? "open" : ""}><summary><b>${esc(day(x.day))}</b> <span class="tag">${esc(x.kind)}</span> · ${arr(x.verdict)}
        <span class="muted small"> · ${x.nifty != null ? "Nifty" : "market"} ${mv(x.nifty ?? x.market)}</span></summary><div style="padding:0 14px 10px">
        ${MX.table(x.rows, [["sector", "Sector"], ["picks", "Picks", "int"], ["hits", "Moved", "int"], ["misses", "Missed", (v) => v ? `<span class="down">${v}</span>` : "0"], ["move", "Sector move", (v) => mv(v)]])}</div></details>`).join("")
      : `<p class="muted">No graded days yet.</p>`;
    const sec = d.second;
    $("#rvsec", host).innerHTML = !sec ? `<p class="muted">Graded days from before the labels existed: replay them to get the labels.</p>` :
      `<div class="grid g3"><div><h3>TA vs Market Lab's direction</h3>${MX.table(sec.ta, [["label", "TA", "tag"], ["n", "Calls", "int"], ["dir_right", "Market Lab's way", (v) => P1(v)], ["dir_right_low", "…when confidence low", (v) => P1(v)]])}</div>
        <div><h3>Fundamentals vs direction</h3>${MX.table(sec.fund, [["label", "Fundamentals", "tag"], ["n", "Calls", "int"], ["dir_right", "Market Lab's way", (v) => P1(v)], ["dir_right_low", "…when confidence low", (v) => P1(v)]])}</div>
        <div><h3>Chart setup vs size</h3>${MX.table(sec.setup, [["setup", "Setup"], ["n", "Stocks", "int"], ["hit", "Moved", (v) => P1(v)], ["edge", "vs usual", (v) => v == null ? "—" : `${v >= 0 ? "+" : ""}${num(100 * v, 1)} pts`]])}</div></div>
       <p class="muted small">"Market Lab's way" = the move went the way Market Lab's past cases leaned (above or below 50%), whether or not a lean was shown. If "agrees" isn't clearly above "disagrees" over many days, the label adds nothing.</p>`;
    $("#rvday", host).appendChild(dataTable(d.by_day, [["day", "Day", "date"], ["kind", "Calls", "tag"], ["status", "Status", "tag"], ["picks", "Picks", "int"], ["hit", "Picks moved", (v) => P1(v)],
      ["usual", "Their usual", (v) => P1(v)], ["base", "All stocks", (v) => P1(v)], ["leans", "Lean calls", "int"], ["lean_right", "Lean right", (v) => P1(v)]], { per: 10, tools: false }));
    const prof = st.move !== "gp";
    $("#rvt", host).appendChild(dataTable(d.rows.map((r) => ({ ...r, chance_pct: 100 * r.chance, usual_pct: 100 * r.usual, edge: 100 * r.lift, ret_pct: r.ret == null ? null : 100 * r.ret })),
      [["day", "Day", "date"], ["kind", "Calls", "tag"], ["symbol", "Stock"], ["nodes", "Map", (v) => mini(v)], ["chance_pct", "Chance", "pct0"], ["usual_pct", "Usual", "pct0"], ["edge", "Edge (pts)", "snum2"],
       ["lean", "Lean", (v) => leanHtml(v)], ["confidence", "Confidence", "tag"], ["ret_pct", "Actual", (v) => mv(v == null ? null : v / 100, 2)], ["hit", "Result", (v) => passHtml(v)], ["lean_right", "Lean right", (v) => v == null ? "—" : v ? "✓" : "✗"],
       ...(prof ? [["n_cross", "Crossings", "int"], ["minutes_beyond", "Minutes beyond", "int"], ["first_cross", "First cross"], ["held_to_close", "Held to close", (v) => v == null ? "—" : v ? "yes" : "no"]] : []),
       ["plan_exit", "Plan exit"], ["plan_r", "Plan R", "snum2"], ["why", "Biggest drivers", (v) => v ? `<span class="small">${arr(v)}</span>` : "—"], ["sector", "Sector"]],
      { per: 25, csv: `market_lab_review_${d.move}_${d.size}`, show: ["day", "symbol", "nodes", "chance_pct", "edge", "lean", "confidence", "ret_pct", "hit", ...(prof ? ["n_cross", "minutes_beyond", "first_cross"] : []), "plan_exit", "plan_r", "why"],
        onRow: (r) => prof && profileView($("#rvp", host), r.symbol, r.day) }));
  }

  async function profileView(host, symbol, dayStr, thr, band) {
    thr = thr ?? +st.size; band = band ?? Math.round(80 * thr) / 100;
    host.innerHTML = `<div class="card section">${spinner("Loading the session …")}</div>`;
    host.scrollIntoView({ behavior: "smooth", block: "start" });
    const d = await api(`marketlab/profile?${qs({ symbol, day: dayStr, move: st.move, thr, band })}`);
    if (!d.ready) { host.innerHTML = `<div class="card section">${note(esc(d.why), "warn")}</div>`; return; }
    const p = d.profile, ref = p.ref;
    host.innerHTML = `<div class="card section"><div class="ct"><h2>${esc(symbol)} · ${esc(day(d.session))} · intraday profile (${st.move === "cc" ? "from yesterday's close" : "from today's open"})</h2>
        <span class="aside row" style="gap:6px">Threshold <input class="in" id="pft" type="number" step="0.1" min="0.1" value="${thr}" style="width:70px">%
        Re-cross band <input class="in" id="pfb" type="number" step="0.1" min="0" value="${band}" style="width:70px">% <button class="btn small" id="pfu">Update</button></span></div>
      <p><b>${p.n_cross}</b> crossing${p.n_cross === 1 ? "" : "s"} (▲ ${p.n_up} · ▼ ${p.n_down}) · <b>${p.minutes_beyond}</b> minutes beyond ±${num(thr, 1)}% · first cross ${p.first_cross ? `${esc(p.first_cross)} (${p.first_dir === "up" ? "▲" : "▼"})` : "—"} ·
        ${p.held_to_close ? `held to the close (${p.held_dir === "up" ? "▲" : "▼"})` : "did not hold to the close"} · best ${pct(p.best_pct / 100, 2)}, worst ${pct(p.worst_pct / 100, 2)}, close ${pct(p.close_pct / 100, 2)}</p>
      <div id="pfc"></div>
      ${p.spells.length ? MX.table(p.spells, [["dir", "Direction", (v) => v === "up" ? `<span class="up">▲ up</span>` : `<span class="down">▼ down</span>`], ["start", "From"], ["end", "To"], ["minutes", "Minutes", "int"]]) : `<p class="muted">Never reached ±${num(thr, 1)}%.</p>`}
      <p class="muted small">A crossing starts when a 5-minute bar reaches the threshold and ends when a bar closes back inside the re-cross band, so wobbles around the level count once.</p></div>`;
    const lv = [{ price: ref, color: "faint", label: "ref", dash: "2 4" }, { price: ref * (1 + thr / 100), color: "up", label: `+${thr}%` }, { price: ref * (1 - thr / 100), color: "down", label: `−${thr}%` },
      { price: ref * (1 + band / 100), color: "faint", dash: "3 5", width: 1 }, { price: ref * (1 - band / 100), color: "faint", dash: "3 5", width: 1 }];
    const vm = p.spells.map((x) => ({ i: d.bars.t.findIndex((t) => t.slice(11, 16) === x.start), label: `${x.dir === "up" ? "▲" : "▼"} ${x.minutes}m` }));
    requestAnimationFrame(() => MXCharts.priceChart($("#pfc", host), { ...d.bars, sma50: null, sma200: null }, { mode: "candle", height: 360, levels: lv.map((l) => ({ ...l, fit: true })), vmarks: vm }));
    $("#pfu", host).addEventListener("click", () => profileView(host, symbol, dayStr, +$("#pft", host).value, +$("#pfb", host).value));
  }

  // Market Lab = Move Radar + Scorecard. The full classic tab set (Stock analysis, Review, Debate) lives in Retro.
  const TABS_MAIN = [["radar", "Move Radar"], ["score", "Scorecard"]];
  const TABS_ALL = [["radar", "Move Radar"], ["stock", "Stock analysis"], ["review", "Review"], ["score", "Scorecard"], ["debate", "Debate (classic)"]];
  async function mlRender(el, arg, retro) {
      const TABS = retro ? TABS_ALL : TABS_MAIN;
      RETRO = !!retro;
      if (arg && retro) { st.sym = arg.toUpperCase(); st.tab = "stock"; save(); }
      if (arg && !retro) { location.hash = `#/desk/${encodeURIComponent(arg.toUpperCase())}`; return; }
      if (!TABS.some(([k]) => k === st.tab)) st.tab = "radar";
      el.innerHTML = head("Market Lab", "Which stocks are likely to move at least 1% next session, which way, and what's driving it. Built from the closest past situations; numbers never come from the AI.") +
        (retro ? note(`Market Lab classic (Retro): every tab, including Stock analysis and Review. The everyday Market Lab is <a href="#/market-lab">here</a>.`, "info") : "") +
        `<div class="tabs" id="mltabs">${TABS.map(([k, l]) => `<button data-t="${k}" class="${st.tab === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="mlb2"></div>`;
      $("#mltabs", el).insertAdjacentHTML("beforebegin", `<div id="mlasof" class="ml-asof"></div>`);
      const drawPast = async () => { const x = $("#mlpast", el); if (!x) return;
        let ds; try { ds = await api("marketlab/days"); } catch (e) { return; }
        if (st.vday && !ds.items.some((i) => i.day === st.vday)) { st.vday = null; save(); }
        const f = (v) => day(v, { weekday: "short", day: "numeric", month: "short" });
        x.innerHTML = `<label class="small muted">View night <select class="in" id="mlvday"><option value="">Latest (${f(ds.latest)})</option>${ds.items.filter((i) => i.day !== ds.latest)
          .map((i) => `<option value="${i.day}" ${st.vday === i.day ? "selected" : ""}>${f(i.day)}${i.kind === "replay" ? " · replayed" : ""}</option>`).join("")}</select></label>` +
          (st.vday ? ` <span class="tag warn">Viewing the radar and plans saved on ${f(st.vday)} (read-only)</span> <button class="btn small" id="mlvback">Back to latest</button>` : "");
        $("#mlvday", el).addEventListener("change", (e) => { st.vday = e.target.value || null; save(); drawPast(); if (st.tab === "radar" || st.tab === "stock") show(); });
        $("#mlvback", el)?.addEventListener("click", () => { st.vday = null; save(); drawPast(); show(); }); };
      $("#mlasof", el).insertAdjacentHTML("afterend", `<div id="mlpast" class="ml-asof"></div>`);
      drawPast();
      api("marketlab/asof").then((a) => { const x = $("#mlasof", el); if (!x) return;
        const f = (v) => v ? day(v, { weekday: "short", day: "numeric", month: "short", year: "numeric" }) : "—";
        x.innerHTML = !a.day ? `<span class="tag warn">No radar built yet</span>` : `<span class="ml-asof-k">Radar built from</span> <b>${f(a.day)}</b> close <span class="muted">→</span> <span class="ml-asof-k">for</span> <b>${f(a.for_day)}</b> <span class="muted small">(next weekday; holidays not counted)</span>` +
          (a.stale ? ` <span class="tag warn" title="Prices are newer than the radar">prices up to ${f(a.prices_last)}: radar out of date, build tonight's radar</span>` : ` <span class="tag">up to date with prices</span>`) + ` <a class="btn small" href="#/tally" target="_blank" rel="noopener">Tally: projected vs actual ↗</a>`; }).catch(() => {});
      const go = (t) => { st.tab = t; save(); $$("#mltabs button", el).forEach((x) => x.classList.toggle("on", x.dataset.t === t)); show(); };
      const show = async () => { const b = document.createElement("div"); $("#mlb2", el).replaceChildren(b);   // a slow tab left behind writes into its own detached box
        try {
          if (st.tab === "radar") await radarTab(b, go);
          else if (st.tab === "stock") await stockTab(b);
          else if (st.tab === "score") await scoreTab(b);
          else if (st.tab === "review") await reviewTab(b);
          else b.innerHTML = note("The original bull-and-bear debate, with statistical tests on every claim. It opens on its own page.") + `<div class="row section"><a class="btn primary" href="#/debate">Open the debate</a></div>`;
        } catch (e) { if (b.isConnected) b.innerHTML = note(esc(e.message), "warn"); } };
      $("#mltabs", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) go(x.dataset.t); });
      await show();
  }
  MX.page("market-lab", { title: "Market Lab", reloadOnJob: true, render: (el, arg) => mlRender(el, arg, false) });
  MX.page("market-lab-retro", { title: "Market Lab classic", reloadOnJob: true, render: (el, arg) => mlRender(el, arg, true) });
  MX.mlMap = drawMap; MX.mlMini = mini; MX.mlNodePanel = nodePanel;
})();
