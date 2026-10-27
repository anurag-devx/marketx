/* Live terminal (#/live): Angel live feed in market hours, or a replay of a saved session.
   Row 1: live 5-min chart (search any stock or index; VWAP, opening range, plan legs, ±1% bands; full-session axis) + alerts.
   Row 2: Watch (Stock Desk + Market Lab picks + paper takes + added) | By sector (all 500 stocks) | Indices.
   Row 3: Market Lab live | Paper takes. #/live-focus/SYMBOL = chart + alerts only, full screen (same feed).
   Data: /api/live/*. Read only: no orders. */
(function () {
  const { api, esc, num, $, $$, table, note } = MX;
  const COL = { E1: "up", SL: "down", T1: "blue", T2: "blue" };
  const C = (x, d = 2) => MX.chg(x, d);
  const P = (x) => x == null ? "—" : num(x, x >= 1000 ? 1 : 2);
  const AR = (d) => d === "up" ? `<span class="up">▲</span>` : d === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
  const LBL = (d) => MX.lean(d);
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
    const why = (o) => (o && o.why || []).slice(0, 4).map((w) => `<li>${MX.arr(w)}</li>`).join("");
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

  function callLine(c) {
    if (!c || !c.has_plan) return `<span class="muted">No plan saved for this stock last night.</span>`;
    const pk = (c.picks || [])[0], p = c.intraday, g = (side, k) => p && p.legs.find((x) => x.leg === k && x.side === side);
    const side = (sd, lab, cls) => { const e = g(sd, "E1"); return e ? `<b class="${cls}">${lab}</b> E1 ${P(e.price)} · SL ${P(g(sd, "SL")?.price)} · T1 ${P(g(sd, "T1")?.price)}` : ""; };
    return [pk ? `ML ${num(100 * pk.chance, 0)}%/${num(100 * pk.usual, 0)}% ${AR(pk.lean)}` : `<span class="muted">not a pick</span>`,
      c.ta ? `TA ${AR(c.ta.dir)}${c.ta.score != null ? ` ${c.ta.score > 0 ? "+" : ""}${c.ta.score}` : ""}` : "", p ? `plan ${LBL(p.lean)}` : "",
      side("long", "▲ Long", "up"), side("short", "▼ Short", "down")].filter(Boolean).join(" · ");
  }
  const slotLabel = (i) => { const m = 9 * 60 + 15 + 5 * i; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };

  async function page(el, arg, focus) {
    const ctl = focus ? `<a class="btn" href="#/live" title="back to the full terminal">← Terminal</a>` : `
        <button class="btn primary" id="lvgo">Start live</button><span class="lv-fill small" id="lvfill"></span>
        <span class="lv-rep"><input class="in" type="date" id="lvday" title="session to replay (blank = last saved)"><select class="in" id="lvspd" title="replay speed">
          <option value="1">1 bar/s</option><option value="4" selected>4 bars/s</option><option value="20">20 bars/s</option></select><button class="btn" id="lvrep">Replay</button></span>
        <a class="btn" href="#/tally" target="_blank" rel="noopener" title="projected vs what happened">Tally ↗</a>`;
    el.innerHTML = `<div class="lv ${focus ? "lv-focus" : ""}">
      <div class="lv-top"><h1 style="margin:0">${focus ? "Live chart" : "Live terminal"}</h1><span class="lv-pill" id="lvst">…</span><span class="muted small" id="lvmsg"></span><span style="flex:1"></span>
        ${ctl}<button class="btn" id="lvsnd" title="sound on alerts"></button></div>
      ${focus ? "" : `<div class="lv-strip" id="lvstrip"></div><div class="lv-breadth" id="lvbr"></div>`}
      <div class="lv-r1">
        <div class="card lv-chart"><div class="lv-ch">
            <h2 id="lvsym">—</h2><input class="in" id="lvq" placeholder="Search a stock" style="width:200px">
            <span class="small" id="lvlinks"></span><span style="flex:1"></span>
            ${focus ? "" : `<a class="btn small" id="lvzoom" target="_blank" rel="noopener" title="chart + alerts only, full screen, in a new tab">⤢ Zoom</a>`}</div>
          <div class="lv-callline small" id="lvcl"></div>
          <details class="fold lv-det" id="lvdet"><summary>Details</summary><div id="lvcall"></div></details>
          <div class="small muted" id="lvci" style="margin:4px 0"></div><div id="lvc"></div>
          <div class="row" style="gap:6px;margin-top:8px"><input class="in" id="lvnote" placeholder="quick note → saved to this stock's Stock Desk notes with time and price" style="flex:1"><button class="btn" id="lvnb">Save note</button></div></div>
        <div class="card lv-alerts"><div class="ct"><h2>Alerts</h2><span class="aside small muted" id="lvac"></span></div><div id="lva" class="lv-feed"></div></div>
      </div>
      ${focus ? "" : `<div class="card section"><div class="tabs" id="lvt2" style="margin-top:0"><button data-t="watch" class="on">Watch</button><button data-t="sector">By sector</button><button data-t="index">Indices</button></div><div id="lvt2b"></div></div>
      <div class="tabs" id="lvtabs"><button data-t="picks" class="on">Market Lab live</button><button data-t="takes">Paper takes</button></div><div id="lvtab"></div>`}</div>`;

    // sel: {kind: "stock"|"index", key}
    let sel = arg ? { kind: "stock", key: String(arg).toUpperCase() } : store.get("lv-sel2", null), seq = 0, tab = "picks", tab2 = store.get("lv-tab2", "watch"),
      snd = store.get("lv-sound", true), last = {}, alerts = [], busy = false, data = null, firstLoad = true, callFor = null, openSec = null, SPK = {}, spkBusy = false;
    const selKey = () => sel ? `${sel.kind}:${sel.key}` : "";
    const sndBtn = $("#lvsnd", el); const drawSnd = () => { sndBtn.textContent = snd ? "🔔" : "🔕"; }; drawSnd();
    sndBtn.addEventListener("click", () => { snd = !snd; store.set("lv-sound", snd); drawSnd(); if (snd) beep("up"); });
    const act = async (path, body, msg) => { try { await MX.post(path, body, msg); seq = 0; alerts = []; firstLoad = true; tickNow(); } catch (e) { /* toast */ } };
    if (!focus) {
      $("#lvgo", el).addEventListener("click", () => { const on = data && ["live", "replay", "reconnecting", "starting"].includes(data.status.state);
        return on ? act("live/stop", {}, "Stopped") : act("live/start", {}, "Connecting to Angel live feed"); });
      $("#lvrep", el).addEventListener("click", () => act("live/start", { replay: true, day: $("#lvday", el).value || undefined, speed: +$("#lvspd", el).value }, "Replay starting (≈20 s to load bars)"));
      $("#lvt2", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; tab2 = b.dataset.t; store.set("lv-tab2", tab2); $$("#lvt2 button", el).forEach((x) => x.classList.toggle("on", x === b)); drawRow2(); });
      $$("#lvt2 button", el).forEach((x) => x.classList.toggle("on", x.dataset.t === tab2));
      $("#lvtabs", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; tab = b.dataset.t; $$("#lvtabs button", el).forEach((x) => x.classList.toggle("on", x === b)); drawTab(); });
    }
    const q = $("#lvq", el);
    const goQ = () => { const s = q.value.trim().toUpperCase(); if (s) { q.value = ""; pickStock(s); } };
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") goQ(); });
    MX.suggest && MX.suggest(q, { kinds: "stocks", recent: "stock", onPick: () => goQ() });
    const saveNote = async () => { const t = $("#lvnote", el).value.trim(); if (!t || !sel || sel.kind !== "stock") return MX.toast(sel && sel.kind === "stock" ? "Write a note first" : "Pick a stock", true);
      try { const r = await MX.post("live/note", { symbol: sel.key, text: t }); $("#lvnote", el).value = ""; MX.toast(`Note saved to ${sel.key} (${r.stamp})`); } catch (e) { /* toast */ } };
    $("#lvnb", el).addEventListener("click", saveNote); $("#lvnote", el).addEventListener("keydown", (e) => { if (e.key === "Enter") saveNote(); });

    function setSel(s) { sel = s; store.set("lv-sel2", s); callFor = null; $$(".lv-row", el).forEach((r) => r.classList.toggle("sel", r.dataset.k === selKey())); drawChart(); }
    async function pickStock(s) {
      if (data && data.status.state !== "stopped" && !(data.watch || []).some((r) => r.symbol === s)) { try { await MX.post("live/watch", { symbol: s }); } catch (e) { return; } }
      setSel({ kind: "stock", key: s });
    }
    const pickIndex = (k) => setSel({ kind: "index", key: k });

    function drawTop(d) {
      const s = d.status, pill = $("#lvst", el);
      pill.textContent = s.state === "live" ? "● LIVE" : s.state === "replay" ? "▶ REPLAY" : s.state; pill.className = `lv-pill ${s.state}`;
      const on = ["live", "replay", "reconnecting", "starting"].includes(s.state);
      if (!focus) { const go = $("#lvgo", el); go.textContent = on ? (s.state === "replay" ? "■ Stop replay" : "■ Stop live") : "Start live"; go.classList.toggle("primary", !on); go.classList.toggle("danger", on);
        $("#lvrep", el).disabled = on;
        const f = d.fill || {}; $("#lvfill", el).innerHTML = s.mode === "live" && on && (f.done || f.waiting) ? `earlier bars ${f.done}${f.waiting ? ` · <span title="${esc(f.error || "")}">${f.waiting} retrying ⓘ</span>` : " ✓"}` : ""; }
      $("#lvmsg", el).textContent = [s.msg, d.last ? `last tick ${d.last.slice(11)}` : "", d.ticks ? `${num(d.ticks, 0)} ticks` : "",
        d.packets && s.state !== "stopped" ? `packets ${num(d.packets.all, 0)}${d.packets.no_price ? ` (${num(d.packets.no_price, 0)} without a price)` : ""}` : "",
        s.reconnects ? `${s.reconnects} reconnects` : "", d.plan_day ? `plan of ${d.plan_day}` : "", !s.market_open && s.state === "stopped" ? "market closed: use Replay to try it" : ""].filter(Boolean).join(" · ");
      if (focus) return;
      $("#lvstrip", el).innerHTML = (d.strip || []).map((x) => `<button class="lv-ix" data-ix="${esc(x.key)}"><div class="muted small">${esc(x.label)}</div><b>${P(x.ltp)}</b> ${C(x.chg)}</button>`).join("") || `<span class="muted small">Indices appear once the feed runs.</span>`;
      $$(".lv-ix", el).forEach((b) => b.addEventListener("click", () => pickIndex(b.dataset.ix)));
      const b = d.breadth; if (b && b.n) { const a = 100 * b.adv / b.n;
        $("#lvbr", el).innerHTML = `<div class="lv-bar"><span class="up" style="width:${a}%"></span><span class="down" style="width:${100 - a}%"></span></div>
          <span class="small"><b class="up">${b.adv}</b> up · <b class="down">${b.dec}</b> down of ${b.n} · above VWAP ${b.above_vwap} · ≥+1% <b class="up">${b.up1}</b> · ≤−1% <b class="down">${b.down1}</b></span>`; }
      else $("#lvbr", el).innerHTML = "";
    }

    function drawRow2() {
      if (focus) return; const host = $("#lvt2b", el), d = data || {};
      if (tab2 === "watch") {
        const rows = d.watch || [];
        if (!rows.length) { host.innerHTML = `<p class="muted small">Start the feed: watched = Stock Desk list + Market Lab picks + paper takes + stocks you search.</p>`; return; }
        const need = rows.map((r) => r.symbol).filter((s) => !(s in SPK));
        if (need.length && !spkBusy) { spkBusy = true; MX.sparks(need).then((x) => { need.forEach((s) => { SPK[s] = x[s] || null; }); spkBusy = false; }); }
        host.innerHTML = `<div class="scroll"><table class="tbl lv-tbl"><thead><tr><th>Stock</th><th>20 days</th><th>Signals</th><th class="r">Last</th><th class="r">Chg</th><th class="r">vs VWAP</th><th class="r">Day range</th><th>Plan / crosses</th><th>Sector</th></tr></thead><tbody>${rows.map((r) => {
          const f = last[r.symbol] != null && r.ltp != null && r.ltp !== last[r.symbol] ? (r.ltp > last[r.symbol] ? "fl-up" : "fl-down") : "";
          const sig = chips({ ...r, crossed: [], fired: [] }), ev = chips({ crossed: r.crossed, fired: r.fired });
          return `<tr class="lv-row ${selKey() === "stock:" + r.symbol ? "sel" : ""}" data-k="stock:${esc(r.symbol)}" data-s="${esc(r.symbol)}"><td><b>${esc(r.symbol)}</b> <span class="muted small">${r.src.map(esc).join(" · ")}</span></td>
            <td>${MX.spark(SPK[r.symbol])}</td><td><div class="lv-chips">${sig}</div></td><td class="r num ${f}">${P(r.ltp)}</td><td class="r" style="${MX.heat(r.chg, 3)}">${C(r.chg)}</td><td class="r">${C(r.vs_vwap)}</td>
            <td class="r small">${r.low != null ? `${P(r.low)} – ${P(r.high)}` : "—"}</td><td><div class="lv-chips">${ev || `<span class="muted small">—</span>`}</div></td><td class="small muted">${esc(r.sector || "")}</td></tr>`; }).join("")}</tbody></table></div>`;
        rows.forEach((r) => { last[r.symbol] = r.ltp; });
        $$(".lv-row", host).forEach((tr) => tr.addEventListener("click", () => pickStock(tr.dataset.s)));
      } else if (tab2 === "sector") {
        const rows = d.sectors || [];
        if (!rows.length) { host.innerHTML = `<p class="muted small">Sectors appear once the feed runs.</p>`; return; }
        host.innerHTML = `<div class="scroll"><table class="tbl lv-tbl"><thead><tr><th>Sector</th><th class="r">Stocks</th><th>Up / down</th><th class="r">Median</th><th class="r">Sector index</th><th class="r">Above VWAP</th><th>Best</th><th>Worst</th></tr></thead><tbody>${rows.map((r) =>
          `<tr class="lv-row lv-sec ${openSec === r.sector ? "sel" : ""}" data-sec="${esc(r.sector)}"><td><b>${openSec === r.sector ? "▾" : "▸"} ${esc(r.sector)}</b></td><td class="r">${r.n}</td>
            <td><div class="lv-bar sm"><span class="up" style="width:${100 * r.adv / r.n}%"></span><span class="down" style="width:${100 * r.dec / r.n}%"></span></div><span class="small"><span class="up">${r.adv}</span> / <span class="down">${r.dec}</span></span></td>
            <td class="r" style="${MX.heat(r.median, 2)}">${C(r.median)}</td><td class="r">${r.index ? `<button class="lv-sym" data-ix="${esc(r.index.key)}">${esc(r.index.label)}</button> ${C(r.index.chg)}` : `<span class="muted small">—</span>`}</td><td class="r">${r.above_vwap}/${r.n}</td>
            <td><button class="lv-sym" data-s="${esc(r.best.symbol)}">${esc(r.best.symbol)}</button> ${C(r.best.chg)}</td><td><button class="lv-sym" data-s="${esc(r.worst.symbol)}">${esc(r.worst.symbol)}</button> ${C(r.worst.chg)}</td></tr>
            ${openSec === r.sector ? `<tr><td colspan="8" id="lvsecrows"><span class="muted small">Loading …</span></td></tr>` : ""}`).join("")}</tbody></table></div>`;
        $$(".lv-sec", host).forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.closest("button")) return; openSec = openSec === tr.dataset.sec ? null : tr.dataset.sec; drawRow2(); }));
        if (openSec) api(`live/sector?${MX.qs({ name: openSec })}`).then((x) => { const c = $("#lvsecrows", host); if (!c) return;
          c.innerHTML = `<div class="lv-secgrid">${x.items.map((s) => `<button class="lv-secst" data-s="${esc(s.symbol)}"><b>${esc(s.symbol)}</b>${s.watched ? " •" : ""} ${C(s.chg)}<span class="muted small"> ${P(s.ltp)}</span></button>`).join("")}</div>`;
          $$(".lv-secst", c).forEach((b) => b.addEventListener("click", () => pickStock(b.dataset.s))); }).catch(() => {});
      } else {
        const rows = d.indices || [];
        if (!rows.length) { host.innerHTML = `<p class="muted small">Indices appear once the feed runs.</p>`; return; }
        host.innerHTML = `<div class="scroll"><table class="tbl lv-tbl"><thead><tr><th>Index</th><th class="r">Last</th><th class="r">Chg</th><th class="r">vs open</th><th class="r">Day range</th><th class="r">Updated</th></tr></thead><tbody>${rows.sort((a, b) => (b.chg ?? -99) - (a.chg ?? -99)).map((r) =>
          `<tr class="lv-row ${selKey() === "index:" + r.key ? "sel" : ""}" data-k="index:${esc(r.key)}" data-ix="${esc(r.key)}"><td><b>${esc(r.label)}</b></td><td class="r num">${P(r.ltp)}</td><td class="r" style="${MX.heat(r.chg, 2)}">${C(r.chg)}</td><td class="r">${C(r.vs_open)}</td>
            <td class="r small">${r.low != null ? `${P(r.low)} – ${P(r.high)}` : "—"}</td><td class="r small muted">${esc(r.ts || "")}</td></tr>`).join("")}</tbody></table></div>`;
      }
      $$("[data-ix]", host).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); pickIndex(b.dataset.ix); }));
      $$("button.lv-sym[data-s]", host).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); pickStock(b.dataset.s); }));
    }

    function drawAlerts(d) {
      const nw = d.alerts || [];
      if (nw.length && !firstLoad && snd) beep(nw[nw.length - 1].tone);
      alerts = alerts.concat(nw).slice(-300); seq = d.seq || seq; firstLoad = false;
      $("#lvac", el).textContent = alerts.length ? `${alerts.length}` : "";
      $("#lva", el).innerHTML = alerts.length ? alerts.slice().reverse().map((a) => `<div class="lv-al ${esc(a.tone)} ${nw.includes(a) ? "new" : ""}"><span class="muted small">${esc(a.at)}</span>
        <button class="lv-sym" data-s="${esc(a.symbol)}">${esc(a.symbol)}</button> ${esc(a.text.replace(a.symbol + " ", ""))}</div>`).join("") : `<p class="muted small">±1% / ±2% crosses, plan E1 / SL / T1 / T2 and paper fills show here.</p>`;
      $$("#lva .lv-sym", el).forEach((b) => b.addEventListener("click", () => pickStock(b.dataset.s)));
      fitAlerts();
    }

    function drawTab() {
      if (focus) return; const host = $("#lvtab", el), d = data || {};
      if (tab === "picks") {
        const p = d.picks || [];
        if (!p.length) { host.innerHTML = `<p class="muted">No Market Lab picks loaded (build tonight's radar, then start the feed).</p>`; return; }
        const done = p.filter((x) => x.moved), right = p.filter((x) => x.lean_right != null), ok = right.filter((x) => x.lean_right);
        host.innerHTML = `<p class="small">So far: <b>${done.length}/${p.length}</b> picks are ≥1% away now · ${p.filter((x) => x.touched).length} touched 1% · lean right <b>${ok.length}/${right.length}</b> (only picks with a shown lean).</p>` +
          table(p, [["symbol", "Stock", (v) => `<button class="lv-sym" data-s="${esc(v)}">${esc(v)}</button>`], ["move", "Move", (v) => v === "cc" ? "close-to-close" : "open-to-close"],
            ["chance", "Chance ≥1%", (v, r) => MX.chanceBar(v, r.usual)], ["lean", "Lean", (v) => LBL(v)],
            ["move_now", "Now", (v) => C(v)], ["moved", "≥1% now", (v) => v == null ? "—" : v ? "✓" : "·"], ["touched", "Touched", (v) => v == null ? "—" : v ? "✓" : "·"],
            ["beyond_min", "Min beyond 1%", (v, r) => v ? `${num(v, 0)} <span class="muted small">from ${esc(r.first || "")}</span>` : "—"], ["lean_right", "Lean right?", (v) => v == null ? "—" : v ? `<span class="up">yes</span>` : `<span class="down">no</span>`],
            ["confidence", "Conf.", (v) => MX.conf(v)]], "Nothing here.", { rowClass: (r) => r.moved == null ? "" : r.moved && r.lean_right !== false ? "row-hit" : r.touched ? "" : "row-miss" });
      } else {
        const t = d.takes || [];
        if (!t.length) { host.innerHTML = `<p class="muted">No paper takes for this plan (Trade plan → Paper take).</p>`; return; }
        const tot = t.reduce((a, x) => a + (x.pnl || 0), 0), cst = t.reduce((a, x) => a + (x.costs || 0), 0);
        host.innerHTML = `<p class="small">Total net paper P&amp;L <b class="${tot >= 0 ? "up" : "down"}">₹${num(tot, 0)}</b> <span class="muted">after ₹${num(cst, 0)} costs (brokerage, STT, exchange, stamp, GST, 0.03% slippage per side; open trades include the exit)</span></p>` +
          table(t, [["symbol", "Stock", (v) => `<button class="lv-sym" data-s="${esc(v)}">${esc(v)}</button>`], ["mode", "Mode"], ["state", "State"], ["side", "Side", (v) => v || "—"],
            ["entry", "Entry", (v) => P(v)], ["left", "Open qty", (v, r) => `${v}/${r.qty}`], ["stop", "Stop", (v) => P(v)], ["last", "Last", (v) => P(v)],
            ["gross", "Gross ₹", (v) => num(v, 0)], ["costs", "Costs ₹", (v) => v ? `<span class="muted">−${num(v, 0)}</span>` : "—"],
            ["pnl", "Net P&L", (v) => `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "▲ " : v < 0 ? "▼ " : ""}${MX.money(v, { sign: true })}</b>`], ["r", "Net R", (v) => v == null ? "—" : num(v, 2)],
            ["events", "Fills", (v) => (v || []).map((e) => `<div class="small">${esc(e.at)} ${esc(e.what)} @ ${P(e.price)}</div>`).join("")]]);
      }
      $$(".lv-sym", host).forEach((b) => b.addEventListener("click", () => pickStock(b.dataset.s)));
    }

    const fitAlerts = () => { const c = $(".lv-chart", el); if (c) $("#lva", el).style.maxHeight = `${Math.max(240, c.offsetHeight - 70)}px`; };
    const chartH = () => Math.max(380, Math.min(760, window.innerHeight - (focus ? 230 : 330)));
    async function drawChart() {
      const host = $("#lvc", el);
      if (!sel) { const w = (data && data.watch) || []; if (w.length) sel = { kind: "stock", key: w[0].symbol }; else { host.innerHTML = `<div class="empty" style="height:${chartH()}px">Search a stock or start the feed.</div>`; return; } }
      const k = selKey();
      $("#lvsym", el).textContent = sel.kind === "index" ? ((data && (data.indices || []).find((x) => x.key === sel.key)) || {}).label || sel.key : sel.key;
      if (!focus) $("#lvzoom", el).href = sel.kind === "stock" ? `#/live-focus/${encodeURIComponent(sel.key)}` : "#/live-focus";
      if (callFor !== k) { $("#lvcl", el).innerHTML = ""; $("#lvcall", el).innerHTML = ""; $("#lvlinks", el).innerHTML = ""; }
      if (!data || !data.status || (data.status.state === "stopped" && !data.ticks)) { host.innerHTML = `<div class="empty" style="height:${chartH()}px">Start the feed to see ${esc(sel.key)} live.</div>`; return; }
      let c; try { c = await api(`live/chart?${MX.qs(sel.kind === "index" ? { index: sel.key } : { symbol: sel.key })}`); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
      if (!el.isConnected || selKey() !== k) return;
      if (callFor !== k) { callFor = k;
        if (sel.kind === "stock") { $("#lvcl", el).innerHTML = callLine(c.call); $("#lvcall", el).innerHTML = callPanel(c.call); $("#lvdet", el).hidden = false;
          $("#lvlinks", el).innerHTML = `<a href="#/ta/${encodeURIComponent(sel.key)}" target="_blank" rel="noopener">TA ↗</a> · <a href="#/desk/${encodeURIComponent(sel.key)}" target="_blank" rel="noopener">Desk ↗</a>`; }
        else { $("#lvcl", el).innerHTML = `<span class="muted">Index · no plan</span>`; $("#lvdet", el).hidden = true; } }
      const levels = [];
      if (c.prev) { levels.push({ price: c.prev, color: "faint", label: "prev", width: 1.2 }); levels.push({ price: c.prev * 1.01, color: "faint", dash: "2 4", label: "+1%" }); levels.push({ price: c.prev * 0.99, color: "faint", dash: "2 4", label: "−1%" }); }
      if (c.or_high != null) { levels.push({ price: c.or_high, color: "accent", dash: "5 3", label: "OR hi" }); levels.push({ price: c.or_low, color: "accent", dash: "5 3", label: "OR lo" }); }
      (c.legs || []).forEach((x) => levels.push({ price: x.price, color: COL[x.leg] || "faint", label: `${x.leg}${x.side === "short" ? "↓" : "↑"}`, width: x.leg === "SL" ? 1.6 : 1.3 }));
      if (!c.bars.t.length) { host.innerHTML = `<div class="empty" style="height:${chartH()}px">No bars for ${esc(sel.key)} yet (they start at 09:15).</div>`; return; }
      MXCharts.priceChart(host, c.bars, { mode: "candle", intraday: true, height: chartH(), levels, slots: c.slots || 75, offset: c.offset || 0, slotLabel });
      fitAlerts();
      $("#lvci", el).innerHTML = `${P(c.ltp)} · blue line = VWAP · OR = 09:15–09:30 range · dashed = prev close ±1%${sel.kind === "stock" ? " · E1/SL/T1/T2 = plan" : ""}${c.offset > 1 ? ` · <span class="down">bars from ${slotLabel(c.offset)} only (earlier bars not loaded yet)</span>` : ""}`;
    }

    async function tickNow() {
      if (busy || !el.isConnected) return; busy = true;
      try { data = await api(`live/state?since=${seq}`); drawTop(data); drawAlerts(data); drawRow2(); drawTab(); } catch (e) { /* server restarting */ } finally { busy = false; }
    }
    await tickNow(); drawChart();
    const t1 = setInterval(() => { if (!el.isConnected) return clearInterval(t1); tickNow(); }, 1500);
    const t2 = setInterval(() => { if (!el.isConnected) return clearInterval(t2); if (data && ["live", "replay", "reconnecting"].includes(data.status.state)) drawChart(); }, 5000);
    const onResize = () => { if (!el.isConnected) return window.removeEventListener("resize", onResize); drawChart(); };
    window.addEventListener("resize", onResize);
  }

  MX.page("live", { title: "Live terminal", render: (el, arg) => page(el, arg, false) });
  MX.page("live-focus", { title: "Live chart", render: (el, arg) => page(el, arg, true) });
})();
