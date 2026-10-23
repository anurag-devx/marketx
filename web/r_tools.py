"""API: Search (incl. deep search, exposure list, web settings), Ask, Documents, Strategy Lab."""
import base64, json
import numpy as np, pandas as pd
from core import config as C, jobs
from web.api import route, cached, _mtime, prices, universe, UserError
from web.r_agents import table
from web import tasks

NEWS_COLS = [["published", "When", "dt"], ["source", "Source"], ["title", "Headline", "long"], ["link", "", "link"]]


def _stores():
    from core import news as N, announcements as A
    return cached("search_stores", (_mtime(N.STORE), _mtime(A.STORE)), lambda: (N.load_store(), A.load()))


def _info():
    from core import company_info as CI
    return cached("cinfo", _mtime(CI.FILE), CI.load)


def _lib_hits(query, symbol=None, k=8):
    try:
        from core import library as L
        if not L.options()["enabled"]:
            return dict(kind="note", text="Documents library is switched off (Documents page).")
        hits = L.search(query, k=k, symbol=symbol)
        if hits.empty and symbol:
            hits = L.search(query, k=k)
    except Exception:
        return dict(kind="note", text="Documents library not set up yet (Settings → AI models → install packages).")
    if hits.empty:
        return dict(kind="note", text="No passages in your documents or article text.")
    return dict(kind="md", title="Documents and articles", text="\n\n".join(
        f"**{r.title}**" + (f" · {r.where}" if r.where else "") + (f" · {str(r.published)[:10]}" if r.published else "")
        + (f" · [open]({r.location})" if str(r.location).startswith("http") else "") + f"\n\n*{r.text[:500]}{'…' if len(r.text) > 500 else ''}*"
        for r in hits.itertuples()))


def _moves(sym):
    px = prices()
    p = px[px.symbol == sym].sort_values("date") if px is not None else None
    if p is None or len(p) < 2:
        return None
    c = p.close.values
    ret = lambda n: 100 * (c[-1] / c[-1 - n] - 1) if len(c) > n else None
    return dict(date=p.date.iat[-1], close=c[-1], d1=ret(1), m1=ret(21), m3=ret(63), y1=ret(252))


def _pc(v, d=1):
    return "—" if v is None or not np.isfinite(v) else f"{v:+.{d}f}%"


SENS = {"per_1sd_pct": "snum2", "t": "num1"}


def _stock_view(sym, info):
    from core import search as S, macro as M
    news, ann = _stores()
    row = info[info.symbol == sym]
    r0 = row.iloc[0] if len(row) else None
    g = lambda k: (r0[k] if r0 is not None and isinstance(r0[k], str) else "")
    name, sector, industry, nind = (r0.company if r0 is not None else sym), g("sector"), g("industry"), g("nifty_industry")
    head = [dict(kind="note", text=" · ".join(dict.fromkeys(x for x in [sector, industry, g("basic_industry")] if x)) or "—")]
    mv = _moves(sym)
    if mv:
        items = [dict(label=f"Close ({mv['date']:%d %b})", value=f"₹{mv['close']:,.2f}", delta=_pc(mv["d1"], 2), tone="up" if (mv["d1"] or 0) > 0 else "down"),
                 dict(label="1 month", value=_pc(mv["m1"])), dict(label="3 months", value=_pc(mv["m3"])), dict(label="1 year", value=_pc(mv["y1"]))]
        head.append(dict(kind="kpis", items=items))
    peers = list(info[(info.industry == industry) & (info.symbol != sym)].symbol) if industry else []
    fil = ann[ann.symbol == sym].sort_values("published", ascending=False) if len(ann) else ann
    sn = S.stock_news(sym, news)
    secn = S.sector_news(nind or sector, industry, peers, news, days=30, exclude=sym)
    links, active = S.macro_links(sym, sector, nind, peers=peers)
    fcols = [["published", "When", "dt"], ["category", "Category"], ["subject", "Subject"], ["text", "Text", "long"], ["link", "", "link"]]
    over = [dict(kind="grid", cols=2, items=[
        table("Latest filings", fil.head(5)[["published", "category", "subject", "link"]] if len(fil) else fil, {"published": "dt", "link": "link"}, tools=False, empty="None stored."),
        table("Latest news", sn.head(5)[["published", "title", "source", "link"]] if len(sn) else sn, {"published": "dt", "title": "long", "link": "link"}, tools=False, empty="None."),
        table("Sector and industry, last 30 days", secn.head(5)[["published", "title", "why", "link"]] if len(secn) else secn, {"published": "dt", "title": "long", "link": "link"}, tools=False, empty="None."),
        table("Geopolitics and macro reaching this stock (7 days)", active[active.linked][["theme", "headlines", "link", "reaches_stock_via"]].head(5) if len(active) else active,
              {"reaches_stock_via": "long"}, tools=False, empty="No theme in the last 7 days reaches this stock by name, sector, sensitivity or your list.")])]
    hint = []
    if not (len(links) and (links.source == "computed").any()):
        hint.append("the macro study hasn't run, so links through crude, USD/INR, yields etc. are missing")
    if not len(S.exposures()):
        hint.append("your exposure list is empty (Exposure list tab: you can start from suggestions)")
    cal = M.calendar()
    up = cal[cal.date >= pd.Timestamp.now().normalize()].sort_values("date").head(10) if len(cal) else cal
    nxt = fil[fil.category.astype(str).str.contains("meeting|Results", case=False, regex=True)].head(3) if len(fil) else fil
    tabs = [("Overview", over),
            (f"NSE filings ({len(fil)})", [dict(kind="table", rows=fil[["published", "category", "subject", "text", "link"]] if len(fil) else fil, columns=fcols, csv=f"filings_{sym}", empty="No filings stored.")]),
            (f"News ({len(sn)})", [dict(kind="table", rows=sn[["published", "source", "event_type", "tone", "title", "link"]] if len(sn) else sn, csv=f"news_{sym}", empty="No headlines naming this stock.",
                                        columns=[["published", "When", "dt"], ["source", "Source"], ["event_type", "Type"], ["tone", "Tone"], ["title", "Headline", "long"], ["link", "", "link"]])]),
            (f"Sector and industry ({len(secn)})", [dict(kind="note", text=f"Headlines naming its industry peers ({len(peers)} stocks in {industry or sector}) or about the sector in general."),
                                                    table(None, secn[["published", "why", "title", "source", "link"]] if len(secn) else secn, {"published": "dt", "title": "long", "link": "link"}, empty="Nothing in the last 30 days.")]),
            (f"Macro and geopolitics ({len(active)})", ([dict(kind="note", tone="warn", text="Links are partial: " + "; ".join(hint) + ".")] if hint else [])
             + [table("Themes in the last 7 days and how they reach this stock", active.drop(columns="linked") if len(active) else active, {"reaches_stock_via": "long", "example": "long"},
                      empty="No themed headlines in the last 7 days.", note="strong = named in the headlines, or through a factor the stock / its sector is measurably sensitive to, or your list · indirect = only its sector or peers appear"),
                table("Exposures: computed sensitivity and your list", links, SENS, empty="No sensitivities yet (Macro → Run macro study) and nothing in your exposure list.")]),
            ("Documents and articles", [_lib_hits(name.split(" Ltd")[0], symbol=sym)]),
            ("Upcoming", [table("Your calendar", up, {"date": "date"}, tools=False, empty="No upcoming events in your calendar (Macro → Calendar)."),
                          table("Recent board-meeting / results filings", nxt[["published", "subject", "text"]] if len(nxt) else nxt, {"published": "dt", "text": "long"}, tools=False, empty="None.")])]
    return dict(title=f"{name} ({sym})", symbol=sym, head=head, tabs=[dict(name=n, sections=s) for n, s in tabs])


