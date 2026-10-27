/* Tally (#/tally/DAY): what MarketX projected for a session vs what happened, stock by stock. Opens in its own tab.
   Data: /api/tally (Review ledger + paper takes; the live feed fills 'so far' during the session). */
(function () {
  const { api, esc, num, day, $, $$, note, spinner, qs } = MX;
  const S = (() => { try { return JSON.parse(sessionStorage.getItem("mx-tally") || "{}"); } catch (e) { return {}; } })();
  const st = Object.assign({ move: "cc", size: "1", mode: "intraday", show: "picks", result: "all" }, S);
  const save = () => { try { sessionStorage.setItem("mx-tally", JSON.stringify(st)); } catch (e) { /* ignore */ } };
  const P = (x, d = 0) => x == null ? "—" : `${num(100 * x, d)}%`;
  const RET = (x, sf) => x == null ? `<span class="muted">waiting</span>` : `<span class="${x > 0 ? "up" : x < 0 ? "down" : "muted"}">${x > 0 ? "▲ +" : x < 0 ? "▼ " : ""}${num(100 * x, 2)}%</span>${sf ? ` <span class="muted small">so far</span>` : ""}`;
  const DIR = (d) => d === "up" ? `<span class="up">▲</span>` : d === "down" ? `<span class="down">▼</span>` : `<span class="muted">${esc(d === "unclear" || d === "neutral" ? "•" : d || "—")}</span>`;
  const OK = (v) => v == null ? `<span class="muted">—</span>` : v ? `<span class="up">✓</span>` : `<span class="down">✗</span>`;
  const MOVES = [["cc", "Close-to-close"], ["oc", "Open-to-close"], ["gp", "Overnight gap"]];
  const F = (v) => v ? day(v, { weekday: "short", day: "numeric", month: "short" }) : "—";
  const COL = { E1: "up", E2: "up", SL: "down", T1: "blue", T2: "blue" };

  function planCell(r) {
    if (r.status == null) return `<span class="muted">—</span>`;
    if (r.status === "no data") return `<span class="muted" title="the session's 5-minute bars are not downloaded yet (Fetch + run all)">not graded: no 5-min bars</span>`;
    if (r.status === "no entry") return `<span class="muted">no entry</span>`;
    if (r.status === "live") return `${r.side === "short" ? "▼" : "▲"} in at ${num(r.entry, 2)} <span class="muted small">live</span>`;
    const ex = r.exit ? `${esc(r.exit)}${r.exit_price != null ? ` @ ${num(r.exit_price, 2)}` : ""}` : esc(r.status);
    return `<span class="${r.side === "short" ? "down" : "up"}">${r.side === "short" ? "▼ short" : "▲ long"}</span> ${num(r.entry, 2)} → ${ex}${r.first ? ` <span class="muted small">first ${esc(r.first)}</span>` : ""}`;
  }

  async function dayView(el, arg) {
    el.innerHTML = spinner("Loading the tally …");
    let d;
    try { d = await api(`tally?${qs({ day: arg || undefined, move: st.move, size: st.size, mode: st.mode })}`); }
    catch (e) { el.innerHTML = MX.head("Tally", "") + note(esc(e.message), "warn"); return; }
    if (!d.ready) { el.innerHTML = MX.head("Tally", "Projected vs what happened") + note(esc(d.why), "info"); return; }
    const ds = d.days || [], i = ds.findIndex((x) => x.day === d.day), prev = ds[i + 1], next = ds[i - 1];
    const seg = (k, opts) => `<div class="seg" data-k="${k}">${opts.map(([v, l]) => `<button data-v="${v}" class="${String(st[k]) === String(v) ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`;
    const k = d.kpis;
    const kp = (label, value, sub) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="muted small">${sub || ""}</div></div>`;
    el.innerHTML = MX.head("Tally", "What MarketX projected for a session vs what happened. Projections are the ones kept that night; nothing is recomputed with hindsight.") +
      `<div class="card"><div class="row" style="gap:10px;flex-wrap:wrap;align-items:center">
         <button class="btn" id="typrev" ${prev ? "" : "disabled"}>‹ ${prev ? F(prev.day) : ""}</button>
         <select class="in" id="tyday">${ds.map((x) => `<option value="${x.day}" ${x.day === d.day ? "selected" : ""}>${F(x.day)} · ${x.kind}${x.graded ? "" : " · not graded"}</option>`).join("")}</select>
         <button class="btn" id="tynext" ${next ? "" : "disabled"}>${next ? F(next.day) : ""} ›</button>
         <span class="ty-for">Projected on <b>${F(d.day)}</b> close → for <b>${F(d.for_day)}</b>${d.for_known ? "" : ` <span class="muted small">(expected)</span>`}</span>
         <span class="tag ${d.graded ? "" : "warn"}">${d.graded ? "graded" : d.live ? "session live: so far" : "waiting for the outcome"}</span>
         <span class="tag">${esc(d.kind)}</span></div>
       <div class="row section" style="gap:14px;flex-wrap:wrap;align-items:flex-end">
         <div><div class="mllab">Move</div>${seg("move", MOVES)}</div>
         <div><div class="mllab">Size</div>${seg("size", [["0.5", "≥0.5%"], ["1", "≥1%"], ["2", "≥2%"], ["3", "≥3%"]])}</div>
         <div><div class="mllab">Plan</div>${seg("mode", [["intraday", "Intraday"], ["swing", "Swing"]])}</div>
         <div><div class="mllab">Show</div>${seg("show", [["picks", "Picks"], ["entered", "Plans entered"], ["paper", "Paper"], ["all", "All stocks"]])}</div>
         <div><div class="mllab">Result</div><select class="in" id="tyres">${[["all", "All"], ["matched", "Matched with real"], ["not", "Not matched"], ["lost", "Plan lost (R < 0)"]]
           .map(([v, l]) => `<option value="${v}" ${st.result === v ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
       <p class="muted small" style="margin:6px 0 0">Matched = moved at least ${esc(d.size)}% (${esc((MOVES.find((m) => m[0] === d.move) || [])[1] || d.move)}) and, when a lean was shown, the way it leaned.</p></div>
      <div class="grid g5 section">
        ${kp("Picks moved", `${k.picks_hit}/${k.picks_done || k.picks}`, `said ${P(k.said)} · happened ${P(k.happened)} · usual ${P(k.usual)}`)}
        ${kp("Lean right", k.lean_n ? `${k.lean_right}/${k.lean_n}` : "—", "picks with a shown lean")}
        ${kp("TA / Fund. direction", `${k.ta[1] ? `${k.ta[0]}/${k.ta[1]}` : "—"} · ${k.fund[1] ? `${k.fund[0]}/${k.fund[1]}` : "—"}`, "labels up/down that matched the move")}
        ${kp(`Plans (${esc(d.mode)})`, k.plans_entered ? `${k.plans_r > 0 ? "+" : ""}${num(k.plans_r, 1)} R` : "—", k.plans_entered ? `${k.plans_entered} entered · avg ${num(k.plans_avg_r, 2)} R · target first ${k.target_first}, stop first ${k.stop_first} · passed checks ${k.tradeable_n}: ${num(k.tradeable_r, 1)} R` : "no entries graded")}
        ${kp("Paper", k.paper ? (k.paper_rs != null ? `₹${num(k.paper_rs, 0)}` : "open") : "—", k.paper ? `${k.paper} taken · ${k.paper_done} graded` : "no paper takes that day")}
      </div><div class="card flush section" id="tyt"></div><div id="tyc" class="section"></div><div id="tychat" class="section"></div>`;
    MX.gradesChat($("#tychat", el), () => ({ day: d.day, move: d.move, size: d.size, mode: d.mode }));

    const reload = (dayArg) => { save(); const h = `#/tally/${dayArg || d.day}`; if (location.hash === h) dayView(el, dayArg || d.day); else location.hash = h; };
    $$(".seg", el).forEach((g) => g.addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; st[g.dataset.k] = x.dataset.v;
      if (g.dataset.k === "show") { save(); $$("button", g).forEach((b) => b.classList.toggle("on", b === x)); draw(); } else reload(); }));
    $("#tyres", el).addEventListener("change", (e) => { st.result = e.target.value; save(); draw(); });
    $("#tyday", el).addEventListener("change", (e) => reload(e.target.value));
    $("#typrev", el).addEventListener("click", () => prev && reload(prev.day));
    $("#tynext", el).addEventListener("click", () => next && reload(next.day));

    function draw() {
      let rows = d.rows.filter((r) => st.show === "all" || (st.show === "picks" && (r.pick || r.paper)) || (st.show === "entered" && r.entry != null) || (st.show === "paper" && r.paper));
      const matched = (r) => r.hit === true && r.lean_right !== false;
      if (st.result === "matched") rows = rows.filter(matched).sort((a, b) => Math.abs(b.ret) - Math.abs(a.ret));
      else if (st.result === "not") rows = rows.filter((r) => r.hit != null && !matched(r));
      else if (st.result === "lost") rows = rows.filter((r) => r.r != null && r.r < 0);
      rows.forEach((r) => { r.chance_pct = r.chance == null ? null : 100 * r.chance; r.ret_abs = r.ret == null ? null : Math.abs(r.ret); });
      const host = $("#tyt", el); host.innerHTML = "";
      if (!rows.length) { host.innerHTML = `<p class="muted" style="padding:12px">Nothing to show with these filters.</p>`; return; }
      host.appendChild(MX.dataTable(rows, [
        ["symbol", "Stock", (v, r) => `<a class="ty-desk" href="#/desk/${encodeURIComponent(v)}" target="_blank" rel="noopener" title="open in Stock Desk"><b>${esc(v)}</b> ↗</a>${r.pick ? ` <span class="tag">pick #${r.rank}</span>` : ""}${r.paper ? ` <span class="tag">paper</span>` : ""}<div class="muted small">${esc(r.sector || "")}</div>`],
        ["chance_pct", `Said: chance ≥${esc(d.size)}%`, (v, r) => v == null ? "—" : `<b>${num(v, 0)}%</b> <span class="muted small">usual ${P(r.usual)} · ${esc(r.confidence || "")}</span>`],
        ["lean", "Said: lean", (v) => DIR(v)], ["ta", "TA", (v) => DIR(v)], ["fund", "Fund.", (v) => DIR(v)],
        ["ret", `Happened: ${esc((MOVES.find((m) => m[0] === d.move) || [])[1] || d.move)}`, (v, r) => RET(v, r.so_far)],
        ["hit", `≥${esc(d.size)}%?`, (v) => OK(v)], ["lean_right", "Lean right?", (v) => OK(v)], ["ta_right", "TA right?", (v) => OK(v)],
        ["status", "Plan result", (v, r) => planCell(r)], ["r", "R", (v) => v == null ? "—" : `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${num(v, 2)}</b>`],
        ["pnl_rs", "Paper ₹", (v) => v == null ? "—" : `<b class="${v > 0 ? "up" : "down"}">${num(v, 0)}</b>`]],
        { csv: `tally_${d.day}_${d.move}_${d.size}_${d.mode}`, sort: st.result === "matched" ? "ret_abs" : st.show === "all" ? "ret_abs" : "chance_pct", desc: true, per: 50, onRow: (r) => chart(r) }));
    }

    async function chart(r) {
      const host = $("#tyc", el);
      host.innerHTML = `<div class="card">${spinner(`Loading ${esc(r.symbol)} …`)}</div>`;
      let c; try { c = await api(`tally/chart?${qs({ symbol: r.symbol, day: d.day })}`); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
      if (!host.isConnected) return;
      host.innerHTML = `<div class="card"><div class="ct"><h2>${esc(r.symbol)} · ${F(c.for_day)}${c.live ? " · live" : ""}</h2><span class="aside small"><a href="#/ta/${encodeURIComponent(r.symbol)}" target="_blank" rel="noopener">TA workspace ↗</a> · <a href="#/desk/${encodeURIComponent(r.symbol)}" target="_blank" rel="noopener">Stock Desk ↗</a></span></div>
        <div class="small" style="margin-bottom:6px">Said: ${r.chance == null ? "" : `chance ${P(r.chance)} (usual ${P(r.usual)}) · `}lean ${DIR(r.lean)} · TA ${DIR(r.ta)} · plan ${DIR(r.plan_lean)} · Happened: ${RET(r.ret, r.so_far)} · plan ${planCell(r)}${r.r != null ? ` · ${num(r.r, 2)} R` : ""}</div><div id="tycc"></div></div>`;
      if (!c.bars || !c.bars.t.length) { $("#tycc", host).innerHTML = `<div class="empty">No 5-minute bars saved for ${esc(r.symbol)} on ${F(c.for_day)} (Data health → Intraday).</div>`; return; }
      const levels = Object.entries(r.legs || {}).map(([k, v]) => ({ price: v, color: COL[k.replace(/[↑↓]/, "")] || "faint", label: k, width: k.startsWith("SL") ? 1.6 : 1.3 }));
      MXCharts.priceChart($("#tycc", host), c.bars, { mode: "candle", intraday: true, height: 400, levels });
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    draw();
  }

  /* ---------- Compare days: top numbers side by side + trend ---------- */
  async function compareView(el) {
    el.innerHTML = spinner("Loading …");
    let d; try { d = await api(`tally/compare?${qs({ days: (st.cmp || []).join(",") || undefined, move: st.move, size: st.size, mode: st.mode })}`); }
    catch (e) { el.innerHTML = note(esc(e.message), "warn"); return; }
    if (!d.available.length) { el.innerHTML = note("Nothing graded yet.", "info"); return; }
    const chosen = new Set(d.days.map((x) => x.day));
    const rows = d.days, pct1 = (x) => x == null ? "—" : `${num(100 * x, 0)}%`;
    el.innerHTML = `<div class="card"><div class="mllab">Days (${d.available.length} graded; tick up to 40)</div>
        <div class="ty-days">${d.available.slice().reverse().map((x) => `<label class="ty-day"><input type="checkbox" value="${x}" ${chosen.has(x) ? "checked" : ""}> ${F(x)}</label>`).join("")}</div>
        <div class="row" style="gap:6px;margin-top:8px"><button class="btn small" id="tylast5">Last 5</button><button class="btn small" id="tylast10">Last 10</button><button class="btn small" id="tyall">All</button><button class="btn primary small" id="tycmp">Compare</button>
          <span class="muted small">move ${esc(d.move)} · ≥${esc(d.size)}% · ${esc(d.mode)} plans (change them on the Day tab)</span></div></div>
      <div class="card section"><div class="ct"><h2>Over the days</h2><span class="aside muted small">picks that moved vs what was said · plan R per day</span></div><div id="tych1"></div><div id="tych2" class="section"></div></div>
      <div class="card flush section" id="tyct"></div>`;
    const pick = (list) => { st.cmp = list; save(); compareView(el); };
    el.querySelector("#tylast5").addEventListener("click", () => pick(d.available.slice(0, 5)));
    el.querySelector("#tylast10").addEventListener("click", () => pick(d.available.slice(0, 10)));
    el.querySelector("#tyall").addEventListener("click", () => pick(d.available.slice(0, 40)));
    el.querySelector("#tycmp").addEventListener("click", () => pick([...el.querySelectorAll(".ty-day input:checked")].map((x) => x.value).slice(0, 40)));
    if (rows.length >= 2) {
      MXCharts.lineChart(el.querySelector("#tych1"), { x: rows.map((r) => r.day), height: 220, ysuffix: "%", series: [
        { name: "Picks moved", y: rows.map((r) => r.happened == null ? null : 100 * r.happened) }, { name: "Said", y: rows.map((r) => r.said == null ? null : 100 * r.said), dash: "4 3" },
        { name: "Usual", y: rows.map((r) => r.usual == null ? null : 100 * r.usual), dash: "2 3" }] });
      MXCharts.lineChart(el.querySelector("#tych2"), { x: rows.map((r) => r.day), height: 180, zero: true, series: [{ name: "Plan R (day total)", y: rows.map((r) => r.plans_r) }] });
    } else el.querySelector("#tych1").innerHTML = `<p class="muted small">Pick at least 2 days for the chart.</p>`;
    el.querySelector("#tyct").appendChild(MX.dataTable(rows.slice().reverse(), [
      ["day", "Projected on", (v, r) => `<a href="#/tally/${encodeURIComponent(v)}">${F(v)}</a><div class="muted small">for ${F(r.for_day)}</div>`],
      ["picks_hit", "Picks moved", (v, r) => `${v}/${r.picks_done || r.picks}`], ["said", "Said", (v) => pct1(v)], ["happened", "Happened", (v) => pct1(v)], ["usual", "Usual", (v) => pct1(v)],
      ["lean_right", "Lean right", (v, r) => r.lean_n ? `${v}/${r.lean_n}` : "—"], ["ta_right", "TA right", (v, r) => r.ta_n ? `${v}/${r.ta_n}` : "—"],
      ["plans_r", "Plan R", (v, r) => v == null ? `<span class="muted">not graded</span>` : `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${num(v, 1)}</b> <span class="muted small">${r.plans_entered} entered · avg ${num(r.plans_avg_r, 2)}</span>`],
      ["target_first", "Target / stop first", (v, r) => r.plans_entered ? `${v} / ${r.stop_first}` : "—"], ["paper_rs", "Paper ₹", (v, r) => v == null ? (r.paper ? "open" : "—") : num(v, 0)]],
      { csv: `tally_compare_${rows.length}_days`, per: 50 }));
  }

  /* ---------- Stock history: one stock across every graded day ---------- */
  async function stockView(el) {
    el.innerHTML = `<div class="card"><div class="row" style="gap:8px"><input class="in" id="tyhs" placeholder="Stock, e.g. TCS" style="width:220px" value="${esc(st.hsym || "")}"><button class="btn primary" id="tyhgo">Show history</button>
      <span class="muted small">every night it was projected (picks and plans), what happened, and the plan result</span></div></div><div id="tyhb" class="section"></div>`;
    const go = () => { const v = el.querySelector("#tyhs").value.trim().toUpperCase(); if (v) { st.hsym = v; save(); draw(); } };
    el.querySelector("#tyhgo").addEventListener("click", go); el.querySelector("#tyhs").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    MX.suggest(el.querySelector("#tyhs"), { kinds: "stocks", recent: "stock", onPick: () => go() });
    async function draw() {
      const host = el.querySelector("#tyhb"); if (!st.hsym) { host.innerHTML = ""; return; }
      host.innerHTML = spinner(`Loading ${esc(st.hsym)} …`);
      let d; try { d = await api(`tally/stock?${qs({ symbol: st.hsym, move: st.move, size: st.size, mode: st.mode })}`); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
      const s = d.summary;
      if (!d.rows.length) { host.innerHTML = note(`${esc(d.symbol)} has no graded nights.`, "info"); return; }
      host.innerHTML = `<div class="grid g4">
          <div class="card kpi"><div class="label">Nights</div><div class="value">${s.days}</div><div class="muted small">${s.graded} graded</div></div>
          <div class="card kpi"><div class="label">Moved ≥${esc(d.size)}%</div><div class="value">${s.hit}/${s.graded}</div><div class="muted small">when a pick: ${s.pick_hit}/${s.pick_graded}</div></div>
          <div class="card kpi"><div class="label">Plans entered</div><div class="value">${s.plans_entered}</div><div class="muted small">total ${s.plans_r > 0 ? "+" : ""}${num(s.plans_r, 2)} R</div></div>
          <div class="card kpi"><div class="label">Open</div><div class="value"><a href="#/desk/${encodeURIComponent(d.symbol)}" target="_blank" rel="noopener" style="font-size:16px">Stock Desk ↗</a></div><div class="muted small">full analysis</div></div></div>
        <div class="card flush section" id="tyht"></div>`;
      host.querySelector("#tyht").appendChild(MX.dataTable(d.rows, [
        ["day", "Projected on", (v, r) => `<a href="#/tally/${encodeURIComponent(v)}">${F(v)}</a><div class="muted small">for ${F(r.for_day)}</div>`],
        ["pick", "Pick", (v, r) => v ? `<span class="tag">#${r.rank}</span>` : `<span class="muted">—</span>`], ["chance", "Said: chance", (v, r) => v == null ? "—" : `<b>${P(v)}</b> <span class="muted small">usual ${P(r.usual)}</span>`],
        ["lean", "Lean", (v) => DIR(v)], ["ta", "TA", (v) => DIR(v)], ["ret", "Happened", (v) => RET(v)], ["hit", `≥${esc(d.size)}%?`, (v) => OK(v)], ["lean_right", "Lean right?", (v) => OK(v)],
        ["status", "Plan result", (v, r) => planCell(r)], ["r", "R", (v) => v == null ? "—" : `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${num(v, 2)}</b>`]],
        { csv: `tally_${d.symbol}_history`, per: 60 }));
    }
    draw();
  }

  async function page(el, arg, fromTab) {
    if (arg && !fromTab) st.view = "day";                       // a link to a day (#/tally/DAY) opens the Day tab
    el.innerHTML = `<div class="tabs" id="tyv" style="margin-top:0">${[["day", "Day"], ["compare", "Compare days"], ["stock", "Stock history"]].map(([k, l]) => `<button data-v="${k}" class="${(st.view || "day") === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="tyvb"></div>`;
    const body = el.querySelector("#tyvb");
    el.querySelector("#tyv").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.view = b.dataset.v; save(); page(el, arg, true); });
    const v = st.view || "day";
    if (v === "compare") { body.innerHTML = MX.head("Tally · compare days", "The same top numbers as the Day tab, for several nights side by side."); const x = document.createElement("div"); body.appendChild(x); return compareView(x); }
    if (v === "stock") { body.innerHTML = MX.head("Tally · stock history", "One stock across every graded night."); const x = document.createElement("div"); body.appendChild(x); return stockView(x); }
    return dayView(body, arg);
  }

  MX.page("tally", { title: "Tally", render: page });
})();
