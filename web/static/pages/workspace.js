/* Chart workspace (Strategy Lab → Chart workspace): saved charts with drawings, notes, tags, status, a snapshot of what
   MarketX said at save time, and a review of what happened next. Save buttons call MX.saveChart. Data: /api/workspace/*. */
(function () {
  const { api, post, esc, num, pct, cls, inr, day, $, $$, head, note, card, toast, md } = MX;
  const TOOLS = [["off", "Pointer"], ["hline", "Level"], ["trend", "Trend line"], ["rect", "Zone"], ["arrow", "Arrow"], ["text", "Text"], ["entry", "Entry ▲"], ["exit", "Exit ▼"]];
  const TWO = new Set(["trend", "rect", "arrow"]);
  const SWATCH = ["#4f46e5", "#059669", "#dc2626", "#0ea5e9", "#f59e0b", "#6b7280"];
  const uid = () => Math.random().toString(36).slice(2, 10);

  MX.saveChart = async (o) => {
    try { const r = await post("workspace/save", o);
      toast(`Saved to Strategy Lab → Chart workspace`); return r.id; }
    catch (e) { return null; }
  };

  // ---------- list ----------
  async function listPage(el) {
    const d = await api("workspace/list");
    el.innerHTML = head("Chart workspace", "Charts you saved, with your drawings and notes, plus what MarketX said at the time. Save one from any stock's Chart, Intraday or Trade plan tab.",
      `<a class="btn" href="#/lab">Strategies</a>`) +
      `<div class="panel row"><input class="in" id="wq" placeholder="Filter by stock, title or tag" style="max-width:280px"><select class="in" id="ws" style="max-width:160px"><option value="">Any status</option>${d.statuses.map((s) => `<option>${s}</option>`).join("")}</select>
        <span class="muted small">${d.items.length} saved</span></div><div id="wl" class="section"></div>`;
    const draw = (items) => { $("#wl", el).innerHTML = !items.length ? note("Nothing saved yet. Open a stock → Chart (or Trade plan) → Save to workspace.") :
      `<div class="grid g3">${items.map((x) => `<a class="card ws-tile" href="#/lab/charts/${encodeURIComponent(x.id)}"><div class="row" style="justify-content:space-between"><b>${esc(x.symbol)}</b><span class="tag">${esc(x.status)}</span></div>
        <div>${esc(x.title || "")}</div><div class="muted small">${esc(x.timeframe)} · saved ${esc(String(x.saved_at).slice(0, 16))} · ${x.drawings} drawing${x.drawings === 1 ? "" : "s"}${x.has_notes ? " · notes" : ""}${x.lean ? ` · lean then: ${esc(x.lean)}` : ""}</div>
        <div>${x.tags.map((t) => `<span class="tag acc">${esc(t)}</span>`).join(" ")}</div></a>`).join("")}</div>`; };
    const refilter = () => { const q = $("#wq", el).value.toLowerCase(), s = $("#ws", el).value;
      draw(d.items.filter((x) => (!s || x.status === s) && (!q || `${x.symbol} ${x.title} ${x.tags.join(" ")}`.toLowerCase().includes(q)))); };
    $("#wq", el).addEventListener("input", refilter); $("#ws", el).addEventListener("change", refilter);
    draw(d.items);
  }

  // ---------- drawing layer ----------
  function idxOf(t, key) { if (!key) return t.length - 1; const k = t.indexOf(key); if (k >= 0) return k;
    let best = 0; for (let i = 0; i < t.length; i++) if (t[i] <= key) best = i; return best; }
  function render(map, drawings, sel) {
    const NS = "http://www.w3.org/2000/svg", g0 = map.svg.querySelector("g.ws") || map.svg.appendChild(document.createElementNS(NS, "g"));
    g0.setAttribute("class", "ws"); g0.innerHTML = "";
    const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); g0.appendChild(e); return e; };
    const X = (p) => map.x(idxOf(map.t, p.t)), Y = (p) => map.y(p.p), right = map.W - map.padR;
    drawings.forEach((d) => { const c = d.color, a = d.points[0], b = d.points[1] || d.points[0], w = d.id === sel ? 3 : 2;
      if (d.type === "hline") { mk("line", { x1: map.padL, x2: right, y1: Y(a), y2: Y(a), stroke: c, "stroke-width": w });
        const t = mk("text", { x: right - 4, y: Y(a) - 4, "text-anchor": "end", style: `fill:${c}`, "font-size": 11, "font-weight": 600 }); t.textContent = `${d.text ? d.text + " " : ""}${num(a.p, 2)}`; }
      else if (d.type === "trend") mk("line", { x1: X(a), x2: X(b), y1: Y(a), y2: Y(b), stroke: c, "stroke-width": w });
      else if (d.type === "rect") mk("rect", { x: Math.min(X(a), X(b)), y: Math.min(Y(a), Y(b)), width: Math.abs(X(b) - X(a)) || 2, height: Math.abs(Y(b) - Y(a)) || 2, fill: c, opacity: 0.14, stroke: c, "stroke-width": w - 1 });
      else if (d.type === "arrow") { mk("line", { x1: X(a), x2: X(b), y1: Y(a), y2: Y(b), stroke: c, "stroke-width": w });
        const ang = Math.atan2(Y(b) - Y(a), X(b) - X(a)), L = 10, p1 = [X(b) - L * Math.cos(ang - 0.4), Y(b) - L * Math.sin(ang - 0.4)], p2 = [X(b) - L * Math.cos(ang + 0.4), Y(b) - L * Math.sin(ang + 0.4)];
        mk("polygon", { points: `${X(b)},${Y(b)} ${p1.join(",")} ${p2.join(",")}`, fill: c }); }
      else if (d.type === "text") { const t = mk("text", { x: X(a), y: Y(a), style: `fill:${c}`, "font-size": 12, "font-weight": 600, "paint-order": "stroke", stroke: "var(--surface)", "stroke-width": 3 }); t.textContent = d.text || "note"; }
      else if (d.type === "entry" || d.type === "exit") { const up = d.type === "entry", cx = X(a), cy = Y(a), s = up ? 1 : -1;
        mk("polygon", { points: `${cx},${cy} ${cx - 7},${cy + s * 13} ${cx + 7},${cy + s * 13}`, fill: c });
        const t = mk("text", { x: cx, y: cy + s * 26, "text-anchor": "middle", style: `fill:${c}`, "font-size": 10.5, "font-weight": 700 }); t.textContent = `${d.text || (up ? "ENTRY" : "EXIT")} ${num(a.p, 2)}`; } });
    return g0;
  }

  // ---------- editor ----------
  async function editor(el, id) {
    const d = await api(`workspace/chart?id=${encodeURIComponent(id)}`);
    const x = d.item, rv = d.review, snap = x.snapshot || {};
    let drawings = x.drawings || [], tool = "off", color = SWATCH[0], first = null, sel = null, showPlan = true, map = null;
    const hist = [];
    el.innerHTML = head(`<input class="in ws-title" id="wt" value="${esc(x.title || "")}">`, `<a href="#/stock/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a> · ${esc(x.timeframe)} · saved ${esc(String(x.saved_at).slice(0, 16))} (last bar ${esc(day(x.saved_day))})`,
      `<button class="btn primary" id="wsave">Save</button><button class="btn" id="wpng">Export PNG</button><button class="btn" id="wdel">Delete</button><a class="btn" href="#/lab/charts">All charts</a>`) +
      `<div class="card"><div class="row ws-tools">${TOOLS.map(([k, l]) => `<button class="btn small ${k === "off" ? "on" : ""}" data-tool="${k}">${l}</button>`).join("")}
          <input class="in" id="wtxt" placeholder="Label (optional)" style="width:140px">
          <span class="ws-sw">${SWATCH.map((c, i) => `<button class="sw ${i ? "" : "on"}" data-c="${c}" style="background:${c}" title="${c}"></button>`).join("")}</span>
          <button class="btn small" id="wundo">Undo</button><button class="btn small" id="wclear">Clear all</button>
          <label class="switch" style="margin-left:auto"><input type="checkbox" id="wplan" checked><span></span>Plan at save</label></div>
        <div id="wch" class="section"></div><p class="muted small" id="whint">Pick a tool, then click on the chart (two clicks for trend lines, zones and arrows). The dashed line marks the day you saved.</p></div>
      <div class="split section" style="grid-template-columns:minmax(0,1fr) 340px"><div>
          <div class="card"><div class="ct"><h2>Notes</h2><span class="aside muted small">Markdown</span></div><textarea class="in" id="wnotes" rows="9" placeholder="What do you see? Why this level? What would change your mind?">${esc(x.notes || "")}</textarea>
            <details class="fold" style="margin-top:8px"><summary>Preview</summary><div id="wprev" style="padding:0 14px 10px"></div></details></div>
          <div class="card section"><div class="ct"><h2>Drawings</h2></div><div id="wdl"></div></div></div>
        <div><div class="card"><div class="ct"><h2>Status and tags</h2></div>
            <div class="fld"><label>Status</label><select class="in" id="wst">${d.statuses.map((s) => `<option ${s === x.status ? "selected" : ""}>${s}</option>`).join("")}</select></div>
            <div class="fld" style="margin-top:8px"><label>Tags (comma-separated)</label><input class="in" id="wtags" value="${esc((x.tags || []).join(", "))}"></div>
            <div class="fld" style="margin-top:8px"><label>Your trade (optional)</label><div class="row">
              <select class="in" id="tside" style="width:90px"><option value="">—</option><option ${x.trade?.side === "long" ? "selected" : ""}>long</option><option ${x.trade?.side === "short" ? "selected" : ""}>short</option></select>
              <input class="in" id="tin" type="number" step="0.05" placeholder="Entry" value="${esc(x.trade?.entry ?? "")}" style="width:90px"><input class="in" id="tout" type="number" step="0.05" placeholder="Exit" value="${esc(x.trade?.exit ?? "")}" style="width:90px">
              <input class="in" id="tqty" type="number" placeholder="Qty" value="${esc(x.trade?.qty ?? "")}" style="width:70px"></div><div class="muted small" id="tres"></div></div></div>
          <div class="card section"><div class="ct"><h2>What happened next</h2></div>${rv && rv.sessions ? `<dl class="kv"><dt>Sessions since save</dt><dd>${rv.sessions}</dd>
              <dt>Change since save</dt><dd class="${cls(rv.change_pct)}">${pct(rv.change_pct, 2)}</dd><dt>Next session (close-to-close)</dt><dd class="${cls(rv.next_cc_pct)}">${pct(rv.next_cc_pct, 2)}</dd>
              <dt>Next session (open-to-close)</dt><dd class="${cls(rv.next_oc_pct)}">${pct(rv.next_oc_pct, 2)}</dd></dl>` : `<p class="muted">No sessions since you saved yet.</p>`}</div>
          <div class="card section"><div class="ct"><h2>What MarketX said at save</h2></div>${snapHtml(snap)}</div></div></div>`;
    const drawChart = () => {
      if (!d.bars) { $("#wch", el).innerHTML = note("No prices for this chart (for 5-minute charts, download the 5-minute bars first).", "warn"); return; }
      const lv = [];
      if (showPlan && snap.plan) { const p = snap.plan.intraday || snap.plan.swing;
        (p?.legs || []).filter((l) => l.price != null).forEach((l) => lv.push({ price: l.price, label: l.leg + (p.lean === "unclear" ? (l.side === "short" ? "↓" : "↑") : ""), color: l.leg === "SL" ? "down" : l.leg.startsWith("T") ? "blue" : "up", from: Math.max(0, (rv?.saved_index ?? d.bars.t.length - 1) - 8), fit: false })); }
      map = MXCharts.priceChart($("#wch", el), d.bars, { mode: "candle", height: 440, intraday: d.bars.intraday, levels: lv, vmarks: rv ? [{ i: rv.saved_index, label: "saved" }] : [] });
      if (!map) return;
      render(map, drawings, sel);
      // capture layer for drawing (above the hover overlay)
      const NS = "http://www.w3.org/2000/svg", cap = document.createElementNS(NS, "rect");
      Object.entries({ x: map.padL, y: map.padT, width: map.W - map.padL - map.padR, height: map.priceH, fill: "transparent", class: "ws-cap" }).forEach(([k, v]) => cap.setAttribute(k, v));
      cap.style.pointerEvents = tool === "off" ? "none" : "all"; cap.style.cursor = "crosshair"; map.svg.appendChild(cap);
      const at = (ev) => { const r = map.svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * map.W, py = ((ev.clientY - r.top) / r.height) * map.H;
        return { t: map.t[map.i(px)], p: +map.p(py).toFixed(2) }; };
      cap.addEventListener("click", (ev) => { const pt = at(ev), text = $("#wtxt", el).value.trim();
        if (TWO.has(tool) && !first) { first = pt; $("#whint", el).textContent = "Now click the second point."; return; }
        hist.push(JSON.stringify(drawings));
        drawings = drawings.concat([{ id: uid(), type: tool, points: TWO.has(tool) ? [first, pt] : [pt], color: tool === "exit" && color === SWATCH[0] ? "#dc2626" : tool === "entry" && color === SWATCH[0] ? "#059669" : color, text }]);
        first = null; $("#whint", el).textContent = "Added. Keep clicking to add more, or pick Pointer to stop."; render(map, drawings, sel); list(); });
      cap.addEventListener("mousemove", (ev) => { if (!first) return; const pt = at(ev); render(map, drawings.concat([{ id: "tmp", type: tool, points: [first, pt], color, text: "" }]), sel); });
    };
    const list = () => { $("#wdl", el).innerHTML = !drawings.length ? `<p class="muted small">No drawings yet.</p>` :
      `<table class="tbl"><tbody>${drawings.map((g) => `<tr data-id="${g.id}" class="click"><td><span class="ws-dot" style="background:${g.color}"></span> ${esc(TOOLS.find((t) => t[0] === g.type)?.[1] || g.type)}</td>
        <td>${g.points.map((p) => `${esc(String(p.t).slice(0, 16))} @ ${num(p.p, 2)}`).join(" → ")}</td><td>${esc(g.text || "")}</td><td class="r"><button class="btn small" data-del="${g.id}">✕</button></td></tr>`).join("")}</tbody></table>`;
      $$("[data-del]", el).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); hist.push(JSON.stringify(drawings)); drawings = drawings.filter((g) => g.id !== b.dataset.del); sel = null; render(map, drawings, sel); list(); }));
      $$("#wdl tr[data-id]", el).forEach((r) => r.addEventListener("click", () => { sel = sel === r.dataset.id ? null : r.dataset.id; render(map, drawings, sel); })); };
    $$("[data-tool]", el).forEach((b) => b.addEventListener("click", () => { tool = b.dataset.tool; first = null; $$("[data-tool]", el).forEach((y) => y.classList.toggle("on", y === b));
      const cap = $(".ws-cap", el); if (cap) cap.style.pointerEvents = tool === "off" ? "none" : "all";
      $("#whint", el).textContent = tool === "off" ? "Pointer: hover the chart to read prices." : TWO.has(tool) ? "Click the first point, then the second." : "Click where it should go."; }));
    $$(".sw", el).forEach((b) => b.addEventListener("click", () => { color = b.dataset.c; $$(".sw", el).forEach((y) => y.classList.toggle("on", y === b)); }));
    $("#wundo", el).addEventListener("click", () => { if (!hist.length) return; drawings = JSON.parse(hist.pop()); render(map, drawings, sel); list(); });
    $("#wclear", el).addEventListener("click", () => { if (!drawings.length || !confirm("Remove every drawing on this chart?")) return; hist.push(JSON.stringify(drawings)); drawings = []; render(map, drawings, sel); list(); });
    $("#wplan", el).addEventListener("change", (e) => { showPlan = e.target.checked; drawChart(); });
    const prev = () => ($("#wprev", el).innerHTML = md($("#wnotes", el).value || "_No notes yet._"));
    $("#wnotes", el).addEventListener("input", MX.debounce(prev, 300)); prev();
    const tres = () => { const s = $("#tside", el).value, a = +$("#tin", el).value, b = +$("#tout", el).value, q = +$("#tqty", el).value;
      $("#tres", el).innerHTML = s && a && b ? (() => { const r = (s === "long" ? b / a - 1 : a / b - 1) * 100; return `Result <b class="${cls(r)}">${pct(r, 2)}</b>${q ? ` · ${inr((s === "long" ? b - a : a - b) * q, 0)}` : ""} (before costs)`; })() : ""; };
    ["#tside", "#tin", "#tout", "#tqty"].forEach((k) => $(k, el).addEventListener("input", tres)); tres();
    $("#wsave", el).addEventListener("click", async () => {
      await post("workspace/save", { id, title: $("#wt", el).value, notes: $("#wnotes", el).value, status: $("#wst", el).value, drawings,
        tags: $("#wtags", el).value.split(",").map((t) => t.trim()).filter(Boolean),
        trade: { side: $("#tside", el).value || null, entry: +$("#tin", el).value || null, exit: +$("#tout", el).value || null, qty: +$("#tqty", el).value || null } }, "Saved"); });
    $("#wdel", el).addEventListener("click", async () => { if (!confirm("Delete this saved chart, its drawings and notes?")) return; await post("workspace/delete", { id }, "Deleted"); location.hash = "#/lab/charts"; });
    $("#wpng", el).addEventListener("click", () => exportPng($("#wch svg", el), `${x.symbol}_${x.saved_day}.png`));
    drawChart(); list(); window.addEventListener("mx-resize", drawChart);
  }

  function snapHtml(s) {
    const ml = s.market_lab, p = s.plan;
    if (!ml && !p) return `<p class="muted">No Market Lab radar or plan existed when this was saved.</p>`;
    const row = (k, v) => v ? `<dt>${k}</dt><dd>${num(100 * v.chance, 0)}% (usual ${num(100 * v.usual, 0)}%), up-share ${num(100 * v.up_share, 0)}%</dd>` : "";
    return `${ml ? `<dl class="kv"><dt>Radar day</dt><dd>${esc(ml.day)}</dd>${row("≥1% close-to-close", ml.cc_1)}${row("≥1% open-to-close", ml.oc_1)}${row("≥1% overnight gap", ml.gp_1)}</dl>` : ""}
      ${p ? Object.entries(p).map(([m, v]) => `<div class="small" style="margin-top:6px"><b>${esc(m)} plan</b> · lean ${esc(v.lean)} · ${v.tradeable ? "tradeable" : "no trade"}<br>${v.legs.filter((l) => l.price != null).map((l) => `${l.leg}${l.side === "short" && v.lean === "unclear" ? "↓" : ""} ${num(l.price, 2)}`).join(" · ")}</div>`).join("") : ""}
      ${(s.patterns || []).length ? `<div class="small muted" style="margin-top:6px">Candles then: ${s.patterns.map((x) => esc(`${x.label} (${x.direction})`)).join(", ")}</div>` : ""}`;
  }

  function exportPng(svg, name) {
    if (!svg) return;
    const c = svg.cloneNode(true), cs = getComputedStyle(document.documentElement), vb = svg.viewBox.baseVal;
    c.querySelectorAll("text").forEach((t, i) => { const src = svg.querySelectorAll("text")[i]; if (!t.getAttribute("style")) t.setAttribute("fill", getComputedStyle(src).fill); t.setAttribute("font-family", "-apple-system, Segoe UI, sans-serif"); });
    c.querySelectorAll(".ws-cap").forEach((r) => r.remove());
    c.setAttribute("xmlns", "http://www.w3.org/2000/svg"); c.setAttribute("width", vb.width); c.setAttribute("height", vb.height);
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect"); bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", cs.getPropertyValue("--surface").trim() || "#fff"); c.insertBefore(bg, c.firstChild);
    const txt = new XMLSerializer().serializeToString(c).replace(/var\(--surface\)/g, cs.getPropertyValue("--surface").trim() || "#fff");
    const img = new Image(), url = URL.createObjectURL(new Blob([txt], { type: "image/svg+xml" }));
    img.onload = () => { const cv = document.createElement("canvas"); cv.width = vb.width * 2; cv.height = vb.height * 2; const g = cv.getContext("2d"); g.scale(2, 2); g.drawImage(img, 0, 0);
      URL.revokeObjectURL(url); cv.toBlob((b) => { const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name; a.click(); }); };
    img.src = url;
  }

  MX.workspacePage = (el, id) => (id ? editor(el, id) : listPage(el));
})();