def _sector_view(sector, info):
    from core import search as S
    news, _ = _stores()
    members = info[(info.sector == sector) | (info.industry == sector) | (info.basic_industry == sector) | (info.nifty_industry == sector)]
    secs = []
    px = prices()
    if px is not None and len(members):
        last = px[px.symbol.isin(members.symbol)].sort_values("date").groupby("symbol").tail(22)
        mv = last.groupby("symbol").close.apply(lambda c: 100 * (c.iat[-1] / c.iat[-2] - 1) if len(c) > 1 else np.nan).rename("last_day_pct")
        m1 = last.groupby("symbol").close.apply(lambda c: 100 * (c.iat[-1] / c.iat[0] - 1) if len(c) > 1 else np.nan).rename("month_pct")
        tab = members[["symbol", "company", "industry"]].set_index("symbol").join(mv).join(m1).reset_index().sort_values("last_day_pct", ascending=False)
        secs.append(dict(kind="kpis", items=[dict(label="Stocks", value=str(len(tab))), dict(label="Median last day", value=_pc(tab.last_day_pct.median(), 2)),
                                             dict(label="Median 1 month", value=_pc(tab.month_pct.median()))]))
        secs.append(dict(kind="table", title="Stocks", rows=tab, columns=[["symbol", "Stock", "sym"], ["industry", "Industry"], ["last_day_pct", "Last day", "spct2"], ["month_pct", "1 month", "spct1"]]))
    secn = S.sector_news(sector, sector, list(members.symbol), news, days=30)
    secs.append(table(f"News, last 30 days ({len(secn)})", secn[["published", "why", "title", "source", "link"]] if len(secn) else secn, {"published": "dt", "title": "long", "link": "link"}, empty="None."))
    sens = S._read_csv(C.RESULTS / "macro" / "sens_3y.csv")
    if len(sens):
        secs.append(table("Macro sensitivity (last 3 years)", sens[sens.sector == sector].drop(columns="sector"), SENS, tools=False, empty="Not computed for this group."))
    mine = S.exposures()
    mine = mine[mine.target.astype(str).str.lower() == sector.lower()]
    if len(mine):
        secs.append(table("Your exposure list", mine, tools=False))
    secs.append(_lib_hits(sector))
    return dict(title=sector, sections=secs)


def _theme_view(theme):
    from core import search as S
    v = S.theme_view(theme)
    h = v["headlines"]
    return dict(title=v["label"], sections=[
        dict(kind="note", text="Acts on markets mainly through: " + (", ".join(v["factors"]) or "—")),
        dict(kind="grid", cols=2, items=[table("Sectors most sensitive to those factors (computed, 3 years)", v["sectors"], SENS, tools=False, empty="Run the macro study first."),
                                         table("Your exposure list for this theme", v["mine"], tools=False, empty="Nothing yet: add rows in Exposure list.")]),
        table("Most sensitive stocks", v["stocks"], SENS, tools=False, empty="—"),
        table(f"Headlines, last 30 days ({len(h)})", h[["published", "title", "source", "link"]] if len(h) else h, {"published": "dt", "title": "long", "link": "link"}, empty="None stored.")])


def _text_view(q, kinds=None, days=365):
    from core import search_index as SI
    hits = SI.search(q, k=40, kinds=kinds or None, since=pd.Timestamp.now() - pd.Timedelta(days=days))
    secs = []
    if len(hits):
        h = hits.assign(published=pd.to_datetime(hits.published, errors="coerce"))
        secs.append(dict(kind="table", title=f"Headlines and filings ({len(h)})", rows=h[["published", "kind", "title", "symbols", "source", "link"]],
                         columns=[["published", "When", "dt"], ["kind", "Kind"], ["title", "Text", "long"], ["symbols", "Stocks"], ["source", "Source"], ["link", "", "link"]],
                         note="Plain word matching: build the search index (Data health → History downloads) to also find matches by meaning." if hits.score.isna().all() else None))
    else:
        secs.append(dict(kind="note", text="No headlines or filings match."))
    secs.append(_lib_hits(q))
    return secs


@route("GET", "search/query")
def search_query(p):
    from core import search as S, macro as M
    q = p.get("q", "").strip()
    if not q:
        raise UserError("type something to search")
    info = _info()
    r = S.resolve(q, info)
    chips = ([f"stock: {s}" for s in r["stocks"]] + [f"sector: {s}" for s in r["sectors"]] + [f"industry: {s}" for s in r["industries"]]
             + [f"theme: {M.THEMES[t][0]}" for t in r["themes"]])
    out = dict(kind=r["kind"], understood=" · ".join(chips) if chips else "free text")
    if r["kind"] == "stock":
        names = dict(zip(info.symbol, info.company))
        sym = p.get("sym") if p.get("sym") in r["stocks"] else r["stocks"][0]
        out["choices"] = [[s, f"{s} ({str(names.get(s, ''))[:28]})"] for s in r["stocks"]] if len(r["stocks"]) > 1 else []
        out["view"] = _stock_view(sym, info)
    elif r["kind"] == "sector":
        out["view"] = _sector_view((r["sectors"] or r["industries"])[0], info)
    elif r["kind"] == "theme":
        out["view"] = _theme_view(r["themes"][0])
    else:
        out["view"] = dict(title=f"“{q}”", sections=_text_view(q, (p.get("kinds") or "news,filing").split(","), int(p.get("days", 365))))
    return out


@route("GET", "search/text")
def search_text(p):
    q = p.get("q", "").strip()
    if not q:
        raise UserError("type something to search")
    return dict(sections=_text_view(q, [k for k in (p.get("kinds") or "news,filing").split(",") if k in ("news", "filing")], int(p.get("days", 365))))


# ---------------- deep search ----------------
@route("GET", "deep")
def deep(_):
    from core import llm, websearch as WS, deepsearch as D
    ok, msg = llm.status("search")
    wok, wmsg = WS.status()
    return dict(model_ok=ok, model=msg, web=wmsg, running=tasks.running("deep"),
                history=[dict(file=h["file"], when=h["when"], question=h["question"]) for h in D.history()])


