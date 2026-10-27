/* Overview (#/overview): the daily loop on one page.
   1 Night: is tonight's data and radar ready?  2 Morning: tonight's picks → Stock Desk.  3 Day: Live terminal.
   4 Evening: the last graded day (Tally). Retro (#/retro): every older feature, untouched, one tile each. */
(function () {
  const { api, esc, num, day, $, $$, note, spinner, startJob } = MX;
  const F = (v, o = { weekday: "short", day: "numeric", month: "short" }) => v ? day(v, o) : "—";
  const P = (x) => x == null ? "—" : `${num(100 * x, 0)}%`;
  const LEAN = (l) => l === "up" ? `<span class="up">▲</span>` : l === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
  const step = (n, title, sub, body, cls = "") => `<section class="loop-step ${cls}"><div class="loop-n">${n}</div><div class="loop-b"><div class="loop-h"><h2>${title}</h2><span class="muted small">${sub}</span></div>${body}</div></section>`;

  async function overview(el) {
    el.innerHTML = MX.head("Overview", "The daily loop: data in at night → picks in the morning → live in the day → tally in the evening.") + `<div class="loop" id="loop">${spinner("Loading …")}</div>`;
    const [st, asof, rd, lv, ty] = await Promise.all([api("status").catch(() => null), api("marketlab/asof").catch(() => null),
      api("marketlab/radar").catch(() => null), api("live/state").catch(() => null), api("tally").catch(() => null)]);
    const host = $("#loop", el); if (!host) return;
    // 1 Night
    const running = (st && st.running) || [], stale = asof && asof.stale;
    const night = `<div class="loop-kv"><div title="Daily bars from Angel One (checked against NSE's bhavcopy); last downloaded ${esc((st && st.last_download) || "—")}"><span class="k">Prices</span><b>${F(st && st.last_bar)}</b></div>
        <div title="Market Lab radar built from that day's close, for the next session"><span class="k">Radar</span><b>${asof && asof.day ? `${F(asof.day)} → ${F(asof.for_day)}` : "not built"}</b></div>
        <div><span class="k">Jobs</span><b>${running.length ? running.map((j) => esc(j.title)).join(", ") : "idle"}</b></div></div>
      ${stale ? note(`Prices are newer than the radar: run <b>Fetch + run all</b> (or Build tonight's radar) to bring it up to date.`, "warn") : ""}
      <div class="row" style="gap:6px"><button class="btn primary owner-act" id="ovfetch">Fetch + run all</button><a class="btn" href="#/data">Data health</a><a class="btn" href="#/coverage">Coverage</a></div>`;
    // 2 Morning
    const rows = rd && rd.ready ? rd.rows.slice(0, 10) : [];
    const SP = await MX.sparks(rows.map((r) => r.symbol));
    const picks = rows.length ? `<table class="tbl"><thead><tr><th>Stock</th><th>20 days</th><th>Chance ≥1% <span class="muted small">(tick = usual)</span></th><th class="r">Edge</th><th>Lean</th><th>Confidence</th><th>Biggest drivers</th></tr></thead><tbody>${rows.map((r) =>
        `<tr><td><a class="sym" href="#/desk/${encodeURIComponent(r.symbol)}" target="_blank" rel="noopener">${esc(r.symbol)}</a><div class="co">${esc(r.sector || "")}</div></td><td>${MX.spark(SP[r.symbol])}</td><td>${MX.chanceBar(r.chance, r.usual)}</td><td class="r">${MX.chg(100 * (r.chance - r.usual), 0, { pct: false, suffix: " pts" })}</td>
          <td>${MX.lean(r.lean)}</td><td>${MX.conf(r.confidence)}</td><td class="small">${esc(r.why || "")}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty-next"><b>No picks yet.</b> ${esc((rd && rd.why) || "")} <span class="muted">Next: run Fetch + run all (after 18:00), or Market Lab → Scorecard → Build tonight's radar.</span></div>`;
    const morning = picks + `<div class="row" style="gap:6px;margin-top:8px"><a class="btn" href="#/market-lab">Full Move Radar →</a><span class="muted small">top 10 by edge · close-to-close ≥1% · click a stock for its Stock Desk</span></div>`;
    // 3 Day
    const s = lv && lv.status, on = s && ["live", "replay", "reconnecting", "starting"].includes(s.state);
    const strip = (lv && lv.strip || []).slice(0, 4).map((x) => `<div><span class="k">${esc(x.label)}</span><b>${num(x.ltp, x.ltp > 1000 ? 0 : 2)}</b> ${MX.chg(x.chg, 2)}</div>`).join("");
    const dayb = `<div class="loop-kv"><div><span class="k">Feed</span><b>${s ? (on ? `<span class="up">● ${esc(s.state)}</span>` : esc(s.state)) : "—"}</b></div>
        <div><span class="k">Market</span><b>${s && s.market_open ? "open hours" : "closed"}</b></div>${strip}</div>
      ${lv && lv.alerts && lv.alerts.length ? `<div class="small muted">Latest alert: ${esc(lv.alerts[lv.alerts.length - 1].at)} ${esc(lv.alerts[lv.alerts.length - 1].text)}</div>` : ""}
      <div class="row" style="gap:6px;margin-top:8px"><a class="btn ${on ? "" : "primary"}" href="#/live">Live terminal →</a></div>`;
    // 4 Evening
    const k = ty && ty.ready ? ty.kpis : null;
    const evening = k ? `<div class="loop-kv"><div><span class="k">Projected</span><b>${F(ty.day)} → ${F(ty.for_day)}</b></div>
        <div><span class="k">Picks moved</span><b>${k.picks_hit}/${k.picks_done || k.picks}</b> <span class="muted small">said ${P(k.said)} · happened ${P(k.happened)}</span></div>
        <div><span class="k">Plans</span><b>${k.plans_entered ? MX.chg(k.plans_r, 1, { pct: false, suffix: " R" }) : "not graded"}</b></div>
        <div><span class="k">Paper</span><b class="${k.paper_rs > 0 ? "up" : k.paper_rs < 0 ? "down" : ""}">${k.paper_rs != null ? MX.money(k.paper_rs, { sign: true, short: true }) : k.paper ? "open" : "—"}</b></div></div>
      <div class="row" style="gap:6px;margin-top:8px"><a class="btn" href="#/tally">Tally →</a></div>` : `<div class="empty-next"><b>Nothing graded yet.</b> <span class="muted">A night is graded after the next session's prices download (Fetch + run all).</span></div>`;
    host.innerHTML = step(1, "Night · data in", "Fetch + run all after 18:00", night, stale ? "warn" : "") + step(2, "Morning · tonight's picks", rd && rd.day ? `radar of ${F(rd.day)}` : "", morning) +
      step(3, "Day · live", "09:15–15:30", dayb, on ? "live" : "") + step(4, "Evening · tally", "said vs happened", evening);
    $("#ovfetch", el)?.addEventListener("click", () => startJob("fetch+pipeline"));
  }

  const RETRO = [
    ["Old agents system", [["overview-retro", "Agents overview", "the previous Overview: regime, swing trades and exits from the agents"], ["agents", "Agents", "each agent's last run and output"],
      ["signals", "Signals", "the agents' signals"], ["strategies", "Strategies", "swing strategies and their tests"], ["validation", "Validation", "strategy validation"],
      ["positions", "Positions & risk", "positions, exposures, orders"], ["screener", "Screener", "setups shortlist, results, movers"]]],
    ["Market Lab classic", [["market-lab-retro", "Market Lab (all tabs)", "Stock analysis (influence map), Review, Debate tab"], ["stock", "Stock page", "the older single-stock page"],
      ["ta", "TA workspace", "full chart workspace with evidence and drawings"]]],
    ["Market & research", [["market", "Market", "breadth, sectors, movers, news, catalyst"], ["market-news", "Market news", "daily report, macro, news research"],
      ["search", "Search", "search news, filings, documents"], ["ask", "Ask", "chat with the local model over MarketX data"], ["documents", "Documents", "your document library"],
      ["records", "Reports & logs", "saved reports and job logs"]]],
    ["Labs", [["debate", "Debate", "bull vs bear with statistical checks"], ["scorecard", "Forecast scorecard", "Debate forecasts graded"], ["kronos", "Kronos", "experimental forecasting model"],
      ["lab", "Strategy Lab", "build and test your own rules"], ["screener-research", "Screener research", "screener experiments"]]],
  ];
  function retro(el) {
    el.innerHTML = MX.head("Retro", "Everything outside the daily loop: older systems, extra views and labs. All still work and the old agents still run every night.") +
      RETRO.map(([g, items]) => `<div class="section"><h3>${esc(g)}</h3><div class="retro-grid">${items.map(([k, t, d]) =>
        `<a class="retro-tile" href="#/${k}"><b>${esc(t)}</b><span>${esc(d)}</span></a>`).join("")}</div></div>`).join("");
  }

  MX.page("overview", { title: "Overview", reloadOnJob: true, render: overview });
  MX.page("retro", { title: "Retro", render: retro });
})();
