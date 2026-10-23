"""Screener → Daily moves (breadth history + every stock's move on any day)
and Market → Catalyst (which stocks moved when similar news came out before)."""
from plotly.subplots import make_subplots
from ui.common import *  # noqa: F401,F403
from core import news as newsmod, announcements as annmod, company_info
from research import daily_moves as DM, catalyst as CA

BUCKETS = [">+5%", "+2 to +5%", "+1 to +2%", "-1 to +1%", "-2 to -1%", "-5 to -2%", "< -5%"]
CAT_TYPES = ["stock news / filing", "sector news", "industry move", "sector headline only", "none found"]


def _mt(p):
    return p.stat().st_mtime if p.exists() else 0


@st.cache_resource(show_spinner="Preparing daily moves for all stocks...")
def _panel(mtime):
    px = prices()
    idx = pd.read_parquet(C.DATA / "index.parquet")
    idx["date"] = pd.to_datetime(idx.date).dt.tz_localize(None).dt.normalize()
    from core.quality import apply_corporate_actions
    px, _ = apply_corporate_actions(px)
    return DM.panel(px, idx)


def panel():
    return _panel(_mt(C.DATA / "prices.parquet"))


@st.cache_resource(show_spinner="Matching news and filings to trading days...")
def _catalysts(price_mt, news_mt, ann_mt):
    return DM.Catalysts(newsmod.load_store(), annmod.load(), panel()["dates"])


def catalysts():
    return _catalysts(_mt(C.DATA / "prices.parquet"), _mt(newsmod.STORE) or _mt(newsmod.LEGACY), _mt(annmod.STORE))


@st.cache_data(ttl=600)
def _info(mt):
    return company_info.load()


def info():
    return _info(_mt(company_info.FILE))