@route("GET", "deep/brief")
def deep_brief(p):
    from core import deepsearch as D
    h = next((x for x in D.history(200) if x["file"] == p.get("file")), None)
    if not h:
        raise UserError("brief not found")
    return h


@route("POST", "deep/run")
def deep_run(_, body):
    from core import deepsearch as D, llm
    q = str(body.get("question", "")).strip()
    if not q:
        raise UserError("type a question first")
    if not llm.status("search")[0]:
        raise UserError("Choose a model for Deep search in Settings → AI models (local by default).")
    try:
        tid = tasks.start("deep", lambda log, prog: D.run(q, use_web=bool(body.get("web", True)), log=log))
    except RuntimeError as e:
        raise UserError(str(e))
    return dict(task=tid)


@route("GET", "task")
def task(p):
    t = tasks.get(p.get("id", ""))
    if not t:
        raise UserError("task not found (the app may have restarted)")
    return t


# ---------------- exposure list ----------------
@route("GET", "exposures")
def exposures(_):
    from core import search as S
    d = S.exposures()
    return dict(rows=d.fillna("").astype(str).to_dict("records"), columns=S.EXP_COLS, drivers=S.driver_options())


@route("POST", "exposures/save")
def exposures_save(_, body):
    from core import search as S
    d = pd.DataFrame(body.get("rows") or [], columns=S.EXP_COLS)
    S.save_exposures(d)
    return dict(saved=len(S.exposures()))


@route("POST", "exposures/starter")
def exposures_starter(_, body):
    from core import search as S
    cur = S.exposures()
    merged = pd.concat([cur, S.starter_rows()], ignore_index=True).drop_duplicates(["driver", "target"], keep="first")
    S.save_exposures(merged)
    return dict(added=len(merged) - len(cur))


# ---------------- web search settings ----------------
@route("GET", "websearch")
def websearch(_):
    from core import websearch as WS, secrets
    o = WS.options()
    rows = [dict(provider=label, key_name=key, key_found=secrets.has(key), used_this_month=WS.usage().get(k, 0)) for k, (label, key) in WS.PROVIDERS.items() if key]
    return dict(options=o, providers=[[k, v[0]] for k, v in WS.PROVIDERS.items()], rows=rows, status=WS.status()[1])


@route("POST", "websearch/save")
def websearch_save(_, body):
    from core import websearch as WS
    if body.get("provider") not in WS.PROVIDERS:
        raise UserError("unknown provider")
    WS.save_options({"provider": body["provider"], "monthly_cap": max(1, int(body.get("monthly_cap") or 1)),
                     "results": min(20, max(3, int(body.get("results") or 5))), "india_bias": bool(body.get("india_bias"))})
    return dict(ok=True)


# ---------------- Ask ----------------
@route("GET", "ask")
def ask_meta(_):
    from core import llm
    ok, msg = llm.status("ask")
    return dict(model_ok=ok, model=msg, online=bool(ok and llm.is_online("ask")),
                examples=["Why were there no trades today?", "What is the momentum portfolio holding and why?",
                          "Which strategies work in the current market state?", "What did the agents say about TECHM?"])


@route("POST", "ask")
def ask(_, body):
    from core import ask as A
    q = str(body.get("question", "")).strip()
    if not q:
        raise UserError("type a question")
    hist = [m for m in (body.get("history") or []) if m.get("role") in ("user", "assistant")][-8:]
    try:
        text, mode, parts, hits = A.answer(q, hist, universe(), prices(), use_docs=bool(body.get("docs")))
    except Exception as e:
        return dict(text=f"Couldn't get an answer: {e}", parts=[], hits=[])
    if mode == "lookup":
        text = _lookup(parts)
    out_parts = []
    for k, v in parts.items():
        if isinstance(v, pd.DataFrame):
            out_parts.append(table(k.replace("_", " "), v.head(50), tools=False, empty="none"))
        else:
            out_parts.append(dict(kind="md", title=k.replace("_", " "), text="```\n" + json.dumps(v, indent=1, default=str)[:4000] + "\n```"))
    return dict(text=text, mode=mode, parts=out_parts, hits=_hits(hits))


STATE_LABEL = {"up_calm": "Uptrend, calm", "up_volatile": "Uptrend, volatile", "down_calm": "Downtrend, calm", "down_volatile": "Downtrend, volatile", "unknown": "Unknown"}


def _lookup(parts):
    """No model: show the records that answer the question."""
    syms = [k.split("_")[0] for k in parts if k.endswith("_signal_history")]
    reg = parts.get("market_regime_last_days")
    out = []
    if reg is not None and len(reg):
        r = reg.iloc[0]
        out.append(f"Market state on {pd.Timestamp(r.date).date()}: **{STATE_LABEL.get(r.market_state, r.market_state)}**. "
                   f"Active swing strategies: {r.active_strategies or 'none'}." + (f" {r.note}" if r.note else ""))
    for s in syms:
        sig, dec = parts.get(f"{s}_signal_history"), parts.get(f"{s}_decision_history")
        if sig is not None and len(sig):
            last = sig.iloc[0]
            out.append(f"**{s}**: last signal {pd.Timestamp(last.date).date()} from {last.strategy} ({'switched on' if last.strategy_active else 'strategy switched off'}).")
        else:
            out.append(f"**{s}**: no signal recorded since the agents started running.")
        if dec is not None and len(dec):
            d = dec.iloc[0]
            out.append(f"Last risk decision: {'approved, ' + str(int(d.quantity)) + ' shares' if d.approved else 'rejected: ' + str(d.reject_reason)}.")
    return "\n\n".join(out) or "I found no stored records that match."


def _hits(hits):
    if hits is None or not len(hits):
        return []
    return [dict(title=str(r.title), where=str(r.where or ""), published=str(r.published or "")[:10], text=str(r.text)[:600],
                 link=str(r.location) if str(r.location).startswith("http") else None,
                 how=" + ".join(x for x, on in (("meaning", getattr(r, "in_vector", False)), ("keywords", getattr(r, "in_keywords", False))) if on))
            for r in hits.itertuples()]


# ---------------- Documents ----------------
KIND_LABEL = {"file": "File", "web": "Web page", "site": "Website page", "news": "News article"}


def _pkgs():
    import importlib.util
    def has(m):
        try:
            return importlib.util.find_spec(m) is not None
        except (ImportError, ValueError):
            return False
    PK = {"llama_index.core": "LlamaIndex", "llama_index.embeddings.ollama": "LlamaIndex Ollama embeddings", "lancedb": "LanceDB",
          "trafilatura": "trafilatura", "pypdf": "pypdf", "docx": "python-docx", "xlrd": "xlrd (geopolitical risk index)"}
    miss = [l for m, l in PK.items() if not has(m)]
    lib_ok = all(has(m) for m in PK if m != "xlrd")
    return lib_ok, miss, list(PK.values())


