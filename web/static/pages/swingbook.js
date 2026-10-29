/* Market Lab → Swing picks: the monthly buy-only book (15 stocks, hold 20 sessions, liquid 200, simple rules).
   Separate from the intraday work. Data: /api/swing, /api/swing/history, /api/swing/rebalance (marketlab/swingbook.py). */
(function () {
  const { api, esc, num, day, $, note, spinner } = MX;
  const F = (v) => v ? day(v, { weekday: "short", day: "numeric", month: "short", year: "numeric" }) : "—";
  const P = (v, d = 1) => v == null ? "—" : MX.chg(100 * v, d);
  const sym = (s) => `<a class="sym" href="#/desk/${encodeURIComponent(s)}" target="_blank" rel="noopener">${esc(s)}</a>`;

  async function tab(el) {
    el.innerHTML = spinner("Loading swing picks …");
    let d, h;
    try { [d, h] = await Promise.all([api("swing"), api("swing/history")]); } catch (e) { el.innerHTML = note(esc(e.message), "warn"); return; }
    if (!d.ready) { el.innerHTML = `<div class="empty-next"><b>No swing picks yet.</b> <span class="muted">${esc(d.why)}</span></div>`; return; }
    const t = d.tonight, b = d.book, s = d.summary || {};
    const next = t.rebalance_tonight ? `<b class="up">tonight</b> (buy at tomorrow's 09:20)` : t.next_rebalance ? `<b>${F(t.next_rebalance)}</b>` : `in <b>${t.sessions_left}</b> session${t.sessions_left === 1 ? "" : "s"}`;
    el.innerHTML = `<div class="kr-top"><span class="tag">swing · monthly</span> <span class="muted small">${d.topn} stocks from the liquid 200 · hold ${d.hold} sessions · buy only · rules, no AI · separate from intraday</span></div>
      <div class="card sim-head ${s.cagr > s.nifty_cagr ? "passed" : "failed"}"><div>
          <div class="muted small">Track record ${F(s.first)} → ${F(s.last)} · ${s.rebalances} rebalances (${s.live || 0} live, the rest rebuilt without hindsight) · after costs</div>
          <div class="sim-big">${num(s.cagr, 1)}% <span class="muted" style="font-size:16px">a year vs Nifty ${num(s.nifty_cagr, 1)}%</span></div>
          <div class="small">worst drop ${num(s.max_dd, 1)}% · ${num(s.win, 0)}% of months positive · beat Nifty in ${num(s.beat, 0)}% of months</div></div>
        <div class="sim-v"><div class="small">Next rebalance ${next}</div><div class="muted small">ranked tonight: ${F(t.day)}</div>
          <div class="muted small">target is still 1% a day: this book averages about ${num((Math.pow(1 + s.cagr / 100, 1 / 248) - 1) * 100, 2)}% a day</div></div></div>
      ${t.rebalance_tonight ? `<div class="card section kr-est"><b>Rebalance tonight.</b> Buy: ${d.bought.map(sym).join(" · ") || "—"} &nbsp; Sell: ${d.sold.map(sym).join(" · ") || "—"}</div>` : ""}
      <div class="card flush section"><div class="ct" style="padding:14px 16px 0"><h2>Current book</h2><span class="aside muted small">picked ${F(b.day)}${b.entry ? ` · bought ${F(b.entry)} 09:20` : ""} · so far ${P(b.net, 2)} after costs · Nifty ${P(b.nifty, 2)}</span></div><div id="swb"></div></div>
      <div class="grid g2 section">
        <div class="card"><div class="ct"><h2>If it rebalanced tonight</h2><span class="aside muted small">information only: changes happen on the rebalance day</span></div>
          <p class="small"><b class="up">Would buy</b> ${d.would_buy.map(sym).join(" · ") || "—"}</p><p class="small"><b class="down">Would sell</b> ${d.would_sell.map(sym).join(" · ") || "—"}</p></div>
        <div class="card"><div class="ct"><h2>Rules in use</h2><span class="aside muted small">direction learned from history before this year</span></div>
          <p class="small">${Object.entries(d.signs).filter(([k]) => !k.startsWith("m_")).map(([k, v]) => `<span class="chip">${v > 0 ? "▲" : "▼"} ${esc(k)}</span>`).join(" ")}</p></div></div>
      <div class="card section"><div class="ct"><h2>Book vs Nifty</h2><span class="aside muted small">₹100 at the start, after costs</span></div><div id="swc"></div></div>
      <div class="card section"><div class="ct"><h2>Ahead of Nifty, by year</h2></div><div class="kr-chips" style="margin-left:0">${(s.years || []).map((y) => `<span class="chip">${y.year} ${MX.chg(y.excess, 1, { suffix: " pts" })}</span>`).join("")}</div></div>
      <div class="card flush section"><div class="ct" style="padding:14px 16px 0"><h2>Every rebalance</h2><span class="aside muted small">click a row for its 15 stocks</span></div><div id="swh"></div><div id="swr"></div></div>`;
    const SP = await MX.sparks(b.held.map((x) => x.symbol));
    $("#swb", el).appendChild(MX.dataTable(b.held, [["symbol", "Stock", (v) => sym(v)], ["spark", "20 days", (v, r) => MX.spark(SP[r.symbol])],
      ["why", "Why it was picked", (v) => `<span class="small">${esc(v || "")}</span>`], ["close", "Close at pick", "inr"],
      ["ret", "So far", (v) => P(v, 2), true], ["rank_tonight", "Rank tonight", (v) => v ? `#${v}` : `<span class="muted">out of top 40</span>`]],
      { per: 20, rowClass: (r) => r.ret == null ? "" : r.ret > 0 ? "row-hit" : "row-miss" }));
    if (h.ready && h.curve.x.length > 1) MXCharts.lineChart($("#swc", el), { x: h.curve.x, height: 240, log: true, series: [
      { name: "Swing book", y: h.curve.you, area: true, width: 2.2 }, { name: "Nifty", y: h.curve.nifty, dash: true }] });
    if (h.ready) {
      $("#swh", el).appendChild(MX.dataTable(h.rows, [["day", "Picked", (v, r) => `${F(v)} ${r.kind === "live" ? `<span class="tag c-high">live</span>` : `<span class="tag">replay</span>`}`],
        ["done", "Status", (v) => v ? "closed" : `<span class="tag warn">open</span>`], ["net", "After costs", (v) => P(v, 2), true], ["nifty", "Nifty", (v) => P(v, 2), true],
        ["vs", "vs Nifty", (v, r) => r.net == null || r.nifty == null ? "—" : MX.chg(100 * (r.net - r.nifty), 2, { suffix: " pts" }), true],
        ["turnover", "Changed", (v) => v == null ? "—" : `${num(100 * v / 2, 0)}%`, true]],
        { per: 24, csv: "swing_rebalances", rowClass: (r) => r.net == null ? "" : r.net > r.nifty ? "row-hit" : "row-miss" }));
      $("#swh", el).addEventListener("click", async (e) => { const tr = e.target.closest("tbody tr"); if (!tr) return;
        const row = h.rows.find((r) => tr.textContent.includes(F(r.day))); if (!row) return;
        const rec = await api(`swing/rebalance?day=${row.day}`);
        $("#swr", el).innerHTML = `<div style="padding:12px 16px"><b>${F(row.day)}</b> <span class="muted small">${esc(rec.kind)} · saved ${esc(rec.saved)}</span></div>`;
        $("#swr", el).appendChild(MX.dataTable(rec.picks, [["symbol", "Stock", (v) => sym(v)], ["why", "Why", (v) => `<span class="small">${esc(v || "")}</span>`],
          ["ret", row.done ? "Return" : "So far", (v) => P(v, 2), true]], { per: 20, rowClass: (r) => r.ret == null ? "" : r.ret > 0 ? "row-hit" : "row-miss" })); });
    }
  }
  MX.swingTab = tab;
})();
