"""Market highlights from the downloaded prices, and market/world news."""
from ui.common import *  # noqa: F401,F403
from core import market as mk, news as newsmod


@st.cache_resource(show_spinner="Preparing market history (once per data update)...")
def _prepared(mtime):
    p = mk.prepare(prices())
    return p, mk.breadth(p), np.sort(p.date.unique())


@st.cache_data(ttl=3600)
def index_series(mtime):
    i = pd.read_parquet(C.DATA / "index.parquet")
    i["date"] = pd.to_datetime(i.date).dt.tz_localize(None).dt.normalize()
    i = i.sort_values("date")
    i["sma200"] = i.nifty.rolling(200).mean()
    return i


RANGES = {"3 months": 91, "6 months": 182, "1 year": 365, "3 years": 1096, "5 years": 1827, "10 years": 3653, "All": None}


def highlights_tab():
    p = C.DATA / "prices.parquet"
    if not p.exists():
        st.info("No price data yet.")
        return
    mt = p.stat().st_mtime
    panel, breadth, dates = _prepared(mt)
    first, last = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    c = st.columns([1.3, 1.3, 1.5, 3])
    as_of = pd.Timestamp(c[0].date_input("As of", value=last.date(), min_value=(first + pd.Timedelta(days=1)).date(),
                                         max_value=last.date(), key="mk-asof",
                                         help="Every number and chart below is as of this day (the last trading day on or before it)."))
    rng = c[1].selectbox("Chart range", list(RANGES), index=2, key="mk-rng")
    custom = c[2].toggle("Custom start", key="mk-cust")
    start = (pd.Timestamp(c[2].date_input("From", value=(as_of - pd.Timedelta(days=365)).date(), min_value=first.date(),
                                          max_value=as_of.date(), key="mk-from")) if custom else
             (first if RANGES[rng] is None else as_of - pd.Timedelta(days=RANGES[rng])))
    d, sec, s = mk.snapshot(panel, universe(), as_of)
    day = s["date"]
    c[3].caption(f"Nifty 500 stocks on **{pd.Timestamp(day):%a %d %b %Y}**" + (" (latest bar)" if day == last else "")
                 + f" · charts {pd.Timestamp(start):%d %b %Y} → {pd.Timestamp(day):%d %b %Y}")
    c = st.columns(6)
    c[0].metric("Rose / fell", f"{s['up']} / {s['down']}")
    c[1].metric("Up more than 1%", s["up1"])
    c[2].metric("Down more than 1%", s["down1"])
    c[3].metric("New 52-week highs", s["new_highs"])
    c[4].metric("New 52-week lows", s["new_lows"])
    c[5].metric("Above 200-day average", f"{s['above200']:.0f}%" if s["above200"] == s["above200"] else "—")

    a, b = st.columns(2)
    with a:
        ix = index_series(mt)
        ix = ix[(ix.date >= start) & (ix.date <= day)]
        chg = 100 * (ix.nifty.iat[-1] / ix.nifty.iat[0] - 1) if len(ix) > 1 else float("nan")
        st.markdown(f"**Nifty 50** ({chg:+.1f}% over the range)")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ix.date, y=ix.nifty, name="Nifty 50", line=dict(color=ACCENT, width=2)))
        fig.add_trace(go.Scatter(x=ix.date, y=ix.sma200, name="200-day average", line=dict(color=REF, width=1.3, dash="dash")))
        st.plotly_chart(fig_base(fig, 280, legend=True), width="stretch")
    with b:
        st.markdown("**Share of stocks above their 200-day average**")
        br = breadth[(breadth.date >= start) & (breadth.date <= day)]
        fig = go.Figure(go.Scatter(x=br.date, y=br.pct_above_200, line=dict(color=ACCENT, width=2), fill="tozeroy",
                                   fillcolor="rgba(47,111,214,0.08)", hovertemplate="%{x|%d %b %Y}: %{y:.0f}%<extra></extra>"))
        fig.add_hline(y=50, line_dash="dot", line_color=REF)
        fig.update_yaxes(range=[0, 100], ticksuffix="%")
        st.plotly_chart(fig_base(fig, 280), width="stretch")

    st.markdown(f"**Sectors: average move of their stocks** (to {pd.Timestamp(day):%d %b %Y})")
    sp = st.radio("Period", ["day", "week", "month"], horizontal=True, key="sec-per", label_visibility="collapsed")
    sv = sec[sec.stocks >= 3].sort_values(sp)
    fig = go.Figure(go.Bar(x=sv[sp], y=sv.sector, orientation="h", marker_color=[GOOD if x >= 0 else BAD for x in sv[sp]],
                           text=[f"{x:+.1f}%" for x in sv[sp]], textposition="outside", customdata=sv.stocks,
                           hovertemplate="%{y}: %{x:+.2f}% (%{customdata} stocks)<extra></extra>"))
    fig.update_traces(cliponaxis=False)
    fig.update_xaxes(ticksuffix="%")
    st.plotly_chart(fig_base(fig, 30 + 22 * len(sv)), width="stretch")

    cols = ["symbol", "company", "sector", "close", "r1", "r21", "vol_ratio"]
    cfg = {"r1": st.column_config.NumberColumn("Day %", format="%+.2f"), "r21": st.column_config.NumberColumn("Month %", format="%+.1f"),
           "vol_ratio": st.column_config.NumberColumn("Volume vs 20-day avg", format="%.1f×"), "close": st.column_config.NumberColumn("Close ₹", format="%.2f")}
    t = st.columns(2)
    t[0].markdown("**Top gainers**")
    t[0].dataframe(d.nlargest(10, "r1")[cols], hide_index=True, width="stretch", column_config=cfg)
    t[1].markdown("**Top losers**")
    t[1].dataframe(d.nsmallest(10, "r1")[cols], hide_index=True, width="stretch", column_config=cfg)
    t = st.columns(2)
    t[0].markdown("**Unusual volume** (3× normal or more)")
    uv = d[d.vol_ratio >= 3].sort_values("vol_ratio", ascending=False)
    t[0].dataframe(uv[cols].head(15), hide_index=True, width="stretch", column_config=cfg) if len(uv) else t[0].caption("None that day.")
    t[1].markdown("**New 52-week highs**")
    nh = d[d.new_high].sort_values("r1", ascending=False)
    t[1].dataframe(nh[cols].head(15), hide_index=True, width="stretch", column_config=cfg) if len(nh) else t[1].caption("None that day.")
    pick = st.selectbox("Open a stock in Stock lookup", [""] + sorted(d.symbol), key="mk-open")
    if pick:
        go_stock(pick)


