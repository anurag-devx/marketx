/* Coverage (#/coverage): for every Nifty 500 stock, which data MarketX has, which signals can be computed and which of
   them feed the forecasts. Filter, select stocks and analyse them (full Stock Desk analysis) as a run; every run is kept
   (Runs). #/coverage/SYMBOL = one stock's checklist. Data: /api/coverage, /api/coverage/stock, /api/desk/run(s). */
(function () {
  const { api, esc, num, day, $, $$, note, spinner, startJob, qs } = MX;
  const S = (() => { try { return JSON.parse(sessionStorage.getItem("mx-cov") || "{}"); } catch (e) { return {}; } })();
  const st = Object.assign({ q: "", sector: "", need: [], missing: "", radar: false, tab: "stocks" }, S);
  const save = () => { try { sessionStorage.setItem("mx-cov", JSON.stringify(st)); } catch (e) { /* ignore */ } };
  const ICON = { ok: ["✓", "up"], partial: ["◐", "warn"], stale: ["◐", "warn"], missing: ["✗", "down"], none: ["·", "muted"], "n/a": ["–", "muted"] };
  const DOT = { ok: "ok", partial: "partial", stale: "stale", missing: "missing", none: "none", "n/a": "na" };
  const cell = (d) => { const [i, c] = ICON[d.state] || ["?", "muted"]; return `<span title="${esc(d.state)}: ${esc(d.detail)}">${MX.sdot(DOT[d.state] || "none")}<span class="cv-i ${c}">${i}</span></span>`; };
  const STATUS = { used: ["used", "up"], shown: ["shown only", "muted"], candidate: ["candidate", "warn"] };
  let selected = new Set();

  async function page(el, arg) {
    if (arg) return stockView(el, String(arg).toUpperCase());
    el.innerHTML = MX.head("Coverage", "Which data each stock has, which signals that allows, and which signals feed the forecasts. Select stocks to analyse them as a run.") +
      `<div class="tabs" id="cvt">${[["stocks", "Stocks"], ["runs", "Analysis runs"]].map(([k, l]) => `<button data-t="${k}" class="${st.tab === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="cvb"></div>`;
    $("#cvt", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.tab = b.dataset.t; save(); page(el); });
    return st.tab === "runs" ? runsView($("#cvb", el)) : stocksView($("#cvb", el));
  }

  async function stocksView(el) {
    el.innerHTML = spinner("Checking every stock …");
    let d; try { d = await api("coverage"); } catch (e) { el.innerHTML = note(esc(e.message), "warn"); return; }
    const items = d.items, sectors = [...new Set(d.rows.map((r) => r.sector).filter(Boolean))].sort();
    el.innerHTML = `<div class="cv-sum">${items.map((it) => `<div class="cv-s" style="${d.summary[it.id] < 0.5 ? "border-color:color-mix(in srgb, var(--down) 45%, var(--line))" : d.summary[it.id] < 0.9 ? "border-color:color-mix(in srgb, var(--warn) 45%, var(--line))" : ""}"><div class="k">${esc(it.label)}</div><div class="cv-bar"><i style="width:${100 * d.summary[it.id]}%"></i></div>
          <div class="row" style="justify-content:space-between"><b>${num(100 * d.summary[it.id], 0)}%</b><button class="btn small owner-act cvfix" data-job="${esc(it.job)}" title="run the job that fills this">Fix</button></div></div>`).join("")}</div>
      <div class="card section"><div class="mllab">Signals (Market Lab groups) · radar of ${esc(d.radar_day || "—")}</div><div class="cv-sig">${Object.entries(d.signals).map(([g, s]) =>
        `<span class="tag ${STATUS[s][1]}" title="${s === "used" ? "feeds tonight's chance" : s === "shown" ? "in the engine but not shown to help (Scorecard)" : "built, not used until it passes the candidate test"}">${esc(g)} · ${STATUS[s][0]}</span>`).join("")}</div></div>
      <div class="card section"><div class="row cv-f" style="gap:8px;flex-wrap:wrap;align-items:flex-end">
          <div><div class="mllab">Search</div><input class="in" id="cvq" value="${esc(st.q)}" placeholder="symbol or name" style="width:180px"></div>
          <div><div class="mllab">Sector</div><select class="in" id="cvsec"><option value="">All</option>${sectors.map((s) => `<option ${st.sector === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></div>
          <div><div class="mllab">Missing</div><select class="in" id="cvmiss"><option value="">—</option><option value="any" ${st.missing === "any" ? "selected" : ""}>anything</option><option value="none" ${st.missing === "none" ? "selected" : ""}>nothing (complete)</option>
            ${items.map((it) => `<option value="${it.id}" ${st.missing === it.id ? "selected" : ""}>${esc(it.label)}</option>`).join("")}</select></div>
          <div><div class="mllab">Must have</div><div class="cv-need">${items.map((it) => `<label class="small"><input type="checkbox" value="${it.id}" ${st.need.includes(it.id) ? "checked" : ""}> ${esc(it.label)}</label>`).join("")}</div></div>
          <label class="small"><input type="checkbox" id="cvrad" ${st.radar ? "checked" : ""}> on tonight's radar</label></div>
        <div class="row section" style="gap:8px;flex-wrap:wrap"><span id="cvn" class="small muted"></span><button class="btn small" id="cvall">Select all shown</button><button class="btn small" id="cvnone">Clear selection</button>
          <span style="flex:1"></span><input class="in" id="cvlabel" placeholder="run name (optional)" style="width:180px"><button class="btn primary" id="cvgo">Analyse selected</button></div></div>
      <div class="card flush section"><div class="scroll"><table class="tbl cv-tbl"><thead><tr><th></th><th>Stock</th>${items.map((it) => `<th class="c" title="${esc(it.label)}">${esc(it.label.replace(/ \(90 days\)/, ""))}</th>`).join("")}
        <th>Signals usable</th><th>Radar / plan</th></tr></thead><tbody id="cvbody"></tbody></table></div></div>`;
    const used = Object.entries(d.signals).filter(([, s]) => s === "used").map(([g]) => g);
    const filt = () => d.rows.filter((r) => {
      if (st.q && !(r.symbol + " " + (r.name || "")).toLowerCase().includes(st.q.toLowerCase())) return false;
      if (st.sector && r.sector !== st.sector) return false;
      if (st.radar && !r.radar) return false;
      if (st.missing === "any" && !r.missing.length) return false;
      if (st.missing === "none" && r.missing.length) return false;
      if (st.missing && !["any", "none"].includes(st.missing) && !r.missing.includes(st.missing)) return false;
      return st.need.every((k) => ["ok", "partial"].includes(r.data[k].state));
    });
    const draw = () => {
      const rows = filt();
      $("#cvn", el).textContent = `${rows.length} of ${d.rows.length} stocks shown · ${selected.size} selected`;
      $("#cvbody", el).innerHTML = rows.slice(0, 600).map((r) => {
        const u = used.filter((g) => r.signals[g] && r.signals[g].available).length;
        return `<tr><td><input type="checkbox" class="cvpick" value="${esc(r.symbol)}" ${selected.has(r.symbol) ? "checked" : ""}></td>
          <td><a class="sym" href="#/coverage/${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a><div class="co">${esc(r.sector || "")}</div></td>
          ${items.map((it) => `<td class="c">${cell(r.data[it.id])}</td>`).join("")}
          <td><b>${u}</b>/${used.length} <span class="muted small">used groups</span></td>
          <td class="small">${r.radar ? "radar ✓" : "<span class='muted'>radar ✗</span>"} · ${r.plan ? "plan ✓" : "<span class='muted'>plan ✗</span>"}</td></tr>`; }).join("");
      $$(".cvpick", el).forEach((c) => c.addEventListener("change", () => { c.checked ? selected.add(c.value) : selected.delete(c.value); $("#cvn", el).textContent = `${rows.length} of ${d.rows.length} stocks shown · ${selected.size} selected`; }));
    };
    const re = () => { save(); draw(); };
    $("#cvq", el).addEventListener("input", (e) => { st.q = e.target.value; re(); });
    $("#cvsec", el).addEventListener("change", (e) => { st.sector = e.target.value; re(); });
    $("#cvmiss", el).addEventListener("change", (e) => { st.missing = e.target.value; re(); });
    $("#cvrad", el).addEventListener("change", (e) => { st.radar = e.target.checked; re(); });
    $$(".cv-need input", el).forEach((c) => c.addEventListener("change", () => { st.need = $$(".cv-need input", el).filter((x) => x.checked).map((x) => x.value); re(); }));
    $("#cvall", el).addEventListener("click", () => { filt().forEach((r) => selected.add(r.symbol)); draw(); });
    $("#cvnone", el).addEventListener("click", () => { selected = new Set(); draw(); });
    $$(".cvfix", el).forEach((b) => b.addEventListener("click", () => startJob(b.dataset.job)));
    $("#cvgo", el).addEventListener("click", async () => {
      const syms = [...selected];
      if (!syms.length) return MX.toast("Select stocks first", true);
      if (syms.length > 100) return MX.toast("At most 100 stocks per run", true);
      try { const r = await MX.post("desk/run", { symbols: syms, label: $("#cvlabel", el).value, filter: { q: st.q, sector: st.sector, missing: st.missing, need: st.need, radar: st.radar } });
        MX.toast(`Analysing ${r.n} stocks (about ${r.estimate_min} min) · see Analysis runs`); MX.refreshStatus(true); selected = new Set(); st.tab = "runs"; save(); page(el.closest(".page") || el.parentElement.parentElement); } catch (e) { /* toast */ }
    });
    draw();
  }

  async function runsView(el) {
    el.innerHTML = spinner("Loading runs …");
    let d; try { d = await api("desk/runs"); } catch (e) { el.innerHTML = note(esc(e.message), "warn"); return; }
    if (!d.items.length) { el.innerHTML = note("No analysis runs yet: select stocks on the Stocks tab and click Analyse selected.", "info"); return; }
    el.innerHTML = `<div class="card flush">${MX.table(d.items, [["created", "Started", (v) => esc(String(v || "").slice(0, 16))], ["label", "Run", (v, r) => `<a href="#" class="cvrun" data-id="${esc(r.id)}"><b>${esc(v || r.id)}</b></a>`],
      ["n", "Stocks"], ["done", "Analysed", (v, r) => `${v}/${r.n}${r.failed ? ` <span class="down small">${r.failed} failed</span>` : ""}`], ["status", "Status", (v) => esc(v || "—")], ["user", "By"]])}</div><div id="cvrd" class="section"></div>`;
    $$(".cvrun", el).forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); runDetail($("#cvrd", el), a.dataset.id); }));
    runDetail($("#cvrd", el), d.items[0].id);
  }

  async function runDetail(host, id) {
    host.innerHTML = spinner("Loading the run …");
    let d; try { d = await api(`desk/run?${qs({ id })}`); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
    const r = d.run, P = (x) => x == null ? "—" : `${num(100 * x, 0)}%`, DIR = (x) => x === "up" ? `<span class="up">▲</span>` : x === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
    host.innerHTML = `<div class="card"><div class="ct"><h2>${esc(r.label || r.id)}</h2><span class="aside muted small">started ${esc(String(r.created || "").slice(0, 16))} · ${esc(r.status || "")}${r.finished ? ` · finished ${esc(String(r.finished).slice(11, 16))}` : ""}</span></div>
      ${r.filter && Object.keys(r.filter).length ? `<p class="small muted">Selection: ${esc(JSON.stringify(r.filter))}</p>` : ""}
      ${MX.table(d.rows, [["symbol", "Stock", (v, x) => `<a class="sym" href="#/desk/${encodeURIComponent(v)}" target="_blank" rel="noopener">${esc(v)} ↗</a><div class="co">${esc(x.sector || "")}</div>`],
        ["state", "State", (v, x) => v === "done" ? `<span class="up">✓</span> <span class="muted small">${esc(x.snap)}</span>` : v === "failed" ? `<span class="down" title="${esc(x.why || "")}">failed</span>` : `<span class="muted">waiting</span>`],
        ["close", "Close", (v) => v == null ? "—" : num(v, 2)], ["chance", "Chance ≥1%", (v, x) => v == null ? "—" : MX.chanceBar(v, x.usual)], ["lean", "Lean", (v) => MX.lean(v)],
        ["confidence", "Conf.", (v) => v ? MX.conf(v) : "—"], ["ta", "Chart read", (v, x) => `${DIR(v)} ${x.ta_net != null ? `<span class="muted small">${x.ta_net > 0 ? "+" : ""}${x.ta_net}</span>` : ""}`],
        ["pros", "For / against", (v, x) => x.state === "done" ? `<span class="up">${v}</span> / <span class="down">${x.cons}</span>` : "—"]])}
      <p class="muted small">Each analysis is the one saved in this run; Stock Desk keeps every one (pick it in its "saved analyses" list).</p></div>`;
    if (r.status !== "done") setTimeout(() => { if (host.isConnected) runDetail(host, id); }, 10000);
  }

  async function stockView(el, sym) {
    el.innerHTML = spinner(`Checking ${esc(sym)} …`);
    let d; try { d = await api(`coverage/stock?${qs({ symbol: sym })}`); } catch (e) { el.innerHTML = MX.head("Coverage", "") + note(esc(e.message), "warn"); return; }
    const r = d.row;
    el.innerHTML = MX.head(`${esc(sym)} · coverage`, `${esc(r.name || "")} · ${esc(r.sector || "")}`, `<a class="btn" href="#/coverage">← All stocks</a> <a class="btn primary" href="#/desk/${encodeURIComponent(sym)}" target="_blank" rel="noopener">Stock Desk ↗</a>`) +
      `<div class="card section"><div class="ct"><h2>Data</h2></div>${MX.table(d.items.map((it) => ({ ...it, ...r.data[it.id] })), [["label", "Item"], ["state", "Available", (v) => cell({ state: v, detail: v }) + ` ${esc(v)}`], ["detail", "Detail"],
        ["job", "", (v, x) => ["missing", "stale", "partial"].includes(x.state) ? `<button class="btn small owner-act cvfix" data-job="${esc(v)}">Fix</button>` : ""]])}</div>
      <div class="card section"><div class="ct"><h2>Signals</h2><span class="aside muted small">values from the radar of ${esc(d.radar_day || "—")}</span></div>${MX.table(d.signals, [["group", "Group"], ["label", "Signal"],
        ["value", "Value", (v) => v == null ? "<span class='muted'>—</span>" : num(v, 3)], ["available", "Data", (v) => v ? `<span class="up">✓</span>` : `<span class="down">✗</span>`],
        ["status", "Used in forecast", (v, x) => `<span class="tag ${STATUS[v] ? STATUS[v][1] : ""}" title="${esc(x.why)}">${STATUS[v] ? STATUS[v][0] : esc(v)}</span>`]])}</div>
      <div class="card section"><div class="ct"><h2>Technical (plan of ${esc(d.plan_day || "—")})</h2><span class="aside muted small">shown as a second opinion; not in the chance</span></div>
        <p>Chart read ${d.ta.dir ? esc(d.ta.dir) : "—"}${d.ta.score != null ? ` · score ${d.ta.score}` : ""}</p><ul class="small">${(d.ta.why || []).map((w) => `<li>${MX.arr(w)}</li>`).join("")}</ul></div>`;
    $$(".cvfix", el).forEach((b) => b.addEventListener("click", () => startJob(b.dataset.job)));
  }

  MX.page("coverage", { title: "Coverage", render: page });
})();
