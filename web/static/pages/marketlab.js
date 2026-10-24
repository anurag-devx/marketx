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

  function mini(nodes) {
    const ns = sortNodes(nodes || []), n = ns.length || 1;
    return `<svg width="46" height="46" viewBox="0 0 54 54" aria-hidden="true">${ns.map((x, i) => { const a = (i / n) * 2 * Math.PI - Math.PI / 2, px = 27 + 21 * Math.cos(a), py = 27 + 21 * Math.sin(a);
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

  async function radarTab(b, go) {
    b.innerHTML = spinner("Loading tonight's radar …");
    const d = await api(`marketlab/radar?${qs({ when: st.when, move: st.move, size: st.size })}`);
    b.innerHTML = selectors(d) + `<div id="mlr" class="section"></div>`;
    $$(".seg[data-k]", b).forEach((sg) => sg.addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; st[sg.dataset.k] = x.dataset.v; save(); radarTab(b, go); }));
    $("#mlsec", b)?.addEventListener("change", (e) => { st.sector = e.target.value; save(); radarTab(b, go); });
    const host = $("#mlr", b);
    if (!d.ready) { host.innerHTML = note(`⏳ ${esc(d.why)}`, "warn") + (st.when === "close" ? `<div class="row section"><button class="btn primary" id="mlb">Build tonight's radar</button></div>` : "");
      $("#mlb", b)?.addEventListener("click", () => startJob("market-lab")); return; }
    const rows = d.rows.filter((r) => !st.sector || r.sector === st.sector).map((r) => ({ ...r, chance_pct: 100 * r.chance, usual_pct: 100 * r.usual, edge: 100 * r.lift }));
    host.innerHTML = note(`Radar for ${esc(day(d.day))} → next session · ${esc(d.moves[d.move])} · at least ${esc(d.size)}% · status: <b>${esc(d.status)}</b>.
      Sorted by edge: the chance minus the stock's usual chance. Click a row for its influence map.`, d.status === "tested" ? "info" : "warn") + `<div class="card flush section" id="mlt"></div>`;
    $("#mlt", host).appendChild(dataTable(rows, [
      ["symbol", "Stock", "sym"], ["nodes", "Map", (v) => mini(v)],
      ["chance_pct", `Chance ≥${d.size}%`, (v, r) => `<span class="mlbar"><i style="width:${Math.min(100, v)}%"></i><b style="left:${Math.min(100, r.usual_pct)}%"></b></span><b>${num(v, 0)}%</b>`],
      ["usual_pct", "Usual", "pct0"], ["edge", "Edge (pts)", "snum1"], ["lean", "Lean", (v) => leanHtml(v)], ["confidence", "Confidence", "tag"],
      ["n_big", "Cases that big", "int"], ["why", "Biggest drivers", (v) => `<span class="small">${esc(v)}</span>`], ["sector", "Sector"]],
      { csv: `market_lab_${d.day}_${d.move}_${d.size}`, sort: "edge", desc: true, per: 25, show: ["symbol", "nodes", "chance_pct", "usual_pct", "edge", "lean", "confidence", "n_big", "why"], onRow: (r) => { st.sym = r.symbol; save(); go("stock"); }, chart: { x: "usual_pct", y: "chance_pct" } }));
    host.insertAdjacentHTML("beforeend", `<p class="muted small">Bar = chance tomorrow; black tick = the stock's usual chance on any day. Only rows where the bar clearly passes the tick are interesting. Map: green pushes up, red pushes down, grey no clear push.</p>`);
  }

  async function stockTab(b) {
    if (!st.sym) { b.innerHTML = note("Pick a stock in the Move Radar, or type one:") + `<div class="row section"><input class="in" id="mls" placeholder="e.g. TCS"><button class="btn primary" id="mlgo">Open</button></div>`;
      $("#mlgo", b).addEventListener("click", () => { st.sym = $("#mls", b).value.trim().toUpperCase(); save(); stockTab(b); }); return; }
    b.innerHTML = spinner(`Loading ${st.sym} …`);
    let d;
    try { d = await api(`marketlab/stock?${qs({ symbol: st.sym, move: st.move, size: st.size })}`); }
    catch (e) { b.innerHTML = note(esc(e.message), "warn"); return; }
    const v = d.verdict;
    b.innerHTML = `<div class="row" style="justify-content:space-between"><div><h2 style="margin:0">${esc(d.symbol)} <a class="small" href="#/stock/${encodeURIComponent(d.symbol)}">stock page →</a></h2>
        <div class="muted small">${esc(day(d.day))} → next session · ${esc(d.move_label)} · at least ${esc(d.size)}% · ${esc(d.sector || "")} · ${esc(d.status)}</div></div>
        <div class="row"><input class="in" id="mls" placeholder="Another stock" style="width:140px"><button class="btn small" id="mlgo">Open</button></div></div>
      <div class="split section" style="grid-template-columns:minmax(0,1fr) 320px"><div class="card"><div id="mlmap"></div>
        <div class="legend"><span><i style="background:var(--up)"></i>pushes up</span><span><i style="background:var(--down)"></i>pushes down</span><span><i style="background:var(--faint)"></i>no clear push</span>
        <span>thicker = stronger</span><span>dashed = not shown to help on past years</span></div></div>
        <div class="card" id="mlpanel"><h3 style="margin:0 0 6px">Click a node</h3><p class="muted small">Each node is one group of facts about today. Its colour and thickness come from re-running the match without it, never from the AI.</p>
          <dl class="kv"><dt>Chance</dt><dd><b>${P(v.chance)}</b> (usual ${P(v.usual)}, edge ${v.lift >= 0 ? "+" : ""}${num(100 * v.lift, 1)} pts)</dd><dt>Lean</dt><dd>${leanHtml(v.lean)} · ${num(100 * v.up_share, 0)}% of big moves were up${v.direction_tested ? "" : `<div class="muted small">Direction hasn't beaten a coin flip on past years for this combination, so no lean is shown. Node colours are hints only.</div>`}</dd>
          <dt>Confidence</dt><dd>${esc(v.confidence)}</dd><dt>Cases that big</dt><dd>${v.n_big} of 150 closest</dd></dl></div></div>
      <details class="fold section" open><summary>Explain the past cases (AI, optional)</summary><div style="padding:0 14px 14px"><button class="btn" id="mlx">Explain with past cases</button> <span class="muted small" id="mlxs"></span><div id="mlxo"></div></div></details>
      <details class="fold section"><summary>Closest past cases (${d.cases.length})</summary><div id="mlc" style="padding:0 14px 14px"></div></details>
      <details class="fold section"><summary>Outcome range of the 150 closest cases</summary><div id="mlo" style="padding:0 14px 14px"></div></details>
      <details class="fold section"><summary>Today's evidence: ${d.evidence.news.length} headlines, ${d.evidence.filings.length} filings</summary><div id="mle" style="padding:0 14px 14px"></div></details>`;
    drawMap($("#mlmap", b), d, (x) => ($("#mlpanel", b).innerHTML = nodePanel(x, d)));
    $("#mlgo", b).addEventListener("click", () => { const s = $("#mls", b).value.trim().toUpperCase(); if (s) { st.sym = s; save(); stockTab(b); } });
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
  }

  MX.page("market-lab", {
    title: "Market Lab", reloadOnJob: true,
    async render(el, arg) {
      if (arg) { st.sym = arg.toUpperCase(); st.tab = "stock"; save(); }
      el.innerHTML = head("Market Lab", "Which stocks are likely to move at least 1% next session, which way, and what's driving it. Built from the closest past situations; numbers never come from the AI.") +
        `<div class="tabs" id="mltabs">${[["radar", "Move Radar"], ["stock", "Stock analysis"], ["score", "Scorecard"], ["debate", "Debate (classic)"]].map(([k, l]) => `<button data-t="${k}" class="${st.tab === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="mlb2"></div>`;
      const go = (t) => { st.tab = t; save(); $$("#mltabs button", el).forEach((x) => x.classList.toggle("on", x.dataset.t === t)); show(); };
      const show = async () => { const b = $("#mlb2", el);
        try {
          if (st.tab === "radar") await radarTab(b, go);
          else if (st.tab === "stock") await stockTab(b);
          else if (st.tab === "score") await scoreTab(b);
          else b.innerHTML = note("The original bull-and-bear debate, with statistical tests on every claim. It opens on its own page.") + `<div class="row section"><a class="btn primary" href="#/debate">Open the debate</a></div>`;
        } catch (e) { b.innerHTML = note(esc(e.message), "warn"); } };
      $("#mltabs", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) go(x.dataset.t); });
      await show();
    },
  });
})();
