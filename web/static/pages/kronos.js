/* Market Lab → Kronos (experimental): an open-source model that forecasts price candles (github.com/shiyu-coder/Kronos).
   Kept separate: own environment, own results (results/kronos/), own job lane. Nothing in the radar, picks or Tally uses it;
   the Market Lab column is side by side only. Data: /api/kronos/*. Sub-tabs: Forecasts · Scorecard · Run · How it works. */
(function () {
  const { api, post, esc, num, day, $, $$, note, qs, spinner, startJob, openLog, md } = MX;
  const S = (() => { try { return JSON.parse(sessionStorage.getItem("mx-kronos") || "{}"); } catch (e) { return {}; } })();
  const st = Object.assign({ sub: "fc", set: "", day: "", sym: "",
    run: { what: "latest", freq: "1d", list: "nifty50", symbols: "", model: "small", lookback: 400, h: 5, at: "1100", draws: 8, temp: 1.0, top_p: 0.9, device: "cpu", replay: 50, step: 5 } }, S);
  const save = () => { try { sessionStorage.setItem("mx-kronos", JSON.stringify(st)); } catch (e) { /* ignore */ } };
  const F = (v) => !v ? "—" : v.length > 10 ? `${day(v.slice(0, 10), { day: "numeric", month: "short" })} ${v.slice(11, 16)}` : day(v, { weekday: "short", day: "numeric", month: "short" });
  const SUBS = [["fc", "Forecasts"], ["score", "Scorecard"], ["run", "Run"], ["how", "How it works"]];
  const DIRM = (d) => d === "up" ? `<span class="up">▲</span>` : d === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
  const OK = (v) => v == null ? `<span class="muted">—</span>` : v ? `<span class="up">✓ agree</span>` : `<span class="down">✗ differ</span>`;
  let STATUS = null, HOST = null;

  async function tab(host) {
    HOST = host;
    host.innerHTML = spinner("Loading Kronos …");
    try { STATUS = await api("kronos/status"); } catch (e) { host.innerHTML = note(esc(e.message), "warn"); return; }
    const s = STATUS;
    const run = s.running ? `<span class="tag warn">● running: ${esc(s.running.job || "Kronos job")}</span>` : "";
    host.innerHTML = `<div class="kr-top"><span class="tag">experimental</span> <span class="muted small">Kronos (open-source, MIT) forecasts candles from past prices only. Shown next to Market Lab, never mixed into it.</span> ${run}</div>
      <div class="seg kr-subs" id="krsub">${SUBS.map(([k, l]) => `<button data-v="${k}" class="${st.sub === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="krb" class="section"></div>`;
    $("#krsub", host).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; st.sub = b.dataset.v; save();
      $$("#krsub button", host).forEach((x) => x.classList.toggle("on", x === b)); show(); });
    const show = () => { const b = document.createElement("div"); $("#krb", host).replaceChildren(b);
      const fn = { fc: fcView, score: scoreView, run: runView, how: howView }[st.sub] || fcView;
      Promise.resolve(fn(b)).catch((e) => { if (b.isConnected) b.innerHTML = note(esc(e.message), "warn"); }); };
    if (!s.ready && st.sub !== "how") st.sub = "run";
    $$("#krsub button", host).forEach((x) => x.classList.toggle("on", x.dataset.v === st.sub));
    show();
  }

  const setSel = (id) => STATUS.settings.length ? `<label><span class="mllab">Setting</span><select class="in kr-sel" id="${id}">${STATUS.settings.map((x) =>
    `<option value="${esc(x.key)}" ${x.key === st.set ? "selected" : ""}>${esc(x.label)} · ${x.forecasts} forecasts</option>`).join("")}</select></label>` : "";

  // ---------- Forecasts ----------
  async function fcView(el) {
    if (!STATUS.total) { el.innerHTML = `<div class="empty-next"><b>No forecasts yet.</b> <span class="muted">Next: Run → Forecast latest day, or Replay to score past days straight away.</span></div>`; return; }
    el.innerHTML = spinner("Loading forecasts …");
    const d = await api(`kronos/forecasts?${qs({ set: st.set || undefined, as_of: st.day || undefined })}`);
    if (!d.ready) { el.innerHTML = note(esc(d.why), "info"); return; }
    st.set = d.set; st.day = d.day; save();
    const ag = d.agree.n ? `${d.agree.yes}/${d.agree.n} agree` : d.ml ? "no lean shown on these" : d.freq === "1d" ? "no radar kept that night" : "daily only";
    el.innerHTML = `<div class="card"><div class="sim-row">${setSel("krset")}
        <label><span class="mllab">${d.freq === "5m" ? "Forecast time" : "Forecast made at the close of"}</span><select class="in kr-sel" id="krday">${d.days.map((x) => `<option value="${x}" ${x === d.day ? "selected" : ""}>${F(x)}</option>`).join("")}</select></label>
        <div class="kr-chips"><span class="chip">${d.rows.length} stocks</span><span class="chip">${d.up_share != null ? num(100 * d.up_share, 0) : "—"}% lean up</span>
          <span class="chip">avg ${MX.chg(d.avg_pred, 2)} over ${d.h} ${esc(d.unit)}</span><span class="chip" title="Kronos direction vs Market Lab lean, where Market Lab showed one">Market Lab: ${esc(ag)}</span>
          ${d.scored ? `<span class="chip">scored</span>` : `<span class="chip warn">outcome not known yet</span>`}</div></div></div>
      <div class="card section"><div class="ct"><h2 id="krct">Forecast</h2><span class="aside muted small" id="krcs"></span></div><div id="krch"></div></div>
      <div class="card flush section" id="krt"></div>`;
    $("#krset", el)?.addEventListener("change", (e) => { st.set = e.target.value; st.day = ""; save(); fcView(el); });
    $("#krday", el).addEventListener("change", (e) => { st.day = e.target.value; save(); fcView(el); });
    const SP = d.freq === "1d" ? await MX.sparks(d.rows.map((r) => r.symbol)) : {};
    const cols = [["symbol", "Stock", (v) => `<a class="sym" href="#/desk/${encodeURIComponent(v)}" target="_blank" rel="noopener">${esc(v)}</a>`]];
    if (d.freq === "1d") cols.push(["spark", "20 days", (v, r) => MX.spark(SP[r.symbol])]);
    cols.push(["close", "Close", "inr"], ["pred", `Predicted (${d.h} ${d.unit})`, (v) => MX.chg(v, 2), true], ["p_up", "Chance up", (v) => MX.chanceBar(v, 0.5)],
      ["sd", "Paths disagree", (v) => v == null ? "—" : `${num(v, 1)}%`, true]);
    if (d.freq === "1d") cols.push(["ml_lean", "Market Lab", (v, r) => r.ml_chance == null ? `<span class="muted">—</span>` : `${MX.lean(v)} <span class="muted small">${num(100 * r.ml_chance, 0)}%${r.ml_pick ? " · pick" : ""}</span>`],
      ["agree", "Kronos vs ML", (v) => OK(v)]);
    cols.push(["actual", "Actual", (v) => v == null ? `<span class="muted">waiting</span>` : MX.chg(v, 2), true]);
    if (d.freq === "1d") cols.push(["excess", "vs Nifty", (v) => v == null ? "—" : MX.chg(v, 2), true]);
    $("#krt", el).appendChild(MX.dataTable(d.rows, cols, { csv: `kronos_${d.day}`, per: 60,
      rowClass: (r) => r.actual == null ? "" : ((r.actual > 0) === (r.dir === "up")) ? "row-hit" : "row-miss" }));
    $("#krt", el).addEventListener("click", (e) => { const tr = e.target.closest("tbody tr"); if (!tr || e.target.closest("a")) return;
      const s = tr.querySelector("a.sym")?.textContent; if (s) { st.sym = s; save(); chart(el, d); } });
    if (!d.rows.some((r) => r.symbol === st.sym)) st.sym = d.rows[0] && d.rows[0].symbol;
    chart(el, d);
  }

  async function chart(el, d) {
    const box = $("#krch", el); if (!box || !st.sym) return;
    box.innerHTML = spinner("");
    let x; try { x = await api(`kronos/stock?${qs({ symbol: st.sym, as_of: d.day, set: d.set })}`); } catch (e) { box.innerHTML = note(esc(e.message), "warn"); return; }
    $("#krct", el).innerHTML = `${esc(x.symbol)} <span class="muted small">from ${F(x.as_of)} · ${esc(x.setting)}</span>`;
    $("#krcs", el).innerHTML = `predicted ${MX.chg(x.pred, 2)} · chance up ${num(100 * x.p_up, 0)}% · ${x.actual == null ? "actual not known yet" : `actual ${MX.chg(x.actual, 2)}`}` +
      (x.has_candles ? "" : ` · <span title="forecasts made before this version kept only closing prices">closes only</span>`);
    MXCharts.forecastCandles(box, { history: x.history, after: x.after, forecast: x.forecast, band: x.band, future: x.future, height: 340 });
  }

  // ---------- Scorecard ----------
  async function scoreView(el) {
    if (!STATUS.total) { el.innerHTML = note("Nothing to score yet: run a forecast or a replay first.", "info"); return; }
    el.innerHTML = spinner("Scoring …");
    const d = await api(`kronos/score?${qs({ set: st.set || undefined })}`);
    st.set = d.set || st.set; save();
    const top = `<div class="card"><div class="sim-row">${setSel("krset2")}</div></div>`;
    if (!d.ready) { el.innerHTML = top + `<div class="section">${note(esc(d.why), "info")}</div>`; wire(); return; }
    const k = d.kpis, kp = (l, v, s, c) => `<div class="card kpi"><div class="label">${l}</div><div class="value ${c || ""}">${v}</div><div class="muted small">${s}</div></div>`;
    const sg = (v, dd = 3) => v == null ? "—" : `${v > 0 ? "+" : ""}${num(v, dd)}`;
    el.innerHTML = top + `<div class="card section sim-head ${k.good ? "passed" : k.bad ? "failed" : ""}"><div><div class="muted small">${esc(d.label)} · ${k.forecasts.toLocaleString("en-IN")} scored · ${k.days} days · ${k.stocks} stocks</div>
        <div class="sim-big" style="font-size:22px">${esc(k.verdict[0].toUpperCase() + k.verdict.slice(1))}</div></div>
        <div class="sim-v">${k.good ? `<span class="verdict pass">✓ Has ranking skill</span>` : k.bad ? `<span class="verdict fail">✗ Worse than chance</span>` : `<span class="verdict none">• Not proven</span>`}
        <div class="muted small">judged on daily rank correlation, t ≥ 2 needed</div></div></div>
      ${d.mode_mix.replay ? note(`${d.mode_mix.replay} of the scored forecasts are replays. Kronos may have been trained on those dates, so they can look better than reality; live forecasts are the clean test.`, "warn") : ""}
      <div class="grid g4 section">
        ${kp("Direction right", `${num(k.hit_rate, 1)}%`, `always guessing the common side: ${num(k.best_guess, 1)}%`, k.hit_rate > k.best_guess ? "up" : "down")}
        ${kp("Chance-up skill", sg(k.skill), `Brier ${num(k.brier, 3)} vs base ${num(k.brier_base, 3)} · above 0 = better`, k.skill > 0 ? "up" : "down")}
        ${kp("Ranking (daily IC)", sg(k.mean_ic), k.ic_t == null ? "too few days" : `t ${num(k.ic_t, 1)} on ${k.independent} independent days`, k.mean_ic > 0 ? "up" : "down")}
        ${kp("Top fifth − bottom fifth", k.spread == null ? "—" : MX.chg(k.spread, 2), k.spread_t == null ? "" : `actual return gap · t ${num(k.spread_t, 1)}`)}</div>
      <div class="grid g2 section"><div class="card"><div class="ct"><h2>Ranking skill by day</h2><span class="aside muted small">above 0 = higher-ranked stocks did better</span></div><div id="kric"></div></div>
        <div class="card"><div class="ct"><h2>Calibration</h2><span class="aside muted small">said chance up vs share that went up</span></div><div id="krcal"></div></div></div>
      <div class="card section"><div class="ct"><h2>Predicted vs actual</h2><span class="aside muted small">one dot per forecast · a rising cloud = skill · click a dot for the stock</span></div><div id="krsc"></div></div>
      <div class="card flush section" id="krbd"></div>
      <details class="fold section"><summary>How this is scored</summary><div class="md">${md(d.doc)}</div></details>`;
    wire();
    if (d.by_day.length >= 2) MXCharts.lineChart($("#kric", el), { x: d.by_day.map((r) => r.as_of.slice(0, 10)), height: 200, zero: true, series: [{ name: "IC", y: d.by_day.map((r) => r.ic), area: true }] });
    else $("#kric", el).innerHTML = `<p class="muted small">Needs at least 2 scored days.</p>`;
    if (d.calibration.length) MXCharts.lineChart($("#krcal", el), { x: d.calibration.map((r) => `${num(100 * r.predicted, 0)}%`), height: 200, series: [
      { name: "went up", y: d.calibration.map((r) => r.observed) }, { name: "perfect", y: d.calibration.map((r) => r.predicted), dash: true }] });
    else $("#krcal", el).innerHTML = `<p class="muted small">Needs 50 or more scored forecasts.</p>`;
    if (d.points.length >= 3) { const pts = MXCharts.scatter($("#krsc", el), { points: d.points, xlabel: "Predicted %", ylabel: "Actual %", height: 320 });
 void pts; }
    else $("#krsc", el).innerHTML = `<p class="muted small">Not enough scored forecasts.</p>`;
    $("#krbd", el).appendChild(MX.dataTable(d.by_day.slice().reverse(), [["as_of", "Forecast day", (v) => F(v)], ["stocks", "Stocks", "int"],
      ["hit_rate", "Direction right", (v) => v == null ? "—" : `${num(v, 0)}%`, true], ["ic", "Ranking (IC)", (v) => v == null ? "—" : MX.chg(v, 3, { pct: false }), true],
      ["spread", "Top − bottom fifth", (v) => v == null ? "—" : MX.chg(v, 2), true]], { csv: "kronos_by_day", per: 30, rowClass: (r) => r.ic == null ? "" : r.ic > 0 ? "row-hit" : "row-miss" }));
    function wire() { $("#krset2", el)?.addEventListener("change", (e) => { st.set = e.target.value; st.day = ""; save(); scoreView(el); }); }
  }

  // ---------- Run ----------
  const NSTOCKS = { nifty50: 50, top100: 100, all: 500 };
  const MODELF = { mini: 0.35, small: 1, base: 3.5 };
  function estimate(r) {
    const n = r.list === "custom" ? Math.max(1, r.symbols.split(/[ ,]+/).filter(Boolean).length) : NSTOCKS[r.list];
    const runs = r.what === "replay" ? +r.replay : 1, units = runs * n * r.draws * r.h;
    const sp = STATUS.speed || {}, kk = `${r.model}|${r.device}|${r.freq}`;
    let spu, how;
    if (sp[kk]) { spu = sp[kk].sec_per_unit * (r.lookback / (sp[kk].lookback || 400)); how = `measured on your Mac (${sp[kk].runs} run${sp[kk].runs > 1 ? "s" : ""})`; }
    else { const base = sp["small|cpu|1d"] || sp["small|cpu|5m"]; spu = (base ? base.sec_per_unit : 0.16) * MODELF[r.model] * (r.device === "mps" ? 0.4 : 1) * (r.lookback / 400);
      how = base ? "rough guess from your small-model runs" : "rough guess: not measured yet"; }
    const sec = units * spu + 20;
    return { sec, how, units, n, runs };
  }
  const dur = (s) => s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${num(s / 3600, 1)} h`;
  function argsOf(r) {
    const a = [`freq=${r.freq}`, `model=${r.model}`, `lookback=${r.lookback}`, `h=${r.h}`, `draws=${r.draws}`, `temp=${r.temp}`, `top_p=${r.top_p}`, `device=${r.device}`];
    if (r.list === "custom") a.push(`symbols=${r.symbols.toUpperCase().split(/[ ,]+/).filter(Boolean).join(",")}`); else a.push(`list=${r.list}`);
    if (r.freq === "5m") a.push(`at=${r.at}`);
    if (r.what === "replay") a.push(`replay=${r.replay}`, `step=${r.step}`);
    return a;
  }

  function runView(el) {
    const s = STATUS, r = st.run;
    const ctx = s.models[r.model].context;
    if (r.lookback > ctx) r.lookback = ctx;
    if (r.freq === "5m" && ![6, 12, 24].includes(+r.h)) r.h = 12;
    if (r.freq === "1d" && ![1, 3, 5, 10].includes(+r.h)) r.h = 5;
    const seg = (k, opts, dis) => `<div class="seg${dis ? " dis" : ""}" data-rk="${k}">${opts.map(([v, l, t]) => `<button data-v="${v}" class="${String(r[k]) === String(v) ? "on" : ""}" ${dis ? "disabled" : ""} ${t ? `title="${esc(t)}"` : ""}>${esc(l)}</button>`).join("")}</div>`;
    const hist = (s.history || []).map((x) => `<div class="kr-job"><span class="sdot ${x.result === "ok" ? "ok" : x.result === "running" ? "warn" : "bad"}"></span> ${esc(x.job)} <span class="muted small">${esc(x.finished)} · ${esc(x.result)}</span> <button class="btn small" data-file="${esc(x.file)}">Log</button></div>`).join("");
    el.innerHTML = (!s.ready ? `<div class="card"><div class="ct"><h2>Install</h2></div><p class="small">Sets up Kronos's own environment (.venv-kronos, about 1 GB with PyTorch), the model code pinned to one tested commit, and the Kronos-small weights. Your main setup isn't touched. About 5–10 minutes.</p>
        <button class="btn primary" id="krinst">Install Kronos</button></div>` : "") +
      `<div class="card${s.ready ? "" : " section"}"><div class="ct"><h2>Run a forecast</h2><span class="aside muted small">runs on your Mac in its own lane: the daily run and the app keep working</span></div>
        <div class="sim-row"><div><div class="mllab">What</div>${seg("what", [["latest", "Forecast latest"], ["replay", "Replay past days"]])}</div>
          <div><div class="mllab">Bars</div>${seg("freq", [["1d", "Daily"], ["5m", "5-minute", s.intraday ? "" : "download 5-minute bars first (Data health)"]])}</div>
          <div><div class="mllab">Stocks</div>${seg("list", [["nifty50", "Nifty 50"], ["top100", "Top 100"], ["all", "All 500"], ["custom", "My list"]])}</div>
          ${r.list === "custom" ? `<label class="sim-bk"><span class="mllab">Symbols</span><input class="in" id="krsym" placeholder="TCS, INFY, RELIANCE" value="${esc(r.symbols)}"></label>` : ""}</div>
        <div class="sim-row"><div><div class="mllab">Model</div>${seg("model", [["mini", "mini · 4M", "reads up to 2048 bars (about 8 years of days)"], ["small", "small · 25M", "reads up to 512 bars; the default"], ["base", "base · 102M", "largest released; slowest"]])}</div>
          <label><span class="mllab">Lookback (bars, max ${ctx})</span><input class="in kr-num" id="krlb" type="number" min="60" max="${ctx}" step="20" value="${r.lookback}"></label>
          <div><div class="mllab">Horizon</div>${r.freq === "5m" ? seg("h", [["6", "30 min"], ["12", "1 hour"], ["24", "2 hours"]]) : seg("h", [["1", "1 day"], ["3", "3 days"], ["5", "5 days"], ["10", "10 days"]])}</div>
          ${r.freq === "5m" ? `<div><div class="mllab">From</div>${seg("at", [["1000", "10:00"], ["1100", "11:00"], ["1200", "12:00"], ["1300", "13:00"]])}</div>` : ""}</div>
        <div class="sim-row"><div><div class="mllab">Sampled paths</div>${seg("draws", [["4", "4"], ["8", "8"], ["16", "16"], ["32", "32"]])}</div>
          <label class="kr-sl"><span class="mllab">Temperature <b id="krtv">${num(r.temp, 1)}</b></span><input type="range" id="krt" min="0.1" max="2" step="0.1" value="${r.temp}"><span class="muted small">higher = wilder paths · repo suggests 1.2–1.5</span></label>
          <label class="kr-sl"><span class="mllab">top_p <b id="krpv">${num(r.top_p, 2)}</b></span><input type="range" id="krp" min="0.1" max="1" step="0.05" value="${r.top_p}"><span class="muted small">lower = only the likeliest moves</span></label>
          <div><div class="mllab">Run on</div>${seg("device", [["cpu", "Processor"], ["mps", "Mac chip (GPU)"]])}</div></div>
        ${r.what === "replay" ? `<div class="sim-row"><div><div class="mllab">Past ${r.freq === "5m" ? "days" : "forecast days"}</div>${seg("replay", [["10", "10"], ["25", "25"], ["50", "50"], ["100", "100"]])}</div>
          <div><div class="mllab">Spacing</div>${seg("step", [["1", "every day"], ["5", "every 5th day"]])}</div></div>` : ""}
        <div class="kr-est" id="krest"></div>
        <div class="row" style="gap:8px;margin-top:10px"><button class="btn primary" id="krgo" ${s.ready ? "" : "disabled"}>${r.what === "replay" ? "Start replay" : "Start forecast"}</button>
          <button class="btn" id="krsave" ${r.freq === "1d" && r.what === "latest" ? "" : "disabled title=\"nightly uses a daily forecast\""}>Use these options nightly</button></div></div>
      <div class="card section"><div class="ct"><h2>Nightly</h2></div>
        <label class="kr-toggle"><input type="checkbox" id="krnight" ${s.saved.nightly ? "checked" : ""} ${s.ready ? "" : "disabled"}> Run a Kronos forecast at the end of <b>Fetch + run all</b></label>
        <p class="muted small">Starts in Kronos's own lane after the night's data is in. Options: ${s.saved.args && s.saved.args.length ? `<code>${esc(s.saved.args.join(" "))}</code>` : "the defaults (Nifty 50, small, 5 days, 8 paths)"}.</p></div>
      <div class="card section"><div class="ct"><h2>Recent Kronos jobs</h2></div>${hist || `<p class="muted small">None yet.</p>`}</div>`;
    const redraw = () => runView(el);
    $$(".seg[data-rk]", el).forEach((g) => g.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b || b.disabled) return;
      read(); const k = g.dataset.rk; r[k] = ["h", "draws", "replay", "step"].includes(k) ? +b.dataset.v : b.dataset.v;
      if (k === "freq") { r.h = r.freq === "5m" ? 12 : 5; r.lookback = 400; } save(); redraw(); }));
    function read() { const sy = $("#krsym", el); if (sy) r.symbols = sy.value; r.lookback = Math.max(60, Math.min(ctx, +$("#krlb", el).value || 400));
      r.temp = +$("#krt", el).value; r.top_p = +$("#krp", el).value; save(); }
    const est = () => { read(); const e = estimate(r);
      $("#krtv", el).textContent = num(r.temp, 1); $("#krpv", el).textContent = num(r.top_p, 2);
      $("#krest", el).innerHTML = `<b>About ${dur(e.sec)}</b> <span class="muted small">· ${e.runs} run${e.runs > 1 ? "s" : ""} × ${e.n} stocks × ${r.draws} paths × ${r.h} steps · ${esc(e.how)}</span>` +
        (e.sec > 600 ? ` <span class="tag warn">long job</span>` : "") + (r.device === "mps" && !(STATUS.speed || {})[`${r.model}|mps|${r.freq}`] ? ` <span class="muted small">· Mac chip speed not measured yet</span>` : ""); return e; };
    ["#krlb", "#krt", "#krp", "#krsym"].forEach((q) => $(q, el)?.addEventListener("input", est));
    est();
    $("#krgo", el).addEventListener("click", async () => { const e = est();
      if (r.list === "custom" && !r.symbols.trim()) { MX.toast("Add at least one symbol", true); return; }
      if (e.sec > 600 && !confirm(`This will take about ${dur(e.sec)} on your Mac. Start?`)) return;
      if (await startJob(r.what === "replay" ? "kronos-replay" : "kronos-forecast", argsOf(r))) setTimeout(() => HOST && HOST.isConnected && tab(HOST), 800); });
    $("#krsave", el).addEventListener("click", async () => { read(); STATUS.saved = await post("kronos/settings", { args: argsOf(r) }, "Saved: nightly runs will use these options"); redraw(); });
    $("#krnight", el).addEventListener("change", async (e) => { STATUS.saved = await post("kronos/settings", { nightly: e.target.checked }, e.target.checked ? "Nightly Kronos forecast on" : "Nightly Kronos forecast off"); redraw(); });
    $("#krinst", el)?.addEventListener("click", () => startJob("kronos-install"));
    $$("[data-file]", el).forEach((b) => b.addEventListener("click", () => openLog(null, b.dataset.file)));
  }

  // ---------- How it works ----------
  function howView(el) {
    el.innerHTML = `<div class="card md">${md(`**What it does.** For each stock, Kronos reads the last *lookback* candles (open, high, low, close, volume) up to the forecast point — nothing later — and samples several possible future paths. Daily: the next 1–10 sessions. 5-minute: the next 30 minutes to 2 hours of the same session.

**What is stored.** The mean predicted candle per step, the 10–90% range of the sampled closes, the average predicted return, the chance it ends up (share of paths ending higher) and how much the paths disagree. Only in \`results/kronos/\`.

**Settings.** *Model:* mini (4M parameters, reads up to 2048 bars), small (25M, 512 bars; default), base (102M, 512 bars). *Temperature* makes paths wilder or calmer; *top_p* keeps only the likeliest moves. Each combination is scored on its own.

**How it is judged.** Once the horizon has passed: direction vs always guessing the common side; chance-up vs the base rate (Brier skill); whether the stocks it ranked higher actually did better (daily rank correlation, with t-statistics on independent days); top fifth minus bottom fifth.

**Kronos vs Market Lab.** Market Lab asks "will it move at least 1%, and which way does it lean"; Kronos predicts the path. The column shows where both gave a direction and whether they agree. Information only: nothing is combined.

**Kept separate.** Own Python environment (\`.venv-kronos\`), own code (\`kronos_lab/\`), own results, own job lane. Removing it = deleting those folders.

**Caution.** Kronos was trained on data from 45+ exchanges and may have seen these years of Indian prices, so replays can flatter it. Live forecasts are the clean test. Fine-tuning on NSE data is possible in their repo but needs Nvidia GPUs; not set up here. Model: MIT licence; code pinned to one commit.`)}</div>`;
  }

  MX.kronosTab = tab;
  // old link (#/kronos) → Market Lab → Kronos
  MX.page("kronos", { title: "Kronos", render() { if (MX.mlGo) MX.mlGo("kronos"); location.hash = "#/market-lab"; } });
})();
