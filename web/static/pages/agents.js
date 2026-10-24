/* Agents: Agents, Signals, Strategies, Screener research, Validation; Portfolio: Positions & risk; Tools: Reports & logs. */
(function () {
  const { api, post, esc, num, pct, cls, inr, day, dt, $, $$, card, head, note, form, tabs, sections, startJob, dataTable, qs, spinner, md, kpi, openLog } = MX;

  const VERB = { BUY: "Buy", ADD: "Buy", SELL: "Sell", TRIM: "Sell" };
  function orderSentence(x) {
    const q = Math.abs(x.order_qty), v = MX.inr(Math.abs(x.order_value || 0), 0), sym = `<a class="sym" href="#/stock/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>`;
    const why = { BUY: "new pick this month", ADD: `top up: you hold ${x.held_qty}, target is ${x.target_qty}`, SELL: "dropped out of the top list: sell all",
                  TRIM: `cut back: you hold ${x.held_qty}, target is ${x.target_qty}` }[x.action] || x.reason;
    return `<b>${VERB[x.action] || x.action} ${num(q, 0)} share${q === 1 ? "" : "s"}</b> of ${sym} ≈ ${v} <span class="muted">(${esc(why)})</span>`;
  }
  function riskRows(R, a) {
    const P = (x) => `${num(100 * x, 0)}%`;
    return [
      ["Risk per trade", P(R.risk_per_trade), `If a trade hits its stop you lose at most ${MX.inr(R.risk_per_trade * a.equity, 0)}. Share count is sized so this holds.`],
      ["Most positions at once", String(R.max_positions), "No new trades once you hold this many."],
      ["Biggest single position", P(R.max_position_pct), `No one stock above ${MX.inr(R.max_position_pct * a.equity, 0)}.`],
      ["Most stocks per sector", String(R.max_per_sector), "Stops one sector news event hurting the whole account."],
      ["Daily loss stop", P(R.daily_loss_cap), "No new trades for the rest of the day after losing this much."],
      ["Cut size after a fall of", P(R.dd_half), "Below your peak by this much: new trades are half size."],
      ["Pause trading after a fall of", P(R.dd_stop), "Below your peak by this much: no new trades until you recover."],
    ];
  }

  const RUN = { fetch: "Fetch latest data", regime: "Run regime", screener: "Run screener", strategy: "Run strategy", technical: "Run technical", risk: "Run risk",
    validation: "Run validation", report: "Build report", backtest: "Run backtest + analysis", "macro-all": "Fetch macro data + study", announcements: "Fetch news and filings",
    "market-report": "Write the market report", "search-index": "Build the search index" };

  MX.page("agents", {
    title: "Agents", reloadOnJob: true,
    async render(el, arg) {
      const list = await api("agents");
      const cur = list.find((a) => a.key === arg) || list[0];
      el.innerHTML = head("Agents", "Each agent reads the previous agent's output. After new data, run them in order, or use Run all agents.",
        `<button class="btn" id="aall">Run all agents</button>`) +
        `<div class="tiles section">${list.map((a) => { const s = a.working ? "run" : a.extra ? a.extra.state : !a.last ? "idle" : a.last.status === "ok" ? "ok" : a.last.status === "error" ? "bad" : "warn";
          return `<a class="tile ${a.key === cur.key ? "run" : ""}" href="#/agents/${a.key}"><div class="n"><span class="dot ${s}"></span>${esc(a.name)}</div><div class="s">${a.working ? esc(a.working) : a.extra ? esc(a.extra.text) : a.last ? dt(a.last.started) : "never run"}</div></a>`; }).join("")}</div>
        <div class="section card"><div class="ct"><h2>${esc(cur.name)}</h2><span class="aside">${esc(cur.kind)}</span></div><p style="margin:0 0 6px">${esc(cur.what)}</p><p class="muted small"><code>${esc(cur.io)}</code></p>
          <div class="row">${cur.key === "debate" ? `<a class="btn primary" href="#/debate">Open Market Debate Lab</a>` : RUN[cur.key] ? `<button class="btn primary" id="arun">${RUN[cur.key]}</button>` : ""}
          ${cur.key === "strategy" ? `<button class="btn" id="ares">Run research</button>` : ""}</div>
          ${cur.last ? `<div class="grid g4 section">${kpi("Last status", esc(cur.last.status))}${kpi("Last run", dt(cur.last.started))}${kpi("Took", cur.last.seconds != null ? `${num(cur.last.seconds, 0)} s` : "—")}${kpi("Rows out", cur.last.rows_out != null ? num(cur.last.rows_out, 0) : "—")}</div>
            ${cur.last.message ? note(esc(cur.last.message), cur.last.status === "error" ? "warn" : "info") : ""}` : ""}</div><div id="aout"></div>`;
      $("#aall", el).addEventListener("click", () => startJob("pipeline"));
      $("#arun", el)?.addEventListener("click", () => startJob(cur.key));
      $("#ares", el)?.addEventListener("click", () => startJob("strategy-research"));
      const d = await api(`agent?key=${cur.key}`); sections($("#aout", el), d.sections);
    },
  });

  MX.page("signals", {
    title: "Signals", reloadOnJob: true,
    async render(el) {
      const first = await api("signals");
      el.innerHTML = head("Signals and decisions", "Every setup that fired, from every strategy (including those switched off today), and what the agents decided.") +
        (first.dates.length ? `<div class="panel"><div id="sf"></div></div>` : "") + `<div id="ss"></div>`;
      if (!first.dates.length) { sections($("#ss", el), first.sections); return; }
      const f = form($("#sf", el), [
        { k: "date", label: "Day", type: "select", options: first.dates.map((x) => [x, day(x, { weekday: "short", day: "numeric", month: "short", year: "numeric" })]), value: first.date },
        { k: "strategies", label: "Strategy", type: "multi", options: first.strategies },
        { k: "statuses", label: "Outcome", type: "multi", options: ["Approved", "Rejected by risk", "Not ranked", "Strategy off"] }], () => load());
      const load = async () => { const v = f.values(); const d = await api(`signals?${qs({ date: v.date, strategies: v.strategies.join(","), statuses: v.statuses.join("|") })}`); sections($("#ss", el), d.sections); };
      sections($("#ss", el), first.sections);
    },
  });

  MX.page("strategies", {
    title: "Strategies",
    async render(el) {
      el.innerHTML = head("Strategies", "Each strategy is a fixed buy/sell rule. Pick one on the left to read it in plain words; the tabs show how they all did over 10 years.",
        `<button class="btn" id="bt">Run backtest + analysis</button>`) + `<div class="split"><nav class="slist" id="sl"></nav><div><div id="sg" class="section"></div><div id="stt"></div></div></div>`;
      $("#bt", el).addEventListener("click", () => startJob("backtest"));
      const G = await api("strategies/guide").catch(() => ({ items: [] }));
      const fams = [...new Set(G.items.map((x) => x.family))];
      const showS = (id) => {
        const x = G.items.find((i) => i.id === id);
        $$("#sl a", el).forEach((a) => a.classList.toggle("on", a.dataset.id === id));
        if (!x) { $("#sg", el).innerHTML = note("Pick a strategy on the left to see what it buys, when it sells, why it might work and when it fails."); return; }
        const res = x.net_pct != null ? `<span class="${cls(x.net_pct)}"><b>${pct(x.net_pct, 2)}</b></span> per trade after costs over ${num(x.trades, 0)} trades${x.win_pct != null ? `, ${num(x.win_pct, 0)}% winners` : ""}` : esc(x.verdict || "Not backtested yet.");
        $("#sg", el).innerHTML = `<div class="card"><div class="ct"><h2>${esc(x.name)} <span class="code">${esc(x.id)}</span></h2><span class="tag">${esc(x.family)}</span><span class="tag ${/active|paper/.test(x.status) ? "acc" : ""}">${esc({ no_edge: "no edge found", paper: "paper trading", active: "live", research: "research only", "not registered": "not in use" }[x.status] || x.status)}</span></div>
          <dl class="kv"><dt>What</dt><dd>${esc(x.what)}</dd><dt>Buys when</dt><dd>${esc(x.how)}</dd><dt>Sells when</dt><dd>${esc(x.exit)}</dd>
          <dt>Why it might work</dt><dd>${esc(x.why)}</dd><dt>When it fails</dt><dd>${esc(x.fails)}</dd>
          <dt>Backtest</dt><dd>${res}${x.net_pct != null ? ` <span class="muted small">(cost per round trip ${num(G.cost_pct, 1)}%)</span>` : ""}</dd>
          ${x.works_in && x.works_in.length ? `<dt>Switched on in</dt><dd>${esc(x.works_in.join(", "))} markets</dd>` : x.net_pct != null ? `<dt>Switched on in</dt><dd class="muted">No market state: it didn't show an edge in any.</dd>` : ""}</dl></div>`;
        try { sessionStorage.setItem("mx-strat", id); } catch (e) { /* ignore */ }
      };
      $("#sl", el).innerHTML = fams.map((f) => `<div class="grp">${esc(f)}</div>` + G.items.filter((x) => x.family === f).map((x) =>
        `<a data-id="${esc(x.id)}"><span class="dot ${x.works_in && x.works_in.length || /active|paper/.test(x.status) ? "ok" : ""}"></span>${esc(x.name)}</a>`).join("")).join("") +
        `<p class="muted small" style="margin:10px 6px">Green dot = switched on in at least one market state, or being paper-traded.</p>`;
      $("#sl", el).addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) showS(a.dataset.id); });
      let s0 = null; try { s0 = sessionStorage.getItem("mx-strat"); } catch (e) { /* ignore */ }
      showS(s0);
      tabs($("#stt", el), ["Report", "Summary", "Playbook and rules", "Momentum research"], async (i, b) => {
        const d = await api(`strategies?tab=${["report", "summary", "playbook", "momentum"][i]}`);
        if (d.empty) { b.innerHTML = note("No analysis yet. Run Backtest + analysis."); return; }
        sections(b, d.sections);
      }, "strategies");
    },
  });

  MX.page("screener-research", {
    title: "Screener research",
    async render(el) {
      el.innerHTML = head("Screener research", "Does a screener rule actually pick winners? This page tests each rule on past data before you trust it.") +
        `<div class="steps section"><div><i>1</i><b>Pick rules</b>Each rule (e.g. breakout on high volume) is tried with a few settings.</div>
          <div><i>2</i><b>Build on 2016–20</b>Did stocks it picked beat the average stock over the next 5–20 days, after costs?</div>
          <div><i>3</i><b>Check on 2021–23</b>Same test on years it never saw. Passing both = <b style="display:inline">Passed</b>.</div>
          <div><i>4</i><b>Switch on</b>Only passed rules should feed the live screener (Rules tab). 2024+ is kept for a final check.</div></div><div id="srt"></div>`;
      tabs($("#srt", el), ["All results", "One result in detail", "Rules (switch on/off)", "Run tests"], async (i, b) => {
        if (i === 0) {
          b.innerHTML = `<div class="panel"><div id="cf"></div></div><div id="cs"></div>`;
          const f = form($("#cf", b), [{ k: "h", label: "Horizon (sessions)", type: "radio", options: [5, 10, 15, 20], value: 10 },
            { k: "hold", label: "Show holdout (each look is logged)", type: "toggle" }], () => load());
          const load = async () => { const v = f.values(); sections($("#cs", b), (await api(`screener-research?${qs({ tab: "compare", h: v.h, hold: v.hold ? "true" : "" })}`)).sections); };
          await load();
        } else if (i === 1) {
          const first = await api("screener-research?tab=detail");
          b.innerHTML = first.options.length ? `<div class="panel"><div id="df"></div></div><div id="ds"></div>` : `<div id="ds"></div>`;
          if (first.options.length) {
            const f = form($("#df", b), [{ k: "rk", label: "Configuration", type: "select", options: first.options, value: first.rk, wide: true }], () => load());
            const load = async () => sections($("#ds", b), (await api(`screener-research?${qs({ tab: "detail", rk: f.values().rk })}`)).sections);
          }
          sections($("#ds", b), first.sections);
        } else if (i === 2) {
          const d = await api("screener-research?tab=strategies");
          const ST = { experimental: "not passed yet", validated: "passed", enabled: "passed" };
          b.innerHTML = note("Passed = beat the average stock in both 2016–20 and 2021–23 after costs. On = used by the live screener today. You can switch on a rule that hasn't passed, but it stays labelled.") +
            d.strategies.map((s) => `<div class="card section"><div class="ct"><h2>${esc(s.description || s.key)} <span class="code">${esc(s.key)}</span></h2><span class="tag ${s.status === "validated" ? "pos" : ""}">${esc(ST[s.status] || s.status)}</span>${s.enabled ? `<span class="tag acc">on</span>` : ""}
              <span class="aside"><button class="btn small ${s.enabled ? "" : s.status === "validated" ? "primary" : ""}" data-k="${esc(s.key)}" data-on="${s.enabled ? "0" : "1"}">${s.enabled ? "Disable" : s.status === "validated" ? "Enable" : "Enable (unvalidated)"}</button></span></div>
              <p style="margin:0"><b>Why it might work:</b> ${esc(s.hypothesis || "—")}</p><p class="muted small">Judged over ${esc(s.horizon || "")} trading days · ${s.evaluate_on === "shortlist" ? "tests the top-50 ranking" : "tests every stock that qualifies"} · file ${esc(s.file || "")}</p></div>`).join("");
          $$("[data-k]", b).forEach((x) => x.addEventListener("click", async () => { await post("screener-research/enable", { key: x.dataset.k, on: x.dataset.on === "1" }, "Saved"); MX.render(); }));
        } else {
          const d = await api("screener-research?tab=experiments");
          b.innerHTML = `<div class="row section"><button class="btn primary" id="e1">Run all tests (skips finished)</button><button class="btn" id="e2">Re-run everything</button><button class="btn" id="e3">Run today's screen now</button></div><div id="es"></div>
            <p class="muted small">About 15 seconds per configuration. Results are kept per data version, so new prices mean a fresh run.</p>`;
          $("#e1", b).addEventListener("click", () => startJob("screener-experiments"));
          $("#e2", b).addEventListener("click", () => confirm("Re-run every configuration? Only needed after a code change.") && startJob("screener-experiments-force"));
          $("#e3", b).addEventListener("click", () => startJob("screener"));
          sections($("#es", b), d.sections);
        }
      }, "screener-research");
    },
  });

  MX.page("validation", {
    title: "Validation",
    async render(el) {
      el.innerHTML = head("Validation", "Paper-tracks every approved recommendation to its exit and compares live results with the backtest.", `<button class="btn" id="vr">Run validation</button>`) + `<div id="vs"></div>`;
      $("#vr", el).addEventListener("click", () => startJob("validation"));
      sections($("#vs", el), (await api("validation")).sections);
    },
  });

  // ---------- positions ----------
  MX.page("positions", {
    title: "Positions & risk",
    async render(el) {
      const d = await api("positions");
      const a = d.account, R = d.risk;
      const mean = (t) => `<div class="mean">${t}</div>`, ddp = 100 * d.drawdown;
      const ddState = ddp >= 100 * R.dd_stop ? "New trades are paused until you recover." : ddp >= 100 * R.dd_half ? "New trades are half size." : "Normal sizing.";
      el.innerHTML = head("Positions and risk", "Your holdings and the safety limits every new idea is checked against. MarketX never places orders: you do, then record them here.") +
        `<div class="grid g4">${kpi("Account value", inr(a.equity, 0), "", mean("What your trading account is worth now (from Account below)."))}
          ${kpi("Below your peak", `${num(ddp, 1)}%`, `half size at ${num(100 * R.dd_half, 0)}%, pause at ${num(100 * R.dd_stop, 0)}%`, mean(ddState))}
          ${kpi("Open positions", `${d.positions.length} of ${R.max_positions} max`, `${num(100 * d.deployed, 0)}% of money invested`, mean(`Room for ${Math.max(0, R.max_positions - d.positions.length)} more.`))}
          ${kpi("Today's profit/loss", inr(a.today_pnl, 0), `stop for the day at −${num(100 * R.daily_loss_cap, 0)}%`, mean(`No new trades today once losses reach ${inr(R.daily_loss_cap * a.equity, 0)}.`))}</div>
        <div class="section card"><div class="ct"><h2>Your positions</h2><span class="aside muted small">Add a row when you buy, delete it when you sell, then Save.</span><span class="aside"><button class="btn small" id="padd">Add row</button> <button class="btn small primary" id="psave">Save positions</button></span></div><div class="scroll"><table class="tbl edit" id="ptab"></table></div></div>
        <div class="section grid g2"><div id="plive"></div><div id="psec"></div></div>
        <div id="pord"></div>
        <div class="section grid g2"><div class="card"><div class="ct"><h2>Account</h2></div><div id="acf"></div><div class="row" style="margin-top:12px"><button class="btn" id="acs">Save account</button></div></div>
          <div class="card"><div class="ct"><h2>Risk limits</h2></div><dl class="kv">${riskRows(R, a).map(([k, v, m]) => `<dt>${esc(k)}</dt><dd><b>${esc(v)}</b><div class="mean">${esc(m)}</div></dd>`).join("")}</dl></div></div>`;
      let rows = d.positions.map((r) => ({ ...r }));
      const drawEdit = () => {
        const L = (window.MXHELP && MXHELP.LABELS) || {};
        $("#ptab", el).innerHTML = `<thead><tr>${d.columns.map((c) => `<th>${esc(L[c] || c.replace(/_/g, " "))}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map((r, i) => `<tr>${d.columns.map((c) =>
          c === "strategy" ? `<td><select class="in" data-r="${i}" data-c="${c}">${["", ...d.strategy_options].map((o) => `<option value="${esc(o)}" ${String(r[c]) === o ? "selected" : ""}>${esc(o ? (MX.nameOf(o) ? `${MX.nameOf(o)} (${o})` : o) : "")}</option>`).join("")}</select></td>`
            : `<td><input class="in" data-r="${i}" data-c="${c}" value="${esc(r[c] ?? "")}" placeholder="${c === "entry_date" ? "YYYY-MM-DD" : ""}"></td>`).join("")}<td><button class="btn small" data-del="${i}">✕</button></td></tr>`).join("")}</tbody>`;
        $$("[data-c]", el).forEach((x) => x.addEventListener("change", () => (rows[+x.dataset.r][x.dataset.c] = x.value)));
        $$("[data-del]", el).forEach((x) => x.addEventListener("click", () => { rows.splice(+x.dataset.del, 1); drawEdit(); }));
      };
      drawEdit();
      $("#padd", el).addEventListener("click", () => { rows.push(Object.fromEntries(d.columns.map((c) => [c, ""]))); drawEdit(); });
      $("#psave", el).addEventListener("click", async () => { await post("positions/save", { rows }, "Saved positions"); MX.render(); });
      if (d.live.length) {
        $("#plive", el).innerHTML = `<div class="card flush"><div class="ct" style="padding:14px 16px 0"><h2>Live view</h2></div><div id="plv"></div></div>`;
        $("#plv", el).appendChild(dataTable(d.live, [["symbol", "Stock", "symonly"], ["strategy", "Strategy", "strat"], ["entry_price", "Bought at", "inr"], ["last", "Now", "inr"], ["quantity", "Shares", "int"],
          ["value", "Cost ₹", "inr0"], ["pnl_rs", "Profit/loss ₹", "inr0"], ["pnl_pct", "Profit/loss", "spct2"], ["to_stop_pct", "Fall to stop", "spct2"]], { tools: false }));
        $("#psec", el).innerHTML = card("Positions per sector (limit stops one sector dominating)", MX.table(d.sectors, [["sector", "Sector"], ["positions", "Positions", "int"], ["value", "Value", "inr0"], ["limit", "Limit", "int"]]));
      }
      const af = form($("#acf", el), [{ k: "capital", label: "Capital", type: "number", value: a.capital, step: 10000 }, { k: "equity", label: "Equity now", type: "number", value: a.equity, step: 1000 },
        { k: "peak_equity", label: "Peak equity", type: "number", value: a.peak_equity, step: 1000 }, { k: "today_pnl", label: "Today's P&L", type: "number", value: a.today_pnl, step: 100 }]);
      $("#acs", el).addEventListener("click", async () => { await post("account/save", af.values(), "Saved account"); MX.render(); });
      for (const m of d.momentum) {
        const box = document.createElement("div"); box.className = "section card"; $("#pord", el).appendChild(box);
        const draw = async (list = "this") => {
          const o = await api(`orders?${qs({ strategy: m.strategy, list })}`);
          box.innerHTML = `<div class="ct"><h2>Orders: ${MX.named(m.strategy) || esc(m.strategy)}</h2><span class="tag">${esc(m.status === "paper" ? "paper trading (practice)" : m.status)}</span><span class="aside">${o.asof ? `targets from ${day(o.asof)} · ${o.due ? "<b>rebalance due now</b>" : `next rebalance ${day(o.next)}`}` : ""}</span></div>
            <div class="row"><div class="fld narrow" style="max-width:220px"><label>Money for this strategy ₹</label><input class="in" type="number" id="al" value="${o.allocation}" step="50000"></div>
            <div class="seg" id="ol"><button data-v="this" class="${list === "this" ? "on" : ""}">This month</button><button data-v="next" class="${list === "next" ? "on" : ""}">Next month (provisional)</button></div></div>
            <div id="oc"></div>`;
          $("#al", box).addEventListener("change", async (e) => { await post("orders/allocation", { strategy: m.strategy, amount: Number(e.target.value) }, "Saved"); draw(list); });
          $("#ol", box).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) draw(b.dataset.v); });
          const oc = $("#oc", box);
          if (o.empty) { oc.innerHTML = note("No targets yet: run the agents."); return; }
          if (o.needs_money) { oc.innerHTML = note("Enter the money you want in this strategy to get share quantities."); return; }
          const s = o.summary;
          const todo = o.orders.filter((x) => x.order_qty);
          oc.innerHTML = `<div class="grid g4 section">${kpi("Buy orders", s.buys, inr(s.buy_value, 0))}${kpi("Sell orders", s.sells, inr(s.sell_value, 0))}${kpi("Invested after", inr(s.invested_after, 0))}${kpi("Cash left", inr(s.cash_after, 0))}</div>
            <div class="card section"><div class="ct"><h2>${todo.length ? `What to do (${todo.length} order${todo.length > 1 ? "s" : ""})` : "Nothing to trade"}</h2><span class="aside muted small">Sells first, so the cash is there for the buys</span></div>
              ${todo.length ? `<ol class="sent">${todo.map((x) => `<li>${orderSentence(x)}</li>`).join("")}</ol>` : `<p class="muted">Every stock is at or near its target. No orders needed.</p>`}</div>
            <details class="fold section"><summary>Full order table</summary><div id="otab"></div></details>
            ${list === "this" ? `<div class="row" style="margin-top:12px"><button class="btn" id="oapply">I placed these orders: update my positions</button></div>` : ""}`;
          $("#otab", oc).appendChild(dataTable(o.orders, [["action", "Action", "tag"], ["symbol", "Stock", "symonly"], ["order_qty", "Shares to trade", "int"], ["last_price", "Last price", "inr"],
            ["order_value", "Order value", "inr0"], ["held_qty", "You hold", "int"], ["target_qty", "Should hold", "int"], ["target_weight_pct", "Share of money %", "num2"], ["reason", "Why", "long"]], { csv: `orders_${m.strategy}` }));
          $("#oapply", oc)?.addEventListener("click", async () => { if (!confirm("Set this strategy's rows in your positions to the target quantities at the last close?")) return;
            await post("orders/apply", { strategy: m.strategy }, "Positions updated"); MX.render(); });
        };
        draw();
      }
    },
  });

  // ---------- reports & logs ----------
  MX.page("records", {
    title: "Reports & logs",
    async render(el) {
      el.innerHTML = head("Reports and logs") + `<div id="rt"></div>`;
      tabs($("#rt", el), ["Daily reports", "Agent logs", "Job output"], async (i, b) => {
        if (i === 0) {
          const first = await api("records?tab=reports");
          b.innerHTML = first.options.length ? `<div class="panel"><div id="rf"></div></div><div id="rs"></div>` : `<div id="rs"></div>`;
          const show = (d) => ($("#rs", b).innerHTML = card("", md(d.text)));
          if (first.options.length) { const f = form($("#rf", b), [{ k: "name", label: "Report", type: "select", options: first.options, value: first.name }], async () => show(await api(`records?${qs({ tab: "reports", name: f.values().name })}`))); }
          show(first);
        } else if (i === 1) {
          const first = await api("records?tab=logs");
          b.innerHTML = `<div class="panel"><div id="lf"></div></div><div class="card"><pre class="logpre" id="lp"></pre></div>`;
          const show = (d) => ($("#lp", b).textContent = d.lines.join("\n") || "(empty)");
          if (first.options.length) {
            const f = form($("#lf", b), [{ k: "name", label: "Day", type: "select", options: first.options, value: first.name },
              { k: "agent", label: "Agent", type: "select", options: ["all", "data", "regime", "screener", "strategy", "technical", "risk", "validation", "report", "analysis"], value: "all" },
              { k: "errors", label: "Errors only", type: "toggle" }], async () => { const v = f.values(); show(await api(`records?${qs({ tab: "logs", ...v, errors: v.errors ? "true" : "" })}`)); });
          }
          show(first);
        } else {
          const d = await api("records?tab=jobs");
          b.innerHTML = `<div class="card flush" id="jt"></div>`;
          $("#jt", b).appendChild(dataTable(d.jobs, [["finished", "Finished"], ["job", "Job"], ["result", "Result", "status"], ["file", "Log", (v) => `<button class="btn small" data-file="${esc(v)}">open</button>`]], { tools: false }));
          $$("[data-file]", b).forEach((a) => a.addEventListener("click", () => openLog(null, a.dataset.file)));
        }
      }, "records");
    },
  });
})();
