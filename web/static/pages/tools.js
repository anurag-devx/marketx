/* Tools: Search (with Deep search, exposure list, web settings), Ask, Documents, Strategy Lab. */
(function () {
  const { api, post, esc, num, day, dt, $, $$, card, head, note, form, tabs, sections, startJob, dataTable, qs, md, kpi, spinner, editTable, download, toast, openLog } = MX;

  // poll an in-process task until it finishes; onTick(task) for progress
  async function pollTask(id, onTick) {
    for (;;) {
      const t = await api(`task?id=${id}`);
      onTick && onTick(t);
      if (!t.running) { if (t.error) throw new Error(t.error); return t.result; }
      await new Promise((r) => setTimeout(r, 1200));
    }
  }
  MX.pollTask = pollTask;

  const bubble = (role, html) => `<div class="msg ${role}"><div class="who">${role === "user" ? "You" : "MarketX"}</div><div class="body">${html}</div></div>`;
  const hitsHtml = (hits, prefix = "") => !hits || !hits.length ? "" : `<details class="srcs"><summary>Sources (${hits.length} passages)</summary>${hits.map((h, i) =>
    `<div class="src"><b>[${prefix}${i + 1}] ${esc(h.title)}</b>${h.where ? ` · ${esc(h.where)}` : ""}${h.published ? ` · ${esc(h.published)}` : ""}${MX.safeUrl(h.link) ? ` · <a href="${esc(h.link)}" target="_blank" rel="noopener noreferrer">open</a>` : ""}${h.how ? ` <span class="muted small">${esc(h.how)} match</span>` : ""}
     <div class="muted small">${esc(h.text)}</div></div>`).join("")}</details>`;

  // ---------------- Search ----------------
  async function searchTab(b, arg) {
    b.innerHTML = `<div class="panel"><div class="row"><input class="in grow" id="sq" placeholder="A stock (Reliance, HAL), a sector (cement, PSU banks), a theme (Red Sea, tariffs) or any phrase" value="${esc(arg || "")}">
      <button class="btn primary" id="sgo">Search</button></div></div><div id="sres"></div>`;
    const go = async (sym) => {
      const v = $("#sq", b).value.trim(), out = $("#sres", b);
      if (!v) { out.innerHTML = note("Searches everything stored on this Mac: NSE filings, headlines and article text, your documents, macro data and your exposure list. For a written answer to a question, use Deep search."); return; }
      out.innerHTML = spinner("Searching…");
      try { sessionStorage.setItem("mx-search-q", v); } catch (e) { /* ignore */ }
      const r = await api(`search/query?${qs({ q: v, sym })}`), w = r.view;
      out.innerHTML = `<p class="muted small">Understood as: ${esc(r.understood)}</p>${r.choices && r.choices.length ? `<div class="seg" id="sch">${r.choices.map(([s, l]) => `<button data-v="${esc(s)}" class="${s === w.symbol ? "on" : ""}">${esc(l)}</button>`).join("")}</div>` : ""}
        <div class="head" style="margin-top:12px"><div><h1 style="font-size:20px">${esc(w.title)}</h1></div>${w.symbol ? `<div class="actions"><a class="btn" href="#/stock/${encodeURIComponent(w.symbol)}">Open stock page</a><a class="btn" href="#/debate/${encodeURIComponent(w.symbol)}">Debate this stock</a></div>` : ""}</div>
        <div id="shead"></div><div id="sbody"></div>${r.kind !== "text" ? `<details class="section card"><summary><b>Everything matching the words “${esc(v)}”</b></summary><div id="sx"></div></details>` : ""}`;
      $("#sch", out)?.addEventListener("click", (e) => { const x = e.target.closest("button"); if (x) go(x.dataset.v); });
      if (w.head) sections($("#shead", out), w.head);
      if (w.tabs) tabs($("#sbody", out), w.tabs.map((t) => t.name), (i, x) => sections(x, w.tabs[i].sections), "search-stock");
      else sections($("#sbody", out), w.sections);
      const det = $("details", out);
      det?.addEventListener("toggle", async () => { if (det.open && !det._done) { det._done = true; sections($("#sx", out), (await api(`search/text?${qs({ q: v })}`)).sections); } }, { once: false });
    };
    $("#sgo", b).addEventListener("click", () => go());
    $("#sq", b).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    MX.suggest($("#sq", b), { kinds: "stocks,sectors", recent: "search", onPick: () => go() });
    if (!arg) try { $("#sq", b).value = sessionStorage.getItem("mx-search-q") || ""; } catch (e) { /* ignore */ }
    go();
  }

  async function deepTab(b) {
    const d = await api("deep");
    b.innerHTML = `<p class="muted small">Model: ${esc(d.model)} · Web: ${esc(d.web)}</p>
      ${d.model_ok ? "" : note("Choose a model for Deep search in Settings → AI models (local by default).", "warn")}
      <div class="card section"><textarea class="in" id="dq" rows="3" placeholder="e.g. How will the Red Sea shipping disruption affect Indian paint and tyre companies?"></textarea>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="dgo" ${d.model_ok ? "" : "disabled"}>Run deep search</button>
        <label class="switch"><input type="checkbox" id="dweb" checked><span></span>Search the web too</label><span class="spacer"></span>
        ${d.history.length ? `<select class="in" id="dh" style="max-width:420px"><option value="">Open an earlier brief</option>${d.history.map((h) => `<option value="${esc(h.file)}">${esc(h.when.slice(0, 16))} · ${esc(h.question.slice(0, 80))}</option>`).join("")}</select>` : ""}</div></div>
      <div id="dprog"></div><div id="dout"></div>`;
    const show = (rec) => {
      $("#dout", b).innerHTML = card(esc(rec.question), md(rec.brief) + (rec.bad_citations && rec.bad_citations.length ? note(`The brief cites sources that don't exist: ${esc(rec.bad_citations.join(", "))}. Treat those statements with care.`, "warn") : "")
        + `<p class="muted small">${esc(String(rec.when).slice(0, 16))} · ${rec.evidence.length} sources · ${rec.seconds} s · searches: ${esc(rec.queries.join(" | "))}</p>`
        + `<details class="srcs"><summary>Sources (${rec.evidence.length})</summary>${rec.evidence.map((e, i) => `<div class="src"><b>[${i + 1}] ${esc(e.title)}</b> · ${esc(e.kind)} · ${esc(e.source)} ${esc(e.date || "")}${MX.safeUrl(e.url) ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">open</a>` : ""}<div class="muted small">${esc(String(e.text || "").slice(0, 400))}</div></div>`).join("")}</details>`);
    };
    const watch = async (id) => {
      $("#dgo", b).disabled = true;
      try { const rec = await pollTask(id, (t) => ($("#dprog", b).innerHTML = card("Researching…", `<p><span class="spin"></span> ${esc(t.msg)} · ${MX.mmss(t.seconds)}</p><pre class="logpre short">${esc(t.log.join("\n"))}</pre>`)));
        $("#dprog", b).innerHTML = ""; show(rec); }
      catch (e) { $("#dprog", b).innerHTML = note(esc(e.message), "warn"); }
      $("#dgo", b).disabled = false;
    };
    MX.suggest($("#dq", b), { recent: "deep", examples: ["How will a weak monsoon affect FMCG and tractor makers?", "Which sectors gain from a weaker rupee?", "What drives PSU bank stocks after RBI policy?"] });
    $("#dgo", b).addEventListener("click", async () => { const q = $("#dq", b).value.trim(); if (!q) return toast("Type a question first", true); MX.remember("deep", q);
      const r = await post("deep/run", { question: q, web: $("#dweb", b).checked }); watch(r.task); });
    $("#dh", b)?.addEventListener("change", async (e) => { if (e.target.value) show(await api(`deep/brief?${qs({ file: e.target.value })}`)); });
    if (d.running) watch(d.running);
  }

  async function exposureTab(b) {
    const d = await api("exposures");
    b.innerHTML = note("Your own map of what moves what. A row says: when this driver moves, this stock or sector is hurt / helped. Search shows these next to the computed sensitivities. Example: driver ‘Brent crude’, target ‘Paints’ or ‘ASIANPAINT’, effect ‘hurt’.") +
      `<div class="card section"><div id="ext"></div></div><div class="row"><button class="btn" id="exs" title="About 18 widely known relationships, each marked 'starter' in the note">Add starter suggestions</button></div>`;
    editTable($("#ext", b), d.rows, [["driver", "Driver (theme or factor)", { options: d.drivers }], ["target", "Stock symbol or sector"], ["effect", "Effect", { options: ["hurt", "helped", "mixed"] }], ["note", "Note"]],
      { saveLabel: "Save exposure list", onSave: async (rows) => { const r = await post("exposures/save", { rows }); toast(`Saved ${r.saved} rows`); } });
    $("#exs", b).addEventListener("click", async () => { const r = await post("exposures/starter", {}); toast(`Added ${r.added} suggestions`); exposureTab(b); });
  }

  async function webTab(b) {
    const d = await api("websearch");
    b.innerHTML = `<p>Deep search can also search the web. Only the search words go to the provider; the pages it finds are read from this Mac where robots.txt allows.</p><div id="wt"></div>
      <p class="muted small">Keys go in ~/.swing_env on this Mac (never shown here). Free tiers change; check the provider's site.</p>
      <div class="card section"><div id="wf"></div><div class="row" style="margin-top:12px"><button class="btn primary" id="ws">Save web search settings</button></div><p class="muted small">Status: ${esc(d.status)}</p></div>`;
    $("#wt", b).appendChild(dataTable(d.rows, [["provider", "Provider"], ["key_name", "Key name in ~/.swing_env"], ["key_found", "Key found", "bool"], ["used_this_month", "Used this month", "int"]], { tools: false }));
    const o = d.options;
    const f = form($("#wf", b), [{ k: "provider", label: "Provider", type: "select", options: d.providers, value: o.provider }, { k: "monthly_cap", label: "Monthly cap", type: "number", value: o.monthly_cap, min: 1, step: 50 },
      { k: "results", label: "Results per search", type: "number", value: o.results, min: 3, max: 20 }, { k: "india_bias", label: "Add ‘India’ to searches", type: "toggle", value: o.india_bias }]);
    $("#ws", b).addEventListener("click", async () => { await post("websearch/save", f.values(), "Saved"); webTab(b); });
  }

  MX.page("search", {
    title: "Search",
    async render(el, arg) {
      el.innerHTML = head("Search", "One box for any stock, sector, theme or phrase — plus cited research briefs.") + `<div id="st"></div>`;
      if (arg) try { sessionStorage.setItem("mx-tab-search", 0); } catch (e) { /* ignore */ }
      tabs($("#st", el), ["Search", "Deep search", "My exposure list", "Web search settings"], (i, b) => [searchTab, deepTab, exposureTab, webTab][i](b, arg), "search");
    },
  });

  // ---------------- Ask ----------------
  let askChat = [];
  MX.page("ask", {
    title: "Ask",
    async render(el) {
      const m = await api("ask");
      el.innerHTML = head("Ask the system", m.model_ok ? `Answers come from the agents' stored data, written up by <b>${esc(m.model)}</b>${m.online ? " (online: your question and the records are sent to that company)." : ". Nothing leaves this Mac."}` : "") +
        (m.model_ok ? "" : note(`No model available: ${esc(m.model)} Until then, I answer with the matching records. Choose models in Settings → AI models.`, "warn")) +
        `<div class="chips section">${m.examples.map((e) => `<button class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}</div>
        <div class="chat card" id="chat"></div>
        <div class="composer"><textarea class="in" id="aq" rows="2" placeholder="Ask about today's signals, a stock, a strategy, a decision…"></textarea>
          <div class="row"><label class="switch"><input type="checkbox" id="adocs"><span></span>Use my documents</label><span class="spacer"></span><button class="btn" id="aclr">Clear</button><button class="btn primary" id="asend">Ask</button></div></div>`;
      const box = $("#chat", el);
      const draw = () => { box.innerHTML = askChat.length ? askChat.map((x) => bubble(x.role, x.role === "user" ? esc(x.content) : md(x.content) + hitsHtml(x.hits, "D") + (x.parts && x.parts.length ? `<details class="srcs"><summary>Data used</summary><div class="parts"></div></details>` : ""))).join("") : `<div class="empty">Ask anything about what the agents stored.</div>`;
        $$(".msg.assistant", box).forEach((node, i) => { const x = askChat.filter((y) => y.role === "assistant")[i]; const p = $(".parts", node); if (p && x.parts) sections(p, x.parts); });
        box.scrollTop = box.scrollHeight; };
      const send = async (qq) => {
        const q = (qq || $("#aq", el).value).trim(); if (!q) return;
        $("#aq", el).value = ""; askChat.push({ role: "user", content: q }); draw(); box.insertAdjacentHTML("beforeend", bubble("assistant", spinner("Reading the agents' records…"))); box.scrollTop = box.scrollHeight;
        try { const r = await api("ask", { question: q, history: askChat.slice(0, -1).map(({ role, content }) => ({ role, content })), docs: $("#adocs", el).checked });
          askChat.push({ role: "assistant", content: r.text, hits: r.hits, parts: r.parts }); }
        catch (e) { askChat.push({ role: "assistant", content: `Couldn't get an answer: ${e.message}` }); }
        draw();
      };
      $("#asend", el).addEventListener("click", () => send());
      $("#aq", el).addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); MX.remember("ask", $("#aq", el).value.trim()); send(); } });
      MX.suggest($("#aq", el), { recent: "ask", examples: m.examples || [] });
      $("#aclr", el).addEventListener("click", () => { askChat = []; draw(); });
      $$("[data-q]", el).forEach((x) => x.addEventListener("click", () => send(x.dataset.q)));
      draw();
    },
  });

  // ---------------- Documents ----------------
  let docChat = [];
  MX.page("documents", {
    title: "Documents", reloadOnJob: true,
    async render(el) {
      const d = await api("documents");
      if (!d.ready) {
        el.innerHTML = head("Documents") + note(`The documents library needs a few Python packages first (about 200 MB, once). Missing: ${esc(d.missing.join(", "))}.`) +
          `<button class="btn primary" id="dinst">Install document library packages</button>`;
        $("#dinst", el).addEventListener("click", () => startJob("install-ai"));
        return;
      }
      el.innerHTML = head("Documents", "Your own library of files, web pages, sites and news articles that the models answer from. Everything is read and indexed on this Mac.",
        `<label class="switch"><input type="checkbox" id="don" ${d.enabled ? "checked" : ""}><span></span>Use my documents</label>`) +
        `<p class="muted small" style="margin:-6px 0 10px">${esc(d.stats)}</p>` + (d.embed_missing ? note(`The search model isn't available: open Ollama and run <code>ollama pull ${esc(d.embed_missing)}</code> in Terminal (about 270 MB).`, "warn") : "") + `<div id="dt"></div>`;
      $("#don", el).addEventListener("change", async (e) => { await post("documents/enable", { on: e.target.checked }, "Saved"); MX.render(); });
      tabs($("#dt", el), ["Ask", "Add", `Library (${d.library.length})`, "News articles"], async (i, b) => {
        if (i === 0) {
          if (!d.enabled) { b.innerHTML = note("The library is switched off. Turn on Use my documents above."); return; }
          b.innerHTML = `<div class="panel"><div id="dqf"></div></div><div class="chat card" id="dchat"></div>
            <div class="composer"><textarea class="in" id="dq" rows="2" placeholder="Ask about your documents, e.g. 'What order inflow did HAL guide for FY27?'"></textarea><div class="row"><span class="spacer"></span><button class="btn" id="dclr">Clear</button><button class="btn primary" id="dsend">Ask</button></div></div>`;
          const f = form($("#dqf", b), [{ k: "kinds", label: "Only these kinds", type: "multi", options: d.kinds }, { k: "sources", label: "Only these sources", type: "multi", options: d.library.map((x) => [x.source_id, String(x.title).slice(0, 70)]) },
            { k: "symbol", label: "Only passages naming", type: "text", placeholder: "e.g. HAL" }]);
          const box = $("#dchat", b);
          const draw = () => { box.innerHTML = docChat.length ? docChat.map((x) => bubble(x.role, x.role === "user" ? esc(x.content) : md(x.content) + hitsHtml(x.hits))).join("") : `<div class="empty">Ask a question about your documents.</div>`; box.scrollTop = box.scrollHeight; };
          const send = async () => { const q = $("#dq", b).value.trim(); if (!q) return; $("#dq", b).value = ""; docChat.push({ role: "user", content: q }); draw();
            box.insertAdjacentHTML("beforeend", bubble("assistant", spinner("Searching your documents…")));
            const v = f.values(); const r = await api("documents/ask", { question: q, history: docChat.slice(0, -1).map(({ role, content }) => ({ role, content })), ...v }).catch((e) => ({ text: `Couldn't answer: ${e.message}`, hits: [] }));
            docChat.push({ role: "assistant", content: r.text, hits: r.hits }); draw(); };
          $("#dsend", b).addEventListener("click", send);
          $("#dq", b).addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); MX.remember("docs", $("#dq", b).value.trim()); send(); } });
          MX.suggest($("#dq", b), { recent: "docs" });
          $("#dclr", b).addEventListener("click", () => { docChat = []; draw(); });
          draw();
        } else if (i === 1) {
          b.innerHTML = `<p class="muted small">Large folders and websites run in the background; progress is in the bar at the top.</p><div class="grid g2">
            <div class="card"><div class="ct"><h2>Files</h2></div><input type="file" id="af" multiple accept=".pdf,.docx,.doc,.txt,.md,.html,.htm,.rtf" class="in"><p class="muted small">PDF (with selectable text), Word, .txt, .md, .html</p><button class="btn" id="afb">Add files</button></div>
            <div class="card"><div class="ct"><h2>Web pages</h2></div><textarea class="in" id="au" rows="3" placeholder="One link per line (just that page)"></textarea><button class="btn" id="aub" style="margin-top:8px">Add pages</button></div>
            <div class="card"><div class="ct"><h2>A folder on this Mac</h2></div><input class="in" id="afo" placeholder="/Users/you/Documents/Research"><p class="muted small">Every readable file inside, including subfolders. The files stay where they are.</p><button class="btn" id="afob">Add folder</button></div>
            <div class="card"><div class="ct"><h2>A whole website (or section)</h2></div><input class="in" id="as" placeholder="https://www.company.com/investors/">
              <div class="row" style="margin-top:8px"><div class="fld narrow"><label>Page limit</label><input class="in" type="number" id="asl" value="${d.site_page_limit}" min="1" max="5000"></div><label class="switch"><input type="checkbox" id="ass" checked><span></span>Stay in this section</label></div>
              <p class="muted small">~2–3 seconds per page; robots.txt is obeyed.</p><button class="btn" id="asb">Crawl website</button></div></div>
            <div class="row section">${d.queued ? `<span class="muted">Waiting in the queue: ${d.queued} task(s)</span>${d.lane_busy ? "" : `<button class="btn" id="aproc">Process queue</button>`}` : ""}</div>`;
          const add = async (body) => { const r = await post("documents/add", body); toast(r.message); MX.refreshStatus(true); };
          $("#afb", b).addEventListener("click", async () => {
            const files = [...$("#af", b).files]; if (!files.length) return toast("Choose files first", true);
            if (files.some((f) => f.size > 60e6)) return toast("Files over 60 MB: use Add folder instead", true);
            const enc = await Promise.all(files.map((f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res({ name: f.name, data: String(r.result).split(",")[1] }); r.onerror = rej; r.readAsDataURL(f); })));
            add({ kind: "files", files: enc });
          });
          $("#aub", b).addEventListener("click", () => add({ kind: "urls", urls: $("#au", b).value }));
          $("#afob", b).addEventListener("click", () => add({ kind: "folder", path: $("#afo", b).value }));
          $("#asb", b).addEventListener("click", () => add({ kind: "site", url: $("#as", b).value, limit: +$("#asl", b).value, same: $("#ass", b).checked }));
          $("#aproc", b)?.addEventListener("click", async () => { await post("documents/process", {}, "Started"); MX.refreshStatus(true); });
        } else if (i === 2) {
          if (!d.library.length) { b.innerHTML = `<div class="empty">Nothing added yet.</div>`; return; }
          b.innerHTML = `<div id="lt"></div><div class="card section"><div class="row"><select class="in grow" id="lp">${d.library.map((x) => `<option value="${esc(x.source_id)}">${esc(String(x.title).slice(0, 90))}</option>`).join("")}</select>
            <button class="btn" id="lsum" title="Reads the whole source in parts and combines them">Summarise</button><button class="btn" id="lrm" title="Deletes its passages (and the copy of a dropped file). Your own folders are never touched.">Remove</button></div><div id="lsx"></div></div>`;
          $("#lt", b).appendChild(dataTable(d.library, [["enabled", "Use", (v, r) => `<input type="checkbox" data-src="${esc(r.source_id)}" ${v ? "checked" : ""}>`], ["title", "Title", "long"], ["kind", "Kind"], ["chunks", "Passages", "int"],
            ["pages", "Pages", "int"], ["status", "Status", "status"], ["note", "Note"], ["updated", "Updated"], ["location", "File / link"]], { per: 100 }));
          b.addEventListener("change", async (e) => { const c = e.target.closest("[data-src]"); if (c) await post("documents/source", { id: c.dataset.src, action: "enable", on: c.checked }, c.checked ? "Included" : "Left out of answers"); });
          const showSum = async () => { const s = await api(`documents/summary?${qs({ id: $("#lp", b).value })}`); $("#lsx", b).innerHTML = s.text ? `<h3>Summary</h3>${md(s.text)}` : ""; };
          $("#lp", b).addEventListener("change", showSum); showSum();
          $("#lsum", b).addEventListener("click", async () => { const r = await post("documents/source", { id: $("#lp", b).value, action: "summarise" }); toast(r.message); MX.refreshStatus(true); });
          $("#lrm", b).addEventListener("click", async () => { if (!confirm("Remove this source from the library?")) return; await post("documents/source", { id: $("#lp", b).value, action: "remove" }, "Removed"); MX.render(); });
        } else {
          const lab = { ok: "full text", paywall: "paywalled", blocked: "not allowed (robots.txt)", short: "too short", error: "failed", redirect: "Google link" };
          b.innerHTML = `<p>Reads the full text of stored news headlines where the site allows it, adds it to the library (kind <b>News article</b>) and re-links each headline to the stocks the article is actually about.</p>
            <p class="muted small">robots.txt is checked for every page; one request per site every ~12 seconds; paywalled articles are recorded, never bypassed; Google News links aren't followed. Expect full text for roughly a third to half of headlines.</p>
            ${Object.keys(d.articles).length ? `<p>${Object.entries(d.articles).map(([k, v]) => `<b>${num(v, 0)}</b> ${esc(lab[k] || k)}`).join(" · ")}</p>` : ""}
            <div class="row"><select class="in" id="an" style="max-width:220px"><option value="100">100 articles</option><option value="300" selected>300 articles (30–60 min)</option><option value="1000">1000 articles</option></select>
            <button class="btn primary" id="ago" ${d.articles_busy ? "disabled" : ""}>Read article text</button></div>`;
          $("#ago", b).addEventListener("click", () => startJob("news-articles", [`max=${$("#an", b).value}`]));
        }
      }, "documents");
    },
  });

  // ---------------- Strategy Lab ----------------
  async function labHome(el) {
    const d = await api("lab");
    el.innerHTML = head("Strategy Lab", "Your own workspace for testing strategy ideas. The daily agents never read it, until you send a strategy that passes to the Strategy agent and approve it.",
      `<a class="btn primary" href="#/lab/charts">Chart workspace</a><a class="btn" href="#/lab/compare">Compare runs</a><a class="btn" href="#/lab/rules">Rule language</a>`) +
      `<div class="card section"><div class="ct"><h2>New strategy</h2></div><div id="lnew"></div><div class="row" style="margin-top:10px"><button class="btn primary" id="lcreate">Create</button></div></div>
      <div id="llist"></div>`;
    const f = form($("#lnew", el), [{ k: "template", label: "Start from", type: "select", options: d.templates, value: d.templates[0] }, { k: "name", label: "Name", type: "text", placeholder: "New strategy" }]);
    $("#lcreate", el).addEventListener("click", async () => { const r = await post("lab/create", f.values()); location.hash = `#/lab/build/${r.id}`; });
    if (!d.strategies.length) { $("#llist", el).innerHTML = note("No strategies yet. Create one above: start from a template and change the rules, or write your own."); return; }
    $("#llist", el).innerHTML = `<div class="sh"><h3>Your strategies</h3></div>` + d.strategies.map((s) => `<div class="card section lab-row">
      <div class="grow"><b>${esc(s.name)}</b><div class="muted small">${s.mode === "rotation" ? "Rotation (rank and hold)" : "Signal (buy on a rule, exit on rules)"} · updated ${esc(String(s.updated).slice(0, 16))}</div></div>
      <div>${s.last_run ? (s.passes ? `<span class="tag pos">Passes design + validation</span>` : `<span class="tag neg">${esc(s.first_fail || "Fails")}</span>`) : `<span class="muted">Not tested yet</span>`}</div>
      <div class="r"><div>Design edge <b>${s.design_excess == null ? "—" : MX.pct(s.design_excess, 2)}</b></div><div class="muted small">t ${s.design_t == null ? "—" : num(s.design_t, 1)}</div></div>
      <div class="r"><div>Versions tested <b>${s.versions}</b></div><div class="muted small">${s.runs} run(s)</div></div>
      <div class="row"><a class="btn small" href="#/lab/build/${encodeURIComponent(s.id)}">Open</a>${s.last_run ? `<a class="btn small" href="#/lab/results/${encodeURIComponent(s.id)}">Results</a>` : ""}</div></div>`).join("") +
      (d.archived.length ? `<details class="section"><summary>Archived (${d.archived.length})</summary>${d.archived.map((s) => `<div class="row" style="margin-top:6px">${esc(s.name)}<span class="spacer"></span><button class="btn small" data-unarch="${esc(s.id)}">Restore</button></div>`).join("")}</details>` : "");
    $$("[data-unarch]", el).forEach((x) => x.addEventListener("click", async () => { await post("lab/manage", { id: x.dataset.unarch, action: "archive", on: false }, "Restored"); MX.render(); }));
  }

  function labForm(host, s) {
    const d = JSON.parse(JSON.stringify(s.definition));
    const u = d.universe, ex = d.exits, pf = d.portfolio, ro = d.rotation;
    host.innerHTML = `<div class="card section"><div class="ct"><h2>1 · Idea</h2></div><div id="f1"></div></div>
      <div class="card section"><div class="ct"><h2>2 · Which stocks</h2></div><div id="f2"></div><div id="f2w"></div></div>
      <div id="fmode"></div><div id="fmsg"></div>
      <div class="row section"><button class="btn" id="lsave">Save</button><button class="btn" id="lcheck" title="Counts signals per year and lists today's, in seconds">Quick check</button>
        <button class="btn primary" id="lrun">▶ Save and run full test</button><span class="spacer"></span><button class="btn" id="ldup">Duplicate</button><button class="btn" id="larc">Archive</button></div>
      <div id="lprog"></div><div id="lcheckout"></div>`;
    let mode = d.mode || "signal";
    const f1 = form($("#f1", host), [{ k: "name", label: "Name", type: "text", value: d.name }, { k: "notes", label: "Your strategy in words (for you; the rules below are what gets tested)", type: "textarea", value: d.notes, rows: 2 },
      { k: "mode", label: "Type", type: "radio", options: [["signal", "Signal: buy on a rule, exit on rules"], ["rotation", "Rotation: rank stocks, hold the top N, rebalance"]], value: mode }], () => { const m = f1.values().mode; if (m !== mode) { mode = m; drawMode(); } check(); });
    const f2 = form($("#f2", host), [{ k: "source", label: "Universe", type: "radio", options: [["nifty500", "Today's Nifty 500"], ["pit", "Point-in-time (removes survivorship bias)"]], value: u.source,
        help: "Today's Nifty 500 only has stocks that survived into today's index, which flatters results. Point-in-time takes the top N stocks by traded value as they were at each date." },
      { k: "min_price", label: "Min price ₹", type: "number", value: u.min_price, min: 0, step: 10 }, { k: "min_turnover_cr", label: "Min avg traded value ₹ cr/day", type: "number", value: u.min_turnover_cr, min: 0, step: 5 },
      { k: "pit_n", label: "Point-in-time size", type: "number", value: u.pit_n, min: 50, max: 1000, step: 50 },
      { k: "sectors", label: "Only these sectors (empty = all)", type: "multi", options: s.sectors, value: u.sectors, wide: true },
      { k: "exclude_flagged", label: "Skip stocks with data errors", type: "toggle", value: u.exclude_flagged }], () => { warnPit(); check(); });
    const warnPit = () => { $("#f2w", host).innerHTML = f2.values().source === "pit" && !s.has_broad ? note("For a real point-in-time test, download all NSE stocks once (Data health → Download all NSE stocks, 3–4 hours). Until then this ranks only today's Nifty 500 stocks, which removes only part of the bias.", "warn") : ""; };
    warnPit();
    let fm = null;
    const snip = (id, list, label) => `<div class="row" style="margin-top:6px"><select class="in grow" id="sn-${id}">${list.map(([l, c]) => `<option value="${esc(c)}">${esc(l)}</option>`).join("")}</select><button class="btn small" data-snip="${id}">${esc(label)}</button></div>`;
    const drawMode = () => {
      const box = $("#fmode", host);
      if (mode === "signal") {
        box.innerHTML = `<div class="card section"><div class="ct"><h2>3 · Entry</h2></div><div id="f3"></div>${snip("entry", s.snippets, "Add condition")}${snip("market_filter", s.market_snippets, "Add market condition")}</div>
          <div class="card section"><div class="ct"><h2>4 · Exit</h2></div><div id="f4"></div></div><div class="card section"><div class="ct"><h2>5 · Money</h2></div><div id="f5"></div></div>`;
        const a = form($("#f3", box), [{ k: "entry", label: "Buy when (at the close)", type: "textarea", value: d.entry, rows: 3, placeholder: "close > sma(200) and rsi(3) < 15" },
          { k: "market_filter", label: "Market filter (optional): only take signals when", type: "text", value: d.market_filter, placeholder: "nifty > sma(nifty, 200)", wide: true },
          { k: "rank_by", label: "Rank same-day signals by (optional)", type: "text", value: d.rank_by, placeholder: "vol_ratio(20)" }, { k: "rank_desc", label: "Highest first", type: "toggle", value: d.rank_desc },
          { k: "entry_price", label: "Buy at", type: "radio", options: [["next_open", "Next day's open (realistic)"], ["close", "Same day's close (act at 3:25 pm)"]], value: d.entry_price }], () => check());
        const b = form($("#f4", box), [{ k: "stop", label: "Stop loss", type: "select", options: [["atr", "ATR multiple"], ["pct", "% below entry"], ["expr", "Formula (price)"], ["none", "No stop"]], value: ex.stop },
          { k: "stop_value", label: "Stop value (× ATR, %, or formula like lowest(low, 10))", type: "text", value: ex.stop_value },
          { k: "target", label: "Target", type: "select", options: [["r", "R multiple (× stop distance)"], ["atr", "ATR multiple"], ["pct", "% above entry"], ["expr", "Formula (price)"], ["none", "No target"]], value: ex.target },
          { k: "target_value", label: "Target value", type: "text", value: ex.target_value }, { k: "trail_pct", label: "Trailing stop % (0 = off)", type: "number", value: ex.trail_pct, min: 0, max: 50, step: 0.5 },
          { k: "max_days", label: "Max days held", type: "number", value: ex.max_days, min: 1, max: 500 }, { k: "exit_when", label: "Also sell when (at the close, optional)", type: "text", value: ex.exit_when, placeholder: "close < sma(10)", wide: true }], () => check());
        const c = form($("#f5", box), [{ k: "max_positions", label: "Max positions at once", type: "number", value: pf.max_positions, min: 1, max: 100 }, { k: "cost_pct", label: "Round-trip cost %", type: "number", value: pf.cost_pct, min: 0, max: 3, step: 0.05 },
          { k: "capital", label: "Starting capital ₹", type: "number", value: pf.capital, min: 10000, step: 100000 }], () => check());
        fm = () => { const A = a.values(), B = b.values(), Cc = c.values(); const n = (x) => (/^-?\d+(\.\d+)?$/.test(String(x).trim()) ? Number(x) : x);
          return { entry: A.entry, market_filter: A.market_filter, rank_by: A.rank_by, rank_desc: A.rank_desc, entry_price: A.entry_price,
            exits: { stop: B.stop, stop_value: n(B.stop_value), target: B.target, target_value: n(B.target_value), trail_pct: B.trail_pct || 0, max_days: B.max_days, exit_when: B.exit_when },
            portfolio: { max_positions: Cc.max_positions, cost_pct: Cc.cost_pct, capital: Cc.capital }, rotation: ro }; };
      } else {
        box.innerHTML = `<div class="card section"><div class="ct"><h2>3 · Ranking</h2></div><div id="f3"></div>${snip("regime", s.market_snippets, "Add market condition")}</div><div class="card section"><div class="ct"><h2>4 · Money</h2></div><div id="f5"></div></div>`;
        const a = form($("#f3", box), [{ k: "score", label: "Score (rank stocks by this at each rebalance)", type: "text", value: ro.score, placeholder: "prev(ret(126), 21)", wide: true }, { k: "score_desc", label: "Highest first", type: "toggle", value: ro.score_desc },
          { k: "rebalance", label: "Rebalance", type: "radio", options: [["M", "Monthly"], ["W", "Weekly"]], value: ro.rebalance }, { k: "top_n", label: "Hold the top", type: "number", value: ro.top_n, min: 1, max: 200 },
          { k: "hold", label: "Hold each batch for (rebalances)", type: "number", value: ro.hold, min: 1, max: 12 },
          { k: "filter", label: "Only consider stocks where (optional)", type: "text", value: ro.filter, placeholder: "close > sma(200)", wide: true },
          { k: "regime", label: "Go to cash unless (market rule, optional)", type: "text", value: ro.regime, placeholder: "nifty > sma(nifty, 200)", wide: true }], () => check());
        const c = form($("#f5", box), [{ k: "cost_pct", label: "Round-trip cost %", type: "number", value: pf.cost_pct, min: 0, max: 3, step: 0.05 }, { k: "capital", label: "Starting capital ₹", type: "number", value: pf.capital, min: 10000, step: 100000 }], () => check());
        fm = () => { const A = a.values(), Cc = c.values();
          return { entry: d.entry, market_filter: d.market_filter, rank_by: d.rank_by, rank_desc: d.rank_desc, entry_price: d.entry_price, exits: ex,
            portfolio: { ...pf, cost_pct: Cc.cost_pct, capital: Cc.capital }, rotation: { rebalance: A.rebalance, top_n: A.top_n, hold: A.hold, score: A.score, score_desc: A.score_desc, filter: A.filter, regime: A.regime } }; };
      }
      $$("[data-snip]", box).forEach((x) => x.addEventListener("click", () => {
        const id = x.dataset.snip, code = $(`#sn-${id}`, box).value, inp = $(`#f-${id}`, box); if (!inp) return;
        inp.value = inp.value.trim() ? `${inp.value.trim()} and ${code}` : code; inp.dispatchEvent(new Event("input")); inp.dispatchEvent(new Event("change")); check();
      }));
    };
    const defn = () => { const a = f1.values(), b = f2.values(); return { name: a.name, notes: a.notes, mode, universe: { source: b.source, pit_n: b.pit_n, min_price: b.min_price, min_turnover_cr: b.min_turnover_cr, sectors: b.sectors, exclude_flagged: b.exclude_flagged }, ...fm() }; };
    let hard = [];
    const check = MX.debounce(async () => {
      const r = await api("lab/validate", { id: s.id, definition: defn() }).catch((e) => ({ errors: [e.message], infos: [], changed: true }));
      hard = r.errors;
      $("#fmsg", host).innerHTML = r.errors.map((e) => note(esc(e), "warn")).join("") + r.infos.map((e) => note(esc(e))).join("");
      $("#lsave", host).disabled = !!hard.length || !r.changed; $("#lcheck", host).disabled = !!hard.length; $("#lrun", host).disabled = !!hard.length;
    }, 400);
    drawMode(); check();
    $("#lsave", host).addEventListener("click", async () => { await post("lab/save", { id: s.id, definition: defn() }, "Saved"); MX.render(); });
    $("#lcheck", host).addEventListener("click", async () => { const o = $("#lcheckout", host); o.innerHTML = spinner("Checking (the first time loads all prices, about 20 seconds)…");
      try { sections(o, (await api("lab/check", { definition: defn() })).sections); } catch (e) { o.innerHTML = note(esc(e.message), "warn"); } });
    $("#lrun", host).addEventListener("click", async () => {
      const r = await post("lab/run", { id: s.id, definition: defn() });
      $("#lrun", host).disabled = true;
      try { const res = await pollTask(r.task, (t) => ($("#lprog", host).innerHTML = card("Running the full test", `<div class="bar wide"><i style="width:${Math.round(100 * (t.frac || 0.02))}%"></i></div><p class="muted small">${esc(t.msg)} · ${MX.mmss(t.seconds)}</p>`)));
        location.hash = `#/lab/results/${encodeURIComponent(s.id)}/${res.run_id}`; }
      catch (e) { $("#lprog", host).innerHTML = note(esc(e.message), "warn"); $("#lrun", host).disabled = false; }
    });
    $("#ldup", host).addEventListener("click", async () => { const r = await post("lab/manage", { id: s.id, action: "duplicate" }); location.hash = `#/lab/build/${r.id}`; });
    $("#larc", host).addEventListener("click", async () => { if (!confirm("Archive this strategy?")) return; await post("lab/manage", { id: s.id, action: "archive", on: true }, "Archived"); location.hash = "#/lab"; });
  }

  async function labResults(el, sid, rid) {
    const runs = await api(`lab/runs?${qs({ id: sid })}`);
    if (!runs.length) { el.innerHTML = head("Results") + note("Not tested yet. Open the strategy and press Save and run full test.") + `<a class="btn" href="#/lab/build/${encodeURIComponent(sid)}">Open strategy</a>`; return; }
    rid = runs.some((r) => r[0] === rid) ? rid : runs[0][0];
    const r = await api(`lab/report?${qs({ run: rid })}`);
    el.innerHTML = head(esc(r.name), esc(r.caption), `<a class="btn" href="#/lab/build/${encodeURIComponent(sid)}">Edit rules</a><a class="btn" href="#/lab">All strategies</a>`) +
      `<div class="panel"><div id="rsel"></div></div>${r.notes ? `<blockquote class="muted">${esc(r.notes)}</blockquote>` : ""}
      <div class="note ${r.verdict[0] === "pos" ? "info" : "warn"}"><b>${esc(r.verdict[1])}</b>${r.search_note ? `<div class="small" style="margin-top:4px">${esc(r.search_note)}</div>` : ""}</div>
      <div class="row section">${r.revealed ? `<span class="muted small">${esc(r.reveals)}</span>` : `<button class="btn" id="rrev" title="Shows 2024 onwards. Recorded: the Lab counts how often you look.">Reveal holdout</button>`}
        ${r.passes ? `<button class="btn primary" id="rsend">Send to Strategy agent</button>` : ""}<span class="spacer"></span><button class="btn" id="rjson">Download rules (JSON)</button></div>
      <div id="rsec"></div>${r.trades ? `<div class="card section"><div class="ct"><h2>Trades (${num(r.trades, 0)})</h2><span class="aside">click a trade to see it on the chart</span></div><div id="rtr"></div><div id="rtc"></div></div>` : ""}`;
    form($("#rsel", el), [{ k: "run", label: "Test run", type: "select", options: runs.map((x) => [x[0], x[1]]), value: rid, wide: true }], () => { location.hash = `#/lab/results/${encodeURIComponent(sid)}/${$("#f-run", el).value}`; });
    $("#rrev", el)?.addEventListener("click", async () => { if (!confirm("Reveal the holdout (2024 onwards)? Every look after a change weakens it as a test.")) return; await post("lab/reveal", { run: rid }); MX.render(); });
    $("#rsend", el)?.addEventListener("click", async () => { const x = await post("lab/send", { strategy: sid, run: rid }); toast(x.message); });
    $("#rjson", el).addEventListener("click", () => download(JSON.stringify(r.definition, null, 2), `${sid}_${rid}.json`, "application/json"));
    sections($("#rsec", el), r.sections);
    if (r.trades) tradeTable($("#rtr", el), $("#rtc", el), rid, r.periods);
  }

  // trades are paged, sorted and filtered on the server: a run can have thousands
  function tradeTable(host, chart, rid, periods) {
    const COLS = [["signal_date", "Signal", "date"], ["symbol", "Stock", "symonly"], ["sector", "Sector"], ["entry", "Entry", "num2"], ["exit", "Exit", "num2"], ["reason", "Exit reason"],
      ["days", "Days", "int"], ["net_pct", "Net", "spct2"], ["control_pct", "Random", "spct2"], ["taken", "Taken", "bool"], ["period", "Period"]];
    const st = { sort: "signal_date", dir: "desc", page: 0 };
    host.innerHTML = `<div class="panel"><div id="tf"></div></div><div class="dt"><div class="dt-tools"><span class="muted" id="tn"></span><span class="spacer"></span><button class="btn small" id="tcsv">Download CSV</button></div><div class="scroll" id="tb"></div>
      <div class="dt-pages"><button class="btn small" id="tp">‹</button><span class="muted" id="tpg"></span><button class="btn small" id="tx">›</button></div></div>`;
    const f = form($("#tf", host), [{ k: "periods", label: "Period", type: "multi", options: periods, value: periods.map((x) => x[0]) }, { k: "taken", label: "Only trades the portfolio took", type: "toggle" },
      { k: "symbol", label: "Stock", type: "text", placeholder: "e.g. HAL" }], () => { st.page = 0; load(); });
    const params = () => { const v = f.values(); return { run: rid, periods: v.periods.join(","), taken: v.taken ? "true" : "", symbol: v.symbol, sort: st.sort, dir: st.dir }; };
    const load = async () => {
      const d = await api(`lab/trades?${qs({ ...params(), page: st.page })}`);
      $("#tn", host).textContent = `${num(d.total, 0)} trades`; $("#tpg", host).textContent = `page ${d.page + 1} of ${d.pages}`;
      $("#tp", host).disabled = d.page <= 0; $("#tx", host).disabled = d.page >= d.pages - 1;
      $("#tb", host).innerHTML = `<table class="tbl"><thead><tr>${COLS.map((c) => `<th data-k="${c[0]}" class="sortable ${/^(int|num|spct)/.test(c[2] || "") ? "r" : ""}">${esc(c[1])}${st.sort === c[0] ? (st.dir === "asc" ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
        <tbody>${d.rows.map((t, i) => `<tr class="click" data-i="${i}">${COLS.map((c) => `<td class="${/^(int|num|spct)/.test(c[2] || "") ? "r" : ""}">${MX.fmt(c[2] || "text", t[c[0]], t)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      $$("th[data-k]", host).forEach((th) => th.addEventListener("click", () => { st.dir = st.sort === th.dataset.k && st.dir === "desc" ? "asc" : "desc"; st.sort = th.dataset.k; st.page = 0; load(); }));
      $$("tr[data-i]", host).forEach((tr) => tr.addEventListener("click", async (e) => { if (e.target.closest("a")) return; const t = d.rows[+tr.dataset.i];
        const x = await api(`lab/trade?${qs({ run: rid, symbol: t.symbol, date: t.signal_date })}`);
        chart.innerHTML = `<p style="margin:14px 0 6px"><b>${esc(x.title)}</b></p><div class="ch"></div>`; MXCharts.draw($(".ch", chart), x.chart); }));
    };
    $("#tp", host).addEventListener("click", () => { st.page--; load(); });
    $("#tx", host).addEventListener("click", () => { st.page++; load(); });
    $("#tcsv", host).addEventListener("click", async () => download((await api(`lab/trades?${qs({ ...params(), csv: "true" })}`)).csv, `trades_${rid}.csv`, "text/csv"));
    load();
  }

  MX.page("lab", {
    title: "Strategy Lab",
    async render(el, arg) {
      const [sub, id, rid] = String(arg || "").split("/");
      if (sub === "build" && id) {
        const s = await api(`lab/strategy?${qs({ id })}`);
        el.innerHTML = head(esc(s.name), `Saved ${esc(String(s.updated).slice(0, 16))} · version ${esc(s.version)} · ${s.versions_tested} version(s) tested`,
          `<a class="btn" href="#/lab/results/${encodeURIComponent(id)}">Results</a><a class="btn" href="#/lab">All strategies</a>`) + `<div id="lf"></div>`;
        return labForm($("#lf", el), s);
      }
      if (sub === "results" && id) return labResults(el, id, rid);
      if (sub === "compare") {
        el.innerHTML = head("Compare test runs", "", `<a class="btn" href="#/lab">All strategies</a>`) + `<div class="panel"><div id="cf"></div></div><div id="cs"></div>`;
        const d0 = await api("lab/compare");
        if (!d0.options.length) return sections($("#cs", el), d0.sections);
        const f = form($("#cf", el), [{ k: "runs", label: "Runs", type: "multi", options: d0.options, value: d0.picked, wide: true }], async () => sections($("#cs", el), (await api(`lab/compare?${qs({ runs: f.values().runs.join(",") })}`)).sections));
        return sections($("#cs", el), d0.sections);
      }
      if (sub === "charts") return MX.workspacePage(el, id ? decodeURIComponent(id) : null);
      if (sub === "rules") { el.innerHTML = head("Rule language", "", `<a class="btn" href="#/lab">All strategies</a>`) + `<div id="rs"></div>`; return sections($("#rs", el), (await api("lab/rules")).sections); }
      return labHome(el);
    },
  });
})();