# =============================================================== Daily moves
def daily_moves_tab():
    P = panel()
    dates = P["dates"]
    with st.container(border=True):
        st.markdown("##### History: how many stocks moved each day")
        c = st.columns([1.3, 1.2, 1.5, 1.5])
        span = c[0].selectbox("Period", ["1 month", "3 months", "1 year", "3 years", "All"], index=1, key="dm-span")
        thr = c[1].selectbox("Move bigger than", [1, 2, 5], index=1, key="dm-thr", format_func=lambda t: f"{t}%")
        liq = c[2].selectbox("Stocks", [0.0, 5.0, 20.0], key="dm-liq",
                             format_func=lambda v: "All in the data" if v == 0 else f"Avg traded value ≥ ₹{v:.0f} cr/day")
        vs = c[3].toggle("Measure vs Nifty", key="dm-vs", help="Off: plain daily % change. On: % change minus the Nifty 50's that day.")
    cnt = DM.daily_counts(P, liq, vs)
    n = {"1 month": 21, "3 months": 63, "1 year": 252, "3 years": 756, "All": len(cnt)}[span]
    v = cnt.tail(n)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=v.date, y=v[f"up_{thr}"], name=f"Up more than {thr}%", marker_color=GOOD)
    fig.add_bar(x=v.date, y=-v[f"down_{thr}"], name=f"Down more than {thr}%", marker_color=BAD,
                customdata=v[f"down_{thr}"], hovertemplate="%{x|%d %b %Y}: %{customdata} stocks<extra></extra>")
    fig.add_scatter(x=v.date, y=v.nifty_pct, name="Nifty 50 %", line=dict(color=ACCENT, width=1.2), secondary_y=True)
    fig.update_layout(barmode="relative", bargap=0.1)
    fig.update_yaxes(title_text="Stocks (down shown below 0)", secondary_y=False)
    fig.update_yaxes(title_text="Nifty %", secondary_y=True, showgrid=False)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig_base(fig, 340, legend=True), width="stretch")
    with st.expander("Daily counts table"):
        t = v.sort_values("date", ascending=False).assign(date=lambda x: x.date.dt.strftime("%a %d %b %Y"))
        st.dataframe(t, hide_index=True, width="stretch", height=360, column_config={
            **{f"{k}_{x}": st.column_config.NumberColumn(f"{'Up' if k == 'up' else 'Down'} >{x}%") for k in ("up", "down") for x in DM.THRESHOLDS},
            "nifty_pct": st.column_config.NumberColumn("Nifty %", format="%+.2f"), "median_move": st.column_config.NumberColumn("Median stock %", format="%+.2f")})

    with st.container(border=True):
        st.markdown("##### One day: every stock's move")
        c = st.columns([1.2, 3, 3])
        day = c[0].date_input("Day", value=dates[-1].date(), min_value=dates[1].date(), max_value=dates[-1].date(), key="dm-day")
        day = pd.Timestamp(day)
        if day not in dates:
            day = dates[dates.searchsorted(day) - 1] if dates.searchsorted(day) > 0 else dates[0]
            c[0].caption(f"Not a trading day: showing {day:%a %d %b %Y}")
        bk = c[1].multiselect("Move", BUCKETS, default=[BUCKETS[0], BUCKETS[1], BUCKETS[5], BUCKETS[6]], key="dm-bk")
        q = c[2].text_input("Search (stock, company, sector, industry)", key="dm-q")
    d = DM.day_table(P, day, info(), None, liq)
    if vs:
        d["bucket"] = DM.bucket(d.vs_nifty_pct)
    else:
        d["bucket"] = DM.bucket(d.move_pct)
    m = st.columns(6)
    for i, (lab, col) in enumerate((("Up >1%", "up_1"), ("Up >2%", "up_2"), ("Up >5%", "up_5"), ("Down >1%", "down_1"), ("Down >2%", "down_2"), ("Down >5%", "down_5"))):
        row = cnt[cnt.date == day]
        m[i].metric(lab, int(row[col].iat[0]) if len(row) else "—")
    s = d[d.bucket.isin(bk)] if bk else d
    if q:
        hay = s[["symbol", "company", "sector", "industry", "basic_industry"]].fillna("").astype(str).agg(" ".join, axis=1)
        s = s[hay.str.contains(q, case=False, regex=False)]
    by_ind = st.toggle("Group by industry", key="dm-grp")
    if by_ind:
        g = d.groupby(["sector", "industry"]).agg(stocks=("symbol", "size"), median_move=("move_pct", "median"),
                                                  up_2=("move_pct", lambda x: int((x > 2).sum())), down_2=("move_pct", lambda x: int((x < -2).sum()))).reset_index()
        st.dataframe(g.sort_values("median_move", ascending=False), hide_index=True, width="stretch", height=min(600, 40 + 35 * len(g)),
                     column_config={"median_move": st.column_config.NumberColumn("Median move %", format="%+.2f")})
    cols = ["symbol", "move_pct", "vs_nifty_pct", "volume_x_normal", "traded_cr", "market_cap_cr",
            "sector", "industry", "basic_industry", "company", "close", "volume"]
    st.markdown(f"**{len(s)} stocks** on {day:%a %d %b %Y}")
    st.dataframe(s[cols], hide_index=True, width="stretch", height=min(700, 40 + 35 * max(len(s), 1)), column_config={
        "move_pct": st.column_config.NumberColumn("Move %", format="%+.2f"),
        "vs_nifty_pct": st.column_config.NumberColumn("vs Nifty %", format="%+.2f"),
        "close": st.column_config.NumberColumn(format="%.2f"),
        "volume": st.column_config.NumberColumn(format="%d"),
        "volume_x_normal": st.column_config.NumberColumn("Volume × normal", format="%.1f×", help="Volume ÷ median of the previous 20 days"),
        "traded_cr": st.column_config.NumberColumn("Traded ₹ cr", format="%.0f"),
        "market_cap_cr": st.column_config.NumberColumn("Market cap ₹ cr", format="%.0f",
                                                        help="Shares outstanding (from NSE, today) × close. Blank until Data health → Download company sectors and shares."),
        "basic_industry": "Basic industry"})
    if not company_info.FILE.exists():
        st.caption("Sectors are the Nifty 500 list's industries until company sectors are downloaded (Data health).")
    from ui.screener_more import _open_stock
    _open_stock(list(s.symbol.head(200)), "dm")


# =============================================================== Catalyst
@st.cache_resource(show_spinner="Indexing stored headlines and filings (once per update)...")
def _index(news_mt, ann_mt, include_filings):
    nw = newsmod.load_store()
    an = annmod.load() if include_filings else None
    tagger, _ = newsmod.load_tagger()
    return CA.Index(CA.build_docs(nw, an), tagger), tagger


