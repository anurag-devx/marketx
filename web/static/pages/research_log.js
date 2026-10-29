/* Research (#/research): every test with its question, the pass mark set before it ran, status and key numbers.
   Source: research/registry.json (research/RESEARCH_LOG.md is written from the same file). Data: /api/research/log. */
(function () {
  const { api, esc, day, $, $$, spinner, md } = MX;
  const S = (() => { try { return sessionStorage.getItem("mx-research") || "all"; } catch (e) { return "all"; } })();
  let view = S;
  const PILL = { passed: `<span class="verdict pass">✓ Passed</span>`, failed: `<span class="verdict fail">✗ Failed</span>`,
    running: `<span class="verdict run">● Running</span>`, pending: `<span class="verdict none">○ Pending</span>` };
  const F = (v) => v ? day(v, { day: "numeric", month: "short", year: "numeric" }) : "";

  function card(it) {
    return `<div class="card rs-card ${it.status}" id="rs-${esc(it.id)}">
      <div class="rs-top"><span class="rs-id">${esc(it.id)}</span><b class="rs-title">${esc(it.title)}</b><span class="rs-pill">${PILL[it.status] || ""}</span></div>
      <div class="rs-q">${esc(it.question)}</div>
      <div class="rs-mark"><span class="mllab">Pass mark, set before it ran</span><div>${esc(it.pass_mark)}</div></div>
      ${it.headline ? `<div class="rs-res"><span class="mllab">Result${it.date ? ` · ${F(it.date)}` : ""}</span><div>${esc(it.headline)}</div></div>`
        : `<div class="rs-res muted"><span class="mllab">Needs</span><div>${esc(it.needs || "—")}</div></div>`}
      <button class="btn small rs-more" data-id="${esc(it.id)}">Details</button><div class="rs-detail" hidden></div></div>`;
  }

  async function page(el) {
    el.innerHTML = spinner("Loading research …");
    const d = await api("research/log");
    const c = d.count;
    el.innerHTML = MX.head("Research", esc(d.intro)) +
      `<div class="card sim-head"><div><div class="muted small">Goal</div><div class="sim-big" style="font-size:22px">1% a day on capital</div>
          <div class="small">best tested so far: swing book about <b>0.08%</b> a day (19-22% a year); intraday <b>0.06%</b> a day. The day's direction is the main lever (T8 ceiling +0.58% a day).</div></div>
        <div class="sim-v"><div class="kr-chips"><span class="verdict pass">✓ ${c.passed} passed</span><span class="verdict fail">✗ ${c.failed} failed</span>
          ${c.running ? `<span class="verdict run">● ${c.running} running</span>` : ""}<span class="verdict none">○ ${c.pending} pending</span></div>
          <div class="muted small">order agreed: data → research → screens</div></div></div>
      <div class="row section" style="gap:10px;align-items:center"><div class="seg" id="rsv">${[["all", "All"], ["done", "Done"], ["planned", "Planned"], ["passed", "Passed"], ["failed", "Failed"]]
        .map(([k, l]) => `<button data-v="${k}" class="${view === k ? "on" : ""}">${l}</button>`).join("")}</div>
        <span class="muted small">file: research/RESEARCH_LOG.md (same content)</span></div>
      <div class="rs-grid" id="rsg"></div>
      <details class="fold section"><summary>Ground rules</summary><ul class="small">${d.rules.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>
      <details class="fold section"><summary>Plan</summary><p class="small">${esc(d.plan)}</p></details>
      ${d.entries.length ? `<details class="fold section" open><summary>Latest entries</summary><ul class="small">${d.entries.slice().reverse().map((e) => `<li>${esc(F(e.date))} ${esc(e.text)}</li>`).join("")}</ul></details>` : ""}`;
    const draw = () => {
      const pick = d.items.filter((x) => view === "all" || x.kind === view || x.status === view);
      $("#rsg", el).innerHTML = pick.map(card).join("") || `<p class="muted">Nothing here.</p>`;
    };
    $("#rsv", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; view = b.dataset.v;
      try { sessionStorage.setItem("mx-research", view); } catch (x) { /* ignore */ }
      $$("#rsv button", el).forEach((x) => x.classList.toggle("on", x === b)); draw(); });
    $("#rsg", el).addEventListener("click", (e) => { const b = e.target.closest(".rs-more"); if (!b) return;
      const it = d.items.find((x) => x.id === b.dataset.id), box = b.nextElementSibling;
      if (!box.hidden) { box.hidden = true; b.textContent = "Details"; return; }
      box.innerHTML = (it.numbers && it.numbers.length ? `<table class="tbl rs-num"><tbody>${it.numbers.map(([a, v]) => `<tr><td>${esc(a)}</td><td class="r">${esc(v)}</td></tr>`).join("")}</tbody></table>` : "") +
        (it.notes ? `<div class="md small">${md(it.notes)}</div>` : "") + (it.needs && it.headline ? `<p class="small"><b>Needs:</b> ${esc(it.needs)}</p>` : "") +
        (it.script ? `<p class="muted small">Script: <code>${esc(it.script)}</code>${it.results ? ` · Results: <code>${esc(it.results)}</code>` : ""}</p>` : "");
      box.hidden = false; b.textContent = "Hide details"; });
    draw();
  }
  MX.page("research", { title: "Research", render: page });
})();