@route("GET", "documents")
def documents(_):
    from core import llm
    lib_ok, miss, _ = _pkgs()
    if not lib_ok:
        return dict(ready=False, missing=miss)
    from core import library as L, articles as AR
    o, s = L.options(), L.stats()
    emb = llm.settings()["embed_model"]
    try:
        models = llm.ollama_models() or []
    except llm.ModelError:
        models = []
    reg = L.registry()
    lib = []
    if len(reg):
        v = reg.sort_values("updated", ascending=False).copy()
        v["kind"] = v.kind.map(lambda k: KIND_LABEL.get(k, k))
        v["enabled"] = v.enabled.fillna(True).astype(bool)
        lib = v[["source_id", "enabled", "title", "kind", "chunks", "pages", "status", "note", "updated", "location"]].to_dict("records")
    return dict(ready=True, enabled=o["enabled"], site_page_limit=o["site_page_limit"],
                stats=f"{s['sources']} sources · {s['passages']:,} passages · " + " · ".join(f"{KIND_LABEL.get(k, k)} {v}" for k, v in s["by_kind"].items()) + f" · answers by {llm.status('docs')[1]}",
                embed_missing=None if any(m.split(":")[0] == emb.split(":")[0] for m in models) else emb,
                kinds=[[k, v] for k, v in KIND_LABEL.items()], library=lib, queued=len(L.queued()), lane_busy=bool(jobs.busy("library-ingest")),
                articles=AR.summary() or {}, articles_busy=bool(jobs.busy("news-articles")))


@route("POST", "documents/enable")
def documents_enable(_, body):
    from core import library as L
    L.save_options({"enabled": bool(body.get("on"))})
    return dict(ok=True)


@route("POST", "documents/ask")
def documents_ask(_, body):
    from core import library as L
    q = str(body.get("question", "")).strip()
    if not q:
        raise UserError("type a question")
    try:
        text, hits = L.answer(q, [m for m in (body.get("history") or []) if m.get("role") in ("user", "assistant")],
                              kinds=body.get("kinds") or None, source_ids=body.get("sources") or None, symbol=(body.get("symbol") or "").strip().upper() or None)
    except Exception as e:
        text, hits = f"Couldn't answer: {e}", None
    return dict(text=text, hits=_hits(hits))


def _ingest():
    if jobs.busy("library-ingest"):
        cur = jobs.current("library-ingest")
        return "Added: the running library job picks it up." if cur and cur["name"] == "library-ingest" else \
            f"Added to the queue. It starts when '{cur['name'] if cur else 'the current job'}' finishes: press Process queue then."
    jobs.start("library-ingest")
    return "Added. Reading it in the background."


@route("POST", "documents/add")
def documents_add(_, body):
    from core import library as L
    kind = body.get("kind")
    if kind == "files":
        files = body.get("files") or []
        if not files:
            raise UserError("choose files first")
        for f in files[:50]:
            name = str(f.get("name", "file"))
            if not name.lower().endswith((".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm", ".rtf")):
                raise UserError(f"not a supported file type: {name}")
            L.enqueue("file", L.save_upload(name, base64.b64decode(f.get("data", ""))))
    elif kind == "folder":
        p = str(body.get("path", "")).strip()
        if not p:
            raise UserError("type a folder path")
        L.enqueue("folder", p)
    elif kind == "urls":
        urls = [u.strip() for u in str(body.get("urls", "")).splitlines() if u.strip().startswith("http")]
        if not urls:
            raise UserError("one http(s) link per line")
        for u in urls:
            L.enqueue("url", u)
    elif kind == "site":
        s = str(body.get("url", "")).strip()
        if not s.startswith("http"):
            raise UserError("the start page must be an http(s) link")
        lim = int(body.get("limit") or 50)
        L.enqueue("site", s, limit=lim, same_path=bool(body.get("same", True)))
        L.save_options({"site_page_limit": lim})
    else:
        raise UserError("unknown kind")
    return dict(message=_ingest())


@route("POST", "documents/process")
def documents_process(_, body):
    try:
        jobs.start("library-ingest")
    except RuntimeError as e:
        raise UserError(str(e))
    return dict(ok=True)


@route("POST", "documents/source")
def documents_source(_, body):
    from core import library as L
    sid, act = body.get("id", ""), body.get("action")
    if act == "enable":
        L.set_enabled(sid, bool(body.get("on")))
    elif act == "remove":
        L.remove(sid)
    elif act == "summarise":
        L.enqueue("summary", sid)
        return dict(message=_ingest())
    else:
        raise UserError("unknown action")
    return dict(ok=True)


@route("GET", "documents/summary")
def documents_summary(p):
    from core import library as L
    sp = L.SUMMARIES / f"{p.get('id', '')}.md"
    if "/" in p.get("id", "") or not sp.exists():
        return dict(text=None)
    return dict(text=sp.read_text())


# ---------------- Strategy Lab ----------------
def _lab():
    from lab import engine as E, store as S
    return E, S


@route("GET", "lab")
def lab_home(_):
    E, S = _lab()
    lst = S.list_strategies()
    rr = S.runs()
    out = []
    for s in lst:
        r = rr[rr.strategy_id == s["id"]] if len(rr) else rr
        last = r.iloc[0] if len(r) else None
        out.append(dict(id=s["id"], name=s["name"], mode=s["mode"], updated=s["updated"], versions=S.versions_tested(s["id"]), runs=len(r),
                        last_run=None if last is None else last.run_id, passes=None if last is None else bool(last.passes_so_far),
                        first_fail=None if last is None else (str(last.first_fail).split(":")[0] if last.first_fail else None),
                        design_excess=None if last is None else last.design_excess, design_t=None if last is None else last.design_t))
    return dict(templates=list(E.TEMPLATES), strategies=out, archived=[dict(id=s["id"], name=s["name"]) for s in S.list_strategies(archived=True)])


@route("POST", "lab/create")
def lab_create(_, body):
    E, S = _lab()
    t = body.get("template")
    if t not in E.TEMPLATES:
        raise UserError("unknown template")
    d = E.normalize(E.TEMPLATES[t])
    d["name"] = str(body.get("name") or "").strip() or d["name"]
    return dict(id=S.save(d, None, note=f"created from template: {t}"))


@route("POST", "lab/manage")
def lab_manage(_, body):
    E, S = _lab()
    sid, act = body.get("id", ""), body.get("action")
    if not S.get(sid):
        raise UserError("strategy not found")
    if act == "duplicate":
        return dict(id=S.duplicate(sid))
    if act == "archive":
        S.archive(sid, bool(body.get("on", True)))
        return dict(ok=True)
    raise UserError("unknown action")


