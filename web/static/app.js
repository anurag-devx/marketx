/* MarketX web app — core: API client, formatting, components, status bar, search, theme, router.
   Pages live in /pages/*.js and register themselves with MX.page(key, {render}). Plain JavaScript, no libraries. */
(function () {
  "use strict";
  const TOKEN = document.querySelector('meta[name="mx-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => v != null && v !== "" && !isNaN(v);
  const num = (v, d = 2) => !isNum(v) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => !isNum(v) ? "—" : `${v > 0 ? "+" : ""}${num(v, d)}%`;
  const cls = (v) => !isNum(v) ? "" : v > 0 ? "up" : v < 0 ? "down" : "";
  const inr = (v, d) => !isNum(v) ? "—" : `${v < 0 ? "−" : ""}₹${num(Math.abs(v), d ?? (Math.abs(v) >= 1000 ? 0 : 2))}`;
  const day = (s, o = { day: "numeric", month: "short", year: "numeric" }) => s ? new Date(s).toLocaleDateString("en-IN", o) : "—";
  const dt = (s) => s ? new Date(s).toLocaleString("en-IN", { day: "numeric", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const safeUrl = (u) => /^https?:\/\//i.test(String(u || "")) ? String(u) : null;

  // ---------- API ----------
  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-MX-Token": TOKEN }, body: JSON.stringify(body) };
    const r = await fetch(`/api/${path}`, opt);
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  const qs = (o) => Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(Array.isArray(v) ? v.join(",") : v)}`).join("&");
  function toast(msg, bad) { const t = $("#toast"); t.textContent = msg; t.className = `toast${bad ? " bad" : ""}`; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 3600); }
  async function startJob(name, args) {
    try { await api("job/start", { name, args }); toast("Started — progress is in the bar at the top"); refreshStatus(true); return true; }
    catch (e) { toast(e.message, true); return false; }
  }
  async function post(path, body, okMsg) {
    try { const r = await api(path, body); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message, true); throw e; }
  }

  // ---------- plain names for strategy codes (S1, MOM_L6_S1_H3 ...) ----------
  let NAMES = {};
  function momName(v) {
    const m = /^MOM_L(\d+)_S(\d+)_H(\d+)(_F)?$/.exec(v); if (!m) return null;
    const [, L, S, H, F] = m;
    return `Momentum: best ${L}-month return${+S ? `, skip latest ${S} mo` : ""}, hold ${H} mo${F ? ", cash in downtrends" : ""}`;
  }
  const nameOf = (v) => (typeof v === "string" && (momName(v) || NAMES[v])) || null;
  const named = (v) => { const n = nameOf(v); return n ? `${esc(n)} <span class="code">${esc(v)}</span>` : null; };

  // ---------- formatting by column type ----------
  const FMT = {
    text: (v) => named(v) || esc(v ?? "—"),
    long: (v) => `<span class="long">${esc(v ?? "")}</span>`,
    sym: (v, r) => v ? `<a class="sym" href="#/stock/${encodeURIComponent(v)}">${esc(v)}</a>${r && (r.company || r.company_name) ? `<div class="co">${esc(r.company || r.company_name)}</div>` : ""}` : "—",
    symonly: (v) => v ? `<a class="sym" href="#/stock/${encodeURIComponent(v)}">${esc(v)}</a>` : "—",
    int: (v) => num(v, 0), num0: (v) => num(v, 0), num1: (v) => num(v, 1), num2: (v) => num(v, 2), num3: (v) => num(v, 3),
    pct0: (v) => isNum(v) ? `${num(v, 0)}%` : "—", pct1: (v) => isNum(v) ? `${num(v, 1)}%` : "—",
    spct1: (v) => `<span class="${cls(v)}">${pct(v, 1)}</span>`, spct2: (v) => `<span class="${cls(v)}">${pct(v, 2)}</span>`,
    snum1: (v) => `<span class="${cls(v)}">${isNum(v) ? (v > 0 ? "+" : "") + num(v, 1) : "—"}</span>`,
    snum2: (v) => `<span class="${cls(v)}">${isNum(v) ? (v > 0 ? "+" : "") + num(v, 2) : "—"}</span>`,
    frac: (v) => isNum(v) ? `${num(100 * v, 1)}%` : "—",
    inr: (v) => inr(v), inr0: (v) => inr(v, 0), cr: (v) => isNum(v) ? `₹${num(v, 0)} cr` : "—", cr1: (v) => isNum(v) ? `₹${num(v, 1)} cr` : "—",
    x1: (v) => isNum(v) ? `${num(v, 1)}×` : "—",
    date: (v) => day(v), dt: (v) => dt(v),
    bool: (v) => v === true || v === "True" || v === 1 ? `<span class="up">✓</span>` : v === false || v === 0 || v === "False" ? `<span class="muted">—</span>` : "—",
    link: (v) => safeUrl(v) ? `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">open</a>` : "—",
    tag: (v) => v ? `<span class="tag">${esc(v)}</span>` : "—",
    strat: (v) => named(v) || esc(v ?? "—"),
    status: (v) => { const s = String(v || "").toLowerCase(); const c = /ok|pass|valid|enabled|approved|done|clean/.test(s) ? "pos" : /error|fail|reject|bad/.test(s) ? "neg" : ""; return v ? `<span class="tag ${c}">${esc(v)}</span>` : "—"; },
  };
  const RIGHT = /^(int|num|pct|spct|snum|frac|inr|cr|x1)/;
  const fmt = (type, v, row) => (typeof type === "function" ? type : FMT[type] || FMT.text)(v, row);

  // simple table: cols = [[key, label, fn|type, right]]
  function table(rows, cols, empty = "Nothing here.") {
    if (!rows || !rows.length) return `<div class="empty">${esc(empty)}</div>`;
    const right = (c) => c[3] ?? (typeof c[2] === "string" && RIGHT.test(c[2]));
    return `<table class="tbl"><thead><tr>${cols.map((c) => `<th class="${right(c) ? "r" : ""}">${esc(c[1])}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((row) => `<tr>${cols.map((c) => `<td class="${right(c) ? "r" : ""}">${fmt(c[2] || "text", row[c[0]], row)}</td>`).join("")}</tr>`).join("") + `</tbody></table>`;
  }

  // data table: sortable, searchable, CSV, paged. Returns a DOM node.
  function dataTable(rows, cols_, opt = {}) {
    const box = document.createElement("div"); box.className = "dt";
    rows = rows || [];
    let sortK = opt.sort || null, sortDir = opt.desc ? -1 : 1, filter = "", page = 0;
    const allCols = cols_, ckey = `mx-cols-${allCols.map((c) => c[0]).join("|").slice(0, 180)}`;
    let hidden = new Set(opt.show ? allCols.map((c) => c[0]).filter((k) => !opt.show.includes(k)) : []);
    try { const sv = localStorage.getItem(ckey); if (sv) hidden = new Set(JSON.parse(sv)); } catch (e) { /* ignore */ }
    const numCols = allCols.filter((c) => typeof c[2] === "string" && RIGHT.test(c[2]) || rows.slice(0, 20).some((r) => typeof r[c[0]] === "number"));
    const per = opt.per || 50, right = (c) => c[3] ?? (typeof c[2] === "string" && RIGHT.test(c[2]));
    const draw = () => {
      const cols = allCols.filter((c) => !hidden.has(c[0]));
      let r = rows;
      if (filter) { const f = filter.toLowerCase(); r = r.filter((x) => cols.some((c) => String(x[c[0]] ?? "").toLowerCase().includes(f))); }
      if (sortK) r = [...r].sort((a, b) => { const x = a[sortK], y = b[sortK]; if (x == null) return 1; if (y == null) return -1;
        return (isNum(x) && isNum(y) ? x - y : String(x).localeCompare(String(y))) * sortDir; });
      const pages = Math.max(1, Math.ceil(r.length / per)); page = Math.min(page, pages - 1);
      const vis = r.slice(page * per, page * per + per);
      box.innerHTML = `${opt.tools === false ? "" : `<div class="dt-tools">${rows.length > 8 ? `<input class="in dt-f" placeholder="Filter rows" value="${esc(filter)}">` : ""}
          <span class="muted">${r.length.toLocaleString("en-IN")} row${r.length === 1 ? "" : "s"}</span><span class="spacer"></span>
          ${numCols.length >= 1 && rows.length >= 5 ? `<button class="btn small dt-chart" title="Scatter or histogram of any columns, with outliers marked">Chart</button>` : ""}
          ${allCols.length > 6 ? `<button class="btn small dt-cols" title="Show or hide columns">Columns${hidden.size ? ` (${allCols.length - hidden.size}/${allCols.length})` : ""}</button>` : ""}
          ${opt.csv ? `<button class="btn small dt-csv">Download CSV</button>` : ""}</div>`}
        <div class="scroll" ${opt.height ? `style="max-height:${opt.height}px"` : ""}>${!r.length ? `<div class="empty">${esc(opt.empty || "Nothing here.")}</div>` :
        `<table class="tbl"><thead><tr>${cols.map((c) => `<th data-k="${esc(c[0])}" class="${right(c) ? "r" : ""} sortable">${esc(c[1])}${sortK === c[0] ? (sortDir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
        <tbody>${vis.map((row) => `<tr${opt.onRow ? ' class="click"' : ""}>${cols.map((c) => `<td class="${right(c) ? "r" : ""}">${fmt(c[2] || "text", row[c[0]], row)}</td>`).join("")}</tr>`).join("")}</tbody></table>`}</div>
        ${pages > 1 ? `<div class="dt-pages"><button class="btn small dt-prev" ${page ? "" : "disabled"}>‹</button><span class="muted">page ${page + 1} of ${pages}</span><button class="btn small dt-next" ${page < pages - 1 ? "" : "disabled"}>›</button></div>` : ""}`;
      $$("th.sortable", box).forEach((th) => th.addEventListener("click", () => { const k = th.dataset.k; sortDir = sortK === k ? -sortDir : (isNum(rows.find((x) => x[k] != null)?.[k]) ? -1 : 1); sortK = k; draw(); }));
      const f = $(".dt-f", box); if (f) f.addEventListener("input", () => { filter = f.value; page = 0; const pos = f.selectionStart; draw(); const g = $(".dt-f", box); g.focus(); g.setSelectionRange(pos, pos); });
      $(".dt-csv", box)?.addEventListener("click", () => csv(r, allCols, opt.csv));
      $(".dt-chart", box)?.addEventListener("click", () => chartDrawer(r, allCols, numCols, opt.chart));
      $(".dt-cols", box)?.addEventListener("click", () => {
        const d = drawer("Columns", `<p class="muted small">Tick the columns to show. Remembered for this table.</p><div class="colpick">${allCols.map((c) =>
          `<label class="switch"><input type="checkbox" data-k="${esc(c[0])}" ${hidden.has(c[0]) ? "" : "checked"}><span></span>${esc(c[1])}</label>`).join("")}</div>
          <div class="row" style="margin-top:12px"><button class="btn small" id="cp-all">Show all</button></div>`);
        const save = () => { try { localStorage.setItem(ckey, JSON.stringify([...hidden])); } catch (e) { /* ignore */ } draw(); };
        $$("[data-k]", d).forEach((x) => x.addEventListener("change", () => { x.checked ? hidden.delete(x.dataset.k) : hidden.add(x.dataset.k); save(); }));
        $("#cp-all", d).addEventListener("click", () => { hidden.clear(); $$("[data-k]", d).forEach((x) => (x.checked = true)); save(); });
      });
      $(".dt-prev", box)?.addEventListener("click", () => { page--; draw(); });
      $(".dt-next", box)?.addEventListener("click", () => { page++; draw(); });
      if (opt.onRow) $$("tbody tr", box).forEach((tr, i) => tr.addEventListener("click", (e) => { if (!e.target.closest("a")) opt.onRow(vis[i]); }));
    };
    draw();
    return box;
  }
  function csv(rows, cols, name) {
    const keys = cols ? cols.map((c) => c[0]) : Object.keys(rows[0] || {});
    const q = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const text = [keys.join(","), ...rows.map((r) => keys.map((k) => q(r[k])).join(","))].join("\n");
    download(text, `${name || "table"}.csv`, "text/csv");
  }
  // Chart any table: scatter (x vs y) or histogram, with outliers listed
  function chartDrawer(rows, allCols, numCols, pre = {}) {
    const labCol = allCols.find((c) => /sym/.test(c[2])) || allCols.find((c) => !numCols.includes(c));
    const opts = numCols.map((c) => [c[0], c[1]]);
    const d = drawer("Chart this table", `<div id="cdf"></div><div class="ch section"></div><div id="cdo"></div>`);
    const f = form($("#cdf", d), [
      { k: "kind", label: "Chart", type: "radio", options: [["scatter", "Scatter"], ["hist", "Histogram"]], value: numCols.length > 1 ? "scatter" : "hist" },
      { k: "x", label: "X axis / column", type: "select", options: opts, value: pre.x && opts.some((o) => o[0] === pre.x) ? pre.x : opts[0][0] },
      { k: "y", label: "Y axis", type: "select", options: opts, value: pre.y && opts.some((o) => o[0] === pre.y) ? pre.y : (opts[1] || opts[0])[0] }], () => go());
    const go = () => {
      const v = f.values(), host = $(".ch", d), lab = (r) => labCol ? String(r[labCol[0]] ?? "") : "";
      const xl = opts.find((o) => o[0] === v.x)[1], yl = opts.find((o) => o[0] === v.y)[1];
      $("#f-y", d).closest(".fld").style.display = v.kind === "hist" ? "none" : "";
      let outs = [];
      if (v.kind === "hist") {
        const vals = rows.map((r) => +r[v.x]).filter((x) => isFinite(x));
        outs = MXCharts.histogram(host, { values: vals, xlabel: xl }).outliers;
        outs = rows.filter((r) => outs.includes(+r[v.x])).map((r) => [lab(r), r[v.x], null]);
      } else {
        const pts = rows.map((r) => ({ x: +r[v.x], y: +r[v.y], label: lab(r), sym: labCol && /sym/.test(labCol[2]) ? r[labCol[0]] : null })).filter((p) => isFinite(p.x) && isFinite(p.y) && r0(p));
        outs = MXCharts.scatter(host, { points: pts, xlabel: xl, ylabel: yl, height: 360 }).filter((p) => p.out).map((p) => [p.label, p.x, p.y]);
      }
      $("#cdo", d).innerHTML = outs.length ? card(`Outliers (${outs.length})`, table(outs.slice(0, 50).map(([l, x, y]) => ({ l, x, y })),
        [["l", labCol ? labCol[1] : "Row", labCol && /sym/.test(labCol[2]) ? "symonly" : "text"], ["x", xl, "num2"]].concat(v.kind === "hist" ? [] : [["y", yl, "num2"]])),
        "far from the rest") : `<p class="muted small">No clear outliers.</p>`;
    };
    const r0 = (p) => p.x === p.x && p.y === p.y;
    go();
  }
  function download(text, name, type = "text/plain") {
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // columns from the server: [[key, label, type]]
  const LAB = () => (window.MXHELP && MXHELP.LABELS) || {};
  const cols = (spec) => (spec || []).map(([k, l, t]) => [k, (l == null || l === String(k).replace(/_/g, " ")) && LAB()[k] ? LAB()[k] : l, t || "text"]);

  // ---------- small components ----------
  const kpi = (label, value, delta, extra = "") => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div>${delta ? `<div class="delta">${delta}</div>` : ""}${extra}</div>`;
  const card = (title, body, aside = "", cls_ = "") => `<div class="card ${cls_}">${title ? `<div class="ct"><h2>${title}</h2>${aside ? `<span class="aside">${aside}</span>` : ""}</div>` : ""}${body}</div>`;
  const head = (title, sub = "", actions = "") => `<div class="head"><div><h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
  const note = (text, tone = "info") => `<div class="note ${tone}">${text}</div>`;
  const loading = () => `<div class="head"><div><div class="skeleton" style="width:180px;height:22px"></div><div class="skeleton" style="width:320px"></div></div></div><div class="grid g4">${'<div class="card"><div class="skeleton"></div><div class="skeleton" style="width:60%"></div></div>'.repeat(4)}</div>`;
  const spinner = (text = "Working…") => `<div class="empty"><span class="spin"></span> ${esc(text)}</div>`;

  // minimal markdown (headings, bold, italics, bullets, numbered, tables, links, code) — for reports
  function md(text) {
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*(\S.*?\S|\S)\*(?=\W|$)/g, "$1<i>$2</i>")
      .replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    const lines = String(text || "").split("\n"); let out = "", list = null, i = 0;
    const close = () => { if (list) { out += `</${list}>`; list = null; } };
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*\|/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        close(); const row = (x) => x.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const h = row(l); i += 2; const body = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(row(lines[i++]));
        out += `<div class="scroll"><table class="tbl"><thead><tr>${h.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
        continue;
      }
      let m;
      if ((m = l.match(/^(#{1,4})\s+(.*)/))) { close(); const n = Math.min(4, m[1].length + 1); out += `<h${n} class="md-h">${inline(m[2])}</h${n}>`; }
      else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
      else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
      else if (/^\s*(---|___)\s*$/.test(l)) { close(); out += "<hr>"; }
      else if (!l.trim()) { close(); }
      else { close(); out += `<p>${inline(l)}</p>`; }
      i++;
    }
    close();
    return `<div class="md">${out}</div>`;
  }

  // ---------- forms ----------
  /* fields: [{k, label, type: select|multi|number|text|toggle|date|textarea|radio, options:[[v,label]]|[v], value, help, min, max, step, placeholder, wide}] */
  function form(host, fields, onChange) {
    const id = (f) => `f-${f.k}`;
    const opts = (f) => (f.options || []).map((o) => Array.isArray(o) ? o : [o, o]);
    host.innerHTML = `<div class="form">${fields.map((f) => {
      const lab = `<label for="${id(f)}">${esc(f.label)}${f.help ? ` <span class="hint" title="${esc(f.help)}">?</span>` : ""}</label>`;
      const w = f.wide ? " wide" : f.narrow ? " narrow" : "";
      if (f.type === "toggle") return `<div class="fld tog${w}"><label class="switch"><input type="checkbox" id="${id(f)}" ${f.value ? "checked" : ""}><span></span>${esc(f.label)}</label>${f.help ? `<div class="fhelp">${esc(f.help)}</div>` : ""}</div>`;
      if (f.type === "select") return `<div class="fld${w}">${lab}<select class="in" id="${id(f)}">${opts(f).map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(f.value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
      if (f.type === "radio") return `<div class="fld${w}">${lab}<div class="seg" id="${id(f)}">${opts(f).map(([v, l]) => `<button type="button" data-v="${esc(v)}" class="${String(v) === String(f.value) ? "on" : ""}">${esc(l)}</button>`).join("")}</div></div>`;
      if (f.type === "multi") return `<div class="fld${w}">${lab}<div class="multi" id="${id(f)}"><input class="in" placeholder="${esc(f.placeholder || "Any")}" autocomplete="off"><div class="chips"></div><div class="mopts" hidden></div></div></div>`;
      if (f.type === "textarea") return `<div class="fld wide">${lab}<textarea class="in" id="${id(f)}" rows="${f.rows || 4}" placeholder="${esc(f.placeholder || "")}">${esc(f.value ?? "")}</textarea></div>`;
      const t = f.type === "number" ? "number" : f.type === "date" ? "date" : "text";
      return `<div class="fld${w}">${lab}<input class="in" type="${t}" id="${id(f)}" value="${esc(f.value ?? "")}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.step != null ? `step="${f.step}"` : ""} placeholder="${esc(f.placeholder || "")}"></div>`;
    }).join("")}</div>`;
    const state = {};
    fields.forEach((f) => {
      const el = $(`#${id(f)}`, host);
      if (f.type === "multi") {
        const sel = new Set(f.value || []), inp = $("input", el), chips = $(".chips", el), box = $(".mopts", el), all = opts(f);
        const drawChips = () => { chips.innerHTML = [...sel].map((v) => `<span class="chip sm" data-v="${esc(v)}">${esc((all.find((o) => String(o[0]) === String(v)) || [v, v])[1])} ×</span>`).join(""); };
        const drawOpts = () => { const q = inp.value.toLowerCase(); const m = all.filter(([v, l]) => !sel.has(v) && String(l).toLowerCase().includes(q)).slice(0, 60);
          box.innerHTML = m.map(([v, l]) => `<a data-v="${esc(v)}">${esc(l)}</a>`).join("") || `<span class="muted">No match</span>`; box.hidden = false; };
        inp.addEventListener("focus", drawOpts); inp.addEventListener("input", drawOpts);
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { const a = $("a", box); if (a) { a.dispatchEvent(new Event("mousedown")); } e.preventDefault(); } });
        inp.addEventListener("blur", () => setTimeout(() => (box.hidden = true), 150));
        box.addEventListener("mousedown", (e) => { const a = e.target.closest("a"); if (!a) return; e.preventDefault(); const o = all.find((x) => String(x[0]) === a.dataset.v); sel.add(o ? o[0] : a.dataset.v); inp.value = ""; drawChips(); drawOpts(); onChange && onChange(); });
        chips.addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (!c) return; [...sel].forEach((v) => { if (String(v) === c.dataset.v) sel.delete(v); }); drawChips(); onChange && onChange(); });
        drawChips(); state[f.k] = () => [...sel];
      } else if (f.type === "radio") {
        let cur = f.value; el.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; cur = b.dataset.v;
          $$("button", el).forEach((x) => x.classList.toggle("on", x === b)); onChange && onChange(); }); state[f.k] = () => cur;
      } else if (f.type === "toggle") { el.addEventListener("change", () => onChange && onChange()); state[f.k] = () => el.checked; }
      else if (f.type === "number") { el.addEventListener("change", () => onChange && onChange()); state[f.k] = () => el.value === "" ? null : Number(el.value); }
      else { el.addEventListener(f.type === "select" || f.type === "date" ? "change" : "input", debounce(() => onChange && onChange(), f.type === "text" ? 350 : 0)); state[f.k] = () => el.value; }
    });
    return { values: () => Object.fromEntries(Object.entries(state).map(([k, g]) => [k, g()])) };
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  // tabs: names[], render(i, host); remembers the open tab per page
  function tabs(host, names, render, key) {
    const saved = key ? +(sessionStorage.getItem(`mx-tab-${key}`) || 0) : 0;
    host.innerHTML = `<div class="tabs">${names.map((n, i) => `<button data-t="${i}">${esc(n)}</button>`).join("")}</div><div class="tabbody"></div>`;
    const body = $(".tabbody", host);
    const show = (i) => { $$(".tabs button", host).forEach((b) => b.classList.toggle("on", +b.dataset.t === i)); if (key) try { sessionStorage.setItem(`mx-tab-${key}`, i); } catch (e) { /* ignore */ }
      body.innerHTML = loading(); Promise.resolve(render(i, body)).catch((e) => (body.innerHTML = note(esc(e.message), "warn"))); };
    $(".tabs", host).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) show(+b.dataset.t); });
    show(Math.min(saved, names.length - 1));
  }

  // server-described sections: [{kind, ...}]
  function sections(host, list) {
    host.innerHTML = "";
    for (const s of list || []) host.appendChild(section(s));
    return host;
  }
  function section(s) {
    if (s.fold) { const det = document.createElement("details"); det.className = "fold section"; det.innerHTML = `<summary>${esc(s.fold === true ? (s.title || "More details") : s.fold)}</summary>`;
      const inner = section({ ...s, fold: null }); inner.classList.remove("section"); det.appendChild(inner); return det; }
    const wrap = document.createElement("div");
    wrap.className = s.kind === "grid" ? `grid g${s.cols || 2} section` : "section";
    if (s.kind === "grid") { (s.items || []).forEach((x) => { const e = section(x); e.classList.remove("section"); wrap.appendChild(e); }); return wrap; }
    if (s.kind === "kpis") { wrap.innerHTML = `<div class="grid g${Math.min(6, s.cols || s.items.length)}">${s.items.map((k) => kpi(esc(k.label), k.value == null ? "—" : esc(k.value), k.delta ? `<span class="${k.tone || ""}">${esc(k.delta)}</span>` : "")).join("")}</div>`; return wrap; }
    if (s.kind === "note") { wrap.innerHTML = note(s.html ? s.html : esc(s.text), s.tone || "info"); return wrap; }
    if (s.kind === "md") { wrap.innerHTML = card(s.title ? esc(s.title) : "", md(s.text), esc(s.aside || "")); return wrap; }
    if (s.kind === "table") {
      wrap.innerHTML = `<div class="card ${s.title ? "" : "flush"}">${s.title ? `<div class="ct"><h2>${esc(s.title)}</h2><span class="aside">${esc(s.aside || "")}</span></div>` : ""}</div>`;
      const c = $(".card", wrap);
      c.appendChild(dataTable(s.rows, cols(s.columns), { csv: s.csv, empty: s.empty, per: s.per, height: s.height, tools: s.tools, sort: s.sort, desc: s.desc, show: s.show, chart: s.chart }));
      if (s.note) c.insertAdjacentHTML("beforeend", `<p class="muted small">${esc(s.note)}</p>`);
      return wrap;
    }
    if (s.kind === "chart") {
      wrap.innerHTML = card(s.title ? esc(s.title) : "", `<div class="ch"></div>${s.note ? `<p class="muted small">${esc(s.note)}</p>` : ""}`, esc(s.aside || ""));
      requestAnimationFrame(() => MXCharts.draw($(".ch", wrap), s));
      return wrap;
    }
    if (s.kind === "news") { wrap.innerHTML = card(s.title ? esc(s.title) : "", newsList(s.items, s.empty), esc(s.aside || "")); return wrap; }
    wrap.textContent = ""; return wrap;
  }
  function newsList(items, empty = "No headlines.") {
    if (!items || !items.length) return `<div class="empty">${esc(empty)}</div>`;
    return `<div class="list">${items.map((n) => {
      const link = safeUrl(n.link), syms = (Array.isArray(n.symbols) ? n.symbols : String(n.symbols || "").split(",")).filter(Boolean);
      return `<div class="item">${link ? `<a class="t" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>` : `<span class="t">${esc(n.title)}</span>`}
      <div class="m"><span>${esc(n.source || "")}</span><span>${dt(n.published)}</span>
        ${syms.slice(0, 6).map((s) => `<a class="tag acc" href="#/stock/${encodeURIComponent(s)}">${esc(s)}</a>`).join("")}
        ${n.type ? `<span class="tag">${esc(n.type)}</span>${n.type_why ? `<span class="why">“${esc(n.type_why)}”</span>` : ""}` : ""}
        ${n.tone === "positive" || n.tone === "negative" ? `<span class="tag ${n.tone === "positive" ? "pos" : "neg"}">${esc(n.tone)}</span>${n.tone_why ? `<span class="why">“${esc(n.tone_why)}”</span>` : ""}` : ""}
        ${n.roundup ? `<span class="tag">round-up</span>` : ""}</div>${n.summary ? `<div class="sumy">${esc(String(n.summary).slice(0, 260))}</div>` : ""}</div>`; }).join("")}</div>`;
  }


  // editable table: rows [{}], columns [key] or [[key,label,{type,options,placeholder}]]; returns {rows()}
  function editTable(host, rows, columns, opt = {}) {
    const C = columns.map((c) => Array.isArray(c) ? { k: c[0], l: c[1] || c[0], ...(c[2] || {}) } : { k: c, l: String(c).replace(/_/g, " ") });
    let data = (rows || []).map((r) => ({ ...r }));
    const draw = () => {
      host.innerHTML = `<div class="scroll"><table class="tbl edit"><thead><tr>${C.map((c) => `<th>${esc(c.l)}</th>`).join("")}<th></th></tr></thead><tbody>${data.map((r, i) => `<tr>${C.map((c) =>
        c.options ? `<td><select class="in" data-r="${i}" data-c="${esc(c.k)}">${["", ...c.options].map((o) => `<option ${String(r[c.k] ?? "") === String(o) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></td>`
          : `<td><input class="in" data-r="${i}" data-c="${esc(c.k)}" value="${esc(r[c.k] ?? "")}" placeholder="${esc(c.placeholder || "")}"${c.type === "date" ? ' type="date"' : ""}></td>`).join("")}
        <td><button class="btn small" data-del="${i}" title="Delete row">✕</button></td></tr>`).join("")}</tbody></table></div>
        <div class="row" style="margin-top:10px"><button class="btn small et-add">Add row</button>${opt.onSave ? `<button class="btn small primary et-save">${esc(opt.saveLabel || "Save")}</button>` : ""}</div>`;
      $$("[data-c]", host).forEach((x) => x.addEventListener("change", () => (data[+x.dataset.r][x.dataset.c] = x.value)));
      $$("[data-del]", host).forEach((x) => x.addEventListener("click", () => { data.splice(+x.dataset.del, 1); draw(); }));
      $(".et-add", host).addEventListener("click", () => { data.push(Object.fromEntries(C.map((c) => [c.k, ""]))); draw(); });
      $(".et-save", host)?.addEventListener("click", () => opt.onSave(data));
    };
    draw();
    return { rows: () => data };
  }

  // drawer
  function drawer(title, inner) {
    $(".drawer")?.remove();
    const d = document.createElement("div"); d.className = "drawer";
    d.innerHTML = `<header><h2>${title}</h2><span class="spacer"></span><button class="btn small dclose">Close</button></header><div class="dbody">${inner}</div>`;
    document.body.appendChild(d); $(".dclose", d).addEventListener("click", () => d.remove());
    document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { d.remove(); document.removeEventListener("keydown", k); } });
    return d;
  }

  // ---------- page guide (How to use) ----------
  function addGuide(el, page) {
    const g = window.MXHELP && MXHELP.GUIDES[page], h = $(".head", el);
    if (!g || !h || $(".guide-btn", h)) return;
    let act = $(".actions", h); if (!act) { act = document.createElement("div"); act.className = "actions"; h.appendChild(act); }
    act.insertAdjacentHTML("afterbegin", `<button class="btn guide-btn" title="What this page is for, how to use it, and the terms it uses">? How to use</button>`);
    const panel = document.createElement("div"); panel.className = "guide card section"; panel.hidden = true; h.after(panel);
    const G = MXHELP.GLOSSARY;
    tabs(panel, ["What it is", "How to use", "Glossary", "The idea"], (i, b) => {
      b.innerHTML = i === 0 ? `<p>${esc(g.what)}</p>` : i === 1 ? `<ol>${g.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>`
        : i === 2 ? (g.terms.length ? `<dl class="kv gl-list">${g.terms.filter((t) => G[t]).map((t) => `<dt>${esc(t)}</dt><dd>${esc(G[t])}</dd>`).join("")}</dl>` : `<p class="muted">No special terms on this page.</p>`)
        : `<p>${esc(g.idea)}</p>`;
    }, `guide-${page}`);
    let open = false; try { open = sessionStorage.getItem(`mx-guide-${page}`) === "1"; } catch (e) { /* ignore */ }
    panel.hidden = !open;
    $(".guide-btn", h).addEventListener("click", () => { panel.hidden = !panel.hidden; try { sessionStorage.setItem(`mx-guide-${page}`, panel.hidden ? "0" : "1"); } catch (e) { /* ignore */ } });
  }

  // hover definitions: wraps glossary terms found in headings, column titles, KPI labels and form labels
  let GLRE = null;
  function glossarize(root) {
    const G = window.MXHELP && MXHELP.GLOSSARY; if (!G) return;
    if (!GLRE) { const keys = Object.keys(G).sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      GLRE = new RegExp(`(^|[^A-Za-z0-9])(${keys.join("|")})(?![A-Za-z0-9])`); }
    $$("th, .kpi .label, .ct h2, .fld > label, .tabs button, .head .sub", root).forEach((e) => {
      if (e.dataset.gl || e.closest(".guide") || e.querySelector(".switch, input, select") || e.classList.contains("switch")) return; e.dataset.gl = "1";
      const walk = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); let n;
      while ((n = walk.nextNode())) { const m = GLRE.exec(n.nodeValue); if (!m) continue;
        const at = m.index + m[1].length, rest = n.splitText(at); rest.splitText(m[2].length);
        const sp = document.createElement("span"); sp.className = "gl"; sp.title = G[m[2]]; sp.textContent = m[2]; rest.replaceWith(sp); break; }
    });
  }
  let glT; new MutationObserver(() => { clearTimeout(glT); glT = setTimeout(() => glossarize($("#page")), 120); }).observe(document.documentElement, { childList: true, subtree: true });

  // ---------- navigation ----------
  const NAV = [
    ["Main", [["overview", "Overview"], ["market-lab", "Market Lab"], ["stock", "Stock"], ["desk", "Stock Desk"], ["positions", "Positions & risk"], ["data", "Data health"]]],
    ["Market", [["market", "Market"], ["market-news", "Market news"], ["screener", "Screener"]]],
    ["Agents", [["agents", "Agents"], ["signals", "Signals"], ["strategies", "Strategies"], ["validation", "Validation"]]],
    ["Tools", [["search", "Search"], ["ask", "Ask"], ["documents", "Documents"], ["records", "Reports & logs"], ["settings", "Settings"]]],
    ["Labs", [["debate", "Debate"], ["scorecard", "Forecast scorecard"], ["kronos", "Kronos"], ["lab", "Strategy Lab"], ["screener-research", "Screener research"]]],
  ];
  const PAGES = {};
  function drawNav(cur) {
    $("#nav").innerHTML = NAV.map(([g, items]) => `<div class="grp">${g}</div>` + items.map(([k, label]) =>
      `<a href="#/${k}" class="${k === cur ? "on" : ""}">${label}</a>`).join("")).join("");
  }

  // ---------- theme ----------
  function setupTheme() {
    const apply = (t) => { if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
      $$("#theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === t)); };
    let t = "auto"; try { t = localStorage.getItem("mx-theme") || "auto"; } catch (e) { /* ignore */ }
    $("#theme").innerHTML = [["light", "☀"], ["auto", "Auto"], ["dark", "☾"]].map(([v, l]) => `<button data-v="${v}" title="${v === "auto" ? "Follow the Mac's setting" : v === "light" ? "Light" : "Dark"}">${l}</button>`).join("");
    $("#theme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; try { localStorage.setItem("mx-theme", b.dataset.v); } catch (er) { /* ignore */ } apply(b.dataset.v); render(); });
    apply(t);
  }

  // ---------- status bar ----------
  let statusTimer = null, lastStatus = null;
  async function refreshStatus(soon) {
    clearTimeout(statusTimer);
    try {
      const s = await api("status");
      const wasRunning = lastStatus && lastStatus.running.length;
      lastStatus = s; drawStatus(s);
      if (wasRunning && s.running.length < wasRunning) { toast("Job finished"); if (PAGES[route().page]?.reloadOnJob) render(); }
    } catch (e) { $("#status").innerHTML = `<span class="chip bad">Server not reachable — is MarketX still running?</span>`; }
    statusTimer = setTimeout(refreshStatus, soon || (lastStatus && lastStatus.running.length) ? 2500 : 20000);
  }
  let jobsOpen = false;
  document.addEventListener("click", () => { if (jobsOpen) { jobsOpen = false; const d = document.getElementById("jobsdd"); if (d) d.hidden = true; } });
  function drawStatus(s) {
    const fresh = s.last_bar ? `<span class="chip"><span class="dot ${s.fresh ? "ok" : "bad"}"></span>Prices to ${day(s.last_bar)}</span>` : `<span class="chip bad">No prices yet</span>`;
    const stale = s.stale && s.stale.stocks && s.stale.stocks.length ? `<button class="chip warn" id="stale">${s.stale.stocks.length} stocks missing today's bar</button>` : "";
    const q = s.quality || {};
    const qual = q.quarantined && q.quarantined.length ? `<a class="chip warn" href="#/data">${q.quarantined.length} paused for data errors</a>` :
      q.status === "errors" ? `<a class="chip" href="#/data">${q.errors} data issues to review</a>` : "";
    const fr = (j) => j.steps ? Math.min(0.98, (j.step - 1) / j.steps + (j.frac ?? 0.5) / j.steps) : 0.05;
    const rows = s.running.map((j) => `<div class="jobrow"><span class="dot run"></span><span class="jt"><b>${esc(j.title)}</b>${j.label && j.label !== j.title ? ` · ${esc(j.label)}` : ""}${j.steps > 1 ? ` · ${j.step}/${j.steps}` : ""} · ${mmss(j.elapsed)}</span>
        <span class="bar"><i style="width:${(fr(j) * 100).toFixed(0)}%"></i></span>
        <button class="btn small" data-log="${j.lane}">Log</button><button class="btn small" data-stop="${j.lane}">Stop</button></div>`).join("");
    const j0 = s.running[0];
    const jobs = j0 ? `<span class="jobs-wrap"><button class="chip jobs-btn" id="jobsbtn" aria-expanded="${jobsOpen}"><span class="dot run"></span>${s.running.length > 1 ? `${s.running.length} jobs running` : esc(j0.title)} · ${mmss(j0.elapsed)}
        <span class="bar"><i style="width:${(fr(j0) * 100).toFixed(0)}%"></i></span> ▾</button><div class="jobs-dd" id="jobsdd" ${jobsOpen ? "" : "hidden"}>${rows}</div></span>` : "";
    if (!j0) jobsOpen = false;
    const busyMain = s.running.some((j) => j.lane === "main");
    $("#status").innerHTML = `${fresh}${stale}${qual}<span class="job">${jobs}${busyMain ? "" : `<button class="btn primary small" id="runall" title="Download the latest prices, news and filings, then run every agent">Fetch + run all</button>`}</span>`;
    $("#jobsbtn")?.addEventListener("click", (e) => { e.stopPropagation(); jobsOpen = !jobsOpen; $("#jobsdd").hidden = !jobsOpen; $("#jobsbtn").setAttribute("aria-expanded", jobsOpen); });
    $("#jobsdd")?.addEventListener("click", (e) => e.stopPropagation());
    $("#runall")?.addEventListener("click", () => startJob("fetch+pipeline"));
    $("#stale")?.addEventListener("click", () => showStale(s.stale));
    $$("[data-stop]").forEach((b) => b.addEventListener("click", async () => { if (!confirm("Stop this job?")) return; await post("job/stop", { lane: b.dataset.stop }, "Stopped"); refreshStatus(true); }));
    $$("[data-log]").forEach((b) => b.addEventListener("click", () => openLog(b.dataset.log)));
  }
  function showStale(st) {
    const d = drawer(`No price bar for ${esc(day(st.day))}`, `<div class="host"></div><p class="muted small">These stocks are left out of today's screener and agents. Run Fetch data again later to fill them in.</p>`);
    $(".host", d).appendChild(dataTable(st.stocks, [["symbol", "Stock", "symonly"], ["last_bar", "Last bar", "date"], ["reason", "Why", "long"]], { tools: false }));
  }
  async function openLog(lane, file) {
    const d = drawer("Job log", `<pre class="logpre">Loading…</pre>`);
    const load = async () => { if (!document.body.contains(d)) return; const j = await api(file ? `job/file?${qs({ file })}` : `job/log?${qs({ lane })}`);
      const pre = $(".logpre", d); const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      $("h2", d).textContent = `Log · ${j.file || ""}`; pre.textContent = j.lines.join("\n") || "(empty)"; if (atEnd) pre.scrollTop = pre.scrollHeight; if (j.running && !file) setTimeout(load, 2000); };
    load();
  }

  // ---------- search ----------
  function setupSearch() {
    const inp = $("#q"), box = $("#qres"); let sel = 0, items = [], t = null;
    const draw = () => { box.hidden = !items.length; box.innerHTML = items.map((r, i) => `<a href="#/stock/${encodeURIComponent(r.symbol)}" class="${i === sel ? "sel" : ""}"><b>${esc(r.symbol)}</b><span>${esc(r.company)}</span></a>`).join(""); };
    inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => { items = await api(`search?q=${encodeURIComponent(inp.value)}`).catch(() => []); sel = 0; draw(); }, 120); });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter" && items[sel]) { location.hash = `#/stock/${encodeURIComponent(items[sel].symbol)}`; inp.value = ""; items = []; draw(); inp.blur(); }
      else if (e.key === "Escape") { items = []; draw(); inp.blur(); }
    });
    box.addEventListener("click", () => { inp.value = ""; items = []; draw(); });
    document.addEventListener("keydown", (e) => { if (e.key === "/" && document.activeElement !== inp && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); inp.focus(); } });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) { items = []; draw(); } });
  }

  // ---------- suggestions for any search box ----------
  /* MX.suggest(input, { kinds: "stocks,sectors,tags,desk", recent: "key", examples: [..], onPick(item), keepText }) shows a dropdown like the main
     stock search: matches from /api/suggest, plus your recent entries (and examples) for this box. Arrow keys + Enter, or click. */
  const recentGet = (k) => { try { return JSON.parse(localStorage.getItem(`mx-recent-${k}`) || "[]"); } catch (e) { return []; } };
  function remember(k, v) { if (!k || !v) return; try { const r = [String(v), ...recentGet(k).filter((x) => x !== String(v))].slice(0, 12); localStorage.setItem(`mx-recent-${k}`, JSON.stringify(r)); } catch (e) { /* ignore */ } }
  function suggest(inp, o = {}) {
    if (!inp || inp._sugg) return; inp._sugg = true;
    const wrap = document.createElement("span"); wrap.className = "sugg-wrap"; inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
    if (inp.classList.contains("grow") || inp.style.flex) wrap.style.flex = inp.style.flex || "1";
    const box = document.createElement("div"); box.className = "qres sugg"; box.hidden = true; wrap.appendChild(box);
    let items = [], sel = -1, t = null;
    const draw = () => { box.hidden = !items.length; box.innerHTML = items.map((r, i) => `<a role="option" data-i="${i}" class="${i === sel ? "sel" : ""}" style="cursor:pointer"><b>${esc(r.label)}</b><span>${esc(r.sub || "")}</span></a>`).join(""); };
    const fetchItems = async () => {
      const q = inp.value.trim(), ql = q.toLowerCase();
      const rec = recentGet(o.recent || "").filter((x) => !ql || x.toLowerCase().includes(ql)).slice(0, 5).map((x) => ({ value: x, label: x, sub: "recent", kind: "recent" }));
      const ex = (o.examples || []).filter((x) => !ql || x.toLowerCase().includes(ql)).slice(0, 4).map((x) => ({ value: x, label: x, sub: "example", kind: "example" }));
      let srv = [];
      if (o.kinds && (q || /tags|desk/.test(o.kinds))) srv = await api(`suggest?${qs({ q, kinds: o.kinds })}`).catch(() => []);
      items = [...rec, ...srv, ...(srv.length ? [] : ex)].slice(0, 12); sel = -1; draw();
    };
    const pick = (r) => { if (!r) return; if (!o.keepText) inp.value = r.value; items = []; draw(); remember(o.recent, r.value); o.onPick && o.onPick(r); };
    inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(fetchItems, 140); });
    inp.addEventListener("focus", () => { if (!inp.value) fetchItems(); });
    inp.addEventListener("keydown", (e) => {
      if (box.hidden) { if (e.key === "Enter" && o.recent) remember(o.recent, inp.value.trim()); return; }
      if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(-1, sel - 1); draw(); e.preventDefault(); }
      else if (e.key === "Escape") { items = []; draw(); }
      else if (e.key === "Enter") { if (sel >= 0) { e.preventDefault(); e.stopImmediatePropagation(); pick(items[sel]); } else { items = []; draw(); if (o.recent) remember(o.recent, inp.value.trim()); } }
    }, true);
    box.addEventListener("mousedown", (e) => { const a = e.target.closest("a"); if (a) { e.preventDefault(); pick(items[+a.dataset.i]); } });
    inp.addEventListener("blur", () => setTimeout(() => { items = []; draw(); }, 150));
  }

  // ---------- router ----------
  function route() { const [, page = "overview", ...rest] = location.hash.split("/"); return { page: page || "overview", arg: rest.length ? decodeURIComponent(rest.join("/")) : undefined }; }
  let renderSeq = 0;
  async function render() {
    const { page, arg } = route(), seq = ++renderSeq;
    const el = document.createElement("div");          // a fresh host per render: a slow earlier page can't overwrite this one
    $("#page").replaceChildren(el);
    drawNav(page);
    const p = PAGES[page];
    if (!p) { el.innerHTML = head("Not found") + note("No such page.", "warn"); return; }
    document.title = `${p.title || "MarketX"} · MarketX`;
    el.innerHTML = loading();
    try { await p.render(el, arg, () => seq !== renderSeq); if (seq === renderSeq) addGuide(el, page); }
    catch (e) { if (seq === renderSeq) el.innerHTML = head(esc(p.title || "Error")) + note(`Couldn't load this page: ${esc(e.message)}`, "warn"); console.error(e); }
  }
  window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
  let rz; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => window.dispatchEvent(new Event("mx-resize")), 200); });

  window.MX = { api, qs, post, toast, startJob, esc, num, pct, cls, inr, day, dt, mmss, safeUrl, isNum, $, $$, table, dataTable, csv, download,
                cols, kpi, card, nameOf, named, chartDrawer, head, note, loading, spinner, md, form, tabs, sections, section, newsList, drawer, editTable, openLog, fmt, debounce,
                page: (k, def) => (PAGES[k] = def), pageDef: (k) => PAGES[k], suggest, remember, status: () => lastStatus, refreshStatus, route, render };
  document.addEventListener("DOMContentLoaded", async () => { setupTheme(); setupSearch(); refreshStatus();
    try { NAMES = await api("meta/names"); } catch (e) { /* names are a nicety */ } render(); });
})();
