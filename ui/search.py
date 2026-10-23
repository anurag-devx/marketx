"""Search page: one box for any stock, sector, theme or phrase, plus Deep search (cited research briefs)."""
from ui.common import *  # noqa: F401,F403
from core import search as S, news as N, announcements as A, macro as M, company_info as CI, websearch as WS, llm

LINK = st.column_config.LinkColumn("open", display_text="open")
TIME = st.column_config.DatetimeColumn("time", format="DD MMM YYYY HH:mm")


@st.cache_data(ttl=300)
def _stores(n_mt, a_mt):
    return N.load_store(), A.load()


def stores():
    mt = lambda p: p.stat().st_mtime if p.exists() else 0
    return _stores(mt(N.STORE) or mt(N.LEGACY), mt(A.STORE))


@st.cache_data(ttl=600)
def _info(mt):
    return CI.load()


def page():
    st.title("Search")
    t = st.tabs(["🔎 Search", "🧠 Deep search", "🗂 My exposure list", "⚙️ Web search settings"])
    with t[0]:
        search_tab()
    with t[1]:
        deep_tab()
    with t[2]:
        exposure_tab()
    with t[3]:
        web_tab()


# ---------------- Search ----------------
def search_tab():
    c = st.columns([5, 1])
    q = c[0].text_input("Search", key="srch-q", placeholder="A stock (Reliance, HAL), a sector (cement, PSU banks), a theme (Red Sea, tariffs) or any phrase",
                        label_visibility="collapsed")
    c[1].button("Search", type="primary", width="stretch", key="srch-go")
    if not q.strip():
        st.caption("Searches everything stored on this Mac: NSE filings, headlines and article text, your documents, macro data and "
                   "your exposure list. For a written answer to a question, use **Deep search**.")
        return
    info = _info(CI.FILE.stat().st_mtime if CI.FILE.exists() else 0)
    r = S.resolve(q, info)
    news, ann = stores()
    chips = ([f"stock: {s}" for s in r["stocks"]] + [f"sector: {s}" for s in r["sectors"]] + [f"industry: {s}" for s in r["industries"]]
             + [f"theme: {M.THEMES[t][0]}" for t in r["themes"]])
    st.caption("Understood as: " + (" · ".join(chips) if chips else "free text"))
    if r["kind"] == "stock":
        sym = r["stocks"][0]
        if len(r["stocks"]) > 1:
            names = dict(zip(info.symbol, info.company))
            sym = st.radio(f"{len(r['stocks'])} companies match", r["stocks"], horizontal=True, key="srch-sym",
                           format_func=lambda s: f"{s} ({str(names.get(s, ''))[:28]})")
        stock_view(sym, info, news, ann)
    elif r["kind"] == "sector":
        sector_view((r["sectors"] or r["industries"])[0], info, news)
    elif r["kind"] == "theme":
        theme_view(r["themes"][0])
    else:
        text_view(q)
    if r["kind"] != "text":
        with st.expander(f"Everything matching the words “{q}”"):
            text_view(q, key="x")


def _moves(sym):
    px = prices()
    if px is None:
        return None
    p = px[px.symbol == sym].sort_values("date")
    if len(p) < 2:
        return None
    c = p.close.values
    ret = lambda n: 100 * (c[-1] / c[-1 - n] - 1) if len(c) > n else np.nan
    return dict(date=p.date.iat[-1], close=c[-1], d1=ret(1), m1=ret(21), m3=ret(63), y1=ret(252), shares=None)