@route("GET", "lab/strategy")
def lab_strategy(p):
    from core.data import has_broad
    E, S = _lab()
    rec = S.get(p.get("id", ""))
    if not rec:
        raise UserError("strategy not found")
    sec = sorted(universe().Industry.dropna().unique())
    return dict(id=rec["id"], name=rec["name"], updated=rec["updated"], version=rec["version"], versions_tested=S.versions_tested(rec["id"]),
                definition=rec["definition"], sectors=sec, has_broad=has_broad(), snippets=_SNIPPETS, market_snippets=_MSNIPPETS)


_SNIPPETS = [
    ["Price above its 200-day average", "close > sma(200)"], ["50-day average above 200-day (uptrend)", "sma(50) > sma(200)"],
    ["New 55-day high", "close > prev(highest(high, 55))"], ["New 52-week high", "close >= prev(high52())"],
    ["Within 5% of the 52-week high", "close >= 0.95 * high52()"], ["Volume at least 2x normal", "vol_ratio(20) >= 2"],
    ["RSI(14) below 30 (oversold)", "rsi(14) < 30"], ["RSI(3) below 15 (sharp dip)", "rsi(3) < 15"],
    ["Close crosses above the 50-day average", "crosses_above(close, sma(50))"], ["Top 10% by 6-month return", "rank(ret(126)) >= 90"],
    ["Stronger than the Nifty over 3 months", "rs(63) > 0"], ["Top 30% in its sector by 3-month return", "sector_rank(ret(63)) >= 70"],
    ["Gap up of 2%+ at the open", "gap() >= 2"], ["Tight range: Bollinger width near its yearly low", "bb_width(20, 2) <= 1.1 * lowest(bb_width(20, 2), 250)"],
    ["Pullback: close within 2% of the 20-day average", "abs(close / sma(20) - 1) <= 0.02"], ["3 up days in a row", "count(close > prev(close), 3) == 3"],
    ["MACD above its signal line", "macd() > macd_signal()"], ["Daily range (ATR) under 3% of price", "atr_pct(14) < 3"]]
_MSNIPPETS = [["Nifty above its 200-day average", "nifty > sma(nifty, 200)"], ["VIX below 20", "vix < 20"],
              ["Market state is up and calm", 'market_state == "up_calm"'], ["More than 50% of stocks above their 200-day", "breadth > 50"],
              ["Nifty up over 3 months", "ret(nifty, 63) > 0"]]


def _defn(body):
    E, _ = _lab()
    d = body.get("definition")
    if not isinstance(d, dict):
        raise UserError("missing definition")
    try:
        return E.normalize(d)
    except Exception as e:
        raise UserError(f"Couldn't read the rules: {e}")


@route("POST", "lab/validate")
def lab_validate(_, body):
    E, S = _lab()
    d = _defn(body)
    errs = E.validate(d)
    rec = S.get(body.get("id", "")) or {}
    changed = not rec or E.version_hash(d) != rec.get("version") or d["name"] != rec.get("name") or d.get("notes") != (rec.get("definition") or {}).get("notes")
    return dict(errors=[e for e in errs if "broad download" not in e], infos=[e for e in errs if "broad download" in e], changed=changed)


@route("POST", "lab/save")
def lab_save(_, body):
    E, S = _lab()
    d = _defn(body)
    hard = [e for e in E.validate(d) if "broad download" not in e]
    if hard:
        raise UserError("; ".join(hard))
    return dict(id=S.save(d, body.get("id") or None))


@route("POST", "lab/check")
def lab_check(_, body):
    from lab.expr import RuleError
    E, _ = _lab()
    d = _defn(body)
    try:
        qc = E.signal_counts(d)
        sig, last = E.latest_signals(d)
    except RuleError as e:
        raise UserError(str(e))
    py = qc["per_year"]
    title = (f"{qc['total']:,} signals since 2015 in {qc['stocks']} stocks (before the one-trade-per-stock rule)" if d["mode"] == "signal"
             else f"About {int(py.median()):,} stocks eligible per day ({qc['stocks']} different stocks)")
    return dict(sections=[dict(kind="grid", cols=2, items=[
        dict(kind="chart", type="bars", title=title, x=[str(x) for x in py.index], series=[dict(name="per year", y=[float(v) for v in py.values])], height=240, note=qc["info"].get("universe")),
        table(f"{'Signals' if d['mode'] == 'signal' else 'Top picks'} on {pd.Timestamp(last).date()}", sig.head(40), tools=False, empty="None that day.")])])


@route("POST", "lab/run")
def lab_run(_, body):
    from lab.expr import RuleError
    E, S = _lab()
    d = _defn(body)
    sid = body.get("id")
    if not S.get(sid or ""):
        raise UserError("strategy not found")
    hard = [e for e in E.validate(d) if "broad download" not in e]
    if hard:
        raise UserError("; ".join(hard))
    S.save(d, sid, note="saved for a test")

    def job(log, prog):
        try:
            res = E.run(d, progress=lambda f, m: prog(f, m))
        except RuleError as e:
            raise RuntimeError(str(e))
        if d["mode"] == "signal" and (res["trades"] is None or res["trades"].empty):
            raise RuntimeError("No trades: the entry rule never fired (or every stop was above the entry). Try Quick check.")
        return dict(run_id=S.save_run(sid, res), strategy=sid)
    try:
        return dict(task=tasks.start("lab", job))
    except RuntimeError as e:
        raise UserError(str(e))


@route("GET", "lab/runs")
def lab_runs(p):
    _, S = _lab()
    rr = S.runs(p.get("id") or None)
    return [[r.run_id, f"{pd.Timestamp(r.created):%d %b %H:%M} · version {r.version}" + (" · holdout seen" if r.holdout_revealed else ""), r.strategy_id]
            for r in rr.itertuples()] if len(rr) else []


def _hide(df, col, revealed):
    if df is None or revealed or col not in df:
        return df
    return df[pd.to_datetime(df[col]) < pd.Timestamp(C.HOLDOUT_FROM)]


def _pct(v, d=2, sign=True):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%")


def _n(v, d=1):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{d}f}"


def _equity_chart(e, extra, revealed, log=False, title="Growth of the money (₹ lakh)"):
    x = e.date.dt.strftime("%Y-%m-%d").tolist()
    series = [dict(name=n, y=[None if pd.isna(v) else round(float(v), 3) for v in ys], color=c, width=1.4) for n, ys, c in (extra or [])]
    series.append(dict(name="Strategy", y=(e.equity / 1e5).round(3).tolist(), width=2.4))
    vl = [dict(x="2021-01-01", label="validation")] + ([dict(x="2024-01-01", label="holdout")] if revealed else [])
    return dict(kind="chart", type="line", title=title, x=x, series=series, vlines=vl, log=log, height=340)


def _nifty_series(start_value, dates):
    E, _ = _lab()
    _, _, _, market = E.base_data()
    m = market.dropna(subset=["nifty"]).set_index("date").nifty
    m = m[(m.index >= dates.min()) & (m.index <= dates.max())]
    if m.empty:
        return None
    v = m.reindex(dates.values, method="ffill")
    return ("Nifty 50", (start_value / 1e5 * v / v.dropna().iat[0]).values, "#a8b0bf")