def catalyst_tab():
    st.caption("Pick a headline (or type one). The system finds similar news and filings from the past, then shows which stocks, "
               "sectors and kinds of stocks moved on those days. It describes what happened before; it is not a forecast.")
    nw = newsmod.load_store()
    with st.container(border=True):
        c = st.columns([1.3, 4])
        how = c[0].radio("Headline", ["Recent news", "Type my own"], key="ca-how")
        recent = nw[nw.published >= nw.published.max() - pd.Timedelta(days=7)] if len(nw) else nw
        if how == "Recent news" and len(recent):
            recent = recent[~recent.roundup.fillna(False).astype(bool)].sort_values("published", ascending=False)
            pick = c[1].selectbox("Recent headline", list(range(len(recent))), key="ca-pick",
                                  format_func=lambda i: f"{recent.published.iat[i]:%d %b %H:%M} · {recent.title.iat[i][:140]}")
            text, when = recent.title.iat[pick], recent.published.iat[pick]
        else:
            text = c[1].text_input("Headline", key="ca-text", placeholder="e.g. RBI cuts repo rate by 25 bps / Crude jumps 5% / HAL bags ₹5,000 crore order")
            when = None
        c = st.columns(4)
        k = c[0].selectbox("Similar items to use", [50, 100, 200, 400], index=1, key="ca-k")
        ms = c[1].slider("Minimum similarity", 0.05, 0.6, 0.15, 0.05, key="ca-ms")
        fil = c[2].toggle("Include NSE filings", value=True, key="ca-fil")
        same = c[3].toggle("Same event type only", value=False, key="ca-same")
    if not text:
        return
    ix, tagger = _index(_mt(newsmod.STORE) or _mt(newsmod.LEGACY), _mt(annmod.STORE), fil)
    if len(ix.docs) == 0:
        st.info("No stored news yet. Fetch news, and download older history in Data health.")
        return
    et = newsmod.event_type(text)
    named = tagger.find(text)
    secs = DM.sectors_in(text)
    st.markdown(f"**Type:** {et} · **names:** {', '.join(named) or 'no stock'} · **sector words:** {', '.join(secs) or 'none'}")
    cutoff = (when - pd.Timedelta(hours=12)) if when is not None else None      # only items before this one
    sim = ix.search(text, k=k, min_score=ms, event_type=et, exclude_after=cutoff)
    if same and et != "Other":
        sim = sim[sim.event_type == et]
    P = panel()
    res = CA.impact(sim, P, info())
    nd = res["days"]
    if sim.empty or nd == 0:
        st.warning("No similar items with price data. Lower the minimum similarity, or download more news history.")
        return
    n_used = len(res["similar"])
    (st.warning if nd < 10 else st.info)(f"{n_used} similar items on **{nd} trading days**"
                                           + (f" ({len(sim) - n_used} more have no price data yet)" if len(sim) > n_used else "")
                                           + (" — too few to rely on; treat as examples." if nd < 10 else "."))
    _likely(res, named, nd)

    a, b = st.columns([3, 2])
    with a:
        st.markdown("**Sectors on those days** (median stock vs Nifty)")
        s = res["sectors"]
        if len(s):
            s2 = s.sort_values("event_day")
            fig = go.Figure(go.Bar(x=s2.event_day, y=s2.sector, orientation="h", marker_color=[GOOD if x > 0 else BAD for x in s2.event_day],
                                   customdata=np.stack([s2.t_day.round(1), s2.days, s2.next_5.round(2)], axis=1),
                                   hovertemplate="%{y}: %{x:+.2f}% on the day<br>t %{customdata[0]}, %{customdata[1]} days<br>next 5 days %{customdata[2]:+.2f}%<extra></extra>"))
            fig.update_xaxes(title_text="% vs Nifty on the event day")
            st.plotly_chart(fig_base(fig, 30 + 24 * len(s2)), width="stretch")
    with b:
        st.markdown("**The named stock and its industry peers**")
        if len(res["peers"]):
            st.dataframe(res["peers"], hide_index=True, width="stretch", column_config={
                "avg_move": st.column_config.NumberColumn("Avg move %", format="%+.2f"), "avg_size": st.column_config.NumberColumn("Avg size %", format="%.2f"),
                "t": st.column_config.NumberColumn(format="%.1f")})
        else:
            st.caption("The similar items don't name stocks (sector or economy news).")
        st.markdown("**By size** (median stock vs Nifty)")
        st.dataframe(res["sizes"], hide_index=True, width="stretch", column_config={
            "event_day": st.column_config.NumberColumn("Event day %", format="%+.2f"), "t_day": st.column_config.NumberColumn("t", format="%.1f"),
            "next_5": st.column_config.NumberColumn("Next 5 days %", format="%+.2f"), "t_5": st.column_config.NumberColumn("t (5d)", format="%.1f")})

    if len(res["stocks"]):
        st.markdown("**Stocks that moved most consistently on those days**")
        stx = res["stocks"].head(40)
        st.dataframe(stx, hide_index=True, width="stretch", height=min(560, 40 + 35 * len(stx)), column_config={
            "event_day": st.column_config.NumberColumn("Avg on event day %", format="%+.2f"), "t_day": st.column_config.NumberColumn("t", format="%.1f"),
            "up_days_pct": st.column_config.NumberColumn("Up on % of days", format="%.0f"), "next_5": st.column_config.NumberColumn("Avg next 5 days %", format="%+.2f")})
        st.caption("With ~500 stocks tested, about 25 will show |t| above 2 by chance alone. Trust a stock only when its sector agrees "
                   "and there are many days.")
    st.markdown("**The similar past items**")
    sim2 = res["similar"].assign(when=lambda x: x.published.dt.strftime("%d %b %Y"), traded=lambda x: x.day.dt.strftime("%d %b %Y"))
    nm = res["named"]
    if len(nm):
        mv = nm.groupby(["day", "headline"]).agg(stock_move=("move_day", "mean"), stock_next_5=("next_5", "mean")).reset_index()
        sim2 = sim2.assign(headline=sim2.text.str[:160]).merge(mv, on=["day", "headline"], how="left")
    st.dataframe(sim2[[c for c in ["when", "kind", "event_type", "text", "symbols", "score", "stock_move", "stock_next_5", "link"] if c in sim2]],
                 hide_index=True, width="stretch", height=min(520, 40 + 35 * len(sim2)), column_config={
                     "text": st.column_config.TextColumn("headline", width="large"), "score": st.column_config.ProgressColumn("similarity", min_value=0, max_value=1, format="%.2f"),
                     "stock_move": st.column_config.NumberColumn("Named stock that day %", format="%+.1f"),
                     "stock_next_5": st.column_config.NumberColumn("Next 5 days %", format="%+.1f"),
                     "link": st.column_config.LinkColumn("open", display_text="open")})