def stock_view(sym, info, news, ann):
    row = info[info.symbol == sym]
    r0 = row.iloc[0] if len(row) else None
    name = r0.company if r0 is not None else sym
    sector = r0.sector if r0 is not None else ""
    industry = r0.industry if r0 is not None else ""
    nifty_ind = r0.nifty_industry if r0 is not None else ""
    st.subheader(f"{name} ({sym})")
    st.caption(" · ".join(dict.fromkeys(x for x in [sector, industry, r0.basic_industry if r0 is not None else ""] if isinstance(x, str) and x)))
    mv = _moves(sym)
    if mv:
        k = st.columns(6)
        k[0].metric(f"Close ({mv['date']:%d %b})", f"₹{mv['close']:,.2f}", f"{mv['d1']:+.2f}%")
        k[1].metric("1 month", pct(mv["m1"], 1)); k[2].metric("3 months", pct(mv["m3"], 1)); k[3].metric("1 year", pct(mv["y1"], 1))
        if r0 is not None and pd.notna(r0.shares):
            k[4].metric("Market cap", f"₹{r0.shares * mv['close'] / 1e7:,.0f} cr")
        if k[5].button("Open in Stock lookup", key="srch-open"):
            go_stock(sym)
    peers = list(info[(info.industry == industry) & (info.symbol != sym)].symbol) if industry else []
    fil = ann[ann.symbol == sym].sort_values("published", ascending=False) if len(ann) else ann
    sn = S.stock_news(sym, news)
    secn = S.sector_news(nifty_ind or sector, industry, peers, news, days=30, exclude=sym)
    links, active = S.macro_links(sym, sector, nifty_ind, peers=peers)
    t = st.tabs(["Overview", f"NSE filings ({len(fil)})", f"News ({len(sn)})", f"Sector & industry ({len(secn)})",
                 f"Macro & geopolitics ({len(active)})", "Documents & articles", "Upcoming"])
    with t[0]:
        a, b = st.columns(2)
        with a:
            st.markdown("**Latest filings**")
            for x in fil.head(5).itertuples():
                st.markdown(f"- {x.published:%d %b} · {x.category} · {x.subject}" + (f" [pdf]({x.link})" if x.link else ""))
            st.markdown("**Latest news**")
            for x in sn.head(5).itertuples():
                st.markdown(f"- {x.published:%d %b} · [{x.title}]({x.link}) · {x.source}")
        with b:
            st.markdown("**Sector & industry, last 30 days**")
            for x in secn.head(5).itertuples():
                st.markdown(f"- {x.published:%d %b} · [{x.title}]({x.link}) · {x.why}")
            st.markdown("**Geopolitics & macro reaching this stock (last 7 days)**")
            lk = active[active.linked] if len(active) else active
            if len(lk):
                for x in lk.head(5).itertuples():
                    st.markdown(f"- **{x.theme}** ({x.headlines} headlines, {x.link}) → {x.reaches_stock_via}")
            else:
                st.caption("No theme in the last 7 days reaches this stock by name, sector, sensitivity or your list.")
    with t[1]:
        show(fil[["published", "category", "subject", "text", "link"]] if len(fil) else fil, "No filings stored.",
             column_config={"published": TIME, "link": LINK, "text": st.column_config.TextColumn(width="large")}, height=min(600, 40 + 35 * max(len(fil), 1)))
    with t[2]:
        show(sn[["published", "source", "event_type", "tone", "title", "link"]] if len(sn) else sn, "No headlines naming this stock.",
             column_config={"published": TIME, "link": LINK, "title": st.column_config.TextColumn("headline", width="large")},
             height=min(600, 40 + 35 * max(len(sn), 1)))
    with t[3]:
        st.caption(f"Headlines naming its industry peers ({len(peers)} stocks in {industry or sector}) or about the sector in general.")
        show(secn[["published", "why", "title", "source", "link"]] if len(secn) else secn, "Nothing in the last 30 days.",
             column_config={"published": TIME, "link": LINK, "title": st.column_config.TextColumn("headline", width="large")},
             height=min(600, 40 + 35 * max(len(secn), 1)))
    with t[4]:
        _macro_setup_hint(links)
        st.markdown("**Themes in the last 7 days and how they reach this stock**")
        st.caption("strong = named in the headlines, or through a factor the stock / its sector is measurably sensitive to, or your list · "
                   "indirect = only its sector or peers appear in the headlines")
        show(active, "No themed headlines in the last 7 days.", column_config={"linked": None,
             "reaches_stock_via": st.column_config.TextColumn("how it reaches the stock", width="large"),
             "example": st.column_config.TextColumn("example headline", width="large")})
        st.markdown("**Exposures: computed sensitivity and your list**")
        show(links, "No sensitivities yet (Macro & geopolitics → Run macro study) and nothing in your exposure list.",
             column_config={"per_1sd_pct": st.column_config.NumberColumn("Extra move per typical factor move", format="%+.2f%%"),
                            "t": st.column_config.NumberColumn(format="%.1f")})
    with t[5]:
        _library_hits(name.split(" Ltd")[0], symbol=sym)
    with t[6]:
        cal = M.calendar()
        up = cal[cal.date >= pd.Timestamp.now().normalize()].sort_values("date").head(10) if len(cal) else cal
        show(up, "No upcoming events in your calendar (Macro & geopolitics → Calendar).")
        nxt = fil[fil.category.astype(str).str.contains("meeting|Results", case=False, regex=True)].head(3) if len(fil) else fil
        if len(nxt):
            st.markdown("**Recent board-meeting / results filings**")
            show(nxt[["published", "subject", "text"]])