@route("GET", "lab/report")
def lab_report(p):
    E, S = _lab()
    run = S.load_run(p.get("run", ""))
    if run is None:
        raise UserError("run files missing")
    sid = run["strategy_id"]
    d = run["definition"]
    revealed = bool(run.get("holdout_revealed"))
    bp = dict(run["by_period"])
    if not revealed:
        bp.pop("holdout", None)
    n = S.versions_tested(sid)
    v = E.verdict(bp, run["mode"], n)
    if v["passes"]:
        verdict = ("pos", "Passes every test, including the holdout. It beat random picks after costs in all three periods. You can send it to the Strategy agent for paper trading.")
    elif v["passes_so_far"] and not revealed:
        verdict = ("pos", "Passes design and validation. The last test is the holdout (2024 onwards), kept hidden so it can't influence your choices. Reveal it once you're done changing the rules.")
    elif v["passes_so_far"]:
        verdict = ("warn", "Passes design and validation, fails the holdout.")
    else:
        verdict = ("neg", f"Does not pass. First failure: {v['first_fail']}.")
    search_note = (f"You've tested {n} versions of this strategy. With that many tries, a design t-stat of {v['search_t']:.1f} is needed to rule out luck from the search itself"
                   + (" — this one clears it." if v["search_ok"] else " — this one doesn't, so treat a pass with caution.")) if n > 1 else ""
    cards = []
    for per, lab in E.PERIOD_LABEL.items():
        s = run["by_period"].get(per, {})
        if per == "holdout" and not revealed:
            cards.append(dict(label=lab, value="🔒 Hidden", delta="Reveal when you've stopped changing the rules"))
        elif not s or not (s.get("trades") or s.get("periods")):
            cards.append(dict(label=lab, value="No data"))
        elif run["mode"] == "signal":
            cards.append(dict(label=f"{lab} · net per trade", value=_pct(s.get("avg_net_pct")),
                              delta=f"{_pct(s.get('excess_pct'))} vs random · {s['trades']:,} trades · win {s['win_pct']:.0f}% · t {_n(s.get('t_excess'))}"))
        else:
            cards.append(dict(label=f"{lab} · return a year", value=_pct(s.get("cagr_pct"), 1),
                              delta=f"{_pct(s.get('excess_pct'))} a {'week' if run.get('freq') == 'W' else 'month'} vs market · t {_n(s.get('t_excess'))} · max drop {_pct(s.get('max_dd_pct'), 0, False)}"))
    secs = [dict(kind="kpis", cols=3, items=cards),
            table("Every test", pd.DataFrame(v["checks"]).assign(period=lambda x: x.period.map(E.PERIOD_LABEL)), {"ok": "bool"}, tools=False)]
    trades = 0
    if run["mode"] == "signal":
        t = _hide(run["trades"], "signal_date", revealed)
        e = _hide(run["equity"], "date", revealed)
        if t is not None and len(t):
            t = t[t.period != "warmup"].copy()
            t["net_pct"], t["control_pct"], t["excess_pct"] = 100 * t.net, 100 * t.control, 100 * t.excess
            if e is not None and len(e):
                e = e.copy(); e["date"] = pd.to_datetime(e.date)
                nl = _nifty_series(e.equity.iat[0], e.date)
                secs.append(_equity_chart(e, [nl] if nl else None, revealed))
                ps = run["portfolio"].get("all" if revealed else "design", {})
                tk = t[t.taken]
                secs.append(dict(kind="kpis", items=[dict(label="Return a year", value=_pct(ps.get("cagr_pct"), 1), delta=f"Nifty {_pct(ps.get('nifty_cagr_pct'), 1)}"),
                                                     dict(label="Worst drop from a peak", value=_pct(ps.get("max_dd_pct"), 0, False)), dict(label="Sharpe", value=_n(ps.get("sharpe"), 2)),
                                                     dict(label="Signals taken", value=f"{len(tk):,} of {len(t):,}", delta=f"at most {d['portfolio']['max_positions']} positions")]))
            y = t.groupby("year").agg(ex=("excess_pct", "mean"), net=("net_pct", "mean"), n=("net", "size")).reset_index()
            s = t.groupby("state").agg(trades=("net", "size"), net=("net_pct", "mean"), random=("control_pct", "mean"), edge=("excess_pct", "mean"),
                                       win=("net", lambda r: 100 * (r > 0).mean())).reindex(list(STATE_LABEL)).dropna(how="all").reset_index()
            s["state"] = s.state.map(STATE_LABEL)
            r = t.groupby("reason").agg(trades=("net", "size"), avg=("net_pct", "mean")).reset_index()
            r["share"] = 100 * r.trades / r.trades.sum()
            hist = np.histogram(t.net_pct.clip(-25, 25), bins=np.arange(-25, 26, 1))
            secs.append(dict(kind="grid", cols=2, items=[
                dict(kind="chart", type="hbar", title="Edge over random picks, by year (avg per trade)", labels=y.year.astype(str).tolist(), values=y.ex.round(2).tolist(), decimals=2),
                table("By market state", s.round(2), {"net": "snum2", "random": "snum2", "edge": "snum2", "win": "num0", "trades": "int"}, tools=False),
                dict(kind="chart", type="hbar", title="How trades ended (share of trades)", labels=(r.reason + " · avg " + r.avg.map(lambda x: f"{x:+.1f}%")).tolist(), values=r.share.round(1).tolist()),
                dict(kind="chart", type="bars", title=f"Net return per trade (average {t.net_pct.mean():+.2f}%)", x=[f"{a:+.0f}%" for a in hist[1][:-1]], series=[dict(name="trades", y=hist[0].astype(float).tolist())], height=240)]))
            sc = t.groupby("sector").agg(trades=("net", "size"), net=("net_pct", "mean"), edge=("excess_pct", "mean")).reset_index()
            sc = sc[sc.trades >= 30].sort_values("edge", ascending=False)
            if len(sc):
                secs.append(dict(kind="chart", type="hbar", title="Edge by sector (sectors with 30+ trades)", labels=sc.sector.tolist(), values=sc.edge.round(2).tolist(), decimals=2))
            trades = len(t)
        else:
            secs.append(dict(kind="note", text="No trades."))
    else:
        rp = _hide(run["periods"], "period_start", revealed)
        e = _hide(run["equity"], "date", revealed)
        if rp is not None and len(rp):
            rp = rp[rp.period != "warmup"].copy()
            unit = "week" if run.get("freq") == "W" else "month"
            if e is not None and len(e):
                e = e.copy(); e["date"] = pd.to_datetime(e.date)
                nl = _nifty_series(e.equity.iat[0], e.date)
                secs.append(_equity_chart(e, [("Equal-weight control", (e.control_equity / 1e5).values, "#d97706")] + ([nl] if nl else []), revealed, log=True,
                                          title="Growth of the money (₹ lakh, log scale)"))
                ps = run["portfolio"].get("all" if revealed else "design", {})
                secs.append(dict(kind="kpis", items=[dict(label="Return a year", value=_pct(ps.get("cagr_pct"), 1), delta=f"Nifty {_pct(ps.get('nifty_cagr_pct'), 1)}"),
                                                     dict(label="Worst drop from a peak", value=_pct(ps.get("max_dd_pct"), 0, False)), dict(label="Sharpe", value=_n(ps.get("sharpe"), 2)),
                                                     dict(label=f"{unit.capitalize()}s beating the control", value=f"{100 * (rp.excess > 0).mean():.0f}%")]))
            y = rp.groupby("year").agg(ex=("excess", "mean")).reset_index()
            secs.append(dict(kind="grid", cols=2, items=[
                dict(kind="chart", type="hbar", title=f"Edge over the control, by year (avg per {unit})", labels=y.year.astype(str).tolist(), values=(100 * y.ex).round(2).tolist(), decimals=2),
                dict(kind="chart", type="bars", title=f"Every {unit}: strategy minus control (%)", x=pd.to_datetime(rp.period_start).dt.strftime("%Y-%m-%d").tolist(),
                     series=[dict(name="excess", y=(100 * rp.excess).round(2).tolist())], height=240)]))
            nx = run.get("next")
            if nx is not None and len(nx):
                secs.append(table("What it would buy at the next rebalance", nx, tools=False, note=f"Ranked on data to {run['data_last']}; final at the period end."))
            pk = _hide(run.get("picks"), "period_start", revealed)
            if pk is not None and len(pk):
                secs.append(table("Every pick, every period", pk.sort_values(["period_start", "rank"], ascending=[False, True]), csv="picks"))
        else:
            secs.append(dict(kind="note", text="No results."))
    secs.append(dict(kind="md", title="Rules tested", text=describe(d)))
    nrev, nver = S.reveals(sid)
    return dict(name=d["name"], notes=d.get("notes"), strategy=sid, run_id=run["run_id"], mode=run["mode"], revealed=revealed, passes=bool(v["passes"]),
                reveals=f"Holdout seen {nrev} time(s), {nver} version(s)" if revealed else None,
                caption=f"Tested {run['created'][:16]} on data to {run['data_last']} · {run['info'].get('universe', '')} · took {run['seconds']}s",
                verdict=verdict, search_note=search_note, sections=secs, trades=trades, periods=[[k, v] for k, v in E.PERIOD_LABEL.items() if revealed or k != "holdout"], definition=d)


