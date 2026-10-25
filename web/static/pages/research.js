/* Research: Market Debate Lab, Forecast scorecard, Macro & geopolitics, Market report, News research. */
(function () {
  const { api, post, esc, num, day, dt, $, $$, card, head, note, form, tabs, sections, startJob, dataTable, qs, md, kpi, openLog, mmss, editTable, download } = MX;
  const pctv = (v, d = 1) => (v == null ? "—" : `${num(100 * v, d)}%`);

  // ---------------- Debate Lab ----------------
  function progressBox(host) {
    let timer;
    const draw = async () => {
      if (!document.body.contains(host)) return;
      const p = await api("debate/progress").catch(() => null);
      if (!p) return;
      if (p.running) {
        host.innerHTML = card("Running", `<p style="margin:0 0 8px"><span class="spin"></span> ${esc(p.label || p.job)} · ${mmss(p.elapsed || 0)}${p.note ? ` · ${esc(p.note)}` : ""}</p>
          ${p.pct != null ? `<div class="bar wide"><i style="width:${Math.max(2, Math.min(100, p.pct))}%"></i></div>` : ""}<pre class="logpre short">${esc(p.tail || "")}</pre>`);
        timer = setTimeout(draw, 2000);
      } else {
        const l = p.last;
        host.innerHTML = l ? `<div class="note ${l.result === "ok" ? "info" : "warn"}">Last lab job: ${esc(l.job)} · ${esc(l.result)} · ${dt(l.finished)} <button class="btn small" data-file="${esc(l.file)}">Log</button>
          ${l.tail ? `<pre class="logpre short">${esc(l.tail)}</pre>` : ""}</div>` : "";
        $("[data-file]", host)?.addEventListener("click", (e) => openLog(null, e.target.dataset.file));
        if (host._wasRunning) { host._wasRunning = false; host.dispatchEvent(new Event("mx-done")); }
      }
      if (p.running) host._wasRunning = true;
    };
    draw();
    return () => clearTimeout(timer);
  }

  async function debateRun(b, sym) {
    const f0 = await api("debate/form");
    b.innerHTML = `<div class="card section"><div class="ct"><h2>New analysis</h2><span class="aside"><span class="dot ${f0.model_ok ? "ok" : "warn"}"></span> Agents' model: ${esc(f0.model)}</span></div>
        <div id="dfm"></div><div id="dhint" class="muted small" style="margin-top:6px"></div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="dgo">Run analysis</button><span class="muted small">About 3–8 min with the local model; instant without it.</span></div></div>
      <div id="dprog" class="section"></div><div id="davail" class="section"></div>`;
    const f = form($("#dfm", b), [
      { k: "sym", label: "Stock (NSE)", type: "select", options: f0.symbols, value: sym || f0.symbols[0]?.[0], wide: true },
      { k: "h", label: "Horizon (sessions)", type: "number", value: 5, min: 1, max: 120 },
      { k: "target", label: "Question", type: "select", options: f0.targets, value: "rel" },
      { k: "date", label: "Analysis date", type: "date", value: f0.last, help: "Evidence is cut off at 15:30 IST on this day. A past date is a replay, scored separately." },
      { k: "rounds", label: "Cross-examination rounds", type: "number", value: 1, min: 1, max: 3 },
      { k: "band", label: "Neutral band ± % (3-way only)", type: "number", value: 0.5, min: 0.1, max: 10, step: 0.1 },
      { k: "llm", label: "Use language model", type: "toggle", value: true, help: "Off: template theses (instant). Statistics and probabilities are the same either way." }], () => hint());
    const hint = () => { const v = f.values(); const x = [];
      if (!((v.h === 5) && (v.target === "rel" || v.target === "abs"))) x.push("⚗️ Experimental question — scored apart from the two fixed questions (5 sessions vs Nifty, 5 sessions absolute).");
      if (v.date && f0.last && v.date < f0.last) x.push(`↺ Replay for ${v.date}: evidence stops at that day's close; labelled retrospective.`);
      $("#dhint", b).textContent = x.join(" "); };
    hint();
    $("#dgo", b).addEventListener("click", async () => {
      const v = f.values();
      if (!v.sym) return;
      const args = [`sym=${v.sym.replace(/&/g, "__amp__")}`, `h=${Math.round(v.h || 5)}`, `target=${v.target}`, `rounds=${Math.round(v.rounds || 1)}`];
      if (v.date) args.push(`date=${v.date}`);
      if (v.target === "three") args.push(`band=${v.band || 0.5}`);
      if (!v.llm) args.push("--no-llm");
      if (await startJob("debate", args)) setTimeout(() => progressBox($("#dprog", b)), 600);
    });
    const pb = $("#dprog", b); progressBox(pb);
    pb.addEventListener("mx-done", () => { try { sessionStorage.setItem("mx-tab-debate", 1); } catch (e) { /* ignore */ } MX.render(); });
    sections($("#davail", b), [{ kind: "table", title: "Data available to the agents", rows: f0.availability, columns: [["data", "Data"], ["status", "Status", "long"]], tools: false }]);
  }

  async function debateResults(b) {
    const list = await api("debate/list");
    if (!list.length) { b.innerHTML = note("No analyses yet. Run one in New analysis."); return; }
    b.innerHTML = `<div class="panel"><div id="rsel"></div></div><div id="rhead"></div><div id="rbody"></div>`;
    const f = form($("#rsel", b), [{ k: "id", label: "Analysis", type: "select", options: list, value: list[0][0], wide: true }], () => show());
    const show = async () => {
      const id = f.values().id;
      const d = await api(`debate/record?${qs({ id, view: "verdict" })}`), h = d.head;
      $("#rhead", b).innerHTML = `<div class="card section"><div class="ct"><h2>${esc(h.symbol)} <span class="muted">${esc(h.company)}</span></h2><span class="tag ${h.tone}">${esc(h.verdict)}</span>
          ${h.mode !== "live" ? `<span class="tag">replay</span>` : ""}<span class="aside">${dt(h.created)}</span></div>
        <p style="margin:0 0 10px">${esc(h.definition)}</p>
        <div class="grid g4">${kpi("P(positive)", pctv(h.p_up), `base rate ${pctv(h.base_up)}`)}${kpi("Reference price", MX.inr(h.reference_price), esc(dt(h.reference_time)))}
          ${kpi("Expected (similar past)", h.expected == null ? "—" : `${h.expected > 0 ? "+" : ""}${num(h.expected, 2)}%`)}${kpi("Calibration", esc(String(h.calibration || "—").split(":")[0]), esc([String(h.calibration || "").split(":").slice(1).join(":").trim(), h.label].filter(Boolean).join(" · ")))}</div>
        ${h.mode_note ? `<p class="muted small">${esc(h.mode_note)}</p>` : ""}</div>`;
      tabs($("#rbody", b), ["Verdict", "Bull and bear", "Debate timeline", "Statistics", "Evidence", "Model check", "Record"], async (i, x) => {
        if (i === 0) return sections(x, d.sections);
        if (i === 5) return sections(x, (await api(`debate/model?${qs({ id })}`)).sections);
        if (i === 6) {
          const r = await api(`debate/record?${qs({ id, view: "record" })}`);
          x.innerHTML = `<div class="row section"><button class="btn" id="rjs">Download the full record (JSON)</button></div><p class="muted small">Every forecast record is written once and hashed; this is the exact file the scorecard reads.</p>
            <pre class="logpre">${esc(JSON.stringify(r.record.spec, null, 2))}</pre>`;
          $("#rjs", x).addEventListener("click", () => download(JSON.stringify(r.record, null, 2), `${id}.json`, "application/json"));
          return;
        }
        sections(x, (await api(`debate/record?${qs({ id, view: ["", "sides", "timeline", "stats", "evidence"][i] })}`)).sections);
      }, "debate-rec");
    };
    await show();
  }

  async function debateBatch(b, U) {
    const d = await api("debate/batch");
    const inc = d.selection.include || [], exc = d.selection.exclude || [];
    const names = (t) => inc.filter((r) => r.type === t).map((r) => r.name);
    const rule = (t) => inc.find((r) => r.type === t);
    b.innerHTML = `${note("After every <b>Fetch data + run all agents</b>, the code-only part of the lab forecasts this list (no language model). Results: <a href=\"#/scorecard\">Forecast scorecard</a>.")}
      <div class="row section"><label class="switch"><input type="checkbox" id="bon" ${d.enabled ? "checked" : ""}><span></span>Run nightly</label>
        <button class="btn" id="b1">Run for the latest day</button><button class="btn" id="b2" title="Fills the scorecard quickly with retrospective forecasts, reported apart from live ones">Replay last 20 sessions</button><button class="btn" id="b3">Download NSE index lists</button></div>
      <div id="bprog" class="section"></div>
      <div class="card section"><div class="ct"><h2>${esc(d.selection.name)} · ${d.symbols.length} stocks</h2></div><p class="muted small" style="margin:0">${esc(d.describe)} · questions: 5 sessions vs Nifty (main), 5 sessions absolute (second)</p>
        ${d.notes.map((n) => note(esc(n), "warn")).join("")}<p class="small">${esc(d.symbols.join(", "))}</p></div>
      <div class="card section"><div class="ct"><h2>Change the stock list</h2></div><div id="bsel"></div><div class="row" style="margin-top:12px"><button class="btn primary" id="bsave">Save selection</button></div></div>
      <div class="card section"><div class="ct"><h2>Custom lists</h2></div><div id="bcf"></div><div class="row" style="margin-top:10px"><button class="btn" id="bcs">Save list</button></div><div id="bcl"></div></div>
      <div id="brec"></div><div id="bidx"></div>`;
    $("#bon", b).addEventListener("change", (e) => post("debate/batch/config", { enabled: e.target.checked }, "Saved"));
    $("#b1", b).addEventListener("click", () => startJob("debate-batch"));
    $("#b2", b).addEventListener("click", () => startJob("debate-replay"));
    $("#b3", b).addEventListener("click", () => startJob("stock-lists"));
    progressBox($("#bprog", b));
    const symOpts = U.symbols, customs = Object.keys(d.customs);
    const f = form($("#bsel", b), [
      { k: "name", label: "Name of this selection", type: "text", value: d.selection.name, wide: true },
      { k: "idx", label: "NSE indices", type: "multi", options: d.indices, value: names("index") },
      { k: "sec", label: "Sectors (Nifty 500 industry)", type: "multi", options: d.sectors, value: names("sector") },
      { k: "top", label: "Most liquid", type: "toggle", value: !!rule("top_liquid") }, { k: "topn", label: "how many", type: "number", value: rule("top_liquid")?.n || 50, min: 10, max: 500, step: 10 },
      { k: "hold", label: "Your holdings", type: "toggle", value: !!rule("holdings") }, { k: "scr", label: "Screener shortlist", type: "toggle", value: !!rule("screener") },
      { k: "expo", label: "Exposure list stocks", type: "toggle", value: !!rule("exposure") },
      { k: "cus", label: "Custom lists", type: "multi", options: customs, value: names("custom").filter((x) => customs.includes(x)) },
      { k: "syms", label: "Add stocks by hand", type: "multi", options: symOpts, value: rule("symbols")?.symbols || [] },
      { k: "xs", label: "Exclude sectors", type: "multi", options: d.sectors, value: exc.filter((r) => r.type === "sector").map((r) => r.name) },
      { k: "xy", label: "Exclude stocks", type: "multi", options: symOpts, value: exc.find((r) => r.type === "symbols")?.symbols || [] }]);
    $("#bsave", b).addEventListener("click", async () => {
      const v = f.values();
      const include = [...v.idx.map((name) => ({ type: "index", name })), ...v.sec.map((name) => ({ type: "sector", name })),
        ...(v.top ? [{ type: "top_liquid", n: Math.round(v.topn || 50) }] : []), ...(v.hold ? [{ type: "holdings" }] : []), ...(v.scr ? [{ type: "screener" }] : []),
        ...(v.expo ? [{ type: "exposure" }] : []), ...v.cus.map((name) => ({ type: "custom", name })), ...(v.syms.length ? [{ type: "symbols", symbols: v.syms }] : [])];
      const exclude = [...v.xs.map((name) => ({ type: "sector", name })), ...(v.xy.length ? [{ type: "symbols", symbols: v.xy }] : [])];
      await post("debate/batch/config", { selection: { name: v.name || "Selection", include, exclude } }, "Saved. New forecasts use it from the next run.");
      MX.render();
    });
    const cf = form($("#bcf", b), [{ k: "name", label: "List name", type: "text" }, { k: "symbols", label: "Stocks", type: "multi", options: symOpts, wide: true }]);
    $("#bcs", b).addEventListener("click", async () => { const v = cf.values(); await post("debate/custom", v, `Saved ${v.name}`); MX.render(); });
    $("#bcl", b).innerHTML = customs.map((k) => `<div class="row" style="margin-top:8px"><b>${esc(k)}</b> <span class="muted small">(${d.customs[k].length}) ${esc(d.customs[k].slice(0, 30).join(", "))}</span><span class="spacer"></span><button class="btn small" data-del="${esc(k)}">Delete</button></div>`).join("");
    $$("[data-del]", b).forEach((x) => x.addEventListener("click", async () => { if (!confirm(`Delete the list ${x.dataset.del}?`)) return; await post("debate/custom", { delete: x.dataset.del }, "Deleted"); MX.render(); }));
    const S = [];
    if (d.recent) S.push({ kind: "table", title: `Recent batch forecasts · ${day(d.recent.as_of)}`, aside: Object.entries(d.recent.counts).map(([k, v]) => `${k}: ${v}`).join(" · "), rows: d.recent.rows, csv: "batch_forecasts",
      columns: [["symbol", "Stock", "symonly"], ["sector", "Sector"], ["target", "Question"], ["p_up", "P(positive)", "num3"], ["base_up", "Base rate", "num3"], ["edge", "Edge", "snum2"], ["verdict", "Verdict"], ["calibration", "Calibration"], ["mode", "Mode"]] });
    S.push({ kind: "table", title: "NSE index lists on this Mac", rows: d.index_status, columns: [["index", "Index"], ["members", "Members", "int"], ["updated", "Updated"]], tools: false });
    sections($("#brec", b), S);
  }

  MX.page("debate", {
    title: "Debate Lab",
    async render(el, arg) {
      el.innerHTML = head("Market Debate Lab", "Bull and bear agents argue from the same evidence; every claim is tested on history; a judge decides — often “Insufficient evidence”.") + `<div id="dt"></div>`;
      const U = await api("meta/lists").catch(() => ({ symbols: [] }));
      if (arg) try { sessionStorage.setItem("mx-tab-debate", 0); } catch (e) { /* ignore */ }
      tabs($("#dt", el), ["New analysis", "Results", "Nightly batch", "How it works"], async (i, b) => {
        if (i === 0) return debateRun(b, arg);
        if (i === 1) return debateResults(b);
        if (i === 2) return debateBatch(b, U);
        sections(b, (await api("debate/how")).sections);
      }, "debate");
    },
  });

  // ---------------- Forecast scorecard ----------------
  MX.page("scorecard", {
    title: "Forecast scorecard",
    async render(el) {
      const first = await api("scorecard");
      el.innerHTML = head("Forecast scorecard", esc(first.status), `<button class="btn" id="sev">Score finished forecasts</button>`) + `<div id="sf"></div><div id="sb"></div>`;
      $("#sev", el).addEventListener("click", () => startJob("debate-eval"));
      if (first.empty) { $("#sb", el).innerHTML = note("Nothing scored yet. Forecasts are scored once their horizon ends: run the nightly batch, or replay the last 20 sessions in Debate Lab → Nightly batch."); return; }
      const F = first.filters;
      $("#sf", el).innerHTML = `<div class="panel"><div id="sff"></div></div>`;
      const f = form($("#sff", el), [
        { k: "mode", label: "Forecasts", type: "radio", options: [["live", "Live"], ["retrospective", "Replays"], ["both", "Both"]], value: first.mode },
        { k: "targets", label: "Question", type: "multi", options: [["rel", "vs Nifty"], ["abs", "Absolute"]] },
        { k: "sources", label: "Source", type: "multi", options: F.sources }, { k: "lists", label: "List", type: "multi", options: F.lists },
        { k: "sectors", label: "Sector", type: "multi", options: F.sectors },
        { k: "start", label: "From", type: "date", value: F.first }, { k: "end", label: "To", type: "date", value: F.last }], () => load());
      const params = () => { const v = f.values(); return { mode: v.mode, targets: v.targets.join(","), sources: v.sources.join("|"), lists: v.lists.join("|"), sectors: v.sectors.join("|"), start: v.start, end: v.end }; };
      const views = ["table", "calibration", "time", "buckets", "verdicts", "breakdowns", "misses", "h2h"];
      const load = () => tabs($("#sb", el), ["Forecast vs actual", "Calibration", "Over time", "Probability buckets", "Verdicts", "Breakdowns", "Biggest misses", "Debate vs batch"],
        async (i, b) => sections(b, (await api(`scorecard?${qs({ ...params(), view: views[i] })}`)).sections), "scorecard");
      load();
    },
  });

  // ---------------- Macro ----------------
  /* Market news: the market report, macro & geopolitics and news research on one page (tabs) */
  const MN = [["report", "Market report"], ["macro", "Macro & geopolitics"], ["news-research", "News research"]];
  MX.page("market-news", {
    title: "Market news", reloadOnJob: true,
    async render(el, arg) {
      const tab = MN.some(([k]) => k === arg) ? arg : (() => { try { return sessionStorage.getItem("mx-mn") || "report"; } catch (e) { return "report"; } })();
      el.innerHTML = `<div class="tabs" id="mnt">${MN.map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div><div id="mnb"></div>`;
      $("#mnt", el).addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) location.hash = `#/market-news/${x.dataset.t}`; });
      try { sessionStorage.setItem("mx-mn", tab); } catch (e) { /* ignore */ }
      const def = MX.pageDef(tab);
      if (def) await def.render($("#mnb", el));
    },
  });

  MX.page("macro", {
    title: "Macro",
    async render(el) {
      el.innerHTML = head("Macro and geopolitics", "Global factors (crude, rupee, US yields, dollar, VIX, geopolitical risk) and how Indian sectors have reacted to them.",
        `<button class="btn" id="mf">Fetch macro data</button><button class="btn" id="mr">Run macro study</button>`) + `<div id="mst"></div><div id="mt"></div>`;
      $("#mf", el).addEventListener("click", () => startJob("macro-fetch"));
      $("#mr", el).addEventListener("click", () => startJob("macro-research"));
      const status = (s) => { $("#mst", el).innerHTML = s ? `<p class="muted small" style="margin:-6px 0 10px">${esc(s)}</p>` : ""; };
      tabs($("#mt", el), ["Indicators", "Geopolitics", "Sector sensitivity", "Shock days", "India data", "Calendar"], async (i, b) => {
        const tab = ["indicators", "geo", "sensitivity", "shocks", "india", "calendar"][i];
        if (tab === "indicators") {
          const d0 = await api("macro?tab=indicators"); status(d0.status);
          if (!d0.series) return sections(b, d0.sections);
          b.innerHTML = `<div id="mk"></div><div class="panel"><div id="mff"></div></div><div id="mc"></div>`;
          sections($("#mk", b), [d0.sections[0]]);
          const f = form($("#mff", b), [{ k: "series", label: "Compare", type: "multi", options: d0.series, value: ["DCOILBRENTEU", "DEXINUS", "DGS10"].filter((s) => d0.series.some((x) => x[0] === s)), wide: true },
            { k: "years", label: "Years", type: "radio", options: [1, 3, 5, 10], value: 3 }, { k: "rebase", label: "Rebase to 100", type: "toggle", value: true }], () => draw());
          const draw = async () => { const v = f.values(); const d = await api(`macro?${qs({ tab, series: v.series.join(","), years: v.years, rebase: v.rebase ? "true" : "false" })}`); sections($("#mc", b), d.sections.slice(1)); };
          sections($("#mc", b), d0.sections.slice(1));
          return;
        }
        if (tab === "geo") {
          b.innerHTML = `<div class="panel"><div id="gff"></div></div><div id="gc"></div>`;
          const f = form($("#gff", b), [{ k: "days", label: "Headlines from the last", type: "radio", options: [[7, "7 days"], [30, "30 days"], [90, "90 days"]], value: 30 }], () => draw());
          const draw = async () => { const d = await api(`macro?${qs({ tab, days: f.values().days })}`); status(d.status); sections($("#gc", b), d.sections); };
          return draw();
        }
        if (tab === "sensitivity") {
          b.innerHTML = `<div class="panel"><div id="sff"></div></div><div id="sc"></div>`;
          const f = form($("#sff", b), [{ k: "window", label: "Window", type: "radio", options: [["3y", "Last 3 years"], ["all", "All history"]], value: "3y" }], () => draw());
          const draw = async () => { const d = await api(`macro?${qs({ tab, window: f.values().window })}`); status(d.status); sections($("#sc", b), d.sections); };
          return draw();
        }
        const d = await api(`macro?tab=${tab}`); status(d.status);
        if (tab === "shocks") return sections(b, d.sections);
        b.innerHTML = note(tab === "india" ? "India's own data (GDP, CPI, IIP, PMI, RBI rate…) has no free daily feed. Type the latest numbers here when they're released; the market report and agents read them."
          : "Upcoming events the agents should know about (RBI policy, Budget, US Fed, elections, results season). Dates as YYYY-MM-DD.") + `<div class="card section"><div id="et"></div></div>`;
        const cols = d.columns.map((c) => [c, c.replace(/_/g, " "), c === "date" || c === "released" ? { type: "date" } : {}]);
        editTable($("#et", b), d.rows, cols, { saveLabel: "Save", onSave: async (rows) => { const r = await post(tab === "india" ? "macro/india" : "macro/calendar", { rows }); MX.toast(`Saved ${r.saved} row${r.saved === 1 ? "" : "s"}`); } });
      }, "macro");
    },
  });

  // ---------------- Market report ----------------
  MX.page("report", {
    title: "Market report",
    reloadOnJob: true,
    async render(el, arg) {
      const d = await api(`report?${qs({ day: arg })}`);
      el.innerHTML = head("Market report", `A written review of the day from stored prices, NSE filings and headlines. <span class="dot ${d.model_ok ? "ok" : "warn"}"></span> ${esc(d.model)}`,
        `<button class="btn" id="rauto" title="On: written every evening in Fetch + run all. Off: only when you click Write report.">Auto each evening: …</button> <button class="btn primary" id="rw">Write report</button>`) +
        `<div class="panel"><div id="rf"></div></div><div id="rb"></div>`;
      const f = form($("#rf", el), [
        ...(d.days.length ? [{ k: "pick", label: "Saved reports", type: "select", options: d.days.map((x) => [x, day(x, { weekday: "short", day: "numeric", month: "short", year: "numeric" })]), value: d.day }] : []),
        { k: "when", label: "Write for", type: "date", value: d.last }, { k: "llm", label: "Use language model", type: "toggle", value: d.model_ok }], () => {
        const v = f.values(); if (v.pick && v.pick !== d.day) location.hash = `#/report/${v.pick}`; });
      const ab = $("#rauto", el), aset = (on) => { ab.textContent = `Auto each evening: ${on ? "on" : "off"}`; ab.classList.toggle("primary", on); ab.dataset.on = on ? "1" : ""; };
      api("report/auto").then((r) => aset(r.on)).catch(() => {});
      ab.addEventListener("click", async () => { try { aset((await MX.post("report/auto", { on: !ab.dataset.on }, "Saved")).on); } catch (e) { /* toast shown */ } });
      $("#rw", el).addEventListener("click", () => { const v = f.values(); startJob("market-report", [v.when || d.last, ...(v.llm ? [] : ["--no-llm"])].filter(Boolean)); });
      if (d.markdown) $(".actions", el).insertAdjacentHTML("beforeend", `<button class="btn" id="rmd">Download Markdown</button>`);
      $("#rmd", el)?.addEventListener("click", () => download(d.markdown, `market_report_${d.day}.md`, "text/markdown"));
      sections($("#rb", el), d.sections);
    },
  });

  // ---------------- News research ----------------
  MX.page("news-research", {
    title: "News research",
    async render(el) {
      const d = await api("news-research");
      el.innerHTML = head("News research", "Do stocks move more on days with news or filings, and does the move continue? Measured on every stored item.",
        `<button class="btn" id="nr">Run the news study</button>`) + (d.status ? `<p class="muted small" style="margin:-6px 0 10px">${esc(d.status)}</p>` : "") + `<div id="ns"></div>`;
      $("#nr", el).addEventListener("click", () => startJob("news-research"));
      sections($("#ns", el), d.sections);
    },
  });
})();