def _likely(res, named, nd):
    """Short 'likely impacted' summary from the tables (only things with some statistical weight)."""
    out = []
    p = res["peers"]
    if len(p):
        r = p.iloc[0]
        if named:
            out.append(f"**{', '.join(named)}** (named): stocks named in similar items moved **{r.avg_move:+.1f}%** vs Nifty on average "
                       f"(typical size {r.avg_size:.1f}%) over {int(r.days)} days.")
        pe = p.iloc[2]
        if pd.notna(pe.t) and abs(pe.t) >= 2:
            out.append(f"Industry peers of the named stock moved **{pe.avg_move:+.2f}%** on the day (t {pe.t:.1f}).")
    s = res["sectors"]
    if len(s):
        sig = s[(s.t_day.abs() >= 2) & (s.days >= 5)]
        if len(sig):
            out.append("Sectors that moved consistently: " + ", ".join(f"**{r.sector}** {r.event_day:+.2f}% (t {r.t_day:.1f})" for r in sig.head(5).itertuples()))
    stx = res["stocks"]
    if len(stx):
        sig = stx[(stx.t_day.abs() >= 3) & (stx.days >= 8)]
        if len(sig):
            out.append("Stocks with the most consistent moves: " + ", ".join(f"{r.symbol} {r.event_day:+.1f}%" for r in sig.head(8).itertuples()))
    if out:
        with st.container(border=True):
            st.markdown("##### Likely affected, going by the past")
            for o in out:
                st.markdown("- " + o)
    elif nd >= 10:
        st.caption("No sector or stock moved consistently on these days: past news like this didn't have a clear market-wide effect.")