def _macro_setup_hint(links):
    have_computed = len(links) and (links.source == "computed").any()
    have_list = len(S.exposures()) > 0
    if have_computed and have_list:
        return
    c = st.columns([3, 1.3])
    msg = []
    if not have_computed:
        msg.append("the macro study hasn't run, so links through crude, USD/INR, yields etc. are missing")
    if not have_list:
        msg.append("your exposure list is empty (My exposure list tab: you can start from suggestions)")
    c[0].info("Links are partial: " + "; ".join(msg) + ".")
    if not have_computed and c[1].button("Fetch macro data + run the study", key="srch-macro", disabled=jobs.busy("macro-all"),
                                         help="About 3 minutes; runs alongside everything else."):
        launch("macro-all")


def _library_hits(query, symbol=None):
    try:
        from core import library as L
        if not L.options()["enabled"]:
            st.caption("Documents library is switched off (Documents page).")
            return
        hits = L.search(query, k=8, symbol=symbol)
        if hits.empty and symbol:
            hits = L.search(query, k=8)
    except Exception:
        st.caption("Documents library not set up yet (Settings → AI models → install packages).")
        return
    if hits.empty:
        st.caption("No passages in your documents or article text.")
        return
    for x in hits.itertuples():
        loc = x.location if str(x.location).startswith("http") else ""
        st.markdown(f"**{x.title}**" + (f" · {x.where}" if x.where else "") + (f" · {x.published[:10]}" if x.published else "")
                    + (f" · [open]({loc})" if loc else ""))
        st.caption(x.text[:500] + ("…" if len(x.text) > 500 else ""))


def sector_view(sector, info, news):
    st.subheader(sector)
    members = info[(info.sector == sector) | (info.industry == sector) | (info.basic_industry == sector) | (info.nifty_industry == sector)]
    px = prices()
    if px is not None and len(members):
        last = px[px.symbol.isin(members.symbol)].sort_values("date").groupby("symbol").tail(22)
        mv = last.groupby("symbol").close.apply(lambda c: 100 * (c.iat[-1] / c.iat[-2] - 1) if len(c) > 1 else np.nan).rename("last_day_pct")
        m1 = last.groupby("symbol").close.apply(lambda c: 100 * (c.iat[-1] / c.iat[0] - 1) if len(c) > 1 else np.nan).rename("month_pct")
        tab = members[["symbol", "company", "industry"]].set_index("symbol").join(mv).join(m1).reset_index().sort_values("last_day_pct", ascending=False)
        k = st.columns(3)
        k[0].metric("Stocks", len(tab)); k[1].metric("Median last day", pct(tab.last_day_pct.median())); k[2].metric("Median 1 month", pct(tab.month_pct.median(), 1))
        show(tab, height=min(400, 40 + 35 * len(tab)), column_config={"last_day_pct": st.column_config.NumberColumn("Last day", format="%+.2f%%"),
                                                                      "month_pct": st.column_config.NumberColumn("1 month", format="%+.1f%%")})
    secn = S.sector_news(sector, sector, list(members.symbol), news, days=30)
    st.markdown(f"**News, last 30 days** ({len(secn)})")
    show(secn[["published", "why", "title", "source", "link"]] if len(secn) else secn, "None.", column_config={"published": TIME, "link": LINK},
         height=min(500, 40 + 35 * max(len(secn), 1)))
    sens = S._read_csv(C.RESULTS / "macro" / "sens_3y.csv")
    if len(sens):
        st.markdown("**Macro sensitivity (last 3 years)**")
        show(sens[sens.sector == sector].drop(columns="sector"), "Not computed for this group.",
             column_config={"per_1sd_pct": st.column_config.NumberColumn("Extra move per typical factor move", format="%+.2f%%"), "t": st.column_config.NumberColumn(format="%.1f")})
    mine = S.exposures()
    mine = mine[mine.target.astype(str).str.lower() == sector.lower()]
    if len(mine):
        st.markdown("**Your exposure list**")
        show(mine)
    st.markdown("**Documents & articles**")
    _library_hits(sector)


