/* Trade plan view (secondary to Market Lab): summary card, chart with entry / exit tags and zones, legs, candle patterns.
   MX.planView(host, symbol) is used by the Stock page and Market Lab → Stock analysis. Data: /api/tradeplan. */
(function () {
  const { api, esc, num, inr, day, $, $$, note, startJob } = MX;
  const COL = { E1: "up", E2: "up", SL: "down", T1: "blue", T2: "blue" };
  const COLS = { E1: "up", E2: "up", SL: "down", T1: "blue", T2: "blue" };
  const SHORT = COL;                                     // colours follow the role (entry / stop / target), not the side
  let mode = (() => { try { return sessionStorage.getItem("mx-tp-mode") || "intraday"; } catch (e) { return "intraday"; } })();
  const show = { plan: true, levels: true, patterns: true };

  function card(d) {
    const p = d.plan, sides = p.sides || [], lv = p.levels;
    const leanTxt = p.lean === "up" ? `<span class="up">▲ up-lean · long plan</span>` : p.lean === "down" ? `<span class="down">▼ down-lean · short plan</span>` : `<span class="muted">— no tested lean · plan both ways (first break wins)</span>`;
    const legs = p.legs.filter((x) => x.price != null);
    const fmtLeg = (x) => `<span class="tp-leg tp-${(x.side === "short" ? SHORT : COLS)[x.leg] || "faint"}">${x.leg}${p.lean === "unclear" ? (x.side === "short" ? "↓" : "↑") : ""} ${inr(x.price)}</span>`;
    return `<div class="card tp-card"><div class="ct"><h2>${esc(d.symbol)} · Trade plan (${esc(p.mode)})</h2><span class="aside">${leanTxt}</span></div>
      <div class="tp-sum"><b>${p.n_entries} entr${p.n_entries === 1 ? "y" : "ies"} · ${p.n_exits} exits</b>
        ${sides.map((s) => ` · ${esc(s.side)}: ${num(s.shares, 0)} shares (≈${inr(s.value_rs, 0)}), risk ${inr(s.risk_rs, 0)}, reward:risk <b>${num(s.rr, 1)} : 1</b>, stop ${num(s.stop_atr, 1)}× ATR`).join("")}</div>
      <div class="tp-legs">${legs.map(fmtLeg).join("")}<span class="tp-leg tp-faint">TX ${esc(p.hold)}</span></div>
      ${p.flags.length ? note(`${p.tradeable ? "Note" : "<b>No trade</b>"}: ${p.flags.map(esc).join(" · ")}`, p.tradeable ? "info" : "warn") : `<p class="muted small" style="margin:6px 0 0">Passes the plan's checks (reward:risk ≥ 1.5, stop ≤ 2× ATR, positive Market Lab edge, risk limits).</p>`}
      <p class="muted small" style="margin:6px 0 0">Levels from the ${esc(day(lv.date))} bar · ATR ${inr(lv.atr)} · pivot ${inr(lv.P)} · rules ${esc(d.version)}. Secondary to Market Lab: it never changes the verdict. Not tested on history yet (needs 5-minute bars).</p></div>`;
  }

  function chartSpec(d) {
    const p = d.plan, b = d.bars, n = b.t.length, from = Math.max(0, n - 12), lv = p.levels, levels = [], zones = [], marks = [];
    if (show.plan) {
      p.legs.filter((x) => x.price != null).forEach((x) => levels.push({ price: x.price, color: (x.side === "short" ? SHORT : COL)[x.leg], from, label: p.lean === "unclear" ? `${x.leg}${x.side === "short" ? "↓" : "↑"}` : x.leg, fit: true, width: x.leg === "SL" ? 1.8 : 1.4 }));
      const sides = [...new Set(p.legs.filter((x) => x.side !== "both").map((x) => x.side))];
      sides.forEach((sd) => { const g = (k) => p.legs.find((x) => x.leg === k && x.side === sd);
        const e1 = g("E1"), sl = g("SL"), t2 = g("T2") || g("T1");
        if (e1 && t2) zones.push({ lo: e1.price, hi: t2.price, color: "up", from, opacity: 0.07 });
        if (e1 && sl) zones.push({ lo: e1.price, hi: sl.price, color: "down", from, opacity: 0.08 }); });
    }
    if (show.levels) {
      [["P", lv.P], ["R1", lv.R1], ["S1", lv.S1], ["20d hi", lv.hi20], ["20d lo", lv.lo20]].forEach(([k, v]) => levels.push({ price: v, color: "faint", dash: "2 4", width: 1, from: Math.max(0, n - 40) }));
    }
    if (show.patterns) {
      (d.patterns || []).forEach((pt) => { const i = b.t.indexOf(pt.date); if (i < 0) return;
        const bull = pt.direction === "bullish", bear = pt.direction === "bearish";
        marks.push({ i, price: bear ? b.h[i] : b.l[i], dir: bear ? "down" : "up", color: bull ? "up" : bear ? "down" : "faint", label: pt.label }); });
    }
    return { mode: "candle", height: 420, levels, zones, marks };
  }

  async function planView(host, symbol) {
    host.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:10px">
        <div class="seg" id="tpm"><button data-v="intraday" class="${mode === "intraday" ? "on" : ""}">Intraday</button><button data-v="swing" class="${mode === "swing" ? "on" : ""}">Swing (days)</button></div>
        <div class="seg" id="tpt"><button data-v="plan" class="on">Plan</button><button data-v="levels" class="on">Levels</button><button data-v="patterns" class="on">Patterns</button></div></div><div id="tpb">${MX.spinner("Loading the plan …")}</div>`;
    const load = async () => {
      const d = await api(`tradeplan?${MX.qs({ symbol, mode })}`);
      const b = $("#tpb", host);
      if (!d.ready) { b.innerHTML = note(esc(d.why), "warn"); return; }
      b.innerHTML = card(d) + `<div class="card section"><div class="ct"><h2>Chart: last 45 sessions with the next session's plan</h2><span class="aside muted small">tags on the right · green zone = entry to target · red zone = entry to stop</span></div><div id="tpc"></div>
        <div class="legend"><span><i style="background:var(--up)"></i>entries</span><span><i style="background:var(--down)"></i>stop</span><span><i style="background:#0ea5e9"></i>targets</span><span><i style="background:var(--faint)"></i>pivot / R1 / S1 / 20-day high-low</span><span>▲▼ candle patterns</span></div></div>
        <div class="grid g2 section"><div class="card"><div class="ct"><h2>Legs</h2></div>${MX.table(d.plan.legs.map((x) => ({ ...x, pct: x.share == null ? null : 100 * x.share })), [["leg", "Leg", "tag"], ["side", "Side"], ["price", "Price", "inr"], ["pct", "Share %", "pct0"], ["shares", "Shares", "int"], ["condition", "When", (v) => `<span class="small">${esc(v)}</span>`]])}</div>
          <div class="card"><div class="ct"><h2>Candle patterns (last 5 sessions)</h2><span class="aside muted small">${esc(d.patterns_source || "")}</span></div>
            ${d.patterns.length ? MX.table(d.patterns, [["date", "Date", "date"], ["label", "Pattern"], ["direction", "Direction", "tag"]]) : `<p class="muted">None in the last 5 sessions.</p>`}
            <p class="muted small">Patterns are hints until tested on history (Trade plan step 3).</p>
            ${d.talib.ok ? `<p class="muted small">TA-Lib ${esc(d.talib.version)} installed.</p>` : `<p class="muted small">TA-Lib isn't installed: using MarketX's built-in patterns. <button class="btn small" id="tpi">Install TA-Lib (optional)</button></p>`}</div></div>`;
      const N = 45, cut = (a) => a.slice(-N), bb = { t: cut(d.bars.t), o: cut(d.bars.o), h: cut(d.bars.h), l: cut(d.bars.l), c: cut(d.bars.c), v: cut(d.bars.v), sma50: null, sma200: null };
      const draw = () => { if (document.body.contains(host) && $("#tpc", host)) MXCharts.priceChart($("#tpc", host), bb, chartSpec({ ...d, bars: bb })); };
      requestAnimationFrame(draw); window.addEventListener("mx-resize", draw);
      $("#tpi", host)?.addEventListener("click", () => startJob("talib-install"));
      host._redraw = draw;
    };
    $("#tpm", host).addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; mode = x.dataset.v; try { sessionStorage.setItem("mx-tp-mode", mode); } catch (er) { /* ignore */ }
      $$("#tpm button", host).forEach((y) => y.classList.toggle("on", y === x)); load(); });
    $("#tpt", host).addEventListener("click", (e) => { const x = e.target.closest("button"); if (!x) return; show[x.dataset.v] = !show[x.dataset.v]; x.classList.toggle("on", show[x.dataset.v]); host._redraw && host._redraw(); });
    await load();
  }
  MX.planView = planView;
})();