TRADE_COLS = ["signal_date", "symbol", "sector", "entry", "exit", "reason", "days", "net_pct", "control_pct", "taken", "period"]


def _trades(run_id):
    def build():
        _, S = _lab()
        run = S.load_run(run_id)
        if run is None or run.get("trades") is None:
            return pd.DataFrame(columns=TRADE_COLS)
        t = _hide(run["trades"], "signal_date", bool(run.get("holdout_revealed")))
        t = t[t.period != "warmup"].copy()
        t["net_pct"], t["control_pct"] = (100 * t.net).round(2), (100 * t.control).round(2)
        t["entry"], t["exit"] = t.entry.round(2), t.exit.round(2)
        t["signal_date"] = pd.to_datetime(t.signal_date).dt.strftime("%Y-%m-%d")
        return t[TRADE_COLS].reset_index(drop=True)
    from web.api import cached
    _, S = _lab()
    rv = S.q("select holdout_revealed from runs where run_id = ?", [run_id])
    return cached(f"lab_trades_{run_id}", bool(rv.holdout_revealed.iat[0]) if len(rv) else None, build)


@route("GET", "lab/trades")
def lab_trades(p):
    """One page of a run's trades (filtered and sorted on this Mac), or all of them as CSV text."""
    t = _trades(p.get("run", ""))
    if p.get("periods"):
        t = t[t.period.isin(p["periods"].split(","))]
    if p.get("taken") == "true":
        t = t[t.taken.astype(bool)]
    sym = p.get("symbol", "").strip().upper()
    if sym:
        t = t[t.symbol.str.contains(sym, regex=False)]
    col = p.get("sort") if p.get("sort") in TRADE_COLS else "signal_date"
    t = t.sort_values(col, ascending=p.get("dir") == "asc", kind="stable")
    if p.get("csv") == "true":
        return dict(csv=t.to_csv(index=False))
    per, page = 25, max(0, int(p.get("page", 0) or 0))
    return dict(total=len(t), page=page, pages=max(1, -(-len(t) // per)), rows=t.iloc[page * per: page * per + per])


@route("GET", "lab/trade")
def lab_trade(p):
    E, S = _lab()
    run = S.load_run(p.get("run", ""))
    if run is None:
        raise UserError("run files missing")
    at = run["trades"]
    full = at[(at.symbol == p.get("symbol")) & (pd.to_datetime(at.signal_date) == pd.Timestamp(p.get("date")))]
    if full.empty:
        raise UserError("trade not found")
    f = full.iloc[0]
    px, _, _, _ = E.base_data()
    g = px[px.symbol == f.symbol].sort_values("date")
    t0, t1 = pd.Timestamp(f.signal_date), pd.Timestamp(f.exit_date)
    v = g[(g.date >= t0 - pd.Timedelta(days=120)) & (g.date <= t1 + pd.Timedelta(days=40))].reset_index(drop=True)
    idx = lambda d: int((v.date <= pd.Timestamp(d)).sum()) - 1
    ie, ix = idx(f.entry_date), idx(f.exit_date)
    lv = []
    if pd.notna(f.stop):
        lv.append({"from": ie, "to": ix, "price": float(f.stop), "color": "down"})
    if pd.notna(f.target):
        lv.append({"from": ie, "to": ix, "price": float(f.target), "color": "up"})
    return dict(title=f"{f.symbol}: bought {pd.Timestamp(f.entry_date).date()} at {f.entry:.2f}, sold {pd.Timestamp(f.exit_date).date()} at {f.exit:.2f} ({f.reason}), {100 * f.net:+.2f}% after costs",
                chart=dict(kind="chart", type="price", mode="candle", height=380,
                           bars=dict(t=v.date.dt.strftime("%Y-%m-%d").tolist(), o=v.open.round(2).tolist(), h=v.high.round(2).tolist(), l=v.low.round(2).tolist(),
                                     c=v.close.round(2).tolist(), v=v.volume.astype(float).tolist()),
                           marks=[dict(i=ie, price=float(f.entry), color="acc", dir="up"), dict(i=ix, price=float(f.exit), color="down" if f.net < 0 else "up", dir="down")],
                           levels=lv))


@route("POST", "lab/reveal")
def lab_reveal(_, body):
    _, S = _lab()
    S.reveal_holdout(body.get("run", ""))
    return dict(ok=True)


@route("POST", "lab/send")
def lab_send(_, body):
    _, S = _lab()
    live_id, pid = S.send_to_agents(body.get("strategy", ""), body.get("run", ""))
    return dict(message=f"Filed as {live_id}" + (f" (proposal {pid})" if pid else " (a proposal already exists)") + ". Approve it in Settings → Approvals.")


@route("GET", "lab/compare")
def lab_compare(p):
    _, S = _lab()
    rr = S.runs()
    if not len(rr):
        return dict(options=[], sections=[dict(kind="note", text="No test runs yet.")])
    lab = {r.run_id: f"{r.name} · {pd.Timestamp(r.created):%d %b %H:%M} · v{r.version}" for r in rr.itertuples()}
    pick = [x for x in (p.get("runs") or "").split(",") if x in lab] or list(lab)[:3]
    rows, curves = [], {}
    for rid in pick:
        run = S.load_run(rid)
        if run is None:
            continue
        rev = bool(run.get("holdout_revealed"))
        row = dict(run=lab[rid], type=run["mode"])
        for per in ("design", "validation", "holdout"):
            s = run["by_period"].get(per, {})
            if per == "holdout" and not rev:
                row[f"{per}_edge"] = "🔒"; row[f"{per}_t"] = "🔒"; continue
            row[f"{per}_edge"] = round(s.get("excess_pct") or np.nan, 2)
            row[f"{per}_t"] = round(s.get("t_excess") or np.nan, 1)
        ps = run["portfolio"].get("all" if rev else "design", {})
        row["return_a_year"] = round(ps.get("cagr_pct") or np.nan, 1)
        row["worst_drop"] = round(ps.get("max_dd_pct") or np.nan, 0)
        rows.append(row)
        e = _hide(run.get("equity"), "date", rev)
        if e is not None and len(e):
            s_ = pd.Series(e.equity.values / e.equity.iat[0], index=pd.to_datetime(e.date))
            curves[lab[rid][:40]] = s_[~s_.index.duplicated()]
    secs = [table(None, pd.DataFrame(rows), tools=False)]
    if curves:
        w = pd.DataFrame(curves).sort_index().ffill()
        secs.append(dict(kind="chart", type="line", log=True, title="Growth of 1 (log scale)", x=w.index.strftime("%Y-%m-%d").tolist(), height=360,
                         series=[dict(name=c, y=[None if pd.isna(v) else round(float(v), 4) for v in w[c]]) for c in w]))
    secs.append(dict(kind="note", text="Holdout results show only for runs where you revealed them."))
    return dict(options=[[k, v] for k, v in lab.items()], picked=pick, sections=secs)


@route("GET", "lab/rules")
def lab_rules(_):
    from lab.expr import HELP
    secs = [dict(kind="md", title="Rule language", text=(
        "Write rules as formulas. Each is worked out for every stock on every day, using data up to that day's close only. "
        "Signals fire at the close; trades start at the next open unless you choose the same close.\n\n**Examples**\n\n"
        "- `close > sma(200) and rsi(3) < 15`\n- `close > prev(highest(high, 55)) and vol_ratio(20) >= 2`\n- `rank(ret(126)) >= 90 and rs(63) > 0`\n"
        "- `crosses_above(sma(20), sma(50)) and sector == \"Information Technology\"`\n- `nifty > sma(nifty, 200) and vix < 18` (market filter)"))]
    for group, items in HELP.items():
        secs.append(table(group, pd.DataFrame(items, columns=["write", "means"]), tools=False))
    secs.append(dict(kind="md", title="How the test works", text=(
        "- **Periods:** design 2016–20 (change rules freely), validation 2021–23 (a check), holdout 2024 onwards (hidden until you reveal it).\n"
        "- **Random-entry control:** for each trade, random stocks from the same universe are bought the same day with the same exits. The edge is your return minus theirs.\n"
        "- **t-stat:** edge ÷ its standard error, on weekly groups of trades. Design needs 2.0+, validation 1.65+.\n"
        "- **Versions tested:** every distinct set of rules you run counts. The more you try, the higher the bar to rule out luck.\n"
        "- **One trade per stock at a time.** Every signal counts in the edge test; the money simulation takes at most your max positions.\n"
        "- **Costs:** the round-trip cost is taken off every trade (0.3% default).\n"
        "- **Stops:** if a stock opens below the stop, the trade exits at the open. If stop and target are both touched on one day, the stop counts.")))
    return dict(sections=secs)


def describe(d):
    u = d["universe"]
    uni = ("today's Nifty 500" if u["source"] == "nifty500" else f"point-in-time top {u['pit_n']} by traded value") + \
          f", price ₹{float(u['min_price']):.0f}+, turnover ₹{float(u['min_turnover_cr']):.0f} cr+/day" + (f", sectors: {', '.join(u['sectors'])}" if u["sectors"] else "")
    cost = f"{float(d['portfolio']['cost_pct']):.2f}% round-trip cost"
    if d["mode"] == "rotation":
        r = d["rotation"]
        return "\n".join([f"- **Stocks:** {uni}" + (f"; only where `{r['filter']}`" if r["filter"] else ""),
                          f"- **Each {'month' if r['rebalance'] == 'M' else 'week'}-end:** rank by `{r['score']}` ({'highest' if r['score_desc'] else 'lowest'} first), "
                          f"buy the top {r['top_n']} at the next open, hold each batch {r['hold']} rebalance(s)",
                          f"- **Cash when:** not `{r['regime']}`" if r["regime"] else "- **Always invested**", f"- **Costs:** {cost}"])
    ex = d["exits"]
    stop = {"atr": f"{ex['stop_value']}× ATR(14) below entry", "pct": f"{ex['stop_value']}% below entry", "expr": f"at `{ex['stop_value']}`", "none": "none"}[ex["stop"]]
    tgt = {"r": f"{ex['target_value']}× the risk above entry", "atr": f"{ex['target_value']}× ATR(14) above entry", "pct": f"{ex['target_value']}% above entry",
           "expr": f"at `{ex['target_value']}`", "none": "none"}[ex["target"]]
    lines = [f"- **Stocks:** {uni}", f"- **Buy when:** `{d['entry']}`" + (f", only if `{d['market_filter']}`" if d["market_filter"] else ""),
             f"- **Buy at:** {'the next open' if d['entry_price'] == 'next_open' else 'the same close'}",
             f"- **Stop:** {stop} · **Target:** {tgt}" + (f" · **Trailing stop:** {ex['trail_pct']}%" if float(ex["trail_pct"] or 0) else ""),
             f"- **Also sell when:** `{ex['exit_when']}`" if ex["exit_when"] else "", f"- **Time limit:** {ex['max_days']} days",
             f"- **Portfolio:** at most {d['portfolio']['max_positions']} positions" + (f", ranked by `{d['rank_by']}`" if d["rank_by"] else ""),
             f"- **Costs:** {cost}"]
    return "\n".join(l for l in lines if l)
