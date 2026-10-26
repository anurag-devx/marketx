/* Grades assistant: a chat box for Tally and Market Lab → Review. Asks /api/marketlab/assistant; the numbers and the table
   are computed from the Review ledger, the AI only routes the question and words the answer (numbers checked). */
(function () {
  const { esc, num, day } = MX;
  const F = (v) => v ? day(v, { weekday: "short", day: "numeric", month: "short" }) : "—";
  const cell = (t, v, r) => {
    if (v == null || v === "") return `<span class="muted">—</span>`;
    switch (t) {
      case "day": return `<span title="projected on this day's close, for the next session">${F(v)}</span>`;
      case "sym": return `<a href="#/tally/${encodeURIComponent(r.day || "")}" target="_blank" rel="noopener"><b>${esc(v)}</b></a>`;
      case "pct": return `${num(100 * v, 0)}%`;
      case "ret": return `<span class="${v > 0 ? "up" : v < 0 ? "down" : "muted"}">${v > 0 ? "▲ +" : v < 0 ? "▼ " : ""}${num(100 * v, 2)}%</span>`;
      case "ok": return v ? `<span class="up">✓</span>` : `<span class="down">✗</span>`;
      case "dir": return v === "up" ? `<span class="up">▲</span>` : v === "down" ? `<span class="down">▼</span>` : `<span class="muted">•</span>`;
      case "r": return `<b class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${num(v, 2)}</b>`;
      case "num": return num(v, 2);
      case "int": return num(v, 0);
      default: return esc(String(v));
    }
  };
  const EX = ["Which stocks matched yesterday?", "Which stocks did not match?", "Hit rate by sector, last 10 days", "Worst plans yesterday", "Is it getting better?", "How often was TA right overall?"];

  MX.gradesChat = function (host, ctx) {
    host.innerHTML = `<div class="card gc"><div class="ct"><h2>Ask about the grades</h2><span class="aside muted small">numbers are computed from the ledger; the AI only words them</span></div>
      <div class="gc-log" id="gclog"></div>
      <div class="row" style="gap:6px;margin-top:8px"><input class="in" id="gcq" placeholder="e.g. Which stocks matched with real?" style="flex:1"><button class="btn primary" id="gcgo">Ask</button></div>
      <div class="gc-ex">${EX.map((x) => `<button class="lchip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div></div>`;
    const log = host.querySelector("#gclog"), inp = host.querySelector("#gcq");
    const ask = async (q) => {
      q = (q || inp.value).trim(); if (!q) return; inp.value = "";
      const item = document.createElement("div"); item.className = "gc-item";
      item.innerHTML = `<div class="gc-q">${esc(q)}</div><div class="gc-a muted small">Working it out …</div>`;
      log.prepend(item);
      let d; try { d = await MX.api("marketlab/assistant", Object.assign({ question: q }, ctx ? ctx() : {})); }
      catch (e) { item.querySelector(".gc-a").textContent = e.message; return; }
      const days = d.days && d.days.length ? (d.days.length === 1 ? `projected on ${F(d.days[0])}` : `${d.days.length} days, ${F(d.days[d.days.length - 1])} – ${F(d.days[0])}`) : "";
      const tbl = d.rows && d.rows.length ? `<div class="scroll"><table class="tbl"><thead><tr>${d.columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${
        d.rows.map((r) => `<tr>${d.columns.map((c) => `<td>${cell(c.type, r[c.key], r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
      item.querySelector(".gc-a").outerHTML = `<div class="gc-a"><div class="gc-meta muted small">${esc(d.title || "")}${days ? ` · ${days}` : ""} · ${d.writer === "model" ? "worded by the AI, numbers checked" : "plain summary"}</div>
        <p>${esc(d.answer || "")}</p>${tbl}</div>`;
    };
    host.querySelector("#gcgo").addEventListener("click", () => ask());
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
    host.querySelectorAll(".gc-ex button").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
  };
})();
