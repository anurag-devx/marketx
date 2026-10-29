/* Setup: Settings, Data health. */
(function () {
  const { api, post, esc, num, day, dt, $, $$, card, head, note, form, tabs, sections, startJob, dataTable, qs, md, kpi, editTable, toast } = MX;

  // ---------------- Settings ----------------
  async function approvals(b) {
    const d = await api("settings?tab=approvals");
    b.innerHTML = `<div class="sh"><h3>Proposals waiting</h3></div>` + (d.pending.length ? d.pending.map((p) => `<div class="card section"><div class="ct"><h2>${esc(p.title)}</h2><span class="aside">from the ${esc(p.agent)} agent · ${dt(p.created)}</span></div>
        <p>${esc(p.rationale)}</p><details><summary>Evidence</summary><pre class="logpre">${esc((() => { try { return JSON.stringify(JSON.parse(p.evidence), null, 2); } catch (e) { return p.evidence; } })())}</pre></details>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-ap="${esc(p.id)}">Approve</button><button class="btn" data-rj="${esc(p.id)}">Reject</button></div></div>`).join("") : `<div class="empty">No proposals waiting.</div>`) +
      `<div id="apd"></div><div id="apr"></div>` + (d.strategies.length ? `<div class="card section"><div class="ct"><h2>Change a strategy's status</h2></div><div id="apf"></div>
        <div class="row" style="margin-top:10px"><button class="btn" id="apset">Apply</button><span class="muted small">Takes effect at the next run. Active means approved for real money.</span></div></div>` : "");
    $$("[data-ap],[data-rj]", b).forEach((x) => x.addEventListener("click", async () => { await post("settings/proposal", { id: x.dataset.ap || x.dataset.rj, approve: !!x.dataset.ap }, x.dataset.ap ? "Approved" : "Rejected"); approvals(b); }));
    sections($("#apr", b), [d.registry]); sections($("#apd", b), [d.decided]);
    if (d.strategies.length) {
      const f = form($("#apf", b), [{ k: "strategy", label: "Strategy", type: "select", options: d.strategies }, { k: "status", label: "Set status", type: "select", options: ["paused", "active", "paper", "retired"] }]);
      $("#apset", b).addEventListener("click", async () => { await post("settings/strategy-status", f.values(), "Saved"); approvals(b); });
    }
  }

  async function parameters(b) {
    const d = await api("settings?tab=parameters"), c = d.current, R = c.RISK;
    b.innerHTML = note(`Current version ${esc(d.version)}, revision ${esc(d.rev)}. Saving creates a new revision; the next agent run uses it. Changes to costs, liquidity or selection rules only affect strategy choice after you re-run the backtest.`) +
      `<div class="card section"><div class="ct"><h2>Risk agent</h2></div><div id="p1"></div></div><div class="card section"><div class="ct"><h2>Size multiplier by market state</h2></div><div id="p2"></div></div>
      <div class="card section"><div class="ct"><h2>Costs, universe and selection</h2></div><div id="p3"></div></div>
      <div class="card section"><div id="p4"></div><div class="row" style="margin-top:12px"><button class="btn primary" id="psave">Save settings</button><button class="btn" id="preset">Reset to defaults</button></div></div><div id="ph"></div>`;
    const f1 = form($("#p1", b), [{ k: "risk_per_trade", label: "Risk per trade (% of equity)", type: "number", value: +(R.risk_per_trade * 100).toFixed(3), min: 0.1, max: 5, step: 0.1 },
      { k: "max_positions", label: "Max open positions", type: "number", value: R.max_positions, min: 1, max: 50 }, { k: "max_position_pct", label: "Max per stock (% of equity)", type: "number", value: +(R.max_position_pct * 100).toFixed(2), min: 1, max: 100 },
      { k: "max_per_sector", label: "Max per sector", type: "number", value: R.max_per_sector, min: 1, max: 20 }, { k: "daily_loss_cap", label: "Daily loss cap (% of equity)", type: "number", value: +(R.daily_loss_cap * 100).toFixed(2), min: 0.5, max: 10, step: 0.5 },
      { k: "dd_half", label: "Half size at drawdown (%)", type: "number", value: +(R.dd_half * 100).toFixed(2), min: 1, max: 50 }, { k: "dd_stop", label: "Stop new trades at drawdown (%)", type: "number", value: +(R.dd_stop * 100).toFixed(2), min: 1, max: 60 }]);
    const f2 = form($("#p2", b), Object.entries(d.states).map(([k, l]) => ({ k, label: l, type: "number", value: c.STATE_MULT[k], min: 0, max: 2, step: 0.05 })));
    const f3 = form($("#p3", b), [{ k: "COST_RT", label: "Round-trip cost (%)", type: "number", value: +(c.COST_RT * 100).toFixed(3), min: 0, max: 2, step: 0.05 },
      { k: "MIN_TURNOVER_CR", label: "Min avg traded value (₹ cr/day)", type: "number", value: c.MIN_TURNOVER_CR, min: 0, step: 5 }, { k: "MIN_PRICE", label: "Min price (₹)", type: "number", value: c.MIN_PRICE, min: 0, step: 10 },
      { k: "MIN_TRADES_STATE", label: "Min trades per state", type: "number", value: c.MIN_TRADES_STATE, min: 10, max: 500, step: 10 }, { k: "MIN_T_STAT", label: "Min t-stat", type: "number", value: c.MIN_T_STAT, min: 0, max: 5, step: 0.1 }]);
    const f4 = form($("#p4", b), [{ k: "note", label: "Why are you changing this? (saved with the revision)", type: "text", wide: true }]);
    $("#psave", b).addEventListener("click", async () => { const r = await post("settings/parameters", { RISK: f1.values(), STATE_MULT: f2.values(), ...f3.values(), note: f4.values().note }); toast(r.message); parameters(b); });
    $("#preset", b).addEventListener("click", async () => { if (!confirm("Reset all parameters to the defaults?")) return; const r = await post("settings/parameters", { reset: true }); toast(r.message); parameters(b); });
    sections($("#ph", b), [d.history]);
  }

  async function scheduleTab(b) {
    const d = await api("settings?tab=schedule"), s = d.schedule;
    b.innerHTML = `<div class="card section"><div class="ct"><h2>After-close schedule</h2></div>${!d.supported ? `<p class="muted">Scheduling is available on macOS.</p>` :
      `${s ? note(`Runs <b>fetch + all agents</b> on weekdays at ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}. If the Mac is asleep then, it runs when it wakes.`) : note("Not scheduled. The daily run only happens when you click it.")}
        <div class="row"><div class="fld narrow"><label>Time (IST)</label><input class="in" type="time" id="stime" value="${String(s ? s.hour : 16).padStart(2, "0")}:${String(s ? s.minute : 20).padStart(2, "0")}"></div>
        <button class="btn primary" id="ssave">Save schedule</button>${s ? `<button class="btn" id="srm">Remove schedule</button>` : ""}</div>
        ${d.log ? `<details style="margin-top:10px"><summary>Last scheduled run output</summary><pre class="logpre">${esc(d.log)}</pre></details>` : ""}`}</div>
      <div class="card section"><div class="ct"><h2>Alerts after each daily run</h2></div><div id="af"></div>${d.telegram_keys ? "" : `<p class="muted small">Telegram needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in ~/.swing_env.</p>`}
        <div class="row" style="margin-top:10px"><button class="btn primary" id="asave">Save alerts</button><button class="btn" id="atest">Send a test alert</button></div></div>`;
    $("#ssave", b)?.addEventListener("click", async () => { const [h, m] = $("#stime", b).value.split(":").map(Number); await post("settings/schedule", { hour: h, minute: m }, "Saved"); scheduleTab(b); });
    $("#srm", b)?.addEventListener("click", async () => { await post("settings/schedule", { remove: true }, "Removed"); scheduleTab(b); });
    const f = form($("#af", b), [{ k: "macos", label: "macOS notification", type: "toggle", value: d.alerts.macos }, { k: "telegram", label: "Telegram", type: "toggle", value: d.alerts.telegram }]);
    $("#asave", b).addEventListener("click", async () => toast((await post("settings/alerts", f.values())).message));
    $("#atest", b).addEventListener("click", async () => toast((await post("settings/alerts", { test: true })).message));
  }

  async function feeds(b) {
    const d = await api("settings?tab=feeds");
    b.innerHTML = note("RSS feeds for the Market page's news. Add, remove or edit rows, then save.") + `<div class="card section"><div id="ft"></div></div>`;
    editTable($("#ft", b), d.rows, d.columns.map((c) => [c, c, c === "kind" ? { options: ["markets", "companies", "world"] } : {}]),
      { saveLabel: "Save feeds", onSave: async (rows) => toast((await post("settings/feeds", { rows })).message) });
  }

  async function backups(b) {
    const d = await api("settings?tab=backups");
    b.innerHTML = note("Backups hold the agents' database, your settings, positions, account and the Strategy Lab. One is made automatically each day before the agents run; the newest 20 are kept.") +
      `<div class="row section"><button class="btn primary" id="bnow">Back up now</button></div><div id="bl"></div>` +
      (d.backups.length ? `<div class="card section"><div class="ct"><h2>Restore</h2></div><div class="row"><select class="in grow" id="bsel">${d.backups.map((x) => `<option>${esc(x.name)}</option>`).join("")}</select>
        <button class="btn" id="brest">Restore</button></div><p class="muted small">What is there now is saved as a safety backup first.</p></div>` : "");
    $("#bnow", b).addEventListener("click", async () => { toast((await post("settings/backup", {})).message); backups(b); });
    if (d.backups.length) $("#bl", b).appendChild(dataTable(d.backups, [["name", "Backup"], ["tag", "Kind"], ["when", "When"], ["size_mb", "MB", "num1"], ["files", "Files", "int"]], { tools: false }));
    else $("#bl", b).innerHTML = `<div class="empty">No backups yet.</div>`;
    $("#brest", b)?.addEventListener("click", async () => { const n = $("#bsel", b).value; if (!confirm(`Replace current data with ${n}?`)) return; toast((await post("settings/backup", { restore: n })).message); });
  }

  async function ai(b) {
    const d = await api("settings?tab=ai");
    const provs = ["ollama", ...(d.online ? ["anthropic", "openai", "gemini"] : [])];
    b.innerHTML = (d.ollama ? note(`Ollama running · installed: ${esc(d.models.join(", ") || "no models yet")}`) : note("Ollama isn't running on this Mac. Install it from ollama.com and open it; local models are the default for every feature.", "warn")) +
      `<p class="muted small">Local models run on this Mac: questions, your records and your documents never leave it. Online models are optional and stay off until you switch them on below.</p>
      <div class="card section"><div class="ct"><h2>Online models</h2></div><label class="switch"><input type="checkbox" id="aon" ${d.online ? "checked" : ""}><span></span>Allow online models</label>
        ${d.online ? note("When a feature uses an online model, its data (your question, the agents' records or document passages) is sent to that company.", "warn") : ""}
        <div class="grid g3" style="margin-top:10px">${["anthropic", "openai", "gemini"].map((p) => `<div class="fld"><label>${esc(d.providers[p].split(" (")[0])} · key ${d.keys[p] ? "found" : "missing"}</label>
          <input class="in" data-om="${p}" value="${esc(d.online_models[p] || "")}" placeholder="exact model name"></div>`).join("")}</div>
        <p class="muted small">Keys live only in ~/.swing_env on this Mac (outside the project). The app reads them when it makes a request and never shows, logs or saves them.</p></div>
      <div class="card section"><div class="ct"><h2>Model per feature</h2></div><table class="tbl edit"><thead><tr><th>Feature</th><th>Provider</th><th>Model</th><th>Status</th></tr></thead><tbody>
        ${d.features.map((f) => `<tr><td><b>${esc(f.label)}</b></td><td><select class="in" data-fp="${f.key}">${provs.map((p) => `<option value="${p}" ${p === f.provider ? "selected" : ""}>${esc(d.providers[p])}</option>`).join("")}</select></td>
          <td><input class="in" data-fm="${f.key}" list="ollama-models" value="${esc(f.provider === "ollama" ? f.model : "")}" ${f.provider === "ollama" ? "" : "disabled"} placeholder="${f.provider === "ollama" ? "" : "uses the online model above"}"></td>
          <td class="small"><span class="dot ${f.ok ? "ok" : "warn"}" style="display:inline-block;margin-right:6px"></span>${esc(f.status)}</td></tr>`).join("")}</tbody></table>
        <datalist id="ollama-models">${d.models.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
        <div class="fld" style="margin-top:12px;max-width:420px"><label>Embedding model (documents library, always local)</label><input class="in" id="aemb" value="${esc(d.embed_model)}"></div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="asave">Save AI settings</button></div></div>
      <div class="card section"><div class="ct"><h2>Extra packages (documents library and macro data)</h2></div>${d.missing.length ? `<p>Missing: <b>${esc(d.missing.join(", "))}</b>. Install once (about 200 MB if nothing is installed yet).</p><button class="btn" id="ainst">Install missing packages</button>` : `<p class="muted">Installed: ${esc(d.packages.join(", "))}.</p>`}
        <p class="muted small">First-time model downloads (Terminal): <code>ollama pull gemma4:e4b</code> · <code>ollama pull qwen3.5:9b</code> · <code>ollama pull nomic-embed-text</code>. Use the exact names shown on ollama.com/library.</p></div>`;
    $$("[data-fp]", b).forEach((s) => s.addEventListener("change", () => { const m = $(`[data-fm="${s.dataset.fp}"]`, b); m.disabled = s.value !== "ollama"; }));
    $("#aon", b).addEventListener("change", async (e) => { await save(e.target.checked); ai(b); });
    const save = async (online = $("#aon", b).checked) => {
      const features = Object.fromEntries(d.features.map((f) => [f.key, { provider: $(`[data-fp="${f.key}"]`, b).value, model: $(`[data-fm="${f.key}"]`, b).value }]));
      const om = Object.fromEntries($$("[data-om]", b).map((x) => [x.dataset.om, x.value]));
      return post("settings/ai", { online, online_models: om, features, embed_model: $("#aemb", b).value }, "Saved");
    };
    $("#asave", b).addEventListener("click", async () => { await save(); ai(b); });
    $("#ainst", b)?.addEventListener("click", () => startJob("install-ai"));
  }

  function appearance(b) {
    const cur = (() => { try { return localStorage.getItem("mx-theme") || "auto"; } catch (e) { return "auto"; } })();
    b.innerHTML = `<div class="card"><div class="ct"><h2>Theme</h2></div><div id="tf"></div><p class="muted small">Auto follows your Mac's light/dark setting. The switch at the bottom of the sidebar does the same.</p></div>`;
    const f = form($("#tf", b), [{ k: "theme", label: "Appearance", type: "radio", options: [["light", "Light"], ["auto", "Auto"], ["dark", "Dark"]], value: cur }], () => {
      const v = f.values().theme; $(`#theme [data-v="${v}"]`)?.click(); });
  }

  MX.page("settings", {
    title: "Settings",
    async render(el) {
      el.innerHTML = head("Settings") + `<div id="stt"></div>`;
      tabs($("#stt", el), ["Approvals and strategies", "Parameters", "Schedule and alerts", "News feeds", "Backups", "AI models", "Appearance"],
        (i, b) => [approvals, parameters, scheduleTab, feeds, backups, ai, appearance][i](b), "settings");
    },
  });

  // ---------------- Data health ----------------
  async function pricesTab(b) {
    const d = await api("data?tab=prices");
    b.innerHTML = `<div class="row section"><button class="btn primary" id="u1" title="A few minutes. Re-reads the last 10 days; a stock whose history changed (split/bonus) is re-downloaded in full.">Update prices (new days only)</button>
      <button class="btn" id="u2" title="About 30 minutes. Only if something looks wrong.">Re-download all history</button>
      <button class="btn" id="u3" title="Every NSE equity outside today's Nifty 500, for point-in-time tests. 3–4 hours the first time.">${d.has_broad ? "Update all NSE stocks" : "Download all NSE stocks"}</button></div><div id="ps"></div>`;
    $("#u1", b).addEventListener("click", () => startJob("fetch"));
    $("#u2", b).addEventListener("click", () => confirm("Re-download all history? About 30 minutes.") && startJob("fetch-full"));
    $("#u3", b).addEventListener("click", () => startJob("fetch-broad"));
    sections($("#ps", b), d.sections);
  }

  async function historyTab(b) {
    const d = await api("data?tab=history");
    b.innerHTML = note("Everything the news study, Catalyst, Search, Results and the reports learn from. Each download is polite to the source, resumes where it stopped, and can run while you use the app.") +
      `<div class="row section">${d.all_running ? `<button class="btn danger" id="hall" data-stop="history">■ Stop: download all history</button> <span class="dl-live">downloading</span>`
        : `<button class="btn primary" id="hall" title="In order: NSE filings since 2016 → news archives since 2016 → article text (300) → search index. Press again later to continue.">⬇ Download all history (runs for hours; safe to leave running)</button>`}</div>
      <div class="card section flush">${d.rows.map((r, i) => `<div class="hrow${r.running ? " is-running" : ""}"><div class="hl"><b>${esc(r.label)}</b>${r.running ? `<div><span class="dl-live">downloading${r.started ? ` since ${esc(String(r.started).slice(11, 16))}` : ""}</span></div>` : ""}</div>
        <div class="hm">${r.frac != null ? `<div class="bar wide${r.running ? " live" : ""}"><i style="width:${Math.max(1, Math.min(100, 100 * r.frac)).toFixed(1)}%"></i></div>` : ""}<div class="muted small">${esc(r.detail)}</div></div>
        <div>${r.job ? (r.running ? `<button class="btn small danger" data-s="${i}" title="Stops now; it resumes where it stopped next time">■ Stop</button>`
          : `<button class="btn small" data-h="${i}" title="${esc(r.help || "")}">▶ ${esc(r.button)}</button>`) : ""}</div></div>`).join("")}</div>
      ${d.macro ? `<p class="muted small">${esc(d.macro)}</p>` : ""}`;
    const again = () => setTimeout(() => { if (document.body.contains(b)) historyTab(b); }, 1200);
    $("#hall", b).addEventListener("click", async (e) => { if (e.currentTarget.dataset.stop) { await post("job/stop", { lane: "history" }, "Stopped: it resumes where it stopped"); again(); }
      else if (await startJob("history-all")) again(); });
    $$("[data-h]", b).forEach((x) => x.addEventListener("click", async () => { const r = d.rows[+x.dataset.h]; if (await startJob(r.job, r.args)) again(); }));
    $$("[data-s]", b).forEach((x) => x.addEventListener("click", async () => { const r = d.rows[+x.dataset.s];
      await post("job/stop", { lane: r.lane }, "Stopped: it resumes where it stopped next time"); again(); }));
    if (d.busy) setTimeout(() => { if (document.body.contains(b) && b.querySelector("#hall")) historyTab(b); }, 10000);   // live counts while a download runs
  }

  async function newsTab(b) {
    const d = await api("data?tab=news");
    b.innerHTML = `<div class="row section"><button class="btn primary" id="n1">Fetch latest news + filings</button><button class="btn" id="n2" title="Tries every feed and sitemap once (about a minute) and lists what works.">Check news sources</button>
      <button class="btn" id="n3" title="Run after editing data/news_aliases.csv (extra names for a company, one per row: symbol,alias).">Re-link headlines to stocks</button></div>
      <div class="card section"><div id="nf"></div></div><div id="ns"></div><p class="muted small">Older news, NSE filings and article text: see History downloads.</p>`;
    $("#n1", b).addEventListener("click", () => startJob("announcements"));
    $("#n2", b).addEventListener("click", () => startJob("news-check"));
    $("#n3", b).addEventListener("click", () => startJob("news-retag"));
    const o = d.options;
    const f = form($("#nf", b), [{ k: "gnews", label: "Google News search for my stocks", type: "toggle", value: o.gnews, help: `Each news fetch also searches Google News for your holdings, the momentum portfolio and the screener shortlist (up to ${o.gnews_max}). Off by default.` },
      { k: "sitemaps", label: "Read news sitemaps", type: "toggle", value: o.sitemaps, help: "Publishers' last-48-hours sitemaps: more headlines than RSS." }], async () => toast((await post("data/news-options", f.values())).message));
    sections($("#ns", b), d.sections);
  }

  async function qualityTab(b) {
    const d = await api("data?tab=quality"), s = d.summary;
    b.innerHTML = `<div class="row section"><button class="btn" id="qrun">Run checks now</button>${s ? `<span class="tag ${s.status === "ok" ? "pos" : s.status === "errors" ? "neg" : ""}">${esc(s.status)}</span>
      <span class="muted">${s.errors} error(s), ${s.warnings} warning(s), ${s.reviewed} reviewed · checked ${esc(String(s.checked_at).slice(0, 16))}</span>` : `<span class="muted">Not run yet. Checks run after every download and before every agent run.</span>`}</div>
      ${s && s.quarantined.length ? note(`No new signals for <b>${esc(s.quarantined.join(", "))}</b> until their recent errors are reviewed or fixed.`, "warn") : ""}
      ${d.issues.length ? `<div class="panel"><div id="qf"></div></div><div class="card section"><div id="qt"></div>
        <div class="row" style="margin-top:10px"><input class="in grow" id="qnote" placeholder="Note (why the data is right), e.g. real crash on results day"><button class="btn primary" id="qrev">Mark selected as reviewed</button></div>
        <p class="muted small">Tick rows, check the stock on its page, then mark them reviewed if the data is right. If a split or bonus was missed, add it under Corporate actions instead.</p></div>` : ""}`;
    $("#qrun", b).addEventListener("click", () => startJob("quality"));
    if (!d.issues.length) return;
    const sel = new Set();
    const f = form($("#qf", b), [{ k: "sev", label: "Severity", type: "multi", options: ["error", "warning"], value: ["error", "warning"] }, { k: "chk", label: "Check", type: "multi", options: d.checks },
      { k: "rev", label: "Show reviewed", type: "toggle", value: false }], () => draw());
    const draw = () => {
      const v = f.values();
      const rows = d.issues.filter((r) => v.sev.includes(r.severity) && (!v.chk.length || v.chk.includes(r.check)) && (v.rev || !r.reviewed));
      const t = $("#qt", b); t.innerHTML = "";
      t.appendChild(dataTable(rows, [["key", "", (k) => `<input type="checkbox" data-k="${esc(k)}" ${sel.has(k) ? "checked" : ""}>`], ["severity", "Severity", "status"], ["what", "Check"], ["symbol", "Stock", "symonly"],
        ["date", "Date"], ["detail", "Detail", "long"], ["reviewed", "Reviewed", "bool"]], { csv: "quality_issues", height: 420, per: 200 }));
    };
    b.addEventListener("change", (e) => { const c = e.target.closest("[data-k]"); if (c) c.checked ? sel.add(c.dataset.k) : sel.delete(c.dataset.k); });
    $("#qrev", b).addEventListener("click", async () => { if (!sel.size) return toast("Tick issues first", true); toast((await post("data/review", { keys: [...sel], note: $("#qnote", b).value })).message); qualityTab(b); });
    draw();
  }

  async function actionsTab(b) {
    const d = await api("data?tab=actions");
    b.innerHTML = note("For a split or bonus the download didn't catch. Factor 2 = 1:1 bonus or 2:1 split; 1.5 = 1:2 bonus; 5 = 5:1 split. Prices before the ex-date are divided by the factor when loading. If Angel later adjusts its own history, the loader sees the gap is gone and skips it, so nothing is adjusted twice.") +
      `<div class="card section"><div id="at"></div></div><div id="aa"></div>`;
    editTable($("#at", b), d.rows, [["symbol", "Stock"], ["ex_date", "Ex-date (first day at the new price)", { type: "date" }], ["factor", "Factor"], ["note", "Note"]],
      { saveLabel: "Save corporate actions", onSave: async (rows) => { toast((await post("data/actions", { rows })).message); actionsTab(b); } });
    sections($("#aa", b), [d.applied, d.auto].filter(Boolean));
  }

  MX.page("data", {
    title: "Data health", reloadOnJob: true,
    async render(el) {
      el.innerHTML = head("Data health", "Freshness, downloads, quality checks, corporate actions and the stock universe.") + `<div id="dht"></div>`;
      tabs($("#dht", el), ["Prices", "History downloads", "News and filings", "Quality checks", "Corporate actions", "Stock universe"], async (i, b) => {
        if (i === 5) return sections(b, (await api("data?tab=universe")).sections);
        return [pricesTab, historyTab, newsTab, qualityTab, actionsTab][i](b);
      }, "data");
    },
  });
})();
