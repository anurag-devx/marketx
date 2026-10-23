/* Experimental: Kronos. A separate section: its own environment, results and scorecard; nothing else uses it. */
(function () {
  const { api, esc, num, day, dt, $, head, note, form, tabs, sections, startJob, qs, openLog } = MX;

  MX.page("kronos", {
    title: "Kronos", reloadOnJob: true,
    async render(el) {
      const d = await api("kronos");
      el.innerHTML = head("Kronos <span class=\"tag\">experimental</span>", "An open-source model that forecasts price candles (github.com/shiyu-coder/Kronos). Kept separate: the agents, screener and Debate Lab don't use it.",
        d.ready ? `<button class="btn" id="kr">Replay 50 past days</button><button class="btn primary" id="kf">Forecast latest day</button>` : `<button class="btn primary" id="ki">Install Kronos</button>`) +
        `<div id="kst"></div><div id="kt"></div>`;
      const last = d.last ? `Last Kronos job: ${esc(d.last.job)} · ${esc(d.last.result)} · ${esc(d.last.finished)} <button class="btn small" data-file="${esc(d.last.file)}">Log</button>` : "";
      $("#kst", el).innerHTML = !d.ready
        ? note(`Not installed yet. <b>Install</b> sets up its own environment (.venv-kronos, about 1 GB with PyTorch), the model code pinned to one version, and the Kronos-small weights. Your main setup isn't touched. ${last}`)
        : (last ? `<p class="muted small" style="margin:-6px 0 10px">${last}</p>` : "");
      $("[data-file]", el)?.addEventListener("click", (e) => openLog(null, e.target.dataset.file));
      $("#ki", el)?.addEventListener("click", () => startJob("kronos-install"));
      $("#kf", el)?.addEventListener("click", () => startJob("kronos-forecast"));
      $("#kr", el)?.addEventListener("click", () => confirm("Forecast 50 past days (5 sessions apart) for the Nifty 50? Takes a while on the Mac's processor; you can keep using the app.") && startJob("kronos-replay"));
      if (!d.ready && !d.total) return;
      if (!d.total) { $("#kt", el).innerHTML = note("No forecasts yet. Press Forecast latest day, or Replay to score past days straight away."); return; }
      tabs($("#kt", el), ["Forecasts", "Scorecard", "How it works"], async (i, b) => {
        if (i === 0) {
          const f0 = await api("kronos?view=latest");
          b.innerHTML = `<div class="panel"><div id="kd"></div></div><div id="ks"></div><div id="kc"></div>`;
          const f = form($("#kd", b), [{ k: "as_of", label: "Forecast day", type: "select", options: f0.days.map((x) => [x, day(x, { weekday: "short", day: "numeric", month: "short", year: "numeric" })]), value: f0.day }], () => load());
          const wire = (dd) => sections($("#ks", b), dd.sections);
          $("#ks", b).addEventListener("click", async (e) => {     // delegated: survives sorting and paging
            const tr = e.target.closest("tbody tr"); if (!tr || e.target.closest("a")) return;
            const sym = tr.querySelector("a.sym")?.textContent; if (!sym) return;
            const x = await api(`kronos/stock?${qs({ symbol: sym, as_of: f.values().as_of })}`);
            $("#kc", b).innerHTML = `<div class="card section"><div class="ct"><h2>${esc(x.title)}</h2></div><div class="ch"></div></div>`;
            MXCharts.draw($("#kc .ch", b), x.chart); });
          const load = async () => wire(await api(`kronos?${qs({ view: "latest", as_of: f.values().as_of })}`));
          wire(f0);
          $("#kc", b).innerHTML = `<p class="muted small">Click a stock to see its predicted path against what happened.</p>`;
        } else if (i === 1) {
          sections(b, (await api("kronos?view=score")).sections);
        } else {
          b.innerHTML = MX.card("", MX.md(`**What it does.** For each Nifty 50 stock, Kronos-small reads the last 400 daily candles (open, high, low, close, volume) up to the close of the forecast day — nothing later — and samples 8 possible paths for the next 5 sessions.

**What is stored.** The average predicted return, P(up) (share of paths ending higher) and how much the paths disagree. Only in \`results/kronos/\`.

**How it is judged.** Against simple baselines, once the 5 sessions have passed: direction vs always guessing the common side; P(up) vs the base rate (Brier skill); whether higher-ranked stocks actually did better (daily rank correlation, with t-statistics on non-overlapping days); top fifth minus bottom fifth vs the Nifty.

**Kept separate.** Own Python environment (\`.venv-kronos\`), own code folder (\`kronos_lab/\`), own results, own job lane, own page. Removing it = deleting those folders.

**Caution.** Kronos may have been trained on these same years of Indian prices, so replays can flatter it. Only live forecasts are a clean test. Model: MIT licence; code pinned to one commit.`));
        }
      }, "kronos");
    },
  });
})();