def theme_view(theme):
    v = S.theme_view(theme)
    st.subheader(v["label"])
    st.caption("Acts on markets mainly through: " + (", ".join(v["factors"]) or "—"))
    a, b = st.columns(2)
    with a:
        st.markdown("**Sectors most sensitive to those factors** (computed, last 3 years)")
        show(v["sectors"], "Run the macro study first.", column_config={"per_1sd_pct": st.column_config.NumberColumn("Extra move", format="%+.2f%%"),
                                                                          "t": st.column_config.NumberColumn(format="%.1f")})
        st.markdown("**Most sensitive stocks**")
        show(v["stocks"], "—")
    with b:
        st.markdown("**Your exposure list for this theme**")
        show(v["mine"], "Nothing yet: add rows in My exposure list.")
    st.markdown(f"**Headlines, last 30 days** ({len(v['headlines'])})")
    show(v["headlines"][["published", "title", "source", "link"]] if len(v["headlines"]) else v["headlines"], "None stored.",
         column_config={"published": TIME, "link": LINK}, height=min(500, 40 + 35 * max(len(v["headlines"]), 1)))


def text_view(q, key=""):
    from core import search_index as SI
    c = st.columns([2, 2])
    kinds = c[0].multiselect("Show", ["news", "filing"], default=["news", "filing"], key=f"srch-kinds{key}", format_func={"news": "Headlines", "filing": "NSE filings"}.get)
    days = c[1].selectbox("From", [30, 90, 365, 3650], index=2, key=f"srch-days{key}", format_func=lambda d: f"last {d} days" if d < 3650 else "all stored")
    hits = SI.search(q, k=40, kinds=kinds or None, since=pd.Timestamp.now() - pd.Timedelta(days=days))
    st.markdown(f"**Headlines and filings** ({len(hits)})")
    if len(hits):
        h = hits.assign(published=pd.to_datetime(hits.published, errors="coerce"))
        show(h[["published", "kind", "title", "symbols", "source", "link"]], column_config={"published": TIME, "link": LINK,
             "title": st.column_config.TextColumn(width="large")}, height=min(600, 40 + 35 * len(h)))
        if hits.score.isna().all():
            st.caption("Plain word matching: build the search index (Data health → History downloads) to also find matches by meaning.")
    st.markdown("**Documents & articles**")
    _library_hits(q)


# ---------------- Deep search ----------------
def deep_tab():
    ok, msg = llm.status("search")
    wok, wmsg = WS.status()
    st.caption(f"Model: {msg} · Web: {wmsg}")
    q = st.text_area("Question", key="deep-q", height=80,
                     placeholder="e.g. How will the Red Sea shipping disruption affect Indian paint and tyre companies? / What did HAL's management guide for FY27 and what has happened since?")
    c = st.columns([1.2, 1.4, 4])
    use_web = c[1].toggle("Search the web too", value=True, key="deep-web", help="Off: only your stored data and documents.")
    go_ = c[0].button("Run deep search", type="primary", disabled=not ok, width="stretch")
    if not ok:
        st.info("Choose a model for Deep search in Settings → AI models (local by default).")
    if go_ and not q.strip():
        st.warning("Type a question first.")
    elif go_:
        from core import deepsearch as D
        with st.status("Researching...", expanded=True) as stt:
            rec = D.run(q.strip(), use_web=use_web, log=lambda m: st.write(m))
            stt.update(label=f"Done in {rec['seconds']} s", state="complete", expanded=False)
        st.session_state["deep_last"] = rec
    rec = st.session_state.get("deep_last")
    hist = __import__("core.deepsearch", fromlist=["history"]).history()
    if hist:
        labels = {h["file"]: f"{h['when'][:16]} · {h['question'][:90]}" for h in hist}
        pick = st.selectbox("Saved briefs", list(labels), format_func=labels.get, key="deep-hist", index=None, placeholder="Open an earlier brief")
        if pick:
            rec = next(h for h in hist if h["file"] == pick)
    if rec:
        _brief(rec)


