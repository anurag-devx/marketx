/* Today: Overview, Market, Stock. */
(function () {
  // Stock → Intraday: 5-minute bars from Angel One (core/intraday.py), grouped to 5/15/30/60 minutes, with VWAP
  async function intradayTab(t, sym) {
    const { $, $$, esc, inr, num, pct, cls } = MX;
    t.innerHTML = `<div class="card"><div class="ct"><div class="seg" id="idd"><button data-v="1" class="on">1 day</button><button data-v="5">5 days</button><button data-v="20">1 month</button></div>
      <div class="seg" id="idb" style="margin-left:8px"><button data-v="5" class="on">5 min</button><button data-v="15">15 min</button><button data-v="30">30 min</button><button data-v="60">1 hour</button></div>
      <div class="seg" id="idm" style="margin-left:8px"><button data-v="candle" class="on">Candles</button><button data-v="line">Line</button></div><span class="aside"><span id="ida"></span> <button class="btn small" id="iwsv">Save to workspace</button></span></div>
      <div id="idc"></div><div class="legend"><span><i style="background:#0ea5e9"></i>VWAP (volume-weighted average price, restarts each day)</span><span>volume: green up bar, red down bar</span></div><p class="muted small" id="idn"></p></div>`;
    let days = 1, bar = 5, mode = "candle", d = null;
    const load = async () => {
      d = await MX.api(`stock/intraday?${MX.qs({ symbol: sym, days, bar })}`);
      if (d.empty) { $("#idc", t).innerHTML = `<div class="empty">${esc(d.note)}</div>`; $("#ida", t).textContent = ""; return; }
      const L = d.last, ch = 100 * (L.close / L.open - 1);
      $("#ida", t).innerHTML = `${esc(MX.day(d.day))} · O ${inr(L.open)} H ${inr(L.high)} L ${inr(L.low)} C ${inr(L.close)} <span class="${cls(ch)}">${pct(ch, 2)} from open</span>`;
      $("#idn", t).textContent = d.note; draw();
    };
    const draw = () => { if (d && !d.empty && document.body.contains(t)) MXCharts.priceChart($("#idc", t), d.bars, { mode, height: 380, intraday: true }); };
    const seg = (id, set, reload) => $(id, t).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
      $$("button", $(id, t)).forEach((x) => x.classList.toggle("on", x === b)); set(b.dataset.v); reload ? load() : draw(); });
    seg("#idd", (v) => { days = +v; if (days >= 20 && bar < 30) { bar = 30; $$("#idb button", t).forEach((x) => x.classList.toggle("on", x.dataset.v === "30")); } }, true);
    seg("#idb", (v) => (bar = +v), true); seg("#idm", (v) => (mode = v));
    $("#iwsv", t).addEventListener("click", () => MX.saveChart({ symbol: sym, timeframe: "5min", days: Math.max(2, Math.ceil(days * 1.5)), source: "intraday chart" }));
    window.addEventListener("mx-resize", draw);
    await load();
  }

  const { api, esc, num, pct, cls, inr, day, dt, $, table, kpi, card, head, note, form, tabs, sections, newsList, startJob, dataTable, qs } = MX;
  const sym = "sym";

  MX.page("overview", {
    title: "Overview", reloadOnJob: true,
    async render(el) {
      const o = await api("overview");
      const r = o.regime, m = o.market, s = m && m.summary, nifty = m && m.nifty;
      el.innerHTML = head("Overview", r ? `Market as of ${day(r.date, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}` : "The agents haven't completed a run yet",
        `<button class="btn" id="b-agents">Run all agents</button>`) +
        (o.pending ? `<div class="section">${note(`${o.pending} proposal(s) wait for your approval — <a href="#/settings">Settings → Approvals</a>.`)}</div>` : "") +
        (r && r.note ? note(esc(r.note), "warn") : "") +
        `<div class="grid g4 section">
          ${kpi("Nifty 50", nifty ? num(nifty.value, 0) : "—", nifty ? `<span class="${cls(nifty.chg)}">${pct(nifty.chg, 2)}</span> today` : "", nifty ? MXCharts.sparkline(nifty.spark, { h: 34 }) : "")}
          ${kpi("Market state", r ? esc(r.state) : "—", r ? `${r.days} days · size ×${r.size}` : "")}
          ${kpi("Breadth", s ? `${num(s.above200, 0)}%` : "—", s ? `above 200-day · <span class="up">${s.up} up</span> / <span class="down">${s.down} down</span>` : "")}
          ${kpi("India VIX", r && r.vix != null ? num(r.vix, 1) : nifty && nifty.vix ? num(nifty.vix, 1) : "—", r && r.vix_median ? `1-year median ${num(r.vix_median, 1)}` : "")}
        </div>
        <div class="section"><div class="sh"><h3>Agents</h3><a class="more" href="#/agents">Details</a></div>
          <div class="tiles">${o.agents.map((a) => `<div class="tile ${a.state === "run" ? "run" : ""}" title="${esc(a.note || a.sub || "")}"><div class="n"><span class="dot ${a.state}"></span>${esc(a.name)}</div><div class="s">${esc(a.sub)}</div></div>`).join("")}</div></div>
        <div class="section grid g2">
          ${card("Trades for the next open", table(o.trades, [["symbol", "Stock", sym], ["strategy_name", "Strategy"], ["entry", "Entry", "inr"], ["stop", "Stop", "inr"], ["quantity", "Qty", "int"]],
            r && !r.active ? "No trades: no strategy has a tested edge in this market state." : "No approved trades."), r ? `Active: ${esc(r.active || "none")}` : "")}
          ${card("Exits due", table(o.exits, [["symbol", "Stock", sym], ["strategy", "Strategy"], ["days_held", "Days", "int"], ["action", "Action"]], "No exits due."))}
        </div>
        ${m ? `<div class="section grid g3">
          ${card("Top gainers", table(m.gainers, [["symbol", "Stock", sym], ["close", "Close", "inr"], ["r1", "Day", "spct2"]]), `<a href="#/market">Market</a>`)}
          ${card("Top losers", table(m.losers, [["symbol", "Stock", sym], ["close", "Close", "inr"], ["r1", "Day", "spct2"]]))}
          ${card("Sectors today", table([...m.sectors_top, ...m.sectors_bottom], [["sector", "Sector"], ["day", "Day", "spct2"], ["week", "Week", "spct1"]]), "best and worst")}
        </div>` : ""}
        <div class="section grid g2">
          ${card("Screener shortlist", table(o.shortlist, [["symbol", "Stock", sym], ["sector", "Sector"], ["setups_passed", "Setups", "int"], ["rank", "Rank", "int"]], "No shortlist yet: run the agents."),
            o.shortlist && o.shortlist.length ? `${day(o.shortlist[0].screen_date)} · <a href="#/screener">Screener</a>` : "")}
          ${card("Results", (o.results.recent && o.results.recent.length ? table(o.results.recent, [["symbol", "Stock", sym], ["quarter", "Quarter"], ["profit_yoy", "Profit YoY", "spct1"], ["sue_profit", "Surprise", "snum1"], ["react_rel", "Reaction", "spct1"]])
            : `<div class="muted small">No results filed in the last 3 days.</div>`) + (o.results.upcoming && o.results.upcoming.length ? `<h3 style="margin:16px 0 6px">Coming up</h3>` +
            table(o.results.upcoming, [["date", "Date", (v) => day(v, { weekday: "short", day: "numeric", month: "short" })], ["symbol", "Stock", sym]]) : ""), `<a href="#/screener">Results tab</a>`)}
        </div>
        ${o.backdrop.length ? `<div class="section"><div class="sh"><h3>Backdrop</h3><a class="more" href="#/macro">Macro</a></div><div class="grid g6">${o.backdrop.map((b) =>
          kpi(esc(b.label), b.change_kind === "total" ? `${inr(b.value, 0)} cr` : num(b.value, 2), b.change_kind === "total" ? (b.chg_5d != null ? `5 days ${inr(b.chg_5d, 0)} cr` : "") : (b.chg_20d != null ? `${b.chg_20d > 0 ? "+" : ""}${num(b.chg_20d, 2)}${b.change_kind === "points" ? " pts" : "%"} in a month` : ""))).join("")}</div></div>` : ""}`;
      $("#b-agents", el)?.addEventListener("click", () => startJob("pipeline"));
    },
  });

  MX.page("market", {
    title: "Market",
    async render(el) {
      el.innerHTML = head("Market", "Breadth, sectors, movers and news across the Nifty 500") + `<div id="mt"></div>`;
      tabs($("#mt", el), ["Highlights", "News", "Catalyst"], async (i, body) => {
        if (i === 0) {
          body.innerHTML = `<div class="panel"><div id="hf"></div></div><div id="hs"></div>`;
          const f = form($("#hf", body), [
            { k: "as_of", label: "As of", type: "date", value: "" },
            { k: "range", label: "Chart range", type: "radio", options: ["3M", "6M", "1Y", "3Y", "5Y", "All"], value: "1Y" },
            { k: "period", label: "Sector moves over", type: "radio", options: [["day", "Day"], ["week", "Week"], ["month", "Month"]], value: "day" }], () => load());
          const load = async () => { $("#hs", body).innerHTML = MX.spinner("Loading"); const d = await api(`market/highlights?${qs(f.values())}`);
            sections($("#hs", body), [{ kind: "note", tone: "info", text: `Nifty 500 stocks on ${day(d.day, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}` }, ...d.sections]); };
          await load();
        } else if (i === 1) {
          body.innerHTML = `<div class="panel"><div class="row"><div id="nf" style="flex:1"></div><button class="btn" id="nref">Refresh news</button></div></div><div id="ns"></div>`;
          $("#nref", body).addEventListener("click", () => startJob("news"));
          let kinds = null;
          const f = form($("#nf", body), [
            { k: "days", label: "From the last", type: "select", options: [[1, "24 hours"], [3, "3 days"], [7, "7 days"], [30, "30 days"]], value: 7 },
            { k: "q", label: "Search headlines", type: "text", placeholder: "RBI, crude, results" },
            { k: "tagged", label: "Only headlines naming a stock", type: "toggle" }], () => load());
          const load = async () => { const v = f.values(); const d = await api(`market/news?${qs({ ...v, tagged: v.tagged ? "true" : "" })}`); kinds = d.kinds;
            $("#ns", body).innerHTML = card(`${d.items.length} headlines`, newsList(d.items, "No headlines yet. Press Refresh news."),
              `${d.when ? `fetched ${esc(String(d.when).slice(0, 16))}` : "not fetched yet"}${d.errors && d.errors.length ? ` · not working: ${esc(d.errors.slice(0, 4).join(", "))}` : ""}`); };
          await load();
        } else {
          body.innerHTML = note("Pick a headline (or type one). The system finds similar news and filings from the past and shows which stocks and sectors moved on those days. It describes what happened before; it is not a forecast.") +
            `<div class="panel section"><div id="cf"></div><div class="row" style="margin-top:12px"><button class="btn primary" id="cgo">Find similar past news</button></div></div><div id="cs"></div>`;
          const recent = await api("catalyst/recent");
          const f = form($("#cf", body), [
            { k: "pick", label: "Recent headline", type: "select", wide: true, options: [["", "— type my own below —"], ...recent.map((r, i) => [i, `${dt(r.published)} · ${r.title.slice(0, 150)}`])], value: recent.length ? 0 : "" },
            { k: "text", label: "Or type a headline", type: "text", wide: true, placeholder: "e.g. RBI cuts repo rate by 25 bps / Crude jumps 5% / HAL bags ₹5,000 crore order" },
            { k: "k", label: "Similar items to use", type: "select", options: [50, 100, 200, 400], value: 100 },
            { k: "min_score", label: "Minimum similarity", type: "number", value: 0.15, min: 0.05, max: 0.6, step: 0.05 },
            { k: "filings", label: "Include NSE filings", type: "toggle", value: true },
            { k: "same", label: "Same event type only", type: "toggle", value: false }]);
          $("#cgo", body).addEventListener("click", async () => {
            const v = f.values(), r = v.text ? null : recent[v.pick];
            const text = v.text || (r && r.title); if (!text) return MX.toast("Pick or type a headline", true);
            $("#cs", body).innerHTML = MX.spinner("Searching past news and measuring moves (first time indexes everything, about a minute)");
            try { const d = await api(`catalyst?${qs({ text, when: r ? r.published : "", k: v.k, min_score: v.min_score, filings: v.filings ? "true" : "false", same: v.same ? "true" : "false" })}`);
              sections($("#cs", body), d.sections); } catch (e) { $("#cs", body).innerHTML = note(esc(e.message), "warn"); }
          });
        }
      }, "market");
    },
  });

  MX.page("stock", {
    title: "Stock",
    async render(el, arg) {
      let symb = arg; try { symb = symb || localStorage.getItem("mx-stock"); } catch (e) { /* ignore */ }
      symb = (symb || "RELIANCE").toUpperCase();
      try { localStorage.setItem("mx-stock", symb); } catch (e) { /* ignore */ }
      const d = await api(`stock?symbol=${encodeURIComponent(symb)}`);
      const i = d.info, b = d.bars;
      el.innerHTML = head(esc(i.company), `${esc(i.symbol)} · ${esc(i.sector || "")} · last bar ${day(i.last_date, { weekday: "short", day: "numeric", month: "short" })}`,
        `<a class="btn" href="#/debate/${encodeURIComponent(i.symbol)}">Debate this stock</a>`) +
        `<div class="quote"><span class="px">${inr(i.close)}</span><span class="chg ${cls(i.chg)}">${pct(i.chg, 2)}</span><span class="muted">on the day</span></div>
        <div class="card flush" style="margin-top:16px"><div class="facts">
          <div><div class="k">1 month</div><div class="v ${cls(i.r1m)}">${pct(i.r1m)}</div></div>
          <div><div class="k">3 months</div><div class="v ${cls(i.r3m)}">${pct(i.r3m)}</div></div>
          <div><div class="k">1 year</div><div class="v ${cls(i.r1y)}">${pct(i.r1y)}</div></div>
          <div><div class="k">52-week range</div><div class="v">${inr(i.lo52)} – ${inr(i.hi52)}</div></div></div></div><div id="st"></div>`;
      tabs($("#st", el), ["Chart", "Intraday", "Trade plan", "Results", "Ownership", "F&O", "News & filings", "Agents"], (k, t) => {
        if (k === 1) { intradayTab(t, i.symbol); return; }
        if (k === 2) { MX.planView(t, i.symbol); return; }
        if (k > 2) k -= 2;
        if (k === 0) {
          t.innerHTML = `<div class="card"><div class="ct"><div class="seg" id="rng">${[["3M", 91], ["6M", 182], ["1Y", 365], ["3Y", 1100], ["5Y", 1830], ["All", 99999]].map(([l, n]) => `<button data-n="${n}" class="${n === 365 ? "on" : ""}">${l}</button>`).join("")}</div>
            <div class="seg" id="ivl" style="margin-left:8px"><button data-i="D" class="on" title="One candle per trading day">Daily</button><button data-i="W" title="One candle per week (Mon–Fri)">Weekly</button><button data-i="M" title="One candle per month">Monthly</button></div>
            <div class="seg" id="mode" style="margin-left:8px"><button data-m="candle" class="on">Candles</button><button data-m="line">Line</button></div>
            <span class="aside">avg turnover ₹${num(i.avg_turnover_cr, 0)} cr/day · ${pct(i.from_hi)} from 52-week high <button class="btn small" id="wsv">Save to workspace</button></span></div>
            <div id="pc"></div><div class="legend"><span><i style="background:#f59e0b"></i>50-day average</span><span><i style="background:var(--faint)"></i>200-day average</span><span>volume: green up day, red down day</span></div></div>`;
          let n = 365, mode = "candle", ivl = "D";
          // weekly / monthly candles from the daily bars: open = first, high = max, low = min, close = last, volume = sum;
          // the 50- and 200-day averages are read at each period's last day, so they mean the same thing at every interval
          const group = (bars, key) => { const out = { t: [], o: [], h: [], l: [], c: [], v: [], sma50: [], sma200: [] }; let cur = null;
            bars.t.forEach((d, i) => { const k = key(d);
              if (k !== cur) { cur = k; out.t.push(d); out.o.push(bars.o[i]); out.h.push(bars.h[i]); out.l.push(bars.l[i]); out.c.push(bars.c[i]); out.v.push(bars.v[i]); out.sma50.push(bars.sma50[i]); out.sma200.push(bars.sma200[i]); return; }
              const j = out.t.length - 1; out.t[j] = d; out.h[j] = Math.max(out.h[j], bars.h[i]); out.l[j] = Math.min(out.l[j], bars.l[i]);
              out.c[j] = bars.c[i]; out.v[j] += bars.v[i]; out.sma50[j] = bars.sma50[i]; out.sma200[j] = bars.sma200[i]; });
            return out; };
          const weekKey = (d) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7)); return x.toISOString().slice(0, 10); };
          const draw = () => { if (!document.body.contains(t) || !$("#pc", t)) return;
            const cut = new Date(b.t[b.t.length - 1]); cut.setDate(cut.getDate() - n);
            const k0 = b.t.findIndex((x) => new Date(x) >= cut), sl = (a) => a.slice(Math.max(0, k0));
            const daily = { t: sl(b.t), o: sl(b.o), h: sl(b.h), l: sl(b.l), c: sl(b.c), v: sl(b.v), sma50: sl(b.sma50), sma200: sl(b.sma200) };
            const bars = ivl === "W" ? group(daily, weekKey) : ivl === "M" ? group(daily, (d) => d.slice(0, 7)) : daily;
            MXCharts.priceChart($("#pc", t), bars, { mode, height: 400 }); };
          const seg = (id, attr, set) => $(id, t).addEventListener("click", (e) => { const btn = e.target.closest("button"); if (!btn) return;
            MX.$$("button", $(id, t)).forEach((x) => x.classList.toggle("on", x === btn)); set(btn.dataset[attr]); draw(); });
          seg("#rng", "n", (v) => (n = +v)); seg("#mode", "m", (v) => (mode = v)); seg("#ivl", "i", (v) => (ivl = v));
          $("#wsv", t).addEventListener("click", () => MX.saveChart({ symbol: i.symbol, timeframe: "daily", days: Math.min(n, 2000), source: "stock chart" }));
          draw(); window.addEventListener("mx-resize", draw);
        } else if (k === 1) {
          const q = d.results.quarters || [], nx = d.results.next;
          if (!q.length) { t.innerHTML = card("Quarterly results", `<div class="empty">No results read for this stock yet. <a href="#/screener">Screener → Results</a> → Download history.${nx ? `<br>Next results meeting: ${day(nx.date, { weekday: "short", day: "numeric", month: "short" })}` : ""}</div>`); return; }
          t.innerHTML = (nx ? note(`Next results meeting: ${day(nx.date, { weekday: "long", day: "numeric", month: "long" })}`) : "") +
            `<div class="card section"><div class="ct"><h2>Revenue and profit</h2><span class="aside">₹ crore · bars revenue, line profit</span></div><div id="rc"></div></div><div class="section card flush" id="rt"></div>
            <p class="muted small">Surprise = change vs the same quarter last year, scaled by how much that change usually varies (company's own history, not analyst estimates). Blank = not comparable.</p>`;
          const h = [...q].reverse();
          MXCharts.barLine($("#rc", t), h.map((x) => x.quarter), h.map((x) => x.revenue_cr), h.map((x) => x.profit_cr));
          $("#rt", t).appendChild(dataTable(q, [["quarter", "Quarter"], ["revenue_cr", "Revenue ₹ cr", "num0"], ["revenue_yoy", "YoY", "spct1"], ["profit_cr", "Profit ₹ cr", "num1"], ["profit_yoy", "YoY", "spct1"],
            ["margin_pct", "Net margin", "pct1"], ["sue_profit", "Surprise", "snum1"], ["react_rel", "Reaction vs Nifty", "spct1"], ["filed_at", "Filed", "dt"], ["basis", "Basis"]], { tools: false }));
        } else if (k === 2) {
          const h = d.holdings || [];
          if (!h.length) { t.innerHTML = card("Shareholding", `<div class="empty">No shareholding stored for this stock yet. Data health → History downloads → Shareholding patterns.</div>`); return; }
          const pc = (v) => (v == null ? "—" : `${num(v, 2)}%`);
          const chg = (a, b) => (a == null || b == null ? "" : ` <span class="${cls(a - b)} small">${a - b > 0 ? "+" : ""}${num(a - b, 2)}</span>`);
          const rows = h.map((r, i) => ({ ...r, prev: h[i + 1] }));
          t.innerHTML = card("Shareholding by quarter", table(rows, [["quarter", "Quarter"], ["promoter", "Promoter", (v, r) => pc(v) + chg(v, r.prev?.promoter), true],
            ["fii", "Foreign inst.", (v, r) => pc(v) + chg(v, r.prev?.fii), true], ["dii", "Domestic inst.", (v, r) => pc(v) + chg(v, r.prev?.dii), true],
            ["mf", "Mutual funds", (v, r) => pc(v) + chg(v, r.prev?.mf), true], ["retail", "Retail (< ₹2 lakh)", (v) => pc(v), true],
            ["holders", "Shareholders", (v) => (v == null ? "—" : num(v, 0)), true], ["published", "Published", "dt"]]),
            "small numbers = change vs the quarter before, in percentage points") +
            `<p class="muted small">From NSE shareholding filings. Promoter and public go back to listing; the foreign / domestic / mutual fund / retail split is read for the latest quarters only.</p>`;
          const ch = h.slice().reverse();
          if (ch.length > 1) {
            t.insertAdjacentHTML("beforeend", `<div class="card section"><div class="ct"><h2>Promoter and institutional holding</h2></div><div class="ch"></div></div>`);
            MXCharts.draw($(".ch", t), { type: "line", height: 260, ysuffix: "%", x: ch.map((r) => String(r.period_end).slice(0, 10)),
              series: [{ name: "Promoter", y: ch.map((r) => r.promoter) }, { name: "Foreign inst.", y: ch.map((r) => r.fii) }, { name: "Domestic inst.", y: ch.map((r) => r.dii) }] });
          }
        } else if (k === 3) {
          const f = d.fo;
          if (!f) { t.innerHTML = card("F&O", `<div class="empty">No F&O data for this stock: either it isn't in the F&O segment, or the F&O history isn't downloaded yet (Data health → History downloads).</div>`); return; }
          t.innerHTML = `<div class="grid g4">${kpi("Futures open interest", `${num(f.fut_oi / 1e5, 1)} lakh shares`, `${f.oi_5d != null ? pct(f.oi_5d, 1) : "—"} over 5 sessions`)}
            ${kpi("Put-call ratio (open interest)", f.pcr != null ? num(f.pcr, 2) : "—", "above 1 = more puts open than calls")}
            ${kpi("Futures basis", f.basis_pct != null ? pct(f.basis_pct, 2) : "—", "nearest future vs the share price")}
            ${kpi("Price and OI, 5 sessions", esc(f.reading || "—"), f.price_5d != null ? `price ${pct(f.price_5d, 1)}` : "")}</div>
            <div class="card section"><div class="ct"><h2>Futures open interest (lakh shares) and share price</h2><span class="aside">${day(f.date)}</span></div><div class="ch"></div></div>
            <div class="card section"><div class="ct"><h2>Put-call ratio of open interest</h2></div><div class="ch2"></div></div>
            <p class="muted small">"Long build-up" = price and open interest both up; "short build-up" = price down, OI up; "short covering" = price up, OI down; "long unwinding" = both down. These are common readings, not tested signals.</p>`;
          MXCharts.draw($(".ch", t), { type: "line", height: 260, x: f.x, series: [{ name: "Futures OI (lakh)", y: f.oi }, { name: "Close ₹", y: f.close_series, axis: "right" }] });
          MXCharts.draw($(".ch2", t), { type: "line", height: 200, x: f.x, hlines: [1], series: [{ name: "PCR", y: f.pcr_series }] });
        } else if (k === 4) {
          t.innerHTML = card("News & filings", newsList(d.news.map((n) => ({ ...n, source: n.kind === "filing" ? "NSE filing" : n.source })), "No news or filings stored for this stock."), "labels only where the headline shows them");
        } else {
          const sc = d.screener;
          t.innerHTML = `<div class="grid g2">${card("Screener", sc ? `<div><b>${sc.qualifies ? "On the shortlist" : "Not on the shortlist"}</b>${sc.rank ? ` · rank ${num(sc.rank, 0)}` : ""} · ${day(sc.date)}</div><p class="muted small">${esc(sc.reasons || "")}</p>` : `<div class="empty">Not screened yet.</div>`)}
            ${card("Signals from the strategies", table(d.signals, [["date", "Date", "date"], ["strategy", "Strategy"], ["approved", "Risk agent", (v, r) => v === true ? `<span class="up">approved</span>` : v === false ? `<span class="down">rejected</span> <span class="co">${esc(r.reject_reason || "")}</span>` : "—"]], "No signals on this stock recently."))}</div>`;
        }
      }, "stock");
    },
  });
})();