def news_tab():
    nw, errs, fetched = newsmod.load()
    c = st.columns([3, 1])
    c[0].caption(f"Fetched {fetched}" if fetched else "Not fetched yet.")
    if c[1].button("Refresh news", disabled=jobs.busy("news"), width="stretch"):
        launch("news")
    if errs:
        st.caption("Feeds that failed last time: " + ", ".join(errs))
    if nw is None or nw.empty:
        st.info("No headlines yet. Click Refresh news (it runs on your Mac and takes a few seconds).")
        return
    nw = newsmod.tag(nw, universe())
    nw = nw.assign(kind=nw.kind.fillna("").replace("", "other"))
    f = st.columns([2, 2, 1])
    kinds = f[0].multiselect("Type", sorted(nw.kind.unique()), default=sorted(nw.kind.unique()),
                             format_func=lambda k: {"markets": "Markets", "companies": "Companies", "world": "World and geopolitics"}.get(k, k.capitalize()))
    srch = f[1].text_input("Search headlines", placeholder="e.g. RBI, crude, results")
    only = f[2].toggle("Mentions a Nifty 500 stock")
    v = nw[nw.kind.isin(kinds)]
    if srch:
        v = v[v.title.str.contains(srch, case=False) | v.summary.fillna("").str.contains(srch, case=False)]
    if only:
        v = v[v.symbols != ""]
    st.caption(f"{len(v)} headlines")
    for r in v.head(80).itertuples():
        with st.container(border=True):
            st.markdown(f"**[{r.title}]({r.link})**")
            meta = f"{r.source} · {str(r.published or '')[:16]}"
            if r.symbols:
                meta += " · mentions " + ", ".join(r.symbols.split(",")[:5])
            st.caption(meta)
            if r.summary:
                st.write(r.summary[:280])


def page():
    st.title("Market")
    t1, t2, t3 = st.tabs(["Highlights", "News", "Catalyst"])
    with t1:
        highlights_tab()
    with t2:
        news_tab()
    with t3:
        from ui import moves_catalyst
        moves_catalyst.catalyst_tab()
