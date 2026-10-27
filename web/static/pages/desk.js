/* Stock Desk: on-demand deep dive for chosen stocks. #/desk = watchlist, analyse, compare, notes search.
   #/desk/SYMBOL (its own browser tab) = the stock's desk: Overview (star map, verdicts, plan, reasons), Market Lab, Technical,
   Behaviour, Fundamentals, Ownership & F&O, Peers & sector, News & filings, Notes; notes panel always on the right.
   #/desk-compare/A,B = side by side. Data: /api/desk/*. */
(function () {
  const { api, esc, num, pct, inr, day, $, $$, note, dataTable, table, spinner, startJob, head, qs } = MX;
  const P = (x, d = 0) => (x == null ? "—" : `${num(100 * x, d)}%`);
  const S = (x, d = 1) => x == null ? "—" : `<span class="${x > 0 ? "up" : x < 0 ? "down" : "muted"}">${x > 0 ? "▲ +" : x < 0 ? "▼ " : "• "}${num(100 * x, d)}%</span>`;
  const ar = (x) => x === "up" ? `<span class="up">▲ up</span>` : x === "down" ? `<span class="down">▼ down</span>` : `<span class="muted">• ${esc(x || "—")}</span>`;
  const MOVES = [["cc", "Close-to-close"], ["oc", "Open-to-close"], ["gp", "Gap"]];
  const newTab = (s) => `#/desk/${encodeURIComponent(s)}`;

  /* ---------------- list page ---------------- */
  async function listPage(el) {
    el.innerHTML = head("Stock Desk", "Pick stocks, analyse them on demand (everything MarketX computes), and keep notes. Each stock opens in its own tab.") +
      `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><input class="in" id="dkadd" placeholder="Add a stock, e.g. TCS" style="width:200px"><button class="btn" id="dkaddb">Add</button>
        <span style="flex:1"></span><label class="small muted">as of <input class="in" type="date" id="dkasof"></label>
        <button class="btn primary" id="dkan">Analyse selected</button><button class="btn" id="dkcmp">Compare selected</button></div><div id="dkw" class="section"></div></div>
      <div class="card section"><div class="ct"><h2>Search notes</h2></div><div class="row"><input class="in" id="dkq" placeholder="word, tag or symbol" style="width:280px"><button class="btn" id="dkqb">Search</button></div><div id="dkqr" class="section"></div></div>`;
    const draw = async () => {
      const d = await api("desk/watchlist");
      const host = $("#dkw", el);
      if (!d.items.length) { host.innerHTML = `<p class="muted">Your desk is empty: add a stock above.</p>`; return; }
      host.innerHTML = table(d.items, [["symbol", "", (v) => `<input type="checkbox" class="dkpick" value="${esc(v)}">`], ["symbol", "Stock", (v, r) => `<a href="${newTab(v)}" target="_blank" rel="noopener"><b>${esc(v)}</b> ↗</a><div class="muted small">${esc(r.name || "")}</div>`],
        ["analysed", "Analysed", (v, r) => v ? `${esc(v.slice(0, 16))}<div class="muted small">as of ${esc(r.as_of)}</div>` : `<span class="muted">not yet</span>`], ["read", "Chart read", (v) => ar(v)],
        ["notes", "Notes", (v, r) => `${v ? "✓" : "—"} ${r.decisions ? `· ${r.decisions} decisions` : ""}`], ["symbol", "", (v) => `<button class="btn small dkrm" data-s="${esc(v)}">Remove</button>`]]);
      $$(".dkrm", host).forEach((b) => b.addEventListener("click", async () => { await MX.post("desk/watchlist", { remove: b.dataset.s }); draw(); }));
    };
    const picked = () => $$(".dkpick", el).filter((x) => x.checked).map((x) => x.value);
    const add = async () => { const s = $("#dkadd", el).value.trim().toUpperCase(); if (!s) return; await MX.post("desk/watchlist", { add: s }); $("#dkadd", el).value = ""; draw(); };
    $("#dkaddb", el).addEventListener("click", add); $("#dkadd", el).addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
    MX.suggest($("#dkadd", el), { kinds: "stocks", recent: "stock", onPick: () => add() });
    $("#dkan", el).addEventListener("click", async () => { const s = picked(); if (!s.length) return MX.toast("Tick at least one stock", true);
      try { await MX.post("desk/analyse", { symbols: s, as_of: $("#dkasof", el).value || undefined }, "Analysing: about 30–60 s per stock"); MX.refreshStatus(true); } catch (e) { /* toast shown */ } });
    $("#dkcmp", el).addEventListener("click", () => { const s = picked(); if (s.length < 2) return MX.toast("Tick 2–4 stocks", true); window.open(`#/desk-compare/${s.slice(0, 4).join(",")}`, "_blank", "noopener"); });
    const search = async () => { const d = await api(`desk/search?${qs({ q: $("#dkq", el).value })}`);
      $("#dkqr", el).innerHTML = d.items.length ? table(d.items, [["symbol", "Stock", (v) => `<a href="${newTab(v)}" target="_blank" rel="noopener">${esc(v)} ↗</a>`], ["snippet", "Note", (v) => `<span class="small">${esc(v)}</span>`],
        ["tags", "Tags", (v) => (v || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")], ["updated", "Updated"]]) : `<p class="muted">Nothing found.</p>`; };
    $("#dkqb", el).addEventListener("click", search); $("#dkq", el).addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
    MX.suggest($("#dkq", el), { kinds: "desk,tags", recent: "desk-notes", onPick: () => search() });
    await draw();
  }

  /* ---------------- notes panel ---------------- */
  function notesPanel(host, sym, snapRef) {
    host.innerHTML = `<div class="card dk-notes"><div class="ct"><h2>Notes</h2><span class="aside muted small" id="dkns"></span></div>
      <div class="dk-tb">${[["bold", "B"], ["italic", "I"], ["underline", "U"], ["h3", "H"], ["insertUnorderedList", "•"], ["insertOrderedList", "1."], ["check", "☐"], ["strikeThrough", "S"]]
        .map(([c, l]) => `<button class="btn small" data-c="${c}" title="${c}">${l}</button>`).join("")}</div>
      <div class="dk-ed" id="dked" contenteditable="true" spellcheck="true"></div>
      <input class="in" id="dktags" placeholder="tags, comma separated" style="width:100%;margin-top:6px">
      <div class="mllab" style="margin-top:10px">Decision log</div>
      <div class="row" style="gap:4px;flex-wrap:wrap">${["watching", "entered", "exited", "skipped", "note"].map((k) => `<button class="btn small dkdec" data-k="${k}">${k}</button>`).join("")}</div>
      <div class="row" style="gap:4px;margin-top:4px"><input class="in" id="dkdn" placeholder="why (optional)" style="flex:1"><input class="in" id="dkdh" type="number" min="1" max="250" placeholder="days" title="review after N sessions" style="width:64px"></div>
      <div id="dkdue"></div><div id="dkdl"></div>
      <details class="fold"><summary>Versions</summary><div id="dkver" class="small"></div></details></div>`;
    const ed = $("#dked", host), st = $("#dkns", host);
    let timer = null, lastSaved = null;
    const save = async () => { const html = ed.innerHTML; if (html === lastSaved) return; st.textContent = "saving …";
      try { await api("desk/notes", { symbol: sym, html, tags: $("#dktags", host).value.split(",") }); lastSaved = html; st.textContent = "saved"; } catch (e) { st.textContent = "not saved: " + e.message; } };
    const soon = () => { st.textContent = "…"; clearTimeout(timer); timer = setTimeout(save, 1200); };
    ed.addEventListener("input", soon); $("#dktags", host).addEventListener("change", () => { lastSaved = null; save(); });
    window.addEventListener("beforeunload", save);
    $$(".dk-tb button", host).forEach((b) => b.addEventListener("mousedown", (e) => { e.preventDefault(); const c = b.dataset.c;
      if (c === "h3") document.execCommand("formatBlock", false, "h3"); else if (c === "check") document.execCommand("insertHTML", false, "☐&nbsp;"); else document.execCommand(c);
      soon(); }));
    const drawLog = (n) => {
      $("#dkdue", host).innerHTML = (n.due || []).map((d) => `<div class="note warn small" style="margin-top:6px"><b>Review:</b> ${esc(d.kind)} on ${esc(d.at.slice(0, 10))} at ${inr(d.price)} →
          now ${S(d.outcome.ret)} (best ${S(d.outcome.best)}, worst ${S(d.outcome.worst)})${d.outcome.plan_first ? ` · plan: ${esc(d.outcome.plan_first)} first` : ""}
          <div class="row" style="gap:4px;margin-top:4px"><input class="in dkles" data-id="${esc(d.id)}" placeholder="lesson learned" style="flex:1"><button class="btn small dklesb" data-id="${esc(d.id)}">Save</button></div></div>`).join("");
      $$(".dklesb", host).forEach((b) => b.addEventListener("click", async () => { const v = $(`.dkles[data-id="${b.dataset.id}"]`, host).value; await api("desk/review", { symbol: sym, id: b.dataset.id, lesson: v }); load(); }));
      $("#dkdl", host).innerHTML = (n.decisions || []).slice().reverse().map((d) => `<div class="small dk-dec"><b>${esc(d.kind)}</b> · ${esc(d.at.slice(0, 16))}${d.price ? ` · ${inr(d.price)}` : ""}${d.horizon_days ? ` · review in ${d.horizon_days} sessions` : ""}
          ${d.note ? `<div>${esc(d.note)}</div>` : ""}<div class="muted">said: ML ${d.said && d.said.market_lab ? `${P(d.said.market_lab.chance)} / ${esc(d.said.market_lab.lean)}` : "—"} · TA ${esc((d.said && d.said.ta) || "—")}${d.outcome_now && d.outcome_now.sessions ? ` · since: ${S(d.outcome_now.ret)}` : ""}</div>
          ${d.review ? `<div>✓ lesson: ${esc(d.review.lesson)}</div>` : ""}</div>`).join("") || `<p class="muted small">No decisions yet.</p>`;
      $("#dkver", host).innerHTML = (n.versions || []).map((v, i) => `<div><button class="btn small dkv" data-i="${i}">${esc(v.at)}</button> ${v.chars} chars</div>`).join("") || "—";
      $$(".dkv", host).forEach((b) => b.addEventListener("click", async () => { const v = await api(`desk/notes/version?${qs({ symbol: sym, i: b.dataset.i })}`);
        ed.innerHTML = v.html; soon(); MX.toast("Older version restored into the editor (the current one is kept in versions)"); }));
    };
    const load = async () => { const n = await api(`desk/notes?${qs({ symbol: sym })}`); if (!lastSaved) { ed.innerHTML = n.html || ""; lastSaved = ed.innerHTML; }
      $("#dktags", host).value = (n.tags || []).join(", "); st.textContent = n.updated ? `saved ${n.updated.slice(0, 16)}` : ""; drawLog(n); };
    $$(".dkdec", host).forEach((b) => b.addEventListener("click", async () => {
      try { await api("desk/decision", { symbol: sym, kind: b.dataset.k, note: $("#dkdn", host).value, horizon_days: +$("#dkdh", host).value || null });
        $("#dkdn", host).value = ""; MX.toast(`Logged: ${b.dataset.k} (with what the Desk said now)`); load(); } catch (e) { MX.toast(e.message, true); } }));
    load();
    return { pin: (text) => { ed.focus(); const chip = `<span class="pin" data-pin="1">📌 ${esc(text)} · ${esc((snapRef() || {}).as_of || "")}</span>&nbsp;`;
      ed.insertAdjacentHTML("beforeend", `<p>${chip}</p>`); soon(); MX.toast("Pinned to notes"); } };
  }

  /* ---------------- the stock window ---------------- */
  const TABS = [["overview", "Overview"], ["ml", "Market Lab"], ["ta", "Technical"], ["beh", "Behaviour"], ["fund", "Fundamentals"], ["own", "Ownership & F&O"], ["peers", "Peers & sector"], ["news", "News & filings"]];
  async function stockPage(el, sym) {
    document.title = `${sym} · Stock Desk`;
    let tab = (() => { try { return sessionStorage.getItem("mx-dk-tab") || "overview"; } catch (e) { return "overview"; } })(), at = null, snap = null, notes = null, autoRun = false;
    el.innerHTML = `<div class="dk-wrap"><div class="dk-main"><div id="dkh"></div><div class="tabs" id="dkt">${TABS.map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div><div id="dkb"></div></div>
      <div class="dk-side" id="dkside"></div></div>`;
    notes = notesPanel($("#dkside", el), sym, () => snap);
    const pinBtn = (text) => `<button class="btn small dkpin" data-pin="${esc(text)}" title="Pin to notes">📌</button>`;
    const wirePins = (h) => $$(".dkpin", h).forEach((b) => b.addEventListener("click", () => notes.pin(b.dataset.pin)));
    const header = (d) => {
      const s = d.snap, p = s ? s.price : null;
      $("#dkh", el).innerHTML = `<div class="row" style="justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap">
        <div><div class="muted small"><a href="#/desk">Stock Desk</a> · ${esc(s ? s.sector : "")}</div><h1 style="margin:2px 0">${esc(sym)} <span class="muted" style="font-size:16px">${esc(s ? s.name : "")}</span></h1>
          ${p ? `<div><b style="font-size:20px">${inr(p.close)}</b> ${S(p.change, 2)} <span class="muted small">· 1m ${S(p.r1m)} · 3m ${S(p.r3m)} · 1y ${S(p.r1y)} · 52w ${inr(p.lo52)}–${inr(p.hi52)}</span></div>` : ""}</div>
        <div class="row" style="gap:6px;flex-wrap:wrap"><select class="in" id="dksnap" title="saved snapshots">${(d.snapshots || []).map((x) => `<option value="${esc(x)}" ${x === at ? "selected" : ""}>${esc(x.replace(".json", "").replace("_", " · "))}</option>`).join("") || "<option>no snapshot</option>"}</select>
          ${(d.snapshots || []).length > 1 ? `<button class="btn" id="dkdiff" title="this saved analysis next to the latest one">Compare with latest</button>` : ""}
          <label class="small muted">as of <input class="in" type="date" id="dkasof"></label><button class="btn primary" id="dkan">${s ? "Refresh" : "Analyse"}</button>
          <button class="btn" id="dkbr">AI brief</button><input class="in" id="dkcmpq" placeholder="Compare with…" style="width:150px"></div></div>
        ${s ? `<div class="muted small">as of ${esc(s.as_of)} · built ${esc(s.built)} · ${esc(s.took_s)} s</div>` : ""}<div id="dkbrief"></div>`;
      $("#dksnap", el).addEventListener("change", (e) => { at = e.target.value; load(); });
      $("#dkdiff", el)?.addEventListener("click", async () => {
        const box = $("#dkbrief", el), pick = at || ((d.snapshots || [])[1]);
        if (!pick) return MX.toast("Only one saved analysis so far", true);
        box.innerHTML = spinner("Comparing …");
        let r; try { r = await api(`desk/diff?${qs({ symbol: sym, at: pick })}`); } catch (e) { box.innerHTML = note(esc(e.message), "warn"); return; }
        const fmtv = (k, v) => v == null ? "—" : k === "pct" ? P(v) : k === "dir" ? ar(v) : k === "num" ? num(v, 2) : esc(v);
        const list = (xs, cls) => xs.length ? `<ul class="small">${xs.map((x) => `<li class="${cls}">${esc(x)}</li>`).join("")}</ul>` : `<span class="muted small">none</span>`;
        box.innerHTML = `<div class="card section"><div class="ct"><h2>Saved ${esc(String(pick).replace(".json", "").replace("_", " · "))} vs latest</h2><span class="aside small">price since: ${S(r.moved, 2)} <button class="btn small" id="dkdiffx">Close</button></span></div>
          <div class="muted small">then: as of ${esc(r.then.as_of)} (built ${esc(r.then.built)}) · now: as of ${esc(r.now.as_of)} (built ${esc(r.now.built)})</div>
          ${table(r.rows, [["label", ""], ["then", "Then", (v, x) => fmtv(x.kind, v)], ["now", "Now", (v, x) => fmtv(x.kind, v)], ["changed", "", (v) => v ? `<span class="tag warn">changed</span>` : ""]])}
          <div class="grid g2 section"><div><div class="mllab">For: new</div>${list(r.reasons.pro_new, "up")}<div class="mllab">For: gone</div>${list(r.reasons.pro_gone, "muted")}</div>
            <div><div class="mllab">Against: new</div>${list(r.reasons.con_new, "down")}<div class="mllab">Against: gone</div>${list(r.reasons.con_gone, "muted")}</div></div></div>`;
        $("#dkdiffx", el).addEventListener("click", () => { box.innerHTML = ""; });
      });
      $("#dkan", el).addEventListener("click", () => analyse(s ? s.built : null, $("#dkasof", el).value || undefined));
      $("#dkbr", el).addEventListener("click", async () => { const box = $("#dkbrief", el); box.innerHTML = spinner("Writing the brief from the computed facts …");
        try { const r = await MX.post("desk/brief", { symbol: sym }); const out = await MX.pollTask(r.task, () => {});
          box.innerHTML = `<div class="card section"><div class="ct"><h2>Brief</h2><span class="aside muted small">${out.writer === "model" ? "local model, numbers checked" : "facts-only summary"} ${pinBtn("Brief: " + out.text.slice(0, 300))}</span></div><div class="small" style="white-space:pre-line">${esc(out.text)}</div></div>`; wirePins(box); }
        catch (e) { box.innerHTML = note(esc(e.message), "warn"); } });
      const cmp = (v) => { const o = String(v || "").toUpperCase().split(/[ ,]+/).filter(Boolean); if (o.length) window.open(`#/desk-compare/${[sym, ...o].slice(0, 4).join(",")}`, "_blank", "noopener"); };
      MX.suggest($("#dkcmpq", el), { kinds: "stocks", recent: "stock", onPick: (r) => { cmp(r.value); $("#dkcmpq", el).value = ""; } });
      $("#dkcmpq", el).addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.value.trim()) { cmp(e.target.value); e.target.value = ""; } });
    };
    // Analyse (button, or by itself the first time a stock without an analysis is opened): job + wait for the new snapshot
    const analyse = async (before, asOf, quiet) => {
      try { await MX.post("desk/analyse", { symbols: [sym], as_of: asOf }, quiet ? undefined : "Analysing …"); }
      catch (e) { const q = $("#dkqs", el); if (q) q.innerHTML = note(`Couldn't start the analysis: ${esc(e.message)}. Try <b>Analyse</b> in a minute.`, "warn"); return; }
      MX.refreshStatus(true); let n = 0;
      const poll = setInterval(async () => { n++; if (!el.isConnected) return clearInterval(poll);
        const r = await api(`desk/snapshot?${qs({ symbol: sym })}`).catch(() => null);
        if (r && r.ready && r.snap.built !== before) { clearInterval(poll); at = null; load(); MX.toast("Analysis ready"); }
        if (n > 60) { clearInterval(poll); const q = $("#dkqs", el); if (q) q.innerHTML = note("The analysis is taking long: check the job in the bar at the top.", "warn"); } }, 5000);
    };
    const quickView = async (b) => {
      b.innerHTML = `<div id="dkqs">${note("No full analysis for this stock yet: <b>analysing now</b> (8–30 s; peers, behaviour, ownership, F&amp;O, news). Meanwhile, what MarketX already saved:", "info")}</div><div id="dkqv">${spinner("Loading …")}</div>`;
      if (!autoRun && !at) { autoRun = true; analyse(null, undefined, true); }
      let q; try { q = await api(`desk/quick?${qs({ symbol: sym })}`); } catch (e) { $("#dkqv", el).innerHTML = note(esc(e.message), "warn"); return; }
      if (!el.isConnected || snap) return;
      const F = (v) => v ? day(v) : "—", LEAN = (l) => ar(l), legs = (pl) => { if (!pl || !pl.legs.length) return `<span class="muted">—</span>`;
        const sd = (side) => { const g = (k) => pl.legs.find((x) => x.leg === k && x.side === side); const e = g("E1"); return e ? `<div class="small"><b class="${side === "long" ? "up" : "down"}">${side === "long" ? "▲ Long" : "▼ Short"}</b> E1 ${inr(e.price)} · SL ${inr(g("SL")?.price)} · T1 ${inr(g("T1")?.price)} · T2 ${inr(g("T2")?.price)}</div>` : ""; };
        return `<div class="small">lean ${LEAN(pl.lean)}</div>${sd("long")}${sd("short")}`; };
      const why = (o) => (o && o.why || []).slice(0, 6).map((w) => `<li>${esc(w)}</li>`).join("");
      const ml = q.market_lab, pl = q.plan, t = q.tally, p = q.price;
      $("#dkqv", el).innerHTML = `
        ${p ? `<div class="card section"><b style="font-size:20px">${inr(p.close)}</b> ${S(p.change, 2)} <span class="muted small">· ${F(p.date)} · 1m ${S(p.r1m)} · 3m ${S(p.r3m)} · 1y ${S(p.r1y)}</span></div>` : ""}
        <div class="grid g2 section">
          <div class="card"><div class="ct"><h2>Market Lab</h2><span class="aside muted small">${ml ? `radar of ${F(ml.day)}` : ""}</span></div>
            ${ml && ml.rows.length ? table(ml.rows, [["move", "Move", (v) => (MOVES.find((m) => m[0] === v) || [])[1] || v], ["chance", "Chance ≥1%", (v) => P(v)], ["usual", "Usual", (v) => P(v)], ["lean", "Lean", (v) => LEAN(v)]]) : `<p class="muted">Not on the latest radar.</p>`}</div>
          <div class="card"><div class="ct"><h2>Last graded result</h2><span class="aside muted small">${t ? `projected ${F(t.day)} → ${F(t.for_day)}` : ""}</span></div>
            ${t ? `<p>Said ${t.chance != null ? `<b>${P(t.chance)}</b> (usual ${P(t.usual)})` : "—"} · lean ${LEAN(t.lean)}${t.pick ? ` · <span class="tag">pick</span>` : ""}</p>
              <p>Happened ${S(t.ret, 2)} · ≥1%: ${t.hit == null ? "—" : t.hit ? `<span class="up">✓</span>` : `<span class="down">✗</span>`} · plan ${t.status === "no data" ? `<span class="muted">not graded yet</span>` : esc(t.status || "—")}${t.r != null ? ` · <b>${num(t.r, 2)} R</b>` : ""}</p>
              <p class="small"><a href="#/tally/${encodeURIComponent(t.day)}" target="_blank" rel="noopener">Open that day in Tally ↗</a></p>` : `<p class="muted">Not graded yet.</p>`}</div>
          <div class="card"><div class="ct"><h2>Technical ${pl && pl.ta ? `· ${LEAN(pl.ta.dir)} <span class="muted small">score ${pl.ta.score ?? "—"}</span>` : ""}</h2></div>
            ${pl && pl.ta ? `<ul class="small">${why(pl.ta)}</ul>${(pl.patterns || []).map((x) => `<div class="muted small">candle: ${esc(x.label || x.pattern || "")} ${esc(x.direction || "")}</div>`).join("")}` : `<p class="muted">No technical read saved.</p>`}</div>
          <div class="card"><div class="ct"><h2>Plan ${pl ? `<span class="muted small">for the session after ${F(pl.day)}</span>` : ""}</h2></div>
            ${pl ? `<div class="mllab">Intraday</div>${legs(pl.intraday)}<div class="mllab" style="margin-top:8px">Swing</div>${legs(pl.swing)}
              <div class="mllab" style="margin-top:8px">Fundamentals · ${LEAN(pl.fund && pl.fund.dir)}</div><ul class="small">${why(pl.fund)}</ul>` : `<p class="muted">No plan saved.</p>`}</div>
        </div>`;
    };
    const body = async () => {
      const b = $("#dkb", el);
      if (!snap) return quickView(b);
      try { await (RENDER[tab] || RENDER.overview)(b, snap, sym, { pinBtn, wirePins, at }); } catch (e) { b.innerHTML = note(esc(e.message), "warn"); console.error(e); }
    };
    const load = async () => { const d = await api(`desk/snapshot?${qs({ symbol: sym, at })}`); snap = d.ready ? d.snap : null; header(d); await body(); };
    $("#dkt", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; tab = x.dataset.t; try { sessionStorage.setItem("mx-dk-tab", tab); } catch (er) { /* ignore */ }
      $$("#dkt button", el).forEach((y) => y.classList.toggle("on", y === x)); body(); });
    await load();
  }

  const verdictCard = (label, value, sub, pinText, h) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="muted small">${sub || ""}</div>${pinText ? h.pinBtn(pinText) : ""}</div>`;
  async function mlMap(host, sym, m, s, at, onNode) {
    const d = await api(`desk/marketlab?${qs({ symbol: sym, move: m, size: s, at })}`);
    if (!d.ready) { host.innerHTML = note(esc(d.why), "warn"); return null; }
    MX.mlMap(host, d, onNode);
    return d;
  }

  const RENDER = {
    async overview(b, s, sym, h) {
      const ml = s.market_lab || {}, v = (ml.verdicts || []).find((x) => x.move === "cc" && x.size === 1) || {};
      const ev = (s.ta || {}).evidence || {}, plans = (s.ta || {}).plans || {}, fl = plans.fund || {}, own = s.ownership || [];
      const fii = own.length >= 2 && own[own.length - 1].fii_pct != null && own[own.length - 2].fii_pct != null ? own[own.length - 1].fii_pct - own[own.length - 2].fii_pct : null;
      const planLine = (m) => { const p = plans[m]; if (!p || !p.legs) return `<span class="muted">—</span>`; const lg = (k, sd) => (p.legs.find((x) => x.leg === k && x.side === sd) || {}).price;
        const sides = p.lean === "up" ? ["long"] : p.lean === "down" ? ["short"] : ["long", "short"];
        return sides.map((sd) => `<div class="${sd === "long" ? "up" : "down"}"><b>${sd === "long" ? "▲ BUY above" : "▼ SHORT below"} ${inr(lg("E1", sd))}</b> <span class="muted small">stop ${inr(lg("SL", sd))} · T1 ${inr(lg("T1", sd))} · T2 ${inr(lg("T2", sd))}</span></div>`).join("") + (p.tradeable ? "" : `<div class="muted small">no trade: ${esc((p.flags || [])[0] || "")}</div>`); };
      const R = s.reasons || {};
      const lv = (s.ta || {}).levels || {};
      b.innerHTML = `<div class="grid g4 section">
          ${verdictCard("Market Lab · ≥1% next session", ml.ready ? `${P(v.chance)} <span class="muted small">usual ${P(v.usual)}</span>` : "—", ml.ready ? `lean ${ar(v.lean)} · confidence ${esc(v.confidence)}` : esc(ml.why || ""), ml.ready ? `Market Lab: ${P(v.chance)} chance of ≥1% (usual ${P(v.usual)}), lean ${v.lean}` : null, h)}
          ${verdictCard("Chart read", ar(ev.dir), ev.checks ? `${ev.up} up · ${ev.down} down · net ${ev.net}` : "", ev.dir ? `Chart read ${ev.dir} (net ${ev.net})` : null, h)}
          ${verdictCard("Fundamentals", ar(fl.dir), esc((fl.why || []).slice(-1)[0] || ""), fl.dir ? `Fundamentals ${fl.dir}: ${(fl.why || []).join("; ")}` : null, h)}
          ${verdictCard("FII holding, last quarter", fii == null ? "—" : S(fii / 100, 2), own.length ? `promoter ${num(own[own.length - 1].promoter_pct, 1)}%` : "no shareholding data", fii == null ? null : `FII holding ${fii >= 0 ? "+" : ""}${num(fii, 2)} pts last quarter`, h)}</div>
        <div class="grid g2 section"><div class="card"><div class="ct"><h2>Influence map</h2><span class="aside muted small">close-to-close ≥1% · click a node</span></div><div id="dkmap"></div><div id="dknode" class="small"></div></div>
          <div><div class="card"><div class="ct"><h2>Plan</h2></div><div class="mllab">Intraday</div>${planLine("intraday")}<div class="mllab" style="margin-top:6px">Swing</div>${planLine("swing")}
              <div class="muted small" style="margin-top:6px">Levels: pivot ${inr(lv.P)} · R1 ${inr(lv.R1)} · S1 ${inr(lv.S1)} · 20d ${inr(lv.lo20)}–${inr(lv.hi20)} · ATR ${inr(lv.atr)}</div>
              <a class="btn small" href="#/ta/${encodeURIComponent(sym)}" target="_blank" rel="noopener" style="margin-top:6px">TA workspace ↗</a></div>
            <div class="card section"><div class="ct"><h2>Upcoming</h2></div>${(s.upcoming || []).map((x) => `<div class="small"><b>${esc(day(x.date))}</b> · ${esc(x.what)}</div>`).join("") || `<p class="muted small">Nothing scheduled in the data.</p>`}</div></div></div>
        <div class="grid g2 section"><div class="card"><div class="ct"><h2 class="up">For</h2></div>${(R.pro || []).map((x) => `<div class="small dk-r">▲ ${esc(x.text)} <span class="muted">[${esc(x.src)}]</span> ${h.pinBtn("For: " + x.text)}</div>`).join("") || `<p class="muted small">Nothing strong.</p>`}</div>
          <div class="card"><div class="ct"><h2 class="down">Against</h2></div>${(R.con || []).map((x) => `<div class="small dk-r">▼ ${esc(x.text)} <span class="muted">[${esc(x.src)}]</span> ${h.pinBtn("Against: " + x.text)}</div>`).join("") || `<p class="muted small">Nothing strong.</p>`}</div></div>`;
      h.wirePins(b);
      if (ml.ready) await mlMap($("#dkmap", b), sym, "cc", 1, h.at, (x) => { $("#dknode", b).innerHTML = MX.mlNodePanel(x, {}); });
    },
    async ml(b, s, sym, h) {
      const st = RENDER._ml || (RENDER._ml = { move: "cc", size: "1" }), ml = s.market_lab || {};
      if (!ml.ready) { b.innerHTML = note(esc(ml.why || "not available"), "warn"); return; }
      const seg = (k, opts) => `<div class="seg" data-k="${k}">${opts.map(([v, l]) => `<button data-v="${v}" class="${String(st[k]) === String(v) ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`;
      b.innerHTML = `<div class="row section" style="gap:10px">${seg("move", MOVES)}${seg("size", [["0.5", "≥0.5%"], ["1", "≥1%"], ["2", "≥2%"], ["3", "≥3%"]])}</div>
        <div class="split section" style="grid-template-columns:minmax(0,1fr) 320px"><div class="card"><div id="dkmap"></div></div><div class="card" id="dknode"><p class="muted small">Click a node.</p></div></div>
        <div class="grid g2 section"><div class="card"><div class="ct"><h2>Every move and size</h2></div><div id="dkall"></div></div><div class="card"><div class="ct"><h2>Track record on this stock</h2><span class="aside muted small">graded calls (Review)</span></div><div id="dktr"></div></div></div>
        <div class="card section"><div class="ct"><h2>Closest past cases</h2></div><div id="dkcs"></div></div>`;
      $$(".seg", b).forEach((g) => g.addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) { st[g.dataset.k] = x.dataset.v; RENDER.ml(b, s, sym, h); } }));
      $("#dkall", b).innerHTML = table(ml.verdicts, [["move", "Move", (v) => esc((MOVES.find((x) => x[0] === v) || [])[1])], ["size", "Size", (v) => `≥${v}%`], ["chance", "Chance", (v) => P(v)], ["usual", "Usual", (v) => P(v)],
        ["lift", "Edge", (v) => `${v >= 0 ? "+" : ""}${num(100 * v, 1)}`], ["lean", "Lean", (v) => ar(v === "unclear" ? null : v)], ["confidence", "Confidence", "tag"]]);
      const tr = s.track || {};
      $("#dktr", b).innerHTML = tr.rows && tr.rows.length ? table(tr.rows, [["move", "Move"], ["size", "Size", (v) => `≥${v}%`], ["n", "Days", "int"], ["said", "Said", (v) => P(v)], ["hit", "Happened", (v) => P(v)], ["usual", "Usual", (v) => P(v)], ["lean_right", "Lean right", (v, r) => r.leans ? `${P(v)} of ${r.leans}` : "—"]])
        : `<p class="muted small">No graded days for this stock yet (Market Lab → Review → Replay).</p>`;
      const d = await mlMap($("#dkmap", b), sym, st.move, +st.size, h.at, (x) => { $("#dknode", b).innerHTML = MX.mlNodePanel(x, {}); });
      if (d) $("#dkcs", b).appendChild(dataTable(d.cases, [["rank", "#", "int"], ["symbol", "Stock"], ["date", "Date", "date"], ["cc", "Close-to-close %", "snum2"], ["oc", "Open-to-close %", "snum2"], ["gp", "Gap %", "snum2"], ["distance", "Distance", "num2"]], { tools: false }));
    },
    async ta(b, s, sym) {
      b.innerHTML = `<p class="muted small">Live technical analysis (today's data). <a href="#/ta/${encodeURIComponent(sym)}" target="_blank" rel="noopener">Open full screen ↗</a></p><div id="dkta"></div>`;
      const def = MX.pageDef("ta"); if (def) await def.render($("#dkta", b), sym);
    },
    beh(b, s, sym, h) {
      const i = s.intraday || {}, e = s.events || {}, n = e.normal || {}, se = s.seasonality || {};
      const evRow = (k, l) => { const x = e[k] || {}; return dict(l, x); };
      function dict(l, x) { return { what: l, n: x.n || 0, avg_abs1: x.avg_abs1, big1: x.big1, up1: x.up1, avg5: x.avg5 }; }
      b.innerHTML = `<div class="card section"><div class="ct"><h2>Intraday habits (last ${i.sessions || "—"} sessions, ±1% from yesterday's close)</h2>${i.ready ? h.pinBtn(`Intraday: crosses ±1% in ${P(i.crossed_pct)} of sessions, first cross ~${i.median_first}, holds to close ${P(i.held_pct)}`) : ""}</div>
          ${i.ready ? `<div class="grid g4">${[["Sessions reaching ±1%", P(i.crossed_pct)], ["Typical first cross", esc(i.median_first || "—")], ["Held to the close", P(i.held_pct)], ["Choppy (3+ crossings)", P(i.choppy_pct)],
            ["Minutes beyond ±1% (median)", num(i.median_minutes, 0)], ["Opening 30-min range (median)", `${num(i.or_median, 2)}%`], ["Day's high or low set in first 30 min", P(i.extreme_in_or_pct)], ["Avg crossings when crossed", num(i.avg_cross, 1)]]
            .map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value" style="font-size:18px">${v}</div></div>`).join("")}</div>
            <details class="fold"><summary>Last 20 sessions</summary><div id="dkil"></div></details>` : note(esc(i.why || "no 5-minute data"), "warn")}</div>
        <div class="card section"><div class="ct"><h2>How it reacts to events (next session)</h2></div>${table([evRow("results", "Results"), evRow("material", "Material filings"), evRow("busy_news", "Busy news days (3+ headlines)"), dict("Any day (3 years)", n)],
          [["what", "Event"], ["n", "Times", "int"], ["avg_abs1", "Avg move", (v) => P(v, 1)], ["big1", "Moved ≥1%", (v) => P(v)], ["up1", "Up", (v) => P(v)], ["avg5", "Avg 5 days", (v) => S(v)]])}
          ${(e.results || {}).last ? `<details class="fold"><summary>Last results days</summary>${table(e.results.last, [["day", "Day", "date"], ["r1", "Next day", (v) => S(v)], ["r5", "5 days", (v) => S(v)]])}</details>` : ""}</div>
        <div class="grid g2 section"><div class="card"><div class="ct"><h2>Day of week (3 years)</h2></div>${table(se.weekday || [], [["day", "Day"], ["mean", "Avg", (v) => S(v, 2)], ["big", "≥1% days", (v) => P(v)], ["up", "Up", (v) => P(v)], ["n", "n", "int"]])}</div>
          <div class="card"><div class="ct"><h2>Month (5 years)</h2></div>${table(se.month || [], [["month", "Month"], ["mean", "Avg", (v) => S(v)], ["up", "Up", (v) => P(v)], ["n", "n", "int"]])}</div></div>
        <p class="muted small">Descriptive history, not tested predictions.</p>`;
      if (i.ready) $("#dkil", b).innerHTML = table(i.last.slice().reverse(), [["day", "Day", "date"], ["n_cross", "Crossings", "int"], ["first", "First"], ["minutes", "Minutes", "int"], ["held", "Held", (v) => v ? "yes" : "no"], ["best", "Best %", "snum2"], ["worst", "Worst %", "snum2"], ["close", "Close %", "snum2"], ["or_pct", "OR %", "num2"]]);
      h.wirePins(b);
    },
    fund(b, s, sym, h) {
      const f = s.fundamentals || {}, v = f.val || {}, kv = (k, x) => `<dt>${k}</dt><dd>${x ?? "—"}</dd>`, cr = (x) => x == null ? "—" : `₹${num(x, 0)} cr`;
      b.innerHTML = `<div class="grid g2 section"><div class="card"><div class="ct"><h2>Valuation (as of ${esc(s.as_of)})</h2></div><dl class="kv">${kv("Market cap", cr(v.market_cap_cr))}${kv("P/E (12 m)", v.pe == null ? "—" : num(v.pe, 1))}${kv("P/B", v.pb == null ? "—" : num(v.pb, 2))}
          ${kv("ROE", v.roe_pct == null ? "—" : `${num(v.roe_pct, 1)}%`)}${kv("Debt / equity", v.debt_to_equity == null ? "—" : num(v.debt_to_equity, 2))}${kv("Net debt", cr(v.net_debt_cr))}${kv("Free cash flow", cr(v.fcf_cr))}${kv("Balance sheet", esc(v.balance_sheet_date || v.note || "—"))}</dl></div>
          <div class="card"><div class="ct"><h2>Last quarters</h2></div>${(f.quarters || []).length ? table(f.quarters.slice().reverse(), [["period_end", "Quarter", "date"], ["revenue_cr", "Revenue ₹ cr", "int"], ["profit_cr", "Profit ₹ cr", "int"], ["margin_pct", "Margin %", "num1"],
            ["revenue_yoy", "Revenue y/y", (x) => S(x)], ["profit_yoy", "Profit y/y", (x) => S(x)], ["filed_at", "Filed", (x) => esc(String(x || "").slice(0, 10))]]) : `<p class="muted">No quarterly results downloaded (Data health → Quarterly results).</p>`}</div></div>
        <p class="muted small">From NSE filings, as filed; blank = not filed or not downloaded. Nothing is estimated.</p>`;
    },
    own(b, s) {
      const o = s.ownership || [], f = s.fo || {};
      b.innerHTML = `<div class="card section"><div class="ct"><h2>Shareholding</h2></div>${o.length ? table(o.slice().reverse(), [["period_end", "Quarter", "date"], ["promoter_pct", "Promoter %", "num2"], ["fii_pct", "FII %", "num2"], ["dii_pct", "DII %", "num2"], ["mf_pct", "MF %", "num2"], ["retail_pct", "Retail %", "num2"], ["holders", "Holders", "int"]]) : `<p class="muted">No shareholding data for this stock.</p>`}</div>
        <div class="card section"><div class="ct"><h2>F&O</h2></div>${f.ready ? `<p>Futures OI ${num(f.last.fut_oi, 0)} (${f.last.oi_chg >= 0 ? "+" : ""}${num(f.last.oi_chg, 0)}) · PCR ${num(f.last.pcr, 2)} · basis ${num(f.last.basis_pct, 2)}% · expiry ${esc(day(f.last.expiry))}</p>
          ${table(f.rows.slice().reverse(), [["date", "Day", "date"], ["fut_oi", "Fut OI", "int"], ["fut_oi_chg", "OI change", "int"], ["pcr", "PCR", "num2"], ["basis_pct", "Basis %", "num2"]])}` : `<p class="muted">${esc(f.why || "No F&O data.")}</p>`}</div>`;
    },
    peers(b, s, sym) {
      const p = s.peers || {}, r = s.relative;
      b.innerHTML = `<div class="card section"><div class="ct"><h2>Peers · ${esc(p.sector || "")}</h2><span class="aside muted small">${esc(p.by || "")}</span></div>
          ${(p.rows || []).length ? table(p.rows, [["symbol", "Stock", (v, x) => x.me ? `<b>${esc(v)}</b>` : `<a href="${newTab(v)}" target="_blank" rel="noopener">${esc(v)} ↗</a>`], ["close", "Price", "inr"], ["r1m", "1m", (v) => S(v)], ["r3m", "3m", (v) => S(v)], ["r1y", "1y", (v) => S(v)],
            ["chance", "ML chance ≥1%", (v, x) => v == null ? "—" : `${P(v)} <span class="muted small">usual ${P(x.usual)}</span>`], ["ta", "Chart read", (v) => ar(v)], ["pe", "P/E", "num1"], ["pb", "P/B", "num2"], ["roe", "ROE %", "num1"], ["turnover_cr", "Traded ₹ cr/day", "num1"]]) : `<p class="muted">No peers found.</p>`}</div>
        <div class="card section"><div class="ct"><h2>Relative strength, 1 year (start = 100)</h2></div><div id="dkrel"></div></div>`;
      if (r && r.t) requestAnimationFrame(() => MXCharts.lineChart($("#dkrel", b), { x: r.t, height: 300, series: [{ name: sym, y: r.stock, color: "#4f46e5", width: 2 }, ...(r.sector ? [{ name: r.sector_label || "sector", y: r.sector, color: "#f59e0b" }] : []), ...(r.nifty ? [{ name: "Nifty", y: r.nifty, color: "#94a3b8", dash: "4 3" }] : [])] }));
    },
    news(b, s) {
      const n = s.news || {}, sm = n.summary || {};
      b.innerHTML = `<p class="muted small">Last 30 days to ${esc(s.as_of)} · ${sm.news || 0} headlines (${Object.entries(sm.tone || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "—"}) · ${sm.filings || 0} filings (${Object.entries(sm.categories || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "—"})</p>
        <div class="grid g2"><div class="card"><div class="ct"><h2>Headlines</h2></div>${(n.news || []).map((x) => `<div class="small dk-r"><span class="${x.tone === "positive" ? "up" : x.tone === "negative" ? "down" : "muted"}">${x.tone === "positive" ? "▲" : x.tone === "negative" ? "▼" : "•"}</span> ${MX.safeUrl(x.link) ? `<a href="${esc(x.link)}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a>` : esc(x.title)} <span class="muted">${esc(x.source || "")} · ${esc(String(x.published).slice(0, 16))}</span></div>`).join("") || `<p class="muted">None.</p>`}</div>
          <div class="card"><div class="ct"><h2>Filings</h2></div>${(n.filings || []).map((x) => `<div class="small dk-r"><span class="tag">${esc(x.category)}</span> ${MX.safeUrl(x.link) ? `<a href="${esc(x.link)}" target="_blank" rel="noopener noreferrer">${esc(x.subject)}</a>` : esc(x.subject)} <span class="muted">${esc(String(x.published).slice(0, 16))}</span></div>`).join("") || `<p class="muted">None besides routine compliance.</p>`}</div></div>`;
    },
  };

  /* ---------------- compare ---------------- */
  async function comparePage(el, arg) {
    const syms = String(arg || "").toUpperCase();
    document.title = `Compare ${syms} · Stock Desk`;
    const d = await api(`desk/compare?${qs({ symbols: syms })}`);
    el.innerHTML = head(`Compare: ${esc(syms.replace(/,/g, " · "))}`, `From each stock's latest Stock Desk analysis.${d.missing.length ? ` Not analysed yet: ${esc(d.missing.join(", "))}.` : ""}`) +
      `<div class="card">${table(d.rows, [["symbol", "Stock", (v) => `<a href="${newTab(v)}" target="_blank" rel="noopener"><b>${esc(v)}</b> ↗</a>`], ["as_of", "As of", "date"], ["close", "Price", "inr"], ["r1m", "1m", (v) => S(v)], ["r3m", "3m", (v) => S(v)], ["r1y", "1y", (v) => S(v)],
        ["chance", "ML ≥1%", (v, r) => `${P(v)} <span class="muted small">usual ${P(r.usual)}</span>`], ["lean", "Lean", (v) => ar(v === "unclear" ? null : v)], ["confidence", "Confidence", "tag"], ["ta", "Chart read", (v, r) => `${ar(v)} <span class="muted small">${r.ta_net ?? ""}</span>`],
        ["rr", "Swing R:R", "num1"], ["plan_ok", "Plan", (v) => v ? "✓" : "no trade"], ["pe", "P/E", "num1"], ["pb", "P/B", "num2"], ["roe", "ROE %", "num1"], ["pro", "For / against", (v, r) => `<span class="up">${v}</span> / <span class="down">${r.con}</span>`]])}</div>
      <div class="card section"><div class="ct"><h2>1 year, start = 100</h2></div><div id="dkcc"></div></div>`;
    const ks = Object.keys(d.series);
    if (ks.length) { const t = d.series[ks[0]].t, cols = ["#4f46e5", "#f59e0b", "#10b981", "#ef4444"];
      requestAnimationFrame(() => MXCharts.lineChart($("#dkcc", el), { x: t, height: 320, series: ks.map((k, i) => { const m = new Map(d.series[k].t.map((x, j) => [x, d.series[k].y[j]])); return { name: k, y: t.map((x) => m.get(x) ?? null), color: cols[i] }; }) })); }
  }

  MX.page("desk", { title: "Stock Desk", async render(el, arg) { return arg ? stockPage(el, String(arg).toUpperCase()) : listPage(el); } });
  MX.page("desk-compare", { title: "Compare", async render(el, arg) { return comparePage(el, arg); } });
})();
