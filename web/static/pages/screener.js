/* Screener: technical setups, market movers, daily moves, results, news, NSE announcements, agents' shortlist. */
(function () {
  const { api, post, esc, num, day, dt, $, $$, card, head, note, form, tabs, sections, newsList, startJob, dataTable, qs, spinner, download } = MX;
  let LISTS = null;
  const lists = async () => (LISTS = LISTS || (await api("meta/lists")));

  MX.page("screener", {
    title: "Screener",
    async render(el) {
      el.innerHTML = head("Screener", "Research lists, not buy recommendations — the agents still apply their own regime, entry, risk and exit rules.") + `<div id="sct"></div>`;
      tabs($("#sct", el), ["Technical setups", "Market movers", "Daily moves", "Results", "News", "NSE announcements", "Agents' shortlist"], async (i, b) => {
        await [setupsTab, moversTab, dailyTab, resultsTab, newsTab, annTab, shortlistTab][i](b);
      }, "screener");
    },
  });

  // ---------- technical setups ----------
  async function setupsTab(b) {
    const S = await api("screener/schema");
    b.innerHTML = `<div class="panel"><div class="pt">1 · What to screen</div><div id="s1"></div></div>
      <div class="panel"><div class="pt">2 · Must pass: hard filters</div><div id="s2"></div></div>
      <div class="panel"><div class="pt">3 · Look for: technical setups</div><div id="s3on"></div><div id="s3"></div></div>
      <div class="panel"><div class="pt">4 · Qualify, rank and show</div><div id="s4"></div><div class="row" style="margin-top:14px"><button class="btn primary" id="srun">Run screen</button><span class="muted small" id="smsg"></span></div></div>
      <div id="sres"></div>`;
    let base = S.default, F2, F3on, F3 = {}, F4;
    const strat = Object.keys(S.strategies);
    const f1 = form($("#s1", b), [
      { k: "src", label: "Start from", type: "select", options: [["", "Screener defaults"], ...strat.map((k) => [k, `${k} · ${S.strategies[k].status}`])], value: "" },
      { k: "date", label: "Screen date", type: "date", value: String(S.last).slice(0, 10), min: String(S.first).slice(0, 10), max: String(S.last).slice(0, 10) },
      { k: "universe", label: "Universe", type: "radio", options: [["all", "All downloaded"], ["nifty500_today", "Today's Nifty 500"], ["pit_top", "Point-in-time top N"]], value: base.universe }],
      () => { const src = f1.values().src; base = src ? S.strategies[src].config : S.default; build(); });
    const build = () => {
      const fb = base.filters;
      F2 = form($("#s2", b), [
        { k: "min_close", label: "Price at least ₹", type: "number", value: fb.min_close, min: 0, step: 5 },
        { k: "min_history", label: "History at least (sessions)", type: "number", value: fb.min_history, min: 20, help: "252 ≈ one year of trading" },
        { k: "min_median_value_cr", label: "Daily traded value at least ₹ cr", type: "number", value: fb.min_median_value_cr, step: 0.5, help: "Median over the previous 20 sessions" },
        { k: "min_valid_ratio_60d", label: "Traded on at least (% of last 60)", type: "number", value: Math.round(100 * fb.min_valid_ratio_60d), min: 0, max: 100 },
        { k: "exclude_etf", label: "Leave out ETFs", type: "toggle", value: fb.exclude_etf },
        { k: "exclude_data_errors", label: "Leave out stocks with data errors", type: "toggle", value: fb.exclude_data_errors }]);
      const names = Object.keys(S.setups);
      F3on = form($("#s3on", b), names.map((n) => ({ k: n, label: S.setup_label[n], type: "toggle", value: base.setups[n].enabled, help: S.descriptions[n] })));
      const host = $("#s3", b); host.innerHTML = `<div class="tabs" id="s3t">${names.map((n, i) => `<button data-i="${i}" class="${i ? "" : "on"}">${esc(S.setup_label[n])}</button>`).join("")}</div>` +
        names.map((n, i) => `<div class="s3p" data-i="${i}" ${i ? "hidden" : ""}><p class="muted small">${esc(S.descriptions[n])}</p><div id="s3-${n}"></div></div>`).join("");
      names.forEach((n) => {
        const fields = Object.entries(S.setups[n]).filter(([k]) => k !== "enabled").map(([k, sp]) => {
          const v = base.setups[n][k];
          if (sp.type === "bool") return { k, label: sp.label, type: "toggle", value: v, help: sp.help };
          if (Array.isArray(sp.lo)) return { k, label: sp.label, type: "select", options: sp.lo, value: v, help: sp.help };
          return { k, label: sp.label, type: "number", value: v, min: sp.lo, max: sp.hi, step: sp.type === "int" ? 1 : 0.5, help: sp.help };
        });
        F3[n] = form($(`#s3-${n}`, host), fields);
      });
      $("#s3t", host).addEventListener("click", (e) => { const t = e.target.closest("button"); if (!t) return; $$("#s3t button", host).forEach((x) => x.classList.toggle("on", x === t));
        $$(".s3p", host).forEach((p) => (p.hidden = p.dataset.i !== t.dataset.i)); });
      F4 = form($("#s4", b), [
        { k: "combine", label: "A stock qualifies when", type: "select", options: [["any", "any switched-on setup passes"], ["min_k", "at least k setups pass"], ["all", "every switched-on setup passes"]], value: base.combine },
        { k: "min_setups", label: "k", type: "number", value: base.min_setups, min: 1, max: 5, narrow: true },
        { k: "method", label: "Rank qualifiers by", type: "select", options: Object.entries(S.ranking), value: base.ranking.method },
        { k: "mode", label: "Show", type: "radio", options: [["top_n", "the top N"], ["all", "every qualifier"]], value: base.mode },
        { k: "top_n", label: "N", type: "number", value: base.top_n, min: 1, max: 1000, narrow: true },
        ...Object.entries({ rs_126: "6-month strength", rs_63: "3-month strength", rel_volume: "relative volume", adx: "trend strength", trend_slope: "SMA50 slope" })
          .map(([k, l]) => ({ k: `w_${k}`, label: `Weight: ${l}`, type: "number", value: base.ranking.weights[k] ?? 0, step: 0.1, min: 0, max: 10, help: "Used only with the weighted ranking" }))]);
    };
    build();
    $("#srun", b).addEventListener("click", async () => {
      const v1 = f1.values(), v2 = F2.values(), on = F3on.values(), v4 = F4.values();
      const setups = {}; Object.keys(S.setups).forEach((n) => (setups[n] = { enabled: on[n], ...F3[n].values() }));
      const cfg = { universe: v1.universe, filters: { ...v2, min_valid_ratio_60d: v2.min_valid_ratio_60d / 100 }, setups, combine: v4.combine, min_setups: v4.min_setups,
        ranking: { method: v4.method, weights: Object.fromEntries(Object.keys(base.ranking.weights).map((k) => [k, v4[`w_${k}`] ?? 0])) }, mode: v4.mode, top_n: v4.top_n };
      $("#sres", b).innerHTML = spinner("Screening (the first run prepares indicators for every stock, about 15 seconds)");
      try { showRun(await post("screener/run", { config: cfg, date: v1.date }), $("#sres", b)); } catch (e) { $("#sres", b).innerHTML = note(esc(e.message), "warn"); }
    });
  }
  function showRun(r, host) {
    const m = r.meta;
    host.innerHTML = `<div class="section">${head(`${r.shortlist.length || 0} stocks on ${day(m.screen_date)}`, `Config ${esc(m.config_id)} · screener v${esc(m.version)} · data to ${esc(m.data_last)} · ${esc(m.ranking)} · ${esc(m.universe_note)}`,
      `<button class="btn" id="dl1">Shortlist CSV</button><button class="btn" id="dl2">Every stock with reasons</button><button class="btn" id="dl3">Settings JSON</button>`)}</div>
      <div class="grid g3"><div class="card flush" id="fun"></div><div class="span2 card flush" id="sl"></div></div>
      <div class="section card"><div class="ct"><h2>Why a stock is in or out</h2></div><div id="wf"></div><div id="wd"></div></div>`;
    $("#fun", host).appendChild(dataTable(r.funnel, [["step", "Step"], ["stocks", "Stocks", "int"]], { tools: false }));
    if (r.shortlist.length) $("#sl", host).appendChild(dataTable(r.shortlist, [["rank", "#", "int"], ["symbol", "Stock", "sym"], ["sector", "Sector"], ["setups_passed", "Setups", "int"],
      ["qualification_reasons", "Why", "long"], ["close", "Close", "inr"], ["ranking_score", "Score", "num1"], ["rsi14", "RSI", "num0"], ["adx14", "ADX", "num0"],
      ["relative_volume_20d", "Vol ×", "x1"], ["return_126d", "6m return", "spct1"], ["data_quality_flags", "Data flags"]], { csv: "shortlist", height: 520 }));
    else $("#sl", host).innerHTML = note("No stock qualifies with these settings. Nothing was relaxed: the counts show where stocks drop out.") + (r.rejections.length ? MX.table(r.rejections, [["reason", "Reason"], ["stocks", "Stocks", "int"]]) : "");
    $("#dl1", host).addEventListener("click", () => MX.csv(r.shortlist, null, `shortlist_${m.screen_date}`));
    $("#dl2", host).addEventListener("click", () => download(r.all_csv, `screen_all_${m.screen_date}.csv`, "text/csv"));
    $("#dl3", host).addEventListener("click", () => download(JSON.stringify(r.config, null, 2), `screen_${m.config_id}.json`, "application/json"));
    const f = form($("#wf", host), [{ k: "symbol", label: "Stock", type: "select", options: r.symbols, value: r.symbols[0] }], () => loadDetail());
    const loadDetail = async () => {
      const d = await api(`screener/detail?symbol=${encodeURIComponent(f.values().symbol)}`);
      const mark = (ok, on = true) => ok ? `<span class="up">✓</span>` : on ? `<span class="down">✕</span>` : `<span class="muted">○</span>`;
      $("#wd", host).innerHTML = `<div class="grid g3 section">
        <div><h3>Filters</h3><div class="list">${d.filters.map((x) => `<div>${mark(x.ok)} ${esc(x.name)}</div>`).join("")}</div>${d.rejection ? `<p class="muted small">Out because: ${esc(d.rejection)}</p>` : ""}${d.flags ? note(`Data: ${esc(d.flags)}`, "warn") : ""}</div>
        <div><h3>Setups</h3><div class="list">${d.setups.map((x) => `<div>${mark(x.ok, x.on)} ${esc(x.name)}${x.on ? "" : " <span class='muted'>(off)</span>"}</div>`).join("")}</div>
          <p class="muted small">${esc(d.qualification || "")}</p>${d.qualified ? `<span class="tag pos">In the shortlist at rank ${d.rank}</span>` : d.qualifies ? `<span class="tag">Qualifies, ranked ${d.rank}</span>` : ""}
          <p><a href="#/stock/${encodeURIComponent(d.symbol)}">Open ${esc(d.symbol)}</a></p></div>
        <div><h3>Values used</h3><dl class="kv">${d.values.map((x) => `<dt>${esc(x.name)}</dt><dd>${x.value == null ? "—" : num(x.value, 2)}</dd>`).join("")}</dl></div></div>`;
    };
    if (r.symbols.length) loadDetail();
  }

  // ---------- market movers ----------
  async function moversTab(b) {
    const L = await lists(), first = await api("screener/movers?limit=1");
    b.innerHTML = `<div class="panel"><div id="mf"></div></div><div id="ms"></div>`;
    const f = form($("#mf", b), [
      { k: "preset", label: "Scan", type: "select", options: first.presets, value: "Top gainers" },
      { k: "date", label: "Date", type: "date", value: String(first.day).slice(0, 10) },
      { k: "limit", label: "Show up to", type: "number", value: 100, min: 10, max: 2000, step: 10 },
      { k: "min_price", label: "Price at least ₹", type: "number", value: 20, step: 5 },
      { k: "min_turnover", label: "Traded value at least ₹ cr/day", type: "number", value: 2, step: 1, help: "Median of the previous 20 sessions" },
      { k: "only", label: "Limit to", type: "radio", options: [["all", "All stocks"], ["nifty500", "Nifty 500"], ["shortlist", "Shortlist"]], value: "all" },
      { k: "sectors", label: "Sectors", type: "multi", options: L.sectors, wide: true }], () => load());
    const load = async () => { $("#ms", b).innerHTML = spinner("Scanning"); const d = await api(`screener/movers?${qs(f.values())}`); sections($("#ms", b), d.sections); };
    await load();
  }

  // ---------- daily moves ----------
  async function dailyTab(b) {
    b.innerHTML = `<div class="panel"><div id="df"></div></div><div id="ds"></div>`;
    const BK = [">+5%", "+2 to +5%", "+1 to +2%", "-1 to +1%", "-2 to -1%", "-5 to -2%", "< -5%"];
    const f = form($("#df", b), [
      { k: "span", label: "History", type: "radio", options: ["1M", "3M", "1Y", "3Y", "All"], value: "3M" },
      { k: "thr", label: "Move bigger than", type: "radio", options: [[1, "1%"], [2, "2%"], [5, "5%"]], value: 2 },
      { k: "liq", label: "Stocks", type: "select", options: [[0, "All in the data"], [5, "Traded ≥ ₹5 cr/day"], [20, "Traded ≥ ₹20 cr/day"]], value: 0 },
      { k: "vs", label: "Measure vs Nifty", type: "toggle" },
      { k: "day", label: "Day", type: "date", value: "" },
      { k: "buckets", label: "Show moves", type: "multi", options: BK, value: [BK[0], BK[1], BK[5], BK[6]] },
      { k: "group", label: "Also group by industry", type: "toggle" }], () => load());
    const load = async () => { $("#ds", b).innerHTML = spinner("Working out every stock's move"); const v = f.values();
      const d = await api(`screener/daily?${qs({ ...v, vs: v.vs ? "true" : "", group: v.group ? "true" : "", buckets: v.buckets.join("|") })}`); sections($("#ds", b), d.sections); };
    await load();
  }

  // ---------- results ----------
  async function resultsTab(b) {
    const L = await lists();
    b.innerHTML = `<div class="panel"><div class="row"><button class="btn" id="rupd">Update results</button><button class="btn" id="rbf">Download history</button><span class="muted small" id="rstat"></span></div></div><div id="rt"></div>`;
    $("#rupd", b).addEventListener("click", () => startJob("fundamentals"));
    $("#rbf", b).addEventListener("click", () => startJob("fundamentals-backfill"));
    tabs($("#rt", b), ["Latest results", "Coming up", "Does surprise move prices?", "Coverage"], async (i, t) => {
      if (i === 0) {
        t.innerHTML = `<div class="panel"><div id="rf"></div></div><div id="rs"></div>`;
        const f = form($("#rf", t), [
          { k: "days", label: "Filed in the last", type: "select", options: [[3, "3 days"], [7, "7 days"], [15, "15 days"], [30, "30 days"], [60, "60 days"], [90, "90 days"]], value: 30 },
          { k: "sue_min", label: "Profit surprise at least", type: "number", value: "", step: 0.5, placeholder: "any" },
          { k: "shortlist", label: "Only the agents' shortlist", type: "toggle" },
          { k: "sectors", label: "Sectors", type: "multi", options: L.sectors, wide: true }], () => load());
        const load = async () => { const v = f.values(); const d = await api(`screener/results?${qs({ ...v, shortlist: v.shortlist ? "true" : "" })}`);
          $("#rstat", b).textContent = d.status; sections($("#rs", t), d.sections); };
        await load();
      } else {
        const d = await api(`screener/results?view=${["", "calendar", "drift", "coverage"][i]}`); $("#rstat", b).textContent = d.status; sections(t, d.sections);
      }
    }, "results");
  }

  // ---------- news ----------
  async function newsTab(b) {
    const L = await lists(), first = await api("screener/news?hours=24&limit=1");
    b.innerHTML = `<div class="panel"><div class="row" style="margin-bottom:10px"><button class="btn" id="nref">Refresh news</button><span class="muted small">${first.when ? `Last fetched ${esc(String(first.when).slice(0, 16))}` : "No news downloaded yet."}${first.errors && first.errors.length ? ` · not working: ${esc(first.errors.join(", "))}` : ""}</span></div><div id="nf"></div></div><div class="grid g3"><div class="span2" id="nl"></div><div id="nst"></div></div>`;
    $("#nref", b).addEventListener("click", () => startJob("news"));
    const f = form($("#nf", b), [
      { k: "hours", label: "From the last", type: "select", options: [[6, "6 hours"], [24, "24 hours"], [72, "3 days"], [168, "7 days"], [720, "30 days"], [131400, "all stored"]], value: 24 },
      { k: "kw", label: "Words (comma-separated, any)", type: "text", placeholder: "results, order, RBI, crude" },
      { k: "types", label: "Type", type: "multi", options: first.types || [], help: "Set only when the headline contains an explicit phrase" },
      { k: "tones", label: "Tone", type: "multi", options: ["positive", "negative", "(no tone)"] },
      { k: "symbols", label: "Stocks", type: "multi", options: L.symbols },
      { k: "sectors", label: "Sectors", type: "multi", options: L.sectors },
      { k: "sources", label: "Sources", type: "multi", options: first.sources || [] },
      { k: "tagged", label: "Only headlines naming a stock", type: "toggle" },
      { k: "no_roundups", label: "Hide market round-ups", type: "toggle", value: true },
      { k: "shortlist", label: "Only the agents' shortlist", type: "toggle" }], () => load());
    const load = async () => {
      const v = f.values();
      const d = await api(`screener/news?${qs({ hours: v.hours, kw: v.kw, types: v.types.join("|"), tones: v.tones.join(","), symbols: v.symbols.join(","), sectors: v.sectors.join("|"),
        sources: v.sources.join("|"), tagged: v.tagged ? "true" : "", no_roundups: v.no_roundups ? "true" : "false", shortlist: v.shortlist ? "true" : "" })}`);
      $("#nl", b).innerHTML = card(`${d.matched} headlines`, newsList(d.items, "No headlines match."), `of ${d.total} downloaded · labels only where the headline shows them`);
      $("#nst", b).innerHTML = `<div class="card flush"><div class="ct" style="padding:14px 16px 0"><h2>Stocks in these headlines</h2></div><div id="nsth"></div></div>`;
      $("#nsth", b).appendChild(dataTable(d.stocks, [["symbol", "Stock", "symonly"], ["items", "Items", "int"], ["tone_pos_neg", "+ / −"], ["change_1d_pct", "Last day", "spct2"]], { tools: false, empty: "No stock named." }));
    };
    await load();
  }

  // ---------- announcements ----------
  async function annTab(b) {
    const L = await lists(), first = await api("screener/announcements?days=7");
    const i = first.info;
    b.innerHTML = `<div class="panel"><div class="row" style="margin-bottom:10px"><button class="btn" id="aref">Fetch announcements</button><span class="muted small">${i ? `Last fetch ${esc(i.when)}: ${i.fetched} read, ${i.added} new, ${i.total} kept` : "Nothing downloaded yet."}</span></div>${(i && i.errors || []).map((e) => note(esc(e), "warn")).join("")}<div id="af"></div></div>
      <div class="grid g3"><div class="span2" id="al"></div><div id="ast"></div></div>`;
    $("#aref", b).addEventListener("click", () => startJob("announcements"));
    const deflt = ["Results", "Order / contract", "Acquisition / merger", "Fund raising", "Dividend", "Bonus / split", "Buyback", "Credit rating", "Legal / regulatory"];
    const f = form($("#af", b), [
      { k: "days", label: "From the last", type: "select", options: [[1, "1 day"], [3, "3 days"], [7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "1 year"]], value: 7 },
      { k: "kw", label: "Words in the filing", type: "text", placeholder: "crore, NHAI, defence" },
      { k: "cats", label: "Type", type: "multi", options: first.categories || [], value: deflt.filter((x) => (first.categories || []).includes(x)) },
      { k: "symbols", label: "Stocks", type: "multi", options: L.symbols },
      { k: "sectors", label: "Sectors", type: "multi", options: L.sectors },
      { k: "n500", label: "Only today's Nifty 500", type: "toggle", value: true },
      { k: "shortlist", label: "Only the agents' shortlist", type: "toggle" }], () => load());
    const load = async () => {
      const v = f.values();
      const d = await api(`screener/announcements?${qs({ days: v.days, kw: v.kw, cats: v.cats.join("|"), symbols: v.symbols.join(","), sectors: v.sectors.join("|"), n500: v.n500 ? "true" : "false", shortlist: v.shortlist ? "true" : "" })}`);
      $("#al", b).innerHTML = `<div class="card flush" id="alc"></div><p class="muted small">Types come from keyword rules on the filing text: they say what was filed, not whether it is good news.</p>`;
      $("#alc", b).appendChild(dataTable(d.rows, [["published", "Time", "dt"], ["symbol", "Stock", "symonly"], ["category", "Type", "tag"], ["subject", "Subject"], ["text", "Filing", "long"], ["link", "PDF", "link"]], { csv: "nse_announcements", empty: "No filings match." }));
      $("#ast", b).innerHTML = `<div class="card flush"><div class="ct" style="padding:14px 16px 0"><h2>Stocks with filings</h2></div><div id="asth"></div></div>`;
      $("#asth", b).appendChild(dataTable(d.stocks, [["symbol", "Stock", "symonly"], ["items", "Filings", "int"], ["categories", "Types"], ["change_1d_pct", "Last day", "spct2"]], { tools: false, empty: "None." }));
    };
    await load();
  }

  // ---------- shortlist ----------
  async function shortlistTab(b) {
    const d = await api("screener/shortlist");
    if (d.empty) { b.innerHTML = note("The screener runs with the agents (Run all agents). Nothing saved yet."); return; }
    b.innerHTML = `<p class="muted small">Screen date ${day(d.date)} · screener v${esc(d.version)} · data ${esc(d.data)}</p>` + d.groups.map((g, i) => `<div class="section card flush"><div class="ct" style="padding:14px 16px 0"><h2>${esc(g.name)}</h2><span class="aside">${g.rows.length} stocks</span></div><div id="sg${i}"></div></div>`).join("") +
      (d.warnings && d.warnings.length ? `<p class="muted small">Data warnings on these stocks: ${esc(d.warnings.join(", "))}</p>` : "");
    d.groups.forEach((g, i) => $(`#sg${i}`, b).appendChild(dataTable(g.rows, [["rank", "#", "int"], ["symbol", "Stock", "symonly"], ["sector", "Sector"], ["setups_passed", "Setups", "int"],
      ["qualification_reasons", "Why", "long"], ["close", "Close", "inr"], ["rsi14", "RSI", "num0"], ["adx14", "ADX", "num0"], ["rel_volume_20d", "Vol ×", "x1"], ["return_126d", "6m", "spct1"], ["data_quality_flags", "Data flags"]], { csv: `shortlist_${g.name}` })));
  }
})();