def _brief(rec):
    with st.container(border=True):
        st.markdown(f"##### {rec['question']}")
        st.markdown(rec["brief"].replace("$", "\\$"))
        if rec.get("bad_citations"):
            st.warning(f"The brief cites sources that don't exist: {rec['bad_citations']}. Treat those statements with care.")
        st.caption(f"{rec['when'][:16]} · {len(rec['evidence'])} sources · searches: " + " | ".join(rec["queries"]))
    with st.expander(f"Sources ({len(rec['evidence'])})"):
        for i, e in enumerate(rec["evidence"], 1):
            st.markdown(f"**[{i}]** {e['title']} · {e['kind']} · {e['source']} {e['date']}" + (f" · [open]({e['url']})" if e["url"] else ""))
            st.caption(e["text"][:400])


# ---------------- exposure list ----------------
def exposure_tab():
    st.caption("Your own map of what moves what. A row says: when this driver moves, this stock or sector is hurt / helped. "
               "Search shows these next to the computed sensitivities. Example: driver 'Brent crude', target 'Paints' or 'ASIANPAINT', effect 'hurt', "
               "note 'crude-linked raw materials'.")
    d = S.exposures()
    ed = st.data_editor(d if len(d) else pd.DataFrame(columns=S.EXP_COLS), num_rows="dynamic", width="stretch", hide_index=True, key="exp-ed",
                        column_config={"driver": st.column_config.SelectboxColumn("Driver (theme or factor)", options=S.driver_options(), required=True),
                                       "target": st.column_config.TextColumn("Stock symbol or sector", required=True),
                                       "effect": st.column_config.SelectboxColumn(options=["hurt", "helped", "mixed"]),
                                       "note": st.column_config.TextColumn(width="large")})
    c = st.columns([1, 1.6, 3])
    if c[0].button("Save exposure list", type="primary"):
        S.save_exposures(ed)
        st.success("Saved.")
    if c[1].button("Add starter suggestions", help="About 18 widely known relationships (crude → OMCs, airlines, paints; USD/INR → IT, pharma ...). "
                                                   "Each is marked 'starter' in the note: keep, edit or delete them."):
        cur = S.exposures()
        add = S.starter_rows()
        merged = pd.concat([cur, add], ignore_index=True).drop_duplicates(["driver", "target"], keep="first")
        S.save_exposures(merged)
        st.success(f"Added {len(merged) - len(cur)} suggestions. Review them above.")
        st.rerun()


# ---------------- web search settings ----------------
def web_tab():
    o = WS.options()
    st.markdown("Deep search can also search the web. Only the search words go to the provider; the pages it finds are read "
                "from this Mac where robots.txt allows.")
    rows = []
    for k, (label, key) in WS.PROVIDERS.items():
        if key:
            rows.append(dict(provider=label, key_in_swing_env=key, key_found="✅" if __import__("core.secrets", fromlist=["has"]).has(key) else "—",
                             used_this_month=WS.usage().get(k, 0)))
    show(pd.DataFrame(rows))
    st.caption("Free tiers (Oct 2026, check the provider's site): Tavily 1,000/month, no card · Brave $5 credit/month (~1,000), card needed · "
               "Serper 2,500 once · Exa ~$10 credit/month. Paid plans use the same key; just raise the monthly cap.")
    c = st.columns(4)
    prov = c[0].selectbox("Provider", list(WS.PROVIDERS), index=list(WS.PROVIDERS).index(o["provider"]), format_func=lambda k: WS.PROVIDERS[k][0])
    cap = c[1].number_input("Monthly cap", 1, 1_000_000, int(o["monthly_cap"]), 50, help="Searches per month before the app stops using the web")
    n = c[2].number_input("Results per search", 3, 20, int(o["results"]))
    india = c[3].toggle("Add 'India' to searches", value=o["india_bias"])
    if st.button("Save web search settings"):
        WS.save_options({"provider": prov, "monthly_cap": int(cap), "results": int(n), "india_bias": bool(india)})
        st.success("Saved.")
    st.caption("Status: " + WS.status()[1])
